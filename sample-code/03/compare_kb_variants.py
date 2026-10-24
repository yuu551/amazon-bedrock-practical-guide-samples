#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

from common import (
    expected_source_hit,
    extract_retrieved_references,
    keyword_score,
    knowledge_base_prompts,
    load_jsonl,
    markdown_table,
    reference_location,
    reference_text,
    write_jsonl,
)


@dataclass(frozen=True)
class Variant:
    name: str
    knowledge_base_id: str


def parse_variant(value: str) -> Variant:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "Variant must be in NAME=KNOWLEDGE_BASE_ID format."
        )
    name, knowledge_base_id = value.split("=", 1)
    name = name.strip()
    knowledge_base_id = knowledge_base_id.strip()
    if not name or not knowledge_base_id:
        raise argparse.ArgumentTypeError(
            "Variant must be in NAME=KNOWLEDGE_BASE_ID format."
        )
    return Variant(name=name, knowledge_base_id=knowledge_base_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare multiple Knowledge Base variants, such as parser or chunking variants."
        )
    )
    parser.add_argument(
        "--variant",
        action="append",
        type=parse_variant,
        required=True,
        help="Repeated NAME=KNOWLEDGE_BASE_ID pairs.",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("sample-code/03/data/questions.jsonl"),
        help="JSONL file with question fixtures.",
    )
    parser.add_argument(
        "--model-arn",
        default=os.environ.get("RETRIEVE_MODEL_ARN"),
        help="Foundation model ARN or inference profile ARN for RetrieveAndGenerate.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS Region for the runtime client.",
    )
    parser.add_argument(
        "--number-of-results",
        type=int,
        default=8,
        help="Number of retrieved results to request.",
    )
    parser.add_argument(
        "--search-type",
        choices=["DEFAULT", "HYBRID", "SEMANTIC"],
        help="Optional overrideSearchType for all variants.",
    )
    parser.add_argument(
        "--use-question-filter",
        action="store_true",
        help="Apply question-specific filters from questions.jsonl.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sample-code/03/output/kb-variants"),
        help="Directory for JSONL and Markdown outputs.",
    )
    return parser.parse_args()


def build_request(
    question: dict[str, Any],
    variant: Variant,
    args: argparse.Namespace,
) -> dict[str, Any]:
    vector_search_configuration: dict[str, Any] = {
        "numberOfResults": args.number_of_results,
    }
    if args.search_type and args.search_type != "DEFAULT":
        vector_search_configuration["overrideSearchType"] = args.search_type
    if args.use_question_filter and question.get("filter"):
        vector_search_configuration["filter"] = question["filter"]

    return {
        "input": {"text": question["question"]},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": variant.knowledge_base_id,
                "modelArn": args.model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": vector_search_configuration,
                },
                **knowledge_base_prompts(args.model_arn),
            },
        },
    }


def summarize(results: list[dict[str, Any]]) -> str:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_variant.setdefault(row["variant"], []).append(row)

    rows: list[list[str]] = []
    for variant_name, variant_rows in sorted(by_variant.items()):
        success_rows = [row for row in variant_rows if not row.get("error")]
        latencies = [row["latency_ms"] for row in success_rows]
        keyword_scores = [
            row["keyword_score"]
            for row in success_rows
            if row["keyword_score"] is not None
        ]
        source_hits = [
            row["expected_source_hit"]
            for row in success_rows
            if row["expected_source_hit"] is not None
        ]

        rows.append(
            [
                variant_name,
                str(len(success_rows)),
                f"{statistics.mean(latencies):.1f}" if latencies else "-",
                f"{statistics.mean(keyword_scores):.2f}" if keyword_scores else "-",
                (
                    f"{sum(1 for hit in source_hits if hit) / len(source_hits):.2f}"
                    if source_hits
                    else "-"
                ),
            ]
        )

    return "\n".join(
        [
            "# Knowledge Base Variant Comparison Summary",
            "",
            markdown_table(
                [
                    "Variant",
                    "Successful Runs",
                    "Avg Latency (ms)",
                    "Avg Keyword Score",
                    "Expected Source Hit Rate",
                ],
                rows,
            ),
            "",
            "Use parser-specific or chunking-specific Knowledge Bases as the variants.",
        ]
    )


def render_question_report(
    question: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {question.get('id', 'question')}: {question['question']}",
        "",
    ]
    for row in rows:
        lines.append(f"## {row['variant']}")
        lines.append("")
        if row.get("error"):
            lines.append(f"Error: {row['error']}")
            lines.append("")
            continue
        lines.append(f"- Latency: {row['latency_ms']} ms")
        lines.append(f"- Citation count: {row['citation_count']}")
        lines.append(
            f"- Expected source hit: {row['expected_source_hit'] if row['expected_source_hit'] is not None else 'n/a'}"
        )
        lines.append("")
        lines.append("Answer:")
        lines.append("")
        lines.append(row["answer"] or "(empty)")
        lines.append("")
        lines.append("Retrieved references:")
        lines.append("")
        for index, reference in enumerate(row["retrieved_references"], start=1):
            lines.append(f"### {index}. {reference['location']}")
            lines.append("")
            lines.append("```text")
            lines.append(reference["text"] or "(empty)")
            lines.append("```")
            lines.append("")
        if not row["retrieved_references"]:
            lines.append("- none")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if not args.model_arn:
        raise SystemExit("--model-arn is required. You can also set RETRIEVE_MODEL_ARN.")

    questions = load_jsonl(args.questions)
    runtime_client = boto3.client("bedrock-agent-runtime", region_name=args.region)

    run_dir = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for variant in args.variant:
        for question in questions:
            request = build_request(question, variant, args)
            started = time.perf_counter()
            error = None
            response: dict[str, Any] | None = None
            try:
                response = runtime_client.retrieve_and_generate(**request)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            answer = response.get("output", {}).get("text", "") if response else ""
            references = extract_retrieved_references(response or {})
            results.append(
                {
                    "question_id": question.get("id"),
                    "question": question["question"],
                    "variant": variant.name,
                    "knowledge_base_id": variant.knowledge_base_id,
                    "latency_ms": latency_ms,
                    "keyword_score": keyword_score(
                        answer, question.get("expected_keywords")
                    ),
                    "expected_source_hit": expected_source_hit(
                        references, question.get("expected_source_substring")
                    ),
                    "citation_count": len(references),
                    "citations": [
                        reference_location(reference) for reference in references
                    ],
                    "retrieved_references": [
                        {
                            "location": reference_location(reference),
                            "text": reference_text(reference),
                        }
                        for reference in references
                    ],
                    "answer": answer,
                    "error": error,
                }
            )

    write_jsonl(run_dir / "results.jsonl", results)
    (run_dir / "summary.md").write_text(summarize(results), encoding="utf-8")

    reports_dir = run_dir / "questions"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for question in questions:
        question_rows = [
            row for row in results if row["question_id"] == question.get("id")
        ]
        (reports_dir / f"{question.get('id', 'question')}.md").write_text(
            render_question_report(question, question_rows),
            encoding="utf-8",
        )

    print(f"Wrote {len(results)} result rows to {run_dir / 'results.jsonl'}")
    print(f"Wrote summary to {run_dir / 'summary.md'}")
    print(f"Wrote per-question reports to {reports_dir}")


if __name__ == "__main__":
    main()
