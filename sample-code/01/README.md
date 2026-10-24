# 第1章 サンプルコード

Amazon Bedrockのモデルを呼び、回答やトークン使用量を確認するスクリプトです。Converse APIとBedrock Mantleの呼び出し例を用意しています。Guardrailsによる制御と、Strands Agentsの最小エージェントも試せます。

[共通の準備](../README.md)を済ませたら、まず`basic_converse.py`を実行します。Guardrailsは作成と動作確認を順に進め、それ以外の例は関心のあるものを選んでください。CLIで作ったエージェントをRuntimeへ配置する例は[WeatherAgent](weatherDemo/README.md)にあります。

## ファイル構成

| ファイル | 内容 |
|---|---|
| `basic_converse.py` | Converse APIによるモデル呼び出しの基本 |
| `latest_models_converse.py` | Sonnet 4.6を基準にSonnet 5、Opus 5／5.5、GPT-6 Sol／Lunaを試し、応答と会話の継続を確認 |
| `basic_mantle_responses.py` | Bedrock MantleのResponses APIからOpenAI GPTを呼び出す基本 |
| `compare_models.py` | 複数モデル（Claude Sonnet / Haiku / Kimi K2.5）に同じ質問を投げて応答を比較 |
| `setup_guardrail.py` | 第1章で利用するGuardrailの設定をまとめて作成するスクリプト |
| `guardrails_converse.py` | Guardrailsを適用したConverse API呼び出しとトレースの確認 |
| `strands_agent_basic.py` | Strands Agentsによるツール呼び出しエージェントとサブエージェント構成 |
| `strands_mantle_responses.py` | コラムで扱うStrands AgentsからBedrock Mantle経由でOpenAI GPTを呼び出す例 |

## セットアップ

```bash
cd sample-code/01
uv sync
```

## 使うモデル

本編のサンプルは主に次のモデルを使います。まず`basic_converse.py`で、Sonnet 4.6を呼び出せることを確認してください。

| モデル | 使う場面 | 準備 |
|---|---|---|
| Claude Sonnet 4.6（`us.anthropic.claude-sonnet-4-6`） | 第1〜6章の回答生成、エージェント、採点 | 必須 |
| Claude Haiku 4.5（`us.anthropic.claude-haiku-4-5-20251001-v1:0`） | 第1章のモデル比較、第3章のメタデータ生成や抽出 | 必須 |
| Amazon Titan Text Embeddings V2（`amazon.titan-embed-text-v2:0`） | 第2章以降のKnowledge Baseの埋め込み | 必須 |
| Cohere Rerank 3.5（`cohere.rerank-v3-5:0`） | 第3章のリランキング | 試す場合 |
| Kimi K2.5、OpenAI GPT-5.5（Bedrock Mantle） | 第1章のモデル比較とMantleの例 | 試す場合 |
| Sonnet 5、Opus 5／5.5、GPT-6 Sol／Luna、Haiku 5.5 | `latest_models_converse.py`と補足 | 試す場合 |

新しいモデルはアカウントによって利用が制限されていることがあります。個人アカウントや利用の少ないアカウントでは`AccessDeniedException`と`not available for this account`を含むエラーで止まることがあります。その場合は「試す場合」のモデルを飛ばして進めてください。本編の手順は「必須」のモデルだけで進められます。

## 使い方

### Converse APIの基本

```bash
uv run basic_converse.py
```

Claude Sonnet 4.6にシンプルな質問を送り、応答テキストとトークン使用量を表示します。

### 最新モデルを試す

```bash
uv run --frozen python latest_models_converse.py --region us-east-1 --model-id us.anthropic.claude-sonnet-4-6
uv run --frozen python latest_models_converse.py --region us-east-1 --model-id us.anthropic.claude-sonnet-5
uv run --frozen python latest_models_converse.py --region us-east-1 --model-id us.anthropic.claude-opus-5
uv run --frozen python latest_models_converse.py --region us-east-1 --model-id us.anthropic.claude-opus-5-5
uv run --frozen python latest_models_converse.py --region us-east-1 --model-id us.openai.gpt-6-sol
uv run --frozen python latest_models_converse.py --region us-east-1 --model-id us.openai.gpt-6-luna
```

