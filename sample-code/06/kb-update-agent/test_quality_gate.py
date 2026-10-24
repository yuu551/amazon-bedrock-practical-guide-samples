"""決定論的な品質ゲートの単体テスト。"""
from __future__ import annotations

import unittest

from quality_gate import evaluate_quality_gate


METRICS = ("correctness", "completeness", "groundedness")


def _result(scores_by_case: dict[str, tuple[float, float, float]]) -> dict:
    cases = []
    for case_id, values in scores_by_case.items():
        cases.append(
            {
                "case_id": case_id,
                "scores": dict(zip(METRICS, values, strict=True)),
                "error": None,
            }
        )
    averages = {
        metric: round(sum(case["scores"][metric] for case in cases) / len(cases), 3)
        for metric in METRICS
    }
    return {"case_count": len(cases), "averages": averages, "cases": cases}


def _baseline(scores_by_case: dict[str, tuple[float, float, float]]) -> dict:
    return {"exists": True, **_result(scores_by_case)}


SYNC_OK = {"status": "COMPLETE", "statistics": {"failed": 0}}


class QualityGateTest(unittest.TestCase):
    def test_first_run_is_approved(self) -> None:
        current = _result({"P-001": (1.0, 1.0, 1.0)})
        gate = evaluate_quality_gate(current, {"exists": False}, sync_result=SYNC_OK)
        self.assertEqual(gate["decision"], "approve")

    def test_unchanged_scores_are_approved(self) -> None:
        current = _result({"P-001": (1.0, 1.0, 1.0)})
        gate = evaluate_quality_gate(
            current,
            _baseline({"P-001": (1.0, 1.0, 1.0)}),
            sync_result=SYNC_OK,
        )
        self.assertEqual(gate["decision"], "approve")

    def test_average_drop_at_threshold_requests_rollback(self) -> None:
        current = _result({"P-001": (0.8, 1.0, 1.0)})
        gate = evaluate_quality_gate(
            current,
            _baseline({"P-001": (1.0, 1.0, 1.0)}),
            sync_result=SYNC_OK,
        )
        self.assertEqual(gate["decision"], "rollback")

    def test_new_zero_requests_rollback(self) -> None:
        current = _result(
            {"P-001": (0.0, 1.0, 1.0), "P-002": (1.0, 1.0, 1.0)}
        )
        baseline = _baseline(
            {"P-001": (0.5, 1.0, 1.0), "P-002": (1.0, 1.0, 1.0)}
        )
        gate = evaluate_quality_gate(current, baseline, sync_result=SYNC_OK)
        self.assertEqual(gate["decision"], "rollback")
        self.assertEqual(gate["new_zero_scores"], ["P-001.correctness"])

    def test_one_of_five_cases_dropping_to_half_requires_review(self) -> None:
        baseline_scores = {f"P-{i:03d}": (1.0, 1.0, 1.0) for i in range(1, 6)}
        current_scores = dict(baseline_scores)
        current_scores["P-001"] = (0.5, 0.5, 0.5)
        gate = evaluate_quality_gate(
            _result(current_scores), _baseline(baseline_scores), sync_result=SYNC_OK
        )
        self.assertEqual(gate["decision"], "pending_review")
        self.assertEqual(
            gate["average_drops"],
            {"correctness": 0.1, "completeness": 0.1, "groundedness": 0.1},
        )
        self.assertEqual(gate["new_zero_scores"], [])

    def test_evaluation_error_never_auto_approves(self) -> None:
        current = _result({"P-001": (0.0, 0.0, 0.0)})
        current["cases"][0]["error"] = "timeout"
        gate = evaluate_quality_gate(current, {"exists": False}, sync_result=SYNC_OK)
        self.assertEqual(gate["decision"], "pending_review")

    def test_partial_sync_never_runs_an_automatic_side_effect(self) -> None:
        current = _result({"P-001": (1.0, 1.0, 1.0)})
        sync = {"status": "COMPLETE", "statistics": {"failed": 1}}
        gate = evaluate_quality_gate(current, {"exists": False}, sync_result=sync)
        self.assertEqual(gate["decision"], "pending_review")

    def test_changed_case_set_requires_review(self) -> None:
        current = _result({"P-001": (1.0, 1.0, 1.0), "P-002": (1.0, 1.0, 1.0)})
        baseline = _baseline({"P-001": (1.0, 1.0, 1.0)})
        gate = evaluate_quality_gate(current, baseline, sync_result=SYNC_OK)
        self.assertEqual(gate["decision"], "pending_review")

    def test_out_of_range_score_requires_review(self) -> None:
        current = _result({"P-001": (1.5, 1.0, 1.0)})
        gate = evaluate_quality_gate(current, {"exists": False}, sync_result=SYNC_OK)
        self.assertEqual(gate["decision"], "pending_review")


if __name__ == "__main__":
    unittest.main()
