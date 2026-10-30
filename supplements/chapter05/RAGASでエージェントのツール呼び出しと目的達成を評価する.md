# RAGASでエージェントのツール呼び出しと目的達成を評価する

第5章では、RAGの評価に自前のExcelを使い、RAGASは簡単に紹介し、エージェントの評価にはStrands Evals SDKとAgentCore Evaluationsを使いました。RAGASにも、エージェントが呼んだツールや目的を達成できたかを採点する指標があります。RAGの評価でRAGASに慣れたので、エージェントも同じ道具で見てみたいという方もいるかと思います。

この補足では、本編の改善前と改善後のエージェントを実際に動かし、その実行履歴をRAGASで採点してみます。

## 実行履歴を渡して採点する

RAGASでのエージェント評価は、次の3ステップです。

1. エージェントを動かし、実行履歴を取り出す。Strands Agentsなら`agent.messages`に入っている
2. 実行履歴をRAGASの会話データ（`MultiTurnSample`）に変換し、期待するツールと目的を達成したときの状態を添える
3. 指標で採点する

コードの中心部分は次のとおりです。

```python
from ragas.dataset_schema import MultiTurnSample
from ragas.messages import ToolCall
from ragas.metrics.collections import AgentGoalAccuracyWithReference, ToolCallAccuracy

result = agent("Enterpriseプランで稼働率が99.5%まで…サポートに連絡したいです。")

sample = MultiTurnSample(
    user_input=convert_strands_history(agent.messages),  # Strandsの履歴をRAGASの型へ
    reference_tool_calls=[ToolCall(name="search_support_kb", args={})],
    reference="稼働率に応じたクレジット率、申請窓口、申請期限を案内して完了した。"
              "Jiraへの起票はしていない。",
)

tool = await ToolCallAccuracy().ascore(
    user_input=without_tool_args(sample.user_input),
    reference_tool_calls=sample.reference_tool_calls,
)
goal = await AgentGoalAccuracyWithReference(llm=judge_llm).ascore(
    user_input=sample.user_input,
    reference=sample.reference,
)
```

`convert_strands_history()`は、Strandsの履歴の発話・ツール呼び出し・ツールの結果を、RAGASの`HumanMessage`・`AIMessage`・`ToolMessage`へ置き換える関数です。`judge_llm`はBedrockのモデルを採点に使うための接続クラスです。どちらも`sample-code/05/ragas_agent_eval.py`に入っています。

採点には次の4つの指標を使いました。


| 指標                                  | 見ること                              | 採点方法               |
| ----------------------------------- | --------------------------------- | ------------------ |
| `ToolCallAccuracy`                  | 期待するツールを期待する順番で呼んだか               | プログラムで比較。少しでもずれると0 |
| `ToolCallF1`                        | 期待するツールをどれだけ呼び、余計なツールをどれだけ呼ばなかったか | プログラムで比較。部分点あり     |
| `AgentGoalAccuracyWithReference`    | 用意した「目的を達成した状態」に到達したか             | 生成AIが0か1で判定        |
| `AgentGoalAccuracyWithoutReference` | 会話から読み取れる利用者の目的を達成したか             | 生成AIが0か1で判定。正解は不要  |


## 動かしてみる

本編の`agent_eval_workbook.xlsx`の3ケースを、改善前（`before`）と改善後（`after`）のエージェントで動かします。Knowledge Baseは第2章のものを使い、Confluence検索はサンプルの固定データで代用します。

```bash
cd sample-code/05
uv sync --frozen

# エージェントを動かして実行履歴を保存する
uv run python ragas_agent_eval.py run --mode before --knowledge-base-id <KB_ID>
uv run python ragas_agent_eval.py run --mode after --knowledge-base-id <KB_ID>

# 保存した実行履歴をRAGASで採点する
uv run python ragas_agent_eval.py evaluate --mode before
uv run python ragas_agent_eval.py evaluate --mode after
```

筆者が動かした実行履歴を`agent_data/`に同梱しています。Knowledge Baseを用意していなくても、`evaluate`だけでRAGASの採点を試せます。必要な権限は、Bedrockのモデルを呼び出す`bedrock:InvokeModel`です。`run`ではKnowledge Basesを検索する`bedrock:Retrieve`も使います。

## 改善前後の結果

Claude Sonnet 4.6で採点した結果です。同じ実行履歴を3回採点し、どれも同じ値になりました。


