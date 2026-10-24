import { defineBackend } from "@aws-amplify/backend"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import { Duration } from "aws-cdk-lib"
import * as path from "node:path"
import { fileURLToPath } from "node:url"
import { auth } from "./auth/resource"
import { createAgentCoreRuntime } from "./agent/resource"

const backend = defineBackend({ auth })

const stack = backend.createStack("AgentCoreStack")
const currentDir = path.dirname(fileURLToPath(import.meta.url))
const sourceBucketName = (process.env.KNOWLEDGE_BASE_SOURCE_BUCKET_NAME ?? "").trim()
const sourcePrefixes = (process.env.KNOWLEDGE_BASE_SOURCE_PREFIXES ?? "")
  .split(",")
  .map((prefix) => prefix.replace(/^\/+|\/+$/g, ""))
  .filter(Boolean)
if (
  sourceBucketName &&
  (!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(sourceBucketName) ||
    sourcePrefixes.length === 0 ||
    sourcePrefixes.some((prefix) => prefix.includes("..") || /[*?]/.test(prefix)))
) {
  throw new Error("Knowledge Base のS3バケット名と対象プレフィックスを確認してください。")
}

// ---------------------------------------------------------------------------
// 1. AgentCore Gateway + Lambda target（Confluence / Jira）
// ---------------------------------------------------------------------------

// Atlassian 認証情報を保管するシークレット。値はデプロイ後に AWS CLI などで投入する
const atlassianSecret = new secretsmanager.Secret(stack, "AtlassianSecret", {
  secretName: process.env.ATLASSIAN_SECRET_NAME ?? "support-agent/atlassian",
  description:
    "Atlassian Cloud (Confluence/Jira) の email と API トークンを保管するシークレット",
})

const atlassianFn = new lambda.Function(stack, "AtlassianHandler", {
  runtime: lambda.Runtime.PYTHON_3_13,
  handler: "atlassian_gateway_handler.lambda_handler",
  code: lambda.Code.fromAsset(
    path.join(currentDir, "../../gateway-lambda"),
  ),
  timeout: Duration.seconds(30),
  memorySize: 256,
  environment: {
    ATLASSIAN_BASE_URL: process.env.ATLASSIAN_BASE_URL ?? "",
    CONFLUENCE_SPACE_KEY: process.env.CONFLUENCE_SPACE_KEY ?? "",
    JIRA_PROJECT_KEY: process.env.JIRA_PROJECT_KEY ?? "",
    JIRA_ISSUE_TYPE: process.env.JIRA_ISSUE_TYPE ?? "Task",
    ATLASSIAN_SECRET_NAME: atlassianSecret.secretName,
  },
})

// Lambda 関数に Secret の読み取り権限を付与
atlassianSecret.grantRead(atlassianFn)

const gateway = new agentcore.Gateway(stack, "Gateway", {
  gatewayName: process.env.AGENT_GATEWAY_NAME ?? "support-gateway",
  protocolConfiguration: new agentcore.McpProtocolConfiguration({
    supportedVersions: [agentcore.MCPProtocolVersion.of("2025-11-25")],
    searchType: agentcore.McpGatewaySearchType.SEMANTIC,
  }),
  authorizerConfiguration: agentcore.GatewayAuthorizer.usingAwsIam(),
})

gateway.addLambdaTarget("AtlassianTarget", {
  gatewayTargetName: "AtlassianOps",
  lambdaFunction: atlassianFn,
  toolSchema: agentcore.ToolSchema.fromLocalAsset(
    path.join(currentDir, "../../gateway-lambda/atlassian_tool_schema.json"),
  ),
})

// Gateway MCP エンドポイント URL は CDK 側で属性公開されている
const gatewayMcpUrl = gateway.gatewayUrl ?? ""

// ---------------------------------------------------------------------------
// 2. AgentCore Memory（セッション継続用の短期記憶）
// ---------------------------------------------------------------------------

const memory = new agentcore.Memory(stack, "Memory", {
  memoryName: process.env.AGENT_MEMORY_NAME ?? "SampleConnectSupportMemory",
})

// ---------------------------------------------------------------------------
// 3. AgentCore Runtime（Strands エージェント実行基盤）
// ---------------------------------------------------------------------------

const cognitoResources = backend.auth.resources
const { runtime } = createAgentCoreRuntime(stack, {
  userPool: cognitoResources.userPool,
  userPoolClient: cognitoResources.userPoolClient,
  knowledgeBaseId: process.env.KNOWLEDGE_BASE_ID ?? "",
  modelId: process.env.MODEL_ID ?? "",
  gatewayUrl: gatewayMcpUrl,
  memoryId: memory.memoryId,
  memoryArn: memory.memoryArn,
  sourceBucketName,
  sourcePrefixes,
  runtimeName: process.env.AGENT_RUNTIME_NAME,
})

// Runtime から Gateway へのアクセスを許可
gateway.grantRead(runtime)
gateway.grantInvoke(runtime)

// ---------------------------------------------------------------------------
// 4. フロントエンドへの出力
// ---------------------------------------------------------------------------

backend.addOutput({
  custom: {
    agentCoreRuntimeArn: runtime.agentRuntimeArn,
    gatewayUrl: gatewayMcpUrl,
    memoryId: memory.memoryId,
  },
})
