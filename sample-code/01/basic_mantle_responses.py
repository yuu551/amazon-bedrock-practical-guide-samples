import os

from aws_bedrock_token_generator import provide_token
from openai import BedrockOpenAI

REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = os.getenv("MODEL_ID", "openai.gpt-5.5")

client = BedrockOpenAI(
    aws_region=REGION,
    bedrock_token_provider=provide_token,
)

response = client.responses.create(
    model=MODEL_ID,
    input="Amazon Bedrockとは何ですか？3文で簡潔に説明してください。",
)

print(response.output_text)

if response.usage:
    print(f"\n入力トークン数: {response.usage.input_tokens}")
    print(f"出力トークン数: {response.usage.output_tokens}")
