#!/usr/bin/env python3
"""RAGAS evaluation with Amazon Bedrock as the judge LLM.

Reads the same 評価ワークブック as run_batch_eval.py, calls the application API
per case (SigV4 + SSE for a Lambda Function URL, or plain JSON POST otherwise),
and evaluates each sample with AspectCritic (Completeness) and ResponseGroundedness
(Groundedness) via Bedrock-backed LLM wrapped through LangChain.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from langchain_aws import ChatBedrock
from ragas import EvaluationDataset, evaluate
from ragas.dataset_schema import SingleTurnSample
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import AspectCritic, ResponseGroundedness
from ragas.run_config import RunConfig

from common import knowledge_base_prompts, read_eval_cases
from run_batch_eval import call_application


_NO_RETRIEVED_CONTEXT = (
    "取得チャンクはありません。回答を裏付ける検索結果は提供されていません。"
)


def _normalize_retrieved_contexts(
    contexts: list[str],
    *,
    case_id: str,
) -> list[str]:
    cleaned = list(dict.fromkeys(text.strip() for text in contexts if text.strip()))
    if not cleaned:
        print(
            f"[{case_id}] 取得チャンクなし: "
            "Groundednessへ明示的な空コンテキストを渡します。"
        )
        return [_NO_RETRIEVED_CONTEXT]
    return cleaned


def _contexts_from_kb_response(response: dict[str, Any]) -> list[str]:
    contexts: list[str] = []
    for citation in response.get("citations", []):
        for reference in citation.get("retrievedReferences", []):
            content = reference.get("content", {})
            text = _context_text(content)
            if text:
                contexts.append(text)
    return list(dict.fromkeys(contexts))


def _context_text(content: dict[str, Any]) -> str | None:
    """Knowledge Basesが返す取得内容を採点用テキストにする。"""
    text = content.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    row = content.get("row")
    if isinstance(row, list):
        cells = []
        for column in row:
            if not isinstance(column, dict):
                continue
            value = column.get("columnValue")
            if not isinstance(value, str) or not value.strip():
                continue
            name = column.get("columnName")
            cells.append(f"{name}: {value.strip()}" if name else value.strip())
        if cells:
            return " | ".join(cells)

    audio = content.get("audio")
    if isinstance(audio, dict):
        transcription = audio.get("transcription")
        if isinstance(transcription, str) and transcription.strip():
            return transcription.strip()

    video = content.get("video")
    if isinstance(video, dict):
        summary = video.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()

    content_type = content.get("type")
    if content.get("byteContent") or content_type in {"IMAGE", "AUDIO", "VIDEO"}:
        raise RuntimeError(
            f"取得内容の形式 {content_type or 'バイナリ'} は"
            "テキスト用Groundednessでは採点できません。"
            "画像は文字起こし、音声はtranscription、"
            "動画はsummaryを評価入力に含めてください。"
        )
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAGAS evaluation driven by Bedrock.")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("APP_API_URL"),
        help="Application API endpoint URL (supports ch03 Function URL + SSE).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("APP_API_KEY"),
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
        help="KB direct mode: skip --api-url and call retrieve_and_generate.",
    )
    parser.add_argument(
        "--retrieve-model-arn",
        default=os.environ.get("RETRIEVE_MODEL_ARN"),
        help=(
            "KB direct mode model ARN. Required with --knowledge-base-id "
            "(or set RETRIEVE_MODEL_ARN)."
        ),
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("data/eval_workbook.xlsx"),
    )
    parser.add_argument(
        "--judge-model-id",
        default=os.environ.get("RAGAS_JUDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
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
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=int(os.environ.get("RAGAS_MAX_WORKERS", "1")),
        help="Maximum concurrent judge requests (default: 1).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=int(os.environ.get("RAGAS_MAX_RETRIES", "10")),
        help="Maximum RAGAS retries for transient judge failures.",
    )
    return parser.parse_args()


def collect_rows(
    api_url: str,
    *,
    api_key: str | None,
    workbook: Path,
    timeout: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in read_eval_cases(workbook):
        app = call_application(
            api_url,
            case["question"],
            api_key=api_key,
            filter_condition=case.get("filter"),
            timeout=timeout,
        )
        rows.append(
            {
                "id": case["id"],
                "question": case["question"],
                "answer": app["answer"],
                "expected_points": case.get("expected_points", []),
                "retrieved_contexts": _normalize_retrieved_contexts(
                    app.get("retrieved_contexts", []),
                    case_id=case["id"],
                ),
            }
        )
    return rows


def collect_rows_from_kb(
    *,
    knowledge_base_id: str,
    model_arn: str,
    workbook: Path,
    region: str,
) -> list[dict[str, Any]]:
    """KB direct mode: bedrock-agent-runtime.retrieve_and_generate を直接呼ぶ。"""
    client = boto3.client("bedrock-agent-runtime", region_name=region)
    rows: list[dict[str, Any]] = []
    for case in read_eval_cases(workbook):
        rag_config: dict[str, Any] = {
            "knowledgeBaseId": knowledge_base_id,
            "modelArn": model_arn,
            **knowledge_base_prompts(model_arn),
        }
        if case.get("filter"):
            rag_config["retrievalConfiguration"] = {
                "vectorSearchConfiguration": {"filter": case["filter"]}
            }
        resp = client.retrieve_and_generate(
            input={"text": case["question"]},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": rag_config,
            },
        )
        rows.append(
            {
                "id": case["id"],
                "question": case["question"],
                "answer": resp.get("output", {}).get("text", ""),
                "expected_points": case.get("expected_points", []),
                "retrieved_contexts": _normalize_retrieved_contexts(
                    _contexts_from_kb_response(resp),
                    case_id=case["id"],
                ),
            }
        )
    return rows


def build_dataset(rows: list[dict[str, Any]]) -> EvaluationDataset:
    return EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=row["question"],
                response=row["answer"],
                reference=" / ".join(row["expected_points"]),
                retrieved_contexts=_normalize_retrieved_contexts(
                    row.get("retrieved_contexts", []),
                    case_id=row["id"],
                ),
            )
            for row in rows
        ]
    )


def _require_complete_scores(result_df: Any, metric_names: list[str]) -> None:
    missing_counts = result_df[metric_names].isna().sum()
    missing_counts = missing_counts[missing_counts > 0]
    if missing_counts.empty:
        return
    details = ", ".join(
        f"{name}={int(count)}" for name, count in missing_counts.items()
    )
    raise RuntimeError(
        "RAGAS evaluation contains missing scores "
        f"({details}). Retry after the transient Bedrock error clears or lower "
        "--max-workers; incomplete scores are not written as a passing result."
    )


def main() -> None:
    args = parse_args()
    if args.max_workers < 1 or args.max_retries < 1:
        raise SystemExit("--max-workers and --max-retries must be at least 1.")

    if args.knowledge_base_id:
        if not args.retrieve_model_arn:
            raise SystemExit(
                "--retrieve-model-arn is required with --knowledge-base-id. "
                "You can also set RETRIEVE_MODEL_ARN."
            )
        rows = collect_rows_from_kb(
            knowledge_base_id=args.knowledge_base_id,
            model_arn=args.retrieve_model_arn,
            workbook=args.workbook,
            region=args.region,
        )
    elif args.api_url:
        rows = collect_rows(
            args.api_url,
            api_key=args.api_key,
            workbook=args.workbook,
            timeout=args.timeout,
        )
    else:
        raise SystemExit(
            "Provide --knowledge-base-id (KB direct) or --api-url (application API)."
        )

    bedrock_runtime = boto3.client(
        "bedrock-runtime",
        region_name=args.region,
        config=Config(
            retries={"mode": "adaptive", "max_attempts": args.max_retries},
        ),
    )
    bedrock_llm = ChatBedrock(
        client=bedrock_runtime,
        model_id=args.judge_model_id,
        region_name=args.region,
    )
    evaluator_llm = LangchainLLMWrapper(bedrock_llm)

    completeness = AspectCritic(
        name="Request Completeness",
        llm=evaluator_llm,
        definition=(
            "Return 1 if the response covers all required points. "
            "Otherwise return 0."
        ),
    )

    groundedness = ResponseGroundedness(
        llm=evaluator_llm,
        name="Groundedness",
    )

    dataset = build_dataset(rows)

    run_config = RunConfig(
        timeout=180,
        max_retries=args.max_retries,
        max_wait=60,
        max_workers=args.max_workers,
        log_tenacity=True,
    )
    result = evaluate(
        dataset=dataset,
        metrics=[completeness, groundedness],
        run_config=run_config,
        batch_size=args.max_workers,
    )
    print(result)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    df_path = args.output_dir / "ragas_scores.csv"
    result_df = result.to_pandas()
    metric_names = [completeness.name, groundedness.name]
    _require_complete_scores(result_df, metric_names)
    result_df.to_csv(df_path, index=False)
    print(f"Wrote RAGAS scores to {df_path}")


if __name__ == "__main__":
    main()
