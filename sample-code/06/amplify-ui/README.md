# 第6章のサポートポータル：Amplifyで運用の構成を試す

第4章の問い合わせ対応ポータルに、Guardrailとアプリケーション推論プロファイルを加えたサンプルです。Knowledge Baseとベクトルストアも、`SupportAiPlatform`コンストラクトで構築します。Amplify sandboxで次のリソースを構築し、サンプル資料の取り込みを開始します。取り込みの完了を確認してから質問を送ります。

| カテゴリ | リソース |
|---|---|
| 認証 | Cognito User Pool / Identity Pool |
| 推論 | アプリケーション推論プロファイル / Guardrail（L1 + L2） |
| RAG | Knowledge Base + DataSource + ベクトルストア（S3 Vectors または OpenSearch Serverless） + データソース S3 バケット |
| データ投入 | `BucketDeployment` で `sample-data/` を同期 + `AwsCustomResource` で初回 Ingestion Job 自動発火 |
| エージェント | AgentCore Runtime（Strands エージェント、CodeBuild ARM64 ビルド）、Memory、Gateway、Lambda Target |

第4章の `amplify-ui/` との差分は本ファイル末尾の「第4章サンプルとの差分」を参照してください。

## ファイル構成

| 場所 | 内容 |
|---|---|
| `amplify/auth/` | Cognitoの設定 |
| `amplify/agent/` | AgentCore RuntimeとStrandsエージェント |
| `amplify/constructs/support-ai-platform.ts` | Guardrail、推論プロファイル、Knowledge Baseなどの定義 |
| `amplify/backend.ts` | 各リソースを組み合わせるバックエンド定義 |
| `sample-data/` | Knowledge Baseへ取り込むサンプル資料 |
| `src/` | ViteとReactのフロントエンド |
| `package.json` | 依存関係と起動コマンド |
| `.env.sandbox.example` | 環境変数の記入例。コピーして`.env.sandbox`を作成 |

## 前提

