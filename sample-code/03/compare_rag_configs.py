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
    expected_sources_recall,
    extract_retrieved_references,
    judge_answer_completeness,
    keyword_score,
    knowledge_base_prompts,
    load_json,
    load_jsonl,
    markdown_table,
    reference_location,
    top_citation_hit,
    write_jsonl,
)


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    description: str
    search_type: str | None = None
    use_question_filter: bool = False
    use_rerank: bool = False
    use_query_decomposition: bool = False
    use_implicit_filter: bool = False
    number_of_results: int = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Knowledge Bases retrieval configurations for Chapter 3."
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path("sample-code/03/data/questions.jsonl"),
        help="JSONL file with question fixtures.",
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
        help="Knowledge Base ID.",
    )
    parser.add_argument(
        "--model-arn",
        default=os.environ.get("RETRIEVE_MODEL_ARN"),
        help="Foundation model ARN or inference profile ARN for RetrieveAndGenerate.",
    )
    parser.add_argument(
        "--rerank-model-arn",
        default=os.environ.get("RERANK_MODEL_ARN"),
        help="Optional reranker model ARN.",
    )
    parser.add_argument(
        "--implicit-filter-model-arn",
        default=os.environ.get("IMPLICIT_FILTER_MODEL_ARN"),
        help="Optional model ARN for implicit metadata filtering.",
    )
    parser.add_argument(
        "--implicit-filter-schema",
        type=Path,
        default=Path("sample-code/03/data/metadata_schema.json"),
        help="Metadata schema used when implicit filtering is enabled.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS Region for the runtime client.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sample-code/03/output"),
        help="Directory for JSONL and Markdown summaries.",
    )
    parser.add_argument(
        "--judge-model-id",
        default=os.environ.get("JUDGE_MODEL_ID"),
        help=(
            "Optional lightweight model for answer completeness scoring. "
            "If omitted, the completeness column is left blank."
        ),
    )
    parser.add_argument(
        "--vector-store",
        choices=["opensearch", "s3vectors"],
        default=os.environ.get("VECTOR_STORE", "opensearch"),
        help=(
            "ベクトルストア種別。s3vectors はハイブリッド検索を使えないため、"
            "HYBRID系のexperimentを自動スキップし、セマンティック単独の構成に切り替えます。"
        ),
    )
    return parser.parse_args()


def default_experiments(args: argparse.Namespace) -> list[ExperimentConfig]:
    is_s3vectors = args.vector_store == "s3vectors"
    experiments = [
        ExperimentConfig(
            name="1_baseline",
            description="第2章そのままのセマンティック検索（フィルタ・リランキングなし）。",
        ),
        ExperimentConfig(
            name="2_semantic_filter",
            description="セマンティック検索にメタデータフィルタを追加。古い版や対象外部門を除外できる。",
            use_question_filter=True,
        ),
    ]

    if not is_s3vectors:
        experiments.append(
            ExperimentConfig(
                name="3_hybrid_filter",
                description="ハイブリッド検索＋フィルタ。固有名詞やキーワードの一致精度が上がる。",
                search_type="HYBRID",
                use_question_filter=True,
            )
        )

    if args.rerank_model_arn:
        if is_s3vectors:
            experiments.append(
                ExperimentConfig(
                    name="3_semantic_filter_rerank",
                    description="セマンティック＋フィルタ＋リランキング（S3 Vectors版）。",
                    use_question_filter=True,
                    use_rerank=True,
                )
            )
            experiments.append(
                ExperimentConfig(
                    name="4_semantic_filter_rerank_decompose",
                    description="セマンティック＋フィルタ＋リランキング＋クエリ分解（S3 Vectors版）。",
                    use_question_filter=True,
                    use_rerank=True,
                    use_query_decomposition=True,
                )
            )
        else:
            experiments.append(
                ExperimentConfig(
                    name="4_hybrid_filter_rerank",
                    description="ハイブリッド＋フィルタ＋リランキング。上位順位の精度が高まる。",
                    search_type="HYBRID",
                    use_question_filter=True,
                    use_rerank=True,
                )
            )
            experiments.append(
                ExperimentConfig(
                    name="5_all_techniques",
                    description="リランキング後にクエリ分解を追加。比較質問や複合質問で複数ソースを引きやすくなる。",
                    search_type="HYBRID",
                    use_question_filter=True,
                    use_rerank=True,
                    use_query_decomposition=True,
                )
            )
    else:
        if is_s3vectors:
            experiments.append(
                ExperimentConfig(
                    name="3_semantic_filter_decompose",
                    description="セマンティック＋フィルタ＋クエリ分解（S3 Vectors版、リランカー未指定）。",
                    use_question_filter=True,
                    use_query_decomposition=True,
                )
            )
        else:
            experiments.append(
                ExperimentConfig(
                    name="4_hybrid_filter_decompose",
                    description="ハイブリッド＋フィルタ＋クエリ分解（リランキング未使用時のフォールバック）。",
                    search_type="HYBRID",
                    use_question_filter=True,
                    use_query_decomposition=True,
                )
            )

    if args.implicit_filter_model_arn:
        experiments.append(
            ExperimentConfig(
                name="X_implicit_filter",
                description="モデルが質問から推論したメタデータフィルタを利用。",
                search_type="HYBRID",
                use_implicit_filter=True,
            )
        )

    return experiments


