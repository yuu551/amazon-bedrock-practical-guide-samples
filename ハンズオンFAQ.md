# ハンズオンFAQ

ハンズオンを進めるときに起きやすいつまずきポイントと対処をまとめたものです。一般的に起きやすい問題を網羅的に整理したものなので、ご自身の環境で発生していない事象は読み飛ばして構いません。

## モデルアクセス

### Claudeを呼ぶと AccessDeniedException になる

**原因の候補**

- AWS Marketplaceサブスクリプションが完了していない
- Anthropicの「First Time Use form」を未提出
- IAMロール／ユーザーに`bedrock:InvokeModel`権限がない
- 推論プロファイル（`us.`プレフィックス付き）を使っていないのに Cross-Region専用のモデルIDを指定している

**対処**

- 管理者権限を持つアカウントから一度Bedrockコンソールの Playground で対象モデルを呼ぶ（サブスクリプションが裏側で走ります）
- Anthropic Claude を初めて使う場合は、Playgroundでチャットを送ると First Time Use form のダイアログが表示されます
- IAM側はまずは`AmazonBedrockFullAccess`相当を一時的に付与し、解決したら最小権限へ絞っていく

初回利用時の扱いは第1章の表1-4を参照してください。

### 新しいモデルだけ`AccessDeniedException`になる

`not available for this account`を含むエラーの場合はアカウントで新しいモデルの利用が制限されています。個人アカウントや利用の少ないアカウントでよく起きます。

**対処**

