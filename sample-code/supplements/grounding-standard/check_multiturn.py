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
