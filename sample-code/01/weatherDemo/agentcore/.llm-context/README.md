# AgentCore設定ファイルの型定義

AgentCore CLIが生成した、設定項目の参照用ファイルです。エージェントの実行には、このディレクトリを編集する必要はありません。設定を変更するときは、対応するJSONファイルを編集してください。

| 参照ファイル | 対応する設定 | 確認できること |
|---|---|---|
| `agentcore.ts` | `../agentcore.json` | Runtime、Memoryなどのリソース定義 |
| `aws-targets.ts` | `../aws-targets.json` | デプロイ先の名前、アカウント、リージョン |

型定義のコメントには、使える値や長さなどの制約があります。JSONを編集したあとは、`weatherDemo/`で`agentcore validate`を実行して確認します。

[WeatherAgentの案内に戻る](../../README.md)
