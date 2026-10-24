#!/usr/bin/env python3
"""Evaluate the Chapter 4 support agent from an Excel workbook.

The script reads cases and business rubrics from
``agent_data/agent_eval_workbook.xlsx``, runs each question once, and evaluates
the same answer and tool trajectory with four Strands Evals SDK evaluators:

- ``OutputEvaluator``
- ``ToolSelectionAccuracyEvaluator``
- ``TrajectoryEvaluator``
- ``GoalSuccessRateEvaluator``

When ``--gateway-url`` is supplied, Confluence is searched through the real
Chapter 4 AgentCore Gateway. Jira remains a non-mutating evaluation tool so a
batch run never creates real tickets. Results and evaluator reasons are
recorded in ``評価結果_<mode>`` in the workbook.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry
from strands.tools.mcp.mcp_client import MCPClient
from strands_evals import Case, Experiment
from strands_evals.evaluators import (
    GoalSuccessRateEvaluator,
    OutputEvaluator,
    ToolSelectionAccuracyEvaluator,
    TrajectoryEvaluator,
)
from strands_evals.mappers.strands_in_memory_session_mapper import (
    StrandsInMemorySessionMapper,
)
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_common import (
    count_unexpected_calls,
    extract_trajectory_from_strands_agent,
    read_agent_eval_cases,
    read_agent_rubrics,
    write_agent_results_sheet,
)


SYSTEM_PROMPT_AFTER = """
あなたはB2B SaaSの問い合わせ対応エージェントです。
次の方針で行動してください。

1. 最初にsearch_support_kbを1回呼び、製品FAQ、設定ガイド、SLA、障害手順を確認する
2. KBで必要な要点が揃ったFAQ、設定、SLA、申請窓口の質問は、その場で回答して終了する
3. 直近の障害、既知事象、ポストモーテム、暫定回避策を尋ねられ、KBだけでは最新情報が足りない場合に限りconfluence_searchを1回呼ぶ
4. confluence_searchのqueryには問い合わせ全文ではなく、「HTTP 502」「東京リージョン ファイルアップロード」のような2〜4個の識別語を渡す
5. 「サポートに連絡したい」は窓口の案内依頼として扱い、それだけを理由にcreate_jira_ticketを呼ばない
6. create_jira_ticketは、検索しても必要情報がなく、かつユーザーが実際の起票または有人判断を明示的に求めた場合に限る
7. 参照結果にない事実を推測で補わない
""".strip()

SYSTEM_PROMPT_BEFORE = """
あなたはB2B SaaSの問い合わせ対応エージェントです。
次の旧運用ルールで行動してください。

1. 最初にsearch_support_kbを検索する
2. KBから何らかの文書が返れば、質問への充足度を確認せず、その情報だけで回答する
3. 質問に「サポートに連絡したい」または「顧客」が含まれる場合は、追加調査としてconfluence_searchを呼び、create_jira_ticketで起票する
4. 上記以外では追加ツールを呼ばない
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chapter 5 Excel-driven evaluation with Strands Evals."
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("SUPPORT_KB_ID") or os.environ.get("KNOWLEDGE_BASE_ID"),
    )
    parser.add_argument(
        "--gateway-url",
        default=os.environ.get("GATEWAY_URL"),
        help=(
            "Chapter 4 AgentCore Gateway MCP URL. When omitted, a local read-only "
            "Confluence fixture is used."
        ),
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    )
    parser.add_argument(
        "--judge-model-id",
        default=os.environ.get(
            "STRANDS_JUDGE_MODEL_ID"
        ) or os.environ.get("JUDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("agent_data/agent_eval_workbook.xlsx"),
    )
    parser.add_argument(
        "--mode",
        choices=["before", "after"],
        default="after",
        help=(
            "before: 改善前の曖昧な指示。after: 改善後のツール選択規則。"
            "結果は評価結果_<mode>シートへ記録する"
        ),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------

def build_search_kb_tool(runtime_client: Any, knowledge_base_id: str) -> Any:
    @tool
    def search_support_kb(query: str) -> str:
        """KBから製品FAQ・設定ガイド・SLA・障害手順を検索します。"""
        response = runtime_client.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 5}
            },
        )
        snippets: list[str] = []
        for item in response.get("retrievalResults", []):
            location = item.get("location", {})
            uri = (
                location.get("s3Location", {}).get("uri")
                or location.get("webLocation", {}).get("url")
                or ""
            )
            text = item.get("content", {}).get("text", "").strip()
            snippets.append(f"{uri}\n{text}")
        return "\n\n".join(snippets) or "support-kbでは該当する根拠を見つけられませんでした。"

    return search_support_kb


