# 付録 サンプルコード

コードがある付録は2つです。

- 付録「Amazon S3 Vectorsによる小規模RAG向けベクトルストアの活用」
- 付録「Managed Knowledge Basesで始めるフルマネージドRAG」

AtlassianサイトのセットアップとGitHub Codespacesの付録は、手順だけでコードはありません。実行前に[共通の準備](../README.md)を済ませてください。

## ファイル構成

### S3 Vectors

| ファイル | 内容 |
|---|---|
| `s3_vectors_standalone.py` | ベクトルバケットの作成、ベクトルの登録、検索、ハイブリッド検索、RAG回答生成を1つのスクリプトで実装 |
| `s3_vectors_rerank.py` | S3 Vectors版のKnowledge Basesにリランキングを適用し、前後の順位を比較 |
| `s3vectors_opensearch_comparison.py` | S3 VectorsとOpenSearch Serverlessを、セマンティック検索・ハイブリッド検索で比較 |

### Managed Knowledge Bases

| ファイル | 内容 |
|---|---|
| `managed_kb_comparison.py` | Managed Knowledge Baseと、第2章のOpenSearch Serverless版を、同じ質問で比較 |
| `managed_kb_cross_search.py` | S3用とConfluence用のKnowledge Baseを作り、Agentic Retrievalで横断検索 |
| `managed_kb_full_test.py` | Knowledge Baseの作成から、検索、Agentic Retrieval、Strands Agentの呼び出しまでを通しで確認 |

`rag_answer_quality.py`は、回答の生成と、正確性・完全性・根拠性の判定をまとめた共通のコードです。

## セットアップ

```bash
cd sample-code/appendix
uv sync
```

## S3 Vectors

第2章でKnowledge Basesに任せていた、ベクトル化・登録・検索を、コードで確認します。`setup`、`query`、`rag`の順に試してください。

```bash
uv run s3_vectors_standalone.py setup
uv run s3_vectors_standalone.py query --question "SAML SSOでログインできない場合の確認項目を教えてください"
uv run s3_vectors_standalone.py rag --question "SAML SSOでログインできない場合の確認項目を教えてください" --model-id us.anthropic.claude-sonnet-4-6
```

- `setup`はサンプルFAQをTitan Text Embeddings V2で埋め込み、ベクトルバケットへ登録します
- `query`は`QueryVectors` APIを直接呼び出すセマンティック検索です。`--filter '{"department": {"$eq": "Support"}}'`でメタデータの絞り込みもできます
- `rag`は検索結果をもとにClaudeが回答を作ります

終わったら、バケットとインデックスを削除します。残すと、保管料がかかり続けます。

```bash
uv run s3_vectors_standalone.py cleanup
```

### ハイブリッド検索

S3 Vectorsはキーワード検索に対応していないため、このサンプルでは、セマンティック検索と、文字3-gramのBM25検索を別々に実行し、Reciprocal Rank Fusion（RRF）で順位を統合します。BM25は仕組みを確認するための小規模な実装で、本番ではキーワード検索の基盤を別に用意します。

```bash
uv run s3_vectors_standalone.py hybrid --question "Enterpriseプランの監査ログは何日保存されますか"
```

### Knowledge Basesでリランキングを試す

第2章のセットアップはリランキングの権限を付けないので、`ENABLE_RERANKING=true`を指定して作ります。リポジトリのルートから実行してください。

```bash
cd sample-code/02
ENABLE_RERANKING=true bash infra/setup-s3vectors.sh
set -a; source infra/.env.s3vectors; set +a

cd ../appendix
KNOWLEDGE_BASE_ID="$KB_ID" uv run python s3_vectors_rerank.py
```

指定を忘れると、リランキングが`AccessDeniedException`になります。確認が終わったら、第2章の`bash infra/teardown-s3vectors.sh`で削除します（確認には`yes`と入力します）。

### CLIで試す

実装を読まずに登録と検索だけ試すなら、AWS Labsの[`s3vectors-embed-cli`](https://github.com/awslabs/s3vectors-embed-cli)も使えます（`uv tool install s3vectors-embed-cli`）。ファイルを1件のベクトルとして登録し、文書の分割はしません。

## Managed Knowledge Bases

比較と横断検索は、先に対象のKnowledge Baseが必要です。

### 検索と回答の比較

Managed Knowledge Baseと、第2章のOpenSearch Serverless版に、同じ質問を送って比べます。

```bash
uv run python managed_kb_comparison.py \
  --managed-kb-id <Managed Knowledge BaseのID> \
  --managed-data-source-id <Managed側のData Source ID> \
  --customer-kb-id <OpenSearch Serverless版Knowledge BaseのID> \
  --customer-data-source-id <OpenSearch Serverless側のData Source ID> \
  --answer-model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --judge-model-id us.anthropic.claude-sonnet-4-6 \
  --use-question-filters
```

### 横断検索

S3用とConfluence用のKnowledge Baseを作り、Agentic Retrievalで横断して検索します。必要な値は、引数か環境変数で明示します。

```bash
uv run python managed_kb_cross_search.py --create \
  --role-arn arn:aws:iam::<account-id>:role/<managed-kb-service-role> \
  --s3-bucket <your-data-bucket> \
  --confluence-secret-arn arn:aws:secretsmanager:us-east-1:<account-id>:secret:<name> \
  --confluence-host https://your-instance.atlassian.net \
  --confluence-space <space-key> \
  --name-suffix <unique-run-name> \
  --cleanup
```

- `--cleanup`を付けると、完了後（途中で失敗した場合も）、作ったKnowledge Baseとデータソースを削除します
- 同じアカウントで再実行するときは、`--name-suffix`で一意な名前を指定します
- 既存のKnowledge Baseだけを削除する場合は、誤削除を防ぐため`--cleanup --force-cleanup`を付けます

## 環境変数（S3 Vectors）

| 変数 | 用途 | デフォルト |
|---|---|---|
| `VECTOR_BUCKET_NAME` | ベクトルバケット名 | `sampleconnect-support-faq-vectors` |
| `INDEX_NAME` | ベクトルインデックス名 | `support-faq-index` |
| `AWS_REGION` | AWSリージョン | `us-east-1` |

## 注意事項

- S3 Vectorsに対応したリージョン（執筆時点では`us-east-1`など）で実行してください
- `amazon.titan-embed-text-v2:0`を使えるようにしてください
- 作ったリソースは、使い終わったら[リソース削除手順](../../ハンズオン後のリソース削除手順.md)に沿って削除してください