def build_request(
    args: argparse.Namespace,
    question: dict[str, Any],
    experiment: ExperimentConfig,
    implicit_filter_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    vector_search_configuration: dict[str, Any] = {
        "numberOfResults": experiment.number_of_results,
    }

    if experiment.search_type:
        vector_search_configuration["overrideSearchType"] = experiment.search_type

    if experiment.use_question_filter and question.get("filter"):
        vector_search_configuration["filter"] = question["filter"]

    # フィルタが null の質問ではキーを落とす
    if vector_search_configuration.get("filter") is None:
        vector_search_configuration.pop("filter", None)

    if experiment.use_rerank and args.rerank_model_arn:
        vector_search_configuration["rerankingConfiguration"] = {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": args.rerank_model_arn},
                "numberOfRerankedResults": min(experiment.number_of_results, 5),
            },
        }

    if (
        experiment.use_implicit_filter
        and args.implicit_filter_model_arn
        and implicit_filter_schema
    ):
        metadata_attributes = []
        for attribute in implicit_filter_schema.get("attributes", []):
            metadata_attributes.append(
                {
                    "key": attribute["key"],
                    "type": attribute["type"],
                    "description": attribute["description"],
                }
            )
        vector_search_configuration["implicitFilterConfiguration"] = {
            "metadataAttributes": metadata_attributes,
            "modelArn": args.implicit_filter_model_arn,
        }

    knowledge_base_configuration: dict[str, Any] = {
        "knowledgeBaseId": args.knowledge_base_id,
        "modelArn": args.model_arn,
        "retrievalConfiguration": {
            "vectorSearchConfiguration": vector_search_configuration,
        },
    }

    knowledge_base_configuration.update(knowledge_base_prompts(args.model_arn))
    if experiment.use_query_decomposition:
        knowledge_base_configuration["orchestrationConfiguration"] = {
            **knowledge_base_configuration.get("orchestrationConfiguration", {}),
            "queryTransformationConfiguration": {
                "type": "QUERY_DECOMPOSITION"
            },
        }

    return {
        "input": {"text": question["question"]},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": knowledge_base_configuration,
        },
    }


_SUMMARY_HEADER = [
    "Experiment",
    "Runs",
    "Avg Latency (ms)",
    "Avg Keyword Score",
    "Avg Source Recall",
    "最初の引用が期待文書だった割合",
    "Avg Completeness",
    "Avg Citations",
]


def _aggregate_rows(rows: list[dict[str, Any]]) -> list[list[str]]:
    by_experiment: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_experiment.setdefault(row["experiment"], []).append(row)

    aggregated: list[list[str]] = []
    for experiment_name, experiment_rows in sorted(by_experiment.items()):
        success_rows = [row for row in experiment_rows if not row.get("error")]
        latencies = [row["latency_ms"] for row in success_rows]
        keyword_scores = [
            row["keyword_score"]
            for row in success_rows
            if row["keyword_score"] is not None
        ]
        source_recalls = [
            row["source_recall"]
            for row in success_rows
            if row["source_recall"] is not None
        ]
        top_hits = [
            row["top_citation_hit"]
            for row in success_rows
            if row["top_citation_hit"] is not None
        ]
        completeness_scores = [
            row["completeness"]
            for row in success_rows
            if row["completeness"] is not None
        ]
        citation_counts = [row["citation_count"] for row in success_rows]

        aggregated.append(
            [
                experiment_name,
                str(len(success_rows)),
                f"{statistics.mean(latencies):.1f}" if latencies else "-",
                f"{statistics.mean(keyword_scores):.2f}" if keyword_scores else "-",
                (
                    f"{statistics.mean(source_recalls):.2f}"
                    if source_recalls
                    else "-"
                ),
                (
                    f"{sum(1 for hit in top_hits if hit) / len(top_hits):.2f}"
                    if top_hits
                    else "-"
                ),
                (
                    f"{statistics.mean(completeness_scores):.2f}"
                    if completeness_scores
                    else "-"
                ),
                (
                    f"{statistics.mean(citation_counts):.1f}"
                    if citation_counts
                    else "-"
                ),
            ]
        )
    return aggregated


