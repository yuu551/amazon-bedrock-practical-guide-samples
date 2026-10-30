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
        print("回答\tCLASSIC\tSTANDARD")
        for label, answer in ANSWERS:
            row = [json.dumps(scores(guardrails[tier], answer)) for tier in guardrails]
            print(label, *row, sep="\t")
    finally:
        for guardrail_id in guardrails.values():
            bedrock.delete_guardrail(guardrailIdentifier=guardrail_id)


if __name__ == "__main__":
    main()
