import { defineBackend } from "@aws-amplify/backend"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import { Duration } from "aws-cdk-lib"
import * as path from "node:path"
import { fileURLToPath } from "node:url"
import { auth } from "./auth/resource"
import { createAgentCoreRuntime } from "./agent/resource"
import { SupportAiPlatform } from "./constructs/support-ai-platform"

const backend = defineBackend({ auth })

const stack = backend.createStack("AgentCoreStack")
const currentDir = path.dirname(fileURLToPath(import.meta.url))

// ---------------------------------------------------------------------------
// 1. SupportAiPlatform — Guardrail と アプリケーション推論プロファイル
//    （第6章でアプリを本番化するための共通基盤）
// ---------------------------------------------------------------------------

const platformStack = backend.createStack("SupportAiPlatform")

// MODEL_SOURCE_ARN が未設定なら停止（アカウント ID をハードコードしないため）
const modelSourceArn = process.env.MODEL_SOURCE_ARN
if (!modelSourceArn) {
  throw new Error(
    "Set MODEL_SOURCE_ARN to your foundation/inference-profile ARN. " +
      "Example: arn:aws:bedrock:us-east-1:<your-account-id>:inference-profile/us.anthropic.claude-sonnet-4-6",
  )
}

const supportAi = new SupportAiPlatform(platformStack, "Platform", {
  envName: (process.env.ENV_NAME as "dev" | "stg" | "prod") ?? "dev",
  modelSourceArn,
  vectorStore:
    (process.env.KB_VECTOR_STORE as
      | "S3_VECTORS"
      | "OPENSEARCH_SERVERLESS"
      | undefined) ?? "S3_VECTORS",
  vectorBucketName: process.env.VECTOR_BUCKET_NAME,
  enableReranking: process.env.KB_ENABLE_RERANKING === "true",
  // ローカルの sample-data/ をデータソースバケットへデプロイ時投入し、初回
  // Ingestion Job も自動発火する（読者がワンショットで RAG を試せるように）
  docsSourceDir: path.join(currentDir, "../sample-data"),
  // OpenSearch Serverless 利用時、サンドボックス検証を簡単にするためだけに公開する
  allowOssPublicAccess: process.env.OSS_ALLOW_PUBLIC === "true",
  ossSourceVpcEndpoints: process.env.OSS_SOURCE_VPCES?.split(",").filter(Boolean),
})

// ---------------------------------------------------------------------------
// 2. AgentCore Gateway + Lambda target（Confluence / Jira）
// ---------------------------------------------------------------------------

// Atlassian 認証情報を保管するシークレット。値はデプロイ後に AWS CLI などで投入する
const atlassianSecret = new secretsmanager.Secret(stack, "AtlassianSecret", {
  secretName:
    process.env.ATLASSIAN_SECRET_NAME ?? "support-agent-ch06/atlassian",
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
  gatewayName: process.env.AGENT_GATEWAY_NAME ?? "support-gateway-ch06",
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
// 3. AgentCore Memory（利用者・会話ごとの短期記憶）
// ---------------------------------------------------------------------------

const memory = new agentcore.Memory(stack, "Memory", {
  memoryName:
    process.env.AGENT_MEMORY_NAME ?? "SampleConnectSupportMemoryCh06",
})

// ---------------------------------------------------------------------------
// 4. AgentCore Runtime（Strands エージェント実行基盤）
//    SupportAiPlatform の GuardrailId / InferenceProfileArn を環境変数で受け取り、
//    アプリ側 Converse 呼び出しで `guardrailIdentifier` と推論プロファイル ARN を参照する。
// ---------------------------------------------------------------------------

const cognitoResources = backend.auth.resources
const { runtime } = createAgentCoreRuntime(stack, {
  userPool: cognitoResources.userPool,
  userPoolClient: cognitoResources.userPoolClient,
  knowledgeBaseId: supportAi.knowledgeBaseId,
  // INFERENCE_PROFILE_ARN を Runtime 側で優先するため、MODEL_ID は明示指定された
  // ときだけ環境変数として渡す。未指定の場合は Inference Profile を fallback として利用。
  modelId: process.env.MODEL_ID ?? "",
  gatewayUrl: gatewayMcpUrl,
  guardrailId: supportAi.guardrailId,
  guardrailVersion: supportAi.guardrailVersion,
  inferenceProfileArn: supportAi.inferenceProfileArn,
  memoryId: memory.memoryId,
  memoryArn: memory.memoryArn,
  sourceBucketName: supportAi.docsBucketName,
  sourcePrefixes: ["faq", "manuals", "policies", "product"],
  runtimeName: process.env.AGENT_RUNTIME_NAME,
})

// Runtime から Gateway へのアクセスを許可
gateway.grantRead(runtime)
gateway.grantInvoke(runtime)

// ---------------------------------------------------------------------------
// 5. フロントエンドへの出力
// ---------------------------------------------------------------------------

backend.addOutput({
  custom: {
    agentCoreRuntimeArn: runtime.agentRuntimeArn,
    gatewayUrl: gatewayMcpUrl,
    memoryId: memory.memoryId,
    guardrailId: supportAi.guardrailId,
    guardrailVersion: supportAi.guardrailVersion,
    inferenceProfileArn: supportAi.inferenceProfileArn,
    knowledgeBaseId: supportAi.knowledgeBaseId,
    dataSourceId: supportAi.dataSourceId,
    docsBucketName: supportAi.docsBucketName,
  },
})
