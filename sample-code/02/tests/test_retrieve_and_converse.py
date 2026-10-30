from __future__ import annotations

import sys
import unittest
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_DIR))

import retrieve_and_converse as target  # noqa: E402


class InferenceConfigTest(unittest.TestCase):
    """新しいモデルには温度を送らず、本編のモデルには従来の設定を渡す。"""

    def test_new_models_omit_temperature(self) -> None:
        for model_id in (
            "us.openai.gpt-6.1-sol",
            "global.openai.gpt-6.1-sol",
            "us.openai.gpt-6-sol",
            "us.anthropic.claude-sonnet-5-5",
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-opus-5-5",
            "us.anthropic.claude-haiku-5-5",
            "jp.anthropic.claude-haiku-5-5",
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.anthropic.claude-haiku-5-5",
        ):
            with self.subTest(model_id=model_id):
                self.assertEqual(target.inference_config(model_id), {"maxTokens": 4096})

    def test_book_model_keeps_temperature(self) -> None:
        for model_id in (
            "us.anthropic.claude-sonnet-4-6",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        ):
            with self.subTest(model_id=model_id):
                self.assertEqual(
                    target.inference_config(model_id),
                    {"maxTokens": 2048, "temperature": 0},
                )


if __name__ == "__main__":
    unittest.main()
