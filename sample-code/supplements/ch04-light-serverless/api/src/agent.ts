import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from "@aws-sdk/client-bedrock-agentcore"
import type { AgentInvoker } from "./app.js"

// Web Functionの実行ロールでSigV4署名してRuntimeを呼ぶ。
// RuntimeはIAM認証なので、ブラウザから直接は呼べない
export function createAgentInvoker(options: {
  region: string
  agentRuntimeArn: string
  qualifier?: string
}): AgentInvoker {
  const client = new BedrockAgentCoreClient({ region: options.region })

  return async ({ prompt, sessionId, actorId }) => {
    const res = await client.send(
      new InvokeAgentRuntimeCommand({
        agentRuntimeArn: options.agentRuntimeArn,
        qualifier: options.qualifier ?? "DEFAULT",
        runtimeSessionId: sessionId,
        contentType: "application/json",
        accept: "text/event-stream",
        payload: new TextEncoder().encode(
          JSON.stringify({ prompt, session_id: sessionId, actor_id: actorId }),
        ),
      }),
    )
    if (!res.response) throw new Error("Runtimeから応答がありませんでした。")

    return {
      contentType: res.contentType ?? "text/event-stream",
      body: res.response as AsyncIterable<Uint8Array>,
    }
  }
}
