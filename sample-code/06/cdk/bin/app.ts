#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { SupportAiStack } from "../lib/support-ai-stack";
import { KbUpdateAgentStack } from "../lib/kb-update-agent-stack";

const app = new cdk.App();

// CDK Context または環境変数から設定を取得
// 初回は `cdk.json` に設定するか、`-c key=value` で渡す
const modelSourceArn =
  (app.node.tryGetContext("modelSourceArn") as string | undefined) ??
  process.env.MODEL_SOURCE_ARN ??
  "<your-model-source-arn>";
const devAlertTopicArn =
  (app.node.tryGetContext("devAlertTopicArn") as string | undefined) ??
  process.env.DEV_ALERT_TOPIC_ARN ??
  "<your-dev-sns-topic-arn>";
const prodAlertTopicArn =
  (app.node.tryGetContext("prodAlertTopicArn") as string | undefined) ??
  process.env.PROD_ALERT_TOPIC_ARN ??
  "<your-prod-sns-topic-arn>";

// kb-update-agent 用設定（任意 — KB_UPDATE_KB_ID が設定されたときのみ作成）
const kbUpdateKnowledgeBaseId =
  (app.node.tryGetContext("kbUpdateKnowledgeBaseId") as string | undefined) ??
  process.env.KB_UPDATE_KB_ID;
const kbUpdateAgentConfig = kbUpdateKnowledgeBaseId
  ? {
      runtimeVersion:
        (app.node.tryGetContext("kbUpdateRuntimeVersion") as string | undefined) ??
        process.env.KB_UPDATE_RUNTIME_VERSION ??
        "1",
      knowledgeBaseId: kbUpdateKnowledgeBaseId!,
      dataSourceId:
        (app.node.tryGetContext("kbUpdateDataSourceId") as string | undefined) ??
        process.env.KB_UPDATE_DS_ID ??
        "<your-data-source-id>",
      kbDataBucketName:
        (app.node.tryGetContext("kbUpdateDataBucket") as string | undefined) ??
        process.env.KB_UPDATE_DATA_BUCKET ??
        "<your-kb-data-bucket>",
      triggerKeyPrefix:
        (app.node.tryGetContext("kbUpdateTriggerPrefix") as string | undefined) ??
        process.env.KB_UPDATE_TRIGGER_PREFIX ??
        "sample-data/policies/",
      appApiUrl:
        (app.node.tryGetContext("kbUpdateAppApiUrl") as string | undefined) ??
        process.env.KB_UPDATE_APP_API_URL ??
        "kb",
      modelId:
        (app.node.tryGetContext("kbUpdateModelId") as string | undefined) ??
        process.env.KB_UPDATE_MODEL_ID ??
        "us.anthropic.claude-sonnet-4-6",
      judgeModelId:
        (app.node.tryGetContext("kbUpdateJudgeModelId") as string | undefined) ??
        process.env.KB_UPDATE_JUDGE_MODEL_ID ??
        "us.anthropic.claude-sonnet-4-6",
      snsTopicArn:
        (app.node.tryGetContext("kbUpdateSnsTopicArn") as string | undefined) ??
        process.env.KB_UPDATE_SNS_TOPIC_ARN ??
        "<your-kb-update-sns-topic-arn>",
    }
  : undefined;

// ----------------------------------------------------------------
// 開発環境（modelSourceArn が指定された場合のみ作成）
// SupportAiStack は Guardrail / 推論プロファイル / 監視を提供。
// AgentCore Runtime は別途 Amplify 側（sample-code/06/amplify-ui）で
// 構築し、本スタックの GuardrailId と InferenceProfileArn を環境変数経由で参照する。
// ----------------------------------------------------------------
const vectorStore =
  ((app.node.tryGetContext("vectorStore") as string | undefined) ??
    process.env.KB_VECTOR_STORE) === "OPENSEARCH_SERVERLESS"
    ? "OPENSEARCH_SERVERLESS"
    : "S3_VECTORS";
const enableKbReranking =
  ((app.node.tryGetContext("enableKbReranking") as string | undefined) ??
    process.env.KB_ENABLE_RERANKING) === "true";

const isPlaceholder = (v: string) => !v || v.startsWith("<your-");
if (!isPlaceholder(modelSourceArn)) {
  new SupportAiStack(app, "SupportAiDev", {
    envName: "dev",
    modelSourceArn,
    // SNS トピック未指定（プレースホルダのまま）なら省略し、スタック側で自動作成させる
    alertTopicArn: isPlaceholder(devAlertTopicArn) ? undefined : devAlertTopicArn,
    kbUpdateAgent: kbUpdateAgentConfig,
    vectorStore,
    enableKbReranking,
    env: {
      account: process.env.CDK_DEFAULT_ACCOUNT,
      region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
    },
  });

  new SupportAiStack(app, "SupportAiProd", {
    envName: "prod",
    modelSourceArn,
    alertTopicArn: isPlaceholder(prodAlertTopicArn) ? undefined : prodAlertTopicArn,
    vectorStore,
    enableKbReranking,
    env: {
      account: process.env.CDK_DEFAULT_ACCOUNT,
      region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
    },
  });
}

// ----------------------------------------------------------------
// kb-update-agent を単独デプロイする想定の独立スタック（任意）
// 既存 KB に品質ゲートを後付けで導入したいケースで利用。
// `KB_UPDATE_KB_ID` が設定されている場合にのみ Stack を生成する。
// ----------------------------------------------------------------
if (kbUpdateAgentConfig) {
  new KbUpdateAgentStack(app, "KbUpdateAgentDev", {
    envName: "dev",
    ...kbUpdateAgentConfig,
    env: {
      account: process.env.CDK_DEFAULT_ACCOUNT,
      region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
    },
  });
}
