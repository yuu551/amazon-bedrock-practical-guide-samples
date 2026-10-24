# コンテキストグラウンディングチェックにStandardティアが追加された

第1章では、コンテキストグラウンディングチェックは公式にマルチターンの会話型QAやチャットボットが対象外なので、単発のQ&Aの補助フィルタと位置づけるとよい、と紹介しました。その後Amazon Bedrock Guardrailsに新しいStandardティアが加わり、会話調や推論を含む回答にも使えるようになり、対応言語も広がっています。

https://aws.amazon.com/jp/about-aws/whats-new/2026/10/bedrock-guardrails-grounding-standard/

第1章の説明はClassicティアについては今も正しく、Standardティアなら会話調の回答にも使えるようになったと捉えていただくといいです。

これで、日本語のRAGアプリや、会話を続けるアプリにも、回答の根拠チェックを組み込みやすくなりました。会話を続けるアプリでは、チェックに渡す質問に工夫が要ります。

## ClassicとStandardの違い

| | Classic（従来） | Standard（新規） |
|---|---|---|
| 対応言語 | 英語・フランス語・スペイン語 | 日本語を含む60言語以上 |
| 会話調の回答 | 非対応 | 対応 |
| 推論を含む回答 | 資料に書かれていない内容を厳密に見る | 対応 |
| 利用の条件 | なし | クロスリージョン推論が必要 |

Classicは、資料に書かれたことだけを厳密に確認するため、資料どおりの内容を言い換えた回答や、相づちや計算を足した回答でも、点数が下がることがあります。Standardは、意味が同じなら近い点数になるため、正しいのに止められる回答を減らせます。

## 早速試す

ClassicとStandardのガードレールを一時的に作り、同じ回答の点数を並べて表示し、終わると、作ったガードレールは自動で削除されるサンプルを用意しました。

評価に使う資料は、架空の出張旅費規程です。「来週、大阪に2泊の出張があります。宿泊費はいくらまで精算できますか？」という質問に対する、4通りの回答を評価します。

| 回答の種類 | 内容 |
|---|---|
| 言い換え | 規程どおりに、1泊15,000円が上限だと答える |
| 会話・推論 | 相づちを入れ、2泊なら30,000円と計算して答える |
| 誤った金額 | 規程にない20,000円を上限として答える |
| 質問に答えていない | 規程にある申請期限だけを答える |

下記のように`sample-code/supplements/grounding-standard/`で実行します。

```bash
cd sample-code/supplements/grounding-standard
uv run --frozen python check_grounding.py
```

`tierConfig`はboto3 1.43.116以降で使えます。それより前のバージョンでは「Unknown parameter」で弾かれるためバージョンには注意しましょう。

全体のコードは次のとおりです。

