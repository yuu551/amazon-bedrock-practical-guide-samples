# 天気を答えるWeatherAgent

第1章の天気ツールを、AgentCore CLIでローカル実行・デプロイする例です。東京・大阪・札幌の天気は`main.py`内の固定データで、実際の気象情報は取得しません。回答の生成にはClaude Sonnet 4.6を使います。

完成したプロジェクトを同梱しているため、`agentcore create`で作り直す必要はありません。

## 準備するもの

[共通の実行環境とAWS認証](../../README.md)に加え、AgentCore CLIとDocker互換ランタイムを用意します。このプロジェクトはContainer形式です。Docker DesktopやOrbStackなどを起動してから進めてください。

このサンプルで使うCLIをインストールします。

```bash
npm install -g @aws/agentcore@0.28.1
agentcore --version
```

## ローカルで動かす

リポジトリのルートから実行します。

```bash
cd sample-code/01/weatherDemo
agentcore dev
```

起動したInspector、または別ターミナルの同じディレクトリから質問を送ります。ローカル実行でも、モデル呼び出しにはAWS認証と利用料金が必要です。

```bash
agentcore invoke --dev "東京と札幌の天気を比較してください。"
```

終了するときは、`agentcore dev`を起動したターミナルでCtrl+Cを押します。

## AWSへデプロイする

`agentcore/aws-targets.json`には著者のアカウント情報を含めていません。ご自身のデプロイ先を設定してください。次の`YOUR_ACCOUNT_ID`を12桁のAWSアカウントIDに置き換えます。

```json
[
  {"name": "dev", "account": "YOUR_ACCOUNT_ID", "region": "us-east-1"}
]
```

`weatherDemo/`で設定を検証し、デプロイします。

```bash
agentcore validate
agentcore deploy
agentcore status
agentcore invoke "東京と札幌の天気を比較してください。"
```

RuntimeとMemoryの構成は`agentcore/agentcore.json`で確認できます。使い終えた環境は[リソース削除手順](../../../ハンズオン後のリソース削除手順.md)に従って片付けてください。

## コードを読む

| 場所 | 内容 |
|---|---|
| [app/WeatherAgent](app/WeatherAgent/README.md) | 天気ツール、モデル設定、会話の処理 |
| `agentcore/agentcore.json` | RuntimeとMemoryの定義 |
| `agentcore/aws-targets.json` | デプロイ先アカウントとリージョン |
| [agentcore/cdk](agentcore/cdk/README.md) | CLIが使うCDKプロジェクト |
| [agentcore/.llm-context](agentcore/.llm-context/README.md) | 設定ファイルの型定義 |

[第1章のコード一覧に戻る](../README.md)・[AgentCore CLIの公式リポジトリ](https://github.com/aws/agentcore-cli)
