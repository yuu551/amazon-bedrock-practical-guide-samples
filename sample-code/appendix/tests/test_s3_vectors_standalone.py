from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch


SAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_DIR))

import s3_vectors_standalone as target  # noqa: E402


class CharacterNgramsTest(unittest.TestCase):
    def test_normalizes_width_case_and_punctuation(self) -> None:
        self.assertEqual(
            target.character_ngrams("ＡPI-ログ", size=3),
            ["api", "piロ", "iログ"],
        )

    def test_rejects_non_positive_size(self) -> None:
        with self.assertRaises(ValueError):
            target.character_ngrams("監査ログ", size=0)


class KeywordSearchTest(unittest.TestCase):
    def test_ranks_audit_log_document_first(self) -> None:
        results = target.keyword_search("Enterpriseプランの監査ログは何日保存されますか")

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["key"], "faq-audit-log-retention")


class ReciprocalRankFusionTest(unittest.TestCase):
    def test_merges_independent_rankings_and_keeps_single_route_results(self) -> None:
        semantic_results = [
            {
                "key": "semantic-only",
                "distance": 0.1,
                "source_text": "意味検索だけの文書",
                "metadata": {},
            },
            {
                "key": "both",
                "distance": 0.2,
                "source_text": "両方の検索に出る文書",
                "metadata": {},
            },
        ]
        keyword_results = [
            {
                "key": "both",
                "keyword_score": 2.0,
                "source_text": "両方の検索に出る文書",
                "metadata": {},
            },
            {
                "key": "keyword-only",
                "keyword_score": 1.0,
                "source_text": "キーワード検索だけの文書",
                "metadata": {},
            },
        ]

        results = target.reciprocal_rank_fusion(
            semantic_results,
            keyword_results,
            top_k=3,
        )

        self.assertEqual(results[0]["key"], "both")
        self.assertEqual(
            {result["key"] for result in results},
            {"semantic-only", "both", "keyword-only"},
        )
        keyword_only = next(
            result for result in results if result["key"] == "keyword-only"
        )
        self.assertIsNone(keyword_only["semantic_rank"])
        self.assertEqual(keyword_only["keyword_rank"], 2)

    def test_rejects_invalid_parameters(self) -> None:
        with self.assertRaises(ValueError):
            target.reciprocal_rank_fusion([], [], top_k=0)
        with self.assertRaises(ValueError):
            target.reciprocal_rank_fusion([], [], rrf_k=0)


class HybridSearchTest(unittest.TestCase):
    @patch.object(target, "query")
    def test_queries_all_sample_documents_before_fusion(self, query_mock) -> None:
        query_mock.return_value = [
            {
                "key": "faq-audit-log-retention",
                "distance": 0.1,
                "source_text": target.SAMPLE_DOCUMENTS[3]["text"],
                "metadata": target.SAMPLE_DOCUMENTS[3]["metadata"],
            }
        ]

        results = target.hybrid_search("監査ログの保持期間", top_k=1)

        query_mock.assert_called_once_with(
            "監査ログの保持期間",
            top_k=len(target.SAMPLE_DOCUMENTS),
        )
        self.assertEqual(results[0]["key"], "faq-audit-log-retention")
        self.assertEqual(results[0]["semantic_rank"], 1)
        self.assertEqual(results[0]["keyword_rank"], 1)


class RagModelSettingsTest(unittest.TestCase):
    """Haiku 5.5には温度を送らず、Haiku 4.5は従来どおり温度を送る。"""

    def _inference_config(self, model_id: str) -> dict:
        runtime = unittest.mock.MagicMock()
        runtime.converse.return_value = {
            "output": {"message": {"content": [{"reasoningContent": {}}, {"text": "回答"}]}}
        }
        with (
            patch.object(target, "query", return_value=[{"key": "k", "source_text": "t"}]),
            patch.object(target, "print_query_results"),
            patch.object(target, "get_bedrock_runtime_client", return_value=runtime),
            patch("builtins.print"),
        ):
            target.rag("q", model_id=model_id)
        return runtime.converse.call_args.kwargs["inferenceConfig"]

    def test_haiku_5_5_omits_temperature(self) -> None:
        for model_id in (
            "us.anthropic.claude-haiku-5-5",
            "jp.anthropic.claude-haiku-5-5",
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.anthropic.claude-haiku-5-5",
        ):
            with self.subTest(model_id=model_id):
                self.assertEqual(self._inference_config(model_id), {"maxTokens": 4096})

    def test_haiku_4_5_keeps_temperature(self) -> None:
        self.assertEqual(
            self._inference_config("us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            {"maxTokens": 600, "temperature": 0.1},
        )


if __name__ == "__main__":
    unittest.main()
