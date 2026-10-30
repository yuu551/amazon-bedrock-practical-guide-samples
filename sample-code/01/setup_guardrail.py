"""第1章で利用するGuardrailをスクリプトから一括作成する。

実行例:
    uv run python setup_guardrail.py

実行後に表示される `Guardrail ID` を環境変数 `GUARDRAIL_ID` に設定して
`guardrails_converse.py` を実行する。
"""

import boto3

REGION = "us-east-1"

client = boto3.client("bedrock", region_name=REGION)

response = client.create_guardrail(
    name="bedrock-practical-guide-ch01",
    description="第1章のGuardrails動作確認用（コンテンツフィルター・拒否トピック・PIIフィルターを有効化）",
    blockedInputMessaging="申し訳ありませんが、モデルはこの質問に回答できません。",
    blockedOutputsMessaging="申し訳ありませんが、モデルはこの質問に回答できません。",
    # STANDARD tierを使うにはCross-Region Inferenceの有効化が必要
    crossRegionConfig={"guardrailProfileIdentifier": "us.guardrail.v1:0"},
    contentPolicyConfig={
        "filtersConfig": [
            {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
            {"type": "INSULTS", "inputStrength": "HIGH", "outputStrength": "HIGH"},
            {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"},
            {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
            {"type": "MISCONDUCT", "inputStrength": "HIGH", "outputStrength": "HIGH"},
            {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
        ],
        "tierConfig": {"tierName": "STANDARD"},
    },
    topicPolicyConfig={
        "topicsConfig": [
            {
                "name": "InvestmentAdvice",
                "definition": "個別株や投資信託の購入・売却を勧める助言",
                "examples": ["今買うべき銘柄を教えて"],
                "type": "DENY",
            }
        ],
        # STANDARD tierを指定すると日本語など多言語の検出精度が上がる（Cross-Region Inferenceが必要）
        "tierConfig": {"tierName": "STANDARD"},
    },
    sensitiveInformationPolicyConfig={
        "piiEntitiesConfig": [
            {"type": "EMAIL", "action": "ANONYMIZE"},
            {"type": "PHONE", "action": "ANONYMIZE"},
            {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "BLOCK"},
        ]
    },
)

print(f"Guardrail ID: {response['guardrailId']}")
print(f"Guardrail ARN: {response['guardrailArn']}")
print(f"Version: {response['version']}")
print()
print("上記IDを環境変数 GUARDRAIL_ID に設定して guardrails_converse.py を実行してください。")
