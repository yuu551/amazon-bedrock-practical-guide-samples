"""Strands Evalsの統合レポート整形を確認するテスト。"""

from types import SimpleNamespace
import unittest

import strands_eval


class StrandsReportTest(unittest.TestCase):
    def test_flattened_report_is_grouped_by_evaluator(self) -> None:
        report = SimpleNamespace(
            cases=[
                {"name": "case-a", "evaluator": "OutputEvaluator"},
                {"name": "case-b", "evaluator": "OutputEvaluator"},
                {"name": "case-a", "evaluator": "ToolSelectionAccuracyEvaluator"},
            ],
            scores=[1.0, 0.5, 1.0],
            test_passes=[True, False, True],
            reasons=["ok", "missing", "ok"],
        )

        summary = strands_eval.summarize_report(report)

        self.assertEqual([row["evaluator"] for row in summary], [
            "OutputEvaluator",
            "ToolSelectionAccuracyEvaluator",
        ])
        self.assertEqual(summary[0]["overall_score"], 0.75)
        self.assertEqual(summary[0]["cases"][1], {
            "name": "case-b",
            "score": 0.5,
            "pass": False,
            "reason": "missing",
        })


if __name__ == "__main__":
    unittest.main()
