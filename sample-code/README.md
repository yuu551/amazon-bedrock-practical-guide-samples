# サンプルコード

『Amazon Bedrock実践ガイド』のサンプルを実行するための共通案内です。必要なツールとAWS認証を準備し、取り組む章のREADMEへ進んでください。

[必要なツール](#ツール)・[AWS認証](#認証情報)・[時間と費用](#章ごとの所要時間とコストの目安)・[章の依存関係](#章の依存関係)

章別のコードの一覧、作業を終えるときの片付け、困ったときの問い合わせ先は[サポートリポジトリのトップ](../README.md)にまとめています。

## 前提条件

### ツール

| ツール | バージョン | 用途 |
|---|---|---|
| Python | 3.11以上（第6章の一部は3.12以上） | CLIスクリプト、Lambda |
| [uv](https://docs.astral.sh/uv/) | 最新版 | Python依存管理 |
| Node.js | 24（各アプリの`mise.toml`とCodespacesも24） | フロントエンド、CDK |
| pnpm | 10.34.5 | Node.js依存管理 |
| AWS CLI v2 | 最新版 | S3操作、認証情報の管理 |

ローカル環境に`pnpm`がない場合は、Node.jsに同梱される`npm`で本書のサンプルと同じバージョンを導入します。

```bash
npm install --global pnpm@10.34.5
pnpm --version
```

### AWSアカウント

Bedrockのモデル呼び出しと、各章で作成するリソースを操作できるAWS認証情報を用意します。モデルを利用できるかどうかは、呼び出し権限、モデルの提供リージョン、初回利用の手続きによって変わります。

AWS Marketplace経由のモデルを初めて利用するときは、契約に必要な権限と支払い方法も確認してください。AnthropicモデルをBedrock Runtimeから使う場合は、初回の利用目的申請が必要です。詳しい条件は[AWS公式のモデルアクセス手順](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)を参照してください。

### AWSリージョン

本書のサンプルコードでは`us-east-1`を使用します。Cross-Region Inference Profileを使用するスクリプトでは、モデルIDに`us.`プレフィックス付きのIDを指定しています（例: `us.anthropic.claude-sonnet-4-6`）。リージョンやモデルIDは環境に合わせて変更してください。

本編のサンプルが既定で使うモデルは、Claude Sonnet 4.6（`us.anthropic.claude-sonnet-4-6`）です。執筆後に公開されたClaude Haiku 5.5（`us.anthropic.claude-haiku-5-5`）は単価がSonnet 4.6の約30分の1（入力が10万トークン以下の場合）で、第5章の評価ケースで比べた回答の質もほぼ同じでした。モデルIDを切り替えれば各章のサンプルで使え、Knowledge Basesの`RetrieveAndGenerate`での回答生成にも使えます。費用を抑えて試したいときは、Haiku 5.5に切り替えるのもおすすめです。ただし、アカウントによっては新しいモデルに利用制限がかかっていることがあります。設定の違いや比較の結果は、補足の[本編のコードで最新モデルを使う](../supplements/chapter01/本編のコードで最新モデルを使う.md)を参照してください。

サンプルには、Haiku 5.5やSonnet 5.5のような新しいモデルを指定したときに`temperature`を送らない分岐を加えています。本文に載せたコードは既定のSonnet 4.6向けの形で、`temperature`の指定が残っています。既定のモデルで動かす場合の動きは変わりません。

### 認証情報

AWS CLIのプロファイル、SSO、または設定済みの環境変数で認証します。Codespacesでは付録の手順どおり`aws login --remote`で認証できます。一時認証情報を環境変数へ設定する場合はアクセスキーとシークレットキーに加えて`AWS_SESSION_TOKEN`も必要です。

```bash
# プロファイルを使う場合
export AWS_PROFILE=your-profile

# リージョンの明示指定（スクリプトごとにデフォルトが異なる場合があります）
export AWS_REGION=us-east-1
```

## ハンズオンの進め方

第2章で作ったKnowledge Baseは第3〜5章で再利用します。途中の章から試す場合は、[章の依存関係](#章の依存関係)を確認して準備します。

### 章ごとの所要時間とコストの目安

所要時間は読みながら手を動かす想定の概算です。コストはご自身のリージョン・推論量・実行頻度により上下します。

| 章 | やること | 目安所要時間 | 主な発生コスト |
|---|---|---|---|
| 第1章 | Bedrock API、Guardrails、Strands Agents最小例 | 30分〜1時間 | モデルの入出力トークン、Guardrails。weatherDemoをデプロイした場合はAgentCore Runtime・Memory |
| 第2章 | サンプルデータ準備、Knowledge Base構築、検証UIデプロイ | 2〜3時間 | OpenSearch Serverless常駐費（S3 Vectors版なら常駐費なし） + Bedrock推論 + Embedding |
| 第3章 | メタデータフィルタ・ハイブリッド・リランキング・クエリ分解 | 3〜4時間 | 第2章のKnowledge Baseを継続。比較用の追加Knowledge Baseで増 |
| 第4章 | AgentCore RuntimeとGatewayでのエージェント構築、Atlassian連携 | 3〜5時間 | AgentCore Runtime・Gateway、Lambda、Atlassianの契約プラン |
| 第5章 | バッチ評価、LLM-as-a-Judge、フィードバックからのKnowledge Base更新 | 2〜3時間 | Bedrock推論（Judge用に複数モデル） |
| 第6章 | CDK化、Guardrail本番運用、Cedar・Interceptor、CloudWatch | 4〜6時間 | Amplify版はAgentCore Runtime・Gateway、Knowledge Base（既定のベクトルストアはS3 Vectors）。CDK版は各スタックのサービス（CloudWatch・Secrets Manager等）。`SupportAiProd`まで作ると同じ構成がもう1つ増える |
| 付録 | S3 Vectorsでの小規模RAG、Managed Knowledge Baseとの比較 | 1〜2時間 | S3 Vectorsの保存・検索、Bedrock推論 + Embedding |

### 費用の確認

CodespacesとAWSの料金は別に発生します。モデルの呼び出し回数だけでなく、保存データや起動したリソースの費用も確認してください。特にOpenSearch Serverless Classicは、検索していない間も稼働料金がかかります。

Codespacesは、GitHubの個人アカウントに毎月の無料枠があります。計算時間は稼働時間にコア数を掛けて数えるので、2コアで5時間使うと10コア時間です。停止中もストレージは消費します。

| 個人アカウントのプラン | 月の計算時間枠 | 2コアで使える時間 | ストレージ枠 |
|---|---:|---:|---:|
| GitHub Free | 120コア時間 | 60時間 | 15 GB-month |
| GitHub Pro | 180コア時間 | 90時間 | 20 GB-month |

使った量は[Billing画面](https://github.com/settings/billing)で確認できます。詳しくは[GitHub Codespacesの料金](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)を参照してください。作業を中断するときはCodespacesを停止し、使い終えたら削除します。

AWSの費用は、推論や検索を実行した分と、リソースやデータを残している分に分かれます。各サービスの単価は[AWS料金一覧](https://aws.amazon.com/pricing/)で確認できます。まず少数の質問で試し、使い終えたリソースは削除しましょう。

### ハンズオン後のリソース削除

使い終えたリソースは[リソース削除手順](../ハンズオン後のリソース削除手順.md)に沿って片付けます。特にOpenSearch Serverless ClassicのOCUは、検索していない間も料金がかかるので早めに削除してください。

## 章を通して作るアプリ

架空のSaaS企業「SampleConnect」のサポート担当者が使うアプリを作ります。第2章のRAGアプリに、第3章で検索設定の比較、第4章でツールを使うエージェントを加えます。第5章では回答を評価し、第6章では環境構築や運用をコードにまとめます。

章ごとのコードと手順は、トップのREADMEの章別一覧から開けます。

### 共通基盤: Amplify Gen 2

第2〜4章のフロントエンドはすべてAmplify Gen 2プロジェクトとして構成されています。

| 要素 | 内容 |
|---|---|
| 認証 | Cognito User Pool + Identity Pool（`<Authenticator>`コンポーネント） |
| バックエンド定義 | `amplify/backend.ts`（CDKでLambda・権限・出力を定義） |
| デプロイ | `pnpm exec ampx sandbox`（開発）/ Git連携（本番） |
| フロントエンド設定 | `amplify_outputs.json`（バックエンドリソースの参照を自動生成） |

各章の`backend.ts`で必要なリソースを定義しています。章が変わっても、Amplify sandboxでバックエンドを作成し、画面を起動する流れは共通です。

## 章ごとのセットアップ（共通パターン）

### Pythonプロジェクト

各章のディレクトリで`uv sync`を実行すると、依存パッケージがインストールされます。

```bash
cd sample-code/01
uv sync
```

スクリプトの実行には`uv run`を使用します。

```bash
uv run basic_converse.py
```

### フロントエンド（第2〜4章）

各章の`amplify-ui/`はAmplify Gen 2プロジェクトです。初回は`pnpm exec ampx sandbox`でバックエンド（Cognito・Lambda等）をデプロイし、`amplify_outputs.json`を生成します。

```bash
cd sample-code/02/amplify-ui   # 03/, 04/ も同様
pnpm install --frozen-lockfile

# バックエンドをデプロイ（初回のみ）
# 第2章で作ったKnowledge Base IDとモデルIDを環境変数で渡す
KNOWLEDGE_BASE_ID="$KNOWLEDGE_BASE_ID" MODEL_ID=us.anthropic.claude-sonnet-4-6 pnpm exec ampx sandbox

# フロントエンドの開発サーバー（別ターミナル）
pnpm run dev
```

`pnpm exec ampx sandbox`が`amplify_outputs.json`を生成し、フロントエンドはそこからCognito設定やFunction URLを読み取ります。章ごとに渡す環境変数が違うので、各章のREADMEの手順に従ってください。sandboxは`pnpm exec ampx sandbox delete`で削除します。

### CDK（第6章）

CDKを初めて使うアカウントとリージョンでは最初に一度だけbootstrapを実行します。Amplifyのsandboxを動かしたことがあるアカウントでは、済んでいることが多いです。

```bash
cd sample-code/06/cdk
pnpm install --frozen-lockfile
pnpm exec cdk bootstrap aws://<アカウントID>/us-east-1   # 初回のみ
pnpm exec cdk deploy SupportAiDev
```

デプロイの前に必要な環境変数は[第6章CDK版のREADME](06/cdk/README.md)を参照してください。

## 章の依存関係

前の章で用意するリソースと、後続の章で使うものの関係です。

| 取り組む章 | 先に用意するもの |
|---|---|
| 第1章 | 共通の実行環境とAWS認証 |
| 第2章 | 第1章で確認したモデルへのアクセス |
| 第3章 | 第2章のKnowledge Baseと取り込み済み資料 |
| 第4章 | 第2章のKnowledge Base。ConfluenceとJiraは第4章で準備 |
| 第5章 | 第2章または第3章のRAG API。エージェント評価では第4章の構成も使用 |
| 第6章 | 試す構成に応じて、第2〜5章の資料・評価ケース・既存リソース |

## 注意事項

- サンプルコードは書籍の学習用です。本番環境にそのまま適用する場合は、エラーハンドリングやセキュリティ設定を十分に検討してください
- モデルIDやARN、Knowledge Base IDなどのプレースホルダーは、ご自身の環境の値に置き換えてください
