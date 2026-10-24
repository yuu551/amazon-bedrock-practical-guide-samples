#!/usr/bin/env python3
"""網羅型質問セットに対して、3方式のRAGを横並びで比較するスクリプト。

3方式:
  A_hybrid_filter         : RetrieveAndGenerate（hybrid）を1回呼ぶだけ
  B_managed_decomposition : マネージドの Query Decomposition を有効化
  D_topic_orchestration   : トピックフォルダ単位の網羅型RAG（orchestrate_topic_rag）

出力: run_dir / results.jsonl と summary.md
"""
from __future__ import annotations

import argparse
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

from common import (
    expected_sources_recall,
    extract_retrieved_references,
    judge_answer_completeness,
    knowledge_base_prompts,
    load_json,
    load_jsonl,
    markdown_table,
    reference_location,
    top_citation_hit,
    write_jsonl,
)
from orchestrate_topic_rag import (
    analyze_question as topic_analyze_question,
    extract_in_parallel,
    group_chunks_by_topic,
    list_topic_files,
    retrieve_chunks as topic_retrieve_chunks,
    synthesize_answer as topic_synthesize_answer,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare hybrid_filter / managed decomposition / topic orchestration."
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("sample-code/03/data/questions_topic_orch.jsonl"),
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
    )
    parser.add_argument(
        "--data-bucket",
        default=os.environ.get("TOPIC_DATA_BUCKET"),
        help="トピックフォルダを保存しているS3バケット名。",
    )
    parser.add_argument(
        "--topic-prefix",
        default=os.environ.get("TOPIC_DATA_PREFIX", "sample-data/topics/"),
    )
    parser.add_argument(
        "--topic-config",
        type=Path,
        default=Path("sample-code/03/data/topic_config.json"),
    )
    parser.add_argument(
        "--model-arn",
        default=os.environ.get("RETRIEVE_MODEL_ARN"),
        help="RetrieveAndGenerate用の最終回答モデルARN。",
    )
    parser.add_argument(
        "--analysis-model-id",
        default=os.environ.get("ANALYSIS_MODEL_ID"),
        help="トピック方式の質問分析モデルID（Sonnet推奨）。",
    )
    parser.add_argument(
        "--extraction-model-id",
        default=os.environ.get("EXTRACTION_MODEL_ID"),
        help="トピック方式のファイル抽出モデルID（Haiku推奨）。",
    )
    parser.add_argument(
        "--final-model-id",
        default=os.environ.get("FINAL_MODEL_ID"),
        help="トピック方式の統合モデルID（Sonnet推奨）。",
    )
    parser.add_argument(
        "--judge-model-id",
        default=os.environ.get("JUDGE_MODEL_ID"),
        help="回答完全性を簡易判定する軽量モデル。",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--search-type",
        default="HYBRID",
        choices=["HYBRID", "SEMANTIC"],
    )
    parser.add_argument(
        "--number-of-results",
        type=int,
        default=6,
    )
    parser.add_argument(
        "--retrieve-top-k",
        type=int,
        default=20,
        help="トピック方式のグルーピング用 retrieve 取得チャンク数。",
    )
    parser.add_argument(
        "--top-n-topics",
        type=int,
        default=2,
    )
    parser.add_argument(
        "--max-files-per-topic",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=8,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sample-code/03/output/compare_topic_orchestration"),
    )
    parser.add_argument(
        "--strategies",
        default="A,B,D",
        help="Comma-separated list of strategies (subset of A,B,D).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# A: ハイブリッド検索のみ
# ---------------------------------------------------------------------------


def run_hybrid_only(
    agent_runtime: Any,
    args: argparse.Namespace,
    question: dict[str, Any],
) -> dict[str, Any]:
    request = {
        "input": {"text": question["question"]},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": args.knowledge_base_id,
                "modelArn": args.model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {
                        "numberOfResults": args.number_of_results,
                        "overrideSearchType": args.search_type,
                    }
                },
                **knowledge_base_prompts(args.model_arn),
            },
        },
    }
    started = time.perf_counter()
    response = agent_runtime.retrieve_and_generate(**request)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    references = extract_retrieved_references(response)
    return {
        "answer": response.get("output", {}).get("text", ""),
        "citations": [reference_location(r) for r in references],
        "references": references,
        "latency_ms": latency_ms,
    }


