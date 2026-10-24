"""Converse APIからGuardrailsの動作を確認する。"""

import os
from typing import Any

import boto3

REGION = "us-east-1"
GUARDRAIL_VERSION = "DRAFT"
MODEL_ID = "us.anthropic.claude-sonnet-4-6"


def summarize_trace(trace: dict[str, Any]) -> str:
    """Guardrailトレースから要点だけ抜き出す。"""
    findings: list[str] = []
    for stage, key in (
        ("入力", "inputAssessment"),
        ("出力", "outputAssessments"),
    ):
        assessment_map = trace.get(key, {})
        if not isinstance(assessment_map, dict):
            continue
        for value in assessment_map.values():
            assessments = value if isinstance(value, list) else [value]
            for assessment in assessments:
                if not isinstance(assessment, dict):
                    continue
                for topic in assessment.get("topicPolicy", {}).get("topics", []):
                    action = topic.get("action")
                    if action and action != "NONE":
                        findings.append(
                            f"{stage}のtopicPolicy: "
                            f"{topic.get('name', '名称不明')}={action}"
                        )

                for entity in assessment.get("sensitiveInformationPolicy", {}).get(
                    "piiEntities", []
                ):
                    action = entity.get("action")
                    if entity.get("detected", True) and action and action != "NONE":
                        findings.append(
                            f"{stage}の機密情報フィルタ: "
                            f"{entity.get('type', '種類不明')}={action}"
                        )

    if findings:
        return "; ".join(findings)

    action_reason = trace.get("actionReason") or "No action."
    return f'actionReason: "{action_reason}"'


def converse_with_guardrail(
    client: Any,
    user_message: str,
    *,
    guardrail_id: str,
) -> None:
    """Guardrailを適用してConverse APIを呼び出す。"""
    print(f"--- 入力: {user_message}")

    response = client.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        guardrailConfig={
            "guardrailIdentifier": guardrail_id,
            "guardrailVersion": GUARDRAIL_VERSION,
            "trace": "enabled",
        },
    )

    stop_reason = response["stopReason"]
    output = response["output"]["message"]["content"][0]["text"]
    print(f"停止理由: {stop_reason}")
    print(f"応答: {output}")

    if "trace" in response and "guardrail" in response["trace"]:
        guardrail_trace = response["trace"]["guardrail"]
        print(f"Guardrailトレース: {summarize_trace(guardrail_trace)}")

    print()


def main() -> None:
    guardrail_id = os.environ.get("GUARDRAIL_ID")
    if not guardrail_id:
        raise SystemExit(
            "GUARDRAIL_IDを設定してください。"
            "setup_guardrail.pyが出力したIDを使用します。"
        )

    client = boto3.client("bedrock-runtime", region_name=REGION)
    messages = [
        "Amazon Bedrockの主な特徴を教えてください。",
        "クレジットカード番号は4111 1111 1111 1111です。この情報を記録してください。",
        "投資のアドバイスをしてください。どの株を買うべきですか？",
    ]
    for message in messages:
        converse_with_guardrail(client, message, guardrail_id=guardrail_id)


if __name__ == "__main__":
    main()