- 本編の手順はClaude Sonnet 4.6、Claude Haiku 4.5、Titan Text Embeddings V2で進められます。新しいモデルの例は飛ばして構いません
- 章ごとに使うモデルは[第1章のREADME](sample-code/01/README.md#使うモデル)にまとめています

### Cross-Region Inference Profile が見つからないというエラー

**原因の候補**

- モデルIDが`us.`プレフィックス無しになっている（例: `anthropic.claude-sonnet-4-6`を直接指定）
- リージョンがus-east-1以外で、本書のサンプルコードのデフォルトとずれている

**対処**

- スクリプト中のモデルIDが`us.anthropic.claude-sonnet-4-6`のようにプレフィックス付きであることを確認
- `AWS_REGION=us-east-1`を設定するか、スクリプト内のリージョン指定を環境に合わせて変更

## リージョンとリソース命名

### S3バケット名が衝突して作成できない

S3バケット名はすべてのAWSアカウントを通して一意です。`sampleconnect-kb-datasource-<アカウントID>`のような形式でも、ほかのアカウントが先に使っていることがあります。

**対処**

- 名前にタイムスタンプやランダムな文字列を付けます
- 第2章の`infra/setup.sh`は乱数のサフィックスを自動で付けるので、スクリプトで作る場合はほとんど起きません

## Knowledge Base同期

### データソースの同期が CREATE_FAILED や SYNC_INDEXING_FAILED になる

**原因の候補**

- Knowledge Baseのサービスロールに S3 / Bedrock / OpenSearch の権限が足りていない
- OpenSearch Serverless のデータアクセスポリシーがサービスロールを許可していない
- S3バケットのオブジェクトが空（または巨大すぎてタイムアウト）
- メタデータ JSON が壊れている（JSONとして parse できない）

**対処**

- Bedrockコンソール → Knowledge Base → Data source → Sync history で詳細エラーを確認
- メタデータJSONは`metadataAttributes`キーがトップにあるか、各属性の型が一致しているかを確認
- サービスロールの信頼関係に`bedrock.amazonaws.com`が入っているか確認

### 同期は成功するのにRetrieveで結果が0件

**原因の候補**

- Embeddingモデルが想定と違う（次元数の不一致）
- メタデータフィルタの条件が厳しすぎる
- 同期完了直後はインデックス反映に数十秒かかる

**対処**

- 同期完了から1〜2分待ってから再試行
- フィルタを外して結果が返るか試す
- Embeddingモデルが`amazon.titan-embed-text-v2:0`（1024次元）と一致しているか確認

## Atlassian連携（第4章）

### confluence_searchが401を返す

**原因の候補**

- APIトークンが期限切れまたは取り消されている
- Secrets Manager の`support-agent/atlassian`に投入したJSONの形式が違う（`email`と`apiToken`のキーが必要）
- AtlassianアカウントのメールアドレスとAPIトークンの組み合わせが一致していない

**対処**

- <https://id.atlassian.com/manage-profile/security/api-tokens>で新しいトークンを発行
- 次のJSONで再投入

```bash
aws secretsmanager put-secret-value \
  --secret-id support-agent/atlassian \
  --secret-string '{"email":"<your-email>","apiToken":"<new-token>"}'
```

### create_jira_ticketが403を返す

**原因の候補**

- APIトークン発行ユーザーがJiraプロジェクトでissue作成権限を持っていない
- プロジェクトキーの指定ミス

**対処**

- Jiraプロジェクトの権限設定で`Create issues`ロールが付与されているか確認
- `JIRA_PROJECT_KEY`の値がプロジェクト一覧の表記と一致しているか確認

## CDKデプロイ（第6章）

### `cdk deploy`が`Bootstrap stack version 6 required`で失敗

CDKのbootstrapが未実行か、古いままです。`sample-code/06/cdk`で次を実行します。

```bash
pnpm exec cdk bootstrap aws://<アカウントID>/us-east-1
```

### `<your-...>`プレースホルダのまま実行してエラー

`MODEL_SOURCE_ARN`が未設定のまま、`bin/app.ts`のプレースホルダー（`<your-model-source-arn>`）が使われています。環境変数かCDK contextの`modelSourceArn`で、コピー元のARNを渡します。

```bash
export MODEL_SOURCE_ARN="arn:aws:bedrock:us-east-1:<アカウントID>:inference-profile/us.anthropic.claude-sonnet-4-6"
pnpm exec cdk deploy SupportAiDev
```

詳しくは[第6章CDK版のREADME](sample-code/06/cdk/README.md)を参照してください。

## Amplify sandbox（第2〜4章）

### `pnpm exec ampx sandbox`がハングする

**原因の候補**

- Node.jsのバージョンが古い（サンプルは24を前提にしています）
- 既存のsandboxプロセスが残っている
- AWS認証情報の有効期限が切れている

**対処**

- `node --version`で24であることを確認
- 既存のプロセスがあれば`Ctrl+C`で停止し、`pnpm exec ampx sandbox delete`で削除してからやり直す
- 第6章のAmplify版は`pnpm sandbox`と`pnpm sandbox:delete`を使う
- `aws sts get-caller-identity`で認証情報の有効性を確認

### `amplify_outputs.json`が生成されない

sandboxのデプロイが途中で失敗しています。

**対処**

- sandboxのログの末尾でエラーの内容を確認する
- CloudFormationのコンソールで、`amplify-`で始まるスタックの状態とイベントを確認する

## AgentCore（第4章）

### Runtime呼び出しでタイムアウトする

**原因の候補**

- ECRへのコンテナイメージプッシュが完了していない
- IAMロールがAgentCore Runtime用の信頼関係を持っていない
- Knowledge Base IDなどの環境変数がRuntimeに渡っていない

**対処**

- Bedrockコンソール → AgentCore → Agent Runtimeで該当Runtimeのステータスを確認
- `aws bedrock-agentcore get-agent-runtime`でRuntimeの設定を取得し環境変数を確認

### Gatewayのツールが`tools/list`に表示されない

**原因の候補**

- ターゲットLambdaのスキーマ登録が失敗している
- ツール名にプレフィックス（`TargetName___tool_name`）が付いていて期待した名前と異なる

**対処**

- `aws bedrock-agentcore list-targets`でターゲットの登録状況を確認
- CLIサンプル（`run_gateway_agent.py`）はサフィックスマッチで絞り込めるため、フルネームを把握しなくても呼び出せる

## コスト

### 寝ている間も課金されているのが気になる

**原因の候補**

- OpenSearch Serverlessのコレクションが残っている（検索していない間もOCUの料金がかかります。単価は[OpenSearch Serverlessの料金](https://aws.amazon.com/opensearch-service/pricing/)を参照）
- AgentCore RuntimeやGatewayを削除していない

**対処**

- 中断する場合は`sample-code/02`で`bash infra/teardown.sh`を実行し、第2章のKnowledge Baseとコレクションを削除する
- 常駐費を避けたい場合は第2章をS3 Vectors版（`bash infra/setup-s3vectors.sh`）で作る。ハイブリッド検索は使えません
- 削除の手順全体は[ハンズオン後のリソース削除手順](ハンズオン後のリソース削除手順.md)を参照

### Bedrock推論コストが想定より大きい

**原因の候補**

- 評価バッチ（第5章）を複数モデルで何度も流している
- Query Decomposition（第3章）で1質問あたり複数のサブクエリ呼び出しが発生

**対処**

- Cost ExplorerでBedrockのコストをサービス／リージョン別に確認
- 評価対象のサンプル数を絞る、Judgeモデルを統一する、などで削減

## 困ったときの確認順序

1. **エラーメッセージ全文をAWS Knowledge Centerで検索** ── 公式FAQに当たるケースが多い
2. **CloudTrail** ── APIコールが拒否されたか、どの権限不足かを特定
3. **CloudFormationスタックのStatusとEventsタブ** ── デプロイ時の失敗原因が辿れる
4. **このリポジトリのIssues** ── 同じ事象が報告されている可能性

それでも解決しない場合は[Issues](https://github.com/yuu551/amazon-bedrock-practical-guide-samples/issues)から気軽にお知らせください！
