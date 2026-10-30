import { Construct } from "constructs"
import { ArnFormat, Stack } from "aws-cdk-lib"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import * as cognito from "aws-cdk-lib/aws-cognito"
import { Platform } from "aws-cdk-lib/aws-ecr-assets"
import * as iam from "aws-cdk-lib/aws-iam"
import { ContainerImageBuild } from "@cdklabs/deploy-time-build"
import * as path from "node:path"
import { fileURLToPath } from "node:url"

// ---------------------------------------------------------------------------
// AgentCore Runtime — Strands エージェントのコンテナ実行基盤
// ---------------------------------------------------------------------------

export interface CreateAgentCoreRuntimeResult {
  runtime: agentcore.Runtime
}

export function createAgentCoreRuntime(
  scope: Construct,
  props: {
    userPool: cognito.IUserPool
    userPoolClient: cognito.IUserPoolClient
    knowledgeBaseId: string
    modelId: string
    gatewayUrl: string
    memoryId: string
    memoryArn: string
    sourceBucketName?: string
    sourcePrefixes?: string[]
    runtimeName?: string
  },
): CreateAgentCoreRuntimeResult {
  const currentDir = path.dirname(fileURLToPath(import.meta.url))

  // CodeBuild で ARM64 コンテナイメージをビルド
  const image = new ContainerImageBuild(scope, "AgentImage", {
    directory: path.join(currentDir),
    platform: Platform.LINUX_ARM64,
  })

  // AgentCore Runtime（Cognito 認証付き）
  const runtime = new agentcore.Runtime(scope, "Runtime", {
    // runtimeName は先頭が英字、以降は英数字とアンダースコアのみ（ハイフン不可）
    runtimeName: props.runtimeName ?? "support_agent",
    agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromEcrRepository(
      image.repository,
      image.imageTag,
    ),
    networkConfiguration:
      agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
    authorizerConfiguration:
      agentcore.RuntimeAuthorizerConfiguration.usingCognito(
        props.userPool,
        [props.userPoolClient],
      ),
    environmentVariables: {
      KNOWLEDGE_BASE_ID: props.knowledgeBaseId,
      MODEL_ID: props.modelId,
      GATEWAY_URL: props.gatewayUrl,
      AGENTCORE_MEMORY_ID: props.memoryId,
      KNOWLEDGE_BASE_SOURCE_BUCKET_NAME: props.sourceBucketName ?? "",
      KNOWLEDGE_BASE_SOURCE_PREFIXES: (props.sourcePrefixes ?? []).join(","),
    },
  })

  const knowledgeBaseArn = Stack.of(scope).formatArn({
    service: "bedrock",
    resource: "knowledge-base",
    resourceName: props.knowledgeBaseId,
    arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
  })

  // Knowledge Base の検索権限は、環境変数で渡したIDに限定
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["bedrock:Retrieve"],
      resources: [knowledgeBaseArn],
    }),
  )

  // モデルIDと推論プロファイルARNのどちらにも対応
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      resources: ["*"],
    }),
  )

  if (props.sourceBucketName && props.sourcePrefixes?.length) {
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: props.sourcePrefixes.map(
          (prefix) => `arn:aws:s3:::${props.sourceBucketName}/${prefix}/*`,
        ),
      }),
    )
  }

  // AgentCore Memory に対する読み書き権限
  // 短期記憶（イベント読み書き）と長期記憶（戦略経由のレコード取得）の両方を見越して付与する
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:GetMemory",
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:GetEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:ListSessions",
        "bedrock-agentcore:ListActors",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:GetMemoryRecord",
      ],
      resources: [props.memoryArn],
    }),
  )

  // ECR からイメージ取得する権限（fromEcrRepository では自動付与されないケースがある）
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["ecr:GetAuthorizationToken"],
      resources: ["*"],
    }),
  )
  image.repository.grantPull(runtime)

  return { runtime }
}
