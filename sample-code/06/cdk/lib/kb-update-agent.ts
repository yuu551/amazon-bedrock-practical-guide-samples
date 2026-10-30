import * as path from "path";
import { CfnResource, Duration, RemovalPolicy } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";

// ================================================================
// KbUpdateAgent Props
// ================================================================
export interface KbUpdateAgentProps {
  /** 環境名 — dev / stg / prod */
  envName: "dev" | "stg" | "prod";

  /** 環境別エンドポイントが参照する評価済みRuntimeバージョン（既定: 1） */
  runtimeVersion?: string;

  /**
   * 品質ゲートエージェントの Docker コンテキスト（``Dockerfile`` を含むディレクトリ）。
   * 省略時は ``../kb-update-agent`` を使い、CDK の DockerImageAsset 経由で
   * ビルドして ECR にプッシュする。
   */
  agentSourceDir?: string;

  /** 監視対象の Knowledge Base ID */
  knowledgeBaseId: string;

  /** Knowledge Base のデータソース ID */
  dataSourceId: string;

  /** Knowledge Base のデータソース S3 バケット（ロールバック対象） */
  kbDataBucket: s3.IBucket;

  /**
   * 評価時の回答生成経路。``"kb"`` で Knowledge Base を直接
   * ``RetrieveAndGenerate`` する。アプリケーション API 経由なら URL を渡す。
   */
  appApiUrl: string;

  /** エージェント本体のモデル ID（Strands Agent + 回答生成兼用） */
  modelId: string;

  /** LLM-as-a-Judge 採点モデル ID */
  judgeModelId: string;

  /** 結果通知用 SNS トピック ARN */
  snsTopicArn: string;

  /**
   * EventBridge ルールが拾う S3 オブジェクトキーのプレフィックス。
   * 例: ``"sample-data/policies/"``
   */
  triggerKeyPrefix: string;
}

// ================================================================
// KbUpdateAgent — Knowledge Base 更新時の品質ゲートエージェント基盤
// ================================================================
//
// S3 → EventBridge → Lambda → AgentCore Runtime の流れを1コンストラクトで
// まとめます。Runtime には Session Storage（/mnt/workspace）と環境変数を
// CFN escape hatch で注入し、ベースライン JSON は KB データソースとは別の
// バケットへ保管します。
//
export class KbUpdateAgent extends Construct {
  /** AgentCore Runtime の ARN */
  readonly runtimeArn: string;

  /** ベースライン JSON 用 S3 バケット */
  readonly baselineBucket: s3.IBucket;

  /** Trigger Lambda */
  readonly triggerFunction: lambda.IFunction;

