# AgentCore Gateway Interceptors

AgentCore Gatewayに設定するREQUEST / RESPONSEインターセプターLambdaのサンプルです。

## ファイル構成

| ファイル | 種別 | 役割 |
|---|---|---|
| `request_handler.py` | REQUEST | JWTロール認可 + 引数バリデーション（ツール呼び出し前に実行） |
| `response_handler.py` | RESPONSE | tools/listのフィルタリング（レスポンス返却前に実行） |

## 2つのインターセプターの役割

RESPONSE側は、ロールに許可されたツールだけを一覧に残します。ツール一覧から除外するだけでは呼び出し自体を禁止できないため、REQUEST側でもロールと引数を検査して拒否します。

## デプロイ

[CDK版のセットアップ](../cdk/README.md)を先に済ませ、`MODEL_SOURCE_ARN`を設定してください。`GatewayInterceptors`コンストラクトが2つのLambda関数を作成します。

```bash
cd sample-code/06/cdk
pnpm exec cdk deploy SupportAiProd
```

デプロイ後、スタック出力の`RequestInterceptorArn`と`ResponseInterceptorArn`をAgentCore Gatewayに設定してください。

ロールによる制御は、Gatewayが検証したJWTを使う想定です。認証方式の切り替えは[Gateway Policyの準備](../gateway-policy/README.md#適用前の準備)、全体の構成は[第6章の案内](../README.md)を参照してください。