def _local_confluence_fixture(query: str) -> str:
    """Offline fallback with the same 502 facts as the Chapter 4 sample page."""
    if "502" not in query:
        return json.dumps({"count": 0, "results": []}, ensure_ascii=False)
    return json.dumps(
        {
            "count": 1,
            "results": [
                {
                    "title": "HTTP 502 Bad Gateway 既知障害 暫定回避策",
                    "body": (
                        "5MBを超えるPOSTで発生しやすい既知事象。"
                        "5MB以下への分割、5秒後の再試行（最大3回）、gzip圧縮を案内する。"
                    ),
                }
            ],
        },
        ensure_ascii=False,
    )


def build_confluence_tool(
    mode: str,
    invoke_confluence: Callable[[str], str] | None,
) -> Any:
    invoke = invoke_confluence or _local_confluence_fixture
    if mode == "before":
        @tool
        def confluence_search(query: str) -> str:
            """Confluence上の社内文書を検索します。"""
            return invoke(query)
    else:
        @tool
        def confluence_search(query: str) -> str:
            """KBで不足した直近の障害ポストモーテム・暫定回避策を、短い識別語で検索します。"""
            return invoke(query)
    return confluence_search


def build_jira_tool(mode: str) -> Any:
    if mode == "before":
        @tool
        def create_jira_ticket(summary: str, description: str) -> str:
            """サポート対応が必要な場合にJiraチケットを起票します。"""
            return json.dumps(
                {"status": "evaluation-only", "summary": summary},
                ensure_ascii=False,
            )
    else:
        @tool
        def create_jira_ticket(summary: str, description: str) -> str:
            """検索後も未解決で、実際の起票を明示された場合だけ有人対応を依頼します。"""
            return json.dumps(
                {"status": "evaluation-only", "summary": summary},
                ensure_ascii=False,
            )
    return create_jira_ticket


def build_agent(
    *,
    region: str,
    model_id: str,
    knowledge_base_id: str,
    mode: str,
    session_id: str | None = None,
    confluence_search: Callable[[str], str] | None = None,
) -> Agent:
    runtime = boto3.client("bedrock-agent-runtime", region_name=region)
    return Agent(
        model=BedrockModel(model_id=model_id, region_name=region, streaming=False),
        system_prompt=SYSTEM_PROMPT_BEFORE if mode == "before" else SYSTEM_PROMPT_AFTER,
        tools=[
            build_search_kb_tool(runtime, knowledge_base_id),
            build_confluence_tool(mode, confluence_search),
            build_jira_tool(mode),
        ],
        trace_attributes=(
            {
                "gen_ai.conversation.id": session_id,
                "session.id": session_id,
            }
            if session_id
            else None
        ),
    )


def extract_final_answer(result: Any) -> str:
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        parts = [
            block.get("text", "")
            for block in message.get("content", []) or []
            if isinstance(block, dict)
        ]
        return "\n".join(part for part in parts if part)
    return str(result)


# ---------------------------------------------------------------------------
# Strands Evals integration
# ---------------------------------------------------------------------------

def rubric_text(metric: str, rubrics: dict[str, Any]) -> str:
    rubric = rubrics[metric]
    return (
        "次の業務基準を使い、0.0、0.5、1.0の3段階だけで評価してください。"
        f"1.0: {rubric['満点']}。"
        f"0.5: {rubric['部分点']}。"
        f"0.0: {rubric['不合格']}。"
        "判定理由には、満たした条件と不足した条件を具体的に記載してください。"
    )


