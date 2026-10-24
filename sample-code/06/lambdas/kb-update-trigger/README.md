# kb-update-trigger Lambda

S3 → EventBridge → Lambda → AgentCore Runtime のトリガー部分のサンプル実装です。CDKはLambdaの予約済み同時実行数を1に設定し、連続更新を1件ずつ処理します。メタデータ単独のイベントは無視し、文書本体のイベントで評価を開始します。

対象バケットはバージョニングが必須です。メタデータを先に配置し、文書本体を後で配置してください。Lambdaは文書のオブジェクト作成イベントの`version-id`と、対応する`.metadata.json`のVersion IDを`object_versions`としてRuntimeへ渡します。Version IDがない場合はRuntimeを起動しません。

このLambdaだけを手動で配置する必要はありません。[第6章のKnowledge Base更新エージェント](../../README.md#knowledge-base更新エージェント-1)にあるCDK手順で、RuntimeとEventBridgeルールをまとめて作成できます。以下はトリガーの設定や権限を確認するための説明です。

## 必要な権限（Lambda 実行ロール）

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeAgentRuntime",
      "Resource": "arn:aws:bedrock-agentcore:*:*:runtime/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<KB_SYNC_BUCKET>/*"
    },
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "logs:CreateLogGroup"],
      "Resource": "*"
    }
  ]
}
```

## 環境変数

| 変数 | 用途 |
|---|---|
| `KB_UPDATE_RUNTIME_ARN` | 起動する AgentCore Runtime の ARN |
| `KB_SYNC_BUCKET` | 想定するデータソースバケット名（不一致イベントは無視）。任意 |

## EventBridge ルール例

S3のオブジェクト作成イベント（`Object Created`）を`arn:aws:lambda:us-east-1:<account>:function:kb-update-trigger`へ流します。`PutObject`、`POST Object`、`CopyObject`、`CompleteMultipartUpload`はすべてイベントのVersion IDで追跡します。

```json
{
  "source": ["aws.s3"],
  "detail-type": ["Object Created"],
  "detail": {
    "bucket": {"name": ["<KB_SYNC_BUCKET>"]},
    "object": {"key": [{"prefix": "sample-data/policies/"}]}
  }
}
```

## 競合時の動作

Trigger Lambda内のRuntime呼び出しは、現在の品質評価が終わるまで応答を待ちます。予約済み同時実行数1により、次のEventBridgeイベントは前の実行後に処理されます。手動同期などですでに取り込みジョブがある場合は、Runtime側がそのジョブの終了を確認してから今回分の同期を開始します。

大量の更新を短時間に送ると直列処理の待ち時間が増えるため、Lambdaの`Throttles`と`Errors`、EventBridgeの`FailedInvocations`を監視します。
