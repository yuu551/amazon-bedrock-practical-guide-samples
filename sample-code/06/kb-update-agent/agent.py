"""品質ゲートの比較結果を説明するエージェントの定義。

承認・ロールバック・レビュー依頼の判定と副作用は handler.py が担う。
エージェントにはツールを渡さず、原因仮説と通知文の下書きだけを任せる。
"""
from __future__ import annotations

from strands import Agent
from strands.models import BedrockModel

SYSTEM_PROMPT = """あなたはKnowledge Base更新の品質分析担当です。
入力には、コードが確定した判定、同期結果、評価結果、ベースライン、更新対象ファイル一覧、
セッションIDが含まれます。判定は変更せず、スコア差分と問題ケースを読み取り、原因仮説と
運用担当向けの簡潔な通知文を日本語で作成してください。

あなたには外部状態を変更するツールはありません。承認、ベースライン保存、文書削除、
再同期、通知送信を実行したと書かないでください。原因仮説は事実と区別してください。"""


def build_agent(model_id: str, region: str) -> Agent:
    """副作用ツールを持たない品質分析エージェントを生成する。"""
    return Agent(
        model=BedrockModel(
            model_id=model_id,
            region_name=region,
            streaming=True,
        ),
        system_prompt=SYSTEM_PROMPT,
    )