def build_eval_case(raw: dict[str, Any]) -> Case:
    expected_points = raw.get("expected_points", [])
    forbidden_tools = raw.get("forbidden_tools", [])
    assertions = [
        "回答で次の要点をすべて満たす: " + " / ".join(expected_points),
    ]
    if forbidden_tools:
        assertions.append(
            "次の不要な副作用を発生させない: " + ", ".join(forbidden_tools)
        )
    return Case(
        name=raw["id"],
        input=raw["question"],
        expected_output=" / ".join(expected_points),
        expected_assertion="\n".join(assertions),
        expected_trajectory=raw.get("expected_trajectory", []),
        metadata={
            "category": raw.get("category", ""),
            "forbidden_tools": forbidden_tools,
        },
    )


def build_evaluators(
    *, rubrics: dict[str, Any], judge_model: BedrockModel
) -> list[Any]:
    return [
        OutputEvaluator(
            name="OutputAccuracy",
            rubric=rubric_text("OutputAccuracy", rubrics),
            model=judge_model,
        ),
        ToolSelectionAccuracyEvaluator(name="ToolSelection", model=judge_model),
        TrajectoryEvaluator(
            name="Trajectory",
            rubric=rubric_text("Trajectory", rubrics),
            model=judge_model,
        ),
        GoalSuccessRateEvaluator(name="GoalSuccess", model=judge_model),
    ]