| 必須 | 用途 |
|---|---|
| Node.js 24（`mise.toml` で指定済み） | `ampx` / `tsc` 実行環境 |
| pnpm | 依存関係インストール |
| AWS CLI v2 / 認証 | `us-east-1` で `bedrock-agentcore`・`bedrock-agent`・`s3vectors`・`aoss` を実行できる権限 |
| Bedrockモデルへのアクセス | 使用するモデルの呼び出し権限と初回利用の手続きを[共通の準備](../../README.md#awsアカウント)で確認 |

## セットアップ

```bash
cd sample-code/06/amplify-ui
pnpm install
cp .env.sandbox.example .env.sandbox
# `.env.sandbox` を環境に合わせて書き換える
# - 必須: MODEL_SOURCE_ARN（推論プロファイルのコピー元 ARN）
# - 任意: ATLASSIAN_BASE_URL ほか Atlassian 連携、KB_VECTOR_STORE 切替、KB_ENABLE_RERANKING
```

## デプロイ

```bash
# .env.sandbox を読み込んで起動
set -a; source .env.sandbox; set +a
pnpm sandbox            # ampx sandbox --identifier ch06-private を起動
```

`Deployment completed`が出たら、`amplify_outputs.json`で作成されたリソースを確認します。資料の取り込みは非同期のため、この時点では検索できない場合があります。下の手順で取り込みジョブが`COMPLETE`になったことを確認してください。

`custom.guardrailVersion`には、作成したGuardrailの番号付きバージョンが出力されます。モデル呼び出しでは、このバージョンを指定します。

### ベクトルストアの切替

```bash
# デフォルト（S3 Vectors、コスト低・サンドボックス向き）
pnpm sandbox

# OpenSearch Serverless に切り替える場合（一度サンドボックスを delete してから）
pnpm sandbox:delete

# サンドボックス検証用に公開設定で起動
KB_VECTOR_STORE=OPENSEARCH_SERVERLESS OSS_ALLOW_PUBLIC=true pnpm sandbox

# VPCエンドポイントを指定する例（後述のBedrock接続設定も必要）
KB_VECTOR_STORE=OPENSEARCH_SERVERLESS \
  OSS_SOURCE_VPCES=vpce-xxxxxxxxxxxxxxxxx \
  pnpm sandbox
```

`OSS_ALLOW_PUBLIC=true`も`OSS_SOURCE_VPCES`も設定しない場合は、許可する接続元がないためCDKが警告を出します。

プライベートなコレクションへKnowledge Basesを接続するには、VPCエンドポイントの指定に加えて、ネットワークポリシーの`SourceServices`で`bedrock.amazonaws.com`を許可します。現在のコンストラクトはこの項目を設定しないため、閉域構成を試す場合は[AWS公式の設定例](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-create-security.html)に従って`support-ai-platform.ts`を変更してください。

S3 Vectorsを選んだ場合も、Knowledge Basesの`rerankingConfiguration`は利用できます。サービスロールへ権限を付けるには`KB_ENABLE_RERANKING=true`を指定します。`bedrock:Rerank`はリソースレベル権限に未対応なので同アクションだけ`Resource: "*"`となり、`bedrock:InvokeModel`はCohere Rerank 3.5のARNに限定されます。既定では権限を追加しません。

## 動作確認

`amplify_outputs.json` の `custom` セクションに、リソースの ID/ARN が一通り出ています。

```bash
cat amplify_outputs.json
# 例
# "knowledgeBaseId": "KMDDYAFSKJ"
# "agentCoreRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:...:runtime/support_agent_ch06-..."
```

### 取り込みを確認してKnowledge Baseを検索する

```bash
KB_ID=$(jq -r .custom.knowledgeBaseId amplify_outputs.json)
DS_ID=$(jq -r .custom.dataSourceId amplify_outputs.json)
aws bedrock-agent list-ingestion-jobs \
  --region us-east-1 \
  --knowledge-base-id "$KB_ID" \
  --data-source-id "$DS_ID"
```

最新のジョブが`COMPLETE`になり、失敗件数が0件であることを確認してから検索します。

```bash
aws bedrock-agent-runtime retrieve \
  --region us-east-1 \
  --knowledge-base-id "$KB_ID" \
  --retrieval-query 'text=SAML SSOでログインできない場合の確認項目を教えてください'
```

### Atlassian 連携を使う場合

`support-agent-ch06/atlassian` Secret に email と API トークンを投入してください。

```bash
aws secretsmanager put-secret-value \
  --region us-east-1 \
  --secret-id support-agent-ch06/atlassian \
  --secret-string '{"email":"<your-email>","apiToken":"<your-token>"}'
```

`.env.sandbox`の`ATLASSIAN_BASE_URL`、`CONFLUENCE_SPACE_KEY`、`JIRA_PROJECT_KEY`も、ご自身のサイトの値に合わせてください。

### フロントエンドを動かす

```bash
pnpm dev    # Vite dev server（http://localhost:5173）
```

Cognito へのサインアップ後、AgentCore Runtime 経由でエージェントが応答します。

## クリーンアップ

```bash
pnpm sandbox:delete
```

S3 Vectors の VectorBucket / OpenSearch Serverless の Collection も含めて削除されます。AOSS は時間単位の課金が発生するため、検証が終わったら必ず削除してください。

## 第4章サンプルとの差分

| 追加・変更 | 内容 |
|---|---|
| `amplify/constructs/support-ai-platform.ts` | 本章の `SupportAiPlatform` Construct 一式 |
| `amplify/backend.ts` | 上記 Construct を `backend.createStack("SupportAiPlatform")` に組み込み、`createAgentCoreRuntime` へ `guardrailId` / `inferenceProfileArn` / `knowledgeBaseId` を環境変数で渡す |
| `amplify/agent/resource.ts` | Runtime の env vars を `KNOWLEDGE_BASE_ID` / `MODEL_ID` / `GATEWAY_URL` / `GUARDRAIL_ID` / `GUARDRAIL_VERSION` / `INFERENCE_PROFILE_ARN` で受け取る形に拡張 |
| `amplify/agent/Dockerfile` | Docker Hub のレート制限を避けるため `public.ecr.aws/docker/library/python:3.13-slim` ベースに切替 |
| `sample-data/` | Knowledge Baseの取り込み対象のマークダウンサンプル（第2章 `sample-code/02/sample-data/` と同等） |
| `package.json` | `name` を `chapter-06-support-portal-prod`、`sandbox` script に `--identifier ch06-private`、`@aws-cdk/aws-bedrock-alpha` を追加 |
| リソース命名 | Atlassian Secret = `support-agent-ch06/atlassian`、Gateway = `support-gateway-ch06`、Memory = `SampleConnectSupportMemoryCh06`、Runtime = `support_agent_ch06`（第4章と並走可能） |

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `Set MODEL_SOURCE_ARN to your foundation/inference-profile ARN.` で停止 | `MODEL_SOURCE_ARN` 環境変数が未設定 | `.env.sandbox` に `MODEL_SOURCE_ARN=arn:aws:bedrock:us-east-1:<your-account-id>:inference-profile/...` を追加 |
| Knowledge Baseのベクトルストア種別を変えたい | `metadataConfiguration` 等は CFn で置換扱い | `pnpm sandbox:delete` してから `KB_VECTOR_STORE=...` を指定して再デプロイ |
| Ingestion Job が `Filterable metadata must have at most 2048 bytes` で失敗 | S3 Vector Indexの非フィルタ対象キーが不足 | `AMAZON_BEDROCK_TEXT` と `AMAZON_BEDROCK_METADATA` を `nonFilterableMetadataKeys` に登録する（本サンプルは対応済み） |
| OSS デプロイで `Access denied for operation 'CreateIndex'.` | CDK CFn 実行ロールが OSS データアクセスポリシーに含まれていない | デフォルト qualifier `hnb659fds` 用 ARN を Construct が自動付与済み。カスタム qualifier の場合は `ossDataAccessPrincipals` プロパティで上書き |
| OpenSearchへの接続が拒否される | ネットワークポリシーの接続元設定が不足 | 検証時の公開アクセス設定、または閉域構成の`SourceVPCEs`と`SourceServices`を確認する |
| Retrieve でヒットなし | Ingestion Job の完了前 | `aws bedrock-agent list-ingestion-jobs --knowledge-base-id ... --data-source-id ... ` で `COMPLETE` を待つ |
| `AlreadyExists` で GuardrailやKnowledge Baseの作成に失敗 | `cdk/` のスタンドアロンCDKスタック（SupportAiDev等）が同じ`support-dev-*`名でリソースを作成済み | 作成済みの環境と保存データを確認し、使い終えた方を削除してから再デプロイする。必要な環境を残す場合は環境名を分ける |

[第6章の構成一覧に戻る](../README.md)・[作業後のリソース削除](../../../ハンズオン後のリソース削除手順.md)

## 既存のS3 Vectors環境を更新する場合

以前のコードでは、NestedStackの未解決Tokenを加工してバケット名に使っていたため、合成時のToken番号が変わるとバケットの置換が発生する場合がありました。現在のコードは、Constructパスから安定した名前を生成します。

既存環境のデータを保持して更新する場合は、S3 Vectorsコンソールで現在のバケット名を確認し、`VECTOR_BUCKET_NAME`にその名前を指定してください。Amplify Hostingではアプリの環境変数へ、sandboxでは起動時の環境変数へ設定します。新規環境では省略できます。既存環境の名前を確認せずに、例示した名前へ変更しないでください。
