import { defineBackend } from "@aws-amplify/backend"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs"
import * as iam from "aws-cdk-lib/aws-iam"
import * as logs from "aws-cdk-lib/aws-logs"
import { Duration, RemovalPolicy } from "aws-cdk-lib"
import * as path from "node:path"
import { fileURLToPath } from "node:url"
import { auth } from "./auth/resource"
import { data } from "./data/resource"

const backend = defineBackend({ auth, data })

// ---------------------------------------------------------------------------
// RAG Lambda（Node.js / 応答ストリーミング） — 第3章: 検索設定の動的切替対応
// ---------------------------------------------------------------------------

const apiStack = backend.createStack("RagApiStack")
const currentDir = path.dirname(fileURLToPath(import.meta.url))
const traceDestination = process.env.RAG_TRACE_DESTINATION ?? "none"
if (!["none", "langfuse", "agentcore"].includes(traceDestination)) {
  throw new Error("RAG_TRACE_DESTINATIONはnone、langfuse、agentcoreのいずれかです。")
}
const traceServiceName = process.env.RAG_TRACE_SERVICE_NAME ?? `${apiStack.stackName}-rag`
const langfuseSecretArn = process.env.LANGFUSE_SECRET_ARN ?? ""
if (traceDestination === "langfuse" && !langfuseSecretArn) {
  throw new Error("LangfuseのAPIキーを保存したLANGFUSE_SECRET_ARNを指定してください。")
}
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

const ragFunction = new lambdaNode.NodejsFunction(apiStack, "RagHandler", {
  runtime: lambda.Runtime.NODEJS_24_X,
  handler: "handler",
  entry: path.join(currentDir, "functions/rag-handler/handler.ts"),
  timeout: Duration.seconds(120),
  memorySize: 512,
  environment: {
    KNOWLEDGE_BASE_ID: process.env.KNOWLEDGE_BASE_ID ?? "",
    MODEL_ID: process.env.MODEL_ID ?? "",
    KNOWLEDGE_BASE_SOURCE_BUCKET_NAME: sourceBucketName,
    KNOWLEDGE_BASE_SOURCE_PREFIXES: sourcePrefixes.join(","),
    RERANK_MODEL_ARN: process.env.RERANK_MODEL_ARN ?? "",
    ROUTE_MODEL_ARN: process.env.ROUTE_MODEL_ARN ?? "",
    // us.* 推論プロファイルとKBが us-east-1 にあるので、Bedrockクライアントを
    // Lambdaの実行リージョン（Tokyo）ではなく us-east-1 に固定する
    BEDROCK_REGION: process.env.BEDROCK_REGION ?? "us-east-1",
    RAG_TRACE_DESTINATION: traceDestination,
    OTEL_SERVICE_NAME: traceServiceName,
    ...(traceDestination === "langfuse" ? {
      LANGFUSE_BASE_URL: process.env.LANGFUSE_BASE_URL ?? "https://cloud.langfuse.com",
      LANGFUSE_SECRET_ARN: langfuseSecretArn,
    } : {}),
  },
  bundling: {
    format: lambdaNode.OutputFormat.ESM,
    target: "node24",
    minify: false,
    sourceMap: true,
    banner: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);",
    externalModules: ["@aws/aws-distro-opentelemetry-node-autoinstrumentation"],
    nodeModules: traceDestination === "agentcore"
      ? ["@aws/aws-distro-opentelemetry-node-autoinstrumentation"] : [],
  },
})

if (traceDestination === "langfuse") {
  ragFunction.addToRolePolicy(new iam.PolicyStatement({
    actions: ["secretsmanager:GetSecretValue"], resources: [langfuseSecretArn],
  }))
}
if (traceDestination === "agentcore") {
  // Transaction Searchの有効化は、アカウント・リージョン単位で事前に行う。
  const observabilityLogs = new logs.LogGroup(apiStack, "RagObservabilityLogs", {
    logGroupName: `/aws/bedrock-agentcore/runtimes/${traceServiceName}`,
    retention: logs.RetentionDays.ONE_WEEK,
    removalPolicy: RemovalPolicy.DESTROY,
  })
  // 入出力のログは、コンソールが読むotel-rt-logsへ送る。
  new logs.LogStream(apiStack, "RagObservabilityContentLogs", {
    logGroup: observabilityLogs,
    logStreamName: "otel-rt-logs",
    removalPolicy: RemovalPolicy.DESTROY,
  })
  ragFunction.addToRolePolicy(new iam.PolicyStatement({
    actions: ["xray:PutTraceSegments"], resources: ["*"],
  }))
  ragFunction.addToRolePolicy(new iam.PolicyStatement({
    actions: ["logs:PutLogEvents"], resources: [`${observabilityLogs.logGroupArn}:*`],
  }))
}

// Bedrock 権限（Knowledge Bases + クロスリージョン推論プロファイル + Rerank）
ragFunction.addToRolePolicy(
  new iam.PolicyStatement({
    actions: [
      "bedrock:RetrieveAndGenerate",
      "bedrock:Retrieve",
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Rerank",
      "bedrock:GetInferenceProfile",
      "bedrock:ListInferenceProfiles",
    ],
    resources: ["*"],
  }),
)

if (sourceBucketName) {
  ragFunction.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["s3:GetObject"],
      resources: sourcePrefixes.map(
        (prefix) => `arn:aws:s3:::${sourceBucketName}/${prefix}/*`,
      ),
    }),
  )
}

// ---------------------------------------------------------------------------
// Function URL（IAM認証 + 応答ストリーミング）
// ---------------------------------------------------------------------------

const fnUrl = ragFunction.addFunctionUrl({
  authType: lambda.FunctionUrlAuthType.AWS_IAM,
  invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
  cors: {
    allowedOrigins: ["*"],
    allowedMethods: [lambda.HttpMethod.POST],
    allowedHeaders: ["*"],
  },
})

// 認証済みユーザに Function URL の呼び出し権限
backend.auth.resources.authenticatedUserIamRole.attachInlinePolicy(
  new iam.Policy(apiStack, "RagInvokePolicy", {
    statements: [
      new iam.PolicyStatement({
        actions: ["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"],
        resources: [ragFunction.functionArn],
      }),
    ],
  }),
)

backend.addOutput({
  custom: {
    ragFunctionUrl: fnUrl.url,
  },
})