```python
"""ClassicとStandardのコンテキストグラウンディングチェックを、同じ回答で比べる。

ガードレールを一時的に2つ作り、ApplyGuardrailで点数を取り、最後に削除する。
"""
import json
import time

import boto3

REGION = "us-east-1"
PROFILE_ID = "us.guardrail.v1:0"  # 東京リージョンでは apac.guardrail.v1:0

SOURCE = """出張旅費規程
- 宿泊費の上限は1泊12,000円です。
- 東京都・大阪府に宿泊する場合、上限は1泊15,000円です。
- 精算の申請は出張終了日から14日以内に行ってください。"""
QUERY = "来週、大阪に2泊の出張があります。宿泊費はいくらまで精算できますか？"
ANSWERS = [
    ("言い換え", "大阪府に宿泊する場合、宿泊費の上限は1泊15,000円です。"),
    ("会話・推論", "大阪出張ですね！大阪府は1泊15,000円が上限なので、2泊なら合計30,000円まで精算できます。出張から戻ったら、14日以内に申請するのを忘れないようにしてくださいね。"),
    ("誤った金額", "大阪府は1泊20,000円が上限なので、2泊なら合計40,000円まで精算できます。"),
    ("質問に答えていない", "精算の申請は出張終了日から14日以内に行ってください。"),
]

bedrock = boto3.client("bedrock", region_name=REGION)
runtime = boto3.client("bedrock-runtime", region_name=REGION)


def create_guardrail(tier: str) -> str:
    # action を NONE にすると、ブロックせず点数だけが返る
    filters = [
        {"type": kind, "threshold": 0.7, "action": "NONE", "enabled": True}
        for kind in ("GROUNDING", "RELEVANCE")
    ]
    response = bedrock.create_guardrail(
        name=f"tmp-grounding-{tier.lower()}-{int(time.time())}",
        blockedInputMessaging="blocked",
        blockedOutputsMessaging="blocked",
        contextualGroundingPolicyConfig={"filtersConfig": filters, "tierConfig": {"tierName": tier}},
        crossRegionConfig={"guardrailProfileIdentifier": PROFILE_ID},
    )
    return response["guardrailId"]


def scores(guardrail_id: str, answer: str) -> dict:
    response = runtime.apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion="DRAFT",
        source="OUTPUT",
        outputScope="FULL",
        content=[
            {"text": {"text": SOURCE, "qualifiers": ["grounding_source"]}},
            {"text": {"text": QUERY, "qualifiers": ["query"]}},
            {"text": {"text": answer, "qualifiers": ["guard_content"]}},
        ],
    )
    found = {}
    for assessment in response.get("assessments", []):
        for item in assessment.get("contextualGroundingPolicy", {}).get("filters", []):
            found[item["type"]] = round(item["score"], 2)
    return found


def main() -> None:
    guardrails = {}
    try:
        for tier in ("CLASSIC", "STANDARD"):
            guardrails[tier] = create_guardrail(tier)
        print("回答	CLASSIC	STANDARD")
        for label, answer in ANSWERS:
            row = [json.dumps(scores(guardrails[tier], answer)) for tier in guardrails]
            print(label, *row, sep="	")
    finally:
        for guardrail_id in guardrails.values():
            bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)


if __name__ == "__main__":
    main()
```

実行すると、回答ごとの根拠（GROUNDING）と関連性（RELEVANCE）の点数が、ClassicとStandardを並べて表示されます。

```text
回答	CLASSIC	STANDARD
言い換え	{"GROUNDING": 0.99, "RELEVANCE": 0.53}	{"GROUNDING": 1.0, "RELEVANCE": 1.0}
会話・推論	{"GROUNDING": 0.77, "RELEVANCE": 0.98}	{"GROUNDING": 1.0, "RELEVANCE": 1.0}
誤った金額	{"GROUNDING": 0.0, "RELEVANCE": 0.97}	{"GROUNDING": 0.0, "RELEVANCE": 1.0}
質問に答えていない	{"GROUNDING": 0.99, "RELEVANCE": 0.0}	{"GROUNDING": 1.0, "RELEVANCE": 0.4}
```

## 結果

しきい値を0.7とすると、ブロックされるかどうかは次のようになります。

| 回答の種類 | Classic | Standard |
|---|---|---|
| 言い換え | ブロック（関連性0.53） | 通過 |
| 会話・推論 | 通過（根拠0.77） | 通過 |
| 誤った金額 | ブロック（根拠0.0） | ブロック（根拠0.0） |
| 質問に答えていない | ブロック（関連性0.0） | ブロック（関連性0.4） |

一番差が出たのは言い換えでした。規程どおりの正しい回答を、Classicは関連性0.53で止めて、Standardは1.0で通過しています。会話・推論は、Classicの根拠が0.77でしきい値に近く、Standardは1.0でStandardの方がこのケースでは適切に評価できていますね。誤った金額は、どちらのティアでも根拠が0.0になり、きちんとブロックされます。

2回実行して、同じ点数が出ましたが、あくまでこのサンプルの結果として確認ください。

## 複数ターンの会話で使う

会話を続けるアプリでも試しました。Converse APIで会話を続け、最後の回答を評価します。

