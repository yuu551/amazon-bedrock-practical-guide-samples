# 第5章 サンプルコード

第5章の評価・改善サイクルを手元で再現するためのスクリプトとサンプルデータです。

RAGアプリの回答を採点し、低評価の原因を調べて資料を更新します。まず[セットアップ](#セットアップ)で評価するAPIのURLを用意し、[評価ケースの準備](#1-評価ケースの準備)から進めてください。エージェント評価では第4章のKnowledge BaseやGatewayを使う例もあります。

[一括評価と文書の更新](#使い方)・[RAGASでのエージェント評価](#6-ragas-でのエージェント評価)・[Strands Evals](#7-strands-evals-での評価)

## ファイル構成

### Excel ワークブック（`data/`）

| ファイル | シート | 用途 |
|---|---|---|
| `eval_workbook.xlsx` | `評価ケース` | 質問、期待要点、期待ツールなど |
|  | `採点基準` | 各指標の満点/部分点/不合格の定義 |
|  | `評価結果` | スクリプトが採点結果を書き込む |
| `feedback_workbook.xlsx` | `フィードバック` | 有人対応や低評価のフィードバック記録 |
|  | `更新候補` | スクリプトがナレッジ更新候補を書き込む |
| `approved_updates.xlsx` | `承認済み更新` | 承認済みドキュメント更新（S3反映用） |

### エージェント評価ワークブック（`agent_data/`）

| ファイル | シート | 用途 |
|---|---|---|
| `agent_eval_workbook.xlsx` | `評価ケース` | 質問、ゴールデン回答の要点、期待軌跡、不要ツール |
|  | `採点基準` | OutputAccuracyとTrajectoryで使う業務固有の基準、および各指標の読み方 |
|  | `評価結果_before` / `評価結果_after` | Strands Evalsのスコアと評価理由（実行時に作成） |

リスト値（期待要点、期待ツールなど）はセミコロン区切りで1セルに記入します。

```
既知障害かどうかを確認する; 回避策を提示する; SLA条件に触れる
```

### スクリプト

| ファイル | 内容 |
|---|---|
| `run_batch_eval.py` | アプリケーションのAPIを呼び、LLMで採点し、結果をExcelへ記録する。ch03のLambda Function URL（IAM認証 + 応答ストリーミング/SSE）を自動判定してSigV4で署名 |
| `extract_feedback_candidates.py` | フィードバックからナレッジ更新候補を抽出し、Excelへ記録する |
| `sync_approved_documents.py` | 承認済み文書をS3にアップロードし、Knowledge Baseの同期を開始する |
| `create_sample_data.py` | サンプルExcelファイルを生成する（初回セットアップ用） |
| `ragas_eval.py` | RAGAS（AspectCritic + ResponseGroundedness）をBedrock（Claude Sonnet 4.6）で走らせる |
| `ragas_eval_collections.py` | `ragas_eval.py`をRAGASの新しい書き方（v1.0で残る書き方）に移した版。保存済みの回答の採点し直しにも使える |
| `compare_answer_models.py` | 同じKnowledge Baseとプロンプトで回答モデルだけを変え、24件をExcelの採点基準とRAGASで採点して比べる |
| `ragas_agent_eval.py` | 第5章のエージェントを改善前・改善後で動かし、実行履歴のツール呼び出しと目的達成をRAGASで評価 |
| `strands_eval.py` | Strands Evals SDK（OutputEvaluator + ToolSelectionAccuracyEvaluator）で第4章と同じ構成のエージェントを評価 |
| `run_agent_batch_eval.py` | ExcelのケースをStrands Evals SDKの4評価器で採点し、改善前後の結果をExcelへ記録する。`--gateway-url`指定時は実Confluenceを検索 |
| `create_agent_sample_data.py` | エージェント評価用Excelを生成する |
| `agentcore_eval.sh` | AgentCore Evaluations on-demand run 用の agentcore CLI ラッパー |
| `common.py` | Excel読み書き、Bedrock応答パーサーなどの共通ヘルパー |

## セットアップ

```bash
cd sample-code/05
uv sync
```

サンプルExcelファイルを再生成する場合は次を実行します。

```bash
uv run create_sample_data.py
```

### 評価するAPIを用意する

一括評価とRAGASは第3章のRAGポータル（本文の想定）または第2章の画面のAPIを呼び出します。どちらもLambda Function URLで、作り方は[第3章のREADME](../03/README.md#ragポータルを動かす)、[第2章のREADME](../02/README.md#フロントエンド)にあります。sandboxのデプロイが終わったら、その`amplify-ui`で次を実行してURLを取り出します。

```bash
export APP_API_URL="$(node -p "require('./amplify_outputs.json').custom.ragFunctionUrl")"
```

第2章の`setup.sh`が最後に表示した`export`の行も、このターミナルで実行しておきます。採点と候補抽出のモデルは本文と同じSonnet 4.6を使います。

```bash
export JUDGE_MODEL_ID=us.anthropic.claude-sonnet-4-6
export FEEDBACK_MODEL_ID=us.anthropic.claude-sonnet-4-6
export KB_SYNC_BUCKET=<第2章のバケット名>
```

第2章のバケット名は第2章の`infra/.env`の`BUCKET`に書かれています。

## 使い方

### 1. 評価ケースの準備

`data/eval_workbook.xlsx`をExcelで開き、`評価ケース`シートに質問と期待条件を記入します。`採点基準`シートで採点基準を確認・調整します。

### 2. 一括評価の実行

```bash
uv run run_batch_eval.py \
  --api-url "$APP_API_URL" \
  --judge-model-id "$JUDGE_MODEL_ID"
```

`--api-url`にはアプリケーションのAPIエンドポイントを指定します。ホスト名が`*.lambda-url.<region>.on.aws`の場合はSigV4署名とSSEストリーミング受信を自動で行います（第2章・第3章のLambda Function URL）。それ以外のURLは`{answer, citations}`または`{answer, references}`を返す通常のJSONエンドポイントとして扱います。API GatewayなどでAPIキーが必要な場合は`--api-key`を追加します。

実行後、`eval_workbook.xlsx`の`評価結果`シートに結果が書き込まれます。Excelでフィルタやソートをかけて、低スコアのケースを確認できます。

### 3. フィードバックからナレッジ更新候補を抽出

```bash
uv run extract_feedback_candidates.py \
  --model-id "$FEEDBACK_MODEL_ID"
```

`feedback_workbook.xlsx`の`更新候補`シートに候補が書き込まれます。

### 4. 承認済み文書の反映

```bash
uv run sync_approved_documents.py \
  --bucket "$KB_SYNC_BUCKET" \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --data-source-id "$DATA_SOURCE_ID"
```

`--dry-run`オプションで、実際のアップロード前に内容を確認できます。第2章のKnowledge Baseは`inclusionPrefixes: ["sample-data/"]`で作成されているため、`approved_updates.xlsx`のS3キーは`sample-data/`配下を指定してください。

反映した文書は第2章のバケットに残ります。第2章の`teardown.sh`（S3 Vectors版は`teardown-s3vectors.sh`）でバケットごと削除されます。

### 5. RAGAS での再評価

```bash
uv run ragas_eval.py \
  --api-url "$APP_API_URL" \
  --judge-model-id us.anthropic.claude-sonnet-4-6
```

`evaluate()`の戻りを`output/ragas_scores.csv`に書き出します。根拠との整合性（`Groundedness`）の評価にはAPIのcitationに含まれる`snippet`、またはKnowledge Bases応答の取得内容を本文として渡します。検索しないケースやヒットしなかったケースは除外せず、「取得チャンクなし」という明示的なコンテキストを渡して24件すべてを採点します。テキストに加えて表の行、音声の文字起こし、動画の要約に対応します。文字起こしのない画像など、テキスト用の根拠との整合性を採点できない形式は黙って除外せず、評価前にエラーで終了します。Knowledge Baseを直接評価する場合は`--knowledge-base-id`に加えて`--retrieve-model-arn`を指定するか、環境変数`RETRIEVE_MODEL_ARN`を設定してください。

実行すると、`AspectCritic`などを`ragas.metrics`から読み込む書き方と`LangchainLLMWrapper`について、非推奨の警告（DeprecationWarning）が表示されます。RAGAS 0.4.2から出るようになった、v1.0での削除を予告する警告です。ロックファイルでRAGASを0.4.3に固定しているので、`uv sync --frozen`で入れれば本文と同じ指標で採点できます。`pyproject.toml`でも1.0未満に制限しています。

RAGAS 1.0へ上げる場合は、新しい書き方への移行が必要です。新しい書き方に移した版を`ragas_eval_collections.py`として用意しました。使う指標と渡す内容は`ragas_eval.py`と同じで、書き方だけを変えています。

| 指標 | `ragas_eval.py`（本文） | `ragas_eval_collections.py` |
|---|---|---|
| 期待要点の網羅性 | `AspectCritic` | `DiscreteMetric`に同じ指示文を書く |
| 根拠との整合性 | `ragas.metrics`の`ResponseGroundedness` | `ragas.metrics.collections`の`ResponseGroundedness` |
| 採点役の呼び出し | `LangchainLLMWrapper` | `InstructorBaseRagasLLM`を継承した接続クラス |

```bash
uv run python ragas_eval_collections.py --api-url "$APP_API_URL"
# 保存済みの質問・回答・検索結果だけで採点し直す
uv run python ragas_eval_collections.py --rows-json <回答を保存したJSON>
```

同じ回答144件（6組×24件）を新旧の両方で採点して比べました。期待要点の網羅性は140件（97%）で判定が一致し、書き方を変えるだけで移行できます。根拠との整合性は、指示文は同じですが、新しい実装では例題付きのプロンプトとJSONでの回答に変わるため、平均が0.1前後低く出ました（例：0.84→0.76）。モデル同士の順位は新旧で同じでした。

大きな差はないので、これから評価を始める場合や、RAGASのバージョンを上げる場合は`ragas_eval_collections.py`を使ってください。本文の`ragas_eval.py`は、本文の結果を再現したいときのために残しています。新旧の点を並べて比べると根拠との整合性に差が出るので、改善の前後を比べるときは、どちらかの書き方に統一してお使いください。

### 6. RAGAS でのエージェント評価

`ragas_agent_eval.py`は、第5章のエージェントを`agent_eval_workbook.xlsx`の3ケースで動かし、実行履歴をRAGAS用の会話データ（`MultiTurnSample`）へ変換して採点します。

```bash
uv sync --frozen
# エージェントを動かして実行履歴を保存する（Knowledge BaseのIDが必要）
uv run python ragas_agent_eval.py run --mode before --knowledge-base-id "$KNOWLEDGE_BASE_ID"
uv run python ragas_agent_eval.py run --mode after --knowledge-base-id "$KNOWLEDGE_BASE_ID"
# 保存した実行履歴をRAGASで採点する
uv run python ragas_agent_eval.py evaluate --mode before
uv run python ragas_agent_eval.py evaluate --mode after
```

実行履歴は`agent_data/agent_histories_<mode>.json`に保存されます。リポジトリには筆者が実行した履歴を同梱しているので、`evaluate`だけを先に試せます。採点結果は`output/ragas_agent_eval_<mode>.json`に保存されます。Confluence検索はローカルの固定データで代用します。

実行には、AWS SDKから使える認証情報と、エージェント・評価モデルに対する`bedrock:InvokeModel`権限、`run`ではKnowledge Basesの`bedrock:Retrieve`権限が必要です。出力の読み方とStrands Evals SDK／AgentCore Evaluationsとの使い分けは、[第5章の補足「RAGASでエージェントのツール呼び出しと目的達成を評価する」](../../supplements/chapter05/RAGASでエージェントのツール呼び出しと目的達成を評価する.md)を参照してください。

### 7. Strands Evals での評価

最小構成は次のコマンドで確認できます。

```bash
uv run strands_eval.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --model-id us.anthropic.claude-sonnet-4-6
```

インラインで Strands Agent を構築し、`OutputEvaluator`と`ToolSelectionAccuracyEvaluator`を走らせます。このサンプルのSDKが返す`EvaluationReport`を評価器ごとに集計し、結果を`output/strands_eval.json`に保存します。

Excelの質問セットと業務固有の基準を使い、改善前後を比較する場合は次を実行します。

```bash
uv run run_agent_batch_eval.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --gateway-url "$GATEWAY_URL" \
  --model-id us.anthropic.claude-sonnet-4-6 \
  --mode before

uv run run_agent_batch_eval.py \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --gateway-url "$GATEWAY_URL" \
  --model-id us.anthropic.claude-sonnet-4-6 \
  --mode after
```

各質問は一度だけ実行し、同じ回答とツール軌跡を次の4評価器へ渡します。

| Excelの列 | Strands Evalsの評価器 | 判定対象 |
|---|---|---|
| `OutputAccuracy` | `OutputEvaluator` | ゴールデン回答に対する最終回答の正確性 |
| `ToolSelection` | `ToolSelectionAccuracyEvaluator` | 実際に呼んだ各ツールが、その時点の文脈で妥当だったか |
| `Trajectory` | `TrajectoryEvaluator` | 期待したツールの不足、余分な呼び出し、順序・回数 |
| `GoalSuccess` | `GoalSuccessRateEvaluator` | ケースごとの成功条件と、不要な副作用の有無 |

`OutputEvaluator`と`TrajectoryEvaluator`には`採点基準`シートの業務基準を渡し、`GoalSuccessRateEvaluator`には各ケースの期待要点と不要ツールから生成した`expected_assertion`を渡します。`ToolSelectionAccuracyEvaluator`はSDK標準の観点で、実際に呼んだツールを1件ずつ判定します。呼ぶべきツールの欠落は検出対象ではないため、`TrajectoryEvaluator`との併用が必要です。

`--gateway-url`を指定すると、第4章で作成したAgentCore Gateway経由で実際のConfluenceを読み取り検索します。反復評価で外部データを増やさないよう、`create_jira_ticket`は呼び出し判断と軌跡だけを記録し、実チケットは作成しません。Gatewayを指定しない場合は、ローカルの読み取り専用フィクスチャで動作します。

### 8. AgentCore Evaluations の実行

```bash
SESSION_ID=<第4章エージェントの呼び出しセッションID> \
RUNTIME_ARN="arn:aws:bedrock-agentcore:us-east-1:...:runtime/support_agent-..." \
bash agentcore_eval.sh
```

事前に対象セッションを作っておく必要があります（AgentCore Runtime呼び出しのトレースが残っていること）。`RUNTIME_ARN`と`SESSION_ID`は第4章のエージェントを呼び出したときの値です。

## 環境変数

| 変数 | 用途 |
|---|---|
| `AWS_REGION` | AWSリージョン（デフォルト: `us-east-1`） |
| `APP_API_URL` | アプリケーションのAPIエンドポイントURL（第3章のFunction URL、またはJSONを返すAPI） |
| `APP_API_KEY` | APIキー（API Gatewayで認証する場合） |
| `JUDGE_MODEL_ID` | LLM採点用モデルID（`run_batch_eval.py`） |
| `RAGAS_JUDGE_MODEL_ID` | RAGAS採点用モデルID（`ragas_eval.py`、デフォルト`us.anthropic.claude-sonnet-4-6`） |
| `RAGAS_AGENT_JUDGE_MODEL_ID` | RAGASエージェント評価用モデルID（`ragas_agent_eval.py`、未指定時は`RAGAS_JUDGE_MODEL_ID`を使用） |
| `RETRIEVE_MODEL_ARN` | RAGASのKnowledge Base直接評価で使う推論プロファイルARN（`--knowledge-base-id`指定時は必須） |
| `STRANDS_JUDGE_MODEL_ID` | Strands Evals採点用モデルID（`strands_eval.py`） |
| `GATEWAY_URL` | `run_agent_batch_eval.py`で実Confluence検索に使う第4章AgentCore GatewayのMCP URL |
| `FEEDBACK_MODEL_ID` | ナレッジ候補抽出用モデルID |
| `MODEL_ID` | Strandsエージェントが使う基盤モデルID |
| `KNOWLEDGE_BASE_ID` | Knowledge Base ID（sync / Strands 共通） |
| `DATA_SOURCE_ID` | データソースID（sync用） |
| `KB_SYNC_BUCKET` | S3バケット名（sync用） |
| `RUNTIME_ARN` | AgentCore Evaluationsで評価するRuntime ARN（`agentcore_eval.sh`で必須） |

## 後片付け

第5章では第5章だけのAWSリソースは作りません。評価に使った第2章〜第4章の環境は評価が終わってから[ハンズオン後のリソース削除手順](../../ハンズオン後のリソース削除手順.md)に沿って削除してください。評価結果は`output/`と、`data/`・`agent_data/`のExcelの結果シートに書き込まれます。Excelを配布時の状態に戻す場合は`git checkout -- data agent_data`を実行します。

## 評価から文書の更新まで

1. `eval_workbook.xlsx`の`評価ケース`シートに評価ケースを追加する
2. `run_batch_eval.py`で一括評価を実行し、`評価結果`シートを確認する
3. 低スコアのケースの原因を特定する（検索/回答/ツール/エスカレーション）
4. 低評価や有人対応の記録を`feedback_workbook.xlsx`の`フィードバック`シートに追加する
5. `extract_feedback_candidates.py`でナレッジ更新候補を抽出する
6. 文書オーナーが候補を確認し、承認済み更新を`approved_updates.xlsx`に記入する
7. `sync_approved_documents.py`で反映し、同じ評価ケースで再評価する

[第6章の運用設計へ](../06/README.md)・[第3章RAGの処理を可視化する補足](../../supplements/chapter03/第3章のRAGアプリで回答の過程を可視化する.md)・[共通の準備と費用](../README.md)
