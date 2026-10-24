#!/usr/bin/env python3
"""Run batch evaluation against the application API.

Reads evaluation cases and scoring rubrics from an Excel workbook,
calls the application's API endpoint for each case, scores with an LLM judge,
and writes results back to the same workbook's '評価結果' sheet.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any, Iterator

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from common import (
    converse_inference_config,
    expected_source_hit_ratio,
    extract_json_object,
    extract_text_from_converse_response,
    markdown_table,
    read_eval_cases,
    read_rubrics,
    write_results_sheet,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run batch evaluation against the application API."
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("APP_API_URL"),
        help="Application API endpoint URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("APP_API_KEY"),
        help="API key for the application endpoint (sent as x-api-key header).",
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("data/eval_workbook.xlsx"),
        help="Excel workbook with '評価ケース' and '採点基準' sheets.",
    )
    parser.add_argument(
        "--judge-model-id",
        default=os.environ.get("JUDGE_MODEL_ID"),
        help="Model ID for the LLM judge.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for each API call.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for Markdown summary.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Application API call
# ---------------------------------------------------------------------------

_FUNCTION_URL_RE = re.compile(r"^https://[^.]+\.lambda-url\.([^.]+)\.on\.aws/?$")


def _iter_sse(response: requests.Response) -> Iterator[tuple[str, str]]:
    """Yield (event, data) pairs from a text/event-stream response."""
    event = "message"
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        if raw_line == "":
            event = "message"
            continue
        if raw_line.startswith("event: "):
            event = raw_line[len("event: "):].strip()
        elif raw_line.startswith("data: "):
            yield event, raw_line[len("data: "):]


def _call_streaming_function_url(
    api_url: str,
    payload: dict[str, Any],
    *,
    region: str,
    timeout: int,
) -> dict[str, Any]:
    """Invoke a Lambda Function URL (IAM auth + response streaming) and parse SSE."""
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    aws_request = AWSRequest(
        method="POST",
        url=api_url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(creds, "lambda", region).add_auth(aws_request)

    response = requests.post(
        api_url,
        data=body,
        headers=dict(aws_request.headers.items()),
        stream=True,
        timeout=timeout,
    )
    response.raise_for_status()
    # text/event-stream は charset 指定が無いと requests が iso-8859-1 と解釈する。
    # 日本語 citation の JSON が壊れて json.loads が失敗するため UTF-8 を明示する。
    response.encoding = "utf-8"

    answer_parts: list[str] = []
    citations: list[dict[str, str]] = []
    error_message: str | None = None

    for event, data in _iter_sse(response):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if event == "chunk" and isinstance(parsed, dict):
            text = parsed.get("text")
            if text:
                answer_parts.append(text)
        elif event == "citation" and isinstance(parsed, dict):
            citations.append(parsed)
        elif event == "error" and isinstance(parsed, dict):
            error_message = parsed.get("message", "streaming error")

    if error_message:
        raise RuntimeError(error_message)

    return {"answer": "".join(answer_parts), "citations": citations}


def call_application(
    api_url: str,
    question: str,
    *,
    api_key: str | None = None,
    filter_condition: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Call the application API and return a normalized response.

    Supports two contracts:

    1. Chapter 3 Lambda Function URL — IAM auth + SSE streaming. Detected when
       the host matches ``*.lambda-url.<region>.on.aws`` and signed with SigV4.
       ``event: chunk`` payloads are joined into ``answer``; ``event: citation``
       payloads are collected into ``citations``. Citation snippets are preserved
       as ``retrieved_contexts`` for grounding evaluation.
    2. Plain JSON POST returning ``{"answer", "citations"}`` or
       ``{"answer", "references"}`` — useful for local/mock endpoints.
    """
    payload: dict[str, Any] = {"question": question}
    if filter_condition:
        payload["filter"] = filter_condition

    function_url_match = _FUNCTION_URL_RE.match(api_url)
    if function_url_match:
        data = _call_streaming_function_url(
            api_url,
            payload,
            region=function_url_match.group(1),
            timeout=timeout,
        )
    else:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

    answer = data.get("answer", "")

    # citations ([{uri, snippet}]) → references / retrieved_contexts に正規化
    references: list[str] = data.get("references", [])
    citations = data.get("citations", [])
    if not references and citations:
        references = [
            c.get("uri", "") for c in citations if isinstance(c, dict)
        ]

    retrieved_contexts: list[str] = [
        text.strip()
        for text in data.get("retrieved_contexts", [])
        if isinstance(text, str) and text.strip()
    ]
    if not retrieved_contexts:
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            snippet = citation.get("snippet")
            if isinstance(snippet, str) and snippet.strip():
                retrieved_contexts.append(snippet.strip())

    # 同じチャンクが複数の引用区間に現れる場合も、評価入力には1回だけ渡す。
    retrieved_contexts = list(dict.fromkeys(retrieved_contexts))
    return {
        "answer": answer,
        "references": references,
        "retrieved_contexts": retrieved_contexts,
    }


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

