import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import * as path from "path";

// ================================================================
// GatewayInterceptors Props
// ================================================================
export interface GatewayInterceptorsProps {
  /** 環境名 */
  envName: "dev" | "stg" | "prod";
  /** ブロック対象の顧客ID（カンマ区切り） */
  blockedCustomerIds?: string;
  /** ツール呼び出しで許容する金額の上限 */
  maxAmount?: number;
}

// ================================================================
// GatewayInterceptors — REQUEST / RESPONSE 両方の Lambda を定義
// ================================================================
//
// AgentCore Gateway の Interceptor として設定する Lambda 2つ。
//
// REQUEST（request_handler.py）:
//   - JWT からオペレーターのロールを抽出
//   - ロールに基づくツール単位の認可（更新系は senior-operator のみ）
//   - 引数バリデーション（顧客IDブロック、金額上限）
//   - 違反時はターゲットを呼ばずにショートサーキット
//
// RESPONSE（response_handler.py）:
//   - tools/list のレスポンスをフィルタリング
//   - ロールに許可されたツールだけをエージェントに返す
//   - 見えないツールは呼ぼうとしないため、認可エラーを未然に防ぐ
//
// 両方を組み合わせることで多層防御になる。
//
export class GatewayInterceptors extends Construct {
  /** REQUEST Interceptor Lambda の ARN */
  readonly requestFunctionArn: string;
  /** RESPONSE Interceptor Lambda の ARN */
  readonly responseFunctionArn: string;

  constructor(scope: Construct, id: string, props: GatewayInterceptorsProps) {
    super(scope, id);

    const interceptorCode = lambda.Code.fromAsset(
      path.join(__dirname, "..", "..", "interceptor"),
    );

    // REQUEST Interceptor: ロール認可 + 引数バリデーション
    const requestFn = new lambda.Function(this, "RequestInterceptor", {
      functionName: `support-${props.envName}-gw-request-interceptor`,
      description:
        "Gateway REQUEST: JWTロール認可 + 引数バリデーション",
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "request_handler.lambda_handler",
      code: interceptorCode,
      memorySize: 128,
      timeout: cdk.Duration.seconds(30),
      environment: {
        BLOCKED_CUSTOMER_IDS: props.blockedCustomerIds ?? "",
        MAX_AMOUNT: String(props.maxAmount ?? 1_000_000),
      },
    });

    // RESPONSE Interceptor: tools/list フィルタリング
    const responseFn = new lambda.Function(this, "ResponseInterceptor", {
      functionName: `support-${props.envName}-gw-response-interceptor`,
      description:
        "Gateway RESPONSE: ロール別ツール一覧フィルタリング",
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "response_handler.lambda_handler",
      code: interceptorCode,
      memorySize: 128,
      timeout: cdk.Duration.seconds(30),
    });

    this.requestFunctionArn = requestFn.functionArn;
    this.responseFunctionArn = responseFn.functionArn;
  }
}
