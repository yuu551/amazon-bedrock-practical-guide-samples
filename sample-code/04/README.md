# 第4章 サンプルコード

資料の検索と、必要に応じたJiraへの引き継ぎを行うエージェントのサンプルです。Strands Agentsでエージェントを実装し、AgentCore Runtimeで動かします。外部ツールとの接続にはAgentCore Gatewayを使います。

第2章で作ったKnowledge Baseに、Confluence検索とJira起票のツールを組み合わせます。[シナリオ](#シナリオ)で接続先を確認してから、[セットアップ](#セットアップ)を順に進めてください。ローカルCLIは`GATEWAY_URL`を指定しなければKnowledge Baseの検索だけで動くので、Gatewayを作る前でも試せます。

> [!WARNING]
> 手順3のsandboxはAgentCore Runtime、Gateway、Memory、Lambda関数、Secrets Managerのシークレットを作ります。シークレットは保管している間、月額の料金がかかります。使い終わったら[後片付け](#後片付け)で削除してください。

## ファイル構成

### Amplifyバックエンド（`amplify-ui/amplify/`）

| ファイル | 内容 |
|---|---|
| `backend.ts` | CDKスタック定義（Runtime + Gateway + Lambda target + Memory） |
| `auth/resource.ts` | Cognito認証の定義 |
| `agent/resource.ts` | AgentCore Runtime L2コンストラクト定義 |
| `agent/main.py` | Runtimeコンテナのエントリーポイント（Strandsエージェント） |
| `agent/Dockerfile` | Runtimeコンテナイメージの定義 |
| `agent/requirements.txt` | コンテナ用Python依存パッケージ |

### ローカル動作確認用

| ファイル | 内容 |
|---|---|
| `run_gateway_agent.py` | AgentCore Gatewayのツールを使うエージェントのローカルCLIサンプル |

### Gatewayターゲット（`gateway-lambda/`）

| ファイル | 内容 |
|---|---|
| `atlassian_gateway_handler.py` | Confluence検索とJiraチケット作成を提供するLambda関数 |
| `atlassian_tool_schema.json` | Gatewayターゲット登録時に使用するツールスキーマ |

### 生成AIモデル向けGateway接続先（`gateway-extensions/`）

| ファイル | 内容 |
|---|---|
| `gateway_inference_demo.py` | Bedrock Mantle向けの推論接続先を作成し、モデル呼び出し後に削除するサンプル |
| `test_gateway_inference_demo.py` | 接続先設定、モデル選択、レスポンス解析の単体テスト |

### 検証用UI（`amplify-ui/src/`）

- Vite + React + TypeScriptのSPA
- 第2章のサポートポータルUIをAIエージェント対応に拡張
- AgentCore RuntimeをCognito認証で直接呼び出し
- 質問履歴、参照ソース、エスカレーション状態、Jira起票結果を表示

## シナリオ

第4章では、以下の3つのデータソースを使い分けるエージェントを構築します。

| ツール名 | 接続先 | 用途 |
|---|---|---|
| `support-kb` | Knowledge Bases（S3） | 障害対応手順、既知事象、サポートFAQなどの安定したナレッジ |
| `confluence_search` | AgentCore Gateway → Confluence | 運用メモ、ポストモーテム、一時的な回避策など新しい情報 |
| `create_jira_ticket` | AgentCore Gateway → Jira | 回答を確定できない場合の有人エスカレーション |

## セットアップ

### 1. Atlassianサイトを準備する

ConfluenceとJiraを使えるAtlassian Cloudサイトを用意します。画面つきの手順は本書の付録「Atlassianサイトのセットアップ（Confluence・Jira）」にあります。無料プランを使う場合は[Confluenceのプラン](https://www.atlassian.com/software/confluence/pricing)と[Jiraのプラン](https://www.atlassian.com/software/jira/pricing)で利用人数と制限を確認してください。すでにサイトをお持ちなら、新規登録は不要です。

**1-1. Atlassianサイトのサインアップ**

- [atlassian.com/software/confluence/free](https://www.atlassian.com/software/confluence/free) からサインアップ
- サイトURL（`https://<your-site>.atlassian.net`）が発行されます。これが`ATLASSIAN_BASE_URL`になります
- 同じサイト上でConfluenceとJiraの両方をFreeプランで有効化します

**1-2. Confluenceスペースの作成とテストページ登録**

- Confluenceで空のスペースを1つ作成（例: `Support Knowledge` / スペースキー`SUP`）。スペースキーが`CONFLUENCE_SPACE_KEY`になります
- 本章のサンプル質問にヒットするダミーページを3〜5本登録します。例:
  - 「東京リージョン ファイルアップロード障害 ポストモーテム（2026-03）」
  - 「HTTP 502 Bad Gateway 既知障害 暫定回避策」
  - 「SAML属性マッピングの落とし穴（Microsoft Entra ID）」
- 実在の事故や顧客情報と混同しないよう、架空の日付・サービス名で記載してください

**1-3. Jiraプロジェクトの作成**

- Team-managedプロジェクトを作成（テンプレート例は`Kanban`）
- プロジェクトキー（例: `SUP`）が`JIRA_PROJECT_KEY`になります
- 課題タイプを変えたいときは環境変数`JIRA_ISSUE_TYPE`で上書きできます（デフォルト: `Task`）

**1-4. APIトークンの発行**

- [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) からAPIトークンを作成
- ログインに使うメールアドレスと発行したトークンは手順4でAWS Secrets Managerへ登録します
- `atlassian_gateway_handler.py`はSecrets Managerから取得したメールアドレスとトークンを組み合わせてBasic認証（`email:token`）でAtlassian APIを呼び出します

**1-5. 権限確認**

APIトークンを発行したユーザーが次の権限を持っていることを確認してください。

- Confluenceスペースのページの閲覧
- Jiraプロジェクトの`Create issues`（Team-managedプロジェクトならデフォルトロールで付与されます）

これで次の4つの値がそろいます。手順3で`pnpm exec ampx sandbox`を実行するときに渡してください。Atlassianのメールアドレスとトークンはこの環境変数に含めず、手順4でAWS Secrets Managerへ登録します。

| 環境変数 | 値の例 |
|---|---|
| `ATLASSIAN_BASE_URL` | `https://<your-site>.atlassian.net` |
| `CONFLUENCE_SPACE_KEY` | 1-2で作ったスペースのキー |
| `JIRA_PROJECT_KEY` | 1-3で作ったプロジェクトのキー |
| `JIRA_ISSUE_TYPE` | 課題タイプ（省略時は`Task`） |

### 2. Python環境（ローカルCLI用）

```bash
cd sample-code/04
uv sync
```

### 3. Amplify sandbox でバックエンドをデプロイ

`amplify/backend.ts`のCDKスタックが、AgentCore Runtime・Gateway・Lambda target・Memoryに加えて、Atlassian認証情報を保管するAWS Secrets Managerシークレット（`support-agent/atlassian`）をまとめて作成します。シークレットの値はデプロイ後に別途投入します。

```bash
cd sample-code/04/amplify-ui

pnpm install --frozen-lockfile

KNOWLEDGE_BASE_ID="$KNOWLEDGE_BASE_ID" \
MODEL_ID=us.anthropic.claude-sonnet-4-6 \
ATLASSIAN_BASE_URL=https://YOUR_SITE.atlassian.net \
CONFLUENCE_SPACE_KEY=YOUR_SPACE_KEY \
JIRA_PROJECT_KEY=YOUR_PROJECT_KEY \
  pnpm exec ampx sandbox
```

`KNOWLEDGE_BASE_ID`は第2章の`setup.sh`が表示した値です。sandboxの起動後、`amplify_outputs.json`の`custom`に、RuntimeのARN（`agentCoreRuntimeArn`）、GatewayのURL（`gatewayUrl`）、MemoryのID（`memoryId`）が書き込まれます。

### 4. Atlassian認証情報をSecrets Managerへ格納

`backend.ts`のCDKで作成された`support-agent/atlassian`シークレットに、1-4で発行したメールアドレスとAPIトークンをJSON形式で投入します。リポジトリにはコミットせず、デプロイ環境ごとに個別に登録してください。

```bash
aws secretsmanager put-secret-value \
  --secret-id support-agent/atlassian \
  --secret-string '{"email":"<your-atlassian-email>","apiToken":"<your-api-token>"}'
```

Lambda関数は`get_atlassian_credentials()`内で`SecretsManager:GetSecretValue`を1回だけ呼び、コンテナが生きている間はモジュール変数にキャッシュします。シークレットを更新したい場合は、Lambdaを再デプロイするかコンテナをリサイクルすると新しい値が反映されます。

### 5. ローカルCLIでのエージェント確認

本文のコード4-4と同じく、まずKnowledge Baseの検索だけで動かせます。

```bash
cd sample-code/04
export AWS_REGION=us-east-1
export MODEL_ID=us.anthropic.claude-sonnet-4-6
export KNOWLEDGE_BASE_ID=<第2章のKnowledge Base ID>

uv run run_gateway_agent.py --interactive
```

手順3でGatewayを作った後は`GATEWAY_URL`も入れるとConfluenceとJiraのツールが加わります。値は`amplify-ui/amplify_outputs.json`の`custom.gatewayUrl`から取り出せます。

```bash
export GATEWAY_URL="$(node -p "require('./amplify-ui/amplify_outputs.json').custom.gatewayUrl")"
uv run run_gateway_agent.py --interactive
```

使用するGatewayツールを絞り込む場合:

```bash
uv run run_gateway_agent.py \
  --gateway-tool confluence_search \
  --gateway-tool create_jira_ticket \
  --question "東京リージョンのファイルアップロード障害について確認してください。"
```

セッションをまたいだ会話継続を試す場合は、Memoryの値を渡します（MemoryリソースIDは`amplify_outputs.json`の`custom.memoryId`に出力されます）。

```bash
uv run run_gateway_agent.py \
  --interactive \
  --memory-id "YOUR_MEMORY_ID" \
  --actor-id customer-C12345 \
  --session-id incident-20260405-001
```

### 6. 検証用UIの起動（ローカル開発）

```bash
cd sample-code/04/amplify-ui
pnpm run dev
```

表示されたURLを開き、Cognitoのアカウントを作成してログインしてください。

## 後片付け

第5章で`GATEWAY_URL`を使ったエージェント評価をする場合は評価が終わってから削除します。

```bash
cd sample-code/04/amplify-ui
pnpm exec ampx sandbox delete
```

`support-agent/atlassian`のシークレットはsandboxと一緒に削除予定になります。Atlassianのサイトとトークンの扱いは[リソース削除手順](../../ハンズオン後のリソース削除手順.md)を参照してください。

## 補足

- Runtime・Gateway・Lambda target・MemoryはすべてCDK（`backend.ts`）で定義されます。手動でのGatewayターゲット登録は不要です
- コンテナイメージは`@cdklabs/deploy-time-build`の`ContainerImageBuild`によりCodeBuildでARM64ビルドされます
- `run_gateway_agent.py`はエージェントをローカルで動かす例です。AWS上のRuntimeの確認には、Amplify画面またはInvokeAgentRuntime APIを使います
- Gatewayのツール名は`TargetName___tool_name`のようなプレフィックス付きで表示されることがあります。CLIサンプルでは`confluence_search`のようなサフィックスでもフィルタリングできます
- 本番環境では、Gatewayの認証設定とAtlassian Lambdaの権限を適切に絞ってください

[第5章の評価へ](../05/README.md)・[Gatewayのモデル接続を試す補足](gateway-extensions/README.md)・[作業後のリソース削除](../../ハンズオン後のリソース削除手順.md)
