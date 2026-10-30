import { Construct } from "constructs"
import * as cdk from "aws-cdk-lib"
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
    /** SupportAiPlatform から渡される Guardrail ID（任意） */
    guardrailId?: string
    /** SupportAiPlatform から渡される Guardrail バージョン（任意） */
    guardrailVersion?: string
    /** SupportAiPlatform から渡される アプリケーション推論プロファイル ARN（任意） */
    inferenceProfileArn?: string
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
    runtimeName: props.runtimeName ?? "support_agent_ch06",
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
      ...(props.guardrailId ? { GUARDRAIL_ID: props.guardrailId } : {}),
      ...(props.guardrailVersion
        ? { GUARDRAIL_VERSION: props.guardrailVersion }
        : {}),
      ...(props.inferenceProfileArn
        ? { INFERENCE_PROFILE_ARN: props.inferenceProfileArn }
        : {}),
    },
  })

  // Knowledge Base 検索の権限（KB ID が分かっているので絞る）
  const stack = cdk.Stack.of(scope)
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["bedrock:Retrieve"],
      resources: [
        stack.formatArn({
          service: "bedrock",
          resource: "knowledge-base",
          resourceName: props.knowledgeBaseId,
        }),
      ],
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

  // モデル呼び出しの権限
  // - アプリケーション推論プロファイルを経由する場合: プロファイル ARN とその下の foundation-model
  //   両方への InvokeModel 権限が必要（クロスリージョン推論で別リージョンへルーティングされる場合を含む）
  // - プロファイル未指定時は foundation-model 直叩きのみ許可
  const invokeResources: string[] = props.inferenceProfileArn
    ? [props.inferenceProfileArn, "arn:aws:bedrock:*::foundation-model/*"]
    : ["arn:aws:bedrock:*::foundation-model/*"]
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ],
      resources: invokeResources,
    }),
  )

  // Guardrail を Converse から参照するための ApplyGuardrail 権限（任意）
  // Standard tier（クロスリージョン Guardrail 推論）ではガードレール本体に加えて、
  // ルーティング先リージョンのガードレールプロファイルも権限評価の対象になる
  if (props.guardrailId) {
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:ApplyGuardrail"],
        resources: [
          stack.formatArn({
            service: "bedrock",
            resource: "guardrail",
            resourceName: props.guardrailId,
          }),
          `arn:aws:bedrock:*:${stack.account}:guardrail-profile/*`,
        ],
      }),
    )
  }

  // ECR からイメージ取得する権限（GetAuthorizationToken は Resource 制約不可）
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: ["ecr:GetAuthorizationToken"],
      resources: ["*"],
    }),
  )
  image.repository.grantPull(runtime)

  return { runtime }
}
