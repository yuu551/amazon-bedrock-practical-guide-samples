# 第6章 サンプルコード

本番環境で安定稼働させるための設計パターンを実装したサンプルです。CDKでインフラを構築し、ツールの利用制御やログ分析を加えます。Knowledge Base更新後の自動評価と、AWS Agent Registryでの登録・承認も扱います。

画面から一通り試す場合は[Amplify版](amplify-ui/README.md)、インフラの定義を中心に読む場合は[CDK版](cdk/README.md)から始めてください。同じ環境名で両方をデプロイするとリソース名が衝突するため、使う構成を先に選びます。ログ分析やAgent Registryは個別にも試せます。

[構成とファイル](#ファイル構成)・[セットアップ](#セットアップ)・[実行例](#使い方)・[環境変数](#環境変数一覧)

コード6-12の匿名化設定には、入力・出力のアクションの明示が必要です。配布コードは修正済みです。

## ファイル構成

| ディレクトリ | 内容 |
|---|---|
| [agent-registry](agent-registry/README.md) | エージェント・MCP・スキルの登録と承認 |
| [amplify-ui](amplify-ui/README.md) | 第4章の画面にGuardrailとKnowledge Baseなどを組み込む |
| `gateway-lambda/` | Confluence検索とJira起票のLambda |
| [gateway-policy](gateway-policy/README.md) | ロールと実行順に基づくツール利用の制御 |
| [cdk](cdk/README.md) | インフラを個別に構築するCDKプロジェクト |
| [interceptor](interceptor/README.md) | ツール呼び出しの検査と一覧の絞り込み |
| [log-analyzer](log-analyzer/README.md) | モデル呼び出しログの集計 |
| `kb-update-agent/` | 資料更新後の評価、承認、ロールバック |

### エージェントとスキルの組織管理（`agent-registry/`）

一時的なレジストリへエージェント、MCPサーバー、スキルを登録し、承認の前後で検索結果が変わることを確認します。承認済み1.0.0を公開したまま1.1.0を審査し、切り替え後の`SKILL.md`をStrands Agentへ適用します。詳細は[Agent Registryの手順](agent-registry/README.md)を参照してください。

### Amplify Gen 2 + 本番化 Construct（`amplify-ui/`）

第4章の画面に、Guardrail、推論プロファイル、Knowledge Baseとデータソースを組み込む本章のメインサンプルです。`SupportAiPlatform`コンストラクトがインフラを構築します。[Amplify版の手順](amplify-ui/README.md)を参照してください。

### Gateway Policy（`gateway-policy/`）

JWT認証済みの利用者にはConfluence検索を許可し、`role`が`senior-operator`の利用者にはJira起票も許可するCedarポリシーです。Confluence検索後だけJira起票を許可するDogwoodポリシーも含みます。メインサンプルはIAM認証のため、試す場合は[Gateway Policyの手順](gateway-policy/README.md)に従ってJWT認証へ変更してください。

### CDK（`cdk/`）

IaCによるインフラのコード化と環境分離のサンプルです。

| ファイル | 内容 |
|---|---|
| `bin/app.ts` | CDKアプリケーションのエントリポイント。dev/prod環境を定義 |
| `lib/support-ai-stack.ts` | メインスタック。Platform・Monitoring・Interceptor・KbUpdateAgentの4コンストラクトを統合 |
| `lib/support-ai-platform.ts` | Guardrail、推論プロファイル、Knowledge Baseとベクトルストアの定義 |
| `lib/interceptor.ts` | REQUEST・RESPONSEインターセプターLambdaのコンストラクト |
| `lib/monitoring.ts` | CloudWatchアラーム（レイテンシ・スロットリング・サーバーエラー）の定義 |
| `lib/kb-update-agent.ts` | ベースラインバケット・Runtime（環境変数+Session Storage、Docker は `fromAsset` で自動ビルド）・Trigger Lambda・EventBridgeルール・IAM拡張をまとめたConstruct |
| `lib/kb-update-agent-stack.ts` | 既存のKnowledge Baseに更新後の自動評価を追加する単独Stack（`KbUpdateAgent` Constructを薄くラップ） |

### インターセプター（`interceptor/`）

AgentCore GatewayのREQUESTステージで実行されるLambda関数です。ツール呼び出しの引数を検査し、制限対象の顧客IDや金額上限超過をブロックします。

| ファイル | 内容 |
|---|---|
| `request_handler.py` | ツール呼び出し前のロール認可と引数バリデーション |
| `response_handler.py` | `tools/list`の応答からロールで利用できないツールを除外 |

### ログ分析（`log-analyzer/`）

CloudWatch Logs Insightsを使ったBedrockモデル呼び出しログの集計ツールです。

| ファイル | 内容 |
|---|---|
| `analyze_logs.py` | モデル別・推論プロファイル別・時間帯別の集計、コスト推定、レイテンシ分析 |
| `queries.py` | CloudWatch Logs Insightsのクエリテンプレート |

### Knowledge Base更新エージェント（`kb-update-agent/`）

Knowledge Baseの更新時に自動で品質評価を実行し、承認・ロールバック・レビュー要求をコードで判断するパイプラインです。

| ファイル | 内容 |
|---|---|
| `handler.py` | AgentCore Runtime のエントリポイント。更新可否の判定に従って処理し、`payload.action`で `process` / `approve` / `rollback` を切り替える |
| `quality_gate.py` | 評価平均と個別スコアを比較し、承認・ロールバック・レビュー依頼を決める純粋関数 |
| `test_quality_gate.py` | 閾値、0.1低下、新規0点、評価エラー、同期の部分失敗を確認する単体テスト |
| `test_handler.py` | 0.1低下時に保存・ロールバックを実行せず、保留状態だけを残すことを確認するテスト |
| `agent.py` | 副作用ツールを持たず、原因仮説と通知文の下書きだけを生成するStrands Agentsの定義 |
| `tools.py` | 同期、評価、ベースライン読み込みと、コード側から呼ぶ保存・ロールバック・通知関数を提供 |
| `decide.py` | レビュー依頼判定が出た更新を、運用担当が同じセッションIDで Resume して承認・ロールバックするためのCLI |
| `Dockerfile` | AgentCore Runtime にデプロイするコンテナイメージのビルド定義 |
| `eval_cases.json` | 評価用のテストケース（5件、SAML SSO・APIキー失効・SLAクレジット・監査ログエクスポート・障害初回案内に関する想定質問） |

## セットアップ

### CDK

```bash
cd sample-code/06/cdk
pnpm install
```

デプロイ時に、推論プロファイルのコピー元ARNを環境変数`MODEL_SOURCE_ARN`またはCDK contextの`modelSourceArn`で指定してください。

### ログ分析

```bash
cd sample-code/06/log-analyzer
uv sync
```

前提条件:
- Bedrockのモデル呼び出しログをCloudWatch Logsに出力する設定が有効であること
- `logs:StartQuery`、`logs:GetQueryResults`のIAM権限があること

### Knowledge Base更新エージェント

```bash
cd sample-code/06/kb-update-agent
uv sync
```

### エージェントとスキルの組織管理

boto3 1.43.83とAWS認証情報を使用します。`demo --yes`はレジストリとレコード3件を作って登録・承認・検索・バージョンの切り替えを通しで試し、最後に削除します。

```bash
cd sample-code/06/agent-registry
uv sync --frozen
uv run python register_and_search_registry.py demo --yes --region us-east-1
```

途中で止めてコンソールを確認しながら進める場合の手順は[agent-registry/README.md](agent-registry/README.md)を参照してください。

## 使い方

### CDKデプロイ

```bash
cd sample-code/06/cdk

# 利用する基盤モデルまたはシステム定義推論プロファイルのARNを指定
# 次の値は、利用するモデルの実際のARNに置き換える
export MODEL_SOURCE_ARN="arn:aws:bedrock:us-east-1::foundation-model/your-model-id"

# SupportAiDev / SupportAiProd が表示されることを確認
pnpm exec cdk list

# 開発環境
pnpm exec cdk deploy SupportAiDev

# 本番環境
pnpm exec cdk deploy SupportAiProd
```

スタック出力の`RequestInterceptorArn`と`ResponseInterceptorArn`を、Gatewayの対応するインターセプターに設定します。[インターセプターの案内](interceptor/README.md)で役割を確認してください。

### ログ分析

```bash
cd sample-code/06/log-analyzer

# モデル別集計
uv run analyze_logs.py \
  --start-time 2026-04-01T00:00:00 \
  --end-time 2026-04-11T00:00:00 \
  --group-by model

# 推論プロファイル別集計（アプリケーション単位のコスト把握）
uv run analyze_logs.py \
  --start-time 2026-04-01T00:00:00 \
  --end-time 2026-04-11T00:00:00 \
  --group-by profile

# 時間帯別集計
uv run analyze_logs.py \
  --start-time 2026-04-01T00:00:00 \
  --end-time 2026-04-11T00:00:00 \
  --group-by hour

# エラー集計とKnowledge Bases取り込み状況も含める
uv run analyze_logs.py \
  --start-time 2026-04-01T00:00:00 \
  --end-time 2026-04-11T00:00:00 \
  --group-by model \
  --include-errors \
  --include-kb
```

`queries.py`のクエリはCloudWatchコンソールに直接貼り付けても使えます。

### Knowledge Base更新エージェント

CDKで更新の検知、評価、通知に必要なリソースをまとめて作成します。コンテナも自動でビルドするため、`agentcore deploy`は不要です。

```bash
cd sample-code/06/cdk

export KB_UPDATE_KB_ID=<Knowledge Base ID>
export KB_UPDATE_DS_ID=<データソースID>
export KB_UPDATE_DATA_BUCKET=<KBデータソースのS3バケット>
export KB_UPDATE_SNS_TOPIC_ARN=<通知先SNSトピックARN>
# カスタムエンドポイントへ固定する評価済みRuntimeバージョン（省略時は1）
export KB_UPDATE_RUNTIME_VERSION=<Runtimeバージョン>

# 上書き前の本文・メタデータを復元するため必須
aws s3api put-bucket-versioning \
  --bucket "$KB_UPDATE_DATA_BUCKET" \
  --versioning-configuration Status=Enabled

pnpm exec cdk deploy KbUpdateAgentDev

# コード6-7の承認パターンを実在ファイルで再現
# メタデータを先に配置し、文書本体の配置で評価を起動する
aws s3 cp \
  ../kb-update-agent/test-docs/監査ログFAQ補足_2026-04.md.metadata.json \
  "s3://$KB_UPDATE_DATA_BUCKET/sample-data/policies/監査ログFAQ補足_2026-04.md.metadata.json"
aws s3 cp \
  ../kb-update-agent/test-docs/監査ログFAQ補足_2026-04.md \
  "s3://$KB_UPDATE_DATA_BUCKET/sample-data/policies/監査ログFAQ補足_2026-04.md"
```

EventBridgeルールは`KB_UPDATE_TRIGGER_PREFIX`（既定`sample-data/policies/`）以下のオブジェクト作成イベントを拾います。Trigger Lambdaはメタデータファイル単独のイベントを無視し、文書本体のイベントで`runtimeSessionId`を採番してRuntimeを起動します。このときメタデータのVersion IDも取得するため、必ずメタデータ、文書本体の順に配置します。メタデータだけを更新する場合も、最後に文書本体を再配置してください。S3通知のEventBridge配信は対象バケットで有効化されている必要があります。

更新は1件ずつ処理します。大量の文書を一度に置くと待ち時間が増えるため、投入を分けてください。処理状況はLambdaとEventBridgeのエラー、スロットリングで確認します。

ロールバックでは、今回追加したS3のバージョンを削除します。同名ファイルを上書きした場合は、前のバージョンに戻ります。

`quality_gate.py`のPython関数が、以下のいずれかを選びます。エージェントには副作用ツールを渡さず、コードが確定した判定について原因仮説と通知文の下書きだけを生成させます。

| 判断 | 条件 | 動き |
|---|---|---|
| **承認** | すべての指標がベースライン以上、かつゼロスコアの新規発生なし | コードが`save_baseline_impl` + `send_notification_impl`を実行 |
| **ロールバック** | いずれかの指標が0.2以上低下、またはゼロスコアが新規発生 | コードが`rollback_documents_impl` + `send_notification_impl`を実行 |
| **レビュー依頼** | 承認・ロールバックのいずれにも該当しない軽微な変動 | `/mnt/workspace/pending.json` を残し SNS 通知で運用担当へエスカレーション |

評価エラーや同期の失敗もレビュー依頼になります。判定はPythonコードが行い、エージェントは原因の説明と通知文を作ります。AWSへ接続しないテストは次のとおりです。

```bash
cd sample-code/06/kb-update-agent
# 承認・レビュー依頼・ロールバックで実行する処理を確認
uv run --frozen python -m unittest -v \
  test_quality_gate.py test_handler.py test_tools_sync.py test_tools_rollback.py
```

レビュー依頼の通知メールには `runtimeSessionId` と再開コマンドが書かれているので、運用担当は次のように確定します。

```bash
export KB_UPDATE_RUNTIME_ARN=<Runtime ARN>

# 承認してベースラインを保存する
uv run decide.py approve --session-id <レビュー通知に書かれた sessionId>

# 今回のS3バージョンを削除し、再同期してロールバックする
uv run decide.py rollback --session-id <レビュー通知に書かれた sessionId>
```

通知に記載されたセッションIDで承認・ロールバックしてください。通知後に別の更新が始まった場合は、古いセッションの操作は拒否されます。

## 環境変数一覧

### CDK

| 変数 / プレースホルダー | 用途 |
|---|---|
| `MODEL_SOURCE_ARN` / `modelSourceArn` CDK context | 推論プロファイルのソースモデルARN（必須） |
| `<your-agentcore-role-arn>` | AgentCore Runtime用のIAMロールARN |
| `<your-sns-topic-arn>` | アラーム通知先のSNSトピックARN |

### インターセプター

| 変数 | 用途 |
|---|---|
| `BLOCKED_CUSTOMER_IDS` | ブロック対象の顧客ID（カンマ区切り） |
| `MAX_AMOUNT` | 金額上限（デフォルト: 1,000,000） |

### Knowledge Base更新エージェント

| 変数 | 用途 |
|---|---|
| `KNOWLEDGE_BASE_ID` | Knowledge Base ID |
| `DATA_SOURCE_ID` | データソースID |
| `KB_SYNC_BUCKET` | データソースのバージョニング有効S3バケット（ロールバック時の復元対象） |
| `APP_API_URL` | 評価時の回答生成経路。`kb` でKnowledge Baseを直接 `RetrieveAndGenerate`、URLを与えるとそのアプリAPI経由で評価 |
| `MODEL_ID` | エージェントと回答生成（Knowledge Baseを直接呼ぶ場合）に使うモデル ID |
| `JUDGE_MODEL_ID` | LLM採点用モデル ID |
| `BASELINE_PATH` | ベースライン JSON の置き場。データソースバケットへの取り込み事故を避けるため、Knowledge Baseのデータソースとは別バケット（例: `s3://kb-update-agent-baseline-<suffix>/baseline.json`）を指定する |
| `SNS_TOPIC_ARN` | 通知先のSNSトピックARN |
| `KB_UPDATE_RUNTIME_ARN` | （`decide.py` で使用）レビュー判定を確定する Runtime の ARN |
| `KB_UPDATE_RUNTIME_VERSION` | CDKで環境別エンドポイントへ固定する評価済みRuntimeバージョン（既定: `1`） |

## 構成を選ぶときの注意

- **`cdk/`と`amplify-ui/`は同じ`ENV_NAME=dev`で同時デプロイできません。** リソース名（`support-dev-guardrail`、`support-dev-kb`等）が衝突します。どちらか一方を削除してからもう一方をデプロイしてください
- CDKで使用している`@aws-cdk/aws-bedrock-alpha`はalphaパッケージです。APIが変更される可能性があります
- コスト推定はオンデマンド料金の参考値です。実際の請求額はAWS Cost Explorerで確認してください
- Knowledge Base更新エージェントの評価はcorrectness（正確性）、completeness（網羅性）、groundedness（根拠性）の3軸で0.0/0.5/1.0のスケールで採点します
- インターセプターのブロック対象や金額上限はCDKスタックのpropsで制御します

[共通の準備と費用](../README.md)・[オンライン補足の一覧](../../supplements/README.md)・[作業後のリソース削除](../../ハンズオン後のリソース削除手順.md)