# ---------------------------------------------------------------------------
# B: マネージドのクエリ分解
# ---------------------------------------------------------------------------


def run_managed_decomposition(
    agent_runtime: Any,
    args: argparse.Namespace,
    question: dict[str, Any],
) -> dict[str, Any]:
    prompts = knowledge_base_prompts(args.model_arn)
    request = {
        "input": {"text": question["question"]},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": args.knowledge_base_id,
                "modelArn": args.model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {
                        "numberOfResults": args.number_of_results,
                        "overrideSearchType": args.search_type,
                    }
                },
                **prompts,
                "orchestrationConfiguration": {
                    **prompts.get("orchestrationConfiguration", {}),
                    "queryTransformationConfiguration": {
                        "type": "QUERY_DECOMPOSITION",
                    },
                },
            },
        },
    }
    started = time.perf_counter()
    response = agent_runtime.retrieve_and_generate(**request)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    references = extract_retrieved_references(response)
    return {
        "answer": response.get("output", {}).get("text", ""),
        "citations": [reference_location(r) for r in references],
        "references": references,
        "latency_ms": latency_ms,
    }


# ---------------------------------------------------------------------------
# D: トピックフォルダ網羅型RAG
# ---------------------------------------------------------------------------


def run_topic_orchestration(
    agent_runtime: Any,
    runtime_client: Any,
    s3_client: Any,
    args: argparse.Namespace,
    topics: list[dict[str, Any]],
    question: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()

    plan = topic_analyze_question(
        runtime_client=runtime_client,
        model_id=args.analysis_model_id,
        question=question["question"],
        topics=topics,
    )
    chunks = topic_retrieve_chunks(
        agent_runtime_client=agent_runtime,
        knowledge_base_id=args.knowledge_base_id,
        query=plan.get("retrieval_query", question["question"]),
        top_k=args.retrieve_top_k,
        search_type=args.search_type,
    )
    candidates = group_chunks_by_topic(
        chunks=chunks,
        explicit_target_topics=plan.get("target_topics", []),
        top_n_topics=args.top_n_topics,
    )

    topic_extractions: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    citations: list[str] = []
    for cand in candidates:
        files = list_topic_files(
            s3_client=s3_client,
            bucket=args.data_bucket,
            topic_prefix=args.topic_prefix,
            topic_id=cand.topic_id,
            max_files=args.max_files_per_topic,
        )
        extractions = extract_in_parallel(
            runtime_client=runtime_client,
            model_id=args.extraction_model_id,
            question=question["question"],
            files=files,
            s3_client=s3_client,
            max_concurrency=args.max_concurrency,
        )
        topic_extractions.append({"topic_id": cand.topic_id, "extractions": extractions})
        for extraction in extractions:
            if not extraction.get("relevant_excerpt"):
                continue
            uri = f"s3://{args.data_bucket}/{extraction['s3_key']}"
            citations.append(uri)
            references.append({"location": {"s3Location": {"uri": uri}}})

    final_answer = topic_synthesize_answer(
        runtime_client=runtime_client,
        model_id=args.final_model_id,
        question=question["question"],
        plan=plan,
        topic_extractions=topic_extractions,
    )

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "answer": final_answer,
        "citations": citations,
        "references": references,
        "latency_ms": latency_ms,
        "plan": plan,
        "topic_candidates": [c.__dict__ for c in candidates],
        "topic_extractions": topic_extractions,
    }


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def summarize(results: list[dict[str, Any]]) -> str:
    by_experiment: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_experiment.setdefault(row["experiment"], []).append(row)

    rows: list[list[str]] = []
    for name in sorted(by_experiment.keys()):
        experiment_rows = by_experiment[name]
        success = [r for r in experiment_rows if not r.get("error")]
        lat = [r["latency_ms"] for r in success]
        rec = [r["source_recall"] for r in success if r["source_recall"] is not None]
        top = [r["top_citation_hit"] for r in success if r["top_citation_hit"] is not None]
        comp = [r["completeness"] for r in success if r["completeness"] is not None]
        cit = [r["citation_count"] for r in success]

        rows.append(
            [
                name,
                str(len(success)),
                f"{statistics.mean(lat):.1f}" if lat else "-",
                f"{statistics.mean(rec):.2f}" if rec else "-",
                f"{sum(1 for t in top if t) / len(top):.2f}" if top else "-",
                f"{statistics.mean(comp):.2f}" if comp else "-",
                f"{statistics.mean(cit):.1f}" if cit else "-",
            ]
        )

    return "\n".join(
        [
            "# Topic Orchestration Comparison",
            "",
            markdown_table(
                [
                    "Experiment",
                    "Runs",
                    "Avg Latency (ms)",
                    "Avg Source Recall",
                    "最初の引用が期待文書だった割合",
                    "Avg Completeness",
                    "Avg Citations",
                ],
                rows,
            ),
            "",
            "- A_hybrid_only: ハイブリッド検索でRetrieveAndGenerateを1回",
            "- B_managed_decomposition: マネージドのクエリ分解を有効化",
            "- D_topic_orchestration: トピックフォルダを全読みするMapReduce型",
        ]
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    missing = [
        name
        for name, value in [
            ("--knowledge-base-id", args.knowledge_base_id),
            ("--model-arn", args.model_arn),
            ("--data-bucket", args.data_bucket),
            ("--analysis-model-id", args.analysis_model_id),
            ("--extraction-model-id", args.extraction_model_id),
            ("--final-model-id", args.final_model_id),
        ]
        if not value
    ]
    if missing:
        raise SystemExit("Missing required args: " + ", ".join(missing))

    questions = load_jsonl(args.questions)
    topic_config = load_json(args.topic_config)
    topics = topic_config["topics"]

    agent_runtime = boto3.client("bedrock-agent-runtime", region_name=args.region)
    runtime_client = boto3.client("bedrock-runtime", region_name=args.region)
    s3_client = boto3.client("s3", region_name=args.region)
    judge_client = (
        boto3.client("bedrock-runtime", region_name=args.region)
        if args.judge_model_id
        else None
    )

    started_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir / started_at
    results: list[dict[str, Any]] = []

    all_experiments = {
        "A": ("A_hybrid_only", lambda q: run_hybrid_only(agent_runtime, args, q)),
        "B": ("B_managed_decomposition", lambda q: run_managed_decomposition(agent_runtime, args, q)),
        "D": ("D_topic_orchestration", lambda q: run_topic_orchestration(
            agent_runtime, runtime_client, s3_client, args, topics, q
        )),
    }
    selected = [s.strip().upper() for s in args.strategies.split(",") if s.strip()]
    unknown = [s for s in selected if s not in all_experiments]
    if unknown:
        raise SystemExit(f"Unknown strategies: {unknown}. Choose from A, B, D.")
    experiments = [all_experiments[s] for s in selected]
    print(f"Running strategies: {[name for name, _ in experiments]}")

    for name, runner in experiments:
        for question in questions:
            print(f"  [{name}] {question.get('id')}: {question['question'][:50]}...")
            try:
                outcome = runner(question)
                error = None
            except Exception as exc:  # noqa: BLE001
                outcome = {"answer": "", "citations": [], "references": [], "latency_ms": None}
                error = str(exc)

            references = outcome.get("references") or []
            expected_sources = question.get("expected_sources")
            recall = expected_sources_recall(references, expected_sources)
            top_hit = top_citation_hit(references, expected_sources)

            answer = outcome.get("answer", "")
            completeness = (
                judge_answer_completeness(
                    judge_client,
                    args.judge_model_id,
                    question["question"],
                    answer,
                    question.get("expected_keywords"),
                )
                if judge_client and answer and error is None
                else None
            )

            row = {
                "experiment": name,
                "question_id": question.get("id"),
                "question": question["question"],
                "intent": question.get("intent"),
                "topic_id": question.get("topic_id"),
                "latency_ms": outcome.get("latency_ms"),
                "source_recall": recall,
                "top_citation_hit": top_hit,
                "completeness": completeness,
                "citation_count": len(outcome.get("citations", [])),
                "citations": outcome.get("citations", []),
                "answer": answer,
                "error": error,
            }
            if "plan" in outcome:
                row["plan"] = outcome["plan"]
            if "topic_candidates" in outcome:
                row["topic_candidates"] = outcome["topic_candidates"]
            results.append(row)

    write_jsonl(run_dir / "results.jsonl", results)
    (run_dir / "summary.md").write_text(summarize(results), encoding="utf-8")

    print()
    print(f"Wrote {len(results)} rows to {run_dir / 'results.jsonl'}")
    print(f"Wrote summary to {run_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