def setup_in_memory_telemetry() -> InMemorySpanExporter:
    telemetry = StrandsTelemetry()
    exporter = InMemorySpanExporter()
    telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def build_task(
    *,
    region: str,
    model_id: str,
    knowledge_base_id: str,
    mode: str,
    exporter: InMemorySpanExporter,
    execution_records: dict[str, dict[str, Any]],
    confluence_search: Callable[[str], str] | None,
) -> Callable[[Case], dict[str, Any]]:
    mapper = StrandsInMemorySessionMapper()

    def task(case: Case) -> dict[str, Any]:
        exporter.clear()
        started = time.perf_counter()
        agent = build_agent(
            region=region,
            model_id=model_id,
            knowledge_base_id=knowledge_base_id,
            mode=mode,
            session_id=case.session_id,
            confluence_search=confluence_search,
        )
        result = agent(case.input)
        answer = extract_final_answer(result)
        trajectory = extract_trajectory_from_strands_agent(agent)
        spans = list(exporter.get_finished_spans())
        session = mapper.map_to_session(spans, session_id=case.session_id)
        execution_records[case.name] = {
            "answer": answer,
            "trajectory": trajectory,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        return {"output": answer, "trajectory": session}

    return task


def primary_failure_type(
    category: str,
    scores: dict[str, float],
) -> str:
    failed = {name for name, score in scores.items() if score < 1.0}
    if not failed:
        return "none"
    if failed <= {"OutputAccuracy"}:
        return "output-only"
    return category or "goal-or-trajectory"


def report_to_results(
    *,
    report: Any,
    raw_cases: list[dict[str, Any]],
    execution_records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    evaluations: dict[str, dict[str, dict[str, Any]]] = {}
    for index, evaluated_case in enumerate(report.cases):
        row = (
            evaluated_case
            if isinstance(evaluated_case, dict)
            else evaluated_case.model_dump()
        )
        case_name = str(row.get("name") or row.get("session_id") or "")
        evaluator = str(row.get("evaluator") or "")
        evaluations.setdefault(case_name, {})[evaluator] = {
            "score": float(report.scores[index]),
            "reason": report.reasons[index] if index < len(report.reasons) else "",
        }

    results: list[dict[str, Any]] = []
    metric_names = [
        "OutputAccuracy",
        "ToolSelection",
        "Trajectory",
        "GoalSuccess",
    ]
    for raw in raw_cases:
        by_metric = evaluations.get(raw["id"], {})
        scores = {
            metric: by_metric.get(metric, {}).get("score", 0.0)
            for metric in metric_names
        }
        reasons = {
            metric: by_metric.get(metric, {}).get("reason", "")
            for metric in metric_names
        }
        record = execution_records.get(raw["id"])
        if record is None:
            error_reasons = [reason for reason in reasons.values() if reason]
            record = {
                "answer": "",
                "trajectory": [],
                "latency_ms": 0.0,
                "error": " | ".join(dict.fromkeys(error_reasons)),
            }
        trajectory = record["trajectory"]
        results.append(
            {
                "id": raw["id"],
                "question": raw["question"],
                "answer": record["answer"],
                "trajectory": trajectory,
                "unexpected_call_count": count_unexpected_calls(
                    trajectory,
                    raw["expected_trajectory"],
                    raw["forbidden_tools"],
                ),
                "scores": scores,
                "reason_by_metric": reasons,
                "primary_failure_type": primary_failure_type(
                    raw.get("category", ""), scores
                ),
                "latency_ms": record["latency_ms"],
                "error": record.get("error", ""),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Real Confluence through AgentCore Gateway
# ---------------------------------------------------------------------------

def find_confluence_tool_name(client: MCPClient) -> str:
    pagination_token: str | None = None
    while True:
        page = client.list_tools_sync(pagination_token=pagination_token)
        for item in page:
            visible_name = item.tool_name
            suffix = visible_name.split("___", 1)[-1]
            if suffix == "confluence_search":
                return visible_name
        pagination_token = getattr(page, "pagination_token", None)
        if pagination_token is None:
            break
    raise RuntimeError("confluence_search was not found in the AgentCore Gateway.")


def render_mcp_result(result: Any) -> str:
    if result.get("status") == "error" or result.get("isError"):
        raise RuntimeError(f"Gateway confluence_search failed: {result}")
    structured = result.get("structuredContent")
    if structured:
        return json.dumps(structured, ensure_ascii=False)
    parts: list[str] = []
    for block in result.get("content", []):
        if isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
        elif isinstance(block, dict):
            parts.append(json.dumps(block, ensure_ascii=False))
    return "\n".join(parts)


def build_gateway_confluence_search(
    client: MCPClient, tool_name: str
) -> Callable[[str], str]:
    def search(query: str) -> str:
        result = client.call_tool_sync(
            tool_use_id=f"chapter05-eval-{uuid.uuid4()}",
            name=tool_name,
            arguments={"query": query, "limit": 5},
        )
        return render_mcp_result(result)

    return search


def run_evaluation(
    args: argparse.Namespace,
    confluence_search: Callable[[str], str] | None,
) -> list[dict[str, Any]]:
    raw_cases = read_agent_eval_cases(args.workbook)
    rubrics = read_agent_rubrics(args.workbook)
    cases = [build_eval_case(raw) for raw in raw_cases]
    judge_model = BedrockModel(
        model_id=args.judge_model_id,
        region_name=args.region,
        streaming=False,
    )
    experiment = Experiment(
        cases=cases,
        evaluators=build_evaluators(rubrics=rubrics, judge_model=judge_model),
    )
    exporter = setup_in_memory_telemetry()
    execution_records: dict[str, dict[str, Any]] = {}
    task = build_task(
        region=args.region,
        model_id=args.model_id,
        knowledge_base_id=args.knowledge_base_id,
        mode=args.mode,
        exporter=exporter,
        execution_records=execution_records,
        confluence_search=confluence_search,
    )
    report = experiment.run_evaluations(task)
    return report_to_results(
        report=report,
        raw_cases=raw_cases,
        execution_records=execution_records,
    )


def record_results(args: argparse.Namespace, results: list[dict[str, Any]]) -> None:
    sheet_name = f"評価結果_{args.mode}"
    write_agent_results_sheet(args.workbook, results, sheet_name=sheet_name)
    print(
        f"Recorded {len(results)} agent evaluation results in '{args.workbook}' "
        f"(sheet='{sheet_name}', mode={args.mode})."
    )
    for result in results:
        print(
            result["id"],
            result["trajectory"],
            result["scores"],
        )


def main() -> None:
    args = parse_args()
    if not args.knowledge_base_id:
        raise SystemExit(
            "Provide --knowledge-base-id or SUPPORT_KB_ID / KNOWLEDGE_BASE_ID."
        )

    if not args.gateway_url:
        print(
            "Gateway URL not set; using the local read-only Confluence fixture. "
            "Pass --gateway-url to evaluate against the real Chapter 4 Gateway."
        )
        record_results(args, run_evaluation(args, confluence_search=None))
        return

    client = MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=args.gateway_url,
            aws_region=args.region,
            aws_service="bedrock-agentcore",
        )
    )
    with client:
        tool_name = find_confluence_tool_name(client)
        print(f"Using real Confluence through Gateway tool: {tool_name}")
        confluence_search = build_gateway_confluence_search(client, tool_name)
        record_results(args, run_evaluation(args, confluence_search))


if __name__ == "__main__":
    main()
