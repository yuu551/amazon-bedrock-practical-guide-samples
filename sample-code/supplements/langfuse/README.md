# StrandsとGrokの観測

Strands Agentのモデル呼び出しとツール実行を記録するサンプルです。まずファイルへ保存します。Langfuseの画面で処理を追いたい場合は、送信を有効にします。このディレクトリは独立したStrands/Grokデモです。第3章のアプリを計測する実装と手順は、[回答の過程を可視化する補足](../../../supplements/chapter03/第3章のRAGアプリで回答の過程を可視化する.md)へ移しました。

## ローカルへ記録する

リポジトリのルートから次を実行します。

```bash
cd sample-code/supplements/langfuse
uv sync --frozen
uv run python observe_agent.py --output traces-local.jsonl
```

AWS認証情報で`us-west-2`のBedrock Mantle、Grok 4.6を呼びます。モデルの利用料金が発生します。架空の問い合わせとローカルのツールだけを使い、トレースはファイルへ保存します。

## Langfuseへ送信する

Langfuseのプロジェクトを用意したら、`LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`を環境変数へ設定し、`--send`を付けます。実行前に補足本文のデータ記録方針を確認してください。

```bash
uv run python observe_agent.py --send --output traces-langfuse.jsonl
```

トレースには問い合わせとツールの入出力が含まれます。自分の資料を使う場合は、送信内容を確認してください。

## ローカルでテストする

送信データの組み立てなどをAWSやLangfuseへ接続せずに確認するテストもあります。

```bash
uv run python -m unittest -v
```
