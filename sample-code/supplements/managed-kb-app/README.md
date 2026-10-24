# Managed Knowledge Baseをアプリから使う

Managed Knowledge Baseの検索経過、回答のストリーミング、取得した文書をローカルのブラウザで確認するサンプルです。画面は本編のSampleConnectと同じデザインで、回答はストリーミング表示します。回答モデルは本編と同じClaude Sonnet 4.6です。

詳しい使い方は[補足記事](../../../supplements/managed-knowledge-bases/Managed-Knowledge-Baseをアプリに組み込んでストリーミングする.md)を参照してください。

[起動の準備](#準備)・[検索と回答の3方式](#3つの方式)・[イベントの保存](#cliでイベントを保存する)・[回答の例](results/README.md)

## 準備

- Python 3.11以上とuv、Node.js 24（20.19以上でも動きます）、pnpm 10.34.5を用意します。Codespacesでは準備済みです。
- [補足記事の準備手順](../../../supplements/managed-knowledge-bases/Managed-Knowledge-Baseをアプリに組み込んでストリーミングする.md)に従い、SampleConnectの架空の資料を取り込んだManaged Knowledge Baseを用意し、同期を完了します。第2章のClassic Knowledge BaseのIDは使えません。
- AWS SSOやawsumeなどで認証し、同じターミナルから起動します。アクセスキーをHTMLへ書く必要はありません。
- Knowledge Baseの検索・Agentic Retrievalと、回答モデルをストリーミングで呼び出せる権限が必要です。Agentic Retrievalの権限は[公式のIAM設定](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)を参照してください。

```bash
cd sample-code/supplements/managed-kb-app
uv sync --frozen
cd ui
pnpm install --frozen-lockfile
pnpm build
cd ..
export MANAGED_KB_ID=YOUR_MANAGED_KB_ID
uv run --frozen python app.py
```

ブラウザで `http://127.0.0.1:8767` を開き、質問を入力して送信ボタンを押します。起動だけでは検索を実行しません。通常はKnowledge BaseとSonnetのどちらも`us-east-1`を使い、モデルIDは`us.anthropic.claude-sonnet-4-6`です。KBの作成先が異なる場合は`--kb-region`、モデルの接続先を変える場合は`--model-region`を指定し、そのリージョンでの対応と利用条件を確認してください。

画面だけを確認する場合は、次のコマンドで起動できます。黄色のデモ表示が付き、AWSへの接続や課金はありません。デモ回答は実測結果ではありません。

```bash
uv run --frozen python app.py --demo
```

## 3つの方式

| CLIの方式名 | 検索 | 回答生成 |
|---|---|---|
| `agentic-model`（既定） | AgenticRetrieveStream、generateResponse=False | Sonnet 4.6 / ConverseStream（通常） |
| `retrieve-model` | Retrieve | Sonnet 4.6 / ConverseStream（通常） |
| `agentic-managed` | AgenticRetrieveStream、generateResponse=True | AWSが選ぶマネージドモデル |

回答モデルを自分で指定する方式でも、Agentic Retrieval内部の計画用モデルはAWSが選びます。`agentic-managed`の回答をSonnetの出力としては扱いません。

## CLIでイベントを保存する

イベントを保存する例です。

```bash
uv run --frozen python app.py \
  --mode agentic-model \
  --question 'Enterpriseプランの監査ログの保存期間と、SAML SSOでログインできない場合の確認手順を教えてください。' \
  --output /tmp/managed-kb-agentic-sonnet.jsonl
```

保存先は新規ファイルに限ります。ファイルが既にある場合は別名を指定してください。JSONLには到着時刻、検索の処理名、資料本文、回答、所要時間が含まれます。Knowledge Base IDや質問は記録に自動で付けないため、比較時は実行条件も別に残してください。サンプルに添付した記録は架空の資料を使ったものです。自身の資料で実行したファイルを公開する前には本文と出力を確認してください。

各方式の回答や処理時間は、[実行結果](results/README.md)で確認できます。

## コードとテスト

検索処理は`retrieval.py`、HTTPサーバーは`app.py`、画面は`ui/src/App.tsx`にあります。AWSに接続しないテストは、次のコマンドで実行できます。

```bash
uv run --frozen python -m unittest -v
```

このサンプルは個人がローカルで試す構成です。`127.0.0.1`だけで待ち受け、同時実行は1件に制限しています。Codespacesの転送URLや共有サーバー向けの認証処理は実装していません。過去の問い合わせは画面に残りますが、モデルへ会話履歴は送信しません。ACLの利用者情報とGuardrailsも送信しません。ブラウザを閉じても、進行中のAWS処理が直ちに中止されるとは限りません。

終了するときはターミナルでCtrl+Cを押します。アプリはAWSリソースを作成・削除しません。付録で作ったKnowledge Baseやデータを使い終えたら、付録の削除手順に従って片付けてください。
