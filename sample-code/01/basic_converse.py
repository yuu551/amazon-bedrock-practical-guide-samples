"""Converse APIによるモデル呼び出しの基本"""

import boto3

client = boto3.client("bedrock-runtime", region_name="us-east-1")

response = client.converse(
    modelId="us.anthropic.claude-sonnet-4-6",
    messages=[
        {
            "role": "user",
            "content": [
                {"text": "Amazon Bedrockとは何ですか？3文で簡潔に説明してください。"}
            ],
        }
    ],
    system=[{"text": "あなたはAWSサービスに詳しいアシスタントです。日本語で回答してください。"}],
)

output_message = response["output"]["message"]
print(output_message["content"][0]["text"])

usage = response["usage"]
print(f"\n入力トークン数: {usage['inputTokens']}")
print(f"出力トークン数: {usage['outputTokens']}")
