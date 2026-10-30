import * as cdk from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as actions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as sns from "aws-cdk-lib/aws-sns";
import { Construct } from "constructs";

// ================================================================
// SupportAiMonitoring Props
// ================================================================
export interface SupportAiMonitoringProps {
  /** 環境名 */
  envName: "dev" | "stg" | "prod";
  /** アプリケーション推論プロファイル の ARN（メトリクスの ModelId 次元に使用） */
  inferenceProfileArn: string;
  /** アラート通知先 SNS トピック（省略時はスタック内で自動作成） */
  alertTopic?: sns.ITopic;
}

// ================================================================
// SupportAiMonitoring — Bedrock 推論のアラームを集約する Construct
// ================================================================
//
// 本章で示したとおり、少なくとも InvocationLatency / InvocationThrottles /
// InvocationServerErrors にはアラームを置いておくべきです。品質低下の前に
// 「遅くなった」「スロットリングが増えた」という兆候が出るからです。
//
export class SupportAiMonitoring extends Construct {
  readonly latencyAlarm: cloudwatch.Alarm;
  readonly throttleAlarm: cloudwatch.Alarm;
  readonly serverErrorAlarm: cloudwatch.Alarm;

  readonly alertTopic: sns.ITopic;

  constructor(
    scope: Construct,
    id: string,
    props: SupportAiMonitoringProps,
  ) {
    super(scope, id);

    const alertTopic = props.alertTopic ?? new sns.Topic(this, "AlertTopic", {
      topicName: `support-${props.envName}-alerts`,
      displayName: `Support AI Alerts (${props.envName})`,
    });
    this.alertTopic = alertTopic;

    // ----------------------------------------------------------
    // 推論遅延アラーム（p90 が 10 秒を 3 期間連続で超過）
    // ----------------------------------------------------------
    this.latencyAlarm = new cloudwatch.Alarm(this, "LatencyAlarm", {
      alarmName: `support-${props.envName}-latency-p90`,
      alarmDescription:
        "Bedrock 推論の p90 レイテンシーが 10 秒を 3 期間連続で超過",
      metric: new cloudwatch.Metric({
        namespace: "AWS/Bedrock",
        metricName: "InvocationLatency",
        dimensionsMap: { ModelId: props.inferenceProfileArn },
        statistic: "p90",
        period: cdk.Duration.minutes(5),
      }),
      threshold: 10_000, // ミリ秒
      evaluationPeriods: 3,
      comparisonOperator:
        cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    this.latencyAlarm.addAlarmAction(new actions.SnsAction(alertTopic));

    // ----------------------------------------------------------
    // スロットリングアラーム（5 分間に 5 回以上を 2 期間連続）
    // ----------------------------------------------------------
    this.throttleAlarm = new cloudwatch.Alarm(this, "ThrottleAlarm", {
      alarmName: `support-${props.envName}-throttles`,
      alarmDescription:
        "Bedrock 推論のスロットリングが 5 分間に 5 回以上を 2 期間連続で検出",
      metric: new cloudwatch.Metric({
        namespace: "AWS/Bedrock",
        metricName: "InvocationThrottles",
        dimensionsMap: { ModelId: props.inferenceProfileArn },
        statistic: "Sum",
        period: cdk.Duration.minutes(5),
      }),
      threshold: 5,
      evaluationPeriods: 2,
      comparisonOperator:
        cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    this.throttleAlarm.addAlarmAction(new actions.SnsAction(alertTopic));

    // ----------------------------------------------------------
    // サーバーエラーアラーム（5 分間に 1 回以上）
    // ----------------------------------------------------------
    this.serverErrorAlarm = new cloudwatch.Alarm(
      this,
      "ServerErrorAlarm",
      {
        alarmName: `support-${props.envName}-server-errors`,
        alarmDescription:
          "Bedrock 推論でサーバーエラーが 5 分間に 1 回以上発生",
        metric: new cloudwatch.Metric({
          namespace: "AWS/Bedrock",
          metricName: "InvocationServerErrors",
          dimensionsMap: { ModelId: props.inferenceProfileArn },
          statistic: "Sum",
          period: cdk.Duration.minutes(5),
        }),
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator:
          cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      },
    );
    this.serverErrorAlarm.addAlarmAction(
      new actions.SnsAction(alertTopic),
    );
  }
}
