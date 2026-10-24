"""RAGAS Groundednessへ取得本文が渡ることを確認するテスト。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
from ragas.dataset_schema import SingleTurnSample
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import ResponseGroundedness

import ragas_eval
import run_batch_eval


class RagasInputTest(unittest.TestCase):
    def test_application_citation_snippets_become_retrieved_contexts(self) -> None:
        response = MagicMock()
        response.json.return_value = {
            "answer": "90日です。",
            "citations": [
                {"uri": "s3://bucket/a.md", "snippet": "監査ログは90日保持します。"},
                {"uri": "s3://bucket/a.md", "snippet": "監査ログは90日保持します。"},
            ],
        }
        with patch.object(run_batch_eval.requests, "post", return_value=response):
            result = run_batch_eval.call_application(
                "https://example.test/evaluate",
                "監査ログの保持期間は？",
            )

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(result["references"], ["s3://bucket/a.md", "s3://bucket/a.md"])
        self.assertEqual(
            result["retrieved_contexts"],
            ["監査ログは90日保持します。"],
        )

    def test_kb_response_extracts_retrieved_chunk_text(self) -> None:
        response = {
            "citations": [
                {
                    "retrievedReferences": [
                        {"content": {"text": "監査ログは90日保持します。"}},
                        {"content": {"text": "監査ログは90日保持します。"}},
                    ]
                }
            ]
        }

        self.assertEqual(
            ragas_eval._contexts_from_kb_response(response),
            ["監査ログは90日保持します。"],
        )

    def test_kb_response_converts_rows_audio_and_video_to_text(self) -> None:
        response = {
            "citations": [
                {
                    "retrievedReferences": [
                        {
                            "content": {
                                "type": "ROW",
                                "row": [
                                    {
                                        "columnName": "plan",
                                        "columnValue": "Enterprise",
                                        "type": "STRING",
                                    },
                                    {
                                        "columnName": "retention_days",
                                        "columnValue": "90",
                                        "type": "LONG",
                                    },
                                ],
                            }
                        },
                        {
                            "content": {
                                "type": "AUDIO",
                                "audio": {"transcription": "音声の文字起こし"},
                            }
                        },
                        {
                            "content": {
                                "type": "VIDEO",
                                "video": {"summary": "動画の要約"},
                            }
                        },
                    ]
                }
            ]
        }

        self.assertEqual(
            ragas_eval._contexts_from_kb_response(response),
            [
                "plan: Enterprise | retention_days: 90",
                "音声の文字起こし",
                "動画の要約",
            ],
        )

    def test_image_content_fails_instead_of_being_silently_ignored(self) -> None:
        response = {
            "citations": [
                {
                    "retrievedReferences": [
                        {"content": {"type": "IMAGE", "byteContent": "aGVsbG8="}}
                    ]
                }
            ]
        }

        with self.assertRaisesRegex(RuntimeError, "IMAGE"):
            ragas_eval._contexts_from_kb_response(response)

    def test_dataset_keeps_retrieved_contexts(self) -> None:
        dataset = ragas_eval.build_dataset(
            [
                {
                    "id": "case-1",
                    "question": "監査ログの保持期間は？",
                    "answer": "90日です。",
                    "expected_points": ["90日"],
                    "retrieved_contexts": ["監査ログは90日保持します。"],
                }
            ]
        )

        self.assertEqual(
            dataset.samples[0].retrieved_contexts,
            ["監査ログは90日保持します。"],
        )

    def test_missing_context_is_explicit_instead_of_dropped(self) -> None:
        dataset = ragas_eval.build_dataset(
            [
                {
                    "id": "case-1",
                    "question": "監査ログの保持期間は？",
                    "answer": "90日です。",
                    "expected_points": ["90日"],
                    "retrieved_contexts": [],
                }
            ]
        )

        self.assertEqual(
            dataset.samples[0].retrieved_contexts,
            [ragas_eval._NO_RETRIEVED_CONTEXT],
        )

    def test_missing_metric_score_fails_the_run(self) -> None:
        scores = pd.DataFrame(
            {
                "Request Completeness": [1.0, 0.0],
                "Groundedness": [1.0, None],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "Groundedness=1"):
            ragas_eval._require_complete_scores(
                scores,
                ["Request Completeness", "Groundedness"],
            )


@unittest.skipUnless(
    os.environ.get("RUN_AWS_GROUNDEDNESS_TEST") == "1",
    "AWSを使うGroundedness実機テストは明示実行時だけ有効",
)
class GroundednessAwsTest(unittest.TestCase):
    def test_same_answer_scores_higher_with_supporting_context(self) -> None:
        evaluator_llm = LangchainLLMWrapper(
            ragas_eval.ChatBedrock(
                model_id=os.environ.get(
                    "RAGAS_JUDGE_MODEL_ID",
                    "us.anthropic.claude-sonnet-4-6",
                ),
                region_name=os.environ.get("AWS_REGION", "us-east-1"),
            )
        )
        metric = ResponseGroundedness(llm=evaluator_llm, name="Groundedness")
        response = "監査ログの保持期間は90日です。"
        supported = metric.single_turn_score(
            SingleTurnSample(
                response=response,
                retrieved_contexts=["監査ログは90日間保持されます。"],
            )
        )
        unsupported = metric.single_turn_score(
            SingleTurnSample(
                response=response,
                retrieved_contexts=["監査ログの保持期間は7日間です。"],
            )
        )

        self.assertGreater(supported, unsupported)


HAIKU_5_5_IDS = (
    "us.anthropic.claude-haiku-5-5",
    "jp.anthropic.claude-haiku-5-5",
    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.anthropic.claude-haiku-5-5",
)
HAIKU_4_5_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class ModelSettingsTest(unittest.TestCase):
    """Haiku 5.5には温度を送らずKBのプロンプトを渡し、Haiku 4.5は従来どおりにする。"""

    def _kb_config(self, model_arn: str) -> dict:
        client = MagicMock()
        client.retrieve_and_generate.return_value = {"output": {"text": "a"}, "citations": []}
        cases = [{"id": "c1", "question": "q", "expected_points": []}]
        with (
            patch.object(ragas_eval.boto3, "client", return_value=client),
            patch.object(ragas_eval, "read_eval_cases", return_value=cases),
        ):
            ragas_eval.collect_rows_from_kb(
                knowledge_base_id="KB", model_arn=model_arn, workbook=None, region="us-east-1"
            )
        request = client.retrieve_and_generate.call_args.kwargs
        return request["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]

    def test_kb_direct_mode_passes_both_prompts_for_haiku_5_5(self) -> None:
        for model_arn in HAIKU_5_5_IDS:
            with self.subTest(model_arn=model_arn):
                config = self._kb_config(model_arn)
                generation = config["generationConfiguration"]
                orchestration = config["orchestrationConfiguration"]
                for placeholder in ("$query$", "$search_results$", "$output_format_instructions$"):
                    self.assertIn(placeholder, generation["promptTemplate"]["textPromptTemplate"])
                for placeholder in ("$conversation_history$", "$query$", "$output_format_instructions$"):
                    self.assertIn(placeholder, orchestration["promptTemplate"]["textPromptTemplate"])
                self.assertNotIn("temperature", generation["inferenceConfig"]["textInferenceConfig"])

    def test_kb_direct_mode_unchanged_for_haiku_4_5(self) -> None:
        self.assertEqual(
            self._kb_config(HAIKU_4_5_ID),
            {"knowledgeBaseId": "KB", "modelArn": HAIKU_4_5_ID},
        )

    def _judge_inference_config(self, model_id: str) -> dict:
        runtime = MagicMock()
        runtime.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"scores": {}}'}]}}
        }
        with patch.object(run_batch_eval, "build_judge_prompt", return_value="p"):
            run_batch_eval.judge_case(
                runtime, judge_model_id=model_id, case={}, answer="", references=[], rubrics={}
            )
        return runtime.converse.call_args.kwargs["inferenceConfig"]

    def test_judge_omits_temperature_for_haiku_5_5(self) -> None:
        for model_id in HAIKU_5_5_IDS:
            with self.subTest(model_id=model_id):
                self.assertEqual(self._judge_inference_config(model_id), {"maxTokens": 4096})

    def test_judge_keeps_temperature_for_haiku_4_5(self) -> None:
        self.assertEqual(
            self._judge_inference_config(HAIKU_4_5_ID),
            {"maxTokens": 1200, "temperature": 0},
        )


class CollectionsVersionTest(unittest.TestCase):
    """新しい書き方の版で、passを1.0、failを0.0として集計する。"""

    def test_score_row_maps_verdict_and_groundedness(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        import ragas_eval_collections as rc

        completeness, groundedness = MagicMock(), MagicMock()
        completeness.ascore = AsyncMock(return_value=MagicMock(value="pass"))
        groundedness.ascore = AsyncMock(return_value=MagicMock(value=0.75))
        row = {"id": "c1", "question": "質問", "answer": "回答",
               "expected_points": ["要点"], "retrieved_contexts": ["資料"]}
        score = asyncio.run(rc.score_row(row, object(), completeness, groundedness))
        self.assertEqual(score, {"completeness": 1.0, "groundedness": 0.75})
        kwargs = completeness.ascore.call_args.kwargs
        self.assertEqual(kwargs["reference"], "要点")
        self.assertIn("{user_input}", rc.COMPLETENESS_PROMPT)


if __name__ == "__main__":
    unittest.main()
