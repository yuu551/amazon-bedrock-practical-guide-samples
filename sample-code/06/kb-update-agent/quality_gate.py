"""Knowledge Base 更新の可否を決める決定論的な品質ゲート。"""
from __future__ import annotations

import math
from typing import Any

METRICS = ("correctness", "completeness", "groundedness")
ROLLBACK_THRESHOLD = 0.2
_EPSILON = 1e-9


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _score(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _validation_errors(
    eval_result: dict[str, Any],
    baseline: dict[str, Any],
    sync_result: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []

    if sync_result is not None:
        if sync_result.get("status") != "COMPLETE":
            errors.append(f"同期状態がCOMPLETEではありません: {sync_result.get('status')}")
        statistics = sync_result.get("statistics")
        if not isinstance(statistics, dict):
            statistics = {}
        failed = statistics.get("failed", 0)
        if failed:
            errors.append(f"同期に失敗した文書があります: {failed}件")

    current_cases = eval_result.get("cases")
    if not isinstance(current_cases, list) or not current_cases:
        errors.append("評価ケースがありません")
        current_cases = []
    current_ids: list[str] = []
    for case in current_cases:
        if not isinstance(case, dict):
            errors.append("評価ケースの形式が不正です")
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append("評価ケースIDが不正です")
            case_id = "unknown"
        else:
            current_ids.append(case_id)
        if case.get("error"):
            errors.append(f"評価エラー: {case_id}")
        scores = case.get("scores")
        if not isinstance(scores, dict):
            scores = {}
        for metric in METRICS:
            if _score(scores.get(metric)) is None:
                errors.append(f"個別スコアが不正です: {case_id}.{metric}")
    if len(current_ids) != len(set(current_ids)):
        errors.append("評価ケースIDが重複しています")

    current_averages = eval_result.get("averages")
    if not isinstance(current_averages, dict):
        current_averages = {}
    for metric in METRICS:
        if _score(current_averages.get(metric)) is None:
            errors.append(f"評価平均が不正です: {metric}")

    if baseline.get("exists"):
        baseline_averages = baseline.get("averages")
        if not isinstance(baseline_averages, dict):
            baseline_averages = {}
        for metric in METRICS:
            if _score(baseline_averages.get(metric)) is None:
                errors.append(f"ベースライン平均が不正です: {metric}")
        baseline_cases = baseline.get("cases")
        if not isinstance(baseline_cases, list) or not baseline_cases:
            errors.append("ベースラインの評価ケースがありません")
            baseline_cases = []
        baseline_ids: list[str] = []
        for case in baseline_cases:
            if not isinstance(case, dict):
                errors.append("ベースラインケースの形式が不正です")
                continue
            case_id = case.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                errors.append("ベースラインの評価ケースIDが不正です")
                case_id = "unknown"
            else:
                baseline_ids.append(case_id)
            scores = case.get("scores")
            if not isinstance(scores, dict):
                scores = {}
            for metric in METRICS:
                if _score(scores.get(metric)) is None:
                    errors.append(
                        f"ベースラインの個別スコアが不正です: {case_id}.{metric}"
                    )
        if len(baseline_ids) != len(set(baseline_ids)):
            errors.append("ベースラインの評価ケースIDが重複しています")
        if set(current_ids) != set(baseline_ids):
            errors.append("評価ケースの構成がベースラインと一致しません")

    return errors


def evaluate_quality_gate(
    eval_result: dict[str, Any],
    baseline: dict[str, Any],
    *,
    sync_result: dict[str, Any] | None = None,
    rollback_threshold: float = ROLLBACK_THRESHOLD,
) -> dict[str, Any]:
    """評価結果を承認、ロールバック、レビュー依頼のいずれかに分類する。

    LLMの出力は受け取らない。入力不備、評価エラー、同期の部分失敗は
    自動処理せずレビュー依頼へ倒す。
    """
    errors = _validation_errors(eval_result, baseline, sync_result)
    if errors:
        return {
            "decision": "pending_review",
            "reasons": errors,
            "average_drops": {},
            "new_zero_scores": [],
        }

    if not baseline.get("exists"):
        return {
            "decision": "approve",
            "reasons": ["初回実行のため、現在の評価結果をベースラインとして保存します"],
            "average_drops": {},
            "new_zero_scores": [],
        }

    current_averages = eval_result["averages"]
    baseline_averages = baseline["averages"]
    average_drops = {
        metric: round(
            float(baseline_averages[metric]) - float(current_averages[metric]), 3
        )
        for metric in METRICS
    }

    baseline_cases = {
        case.get("case_id"): case
        for case in baseline.get("cases", [])
        if case.get("case_id") is not None
    }
    new_zero_scores: list[str] = []
    for case in eval_result.get("cases", []):
        case_id = case.get("case_id", "unknown")
        previous = baseline_cases.get(case_id)
        for metric in METRICS:
            current_score = _score(case.get("scores", {}).get(metric))
            if current_score is None:
                return {
                    "decision": "pending_review",
                    "reasons": [f"個別スコアが不正です: {case_id}.{metric}"],
                    "average_drops": average_drops,
                    "new_zero_scores": [],
                }
            previous_score = (
                _score(previous.get("scores", {}).get(metric)) if previous else None
            )
            if current_score == 0.0 and (previous_score is None or previous_score > 0.0):
                new_zero_scores.append(f"{case_id}.{metric}")

    dropped_metrics = [
        metric
        for metric, drop in average_drops.items()
        if drop + _EPSILON >= rollback_threshold
    ]
    if dropped_metrics or new_zero_scores:
        reasons: list[str] = []
        if dropped_metrics:
            reasons.append(
                "平均が閾値以上低下: "
                + ", ".join(
                    f"{metric}={average_drops[metric]:.3f}" for metric in dropped_metrics
                )
            )
        if new_zero_scores:
            reasons.append("新規0点: " + ", ".join(new_zero_scores))
        return {
            "decision": "rollback",
            "reasons": reasons,
            "average_drops": average_drops,
            "new_zero_scores": new_zero_scores,
        }

    if all(drop <= _EPSILON for drop in average_drops.values()):
        return {
            "decision": "approve",
            "reasons": ["全指標の平均がベースライン以上で、新規0点もありません"],
            "average_drops": average_drops,
            "new_zero_scores": [],
        }

    return {
        "decision": "pending_review",
        "reasons": ["平均低下はありますが、ロールバック閾値未満です"],
        "average_drops": average_drops,
        "new_zero_scores": [],
    }
