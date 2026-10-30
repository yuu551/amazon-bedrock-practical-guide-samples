# 第3章 サンプルコード

第2章で構築したRAGの精度を改善するための比較・検証スクリプトと、本文のRAGポータル（`amplify-ui`）です。チャンク戦略やパーサーの比較、メタデータ生成、検索設定の効果測定、カテゴリルーティングを扱います。

検索設定の比較とRAGポータルには第2章で作成・同期したKnowledge Baseを使います。チャンク戦略とパーサーの比較、カスタムチャンキングの例では比較用のKnowledge Baseを別に作ります。

まず[セットアップ](#セットアップ)を済ませ、[検索設定の比較](#検索設定の比較)から試してください。本文の画面を動かす場合は[RAGポータルを動かす](#ragポータルを動かす)へ進みます。第5章の一括評価でも、ここで用意するAPIを使います。そのほかの手順は必要なものを選んで実行できます。

> [!NOTE]
> 第2章をS3 Vectors版で作った場合、ハイブリッド検索は使えません。`compare_rag_configs.py`に`--vector-store s3vectors`を付けると、ハイブリッド検索の段階を飛ばして比較します。本文の表3-12はOpenSearch Serverless版の結果です。

## ファイル構成

### スクリプト

| ファイル | 内容 |
|---|---|
| `compare_rag_configs.py` | 同じ質問セットを5段階の検索設定（baseline → hybrid+filter+rerank+decompose）で実行し、`source_recall`、`top_citation_hit`、`completeness`（簡易ジャッジ）などで比較 |
| `compare_topic_orchestration.py` | 網羅型質問セットで、`hybrid_only`、マネージドのQuery Decomposition、トピックフォルダ網羅型RAGの3方式を比較 |
| `compare_metadata_modes.py` | プロンプト方式とStructured Output方式のメタデータ生成を比較 |
| `compare_kb_variants.py` | パーサーやチャンク設定の異なるKnowledge Base同士を比較 |
| `generate_metadata_sidecars.py` | Bedrockモデルで`.metadata.json`サイドカーファイルを自動生成 |
| `generate_sample_pdfs.py` | Parser比較用の図表・グラフ入りサンプルPDFを生成（reportlab + matplotlib） |
| `llm_document_parse.py` | Converse APIでPDFを読み取り、確認・修正できるマークダウンへ変換 |
| `orchestrate_topic_rag.py` | トピックフォルダ単位のMapReduce型RAG。質問分析 → Knowledge BaseのRetrieveでトピック特定 → トピック内ファイル全読み + Haiku並列抽出 → Sonnet統合 |
| `s3_event_pipeline.py` | S3アップロードイベントからメタデータ生成→Knowledge Baseへの取り込みを自動化するLambdaサンプル |
| `common.py` | プロンプト構築、レスポンスパーサー、出典抽出、各種メトリクス、軽量ジャッジなどの共通ヘルパー |

### データ（`data/`）

| ファイル | 内容 |
|---|---|
| `questions.jsonl` | 5段階比較用の質問セット（expected_sources は配列で、複数ドキュメントが必要な比較にも対応） |
| `questions_decomposition.jsonl` | 複数ソース必須のdecomposition検証用質問セット |
| `questions_topic_orch.jsonl` | 機能トピック単位の網羅型質問セット（トピック内の複数文書を参照する質問。`compare_topic_orchestration.py`で使う） |
| `questions_parser.jsonl` | パーサー比較用の質問セット。表の列関係、図中ラベル、グラフのバー値など、パーサー差がはっきり出る題材で構成 |
| `questions_parser_retention.jsonl` | 本文掲載用。グラフ画像内にしかない保持期間とAPIレート制限を確認する質問セット |
| `questions_faq.jsonl` | 補足のカスタムチャンキング検証ワークフロー専用の質問セット（faq01〜faq05） |
| `metadata_schema.json` | メタデータ生成用のスキーマ定義 |
| `topic_config.json` | 5つの機能トピック（API レート制限・SAML SSO・監査ログ・課金・データ所在地）のIDと説明 |

### 図表入りサンプル（`sample-pdfs/`）

`generate_sample_pdfs.py`が出力するパーサー比較用PDFです。架空の製品「SampleConnect Platform」の表・図・グラフを使って、パーサーによる読み取りの違いを比較します。

| ファイル | 含まれる要素 | パーサー差が見えやすいポイント |
|---|---|---|
| `sampleconnect-plan-matrix.pdf` | カテゴリ列をマージしたプラン×機能マトリクス、料金表 | 表の列と行の対応（Enterpriseのログ保持期間は「2年間」か「90日」か） |
| `sampleconnect-architecture.pdf` | RAG参考アーキテクチャ図、コンポーネント一覧表 | 図中のコンポーネント名と矢印ラベル（「ベクトル検索」「チャンク格納」など） |
| `sampleconnect-retention-chart.pdf` | 対数スケールの棒グラフ2つ（保持期間・APIレート制限）、本文は値を持たない | グラフのバー上部ラベルだけに書かれた数値（「730日」「1,000 req/sec」） |
| `sampleconnect-incident-report.pdf` | 障害報告書、影響範囲と時系列 | 発生時刻や復旧時刻、影響を受けた機能の対応 |

### インフラ（`infra/`）

| ファイル | 内容 |
|---|---|
| `setup-chunking-kbs.sh` | 本文コード3-8のチャンク戦略比較用。固定長／階層的／セマンティックの3つのKnowledge Baseを1つのAOSSコレクション上に構築し、IDを`infra/.env.chunking`に書き出す |
| `teardown-chunking-kbs.sh` | `setup-chunking-kbs.sh`で作成したリソースを削除する |
| `setup-parser-kbs.sh` | Parser比較用に、デフォルトパーサーと基盤モデルパーサーのKnowledge Baseを1つのAOSSコレクション上に2つ構築する |
| `teardown-parser-kbs.sh` | `setup-parser-kbs.sh`で作成したリソースを削除する |
| `setup-custom-chunking-kbs.sh` | 補足のカスタムチャンキング検証ワークフロー専用。FAQのマークダウンと固定長／カスタム（NONE + Lambda）の2つのKnowledge Baseを構築する |
| `teardown-custom-chunking-kbs.sh` | `setup-custom-chunking-kbs.sh`で作成したリソースを削除する |
| `sync-topic-data.sh` | `sample-data/topics/`のトピックフォルダを、第2章のKnowledge Baseへ追加で同期する |
| `spin-up-cheap-rag.sh` | RAGポータルを安く試すための近道。第2章のS3 Vectors版Knowledge Baseを作り、`amplify-ui/.env`を用意する（ハイブリッド検索は使えない） |
| `teardown-cheap-rag.sh` | `spin-up-cheap-rag.sh`で作成したsandboxとKnowledge Baseを削除する |
| `lambda/custom_chunking/main.py` | POST_CHUNKING Lambda本体。`## Q??.`見出しで分割し、`contentMetadata`に`faq_id`と`question`を付与する |

### 出力の保存先

自分で実行した結果は`output/`に保存されます。本編の回答を読みたい場合は、次のフォルダを開いてください。

| 内容 | 保存先 |
|---|---|
| チャンク戦略ごとの回答 | [chunk-strategy-comparison](results/chunk-strategy-comparison/) |
| パーサーごとの回答 | [parser-retention-comparison](results/parser-retention-comparison/) |

### RAGポータル（`amplify-ui/`）

本文の図3-3やフィードバック画面のアプリです。Lambda関数（`amplify/functions/rag-handler/handler.ts`）がKnowledge Basesを呼び出し、回答をストリーミングで返します。手順は[RAGポータルを動かす](#ragポータルを動かす)を参照してください。

### 本文の出力例を再現するスクリプト

本文の出力例3-1〜3-4に添えた`scripts/verify_book_examples.py`はリポジトリのルートの`scripts/`にあります。[本文の出力例を再現する](#本文の出力例を再現する)を参照してください。

## セットアップ

```bash
cd sample-code/03
uv sync
```

第2章の`setup.sh`が最後に表示した`export`の行を、このターミナルでも実行しておきます。以降のコマンドはここで入れた`KNOWLEDGE_BASE_ID`、`DATA_SOURCE_ID`、`MODEL_ARN`、`MODEL_ID`を使います。表示を見失った場合は第2章の`infra/.env`（S3 Vectors版は`infra/.env.s3vectors`）から値を確認できます。

リランキングと簡易ジャッジを試す場合は次の値も入れます。

```bash
export RERANK_MODEL_ARN="arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0"
export JUDGE_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export METADATA_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
```

## RAGポータルを動かす

第2章のKnowledge Baseを使って、本文のRAGポータルをデプロイします。Node.js 24とpnpmを使います。Codespacesでは準備済みです。

```bash
cd sample-code/03/amplify-ui
pnpm install --frozen-lockfile
KNOWLEDGE_BASE_ID="$KNOWLEDGE_BASE_ID" \
MODEL_ID=us.anthropic.claude-sonnet-4-6 \
pnpm exec ampx sandbox
```

デプロイが終わり、`amplify_outputs.json`が生成されたら、別のターミナルで画面を起動します。表示されたURLを開き、Cognitoのアカウントを作成してログインしてください。

```bash
cd sample-code/03/amplify-ui
pnpm run dev
```

画面の検索モードでリランキングを使う場合はデプロイのときに`RERANK_MODEL_ARN`も指定します。回答の引用から元の資料を開けるようにする場合は第2章のバケット名と取り込み対象のプレフィックスを指定します。

```bash
KNOWLEDGE_BASE_ID="$KNOWLEDGE_BASE_ID" \
MODEL_ID=us.anthropic.claude-sonnet-4-6 \
RERANK_MODEL_ARN="$RERANK_MODEL_ARN" \
KNOWLEDGE_BASE_SOURCE_BUCKET_NAME=<第2章のバケット名> \
KNOWLEDGE_BASE_SOURCE_PREFIXES=sample-data \
pnpm exec ampx sandbox
```

第2章のバケット名は第2章の`infra/.env`の`BUCKET`に書かれています。

### 第5章で使うAPIのURL

このアプリのLambda Function URLは第5章の一括評価で`APP_API_URL`として使います。`amplify-ui`で次を実行すると、生成された`amplify_outputs.json`から取り出せます。

```bash
export APP_API_URL="$(node -p "require('./amplify_outputs.json').custom.ragFunctionUrl")"
echo "$APP_API_URL"
```

Function URLはIAM認証です。第5章のスクリプトは現在のAWS認証で署名してから呼び出します。

### 後片付け

sandboxで作るLambda、AppSync、DynamoDB、Cognitoは使った分だけの課金です。使い終わったら削除してください。第5章で一括評価をする場合は評価が終わってから削除します。

```bash
cd sample-code/03/amplify-ui
pnpm exec ampx sandbox delete
```

`spin-up-cheap-rag.sh`で作った場合はリポジトリのルートで`bash sample-code/03/infra/teardown-cheap-rag.sh`を実行します。

## 使い方

### 生成AIでPDFをマークダウンへ変換

`sample-pdfs/`にあるPDFをConverse APIで読み取り、表や図の内容をマークダウンへ変換します。出力済みのファイルは再実行時にスキップするため、中断後も同じコマンドで続きから実行できます。

```bash
uv run python llm_document_parse.py \
  --input-dir sample-pdfs \
  --output-dir output/parsed-markdown
```

| オプション | 用途 | デフォルト |
|---|---|---|
| `--input-dir` | 変換するPDFがあるディレクトリ | `sample-pdfs` |
| `--output-dir` | 変換したマークダウンの保存先 | `output/parsed-markdown` |
| `--model-id` | 読み取りに使うモデルIDまたは推論プロファイルID | Claude Haiku 4.5 |
| `--force` | 保存済みのファイルも変換し直す | オフ |
| `--workers` | 同時に変換するPDFの数。スロットリング時は待ち時間を延ばして再試行する | `1` |

利用するリージョンは環境変数`AWS_REGION`で指定します。未指定の場合は`us-east-1`を使います。詳しい考え方は、[第3章の補足「生成AIでPDFを解析して取り込む」](../../supplements/chapter03/生成AIでPDFを解析して取り込む.md)を参照してください。

### メタデータ生成方式の比較

プロンプトのみで生成する方式と、Structured Outputを使う方式を並べて確認できます。

```bash
uv run compare_metadata_modes.py \
  --document ../02/sample-data/policies/SLAポリシー2026年版.md \
  --schema-file data/metadata_schema.json \
  --model-id "$METADATA_MODEL_ID" \
  --output-dir ./output/metadata-modes
```

### メタデータサイドカーの自動生成

S3データソース用の`.metadata.json`ファイルを一括生成します。Structured Outputに対応したモデルではスキーマベースの生成を、非対応モデルではプロンプトベースのJSON抽出にフォールバックします。

```bash
uv run generate_metadata_sidecars.py \
  --input-dir ../02/sample-data/policies \
  --schema-file data/metadata_schema.json \
  --model-id "$METADATA_MODEL_ID" \
  --output-dir ./output/generated-metadata
```

### 検索設定の比較

ベースライン、ハイブリッド検索、メタデータフィルタリング、リランキング、クエリ分解を順に有効化し、同じ質問セットでの結果を比較します。

```bash
uv run compare_rag_configs.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --model-arn "$MODEL_ARN" \
  --rerank-model-arn "$RERANK_MODEL_ARN" \
  --questions data/questions.jsonl
```

`JUDGE_MODEL_ID`を入れておくと、回答の網羅性を簡易判定した列も埋まります。第2章をS3 Vectors版で作った場合は`--vector-store s3vectors`を追加してください。

### チャンク戦略の比較

本文のコード3-8の手順です。固定長／階層的／セマンティックの3つのKnowledge Baseを作り、同じ質問セットで回答と出典を比べます。AOSSコレクションを作るため、確認が終わったら削除してください。

```bash
cd sample-code/03
bash infra/setup-chunking-kbs.sh
set -a; source infra/.env.chunking; set +a
uv run compare_kb_variants.py \
  --variant fixed="$KB_ID_FIXED" \
  --variant hierarchical="$KB_ID_HIER" \
  --variant semantic="$KB_ID_SEM" \
  --model-arn "$RETRIEVE_MODEL_ARN" \
  --questions data/questions_chunking.jsonl \
  --region us-east-1 --output-dir output/kb-variants

# 比較が終わったらリソースを削除
bash infra/teardown-chunking-kbs.sh
```

`compare_kb_variants.py`の`--variant 名前=Knowledge Base ID`は比べたいKnowledge Baseの数だけ並べられます。パーサーの比較でも同じスクリプトを使います。

### PDFのパーサーを作成から比較まで試す

表・図・グラフを含むサンプルPDFを使って、デフォルトパーサーと基盤モデルパーサーの差を観察する手順です。AOSSコレクションと2つのKnowledge Baseを作るため、残存するとコストが発生します。確認が終わったら`teardown-parser-kbs.sh`で必ず削除してください。

```bash
# 1. 図表入りサンプルPDFを生成（sample-pdfs/ に出力）
uv run generate_sample_pdfs.py

# 2. 本文の比較では対象PDFだけを取り込み、2つのKBを作成
#    FM_PARSER_MODEL_ARN は省略すると Claude Haiku 4.5 を使用
#    指定できるのは Claude・Nova・Llama 4 のビジョン対応モデル。
#    GPT-5.6 Luna は Bedrock Mantle の Responses API では使えるが、FM パーサーには指定できない
PARSER_SAMPLE_FILE=sampleconnect-retention-chart.pdf bash infra/setup-parser-kbs.sh

# 3. 生成された .env を読み込み、質問を両KBで実行
set -a; source infra/.env; set +a
export RETRIEVE_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):inference-profile/us.anthropic.claude-sonnet-4-6"

uv run compare_kb_variants.py \
  --variant default_parser="$KB_ID_DEFAULT" \
  --variant fm_parser="$KB_ID_FM" \
  --questions data/questions_parser_retention.jsonl

# 4. 比較が終わったらリソースを削除
bash infra/teardown-parser-kbs.sh
```

質問ごとの回答と出典は`output/kb-variants/<timestamp>/questions/`に保存されます。すべてのPDFを試す場合は、`PARSER_SAMPLE_FILE`を指定せずに構築し、質問セットに`questions_parser.jsonl`を使ってください。

### FAQを見出し単位で分割する（補足）

本文「FAQや帳票ではカスタムチャンキングも選択肢になる」で触れた`NONE + customTransformationConfiguration`（POST_CHUNKING Lambda）を手元で動かしてみたい読者向けの補足ワークフローです。標準のチャンク戦略（FIXED/HIERARCHICAL/SEMANTIC）の確認には不要なので、この手順は飛ばして構いません。

題材は`sample-faq/sampleconnect-faq.md`（25件のFAQ）で、固定長チャンクとカスタムチャンク（Lambdaで見出し単位に分割）の2つのKnowledge Baseを構築し、同じ質問で retrieve 結果を比較します。

```bash
# 1. FAQのマークダウンをS3に配置し、Lambda関数・2つのKB・AOSSを構築
bash infra/setup-custom-chunking-kbs.sh

# 2. 生成された .env.custom を読み込み、FAQ質問セット（faq01〜faq05）で比較
set -a; source infra/.env.custom; set +a
export RETRIEVE_MODEL_ARN="arn:aws:bedrock:us-east-1:$(aws sts get-caller-identity --query Account --output text):inference-profile/us.anthropic.claude-sonnet-4-6"

uv run compare_kb_variants.py \
  --variant fixed=$KB_ID_FIXED \
  --variant custom=$KB_ID_CUSTOM \
  --questions data/questions_faq.jsonl

# 3. 比較が終わったらリソースを削除
bash infra/teardown-custom-chunking-kbs.sh
```

最終回答のキーワード一致率では、fixedとcustomが同点になるケースもあります。Retrieve APIのチャンクを見ると、fixedには複数FAQが混在し、customは1FAQずつ分かれています。回答の点数と取得内容の両方を見比べてください。custom側では、Lambdaが付けた`faq_id`が`retrievedReferences[].metadata`に返ります。この値から、取得したチャンクがどのFAQに当たるか確認できます。

### トピックフォルダ単位の網羅型RAG

機能ごとに資料をフォルダへまとめ、関連するフォルダ内の全ファイルを読みます。Haikuがファイルごとに関連情報を抽出し、Sonnetが回答をまとめます。契約上のルール、管理者向け手順、既知の不具合など、複数資料にまたがる質問で、クエリ分解との違いを比較するための例です。

事前準備として、トピックフォルダを第2章のKnowledge Baseに同期しておきます。`KB_DATA_BUCKET`と`TOPIC_DATA_BUCKET`にはどちらも第2章の`infra/.env`の`BUCKET`の値を入れます。

```bash
cd sample-code/03
export TOPIC_DATA_BUCKET=<第2章のバケット名>
KB_DATA_BUCKET="$TOPIC_DATA_BUCKET" \
KNOWLEDGE_BASE_ID="$KNOWLEDGE_BASE_ID" \
DATA_SOURCE_ID="$DATA_SOURCE_ID" \
bash infra/sync-topic-data.sh
```

質問分析・抽出・最終回答のモデルも入れておきます。本文と同じく、分析と最終回答はSonnet、ファイルごとの抽出はHaikuを使います。

```bash
export ANALYSIS_MODEL_ID=us.anthropic.claude-sonnet-4-6
export EXTRACTION_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export FINAL_MODEL_ID=us.anthropic.claude-sonnet-4-6
export RETRIEVE_MODEL_ARN="$MODEL_ARN"
```

同期完了後、単発実行は次のとおりです。

```bash
uv run orchestrate_topic_rag.py \
  --question "SampleConnectのAPIレート制限について、契約上のSLAルール、Enterpriseプランで上限緩和を申請する手順、現在報告されている既知の不具合をすべてまとめてください。" \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --data-bucket "$TOPIC_DATA_BUCKET" \
  --analysis-model-id "$ANALYSIS_MODEL_ID" \
  --extraction-model-id "$EXTRACTION_MODEL_ID" \
  --final-model-id "$FINAL_MODEL_ID"
```

### オーケストレーション3方式の比較

ハイブリッド検索のみ、マネージドのクエリ分解、トピックフォルダ網羅型RAGの3方式を同じ網羅型質問セットで比較します。`--strategies A,D`のように記号で絞り込めます。記号はA（ハイブリッド検索のみ）、B（クエリ分解）、D（トピックフォルダ網羅型RAG）の3つです。

```bash
uv run compare_topic_orchestration.py \
  --questions data/questions_topic_orch.jsonl \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --data-bucket "$TOPIC_DATA_BUCKET" \
  --model-arn "$RETRIEVE_MODEL_ARN" \
  --analysis-model-id "$ANALYSIS_MODEL_ID" \
  --extraction-model-id "$EXTRACTION_MODEL_ID" \
  --final-model-id "$FINAL_MODEL_ID" \
  --judge-model-id "$JUDGE_MODEL_ID"
```

## 本文の出力例を再現する

本文の出力例3-1〜3-4は`scripts/verify_book_examples.py`で再現できます。リポジトリのルートで、第2章の`KNOWLEDGE_BASE_ID`と`MODEL_ARN`を入れた状態で実行します。

```bash
uv run --project sample-code/03 scripts/verify_book_examples.py --only ch03-3-1 ch03-3-2
```

`--only`に渡すIDは出力例の番号に対応しています（`ch03-3-1`〜`ch03-3-4`）。回答は実行のたびに少し変わるため、期待するキーワードと引用元が含まれているかで判定します。

## 環境変数

| 変数 | 用途 |
|---|---|
| `AWS_REGION` | AWSリージョン |
| `KNOWLEDGE_BASE_ID` | 第2章のKnowledge Base ID |
| `DATA_SOURCE_ID` | 第2章のデータソースID（`sync-topic-data.sh`、`s3_event_pipeline.py`用） |
| `MODEL_ARN` | 第2章で入れた回答生成モデルのARN（`compare_rag_configs.py`用） |
| `MODEL_ID` | RAGポータルの回答生成モデルID |
| `RETRIEVE_MODEL_ARN` | 比較用Knowledge Baseでの回答生成モデルのARN。`setup-*-kbs.sh`が書き出す`.env`にも入る |
| `RERANK_MODEL_ARN` | リランキング用モデルのARN |
| `METADATA_MODEL_ID` | メタデータ生成用モデルID |
| `ANALYSIS_MODEL_ID` | 質問分析用モデルID（`orchestrate_topic_rag.py`用、Sonnet推奨） |
| `EXTRACTION_MODEL_ID` | ファイル単位の関連情報抽出モデルID（`orchestrate_topic_rag.py`用、Haiku推奨） |
| `FINAL_MODEL_ID` | 最終回答生成用モデルID（`orchestrate_topic_rag.py`用、Sonnet推奨） |
| `TOPIC_DATA_BUCKET` | トピックフォルダを保存しているS3バケット名 |
| `JUDGE_MODEL_ID` | 回答完全性を簡易判定する軽量モデル（`compare_*.py`用） |

## 比較を進める順番

1. 第2章でベースラインのRAGアプリケーションを構築する
2. `compare_metadata_modes.py`でメタデータ生成方式を比較する
3. `generate_metadata_sidecars.py`でメタデータサイドカーを生成し、S3にアップロードする
4. `s3_event_pipeline.py`で取り込みを自動化する
5. `compare_rag_configs.py`でベースライン設定の結果を記録する
6. ハイブリッド検索、メタデータフィルタリング、リランキング、クエリ分解を順に有効化して比較する
7. `compare_kb_variants.py`でパーサーやチャンク設定の異なるKnowledge Baseを比較する
8. `infra/sync-topic-data.sh`でトピックフォルダをKnowledge Baseに追加同期したうえで、`orchestrate_topic_rag.py`と`compare_topic_orchestration.py`でトピック網羅型RAGの効果を確認する

## 補足

- 各スクリプトは書籍の学習用サンプルです。本番向けのツールとしてはエラーハンドリング等の強化が必要です
- `compare_rag_configs.py`の評価は簡易的なものです。本格的なLLM-as-a-Judge評価は第5章で扱います
- メタデータ生成はBedrockのStructured Outputを優先的に使用します。これはプロンプトベースのJSON抽出より安定した出力が得られるためです
- `compare_kb_variants.py`は、パーサーやチャンク設定を変更すると別のKnowledge Baseを作り直す必要があるケースで役立ちます

## RAGポータルの処理をトレースする

[第3章のRAGアプリで回答の過程を可視化する](../../supplements/chapter03/第3章のRAGアプリで回答の過程を可視化する.md)では、質問分析、文書種別ごとの中間回答、最終統合をLangfuseまたはAgentCore Observabilityへ記録します。

`amplify-ui`で`KNOWLEDGE_BASE_ID`、`MODEL_ID`と送信先の認証・権限を設定し、次を実行します。現在のシェルのAWS認証を使います。

```bash
cd amplify-ui
pnpm install --frozen-lockfile
RAG_TRACE_DESTINATION=agentcore pnpm trace:rag --orchestration
```

送信先は`none`（既定）、`langfuse`、`agentcore`から選びます。`--orchestration`を外すと通常検索、`--question '質問文'`で質問を変更できます。検索は既定で意味検索です。ハイブリッド検索に対応したKnowledge Baseでは`--hybrid`を追加できます。回答とトレースIDは`rag-trace-result.json`へ保存します。

LambdaからLangfuseへ送る際は、APIキーをSecrets Managerへ保存し、`LANGFUSE_SECRET_ARN`をデプロイ時に指定します。AgentCoreの場合は、送信先リージョンのTransaction Searchを有効にしてからデプロイしてください。Amplify Hostingでは、同じ設定をビルド環境変数に指定して再デプロイします。

計測部分のローカルテストは`pnpm test`で実行できます。

## RAGポータルの回答モデルを切り替える

`amplify-ui`のハンドラーは、Sonnet 5、Opus 5・5.5、GPT-6 Sol／Lunaを指定すると、温度指定を省き、思考分を含む出力上限とKnowledge Basesのプロンプトを切り替えます。Sonnet 4.6では本編の設定を使います。

通常検索は`MODEL_ID`で検索後の回答を生成します。資料横断モードでは、`MODEL_ID`を質問分析・最終統合に、`ROUTE_MODEL_ARN`を資料種別ごとの中間回答に使います。`ROUTE_MODEL_ARN`が未指定・空文字なら`MODEL_ID`を使います。

GPT-6 Sol／LunaはKnowledge Basesの回答生成で温度指定のエラーになるため、資料横断モードで中間回答にSonnet 4.6を組み合わせます。6モデルの実行結果と設定例は[本編のコードで最新モデルを使う](../../supplements/chapter01/本編のコードで最新モデルを使う.md)に載せています。

[第4章のエージェント構築へ](../04/README.md)・[第5章の評価へ](../05/README.md)・[作業後のリソース削除](../../ハンズオン後のリソース削除手順.md)
