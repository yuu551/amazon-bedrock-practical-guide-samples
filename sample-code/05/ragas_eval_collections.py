#!/usr/bin/env python3
"""ragas_eval.pyを、RAGASの新しい書き方（v1.0で残る書き方）に移した版。

本文のragas_eval.pyは`ragas.metrics`のAspectCriticとLangchainLLMWrapperを使っており、
RAGAS 0.4.2以降では非推奨の警告が出る。この版では次のように置き換える。

  - 期待要点の網羅性：AspectCritic → DiscreteMetric（同じ指示文をプロンプトに書く）
  - 根拠との整合性：ragas.metrics.ResponseGroundedness → ragas.metrics.collections.ResponseGroundedness
  - 採点役のLLM：LangchainLLMWrapper → InstructorBaseRagasLLMを継承した接続クラス

  # 本文と同じくアプリのAPIやKnowledge Baseを呼んで採点する
  uv run python ragas_eval_collections.py --api-url "$APP_API_URL"
  # 保存済みの回答（compare_answer_models.pyの出力など）を採点し直す
  uv run python ragas_eval_collections.py --rows-json output/answer_model_comparison/xxx.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
from typing import Any

from ragas.metrics import DiscreteMetric
from ragas.metrics.collections import ResponseGroundedness

from ragas_agent_eval import BedrockStructuredLLM
from ragas_eval import _normalize_retrieved_contexts, collect_rows, collect_rows_from_kb

# 旧AspectCriticが内部で組み立てていた指示文と同じ内容を、プロンプトとして明示する
COMPLETENESS_PROMPT = (
    "Evaluate the Input based on the criteria defined. "
    "Use only 'pass' (1) and 'fail' (0) as verdict.\n"
    "Criteria Definition: Return pass if the response covers all required points. "
    "Otherwise return fail.\n\n"
    "user_input: {user_input}\n"
    "response: {response}\n"
    "retrieved_contexts: {retrieved_contexts}\n"
    "reference: {reference}"
)


def build_metrics(judge_llm: BedrockStructuredLLM) -> tuple[DiscreteMetric, ResponseGroundedness]:
    completeness = DiscreteMetric(
        name="Request Completeness",
        allowed_values=["pass", "fail"],
        prompt=COMPLETENESS_PROMPT,
    )
    groundedness = ResponseGroundedness(llm=judge_llm, name="Groundedness")
    return completeness, groundedness


async def score_row(row: dict[str, Any], judge_llm, completeness, groundedness) -> dict[str, float]:
    contexts = _normalize_retrieved_contexts(row.get("retrieved_contexts", []), case_id=row["id"])
    verdict = await completeness.ascore(
        llm=judge_llm,
        user_input=row["question"],
        response=row["answer"],
        retrieved_contexts=json.dumps(contexts, ensure_ascii=False),
        reference=" / ".join(row.get("expected_points", [])),
    )
    grounded = await groundedness.ascore(response=row["answer"], retrieved_contexts=contexts)
    return {
        "completeness": 1.0 if verdict.value == "pass" else 0.0,
        "groundedness": float(grounded.value),
    }


async def score_rows(rows, judge_llm, max_workers: int) -> list[dict[str, float]]:
    completeness, groundedness = build_metrics(judge_llm)
    gate = asyncio.Semaphore(max_workers)

    async def one(row):
        async with gate:
            return await score_row(row, judge_llm, completeness, groundedness)

    return await asyncio.gather(*(one(row) for row in rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default=os.environ.get("APP_API_URL"))
    parser.add_argument("--api-key", default=os.environ.get("APP_API_KEY"))
    parser.add_argument("--knowledge-base-id", default=os.environ.get("KNOWLEDGE_BASE_ID"))
    parser.add_argument("--retrieve-model-arn", default=os.environ.get("RETRIEVE_MODEL_ARN"))
    parser.add_argument("--rows-json", type=Path, help="保存済みの回答を採点し直す（API・KBは呼ばない）")
    parser.add_argument("--workbook", type=Path, default=Path("data/eval_workbook.xlsx"))
    parser.add_argument("--judge-model-id",
                        default=os.environ.get("RAGAS_JUDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-workers", type=int, default=int(os.environ.get("RAGAS_MAX_WORKERS", "2")))
    parser.add_argument("--output", type=Path, default=Path("output/ragas_scores_collections.json"))
    args = parser.parse_args()

    if args.rows_json:
        rows = json.loads(args.rows_json.read_text(encoding="utf-8"))
    elif args.knowledge_base_id:
        if not args.retrieve_model_arn:
            parser.error("--knowledge-base-id には --retrieve-model-arn も指定してください。")
        rows = collect_rows_from_kb(knowledge_base_id=args.knowledge_base_id, model_arn=args.retrieve_model_arn,
                                    workbook=args.workbook, region=args.region)
    elif args.api_url:
        rows = collect_rows(args.api_url, api_key=args.api_key, workbook=args.workbook, timeout=args.timeout)
    else:
        parser.error("--api-url、--knowledge-base-id、--rows-json のいずれかを指定してください。")

    judge_llm = BedrockStructuredLLM(model_id=args.judge_model_id, region=args.region, max_retries=10)
    scores = asyncio.run(score_rows(rows, judge_llm, args.max_workers))
    results = [{"id": row["id"], **score} for row, score in zip(rows, scores)]
    summary = {m: round(statistics.mean(r[m] for r in results), 4) for m in ("completeness", "groundedness")}
    print({"Request Completeness": summary["completeness"], "Groundedness": summary["groundedness"]})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote RAGAS scores to {args.output}")


if __name__ == "__main__":
    main()
