#!/usr/bin/env python3
"""Extract knowledge update candidates from feedback logs.

Reads feedback entries from an Excel workbook, asks an LLM to identify
document update candidates via Bedrock Converse API tool use (Structured
Output), and writes results back to the same workbook's '更新候補' sheet.

Tool useでJSONスキーマを強制することで、テキストからのJSON抽出失敗や
スキーマ崩れを避け、後段のExcel記録処理を安定させます。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import boto3

from common import (
    converse_inference_config,
    markdown_table,
    read_feedback_log,
    write_candidates_sheet,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract knowledge update candidates from feedback logs."
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("data/feedback_workbook.xlsx"),
        help="Excel workbook with 'フィードバック' sheet.",
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("FEEDBACK_MODEL_ID"),
        help="Model ID for candidate extraction.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for Markdown summary (optional).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Candidate extraction (Structured Output via Converse tool use)
# ---------------------------------------------------------------------------

# モデルにこのスキーマで返させることで、JSON抽出のテキスト処理を不要にする
FEEDBACK_CANDIDATE_TOOL: dict[str, Any] = {
    "toolSpec": {
        "name": "feedback_candidate",
        "description": (
            "問い合わせ失敗ログから、ナレッジの更新候補を1件返す。"
            "添えられたフィードバック1件に対応する候補を必ず生成すること。"
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "update", "ignore"],
                        "description": (
                            "addは新規追加、updateは既存ドキュメントの修正、"
                            "ignoreは更新不要"
                        ),
                    },
                    "target_document": {
                        "type": "string",
                        "description": (
                            "更新対象のドキュメント識別子（S3キー、FAQ見出し、"
                            "Confluenceページ名など）。actionがignoreの場合は空文字でよい。"
                        ),
                    },
                    "missing_points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "自動回答で欠落していた要点を箇条書きで列挙",
                    },
                    "draft_change": {
                        "type": "string",
                        "description": (
                            "ドキュメントへ追記・修正する変更案のドラフト。"
                            "Markdownで記述してよい。"
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "この更新が必要な理由",
                    },
                    "retest_case_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "更新後に再評価すべき評価ケースID",
                    },
                },
                "required": ["action"],
            }
        },
    }
}


def build_candidate_prompt(feedback: dict[str, Any]) -> str:
    return f"""
あなたはナレッジ更新候補を抽出する編集補助です。
次の問い合わせ失敗ログを読み、`feedback_candidate`ツールで更新候補を1件返してください。

# 問い合わせ
{feedback["question"]}

# 自動回答
{feedback["agent_answer"]}

# 人手修正
{feedback["operator_fix"]}

# 補足コメント
{feedback["comment"]}

# 関連ケースID
{feedback.get("related_case_ids", [])}
""".strip()


def build_candidate(
    runtime_client: Any,
    *,
    model_id: str,
    feedback: dict[str, Any],
) -> dict[str, Any]:
    response = runtime_client.converse(
        modelId=model_id,
        messages=[
            {"role": "user", "content": [{"text": build_candidate_prompt(feedback)}]}
        ],
        toolConfig={
            "tools": [FEEDBACK_CANDIDATE_TOOL],
            "toolChoice": {"tool": {"name": "feedback_candidate"}},
        },
        inferenceConfig=converse_inference_config(model_id, 1000, 0),
    )
    for block in response["output"]["message"]["content"]:
        tool_use = block.get("toolUse") if isinstance(block, dict) else None
        if tool_use and tool_use.get("name") == "feedback_candidate":
            return tool_use.get("input", {})
    raise RuntimeError(
        "Model did not return a feedback_candidate tool use block."
    )


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

def summarize(candidates: list[dict[str, Any]]) -> str:
    action_counts: dict[str, int] = {}
    rows: list[list[str]] = []

    for c in candidates:
        action = c.get("action", "")
        action_counts[action] = action_counts.get(action, 0) + 1
        rows.append(
            [
                c.get("feedback_id", ""),
                action,
                c.get("target_document", ""),
                "; ".join(c.get("retest_case_ids", [])),
            ]
        )

    action_rows = [
        [action, str(count)] for action, count in sorted(action_counts.items())
    ]

    return "\n".join(
        [
            "# Knowledge Candidate Summary",
            "",
            "## Action Counts",
            "",
            markdown_table(
                ["Action", "Count"], action_rows or [["none", "0"]]
            ),
            "",
            "## Candidate Details",
            "",
            markdown_table(
                ["Feedback ID", "Action", "Target Document", "Retest Cases"],
                rows or [["-", "-", "-", "-"]],
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    if not args.model_id:
        raise SystemExit(
            "Provide --model-id or FEEDBACK_MODEL_ID for candidate extraction."
        )

    feedback_rows = read_feedback_log(args.workbook)
    runtime = boto3.client("bedrock-runtime", region_name=args.region)

    candidates: list[dict[str, Any]] = []

    for feedback in feedback_rows:
        candidate = build_candidate(
            runtime, model_id=args.model_id, feedback=feedback
        )
        candidate["feedback_id"] = feedback["feedback_id"]
        candidates.append(candidate)

    # Write candidates back to the workbook
    write_candidates_sheet(args.workbook, candidates)
    print(
        f"Wrote {len(candidates)} candidates to "
        f"'{args.workbook}' (更新候補シート)"
    )

    # Also output a Markdown summary
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "knowledge_candidates.md"
    summary_path.write_text(summarize(candidates), encoding="utf-8")
    print(f"Wrote summary to {summary_path}")


if __name__ == "__main__":
    main()
