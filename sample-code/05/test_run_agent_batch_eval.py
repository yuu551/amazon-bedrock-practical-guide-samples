from __future__ import annotations

from dataclasses import dataclass

from run_agent_batch_eval import (
    build_eval_case,
    primary_failure_type,
    render_mcp_result,
    report_to_results,
    rubric_text,
)


def _raw_case() -> dict:
    return {
        "id": "case-a",
        "question": "question",
        "expected_points": ["point-1", "point-2"],
        "expected_trajectory": ["search_support_kb"],
        "forbidden_tools": ["create_jira_ticket"],
        "category": "silent-failure-overcall",
    }


def test_build_eval_case_separates_goal_from_trajectory() -> None:
    case = build_eval_case(_raw_case())

    assert case.expected_output == "point-1 / point-2"
    assert case.expected_trajectory == ["search_support_kb"]
    assert "point-1" in case.expected_assertion
    assert "create_jira_ticket" in case.expected_assertion
    assert "search_support_kb" not in case.expected_assertion


def test_rubric_text_uses_excel_business_criteria() -> None:
    text = rubric_text(
        "OutputAccuracy",
        {
            "OutputAccuracy": {
                "満点": "all",
                "部分点": "some",
                "不合格": "none",
            }
        },
    )

    assert "1.0: all" in text
    assert "0.5: some" in text
    assert "0.0: none" in text


def test_render_mcp_result_prefers_structured_content() -> None:
    result = render_mcp_result(
        {
            "status": "success",
            "content": [{"text": "fallback"}],
            "structuredContent": {"count": 1},
        }
    )

    assert '"count": 1' in result
    assert "fallback" not in result


@dataclass
class _EvaluatedCase:
    name: str
    evaluator: str

    def model_dump(self) -> dict:
        return {"name": self.name, "evaluator": self.evaluator}


@dataclass
class _Report:
    cases: list[_EvaluatedCase]
    scores: list[float]
    reasons: list[str]


def test_report_to_results_maps_four_sdk_evaluators_to_excel_row() -> None:
    metric_names = [
        "OutputAccuracy",
        "ToolSelection",
        "Trajectory",
        "GoalSuccess",
    ]
    report = _Report(
        cases=[_EvaluatedCase("case-a", name) for name in metric_names],
        scores=[1.0, 0.5, 0.5, 0.0],
        reasons=[f"reason-{name}" for name in metric_names],
    )

    results = report_to_results(
        report=report,
        raw_cases=[_raw_case()],
        execution_records={
            "case-a": {
                "answer": "answer",
                "trajectory": ["search_support_kb", "create_jira_ticket"],
                "latency_ms": 12.3,
            }
        },
    )

    assert results[0]["scores"] == {
        "OutputAccuracy": 1.0,
        "ToolSelection": 0.5,
        "Trajectory": 0.5,
        "GoalSuccess": 0.0,
    }
    assert results[0]["reason_by_metric"]["Trajectory"] == "reason-Trajectory"
    assert results[0]["unexpected_call_count"] == 1
    assert results[0]["primary_failure_type"] == "silent-failure-overcall"


def test_report_to_results_records_task_errors_without_execution_record() -> None:
    metric_names = [
        "OutputAccuracy",
        "ToolSelection",
        "Trajectory",
        "GoalSuccess",
    ]
    report = _Report(
        cases=[_EvaluatedCase("case-a", name) for name in metric_names],
        scores=[0.0, 0.0, 0.0, 0.0],
        reasons=["An error occurred: timeout"] * len(metric_names),
    )

    results = report_to_results(
        report=report,
        raw_cases=[_raw_case()],
        execution_records={},
    )

    assert results[0]["answer"] == ""
    assert results[0]["trajectory"] == []
    assert results[0]["error"] == "An error occurred: timeout"


def test_primary_failure_type_marks_output_only_and_full_pass() -> None:
    full = {
        "OutputAccuracy": 1.0,
        "ToolSelection": 1.0,
        "Trajectory": 1.0,
        "GoalSuccess": 1.0,
    }
    output_only = dict(full, OutputAccuracy=0.5)

    assert primary_failure_type("tool-selection-miss", full) == "none"
    assert primary_failure_type("tool-selection-miss", output_only) == "output-only"
