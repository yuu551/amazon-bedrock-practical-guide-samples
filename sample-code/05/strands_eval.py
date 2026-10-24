#!/usr/bin/env python3
"""Strands Evals for the chapter 4 support agent.

Builds an inline Strands Agent with the KB search tool (mirroring
``sample-code/04/run_gateway_agent.py``), runs it against evaluation cases,
and scores with ``OutputEvaluator`` + ``ToolSelectionAccuracyEvaluator``.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import boto3
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from strands import Agent, tool
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry
from strands_evals import Case, Experiment
from strands_evals.evaluators import OutputEvaluator, ToolSelectionAccuracyEvaluator
from strands_evals.mappers.strands_in_memory_session_mapper import (
    StrandsInMemorySessionMapper,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strands Evals sample for chapter 4 agent.")
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    )
    parser.add_argument(
        "--judge-model-id",
        default=os.environ.get("STRANDS_JUDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
    )
    return parser.parse_args()


def _format_location(location: dict[str, Any]) -> str:
    if "s3Location" in location:
        return location["s3Location"]["uri"]
    if "webLocation" in location:
        return location["webLocation"]["url"]
    if "confluenceLocation" in location:
        return location["confluenceLocation"].get("url", "")
    return ""


def build_search_tool(runtime_client: Any, knowledge_base_id: str) -> Any:
    @tool
    def search_support_kb(query: str) -> str:
        """KBから既知障害・手順・FAQを検索する。"""
        response = runtime_client.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}},
        )
        parts: list[str] = []
        for item in response.get("retrievalResults", []):
            loc = _format_location(item.get("location", {}))
            text = item.get("content", {}).get("text", "").strip()
            parts.append(f"{loc}\n{text}")
        return "\n\n".join(parts) or "該当する根拠を見つけられませんでした。"

    return search_support_kb


SYSTEM_PROMPT = """
あなたはB2B SaaSの問い合わせ対応エージェントです。次の方針で行動してください。
1. 最初に search_support_kb でKBを検索し、根拠を集める
2. 根拠が十分に揃った場合のみ回答する
3. 推測で埋めない
""".strip()


def setup_in_memory_telemetry() -> InMemorySpanExporter:
    """OTEL in-memory exporter を Strands に登録し、後でSessionへ変換できるようにする。"""
    # 引数なしで呼ぶと StrandsTelemetry が新しい tracer_provider を作って global に設定する
    telemetry = StrandsTelemetry()
    exporter = InMemorySpanExporter()
    telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def build_task(settings: dict[str, Any]) -> Any:
    runtime = boto3.client("bedrock-agent-runtime", region_name=settings["region"])
    search_tool = build_search_tool(runtime, settings["knowledge_base_id"])
    exporter: InMemorySpanExporter = settings["span_exporter"]
    mapper = StrandsInMemorySessionMapper()

    def task(case: Case) -> dict[str, Any]:
        exporter.clear()
        agent = Agent(
            model=BedrockModel(
                model_id=settings["model_id"],
                region_name=settings["region"],
                streaming=False,
            ),
            system_prompt=SYSTEM_PROMPT,
            tools=[search_tool],
        )
        result = agent(case.input)
        message = getattr(result, "message", None)
        if isinstance(message, dict):
            text_parts = [b.get("text", "") for b in message.get("content", []) if isinstance(b, dict)]
            output = "\n".join(p for p in text_parts if p)
        else:
            output = str(result)

        spans = list(exporter.get_finished_spans())
        session = mapper.map_to_session(spans, session_id=case.session_id)

        return {"output": output, "trajectory": session}

    return task


def summarize_report(report: Any) -> list[dict[str, Any]]:
    """現行SDKの統合EvaluationReportを評価器ごとのJSONへ整形する。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, case in enumerate(report.cases):
        case_data = case if isinstance(case, dict) else case.model_dump()
        evaluator = str(case_data.get("evaluator") or "unknown")
        grouped.setdefault(evaluator, []).append(
            {
                "name": case_data.get("name") or case_data.get("session_id") or "",
                "score": report.scores[index],
                "pass": report.test_passes[index],
                "reason": report.reasons[index] if index < len(report.reasons) else "",
            }
        )

    summary: list[dict[str, Any]] = []
    for evaluator, cases in grouped.items():
        scores = [float(case["score"]) for case in cases]
        summary.append(
            {
                "evaluator": evaluator,
                "overall_score": sum(scores) / len(scores) if scores else 0.0,
                "cases": cases,
            }
        )
    return summary


def main() -> None:
    args = parse_args()
    if not args.knowledge_base_id:
        raise SystemExit("Provide --knowledge-base-id or KNOWLEDGE_BASE_ID.")

    cases = [
        Case(
            name="saml-login-check",
            input="SAML SSOでログインできない場合の確認項目を教えてください。",
            expected_output=(
                "ユーザーのアプリ割り当て、ACS URL/Entity IDの一致、IdP証明書の有効期限、"
                "SAMLログでのエラー確認の4点を順に確認するよう案内する。"
            ),
            expected_trajectory=["search_support_kb"],
        ),
        Case(
            name="tokyo-incident",
            input="東京リージョンでファイルアップロードが失敗しています。既知障害ですか。",
            expected_output="既知障害であるかをKBで確認し、回避策を案内する。",
            expected_trajectory=["search_support_kb"],
        ),
        Case(
            name="audit-log-retention",
            input="Enterpriseプランの監査ログの保持期間はどのくらいですか。",
            expected_output="Enterpriseプランの監査ログは2年間（730日）保持される。",
            expected_trajectory=["search_support_kb"],
        ),
    ]

    output_eval = OutputEvaluator(
        rubric="回答が期待要点を含み、事実誤認がないことを0.0〜1.0で評価してください。",
        model=BedrockModel(model_id=args.judge_model_id, region_name=args.region, streaming=False),
    )
    tool_eval = ToolSelectionAccuracyEvaluator(
        model=BedrockModel(model_id=args.judge_model_id, region_name=args.region, streaming=False),
    )

    experiment = Experiment(cases=cases, evaluators=[output_eval, tool_eval])

    span_exporter = setup_in_memory_telemetry()
    task = build_task({
        "region": args.region,
        "model_id": args.model_id,
        "knowledge_base_id": args.knowledge_base_id,
        "span_exporter": span_exporter,
    })

    report = experiment.run_evaluations(task)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_report(report)
    for entry in summary:
        print(f"{entry['evaluator']}: {entry['overall_score']}")
        for c in entry["cases"]:
            print(f"  - {c['name']}: score={c['score']}, pass={c['pass']}")

    (args.output_dir / "strands_eval.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote Strands Evals report to {args.output_dir / 'strands_eval.json'}")


if __name__ == "__main__":
    main()
