from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_DIR / "lambda"))
sys.path.insert(0, str(SAMPLE_DIR))
os.environ.setdefault("AWS_REGION", "us-east-1")

import rag_handler  # noqa: E402
import retrieve_and_generate  # noqa: E402

HAIKU_5_5_IDS = (
    "us.anthropic.claude-haiku-5-5",
    "jp.anthropic.claude-haiku-5-5",
    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.anthropic.claude-haiku-5-5",
)
HAIKU_4_5_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class RagHandlerModelSettingsTest(unittest.TestCase):
    """Haiku 5.5には温度を送らずKBのプロンプトを渡し、Haiku 4.5は従来どおりにする。"""

    def _kb_config(self, model_id: str) -> dict:
        with (
            patch.object(rag_handler, "MODEL_ID", model_id),
            patch.object(rag_handler, "KNOWLEDGE_BASE_ID", "KB"),
            patch.object(rag_handler, "agent_runtime") as runtime,
        ):
            runtime.retrieve_and_generate.return_value = {"output": {"text": "a"}}
            rag_handler.handle_retrieve_and_generate("q")
        request = runtime.retrieve_and_generate.call_args.kwargs
        return request["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]

    def test_haiku_5_5_omits_temperature_and_passes_prompts(self) -> None:
        for model_id in HAIKU_5_5_IDS:
            with self.subTest(model_id=model_id):
                self.assertEqual(rag_handler.inference_config(model_id), {"maxTokens": 4096})
                config = self._kb_config(model_id)
                generation = config["generationConfiguration"]
                orchestration = config["orchestrationConfiguration"]
                for placeholder in ("$query$", "$search_results$", "$output_format_instructions$"):
                    self.assertIn(placeholder, generation["promptTemplate"]["textPromptTemplate"])
                for placeholder in ("$conversation_history$", "$query$", "$output_format_instructions$"):
                    self.assertIn(placeholder, orchestration["promptTemplate"]["textPromptTemplate"])
                self.assertNotIn("temperature", generation["inferenceConfig"]["textInferenceConfig"])
                self.assertEqual(
                    retrieve_and_generate.knowledge_base_prompts(model_id),
                    rag_handler.knowledge_base_prompts(model_id),
                )

    def test_haiku_4_5_unchanged(self) -> None:
        self.assertEqual(
            rag_handler.inference_config(HAIKU_4_5_ID),
            {"maxTokens": 2048, "temperature": 0},
        )
        self.assertEqual(
            self._kb_config(HAIKU_4_5_ID),
            {"knowledgeBaseId": "KB", "modelArn": HAIKU_4_5_ID},
        )
        self.assertEqual(retrieve_and_generate.knowledge_base_prompts(HAIKU_4_5_ID), {})

    def test_luna_keeps_previous_settings(self) -> None:
        self.assertEqual(rag_handler.inference_config("us.openai.gpt-5.6-luna"), {"maxTokens": 2048})


if __name__ == "__main__":
    unittest.main()