def summarize(results: list[dict[str, Any]]) -> str:
    sections: list[str] = [
        "# Chapter 3 Retrieval Comparison Summary",
        "",
        "## 全質問平均",
        "",
        markdown_table(_SUMMARY_HEADER, _aggregate_rows(results)),
    ]

    # tagsサブセット別の集計（質問にtagsがあるときだけ出力）
    all_tags: set[str] = set()
    for row in results:
        for tag in row.get("tags") or []:
            all_tags.add(tag)

    if all_tags:
        sections.append("")
        sections.append("## tagsサブセット別平均")
        sections.append("")
        sections.append(
            "改善テクニック別に、対象質問だけで集計した結果。tags は `questions.jsonl` 側で定義。"
        )
        for tag in sorted(all_tags):
            tagged_rows = [r for r in results if tag in (r.get("tags") or [])]
            unique_qids = {r.get("question_id") for r in tagged_rows}
            sections.append("")
            sections.append(f"### tag = `{tag}` ({len(unique_qids)}問)")
            sections.append("")
            sections.append(markdown_table(_SUMMARY_HEADER, _aggregate_rows(tagged_rows)))

    sections.extend(
        [
            "",
            "- Keyword Score: 回答本文に期待キーワードがどれだけ含まれるか（0-1）",
            "- Source Recall: 期待ソースのうち引用に現れた割合（0-1）",
            "- 最初の引用が期待文書だった割合: 最初に引用された文書が期待ソースである割合（0-1）",
            "- Completeness: 軽量LLM-as-Judgeによる回答完全性（0 / 0.5 / 1）",
            "",
            "5章では本格的なLLM-as-Judge評価を扱います。",
        ]
    )
    return "\n".join(sections)


def main() -> None:
    args = parse_args()

    if not args.knowledge_base_id or not args.model_arn:
        raise SystemExit(
            "Both --knowledge-base-id and --model-arn are required. "
            "You can also provide them via KNOWLEDGE_BASE_ID and RETRIEVE_MODEL_ARN."
        )

    questions = load_jsonl(args.questions)
    implicit_filter_schema = None
    if args.implicit_filter_model_arn and args.implicit_filter_schema.exists():
        implicit_filter_schema = load_json(args.implicit_filter_schema)

    runtime_client = boto3.client("bedrock-agent-runtime", region_name=args.region)
    judge_client = (
        boto3.client("bedrock-runtime", region_name=args.region)
        if args.judge_model_id
        else None
    )
    experiments = default_experiments(args)

    started_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir / started_at
    results: list[dict[str, Any]] = []

    for experiment in experiments:
        for question in questions:
            request = build_request(args, question, experiment, implicit_filter_schema)
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
            expected_sources = question.get("expected_sources") or (
                [question["expected_source_substring"]]
                if question.get("expected_source_substring")
                else None
            )
            keyword_metric = keyword_score(answer, question.get("expected_keywords"))
            source_hit = expected_source_hit(
                references, question.get("expected_source_substring")
            )
            source_recall = expected_sources_recall(references, expected_sources)
            top_hit = top_citation_hit(references, expected_sources)
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

            results.append(
                {
                    "question_id": question.get("id"),
                    "title": question.get("title"),
                    "tags": question.get("tags") or [],
                    "question": question["question"],
                    "intent": question.get("intent"),
                    "experiment": experiment.name,
                    "description": experiment.description,
                    "latency_ms": latency_ms,
                    "keyword_score": keyword_metric,
                    "expected_source_hit": source_hit,
                    "source_recall": source_recall,
                    "top_citation_hit": top_hit,
                    "completeness": completeness,
                    "citation_count": len(references),
                    "citations": [reference_location(reference) for reference in references],
                    "answer": answer,
                    "error": error,
                }
            )

    write_jsonl(run_dir / "results.jsonl", results)
    (run_dir / "summary.md").write_text(summarize(results), encoding="utf-8")

    print(f"Wrote {len(results)} result rows to {run_dir / 'results.jsonl'}")
    print(f"Wrote summary to {run_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
