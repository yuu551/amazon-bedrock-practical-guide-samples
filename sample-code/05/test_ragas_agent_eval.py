"""RAGASエージェント評価用の履歴変換を確認するテスト。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from pydantic import BaseModel
from ragas.messages import AIMessage, HumanMessage, ToolMessage
from ragas.metrics.collections import ToolCallAccuracy, ToolCallF1

import ragas_agent_eval


HISTORY = [
    {"role": "user", "content": [{"text": "SAML SSOでログインできません。"}]},
    {
        "role": "assistant",
        "content": [
            {"text": "KBを検索します。"},
            {
                "toolUse": {
                    "toolUseId": "t1",
                    "name": "search_support_kb",
                    "input": {"query": "SAML SSO ログイン"},
                }
            },
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "toolResult": {
                    "toolUseId": "t1",
                    "content": [{"text": "IdPのユーザー割り当てを確認します。"}],
                }
            }
        ],
    },
    {"role": "assistant", "content": [{"text": "IdPのユーザー割り当てを確認してください。"}]},
]

CASE = {
    "id": "custom-case",
    "question": "SAML SSOでログインできません。",
    "expected_points": ["IdPのユーザー割り当てを確認する"],
    "expected_trajectory": ["search_support_kb"],
    "forbidden_tools": ["create_jira_ticket"],
}


class RagasAgentHistoryTest(unittest.TestCase):
    def test_strands_history_becomes_multi_turn_sample(self) -> None:
        sample = ragas_agent_eval.build_sample(CASE, HISTORY)

        self.assertIsInstance(sample.user_input[0], HumanMessage)
        self.assertIsInstance(sample.user_input[1], AIMessage)
        self.assertEqual(sample.user_input[1].tool_calls[0].name, "search_support_kb")
        self.assertIsInstance(sample.user_input[2], ToolMessage)
        self.assertIsInstance(sample.user_input[3], AIMessage)
        self.assertEqual(sample.reference_tool_calls[0].name, "search_support_kb")
        self.assertIn("create_jira_ticket", sample.reference)

    def test_tool_scores_ignore_arguments(self) -> None:
        sample = ragas_agent_eval.build_sample(CASE, HISTORY)
        names_only = ragas_agent_eval.without_tool_args(sample.user_input)

        async def score(metric):
            return (await metric.ascore(
                user_input=names_only,
                reference_tool_calls=sample.reference_tool_calls,
            )).value

        self.assertEqual(asyncio.run(score(ToolCallAccuracy())), 1.0)
        self.assertEqual(asyncio.run(score(ToolCallF1())), 1.0)

    def test_recorded_histories_convert(self) -> None:
        for mode in ("before", "after"):
            path = Path(f"agent_data/agent_histories_{mode}.json")
            for record in json.loads(path.read_text(encoding="utf-8"))["cases"]:
                ragas_agent_eval.convert_strands_history(record["messages"])

    def test_unsupported_content_is_not_silently_dropped(self) -> None:
        history = [
            {
                "role": "assistant",
                "content": [{"reasoningContent": {"text": "internal"}}],
            }
        ]

        with self.assertRaisesRegex(ValueError, "Unsupported assistant content"):
            ragas_agent_eval.convert_strands_history(history)


class JudgeParamsTest(unittest.TestCase):
    """Haiku 5.5の採点では温度を送らず、Haiku 4.5は従来どおり温度0を送る。"""

    def _params(self, model_id: str) -> dict:
        llm = object.__new__(ragas_agent_eval.BedrockStructuredLLM)
        llm.model_id = model_id
        llm.client = MagicMock()
        llm.generate("p", BaseModel)
        return llm.client.chat.completions.create.call_args.kwargs

    def test_haiku_5_5_omits_temperature(self) -> None:
        for model_id in ("us.anthropic.claude-haiku-5-5", "jp.anthropic.claude-haiku-5-5"):
            with self.subTest(model_id=model_id):
                params = self._params(model_id)
                self.assertNotIn("temperature", params)
                self.assertEqual(params["max_tokens"], 4096)

    def test_haiku_4_5_keeps_temperature(self) -> None:
        params = self._params("us.anthropic.claude-haiku-4-5-20251001-v1:0")
        self.assertEqual(params["temperature"], 0)
        self.assertEqual(params["max_tokens"], 1024)


if __name__ == "__main__":
    unittest.main()
