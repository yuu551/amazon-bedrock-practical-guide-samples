# 第2章 サンプルコード

Amazon Bedrock Knowledge Basesで最初のRAGを構築するためのサンプルデータ、CLIスクリプト、Lambdaハンドラー、検証用UIです。

本編ではコンソールでKnowledge Baseを作ります。CLIで同じ構成を用意したい方は[インフラ構築手順](infra/README.md)を使ってください。資料の取り込みを終えてから、下のAPI呼び出しと画面の起動へ進みます。

> [!WARNING]
> 本編のOpenSearch Serverless版は検索していない間もOCU（処理容量）の料金がかかります。費用を抑えたい場合はS3 Vectors版（`infra/setup-s3vectors.sh`）も選べます。ただし、S3 Vectors版では第3章のハイブリッド検索は試せません。使い終えたら、作った方法に合わせて削除してください（[インフラ構築手順](infra/README.md)・[リソース削除手順](../../ハンズオン後のリソース削除手順.md)）。

[セットアップ](#セットアップ)・[APIの使い方](#使い方)・[Lambdaの入出力](#lambda-ハンドラー)

## ファイル構成

### サンプルデータ（`sample-data/`）

S3データソースにアップロードするSampleConnect Platformの製品ドキュメントとメタデータです。Knowledge Basesの取り込み対象になります。

| ディレクトリ | 内容 | document_type | 件数 |
|---|---|---|---|
| `faq/` | SampleConnect PlatformサポートFAQ（SAML、APIキー、APIレート制限、監査ログ、SLA、課金、メンテナンス、障害情報） | `faq` | 20件 |
| `manuals/` | 障害一次対応、インシデント報告、エスカレーション | `manual` | 3件 |
| `policies/` | SLAポリシー（2025・2026年版）、監査ログ保存ポリシー（2025・2026年版）、課金請求ポリシー2026年版 | `policy` | 5件 |
| `product/` | Enterpriseプラン機能ガイド、SAML認証設定、プラン比較、リリースノート、API認証・監査ログ管理 | `manual` / `release_note` | 5件 |

各`.md`ファイルに対応する`.md.metadata.json`がペアで配置されています。メタデータの属性は第3章のメタデータスキーマと互換です。

```json
{
  "metadataAttributes": {
    "document_type": "faq",
    "department": "Support",
    "audience": "support",
    "product": "sampleconnect-platform",
    "effective_date": 20260401
  }
}
```

### 変換スクリプト（`scripts/`）

`faq/`配下の20件のマークダウンは、元データのExcelファイル（`sample-data/faq/source/faq.xlsx`）から変換スクリプト（`scripts/xlsx_to_faq_markdown.py`）で生成しています。Excelを編集してから次を実行すると、`.md`を一括で再生成できます。

```bash
uv run python scripts/xlsx_to_faq_markdown.py
```

メタデータファイル（`.md.metadata.json`）の自動生成は第3章で扱うため、本スクリプトは`.md`のみ出力します。

### CLIスクリプト

| ファイル | 内容 |
|---|---|
| `retrieve_and_generate.py` | RetrieveAndGenerate APIで質問に回答する最小構成 |
| `retrieve_and_converse.py` | Retrieve + Converse APIでスコープ制御付きの回答を生成する |
| `retrieve_and_converse_luna.py` | Retrieve + Converse APIでGPT-5.6 Lunaを利用する最小構成 |
| `compare_low_cost_models.py` | 同一の検索チャンクでHaiku 4.5とGPT-5.6 Lunaを比較する検証用スクリプト |
| `nextgen_direct_retrieve.py` | 同期済みNextGenインデックスをOpenSearch APIで直接検索する診断用スクリプト |
| `scripts/run-paraphrase-eval.sh` | 言い換え質問ごとの回答と出典を確認する（OpenSearch Serverless版・S3 Vectors版のどちらでも動く） |
| `scripts/run-paraphrase-retrieval-eval.sh` | 同じ言い換え質問のRetrieve結果とスコアだけを比較する（どちらの版でも動く） |

### インフラ構築スクリプト（`infra/`）

コンソールの`Quick create`を使わず、CLIだけでKnowledge Base環境を再現するスクリプトです。

| ファイル | 内容 |
|---|---|
| `infra/setup.sh` / `infra/teardown.sh` | OpenSearch Serverless版（本編構成） |
| `infra/setup-s3vectors.sh` / `infra/teardown-s3vectors.sh` | S3 Vectors版（軽量・低コスト代替） |

構成の選び方と作成・削除の手順は、[インフラ構築手順](infra/README.md)を参照してください。

### Lambdaハンドラー（`lambda/`）

`lambda/rag_handler.py`は、Lambda Function URLまたはAPI Gatewayから質問を受け取り、RAGの回答を返すハンドラーです。

### 検証用UI（`amplify-ui/`）

- Vite + React + TypeScript のシンプルなSPA
- Amplify Hostingでの公開を想定
- 第4章でAIエージェント対応に拡張する前提の構成

## セットアップ

### Python環境

```bash
cd sample-code/02
uv sync
```

### 使う値を環境変数に入れる

以降のコマンドは次の環境変数を使います。CLIのsetupで作った場合は完了時に表示される`export`の行をそのまま貼り付けます。

```bash
export KNOWLEDGE_BASE_ID=<Knowledge Base ID>
export DATA_SOURCE_ID=<データソースID>
export MODEL_ARN="arn:aws:bedrock:us-east-1:<AWSアカウントID>:inference-profile/us.anthropic.claude-sonnet-4-6"
export MODEL_ID=us.anthropic.claude-sonnet-4-6
```

コンソールで作った場合、Knowledge Base IDはKnowledge Baseの詳細画面の上部、データソースIDは「データソース」の一覧に表示されます。`MODEL_ARN`は`RetrieveAndGenerate`で、`MODEL_ID`はConverseで使うモデルの指定です。第3章以降でも同じ名前を使います。

### サンプルデータのS3アップロード（コンソールで作る場合）

CLIのsetupはアップロードから取り込みまでを済ませます。コンソールで作る場合だけ、データソースに指定するS3バケットへサンプルデータをアップロードします。バケットは本編の手順に沿って先に作っておきます。

```bash
aws s3 sync sample-data/ s3://<データソースのバケット>/sample-data/
```

アップロード後、Bedrockコンソールでデータソースの同期を実行してください。

### フロントエンド

第2章の画面はCognitoで認証し、`amplify_outputs.json`にあるLambda Function URLを呼び出します。初回は、作成済みのKnowledge Base IDとモデルIDを指定してバックエンドをデプロイします。

```bash
cd sample-code/02/amplify-ui
pnpm install --frozen-lockfile
KNOWLEDGE_BASE_ID=YOUR_KNOWLEDGE_BASE_ID \
MODEL_ID=us.anthropic.claude-sonnet-4-6 \
pnpm exec ampx sandbox
```

デプロイが終わり、`amplify_outputs.json`が生成されたら、別のターミナルで画面を起動します。

```bash
cd sample-code/02/amplify-ui
pnpm run dev
```

表示されたURLを開き、Cognitoのアカウントを作成してログインしてください。接続先は生成された設定ファイルから読み込みます。

## 使い方

### RetrieveAndGenerate（基本）

```bash
uv run retrieve_and_generate.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --model-arn "$MODEL_ARN" \
  --question "SAML SSOでログインできない場合の確認項目を教えてください"
```

会話を継続する場合は`--session-id`を指定します。

```bash
uv run retrieve_and_generate.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --model-arn "$MODEL_ARN" \
  --session-id "$SESSION_ID" \
  --question "IdP証明書の有効期限はどう確認しますか"
```

### Retrieve + Converse（スコープ制御付き）

```bash
uv run retrieve_and_converse.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --model-id "$MODEL_ID" \
  --question "AWSの最新ニュースを教えてください"
```

Lunaを使う場合はモデルIDと推論設定を固定した専用のサンプルを実行します。モデル利用契約に加えて、推論プロファイルとアカウントの既定プロジェクト（`default`）に対する
`bedrock:InvokeModel`権限が必要です。

```bash
uv run retrieve_and_converse_luna.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --question "SAML SSOでログインできない場合の確認項目を教えてください"
```

このサンプルは処理先を米国内に限定するGeo推論ID`us.openai.gpt-5.6-luna`を使用します。Lunaは`temperature`を受け付けないため、Converse APIには`maxTokens`だけを渡します。

Billing部門のFAQだけに絞る場合はメタデータフィルタを指定します。

```bash
uv run retrieve_and_converse.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --model-id "$MODEL_ID" \
  --question "サービスクレジットの申請期限はいつですか" \
  --filter '{"equals": {"key": "department", "value": "Billing"}}'
```

### Haiku 4.5 / GPT-5.6 Luna比較

同じ検索結果をHaikuとLunaへ渡し、回答・出典・処理時間を比較します。結果はJSONとマークダウンへ保存します。

```bash
uv run compare_low_cost_models.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --data-source-id "$DATA_SOURCE_ID"
```

実行前に、対象データソースの最新取り込みが`COMPLETE`で、走査した文書が33件、
失敗が0件であることを検査します。内容が重複する変換元の`faq.xlsx`は検索対象に含めません。

Lunaで回答を生成する場合は、上のRetrieve + Converseのサンプルを使ってください。

## Lambda ハンドラー

### リクエスト

```json
{
  "question": "SAML SSOでログインできない場合の確認項目を教えてください",
  "filter": {"equals": {"key": "department", "value": "Support"}}
}
```

`filter`は省略可能です。第3章でメタデータフィルタリングを導入する際に使用します。

### レスポンス

```json
{
  "answer": "まず、IdP側で対象ユーザーがSampleConnect Platform用のSAMLアプリに割り当てられているかを確認します。...",
  "citations": [
    {
      "uri": "s3://bucket/sample-data/faq/SAML_SSOでログインできない場合の確認項目.md",
      "snippet": "まず、IdP側で対象ユーザーがSampleConnect Platform用のSAMLアプリに..."
    }
  ]
}
```

### 環境変数

| 変数 | 用途 |
|---|---|
| `KNOWLEDGE_BASE_ID` | Knowledge Base ID（必須） |
| `MODEL_ID` | 基盤モデルのモデルIDまたはARN（必須） |
| `AWS_REGION` | AWSリージョン（デフォルト: `us-east-1`） |
| `SYSTEM_PROMPT` | システムプロンプト（設定するとRetrieve + Converseパターンを使用） |

### LunaをLambdaから呼び出す

ローカルで確認した`Retrieve`とConverse APIの処理は、`lambda/rag_handler.py`を
Lambdaへデプロイしてアプリケーションから利用できます。Python 3.12ランタイムを選び、
ハンドラー名を`rag_handler.lambda_handler`にします。Lambdaには次の環境変数を設定します。

```text
KNOWLEDGE_BASE_ID=<Knowledge Base ID>
MODEL_ID=us.openai.gpt-5.6-luna
AWS_REGION=us-east-1
SYSTEM_PROMPT=検索結果に根拠がある場合だけ回答し、推測で補完しないでください。
```

`SYSTEM_PROMPT`を設定すると、ハンドラーは`RetrieveAndGenerate`ではなく、
`Retrieve`で検索したチャンクをConverse APIへ渡します。Lunaを使う場合は、
この環境変数を空にしないでください。

Lambdaの実行ロールには、Knowledge Baseを検索する`bedrock:Retrieve`と、
Lunaを呼び出す`bedrock:InvokeModel`を許可します。`bedrock:InvokeModel`は、
推論プロファイルとアカウントの既定プロジェクト（`default`）を対象に含めます。

Lambda Function URLまたはAPI Gatewayから、次のリクエストを送信します。

```http
POST /
Content-Type: application/json

{"question":"APIキーの同時有効本数をプラン別に教えてください。"}
```

ハンドラーは回答と検索した文書のURIをJSONで返します。インターネットへ公開する場合は、
Function URLのIAM認証またはAPI Gatewayの認証を有効にし、`CORS_HEADERS`の
`Access-Control-Allow-Origin`を利用する画面のオリジンに限定してください。

## 次に進む

第3章では検索精度を改善し、第4章ではツールを使うエージェントへ拡張します。第5章では、この章で作ったAPIの回答を評価します。

[第3章の検索精度改善へ](../03/README.md)・[共通の準備と費用](../README.md)・[作業後のリソース削除](../../ハンズオン後のリソース削除手順.md)