ここで気をつけたいのは、チェックに履歴が渡らないことです。モデルには会話の履歴をすべて渡せますが、チェックが読むのは、資料・質問・回答の3つだけで、たとえば「それなら宿泊費はいくらまで？」と続きの質問だけを渡すと、「大阪」「2泊」が資料にも質問にもないため、正しい回答でも根拠が下がります。

対策として過去のユーザーの発言を、チェックに渡す質問（`query`）に足すことです。資料と質問の渡し方は、[公式ドキュメント](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html)の「Calling contextual grounding check with Converse APIs」と同じです。最後のユーザーメッセージに、`grounding_source`と`query`の`guardContent`を入れます。

```python
query = build_query(HISTORY, QUESTION, with_history)
last = {"role": "user", "content": [guard(SOURCE, "grounding_source"), guard(query, "query")]}
```

質問は1,000文字までで、超えるとエラーになります。`build_query`は、新しい発言から数えて、収まらない古い発言を落とし、渡すのはユーザーの発言だけです。アシスタントの過去の発言を入れると、誤った回答まで根拠になってしまうためです。

全体のコードは次のとおりです。

```python
"""複数ターンの会話で、ClassicとStandardのコンテキストグラウンディングチェックを比べる。

Converse APIで会話を続け、最後の回答を評価する。評価に渡す質問を、
続きの質問だけにした場合と、過去のユーザーの発言を足した場合で比べる。
"""
import json
import time

import boto3

REGION = "us-east-1"
PROFILE_ID = "us.guardrail.v1:0"  # 東京リージョンでは apac.guardrail.v1:0
QUERY_LIMIT = 1000  # 質問の文字数の上限
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

SOURCE = """出張旅費規程
- 宿泊費の上限は1泊12,000円です。
- 東京都・大阪府に宿泊する場合、上限は1泊15,000円です。
- 精算の申請は出張終了日から14日以内に行ってください。"""
HISTORY = [
    {"role": "user", "content": [{"text": "出張旅費規程について教えてください。"}]},
    {"role": "assistant", "content": [{"text": "はい、宿泊費の上限と精算の期限について案内できます。何を知りたいですか？"}]},
    {"role": "user", "content": [{"text": "来週、大阪に2泊の出張があります。"}]},
    {"role": "assistant", "content": [{"text": "大阪出張ですね。宿泊費の上限についてお調べしましょうか？"}]},
]
QUESTION = "それなら宿泊費はいくらまで精算できますか？あと、申請はいつまでにすればいいですか？"
SYSTEM = [{"text": "参照資料に書かれていることだけを使い、会話の流れを踏まえて簡潔に答えてください。資料にない理由や背景は付け加えないでください。"}]

bedrock = boto3.client("bedrock", region_name=REGION)
runtime = boto3.client("bedrock-runtime", region_name=REGION)


def guard(text: str, qualifier: str) -> dict:
    return {"guardContent": {"text": {"text": text, "qualifiers": [qualifier]}}}


def build_query(history: list, question: str, with_history: bool) -> str:
    """チェックに渡す質問を作る。履歴を足す場合は、過去のユーザーの発言を質問の前に連ねる。

    質問は1,000文字までなので、収まらないときは古い発言から落とす。
    """
    if not with_history:
        return question
    user_turns = [m["content"][0]["text"] for m in history if m["role"] == "user"]  # アシスタントの発言は渡さない
    kept = []
    length = len(question)
    for turn in reversed(user_turns):  # 新しい発言から数える
        length += len(turn) + 1  # 改行の分
        if length > QUERY_LIMIT:
            break
        kept.insert(0, turn)
    return "\n".join(kept + [question])


def create_guardrail(tier: str) -> str:
    filters = [
        {"type": kind, "threshold": 0.7, "action": "NONE", "enabled": True}
        for kind in ("GROUNDING", "RELEVANCE")
    ]
    response = bedrock.create_guardrail(
        name=f"tmp-multiturn-{tier.lower()}-{int(time.time())}",
        blockedInputMessaging="blocked",
        blockedOutputsMessaging="blocked",
        contextualGroundingPolicyConfig={"filtersConfig": filters, "tierConfig": {"tierName": tier}},
        crossRegionConfig={"guardrailProfileIdentifier": PROFILE_ID},
    )
    return response["guardrailId"]


def ask(guardrail_id: str, with_history: bool) -> dict:
    # モデルには履歴をすべて渡す。チェックには、最後のメッセージの guardContent だけが渡る
    query = build_query(HISTORY, QUESTION, with_history)
    last = {"role": "user", "content": [guard(SOURCE, "grounding_source"), guard(query, "query")]}
    response = runtime.converse(
        modelId=MODEL_ID,
        messages=HISTORY + [last],
        system=SYSTEM,
        guardrailConfig={"guardrailIdentifier": guardrail_id, "guardrailVersion": "DRAFT", "trace": "enabled_full"},
    )
    found = {}
    for assessments in response["trace"]["guardrail"]["outputAssessments"].values():
        for assessment in assessments:
            for item in assessment.get("contextualGroundingPolicy", {}).get("filters", []):
                found[item["type"]] = round(item["score"], 2)
    return found


def main() -> None:
    guardrails = {}
    try:
        for tier in ("CLASSIC", "STANDARD"):
            guardrails[tier] = create_guardrail(tier)
        print("評価に渡す質問\tCLASSIC\tSTANDARD")
        for label, with_history in (("続きの質問だけ", False), ("過去の発言を足す", True)):
            row = [json.dumps(ask(guardrails[tier], with_history)) for tier in guardrails]
            print(label, *row, sep="\t")
    finally:
        for guardrail_id in guardrails.values():
            bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)


if __name__ == "__main__":
    main()
```

