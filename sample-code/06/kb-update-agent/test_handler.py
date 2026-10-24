"""品質ゲートの判定と副作用の境界を確認するテスト。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import handler
import tools


SYNC_OK = {"status": "COMPLETE", "statistics": {"failed": 0}}
PAYLOAD = {
    "knowledge_base_id": "kb-id",
    "data_source_id": "ds-id",
    "bucket": "bucket-name",
    "api_url": "kb",
    "judge_model_id": "judge-model",
    "model_id": "answer-model",
    "eval_cases": "eval_cases.json",
    "baseline_path": "baseline.json",
    "changed_document_keys": ["sample-data/policies/new.md"],
    "object_versions": {"sample-data/policies/new.md": ["version-new"]},
}


def _five_case_result(*, first_score: float) -> dict:
    metrics = ("correctness", "completeness", "groundedness")
    cases = []
    for index in range(1, 6):
        score = first_score if index == 1 else 1.0
        cases.append(
            {
                "case_id": f"P-{index:03d}",
                "scores": {metric: score for metric in metrics},
                "error": None,
            }
        )
    average = round((first_score + 4.0) / 5.0, 3)
    return {
        "case_count": 5,
        "averages": {metric: average for metric in metrics},
        "cases": cases,
    }


class HandlerSafetyTest(unittest.TestCase):
    def test_review_revision_round_trip_uses_shared_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_path = str(Path(temp_dir) / "baseline.json")
            tools.record_review_revision(baseline_path, "session-1")
            self.assertEqual(tools.load_review_revision(baseline_path), "session-1")
            tools.record_review_revision(baseline_path, "session-2")
            self.assertEqual(tools.load_review_revision(baseline_path), "session-2")

    def _run_process(self, current: dict, baseline: dict) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as temp_dir:
            pending_path = Path(temp_dir) / "pending.json"
            with (
                patch.object(handler, "PENDING_PATH", pending_path),
                patch.object(
                    handler, "start_and_wait_for_sync", return_value=SYNC_OK
                ),
                patch.object(handler, "run_evaluation", return_value=current),
                patch.object(handler, "load_baseline", return_value=baseline),
                patch.object(
                    handler,
                    "record_review_revision",
                    return_value={"revision": "s" * 40},
                ),
                patch.object(handler, "_create_analysis", return_value="原因仮説"),
                patch.object(handler, "save_baseline_impl") as save_baseline,
                patch.object(handler, "rollback_documents_impl") as rollback,
                patch.object(
                    handler,
                    "send_notification_impl",
                    return_value={"sent": True},
                ) as notify,
            ):
                result = handler._process(
                    PAYLOAD,
                    SimpleNamespace(session_id="s" * 40),
                )

            calls = {
                "pending_exists": pending_path.exists(),
                "save_count": save_baseline.call_count,
                "rollback_count": rollback.call_count,
                "rollback_args": rollback.call_args.kwargs if rollback.called else None,
                "notification_count": notify.call_count,
                "notification": notify.call_args.kwargs,
            }
        return result, calls

    def test_minor_drop_saves_pending_state_without_automatic_side_effect(self) -> None:
        current = _five_case_result(first_score=0.5)
        baseline = {"exists": True, **_five_case_result(first_score=1.0)}
        result, calls = self._run_process(current, baseline)

        self.assertEqual(result["decision"], "pending_review")
        self.assertEqual(
            result["gate"]["average_drops"],
            {
                "correctness": 0.1,
                "completeness": 0.1,
                "groundedness": 0.1,
            },
        )
        self.assertTrue(calls["pending_exists"])
        self.assertIsNone(result["action_result"])
        self.assertEqual(calls["save_count"], 0)
        self.assertEqual(calls["rollback_count"], 0)
        self.assertEqual(calls["notification_count"], 1)
        notification = calls["notification"]
        self.assertEqual(
            notification["subject"],
            "【レビュー依頼】Knowledge Base 更新の確認が必要です",
        )
        self.assertIn("コード側の判定: pending_review", notification["message"])
        self.assertIn("correctness': 0.1", notification["message"])
        self.assertIn("新規0点: []", notification["message"])
        self.assertIn("uv run decide.py approve", notification["message"])
        self.assertIn("uv run decide.py rollback", notification["message"])

    def test_unchanged_scores_save_baseline_without_rollback(self) -> None:
        current = _five_case_result(first_score=1.0)
        baseline = {"exists": True, **_five_case_result(first_score=1.0)}
        result, calls = self._run_process(current, baseline)

        self.assertEqual(result["decision"], "approved")
        self.assertFalse(calls["pending_exists"])
        self.assertEqual(calls["save_count"], 1)
        self.assertEqual(calls["rollback_count"], 0)
        self.assertEqual(calls["notification_count"], 1)
        self.assertEqual(
            calls["notification"]["subject"],
            "【自動承認】Knowledge Base 更新を承認しました",
        )

    def test_threshold_drop_rolls_back_without_saving_baseline(self) -> None:
        current = _five_case_result(first_score=0.0)
        baseline = {"exists": True, **_five_case_result(first_score=1.0)}
        result, calls = self._run_process(current, baseline)

        self.assertEqual(result["decision"], "rolled_back")
        self.assertFalse(calls["pending_exists"])
        self.assertEqual(calls["save_count"], 0)
        self.assertEqual(calls["rollback_count"], 1)
        self.assertEqual(
            calls["rollback_args"]["version_ids_json"],
            '{"sample-data/policies/new.md": ["version-new"]}',
        )
        self.assertEqual(calls["notification_count"], 1)
        self.assertEqual(
            calls["notification"]["subject"],
            "【自動ロールバック】Knowledge Base 更新を差し戻しました",
        )

    def test_stale_manual_decisions_do_not_change_shared_state(self) -> None:
        pending = {
            "session_id": "old-session",
            "baseline_path": "s3://baseline-bucket/baseline.json",
            "review_revision": "old-session",
        }
        for action in ("approve", "rollback"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp_dir:
                pending_path = Path(temp_dir) / "pending.json"
                pending_path.write_text(
                    json.dumps(pending), encoding="utf-8"
                )
                with (
                    patch.object(handler, "PENDING_PATH", pending_path),
                    patch.object(
                        handler, "load_review_revision", return_value="new-session"
                    ),
                    patch.object(handler, "save_baseline_impl") as save_baseline,
                    patch.object(handler, "rollback_documents_impl") as rollback,
                    patch.object(handler, "send_notification_impl") as notify,
                ):
                    result = handler.handler(
                        {"action": action, "session_id": "old-session"}
                    )

                self.assertEqual(result["error"], "stale_pending_state")
                self.assertEqual(result["current_revision"], "new-session")
                self.assertTrue(pending_path.exists())
                save_baseline.assert_not_called()
                rollback.assert_not_called()
                notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
