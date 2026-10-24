"""Strands Agentsによるエージェントの基本 ── ツール呼び出しとサブエージェントを体験する"""

from strands import Agent, tool
from strands.models import BedrockModel

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6",
    region_name="us-east-1",
)


# --- ツールの定義 ---


@tool
def get_current_weather(city: str) -> str:
    """指定された都市の現在の天気を取得します。"""
    # 実際には天気APIを呼び出す。ここではデモ用の固定値を返す
    weather_data = {
        "東京": "晴れ、気温22℃、湿度45%",
        "大阪": "曇り、気温20℃、湿度60%",
        "札幌": "雪、気温-2℃、湿度70%",
    }
    return weather_data.get(city, f"{city}の天気情報は見つかりませんでした")


# --- 1. ツールを使うエージェント ---

print("=== ツール呼び出しの基本 ===")
agent = Agent(
    model=model,
    system_prompt="あなたは天気情報アシスタントです。ツールを使って正確な情報を提供してください。",
    tools=[get_current_weather],
)
agent("東京と札幌の天気を比較して教えてください。")

# --- 2. サブエージェント構成 ---

print("\n\n=== サブエージェント構成 ===")

# 天気専門のサブエージェント
weather_agent = Agent(
    model=model,
    name="weather_agent",
    description="天気に関する質問に回答する専門エージェント",
    system_prompt="あなたは天気の専門家です。",
    tools=[get_current_weather],
)

# 親エージェント（サブエージェントをツールとして持つ）
orchestrator = Agent(
    model=model,
    system_prompt=(
        "あなたは総合アシスタントです。専門エージェントに委任して回答してください。"
        "専門エージェントの呼び出しは、1件ずつ順番に行ってください（並列実行はしない）。"
    ),
    tools=[weather_agent],
)
orchestrator("出張で東京と大阪に行きます。現在の天気をそれぞれ調べてください。")