```bash
uv run --frozen python check_multiturn.py
```

```text
評価に渡す質問	CLASSIC	STANDARD
続きの質問だけ	{"GROUNDING": 0.05, "RELEVANCE": 1.0}	{"GROUNDING": 0.0, "RELEVANCE": 1.0}
過去の発言を足す	{"GROUNDING": 0.09, "RELEVANCE": 1.0}	{"GROUNDING": 1.0, "RELEVANCE": 1.0}
```

続きの質問だけだと、Standardでも根拠は0.0です。過去の発言を追加すると、経緯も読み取りStandardは1.0で通ります。Classicは、足しても0.09で、正しい回答を止めました。繰り返し実行しても、同じ傾向でした。

会話が長いときは、古い発言から落とすため、会話の前半で決まった前提が消えることがあります。大事な前提は、直近の発言に含めるか、要約して追加するなどの対応が必要です。

## 設定のしかた

コンソールではコンテキストグラウンディングチェックの設定画面で「Contextual grounding tier」を選びます。Standardを選ぶには、先にガードレールの詳細でクロスリージョン推論を有効にしておきましょう。

APIでは、下記のように`tierConfig`で指定します。

```json
{
  "contextualGroundingPolicyConfig": {
    "filtersConfig": [
      { "type": "GROUNDING", "threshold": 0.7 },
      { "type": "RELEVANCE", "threshold": 0.7 }
    ],
    "tierConfig": { "tierName": "STANDARD" }
  },
  "crossRegionConfig": { "guardrailProfileIdentifier": "us.guardrail.v1:0" }
}
```

東京リージョンでは、本書の第1章と同じく`apac.guardrail.v1:0`を指定します。

気をつけたい点は2つです。

- `UpdateGuardrail`は設定全体を置き換えるため、`tierConfig`を省くとClassicに設定されます
- ティアで評価の仕方が変わるので、ティアを切り替えたら自分のデータで閾値を確かめ直してみましょう

## 本書の記述について

第1章「コンテキストグラウンディングチェックによるハルシネーション検出」で会話型QAを対象外とした説明は、Standardティアの登場で一部読み替えが必要になりました。

[正誤表](../../正誤表.md)にも載せているので合わせてご参照ください。

[オンライン補足の一覧に戻る](../README.md)
