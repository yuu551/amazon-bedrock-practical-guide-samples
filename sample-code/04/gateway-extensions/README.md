# AgentCore Gateway Inference Target

Bedrock MantleのInference Targetを持つ一時Gatewayを作り、モデル一覧取得とモデル呼び出しを確認します。Claude系はMessages API、それ以外はChat Completions APIを使います。最後にTarget、Gateway、IAMロールを削除します。既存のGatewayは変更しません。

## 実行する

[共通のAWS認証](../../README.md#認証情報)を設定してから実行します。使用するboto3は`1.43.83`です。

```bash
cd sample-code/04/gateway-extensions
uv sync --frozen
uv run python gateway_inference_demo.py --yes --region us-east-1
```

このサンプルでは通常のAWS認証情報チェーンを使います。名前付きプロファイルを受け取る引数はありません。実行者にはAgentCore Gatewayの作成・削除、IAMロールの作成・削除とPassRole、STSの参照権限が必要です。

## ローカルのテストと後片付け

AWSを使わない単体テストは次のコマンドで実行できます。

```bash
uv run python -m unittest -v
```

プロセスを強制終了した場合は、`book-gateway-inference-`から始まるGatewayとIAMロールが残っていないか確認してください。

[第4章のコード一覧に戻る](../README.md)
