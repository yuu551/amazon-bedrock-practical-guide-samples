"""モデルによる応答の違いを確認する"""

import time

import boto3

client = boto3.client("bedrock-runtime", region_name="us-east-1")

MODELS = [
    {"id": "us.anthropic.claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
    {"id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "name": "Claude Haiku 4.5"},
    {"id": "moonshotai.kimi-k2.5", "name": "Kimi K2.5"},
]

PROMPT = (
    "RAG（Retrieval-Augmented Generation）の仕組みを、"
    "技術者ではない人にもわかるように3文で説明してください。"
)

for model in MODELS:
    start = time.time()
    response = client.converse(
        modelId=model["id"],
        messages=[{"role": "user", "content": [{"text": PROMPT}]}],
    )
    elapsed = time.time() - start

    output = response["output"]["message"]["content"][0]["text"]
    usage = response["usage"]

    print(f"=== {model['name']} ({elapsed:.2f}秒) ===")
    print(output)
    print(f"入力: {usage['inputTokens']}トークン / 出力: {usage['outputTokens']}トークン")
    print()
