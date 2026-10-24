#!/usr/bin/env python3
"""GPT-5.6 LunaでKnowledge Basesの検索結果から回答を生成する。"""
from __future__ import annotations

import argparse
import os

import boto3


REGION = "us-east-1"
MODEL_ID = "us.openai.gpt-5.6-luna"
SYSTEM_PROMPT = """\
あなたは社内ドキュメントに基づいて回答するアシスタントです。
検索結果に根拠がある場合だけ回答してください。
根拠がなければ「お探しの情報は見つかりませんでした」と回答し、
推測や一般的な知識で補完しないでください。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrieveで検索したチャンクをGPT-5.6 Lunaへ渡します。"
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
        help="Knowledge Base ID（環境変数 KNOWLEDGE_BASE_ID でも指定可）。",
    )
    parser.add_argument(
        "--question",
        required=True,
        help="Knowledge Basesに問い合わせる質問文。",
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=4,
        help="検索で取得するチャンク数（デフォルト: 4）。",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", REGION),
        help=f"AWSリージョン（デフォルト: {REGION}）。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.knowledge_base_id:
        raise SystemExit(
            "--knowledge-base-id または環境変数 KNOWLEDGE_BASE_ID を指定してください。"
        )

    agent_runtime = boto3.client(
        "bedrock-agent-runtime",
        region_name=args.region,
    )
    bedrock_runtime = boto3.client(
        "bedrock-runtime",
        region_name=args.region,
    )

    retrieved = agent_runtime.retrieve(
        knowledgeBaseId=args.knowledge_base_id,
        retrievalQuery={"text": args.question},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": args.num_results,
            }
        },
    )
    results = retrieved.get("retrievalResults", [])
    if not results:
        print("お探しの情報は見つかりませんでした。")
        return

    references: list[str] = []
    source_uris: list[str] = []
    for index, result in enumerate(results, start=1):
        text = result.get("content", {}).get("text", "")
        uri = result.get("location", {}).get("s3Location", {}).get("uri", "")
        references.append(f"[検索結果{index}]\n{text}")
        source_uris.append(uri)

    context = "\n\n".join(references)
    response = bedrock_runtime.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"質問:\n{args.question}\n\n"
                            f"検索結果:\n{context}"
                        )
                    }
                ],
            }
        ],
        # Lunaはtemperatureを受け付けないため指定しない
        inferenceConfig={"maxTokens": 2048},
    )

    content = response.get("output", {}).get("message", {}).get("content", [])
    answer = "\n".join(
        block.get("text", "") for block in content if block.get("text")
    ).strip()

    print("回答:")
    print(answer)
    print("\n出典:")
    for index, uri in enumerate(source_uris, start=1):
        print(f"[{index}] {uri}")


if __name__ == "__main__":
    main()