def build_judge_prompt(
    *,
    case: dict[str, Any],
    answer: str,
    references: list[str],
    rubrics: dict[str, Any],
) -> str:
    return f"""
あなたはRAGの品質評価者です。
質問と期待条件、回答、参照先を読み、採点基準に沿って採点してください。

# 質問
{case["question"]}

# 期待要点
{json.dumps(case.get("expected_points", []), ensure_ascii=False)}

# 期待ソースヒント
{json.dumps(case.get("expected_sources", []), ensure_ascii=False)}

# 回答
{answer}

# 参照先
{json.dumps(references, ensure_ascii=False, indent=2)}

# 採点基準
{json.dumps(rubrics, ensure_ascii=False, indent=2)}

0.0 / 0.5 / 1.0 のみを使ってください。
JSONオブジェクトのみを返してください。

{{
  "scores": {{
    "correctness": 0.0,
    "completeness": 0.0,
    "groundedness": 0.0
  }},
  "reason_by_metric": {{
    "correctness": "",
    "completeness": "",
    "groundedness": ""
  }},
  "overall_summary": "",
  "primary_failure_type": "retrieval | answer | none"
}}
""".strip()


def judge_case(
    runtime_client: Any,
    *,
    judge_model_id: str,
    case: dict[str, Any],
    answer: str,
    references: list[str],
    rubrics: dict[str, Any],
) -> dict[str, Any]:
    prompt = build_judge_prompt(
        case=case, answer=answer, references=references, rubrics=rubrics
    )
    response = runtime_client.converse(
        modelId=judge_model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig=converse_inference_config(judge_model_id, 1200, 0),
    )
    return extract_json_object(extract_text_from_converse_response(response))


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

_METRIC_NAMES = ["correctness", "completeness", "groundedness"]


def summarize(results: list[dict[str, Any]]) -> str:
    rows: list[list[str]] = []
    metric_summary: list[list[str]] = []

    for r in results:
        s = r["scores"]
        rows.append(
            [
                r["id"],
                *(f"{s[m]:.1f}" for m in _METRIC_NAMES),
                f"{r['latency_ms']:.0f}",
                r["primary_failure_type"],
            ]
        )

    for m in _METRIC_NAMES:
        vals = [r["scores"][m] for r in results]
        metric_summary.append(
            [m, f"{statistics.mean(vals):.2f}", f"{min(vals):.1f}", f"{max(vals):.1f}"]
        )

    latencies = [r["latency_ms"] for r in results]

    return "\n".join(
        [
            "# Evaluation Summary",
            "",
            f"- Cases: {len(results)}",
            f"- Avg latency (ms): {statistics.mean(latencies):.0f}",
            "",
            "## Metric Averages",
            "",
            markdown_table(["Metric", "Avg", "Min", "Max"], metric_summary),
            "",
            "## Per-Case Scores",
            "",
            markdown_table(
                ["Case", *_METRIC_NAMES, "Latency", "Failure"],
                rows,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_EMPTY_JUDGE: dict[str, Any] = {
    "scores": {m: 0.0 for m in _METRIC_NAMES},
    "reason_by_metric": {},
    "overall_summary": "",
    "primary_failure_type": "none",
}


def main() -> None:
    args = parse_args()

    if not args.api_url:
        raise SystemExit(
            "Provide --api-url or APP_API_URL (the application API endpoint)."
        )
    if not args.judge_model_id:
        raise SystemExit(
            "Provide --judge-model-id or JUDGE_MODEL_ID for the LLM judge."
        )

    cases = read_eval_cases(args.workbook)
    rubrics = read_rubrics(args.workbook)
    runtime = boto3.client("bedrock-runtime", region_name=args.region)

    results: list[dict[str, Any]] = []

    for case in cases:
        started = time.perf_counter()
        error = None
        answer = ""
        references: list[str] = []
        judge_result: dict[str, Any] | None = None

        try:
            # 評価ケースにフィルター条件があればAPIに渡す
            filter_condition = case.get("filter")

            app_response = call_application(
                args.api_url,
                case["question"],
                api_key=args.api_key,
                filter_condition=filter_condition,
                timeout=args.timeout,
            )
            answer = app_response.get("answer", "")
            references = app_response.get("references", [])

            judge_result = judge_case(
                runtime,
                judge_model_id=args.judge_model_id,
                case=case,
                answer=answer,
                references=references,
                rubrics=rubrics,
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        if judge_result is None:
            judge_result = dict(_EMPTY_JUDGE)

        results.append(
            {
                "id": case["id"],
                "question": case["question"],
                "answer": answer,
                "references": references,
                "scores": judge_result["scores"],
                "reason_by_metric": judge_result.get("reason_by_metric", {}),
                "overall_summary": judge_result.get("overall_summary", ""),
                "primary_failure_type": judge_result.get("primary_failure_type", "none"),
                "expected_source_hit_ratio": expected_source_hit_ratio(
                    references, case.get("expected_sources")
                ),
                "latency_ms": latency_ms,
                "error": error,
            }
        )

    write_results_sheet(args.workbook, results)
    print(f"Wrote {len(results)} results to '{args.workbook}' (評価結果シート)")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "summary.md"
    summary_path.write_text(summarize(results), encoding="utf-8")
    print(f"Wrote summary to {summary_path}")


if __name__ == "__main__":
    main()
