# Amazon Bedrock実践ガイド

[『Amazon Bedrock実践ガイド』](https://gihyo.jp/book/2026/978-4-297-16011-1)（神野雄大 著、技術評論社）のサポートリポジトリです。サンプルコード、正誤表、オンライン補足をまとめています。

ぜひ本を見ながらハンズオンやコードを動かしたい際にご利用ください！

## 準備するもの

AWSアカウントと、Bedrockを使える権限の認証情報を用意してください。リージョンはデフォルトで`us-east-1`を使います。

サンプルを動かすと、AWSの利用料がかかります。特にOpenSearch Serverlessは、検索していない間も料金が発生するので、使い終わったら必ず削除してください。

章ごとの費用の目安は、[サンプルコードの共通案内](sample-code/README.md)にあります。

## 環境を用意する

GitHub Codespacesをおすすめします。

サンプルに必要なツールが最初から入っているので、自分のPCにインストールする必要がありません。

### GitHub Codespaces

Codespacesは、自分のGitHubアカウントのリポジトリで起動します。空のリポジトリを作って、このリポジトリのコードを登録してください。手順の詳細は、本書の付録「GitHub Codespacesによる開発環境のセットアップ」にあります。

```bash
git clone https://github.com/yuu551/amazon-bedrock-practical-guide-samples.git sample-repo
cd sample-repo
git remote rename origin upstream
git remote add origin https://github.com/<あなたのユーザー名>/sample-repo.git
git push -u origin main
```

自分のリポジトリでCodespaceを作成すると、環境ができます。ターミナルで`aws login --remote`を実行して、AWSにログインしてください。このリポジトリが更新されたときは、`git pull upstream main`で取り込めます。

### ご自身のPC

次のツールを用意し、AWSの認証を設定します。

- Python 3.11以上とuv
- Node.js 24とpnpm
- AWS CLI v2

```bash
git clone https://github.com/yuu551/amazon-bedrock-practical-guide-samples.git
cd amazon-bedrock-practical-guide-samples
```

## 第1章から進める

第1章から順に進めてください。

各章のREADMEに、ファイルの説明、セットアップ、実行コマンドがあります。第3〜5章は、第2章で作るKnowledge Baseを使います。

| 章 | 本のタイトル | 取り組むこと |
|---|---|---|
| 第1章 | [Amazon Bedrockによる業務効率化の現在地と基礎知識](sample-code/01/README.md) | モデルの呼び出し、Guardrails、最小のエージェント |
| 第2章 | [Amazon Bedrock Knowledge Basesで始めるドキュメント活用RAGの構築](sample-code/02/README.md) | 資料を検索し、画面から質問する |
| 第3章 | [ドキュメント活用RAGの精度を高める改善テクニック](sample-code/03/README.md) | メタデータや検索設定を変え、回答と根拠を比較する |
| 第4章 | [AIエージェントで実現する情報収集と問い合わせ対応の自動化](sample-code/04/README.md) | AgentCore RuntimeとGatewayでツールを呼び出す |
| 第5章 | [RAG・AIエージェントの品質評価と改善サイクル](sample-code/05/README.md) | 回答を評価し、資料の更新による変化を確かめる |
| 第6章 | [本番環境で安定稼働させるための設計](sample-code/06/README.md) | CDKで構成を管理し、監視や更新の処理を加える |
| 付録 | [Amazon S3 Vectorsによる小規模RAG向けベクトルストアの活用](sample-code/appendix/README.md) | S3 Vectorsでの登録・検索・リランキング |
| 付録 | [Managed Knowledge Baseで始めるフルマネージドRAG](sample-code/appendix/README.md) | 第2章の構成との比較、横断検索 |

## コーディングエージェントに手伝ってもらう

このリポジトリには、ハンズオンを手伝うエージェントスキル（`.agents/skills/bedrock-handson`）を入れています。Agent Skillsに対応したコーディングエージェントでリポジトリを開き、「第2章を始めたい」「このエラーはどうすればいい？」「作ったリソースを片付けたい」と相談してください。準備の確認、エラーの切り分け、後片付けを案内します。料金がかかるコマンドは、実行の前にあなたの了承を得るよう指示しています。Claude Codeは`.claude/skills`、Codexは`.agents/skills`、Kiroは`.kiro/skills`から読み込みます。スキルを使えないエージェントには、`AGENTS.md`を読ませてください。

## 作業を終えるとき

ブラウザやCodespacesを閉じても、AWSのリソースは残ります。[リソース削除手順](ハンズオン後のリソース削除手順.md)に沿って、作った環境を削除してください。

## 困ったとき

エラーが出たときは、まず[ハンズオンFAQ](ハンズオンFAQ.md)を確認してください。解決しないときや、誤りを見つけたときは、[Issues](https://github.com/yuu551/amazon-bedrock-practical-guide-samples/issues)から気軽にお知らせください！ 章とファイル、実行したコマンド、期待した結果と実際の結果を添えていただけると助かります。アクセスキーやAPIトークン、業務資料は貼らないでください。修正のPull Requestは[変更の送り方](CONTRIBUTING.md)を参照してください。

## 正誤表

本文の訂正と、執筆後の機能追加で本文の記述が変わった箇所は、[正誤表](正誤表.md)に載せます。現在、本文の誤りとして訂正する項目はありません。執筆後の機能追加による変更として、第1章のコンテキストグラウンディングチェックを1件掲載しています。

執筆後の機能追加による変更として、第1章のコンテキストグラウンディングチェック（Standardティアの追加と、複数ターンの会話での使い方）を掲載しています。

## オンライン補足

本書の執筆後に登場したモデルや機能を紹介する内容です。

関心のある記事からぜひ読んで理解を深めるのに活用いただけると嬉しいです。
詳しい説明は、[オンライン補足の一覧](supplements/README.md)にあります。

| 章 | 記事 |
|---|---|
| 第1章 | [本編のコードで最新モデルを使う](supplements/chapter01/本編のコードで最新モデルを使う.md) |
| 第1章 | [AgentCore CLI 1.0.0で第1章の手順を進める](supplements/chapter01/AgentCore-CLI-1.0.0で第1章の手順を進める.md) |
| 第1章 | [コンテキストグラウンディングチェックにStandardティアが追加された](supplements/chapter01/コンテキストグラウンディングチェックにStandardティアが追加された.md) |
| 第2章 | [AuroraでKnowledge Basesの検索を検証する](supplements/chapter02/AuroraでKnowledge-Basesの検索を検証する.md) |
| 第2章 | [OpenSearch Serverless NextGenをKnowledge Basesで使えるか試す](supplements/chapter02/OpenSearch-Serverless-NextGenをKnowledge-Basesで使えるか試す.md) |
| 第2章 | [Amplifyコンソールからアプリを公開する](supplements/chapter02/Amplifyコンソールからアプリを公開する.md) |
| 第3章 | [生成AIでPDFを解析して取り込む](supplements/chapter03/生成AIでPDFを解析して取り込む.md) |
| 第3章 | [第3章のRAGアプリで回答の過程を可視化する](supplements/chapter03/第3章のRAGアプリで回答の過程を可視化する.md) |
| 第4章 | [ツール・エージェント・モデルの入口をAgentCore Gatewayで統制する](supplements/chapter04/ツール・エージェント・モデルの入口をAgentCore-Gatewayで統制する.md) |
| 第5章 | [RAGASでエージェントのツール呼び出しと目的達成を評価する](supplements/chapter05/RAGASでエージェントのツール呼び出しと目的達成を評価する.md) |
| 第6章 | [エージェントとスキルをAWS Agent Registryで組織管理する](supplements/chapter06/エージェントとスキルをAWS-Agent-Registryで組織管理する.md) |
| 第6章 | [CloudWatch Omniでエージェントを調べて改善する](supplements/chapter06/CloudWatch-Omniでエージェントを調べて改善する.md) |
| 第6章 | [BedrockのコストをCost Explorerでモデル別に見る](supplements/chapter06/BedrockのコストをCost-Explorerでモデル別に見る.md) |
| 付録 | [Managed Knowledge Baseをアプリに組み込んでストリーミングする](supplements/managed-knowledge-bases/Managed-Knowledge-Baseをアプリに組み込んでストリーミングする.md) |
| 付録 | [Managed Knowledge Baseのデータソースを定期同期する](supplements/managed-knowledge-bases/Managed-Knowledge-Baseのデータソースを定期同期する.md) |
| 付録 | [S3 Vectorsのメタデータフィルタが事前フィルタリングに対応](supplements/s3-vectors/S3-Vectorsのメタデータフィルタが事前フィルタリングに対応.md) |
