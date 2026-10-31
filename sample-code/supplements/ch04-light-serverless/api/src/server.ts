import { serve } from "@hono/node-server"
import { createAgentInvoker } from "./agent.js"
import { createApp } from "./app.js"
import { createTokenVerifier } from "./auth.js"
import { API_VERSION } from "./version.js"

function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`環境変数 ${name} が未設定です`)
  return value
}

const app = createApp({
  verifyToken: createTokenVerifier({
    userPoolId: required("USER_POOL_ID"),
    clientId: required("USER_POOL_CLIENT_ID"),
  }),
  invokeAgent: createAgentInvoker({
    region: process.env.AWS_REGION ?? "us-east-1",
    agentRuntimeArn: required("AGENT_RUNTIME_ARN"),
  }),
  version: API_VERSION,
})

// Lambda Web Functionsは待ち受け先を AWS_LAMBDA_HTTP_ENDPOINT（例: 0.0.0.0:3000）で渡す。
// ローカルでは127.0.0.1:8787で起動する
const [hostname, port] = (process.env.AWS_LAMBDA_HTTP_ENDPOINT ?? "127.0.0.1:8787").split(":")

serve({ fetch: app.fetch, hostname, port: Number(port) }, (info) => {
  console.log(`listening on ${info.address}:${info.port}`)
})