  constructor(scope: Construct, id: string, props: KbUpdateAgentProps) {
    super(scope, id);

    // --------------------------------------------------------------
    // 1. ベースライン JSON 用 S3 バケット
    //    KB データソースのバケットに置くと取り込み事故を起こすので分離
    // --------------------------------------------------------------
    this.baselineBucket = new s3.Bucket(this, "BaselineBucket", {
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const baselinePath = `s3://${this.baselineBucket.bucketName}/baseline.json`;

    // --------------------------------------------------------------
    // 2. Runtime 実行ロール
    // --------------------------------------------------------------
    const runtimeRole = new iam.Role(this, "RuntimeExecutionRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
      description: `kb-update-agent runtime role (${props.envName})`,
    });

    // Bedrock model invoke + KB Retrieve / RetrieveAndGenerate
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:GetInferenceProfile",
        ],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          `arn:aws:bedrock:*:${process.env.CDK_DEFAULT_ACCOUNT}:inference-profile/*`,
        ],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:StartIngestionJob",
          "bedrock:GetIngestionJob",
          "bedrock:ListIngestionJobs",
          "bedrock:Retrieve",
          "bedrock:RetrieveAndGenerate",
        ],
        resources: ["*"],
      }),
    );

    // KB データソースバケットへの delete / get / list（ロールバック）
    props.kbDataBucket.grantReadWrite(runtimeRole);

    // ベースラインバケットへの read/write
    this.baselineBucket.grantReadWrite(runtimeRole);

    // SNS Publish
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["sns:Publish"],
        resources: [props.snsTopicArn],
      }),
    );

    // --------------------------------------------------------------
    // 3. AgentCore Runtime（L2 + escape hatch で Session Storage）
    //    Docker ビルドと ECR への push は CDK の DockerImageAsset が担う
    //    ため、手動で `agentcore deploy` を回す必要はない。
    // --------------------------------------------------------------
    const sourceDir = props.agentSourceDir ?? path.join(__dirname, "..", "..", "kb-update-agent");
    const runtime = new agentcore.Runtime(this, "Runtime", {
      runtimeName: `kb_quality_gate_${props.envName}`,
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset(sourceDir, {
        platform: ecr_assets.Platform.LINUX_ARM64,
      }),
      networkConfiguration:
        agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      executionRole: runtimeRole,
      environmentVariables: {
        KNOWLEDGE_BASE_ID: props.knowledgeBaseId,
        DATA_SOURCE_ID: props.dataSourceId,
        KB_SYNC_BUCKET: props.kbDataBucket.bucketName,
        APP_API_URL: props.appApiUrl,
        MODEL_ID: props.modelId,
        JUDGE_MODEL_ID: props.judgeModelId,
        BASELINE_PATH: baselinePath,
        SNS_TOPIC_ARN: props.snsTopicArn,
        AWS_REGION: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
      },
    });

    // L2 が filesystemConfigurations を未対応のため、CFN escape hatch で
    // Session Storage（/mnt/workspace）を mount する。
    const cfnRuntime = runtime.node.defaultChild as CfnResource;
    cfnRuntime.addPropertyOverride("FilesystemConfigurations", [
      { SessionStorage: { MountPath: "/mnt/workspace" } },
    ]);

    runtime.addEndpoint(props.envName, {
      version: props.runtimeVersion ?? "1",
    });
    this.runtimeArn = runtime.agentRuntimeArn;

    // --------------------------------------------------------------
    // 4. Trigger Lambda（S3 → EventBridge → invoke_agent_runtime）
    // --------------------------------------------------------------
    const triggerLogGroup = new logs.LogGroup(this, "TriggerLogGroup", {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.triggerFunction = new lambda.Function(this, "TriggerFunction", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.handler",
      code: lambda.Code.fromAsset("../lambdas/kb-update-trigger"),
      timeout: Duration.minutes(15),
      memorySize: 256,
      // EventBridge から連続して届く更新も必ず1本ずつ処理し、KB 同期の競合を防ぐ。
      reservedConcurrentExecutions: 1,
      logGroup: triggerLogGroup,
      environment: {
        KB_UPDATE_RUNTIME_ARN: this.runtimeArn,
        KB_SYNC_BUCKET: props.kbDataBucket.bucketName,
      },
    });
    this.triggerFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock-agentcore:InvokeAgentRuntime"],
        resources: [this.runtimeArn, `${this.runtimeArn}/runtime-endpoint/*`],
      }),
    );
    // 文書本体のイベントで対応するメタデータのVersion IDも取得する。
    props.kbDataBucket.grantRead(this.triggerFunction);

    // --------------------------------------------------------------
    // 5. EventBridge ルール（S3 ObjectCreated → Lambda）
    // --------------------------------------------------------------
    new events.Rule(this, "S3PutRule", {
      eventPattern: {
        source: ["aws.s3"],
        detailType: ["Object Created"],
        detail: {
          bucket: { name: [props.kbDataBucket.bucketName] },
          object: { key: [{ prefix: props.triggerKeyPrefix }] },
        },
      },
      targets: [new targets.LambdaFunction(this.triggerFunction)],
    });
  }
}
