# 第2章インフラ構築スクリプト

第2章のKnowledge BaseをCLIで構築する補助スクリプトです。本編のコンソール手順に代えて使えます。同じ構成を別の環境に作るときや、作成・削除のAPI呼び出しを確認したいときに利用してください。

実行前に[共通のAWS認証と費用の案内](../../README.md)を確認します。構築を終えたら[第2章の実行手順](../README.md)へ戻ってください。

2種類のベクトルストアに対応します。

| ベクトルストア | セットアップ | 片付け | 特徴 |
|---|---|---|---|
| OpenSearch Serverless Classic | `setup.sh` | `teardown.sh` | 本書本編と同じ構成。ハイブリッド検索対応、常時ウォーム |
| S3 Vectors | `setup-s3vectors.sh` | `teardown-s3vectors.sh` | 常駐課金なしでコストを抑えたい検証用途向け。セマンティック検索のみ |

## OpenSearch Serverless Classic版

### セットアップ

```bash
cd sample-code/02/infra
bash setup.sh
```

- S3バケット、IAMロール、AOSSコレクション、ベクトルインデックス、Knowledge Base、データソース、取り込みジョブまで順に作成
- 完了時に`KNOWLEDGE_BASE_ID`・`DATA_SOURCE_ID`・`MODEL_ARN`・`MODEL_ID`の`export`コマンドを表示（`sample-code/02`で貼り付けて使う）
- 生成された各リソースID・名前は`infra/.env`に保存される

### 削除

```bash
cd sample-code/02/infra
bash teardown.sh
```

- `.env`を読み取り、逆依存順で削除
- 実行時に`yes`を入力しないと削除されない安全装置あり

### 構成

| リソース | 名前 / 値 |
|---|---|
| S3 バケット | `ch02-kb-sample-data-<SUFFIX>` |
| Knowledge Baseのサービスロール | `ch02-kb-service-role-<SUFFIX>` |
| AOSS コレクション | `ch02-kb-<SUFFIX>`（VECTORSEARCH） |
| ベクトルインデックス | `ch02-kb-index`（faiss + HNSW, 1024次元, l2） |
| 埋め込みモデル | `amazon.titan-embed-text-v2:0` |
| Knowledge Base | `ch02-kb-<SUFFIX>` |
| データソース | `ch02-s3-source`（`s3://.../sample-data/`） |

### 注意

- このClassic版スクリプトでは、冗長構成を有効にした合計2 OCUの最小容量を基準に費用を見積もります。検索していない間も稼働料金がかかるため、使い終えたら`teardown.sh`で削除してください
- `AWS_REGION`は既定`us-east-1`。別リージョンの場合は`export AWS_REGION=ap-northeast-1`等で切り替え可能（ただし Titan Embeddings V2 が提供されている必要あり）
- ベクトルインデックスの作成には`opensearch-py`を一時的に利用します（スクリプト内で自動インストール）

## NextGenや直接検索を試す場合

本編の手順にはClassic版かS3 Vectors版を使ってください。NextGenの接続確認と直接検索は、[補足記事](../../../supplements/chapter02/OpenSearch-Serverless-NextGenをKnowledge-Basesで使えるか試す.md)で扱っています。

S3 VectorsをAPIから直接操作する手順は、[付録のサンプル](../../appendix/README.md)を参照してください。

## S3 Vectors 版

### セットアップ

```bash
cd sample-code/02/infra
bash setup-s3vectors.sh
```

- S3バケット、IAMロール、**S3 vector bucket + vector index**、Knowledge Base、データソース、取り込みジョブまで順に作成
- 各リソースID・名前は`infra/.env.s3vectors`に保存される
- デフォルトは検索精度比較用の33件のマークダウン文書。取り込み統計が33件・失敗0件でなければエラー終了
- AOSS版と比べてベクトルインデックスの準備が早く、常駐課金もないため検証用途向き
- 内容が重複する変換元の`faq.xlsx`は検索対象から除外
- 完了時の`export`の表示はOpenSearch Serverless版と同じ。第3章以降も同じ変数で進められる
- S3 Vectorsはハイブリッド検索に対応していないため、第3章の比較ではハイブリッド検索が自動で飛ばされる。リランキングも試す場合は下の`ENABLE_RERANKING=true`で作る

Knowledge BasesのリランキングをこのKnowledge Baseで使う場合だけ、サービスロールへ必要な権限を追加します。

```bash
ENABLE_RERANKING=true bash setup-s3vectors.sh
```

`bedrock:Rerank`はリソースレベル権限に対応していないため、この設定では同アクションだけ`Resource: "*"`になります。実際に呼び出せるリランキングモデルは、別の`bedrock:InvokeModel`ステートメントでCohere Rerank 3.5のARNに限定しています。既定値は`false`です。

### 削除

```bash
cd sample-code/02/infra
bash teardown-s3vectors.sh
```

### 構成

| リソース | 名前 / 値 |
|---|---|
| S3 バケット（ドキュメント） | `ch02-kbsv-sample-data-<SUFFIX>` |
| Knowledge Baseのサービスロール | `ch02-kbsv-service-role-<SUFFIX>` |
| S3 vector bucket | `ch02-kbsv-vectors-<SUFFIX>` |
| ベクトルインデックス | `ch02-kbsv-index`（float32, 1024次元, cosine） |
| 埋め込みモデル | `amazon.titan-embed-text-v2:0` |
| Knowledge Base | `ch02-kbsv-<SUFFIX>` |
| データソース | `ch02-s3-source`（`s3://.../sample-data/`） |

### OpenSearch Serverless版との違い

| 項目 | S3 Vectors版で確認すること |
|---|---|
| 検索タイプ | S3 Vectorsの一次取得はセマンティック検索のみ。ハイブリッド検索は使えないが、Knowledge Basesの`rerankingConfiguration`によるリランキングは使える |
| メタデータ制約 | Knowledge Basesに付けるカスタムメタデータは、ベクトル1件あたり1KB、最大35キーです。これはS3 Vectors全体のメタデータ上限とは異なります。[Knowledge Basesとの連携条件](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-bedrock-kb.html)を確認してください |
| インデックス設定 | `AMAZON_BEDROCK_TEXT`と`AMAZON_BEDROCK_METADATA`をnon-filterableにする。OpenSearch Serverlessの`AMAZON_BEDROCK_TEXT_CHUNK`と混同しない |
| 応答時間 | データ量や検索条件によって変わります。検索時間と回答生成時間を分けて測り、アプリに必要な応答時間を満たすか確認してください |
| リージョン | S3 Vectors が提供されているリージョンで動作（us-east-1 / us-east-2 / us-west-2 / eu-central-1 / ap-southeast-2 等。最新情報は AWS 公式を参照） |
| コスト | OCUの常駐費用はないが、ベクトルの保存・書き込み・検索には料金が発生する。[費用の確認](../../README.md#費用の確認)を参照 |
