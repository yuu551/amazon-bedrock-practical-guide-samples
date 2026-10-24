import { defineBackend } from "@aws-amplify/backend"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs"
import * as iam from "aws-cdk-lib/aws-iam"
import { Duration } from "aws-cdk-lib"
import * as path from "node:path"
import { fileURLToPath } from "node:url"
import { auth } from "./auth/resource"

const backend = defineBackend({ auth })

// ---------------------------------------------------------------------------
// RAG Lambda（Node.js / 応答ストリーミング）
// ---------------------------------------------------------------------------

const apiStack = backend.createStack("RagApiStack")
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

const ragFunction = new lambdaNode.NodejsFunction(apiStack, "RagHandler", {
  runtime: lambda.Runtime.NODEJS_24_X,
  handler: "handler",
  entry: path.join(currentDir, "functions/rag-handler/handler.ts"),
  timeout: Duration.seconds(60),
  memorySize: 512,
  environment: {
    KNOWLEDGE_BASE_ID: process.env.KNOWLEDGE_BASE_ID ?? "",
    MODEL_ID: process.env.MODEL_ID ?? "",
    KNOWLEDGE_BASE_SOURCE_BUCKET_NAME: sourceBucketName,
    KNOWLEDGE_BASE_SOURCE_PREFIXES: sourcePrefixes.join(","),
  },
  bundling: {
    format: lambdaNode.OutputFormat.ESM,
    target: "node24",
    minify: false,
    sourceMap: true,
  },
})

// Bedrock 権限（Knowledge Bases + クロスリージョン推論プロファイル）
ragFunction.addToRolePolicy(
  new iam.PolicyStatement({
    actions: [
      "bedrock:RetrieveAndGenerate",
      "bedrock:Retrieve",
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
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
