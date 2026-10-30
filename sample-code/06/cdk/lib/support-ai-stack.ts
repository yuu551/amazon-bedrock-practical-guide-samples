import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sns from "aws-cdk-lib/aws-sns";
import { Construct } from "constructs";
import { SupportAiPlatform } from "./support-ai-platform";
import { SupportAiMonitoring } from "./monitoring";
import { GatewayInterceptors } from "./interceptor";
import { KbUpdateAgent } from "./kb-update-agent";

// ----------------------------------------------------------------
// スタック Props
// ----------------------------------------------------------------
export interface SupportAiStackProps extends cdk.StackProps {
  envName: "dev" | "stg" | "prod";
  modelSourceArn: string;
  alertTopicArn?: string;
  /** ブロック対象の顧客ID（カンマ区切り、省略可） */
  blockedCustomerIds?: string;
  /** Knowledge Base のベクトルストア種別（既定: S3_VECTORS） */
  vectorStore?: "S3_VECTORS" | "OPENSEARCH_SERVERLESS";
  /** Knowledge Basesのリランキング用権限を有効にする（既定: false） */
  enableKbReranking?: boolean;
  /** サンプルデータ投入元のローカルディレクトリ（任意） */
  docsSourceDir?: string;
  /**
   * KB 更新の品質ゲートエージェントを構築する場合の設定。
   * 省略時は KbUpdateAgent を作成しない。
   */
  kbUpdateAgent?: {
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
    /** 評価時の回答生成経路。``"kb"`` で KB 直叩き、URLでアプリAPI経由 */
    appApiUrl: string;
    /** 回答生成・エージェント本体のモデル ID */
    modelId: string;
    /** LLM-as-a-Judge 採点モデル ID */
    judgeModelId: string;
    /** 結果通知用 SNS トピック ARN */
    snsTopicArn: string;
  };
}

// ----------------------------------------------------------------
// メインスタック — Platform + Monitoring + Interceptors を束ねる
// ----------------------------------------------------------------
export class SupportAiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: SupportAiStackProps) {
    super(scope, id, props);

    // AI 基盤リソース（Guardrail / 推論プロファイル）
    // AgentCore Runtime は Amplify 側（amplify-ui の amplify/agent）で構築し、
    // ここで作成した GuardrailId と InferenceProfileArn を環境変数で受け取る想定。
    const platform = new SupportAiPlatform(this, "Platform", {
      envName: props.envName,
      modelSourceArn: props.modelSourceArn,
      vectorStore: props.vectorStore,
      enableReranking: props.enableKbReranking,
      docsSourceDir: props.docsSourceDir,
    });

    // CloudWatch アラーム（SNSトピック省略時はスタック内で自動作成）
    const alertTopic = props.alertTopicArn
      ? sns.Topic.fromTopicArn(this, "ImportedAlertTopic", props.alertTopicArn)
      : undefined;
    new SupportAiMonitoring(this, "Monitoring", {
      envName: props.envName,
      inferenceProfileArn: platform.inferenceProfileArn,
      alertTopic,
    });

    // Gateway Interceptors（REQUEST + RESPONSE）
    const interceptors = new GatewayInterceptors(this, "Interceptors", {
      envName: props.envName,
      blockedCustomerIds: props.blockedCustomerIds,
    });

    // Knowledge Base 更新の品質ゲートエージェント（任意）
    let kbUpdateAgent: KbUpdateAgent | undefined;
    if (props.kbUpdateAgent) {
      const cfg = props.kbUpdateAgent;
      kbUpdateAgent = new KbUpdateAgent(this, "KbUpdateAgent", {
        envName: props.envName,
        runtimeVersion: cfg.runtimeVersion,
        knowledgeBaseId: cfg.knowledgeBaseId,
        dataSourceId: cfg.dataSourceId,
        kbDataBucket: s3.Bucket.fromBucketName(
          this,
          "KbDataBucketRef",
          cfg.kbDataBucketName,
        ),
        triggerKeyPrefix: cfg.triggerKeyPrefix,
        appApiUrl: cfg.appApiUrl,
        modelId: cfg.modelId,
        judgeModelId: cfg.judgeModelId,
        snsTopicArn: cfg.snsTopicArn,
      });
    }

    // スタック出力
    new cdk.CfnOutput(this, "InferenceProfileArn", {
      value: platform.inferenceProfileArn,
    });
    new cdk.CfnOutput(this, "GuardrailId", {
      value: platform.guardrailId,
    });
    new cdk.CfnOutput(this, "GuardrailVersion", {
      value: platform.guardrailVersion,
    });
    new cdk.CfnOutput(this, "KnowledgeBaseId", {
      value: platform.knowledgeBaseId,
    });
    new cdk.CfnOutput(this, "DataSourceId", {
      value: platform.dataSourceId,
    });
    new cdk.CfnOutput(this, "DocsBucketName", {
      value: platform.docsBucketName,
    });
    new cdk.CfnOutput(this, "RequestInterceptorArn", {
      value: interceptors.requestFunctionArn,
      description: "Gateway の REQUEST Interceptor に指定する Lambda ARN",
    });
    new cdk.CfnOutput(this, "ResponseInterceptorArn", {
      value: interceptors.responseFunctionArn,
      description: "Gateway の RESPONSE Interceptor に指定する Lambda ARN",
    });

    if (kbUpdateAgent) {
      new cdk.CfnOutput(this, "KbUpdateRuntimeArn", {
        value: kbUpdateAgent.runtimeArn,
        description: "kb-update-agent の AgentCore Runtime ARN",
      });
      new cdk.CfnOutput(this, "KbUpdateBaselineBucket", {
        value: kbUpdateAgent.baselineBucket.bucketName,
        description: "ベースライン JSON 用 S3 バケット",
      });
      new cdk.CfnOutput(this, "KbUpdateTriggerFunctionArn", {
        value: kbUpdateAgent.triggerFunction.functionArn,
        description: "S3 → EventBridge から起動される Trigger Lambda の ARN",
      });
    }
  }
}
