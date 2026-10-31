# 第4章のアプリをAmplifyからCDKへ移行する

第4章の問い合わせ対応アプリを、Amplifyを使わずにCDKだけでデプロイするサンプルです。画面とエージェントは第4章と同じです。画面はS3とCloudFrontから配信し、第4章と同じく画面からAgentCore Runtimeを直接呼びます。`AGENT_ACCESS=api` を付けると、間にLambda Web Functions上のHonoを挟む形にもできます。

詳しい説明とデプロイ時間の比較は[補足記事](../../../supplements/chapter04/第4章のアプリをAmplifyからCDKへ移行する.md)を参照してください。

## 構成

| ディレクトリ | 中身 |
|---|---|
| `web/` | 第4章の画面。`/config.json` から接続先を読む部分と、`actor_id` を送らないようにした部分だけを変えている |
| `api/` | HonoのAPI（`AGENT_ACCESS=api` のときだけ使う）。Cognitoのトークンを検証し、IAM認証でAgentCore Runtimeを呼んでSSEをそのまま返す |
| `agent/` | 第4章のエージェント（`main.py` と `Dockerfile`）。検証済みのトークンの `sub` から `actor_id` を決める。既定ではローカルのDockerでARM64のイメージを作ってデプロイする |
| `infra/` | CDKのスタック。Gateway・Memory・Atlassian用のLambdaは第4章と同じ |
| `gateway-stub/` | Atlassianを設定しないときにGatewayの先に置く、固定の応答を返すLambda |
| `scripts/` | ビルドとデプロイ時間の計測 |

Gatewayから呼ぶLambdaのコードとツールの定義は、第4章の `sample-code/04/gateway-lambda` をそのまま使います。

## 準備

- Node.js 24、pnpm 10.34.5、Dockerを用意します。エージェントはローカルのDockerでARM64のイメージとしてビルドします
- 第2章で作り、同期を終えたKnowledge Baseを用意します
- AWS SSOやawsumeなどで認証したターミナルで実行します
- Lambda Web Functionsを使えるリージョン（米国東部（バージニア北部）など）で作ります
- `cdk deploy` を使う場合は、そのリージョンで一度 `pnpm exec cdk bootstrap` を実行しておきます

```bash
cd sample-code/supplements/ch04-light-serverless
pnpm install --frozen-lockfile
export KNOWLEDGE_BASE_ID=YOUR_KNOWLEDGE_BASE_ID
export MODEL_ID=us.anthropic.claude-sonnet-4-6
# 引用元の資料を画面から開く場合
export KNOWLEDGE_BASE_SOURCE_BUCKET_NAME=YOUR_BUCKET
export KNOWLEDGE_BASE_SOURCE_PREFIXES=sample-data
```

ConfluenceとJiraを設定しない場合、Gatewayの先は `gateway-stub/` の固定の応答を返すLambdaになります。ツールの名前と応答の形は第4章と同じなので、Atlassianのサイトがなくてもツールを呼ぶところまで試せます。

ConfluenceやJiraを使う場合は、第4章と同じく `ATLASSIAN_BASE_URL`、`CONFLUENCE_SPACE_KEY`、`JIRA_PROJECT_KEY`、`JIRA_ISSUE_TYPE` も設定します。デプロイ後に、出力された名前のSecrets Managerのシークレットへ `email` と `apiToken` を保存してください。

同じアカウントに複数の環境を作るときは、`APP_PREFIX`（既定は `ch04-light`）を環境ごとに変えます。

画面からRuntimeを呼ぶ経路は `AGENT_ACCESS` で選べます。

| `AGENT_ACCESS` | 経路 |
|---|---|
| `direct`（既定） | 画面からAgentCore Runtimeを直接呼ぶ。RuntimeがCognitoのトークンを検証する |
| `api` | CloudFrontの `/api/*` をLambda Web Functions上のHonoへ渡し、HonoがIAM認証でRuntimeを呼ぶ。Lambda Web Functionsを使えるリージョンが必要 |

エージェントのデプロイ形式は `AGENT_ARTIFACT` で選べます。

