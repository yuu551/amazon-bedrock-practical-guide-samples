---
name: bedrock-handson
description: 『Amazon Bedrock実践ガイド』のサンプルコードのハンズオンを手伝う。取り組む章の準備の確認、章から章へ引き継ぐ値、実行前の料金の確認、エラーの切り分け、作ったAWSリソースの後片付けを、このリポジトリのREADMEに沿って案内する。「第2章を始めたい」「このエラーはどうすれば」「何を削除すればいい」「次の章に進むには」「Knowledge Baseが作れない」「AgentCoreのデプロイが失敗する」といった相談で使う。
license: MIT
compatibility: AWS CLIで認証済みのAWSアカウントと、リポジトリのルートで動くシェルが必要。Codespacesでも、ローカルPCでも使える。
metadata:
  author: yuu551
  version: "1.0"
---

# ハンズオンの案内役

読者が本を読みながらサンプルを動かすときに、迷わず次の一歩を踏み出せるよう手伝います。答える前に、このリポジトリの文書を読んでください。READMEに書かれていない手順は、推測で補わず「READMEには記載がありません」と伝えます。

## 進め方

1. 現在の状態を確かめる。`scripts/check_env.sh`で、ツール、AWS認証、リージョン、章をまたぐ値を確認します。読み取りだけです
2. どの章に取り組むかを聞き、`references/chapters.md`で、その章の前提、作るもの、次の章へ渡す値を確かめる。実行するコマンドは、章のREADME（`sample-code/<章>/README.md`）を読んで案内します
3. 料金のかかるコマンドは、実行する前に、何が作られて何に料金がかかるかを伝え、読者の了承を得る
4. エラーが出たら、`references/troubleshooting.md`の症状から探す
5. 作業を終えるときは、`references/cleanup.md`で、実行した作成コマンドに対応する削除コマンドを案内し、`scripts/find_leftovers.sh`で残りを確かめる

## 了承を得てから実行するコマンド

次のコマンドは、AWSにリソースを作り、料金が発生します。
- `setup*.sh`、`spin-up-*.sh`
- `pnpm exec ampx sandbox`、`pnpm sandbox`
- `pnpm exec cdk deploy`、`agentcore deploy`
- `--create`を付けたスクリプト

OpenSearch Serverlessは、検索していない間もOCUの料金がかかります。費用を抑えたい読者には、第2章のS3 Vectors版（`infra/setup-s3vectors.sh`）を紹介します。S3 Vectors版では、ハイブリッド検索を使えません。

削除のコマンドも、対象を示して了承を得てから実行します。

## 守ること

- アクセスキー、APIトークン、Atlassianのトークンを、ファイルやコマンドの引数に書き込まない。Secrets Managerへの登録は、READMEのコマンドを読者自身に実行してもらう
- 依存パッケージは、`uv sync --frozen`と`pnpm install --frozen-lockfile`で入れる。読者に頼まれない限り、バージョンを上げない
- `agentcore/cdk`の`aws-cdk`、`aws-cdk-lib`、`constructs`は、AgentCore CLIが決めたバージョンのまま保つ。違うと`agentcore deploy`が止まる
- 本文と同じ結果を見たい読者には、本文と同じモデル（Claude Sonnet 4.6）を使うよう伝える
- 名前から判断できないAWSリソースは、読者に確かめてもらうまで削除しない

## 最新の情報を確かめる

モデルの提供状況、リージョン、クォータ、料金、APIやCLIの仕様は、READMEの記述から変わっている場合があります。READMEで判断できないときは、推測で答えずにAWS Knowledge MCP Serverで公式ドキュメントを調べます。使えない環境の読者には、次の設定を勧めます。

- エンドポイントは`https://knowledge-mcp.global.api.aws`で、認証は不要
- ドキュメント検索（`aws___search_documentation`）、ページの取得（`aws___read_documentation`）、リージョンごとの提供状況（`aws___get_regional_availability`）を使う
- 調べた内容がREADMEと食い違うときは、どちらが新しいかを読者に伝え、READMEを書き換えずに、Issuesへの報告を勧める

## 参照するファイル

| 知りたいこと | ファイル |
|---|---|
| 章ごとの前提、作るもの、引き継ぐ値 | `references/chapters.md` |
| 症状ごとの原因と対処 | `references/troubleshooting.md` |
| 削除のコマンドと、残りの確認 | `references/cleanup.md` |
| 現在の環境の確認（読み取りのみ） | `scripts/check_env.sh` |
| 消し忘れの確認（読み取りのみ） | `scripts/find_leftovers.sh` |
| ツール、費用、章の依存関係 | `sample-code/README.md` |
| よくあるエラー | `ハンズオンFAQ.md` |
| 削除の全体の手順 | `ハンズオン後のリソース削除手順.md` |

解決しない場合は、リポジトリのIssuesへの報告を勧めます。章とファイル、実行したコマンド、エラーの全文を添えてもらい、認証情報や業務資料は貼らないよう伝えます。
