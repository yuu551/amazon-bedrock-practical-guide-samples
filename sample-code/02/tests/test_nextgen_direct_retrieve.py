from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_DIR))

import nextgen_direct_retrieve as target  # noqa: E402


class EndpointHostTest(unittest.TestCase):
    def test_accepts_https_endpoint(self) -> None:
        self.assertEqual(
            target.endpoint_host("https://example.us-east-1.aoss.amazonaws.com/"),
            "example.us-east-1.aoss.amazonaws.com",
        )

    def test_rejects_endpoint_with_path(self) -> None:
        with self.assertRaises(ValueError):
            target.endpoint_host("https://example.aoss.amazonaws.com/index")


class QueryTest(unittest.TestCase):
    def test_semantic_query_uses_candidate_count(self) -> None:
        body = target.semantic_query(
            [0.1, 0.2],
            "vector",
            "text",
            "metadata",
            top_k=4,
            candidate_count=20,
        )

        self.assertEqual(body["size"], 4)
        self.assertEqual(body["query"]["knn"]["vector"]["k"], 20)
        self.assertEqual(body["_source"], ["text", "metadata"])

    def test_hybrid_query_keeps_keyword_then_vector_order(self) -> None:
        body = target.hybrid_query(
            "監査ログ",
            [0.1, 0.2],
            "vector",
            "text",
            "metadata",
            top_k=3,
            candidate_count=10,
        )
        queries = body["query"]["hybrid"]["queries"]

        self.assertEqual(queries[0], {"match": {"text": {"query": "監査ログ"}}})
        self.assertEqual(queries[1]["knn"]["vector"]["k"], 10)

    def test_pipeline_weights_match_query_order(self) -> None:
        body = target.search_pipeline_body(0.3)
        weights = body["phase_results_processors"][0]["normalization-processor"][
            "combination"
        ]["parameters"]["weights"]

        self.assertEqual(weights, [0.3, 0.7])

    def test_pipeline_rejects_single_search_weight(self) -> None:
        with self.assertRaises(ValueError):
            target.search_pipeline_body(1.0)


class NormalizeHitsTest(unittest.TestCase):
    def test_parses_metadata_json_without_returning_vector(self) -> None:
        response = {
            "hits": {
                "hits": [
                    {
                        "_id": "doc-1",
                        "_score": 0.75,
                        "_source": {
                            "text": "監査ログの保存期間は7年間です。",
                            "metadata": json.dumps({"source": "s3://example/doc.md"}),
                            "vector": [0.1, 0.2],
                        },
                    }
                ]
            }
        }

        results = target.normalize_hits(response, "text", "metadata")

        self.assertEqual(
            results,
            [
                {
                    "id": "doc-1",
                    "score": 0.75,
                    "text": "監査ログの保存期間は7年間です。",
                    "metadata": {"source": "s3://example/doc.md"},
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
