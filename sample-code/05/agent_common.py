"""Helper functions for agent evaluation (Chapter 5, Section 5.3).

Reads / writes ``agent_eval_workbook.xlsx`` (sheets: 評価ケース, 採点基準,
評価結果) and provides utilities to extract a tool-call trajectory from a
Strands ``Agent`` and to compare it with the expected trajectory defined in
the workbook.

Kept independent from ``common.py`` so that RAG and agent evaluation tooling
can evolve separately.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from common import (
    _open_or_create_workbook,
    auto_fit_columns,
    join_list_cell,
    split_list_cell,
    style_header_row,
)


# ---------------------------------------------------------------------------
# 評価ワークブック I/O  (シート: 評価ケース, 採点基準, 評価結果)
# ---------------------------------------------------------------------------

def _read_sheet_as_dicts(path: Path, sheet_name: str) -> list[dict[str, Any]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if len(rows) < 2:
        return []
    headers = [str(h).strip() for h in rows[0] if h is not None]
    entries: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not any(cell is not None for cell in row):
            continue
        entries.append(dict(zip(headers, row)))
    return entries


def read_agent_eval_cases(
    path: Path, sheet_name: str = "評価ケース"
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for raw in _read_sheet_as_dicts(path, sheet_name):
        cases.append(
            {
                "id": str(raw.get("ケースID", "")),
                "question": str(raw.get("質問", "")),
                "expected_points": split_list_cell(raw.get("期待要点")),
                "expected_trajectory": split_list_cell(raw.get("期待軌跡")),
                "forbidden_tools": split_list_cell(raw.get("不要ツール")),
                "category": str(raw.get("カテゴリ", "")),
            }
        )
    return cases


def read_agent_rubrics(
    path: Path, sheet_name: str = "採点基準"
) -> dict[str, Any]:
    rubrics: dict[str, Any] = {}
    for raw in _read_sheet_as_dicts(path, sheet_name):
        metric = str(raw.get("評価項目", "")).strip()
        if not metric:
            continue
        rubrics[metric] = {
            "満点": str(raw.get("満点", "")),
            "部分点": str(raw.get("部分点", "")),
            "不合格": str(raw.get("不合格", "")),
        }
    return rubrics


_RESULTS_HEADERS = [
    "ケースID",
    "質問",
    "回答",
    "実際の軌跡",
    "不要呼び出し数",
    "OutputAccuracy",
    "ToolSelection",
    "Trajectory",
    "GoalSuccess",
    "主な失敗類型",
    "採点理由",
    "応答時間(ms)",
    "エラー",
]
_SCORE_FMT = "0.00"
_METRIC_NAMES = ["OutputAccuracy", "ToolSelection", "Trajectory", "GoalSuccess"]


def write_agent_results_sheet(
    path: Path,
    results: list[dict[str, Any]],
    sheet_name: str = "評価結果",
) -> None:
    wb = _open_or_create_workbook(path)
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)
    style_header_row(ws, _RESULTS_HEADERS)

    for r in results:
        scores = r.get("scores", {})
        reasons = r.get("reason_by_metric", {})
        reason_parts = [f"{k}: {v}" for k, v in reasons.items() if v]
        reason_text = "\n".join(reason_parts) if reason_parts else r.get("overall_summary", "")
        ws.append(
            [
                r.get("id", ""),
                r.get("question", ""),
                r.get("answer", ""),
                join_list_cell(r.get("trajectory", [])),
                r.get("unexpected_call_count", 0),
                scores.get("OutputAccuracy", 0.0),
                scores.get("ToolSelection", 0.0),
                scores.get("Trajectory", 0.0),
                scores.get("GoalSuccess", 0.0),
                r.get("primary_failure_type", ""),
                reason_text,
                r.get("latency_ms", 0),
                r.get("error", ""),
            ]
        )

    score_col_start = _RESULTS_HEADERS.index("OutputAccuracy") + 1
    score_col_end = _RESULTS_HEADERS.index("GoalSuccess") + 1
    for row in ws.iter_rows(
        min_row=2, min_col=score_col_start, max_col=score_col_end
    ):
        for cell in row:
            cell.number_format = _SCORE_FMT

    auto_fit_columns(ws)
    wb.save(path)


# ---------------------------------------------------------------------------
# Trajectory helpers
# ---------------------------------------------------------------------------

def _strip_gateway_prefix(name: str) -> str:
    """Gateway tools have a ``<server>___<tool>`` prefix; keep only the suffix."""
    return name.split("___", 1)[1] if "___" in name else name


def extract_trajectory_from_strands_agent(agent: Any) -> list[str]:
    """Return the tool-call sequence from a Strands ``Agent`` after a run."""
    trajectory: list[str] = []
    for msg in getattr(agent, "messages", []):
        if not isinstance(msg, dict):
            continue
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and "toolUse" in block:
                name = block["toolUse"].get("name", "")
                if name:
                    trajectory.append(_strip_gateway_prefix(name))
    return trajectory


def count_unexpected_calls(
    actual: list[str],
    expected: list[str],
    forbidden: list[str],
) -> int:
    """Count tool calls that are either explicitly forbidden or outside the
    expected trajectory.

    A forbidden tool counts every time it appears. Non-forbidden tools that are
    not in ``expected`` count once per call.
    """
    expected_set = set(expected)
    forbidden_set = set(forbidden)
    unexpected = 0
    for name in actual:
        if name in forbidden_set:
            unexpected += 1
        elif name not in expected_set:
            unexpected += 1
    return unexpected
