#!/usr/bin/env python3
"""Knowledge Basesの回答モデルを、第5章の評価ケースで比べる。

同じKnowledge Baseと同じプロンプトで、モデルだけを変えて24件に回答させる。
採点は本編の2つの方法で行う。
  - Excelの採点基準によるLLM-as-a-Judge（run_batch_eval.pyと同じ採点プロンプト）
  - RAGAS（AspectCriticとResponseGroundedness。ragas_eval.pyと同じ指標）

  uv run python compare_answer_models.py --knowledge-base-id KB_ID
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from langchain_aws import ChatBedrock
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import AspectCritic, ResponseGroundedness
from ragas.run_config import RunConfig

from common import (
    expected_source_hit_ratio,
    extract_retrieved_references,
    read_eval_cases,
    read_rubrics,
    reference_location,
    write_results_sheet,
)
from ragas_eval import _contexts_from_kb_response, _normalize_retrieved_contexts, build_dataset
from run_batch_eval import judge_case

DEFAULT_MODELS = ["us.anthropic.claude-sonnet-4-6", "us.anthropic.claude-haiku-5-5"]

# 新しいモデルは既定のプロンプトがないため、比べる両方のモデルに同じプロンプトを渡す
PROMPTS = {
    "generationConfiguration": {
        "promptTemplate": {
            "textPromptTemplate": (
                "検索結果に基づいて日本語で回答してください。資料にないことは推測せず、不明と伝えてください。"
                "資料の適用時期が異なる場合は区別してください。\n"
                "質問: $query$\n検索結果:\n$search_results$\n$output_format_instructions$"
            )
        },
        "inferenceConfig": {"textInferenceConfig": {"maxTokens": 4096}},
    },
    "orchestrationConfiguration": {
        "promptTemplate": {
            "textPromptTemplate": (
                "会話履歴と質問から、関連資料を探す検索クエリを作成してください。\n"
                "会話履歴:\n$conversation_history$\n質問: $query$\n$output_format_instructions$"
            )
        },
        "inferenceConfig": {"textInferenceConfig": {"maxTokens": 4096}},
    },
}


def model_arn(model_id: str, region: str, account: str) -> str:
    return f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}"


def answer_cases(agent_runtime: Any, *, kb_id: str, arn: str, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        config: dict[str, Any] = {"knowledgeBaseId": kb_id, "modelArn": arn, **PROMPTS}
        if case.get("filter"):
            config["retrievalConfiguration"] = {"vectorSearchConfiguration": {"filter": case["filter"]}}
        started = time.perf_counter()
        error, response = None, {}
        try:
            response = agent_runtime.retrieve_and_generate(
                input={"text": case["question"]},
                retrieveAndGenerateConfiguration={"type": "KNOWLEDGE_BASE", "knowledgeBaseConfiguration": config},
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        references = [reference_location(r) for r in extract_retrieved_references(response)]
        rows.append({
            "id": case["id"],
            "question": case["question"],
            "answer": response.get("output", {}).get("text", ""),
            "references": references,
            "expected_points": case.get("expected_points", []),
            "retrieved_contexts": _normalize_retrieved_contexts(_contexts_from_kb_response(response), case_id=case["id"]),
            "expected_source_hit_ratio": expected_source_hit_ratio(references, case.get("expected_sources")),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": error,
        })
        print(f"  {case['id']}: {rows[-1]['latency_ms'] / 1000:.1f}秒{' エラー' if error else ''}", flush=True)
    return rows


def judge_with_rubric(runtime: Any, *, judge_model_id: str, cases, rows, rubrics) -> None:
    by_id = {c["id"]: c for c in cases}
    for row in rows:
        result = judge_case(runtime, judge_model_id=judge_model_id, case=by_id[row["id"]],
                            answer=row["answer"], references=row["references"], rubrics=rubrics)
        row["scores"] = result["scores"]
        row["reason_by_metric"] = result.get("reason_by_metric", {})
        row["primary_failure_type"] = result.get("primary_failure_type", "none")


def judge_with_ragas(runtime: Any, *, judge_model_id: str, region: str, rows) -> None:
    llm = LangchainLLMWrapper(ChatBedrock(client=runtime, model_id=judge_model_id, region_name=region))
    completeness = AspectCritic(
        name="Request Completeness", llm=llm,
        definition="Return 1 if the response covers all required points. Otherwise return 0.",
    )
    groundedness = ResponseGroundedness(llm=llm, name="Groundedness")
    result = evaluate(dataset=build_dataset(rows), metrics=[completeness, groundedness],
                      run_config=RunConfig(timeout=180, max_retries=10, max_wait=60, max_workers=2),
                      batch_size=2).to_pandas()
    for row, (_, scores) in zip(rows, result.iterrows()):
        row["ragas"] = {"completeness": float(scores["Request Completeness"]),
                        "groundedness": float(scores["Groundedness"])}


def summarize(model_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(values):
        return round(statistics.mean(values), 3)
    return {
        "model": model_id,
        "errors": sum(1 for r in rows if r["error"]),
        "excel": {m: mean([r["scores"][m] for r in rows]) for m in ("correctness", "completeness", "groundedness")},
        "excel_full_score_cases": sum(1 for r in rows if all(v == 1.0 for v in r["scores"].values())),
        "ragas": {m: mean([r["ragas"][m] for r in rows]) for m in ("completeness", "groundedness")},
        "latency_seconds": mean([r["latency_ms"] / 1000 for r in rows]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--knowledge-base-id", default=os.environ.get("KNOWLEDGE_BASE_ID"), required=False)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--judge-model-id", default=os.environ.get("JUDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--workbook", type=Path, default=Path("data/eval_workbook.xlsx"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/answer_model_comparison"))
    args = parser.parse_args()
    if not args.knowledge_base_id:
        parser.error("--knowledge-base-id か KNOWLEDGE_BASE_ID を指定してください。")

    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    agent_runtime = session.client("bedrock-agent-runtime")
    runtime = session.client("bedrock-runtime", config=Config(retries={"mode": "adaptive", "max_attempts": 10}))
    cases = read_eval_cases(args.workbook)
    rubrics = read_rubrics(args.workbook)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_book = args.output_dir / "results.xlsx"

    summaries = []
    for model_id in args.models:
        print(f"{model_id}: {len(cases)}件に回答", flush=True)
        rows = answer_cases(agent_runtime, kb_id=args.knowledge_base_id,
                            arn=model_arn(model_id, args.region, account), cases=cases)
        print(f"{model_id}: Excelの採点基準で採点", flush=True)
        judge_with_rubric(runtime, judge_model_id=args.judge_model_id, cases=cases, rows=rows, rubrics=rubrics)
        print(f"{model_id}: RAGASで採点", flush=True)
        judge_with_ragas(runtime, judge_model_id=args.judge_model_id, region=args.region, rows=rows)
        write_results_sheet(result_book, rows, sheet_name=model_id.split(".")[-1][:31])
        (args.output_dir / f"{model_id}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        summaries.append(summarize(model_id, rows))
        print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)

    (args.output_dir / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {args.output_dir}")


if __name__ == "__main__":
    main()
