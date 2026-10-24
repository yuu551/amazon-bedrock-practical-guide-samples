import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { KbUpdateAgent } from "./kb-update-agent";

// ----------------------------------------------------------------
// KbUpdateAgentStack — 品質ゲートエージェントだけを単独で構築するスタック
// ----------------------------------------------------------------
//
// SupportAiStack は dev/prod の運用基盤を一括で構築する想定だが、
// 既存の Knowledge Base に対して品質ゲートだけを後付けで導入したい
// ケースが多いため、独立したスタックも提供する。
//
export interface KbUpdateAgentStackProps extends cdk.StackProps {
  envName: "dev" | "stg" | "prod";

  /** 環境別エンドポイントが参照する評価済みRuntimeバージョン（既定: 1） */
  runtimeVersion?: string;

  /** 監視対象 Knowledge Base ID */
  knowledgeBaseId: string;

  /** Knowledge Base のデータソース ID */
  dataSourceId: string;

  /** Knowledge Base のデータソース S3 バケット名 */
  kbDataBucketName: string;

  /** EventBridge トリガーの S3 キー prefix（例: ``sample-data/policies/``） */
  triggerKeyPrefix: string;

  /** 評価時の回答生成経路。``"kb"`` で KB 直叩き、URL でアプリ API 経由 */
  appApiUrl: string;

  /** 回答生成・エージェント本体のモデル ID */
  modelId: string;

  /** LLM-as-a-Judge 採点モデル ID */
  judgeModelId: string;

  /** 結果通知用 SNS トピック ARN */
  snsTopicArn: string;

  /** Docker コンテキスト（``Dockerfile`` を含むディレクトリ）。省略時は ``../kb-update-agent`` */
  agentSourceDir?: string;
}

export class KbUpdateAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: KbUpdateAgentStackProps) {
    super(scope, id, props);

    const kbUpdateAgent = new KbUpdateAgent(this, "KbUpdateAgent", {
      envName: props.envName,
      runtimeVersion: props.runtimeVersion,
      agentSourceDir: props.agentSourceDir,
      knowledgeBaseId: props.knowledgeBaseId,
      dataSourceId: props.dataSourceId,
      kbDataBucket: s3.Bucket.fromBucketName(
        this,
        "KbDataBucketRef",
        props.kbDataBucketName,
      ),
      triggerKeyPrefix: props.triggerKeyPrefix,
      appApiUrl: props.appApiUrl,
      modelId: props.modelId,
      judgeModelId: props.judgeModelId,
      snsTopicArn: props.snsTopicArn,
    });

    new cdk.CfnOutput(this, "RuntimeArn", {
      value: kbUpdateAgent.runtimeArn,
      description: "AgentCore Runtime ARN",
    });
    new cdk.CfnOutput(this, "BaselineBucket", {
      value: kbUpdateAgent.baselineBucket.bucketName,
      description: "ベースライン JSON 用 S3 バケット",
    });
    new cdk.CfnOutput(this, "TriggerFunctionArn", {
      value: kbUpdateAgent.triggerFunction.functionArn,
      description: "S3 → EventBridge から起動される Trigger Lambda の ARN",
    });
  }
}
