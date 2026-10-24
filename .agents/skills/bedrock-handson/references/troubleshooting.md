# 症状から探す

READMEと`ハンズオンFAQ.md`に書かれている内容を、症状ごとにまとめています。該当しない場合は、エラーの全文を読み、READMEとFAQを探してください。

## 認証とモデル

| 症状 | 原因と対処 |
|---|---|
| `ExpiredToken`、`security token ... expired` | 認証の期限切れ。Codespacesなら`aws login --remote`をやり直す。`aws sts get-caller-identity`で確かめる |
| `AccessDeniedException`に`not available for this account`が含まれる | アカウントで、新しいモデルの利用が制限されている。個人アカウントや利用の少ないアカウントで起きる。Sonnet 4.6、Haiku 4.5、Titan Text Embeddings V2だけで進められるので、ほかのモデルの例は飛ばす |
| 上記以外の`AccessDeniedException`（`bedrock:GetInferenceProfile`など） | IAMユーザーやロールの権限不足。エラーに出た操作の権限を足してもらう。管理者権限を一時的に使うのは、読者の判断に任せる |
| 推論プロファイルが見つからない | モデルIDに`us.`が付いていない。例：`us.anthropic.claude-sonnet-4-6`。リージョンは`us-east-1`が前提 |
| 初回のClaude呼び出しで失敗する | Marketplaceのサブスクリプションと、初回利用フォームが必要。Bedrockコンソールのプレイグラウンドで一度呼ぶ |

## Knowledge Base

| 症状 | 原因と対処 |
|---|---|
| S3バケット名が衝突する | バケット名は全アカウントで一意。第2章の`setup.sh`は乱数のサフィックスを付けるので、スクリプトで作れば起きにくい |
| 同期が`CREATE_FAILED`、`SYNC_INDEXING_FAILED` | サービスロールの権限、OpenSearch Serverlessのデータアクセスポリシー、S3の中身、メタデータJSONの形式を確認。コンソールのSync historyに詳細がある |
| 同期は成功するが、検索が0件 | 埋め込みモデルの次元の不一致、フィルタが厳しすぎる、同期の直後。1〜2分待ち、フィルタを外して試す |

## Amplify

| 症状 | 原因と対処 |
|---|---|
| `pnpm exec ampx sandbox`が進まない | Node.jsが24でない、既存のsandboxが残っている、認証の期限切れ。`node --version`、`pnpm exec ampx sandbox delete`、認証を確認 |
| `amplify_outputs.json`ができない | sandboxのデプロイが途中で失敗している。ログの末尾と、CloudFormationの`amplify-`で始まるスタックのイベントを見る |
| 第6章の`pnpm exec ampx sandbox delete`で消えない | 識別子が違う。`pnpm sandbox:delete`を使う（識別子は`ch06-private`） |

## AgentCore

| 症状 | 原因と対処 |
|---|---|
| `agentcore deploy`が`This project requires a newer version of the AgentCore CLI`で止まる | `agentcore/cdk`の`aws-cdk`、`aws-cdk-lib`、`constructs`が、CLIが決めたバージョンと違う。手で上げたり、自動更新で上がったりした場合に起きる。元のバージョンへ戻す |
| `agentcore deploy`の`Sync CDK dependencies`で`npm install`が失敗する（`Cannot read properties of null`） | `agentcore/cdk/node_modules`が、pnpmで入れたものと混ざっている。`node_modules`を削除してやり直す |
| Runtimeの呼び出しがタイムアウトする | コンテナのビルドや反映が終わっていない、実行ロールの信頼関係、Runtimeに渡す環境変数（Knowledge Base IDなど）が不足。Runtimeの状態と、設定をコンソールか`aws bedrock-agentcore-control get-agent-runtime`で確認 |
| Gatewayのツールが`tools/list`に出ない | ターゲットのスキーマ登録の失敗、ツール名のプレフィックス（`TargetName___tool_name`）。CLI（`run_gateway_agent.py`）は、後ろの部分でも絞り込める |
| Gatewayに接続できない | `get-gateway`で得たURLの末尾に、`/mcp`が必要。`amplify_outputs.json`の`custom.gatewayUrl`は、そのまま使える |
| `confluence_search`が401 | APIトークンの期限切れや取り消し、Secrets Managerのシークレットの形式（`email`と`apiToken`のキー）。トークンを再発行して、読者自身に登録してもらう |
| `create_jira_ticket`が403 | トークンの発行ユーザーに、Jiraの`Create issues`の権限がない。`JIRA_PROJECT_KEY`の値も確認 |

## CDK（第6章）

| 症状 | 原因と対処 |
|---|---|
| `Bootstrap stack version ... required` | `pnpm exec cdk bootstrap aws://<アカウントID>/us-east-1`を、初回に一度だけ実行する |
| `<your-model-source-arn>`のまま実行してエラー | `MODEL_SOURCE_ARN`が未設定。環境変数か、CDK contextの`modelSourceArn`で渡す |
| `UnreservedConcurrentExecution below its minimum value` | Lambdaの同時実行数の上限。Service Quotasで、引き上げを申請する（`KbUpdateAgentDev`） |

## 削除と費用

| 症状 | 原因と対処 |
|---|---|
| 削除が`DELETE_FAILED`になる | 依存するリソース（空でないバケット、IAMロール、ネットワークインターフェース、AgentCore Runtimeの削除待ち）が残っている。同じ削除コマンドをやり直すと、通ることが多い。通らない場合は、コンソールで依存を確認して手で削除する |
| 寝ている間も課金されている | OpenSearch Serverlessのコレクションが残っている。AgentCore RuntimeやGatewayの削除漏れもある。`scripts/find_leftovers.sh`で確認する |
| Bedrockの推論費用が想定より大きい | 第5章の評価を複数モデルで何度も流した、クエリ分解でサブクエリが増えた。Cost Explorerで、モデル別に確認する |