質問は`--question`、続けて送る質問は`--follow-up`で指定できます。`--max-tokens`で出力上限（既定4096）、`--output`で回答・使用量・所要時間のJSON保存先を指定します。既存ファイルは上書きしません。思考ブロックは会話の継続に使い、保存ファイルには含めません。

モデルの既定の思考設定を使い、温度や固定思考予算を指定せずに呼び出します。移行時の変更点と推論先の選び方は、[本編のコードで最新モデルを使う](../../supplements/chapter01/本編のコードで最新モデルを使う.md)を参照してください。

### Bedrock MantleのResponses API

```bash
uv run python basic_mantle_responses.py
```

OpenAI SDKからBedrock MantleのResponses APIを呼び出し、GPT-5.5の応答テキストとトークン使用量を表示します。Bedrock APIキーは発行せず、AWS SDKと同じ認証情報から短期トークンを生成して呼び出します。

### モデル比較

```bash
uv run compare_models.py
```

Claude Sonnet 4.6、Claude Haiku 4.5、Kimi K2.5の3モデルに同じプロンプトを送り、応答内容・応答時間・トークン数を並べて確認できます。

### Guardrailのセットアップ

```bash
uv run setup_guardrail.py
```

次の設定を持つGuardrailを作成します。

| 項目 | 設定 |
|---|---|
| コンテンツフィルター | HATE、INSULTS、SEXUAL、VIOLENCE、MISCONDUCT、PROMPT_ATTACKをHIGHに設定 |
| 拒否トピック | `InvestmentAdvice` |
| 機密情報フィルター | EMAILとPHONEはANONYMIZE、CREDIT_DEBIT_CARD_NUMBERはBLOCK |

実行後に表示される`Guardrail ID`を環境変数`GUARDRAIL_ID`に設定してください。

### Guardrailsの動作確認

```bash
export GUARDRAIL_ID="YOUR_GUARDRAIL_ID"
uv run guardrails_converse.py
```

3つのパターンでGuardrailsの挙動を確認します。

1. 通常の質問に応答が返るか確認します。
2. テスト用クレジットカード番号を含む入力がブロックされるか確認します。
3. 拒否トピックに該当する質問がブロックされるか確認します。

トレースを有効化しているため、Guardrailsの評価結果から、反応したポリシーと実際のアクションを要約して出力します。

### Strands Agentsの最小エージェント

```bash
uv run strands_agent_basic.py
```

ツール（`get_current_weather`）を持つ単体エージェントと、専門エージェント（`weather_agent`）を親エージェントから委任するサブエージェント構成の2パターンを順に実行します。

### コラム：Strands AgentsからBedrock Mantleを呼び出す

```bash
uv run python strands_mantle_responses.py
```

Strands Agentsの`OpenAIResponsesModel`からBedrock Mantleを呼び出します。`client_args`にBedrock MantleのURLと`aws-bedrock-token-generator`で生成した短期トークンを渡し、Bedrock APIキーを固定値として持たせずにOpenAI互換エンドポイントを呼び出します。

## 環境変数

| 変数 | 用途 |
|---|---|
| `AWS_REGION` | Bedrock Mantleの例で使うAWSリージョン（既定値は`us-east-1`。GPT-5.5は`us-east-2`でもIn-Region呼び出し可能） |
| `AWS_PROFILE` | AWSプロファイル（必要に応じて設定） |
| `MODEL_ID` | Bedrock Mantleで呼び出すモデルID（既定値は`openai.gpt-5.5`） |

## 補足

- Converse API、Strands Agents、Guardrailsの基本例は、`us-east-1`に固定しています。`latest_models_converse.py`ではリージョンとモデルIDを引数で変更できます。Converse APIの実行例ではCross-Region Inference Profileの`us.`プレフィックス付きIDを使用しています。Bedrock Mantleの例ではOpenAIモデルID（既定値は`openai.gpt-5.5`）を使用しています
- Guardrailsのスクリプトは、`setup_guardrail.py`でGuardrailを作成し、表示されたIDを環境変数`GUARDRAIL_ID`に設定してから実行します

[次は第2章のRAG構築へ](../02/README.md)・[最新モデルを使う補足](../../supplements/chapter01/本編のコードで最新モデルを使う.md)・[作業後のリソース削除](../../ハンズオン後のリソース削除手順.md)
