"""Strands AgentsからBedrock Mantle経由でOpenAI GPTを呼び出すコラム用サンプル"""

import os

from aws_bedrock_token_generator import provide_token
from strands import Agent
from strands.models.openai_responses import OpenAIResponsesModel

REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = os.getenv("MODEL_ID", "openai.gpt-5.5")

model = OpenAIResponsesModel(
    model_id=MODEL_ID,
    client_args={
        "base_url": f"https://bedrock-mantle.{REGION}.api.aws/openai/v1",
        "api_key": provide_token(region=REGION),
    },
)

agent = Agent(
    model=model,
    system_prompt="あなたはAmazon Bedrockをわかりやすく説明するアシスタントです。",
)

response = agent("Bedrock MantleをStrandsから使う利点を2つ挙げてください。")
print(response)
