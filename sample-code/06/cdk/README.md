# 第6章 サンプルコード: 問い合わせ対応AIの本番基盤（CDK）

第6章のインフラをCDKで構築するサンプルです。Guardrail、推論プロファイル、Knowledge BaseとCloudWatch監視をまとめています。問い合わせ対応エージェントのRuntimeも含めて画面から試す場合は、[Amplify版](../amplify-ui/README.md)を使ってください。

## 前提条件

- Node.js 24
- pnpm（CDK CLIはこのプロジェクトの依存関係から実行します）
- デプロイ先AWSアカウントへの認証情報

## セットアップ

```bash
cd sample-code/06/cdk
pnpm install --frozen-lockfile
```

CDKを初めて使うアカウントとリージョンでは最初に一度だけbootstrapを実行します。

```bash
pnpm exec cdk bootstrap aws://<アカウントID>/us-east-1
```

## デプロイ

推論プロファイルのコピー元となる基盤モデルまたはシステム定義推論プロファイルのARNを、環境変数`MODEL_SOURCE_ARN`またはCDK contextの`modelSourceArn`で指定します。未指定の場合、`SupportAiDev`と`SupportAiProd`は生成されません。

```bash
# 本編と同じSonnet 4.6のシステム定義推論プロファイルをコピー元にする例
export MODEL_SOURCE_ARN="arn:aws:bedrock:us-east-1:<アカウントID>:inference-profile/us.anthropic.claude-sonnet-4-6"

# 対象スタックが表示されることを確認
pnpm exec cdk list

# 開発環境
pnpm exec cdk deploy SupportAiDev

# 本番環境（任意）
pnpm exec cdk deploy SupportAiProd
```

`SupportAiProd`は開発環境と同じ構成（Guardrail、推論プロファイル、Knowledge Base、CloudWatchアラーム）をもう1つ作ります。本番環境の分け方を確かめたいときだけデプロイしてください。ベクトルストアの既定はS3 Vectorsです。OpenSearch Serverlessに切り替えた場合は環境ごとに常駐費がかかります。削除は`pnpm exec cdk destroy SupportAiDev`（`SupportAiProd`も同様）です。

### Knowledge Bases更新品質ゲート

`KbUpdateAgentDev`は任意のスタックで、`KB_UPDATE_KB_ID`を設定したときだけ生成されます。次の値をご自身のKnowledge Baseとデータソースに合わせて設定してから、`cdk list`でスタックが表示されることを確認してください。

```bash
export KB_UPDATE_KB_ID="<your-knowledge-base-id>"
export KB_UPDATE_DS_ID="<your-data-source-id>"
export KB_UPDATE_DATA_BUCKET="<your-kb-data-bucket>"
export KB_UPDATE_SNS_TOPIC_ARN="<your-sns-topic-arn>"

pnpm exec cdk list
pnpm exec cdk deploy KbUpdateAgentDev
```

`KB_UPDATE_TRIGGER_PREFIX`、`KB_UPDATE_APP_API_URL`、`KB_UPDATE_MODEL_ID`、`KB_UPDATE_JUDGE_MODEL_ID`は任意で上書きできます。

トリガーLambdaは、更新処理を直列に実行するため同時実行数を1に予約します。デプロイ前に`aws lambda get-account-settings --region <リージョン>`で予約可能な枠を確認してください。アカウントの同時実行上限が10で、未予約枠を最低10残す必要がある環境では、この予約を追加できません。`UnreservedConcurrentExecution below its minimum value`というエラーになる場合は、Service QuotasからLambdaの同時実行数の上限引き上げを申請してください。未使用の関数を削除するだけでは、アカウントの上限は増えません。

## 注意事項

- `<...>`で囲まれた値は、ご自身の環境に合わせて変更してください
- `@aws-cdk/aws-bedrock-alpha`は alpha パッケージのため、API が変更される可能性があります
- 本プロジェクトと第6章のAmplify統合（`sample-code/06/amplify-ui`）は同じリソース名（`support-dev-guardrail`など）を使います。同一アカウント・同一リージョンで両方をデプロイすると、Guardrail名などの重複でデプロイに失敗します。試す場合はどちらか一方に絞るか、環境名（`envName` / `ENV_NAME`）を分けてください

品質評価の条件と更新・承認の実行例は[第6章のKnowledge Base更新エージェント](../README.md#knowledge-base更新エージェント-1)、削除は[リソース削除手順](../../../ハンズオン後のリソース削除手順.md)を参照してください。

S3 Vectorsのバケットを既存環境と同じ名前で保持する場合は、`SupportAiPlatform`の`vectorBucketName`へ実際のバケット名を渡してください。新規環境では省略するとConstructパスから安定した名前を生成します。
