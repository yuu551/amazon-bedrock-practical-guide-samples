# Aurora PostgreSQLをKnowledge Basesのベクトルストアにする

Aurora Serverless v2とpgvectorを使い、第2章の文書をKnowledge Basesへ取り込むサンプルです。SQLによる保存内容の確認、セマンティック／ハイブリッド検索、メタデータフィルタ、回答生成、第3章の18問による検索評価まで実行します。

手順の理由と実測結果は[補足本文](../../../supplements/chapter02/AuroraでKnowledge-Basesの検索を検証する.md)で説明します。

## 作成するリソースと費用

専用VPCと2サブネット、セキュリティグループ、Aurora PostgreSQLのServerless v2 Writer 1台、S3バケット、Secrets Managerのシークレット、Knowledge Bases用IAMロール、Knowledge BaseとS3データソースを作成します。Auroraは0〜2 ACUで稼働し、1時間操作がないと自動で停止します。NAT Gatewayは作成しません。

Auroraの稼働・ストレージ・I/O、Secrets Manager、S3、埋め込み・回答生成などの利用料金が発生します。検証後は`cleanup --yes`を実行してください。検証専用DBのため、削除時に最終スナップショットを作成しません。

## 前提

- Python 3.11以降、uv、AWS CLI。
- `us-east-1`でAurora PostgreSQL 16.14とData APIが利用できること。別バージョンは`deploy --engine-version`で指定できます。
- シェルにAWS認証情報が設定されていること。スクリプトはプロファイルを指定せず、boto3の通常の認証情報解決を使います。
- 実行者はCloudFormation、EC2のVPC関連、RDS、Secrets Manager、S3、IAMロール作成・PassRole、BedrockのKnowledge Base操作・モデル呼び出しを実行できる必要があります。Knowledge Base用のロールはテンプレートで対象リソースへ限定します。
- 第2章の`sample-data`と第3章の`data/questions.jsonl`を含むリポジトリ全体を取得してください。

## 構築・検証

リポジトリのルートから実行します。

```bash
cd sample-code/supplements/aurora-kb
uv sync --frozen
aws sts get-caller-identity
uv run python -m unittest discover -v
uv run python aurora_kb.py deploy --yes
uv run python aurora_kb.py ingest
uv run python aurora_kb.py inspect
uv run python aurora_kb.py verify
uv run python aurora_kb.py evaluate
```

`deploy`はCloudFormationの作成完了を待ち、`schema.sql`を実行してからKnowledge Baseを作ります。状態は`.state/state.json`へ保存し、パスワードは書き込みません。DBユーザー用のパスワードはSecrets Managerで生成し、初期設定中のメモリ上でSQLへ渡します。

`verify`は2種類の検索方式で、2026年4月の監査ログポリシーに絞り込めること、Enterpriseの保持期間が730日であること、回答に出典が付くことを確認します。既定の回答生成モデルはClaude Sonnet 4.6のUS推論プロファイルです。別の対応モデルは`--model-arn`で指定します。

`evaluate`は回答生成を行わず、検索結果を正解文書と照合します。18問に対してSEMANTIC・HYBRIDそれぞれのフィルタなし／ありを実行し、計72回の検索を行います。取得件数は5チャンクです。APIエラーはスキップせず停止し、そこまでの結果を保存します。

| 出力 | 内容 |
|---|---|
| `.state/ingestion.json` | 同期件数と元文書のSHA-256 |
| `.state/inspection.json` | PostgreSQL／pgvectorのバージョン、件数、次元、インデックス、メタデータ |
| `.state/verification.json` | 代表質問の回答と出典・検索本文 |
| `.state/evaluation.json` | 質問別の検索結果、正解文書、フィルタ、スコア、所要時間 |
| `.state/evaluation-summary.json` | Hit@5、Recall@5、MRR@5 |
| `.state/cleanup.json` | 削除完了の確認 |

## 中断した場合と削除

構築途中で止まった場合は状態ファイルを保持したまま次を実行すると、記録済みのスタックからSQL設定とKnowledge Baseの作成を再開します。

```bash
uv run python aurora_kb.py setup
```

CloudFormationがロールバックした場合は再開できません。失敗理由をCloudFormationのイベントで確認し、以下の削除を済ませてから新しい状態ファイルで作り直してください。

```bash
uv run python aurora_kb.py cleanup --yes
```

削除はKnowledge Base、専用S3バケット内のオブジェクト、CloudFormationスタックの順です。スタックIDと用途タグを照合し、状態ファイルと実行先アカウントが一致することも確認します。処理が中断した場合は同じコマンドで削除を再開します。

2回目の検証では、過去の結果を残して新しい状態パスを指定します。以後のすべてのコマンドに同じ`--state`を付けてください。

```bash
uv run python aurora_kb.py deploy --state .state/second/state.json --yes
uv run python aurora_kb.py ingest --state .state/second/state.json
uv run python aurora_kb.py cleanup --state .state/second/state.json --yes
```

`.state/`には接続先やアカウント情報が含まれるため、Gitへ追加しません。公開する検証結果は`results/`に整理します。

## 保存結果の再採点

採点用の正解文書だけを修正した場合は、保存済みの検索結果を再採点できます。質問文やフィルタが変わっている場合は停止するため、その場合は`evaluate`で検索から実行してください。

```bash
uv run python rescore.py --input-dir .state --output-dir results/rescored
```

2026年9月19日の結果は[results/2026-09-19](results/2026-09-19/)に保存しています。

## OpenSearch・S3 Vectorsとの比較

付録の保存済みログとAuroraの結果を、共通の18問・同じ採点関数で比較します。文書や質問が比較元と異なる場合は停止します。AWSへの再接続は行いません。

```bash
uv run python compare_saved_results.py
```

結果は`results/2026-09-19/cross-store-evaluation.json`と`cross-store-summary.json`に保存します。測定日、料金の計算条件、比較元は[結果のREADME](results/2026-09-19/README.md)を参照してください。