| ケース           | 実際に呼んだツール                    | ツールの順番 | ツールのF1 | 目的（正解あり） | 目的（正解なし） |
| ------------- | ---------------------------- | ------: | ------: | --------: | --------: |
| HTTP 502（改善前） | KB検索                         | 0.00   | 0.67   | 0        | 0        |
| HTTP 502（改善後） | KB検索 → Confluence検索          | 1.00   | 1.00   | 1        | 1        |
| SLAクレジット（改善前） | KB検索 → Confluence検索 → Jira起票 | 0.00   | 0.50   | 0        | 1        |
| SLAクレジット（改善後） | KB検索                         | 1.00   | 1.00   | 1        | 1        |
| SAMLログイン（改善前） | KB検索 → Confluence検索 → Jira起票 | 0.00   | 0.50   | 0        | 1        |
| SAMLログイン（改善後） | KB検索                         | 1.00   | 1.00   | 1        | 1        |


改善前のエージェントは、本編と同じ失敗をしていました。HTTP 502ではConfluenceを検索せずに回答し、SLAクレジットとSAMLログインでは不要なConfluence検索とJira起票をしています。改善後は3ケースとも期待どおりのツールだけを呼び、すべての指標が満点になりました。本編のStrands Evals SDKの結果と同じ傾向です。

ツールの順番（`ToolCallAccuracy`）は、1つでも抜けたり余計に呼んだりすると0になります。合否をはっきり見たいときに向いていますね。F1（`ToolCallF1`）は部分点が付くので、どのくらい期待に近いかを比べられます。HTTP 502の0.67は1つ抜けた場合、SLAクレジットの0.50は2つ余計に呼んだ場合です。

気になったのは正解なしの目的判定で、改善前のSLAクレジットとSAMLログインは1になりました。利用者の質問には正しく答えているので、会話だけを見れば目的は果たしたと判定されます。不要なJira起票は減点されません。「起票はしていない」を含めた正解を渡すと、どちらも0になりました。余計な操作まで見たいなら、正解ありの判定を使います。

## 試すときに気をつけたいこと

### ツールの引数は外して比べる

RAGASのツールの指標は、引数を文字列の完全一致で比べます。エージェントが組み立てる検索語は実行のたびに変わるので、引数を残したままだと、正しいツールを呼んでいても0点になってしまいます。今回も同じSLAクレジットの質問で、改善前は`Enterpriseプラン サービスクレジット 計算方法 稼働率 SLA`、改善後は`Enterpriseプラン サービスクレジット 計算方法 SLA`で検索していました。

そこでサンプルでは、`without_tool_args()`で引数を外し、ツールの名前と順番だけで比べています。検索語の良し悪しまで見たいときは、目的の判定や、本編のStrands Evals SDKのツール引数評価（`ToolParameterAccuracyEvaluator`）と組み合わせても良いと思います。

### 目的を達成した状態は短く誤読されない文で書く

正解ありの目的判定は、2段階で動きます。生成AIが会話から最終状態を英語で要約し、次にその要約と正解の文を比べます。最初は本編のExcelにある期待要点をそのまま並べて正解にしていましたが、改善後のSLAクレジットが3回とも0になりました。回答にはクレジット率の3段階も申請期限もそろっていたのに、要約では細部が落ちて「すべての要点を満たしていない」と判定されたためです。

HTTP 502でも、正解を「分割・再試行・gzip圧縮の回避策を案内した」と書いたら0になりました。判定の理由を見ると、「gzip圧縮の回避」つまりgzipを避けることだと読まれていました。「回避策として、5MB以下への分割・再試行・gzip圧縮を案内した」と書き直すと1になりました。

正解の文は要点の一覧ではなく、目的を達成したときの状態を1〜2文で書きます。しないでほしい操作があれば、「Jiraへの起票はしていない」のように書き添えます。

## 自分のエージェントで試す

自分のエージェントで試すときは、次の3つを用意して実行してみましょう。

1. 評価したい質問でエージェントを動かし、`agent.messages`をJSONで保存する
2. 質問ごとに、期待するツールの呼び出しを決める
3. 質問ごとに、目的を達成した状態を1〜2文で書く

サンプルでは2をExcelの「期待軌跡」から読み、3を`ragas_agent_eval.py`の`REFERENCE_GOALS`に書いています。保存した実行履歴は何度でも採点し直せるので、指標や正解の文を調整するときにエージェントを動かし直す必要はありません。

## 参考資料

- [RAGAS 0.4.3の評価用会話データ](https://docs.ragas.io/en/v0.4.3/concepts/components/eval_sample/)
- [RAGAS 0.4.3のエージェント・ツール利用評価](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/agents/)
- [Amazon Bedrock AgentCoreの評価方式](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations-types.html)
- [Strands Agentsのツール選択評価](https://strandsagents.com/docs/user-guide/evals-sdk/evaluators/tool_selection_evaluator/)

[オンライン補足の一覧へ戻る](../README.md)