| `AGENT_ARTIFACT` | ビルドする場所 | 向いている環境 |
|---|---|---|
| `docker`（既定） | 実行した環境のDocker | Apple SiliconのMacやCI/CDのARM64の環境など、ARM64でネイティブにビルドできる環境 |
| `zip` | なし（uvがarm64向けのwheelを集める。uvが必要） | x86_64の端末で自前でビルドする場合 |
| `codebuild` | CodeBuild | 第4章と同じ。ローカルでビルドしない場合 |

zipとコンテナは同じAgentCore Runtimeのまま切り替えられないので、切り替えるとRuntimeが作り直されます。

## デプロイ

CloudFormationでデプロイする場合です。

```bash
pnpm run deploy
```

開発中に何度もデプロイする場合はcdkdも使えます。cdkdはCloudFormationを通さずにAWSのAPIを直接呼ぶCDKのCLIです。開発・検証用のツールで、本番での利用は想定されていません。CloudFormationで作ったスタックとは別に状態を持つので、同じ `APP_PREFIX` で両方を使わないでください。

```bash
pnpm exec cdkd bootstrap   # 初回だけ
APP_PREFIX=ch04-cdkd pnpm run deploy:cdkd
```

出力された `AppUrl` を開き、アカウントを作ってログインします。画面のサインアップは誰でも使えるので、試し終わったら環境を削除してください。

## GitHub Actionsでデプロイする

`github-actions/deploy.yml` をリポジトリの `.github/workflows/` に置くと、`develop` へのpushで開発環境へ `cdkd deploy`、`main` へのpushで本番環境へ `cdk deploy` します。ランナーはARM64（`ubuntu-24.04-arm`）です。

- リポジトリの変数に `KNOWLEDGE_BASE_ID`、`KNOWLEDGE_BASE_SOURCE_BUCKET_NAME`、`DEV_APP_PREFIX`、`PROD_APP_PREFIX`、`DEV_ROLE_ARN`、`PROD_ROLE_ARN` を設定します
- AWSにはGitHub ActionsのOIDCプロバイダーと、環境ごとのロールを作ります。本番用はCDKのbootstrapのロール（`cdk-hnb659fds-*`）を引き受ける権限と、ECR Publicにログインする権限（`ecr-public:GetAuthorizationToken`、`sts:GetServiceBearerToken`）を付けます。開発用はcdkdがスタックのリソースを直接作るので、そのぶんの権限を付けます
- 信頼ポリシーの `sub` の形は、`gh api repos/OWNER/REPO/actions/oidc/customization/sub` の `sub_claim_prefix` で確かめてください

## ローカルでAPIを動かす

```bash
pnpm test                 # HonoのAPIのテスト
pnpm run build:api
AWS_LAMBDA_HTTP_ENDPOINT=127.0.0.1:8787 \
USER_POOL_ID=... USER_POOL_CLIENT_ID=... AGENT_RUNTIME_ARN=... \
node api/dist/index.js
```

Lambda Web Functionsでも、待ち受け先が `AWS_LAMBDA_HTTP_ENDPOINT` で渡されるだけで、同じ `index.js` が動きます。

## デプロイ時間の計測

計測に使ったスクリプトと結果を置いています。

- `scripts/measure.mjs` は、目印（`v1`、`v2`…）を1つ進めてからデプロイし、コマンドの時間と変更がCloudFrontやRuntimeから見えるまでの時間を記録します
- `scripts/measure-amplify-hosting.mjs` は、比較用にAmplify Hostingのジョブを実行して時間を記録します
- `scripts/measure-actions.mjs` は、GitHub Actionsのワークフローでデプロイしたときの時間を記録します
- 結果は `results/deploy-times.jsonl`、集計は `results/summary.md` です。`node scripts/summarize.mjs` で作り直せます
- 各回のコマンドの出力は `results/raw/` に保存します。検証環境のIDやURLを含むので、リポジトリには入れていません

## 削除

```bash
pnpm run destroy                              # cdk deploy で作った環境
APP_PREFIX=ch04-cdkd pnpm run destroy:cdkd     # cdkd で作った環境
```

第2章のKnowledge Baseと資料のバケットは削除しません。
