#!/usr/bin/env python3
"""S3 VectorsとOpenSearch Serverlessを同一の文書群で比較する。"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

from rag_answer_quality import METRICS, evaluate_answer


EXTRA_QUESTIONS = [
    {
        "id": "E01",
        "question": "ログインできない",
        "expected_keywords": ["IdP", "ACS URL", "証明書"],
        "expected_sources": ["faq/SAML_SSOでログインできない場合の確認項目"],
        "tags": ["short-query"],
    },
    {
        "id": "E02",
        "question": "認証が通らなくて画面が戻される",
        "expected_keywords": ["IdP", "ACS URL", "証明書"],
        "expected_sources": ["faq/SAML_SSOでログインできない場合の確認項目"],
        "tags": ["paraphrase"],
    },
    {
        "id": "E03",
        "question": "クレジット申請いつまで",
        "expected_keywords": ["障害発生月", "翌月末", "サポート窓口"],
        "expected_sources": ["faq/サービスクレジットの申請期限"],
        "tags": ["short-query"],
    },
    {
        "id": "E04",
        "question": "Enterpriseプランで利用できる機能を教えてください",
        "expected_keywords": ["SAML SSO", "監査ログ", "IPアドレス制限"],
        "expected_sources": ["product/Enterpriseプラン機能ガイド"],
        "tags": ["direct"],
    },
    {
        "id": "E05",
        "question": "障害発生時の顧客向け初回案内のテンプレートを教えてください",
        "expected_keywords": ["受け付け", "既知事象", "次回の状況更新"],
        "expected_sources": ["faq/障害発生時の顧客向け初回案内"],
        "tags": ["direct"],
    },
]

VARIANTS = {
    "opensearch_semantic": "OpenSearch Serverless（セマンティック検索）",
    "opensearch_hybrid": "OpenSearch Serverless（ハイブリッド検索）",
    "s3vectors_semantic": "S3 Vectors（セマンティック検索）",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opensearch-kb-id", required=True)
    parser.add_argument("--opensearch-data-source-id", required=True)
    parser.add_argument("--s3vectors-kb-id", required=True)
    parser.add_argument("--s3vectors-data-source-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--expected-document-count", type=int, default=33)
    parser.add_argument("--number-of-results", type=int, default=5)
    parser.add_argument(
        "--answer-model-id",
        help="指定時は、各構成の検索結果から同じモデルとプロンプトで回答を生成する。",
    )
    parser.add_argument(
        "--judge-model-id",
        help="回答品質を採点するモデル。--answer-model-idと同時に指定する。",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "03" / "data" / "questions.jsonl",
    )
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def latest_ingestion_job(agent: Any, kb_id: str, data_source_id: str) -> dict[str, Any]:
    jobs = agent.list_ingestion_jobs(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
        maxResults=10,
    ).get("ingestionJobSummaries", [])
    if not jobs:
        raise RuntimeError(f"取り込みジョブがありません: {kb_id}/{data_source_id}")
    return max(jobs, key=lambda job: job["startedAt"])


def validate_ingestion(
    agent: Any,
    kb_id: str,
    data_source_id: str,
    expected_count: int,
) -> dict[str, Any]:
    job = latest_ingestion_job(agent, kb_id, data_source_id)
    stats = job.get("statistics", {})
    if job.get("status") != "COMPLETE":
        raise RuntimeError(f"取り込みが完了していません: {job}")
    if stats.get("numberOfDocumentsFailed") != 0:
        raise RuntimeError(f"取り込み失敗があります: {job}")
    if stats.get("numberOfDocumentsScanned") != expected_count:
        raise RuntimeError(f"文書数が{expected_count}件ではありません: {job}")
    return job


def data_source_location(
    agent: Any, kb_id: str, data_source_id: str
) -> tuple[str, str]:
    data_source = agent.get_data_source(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
    )["dataSource"]
    config = data_source["dataSourceConfiguration"]
    if config["type"] != "S3":
        raise RuntimeError(f"S3データソースではありません: {kb_id}/{data_source_id}")
    s3_config = config["s3Configuration"]
    prefixes = s3_config.get("inclusionPrefixes", [""])
    if len(prefixes) != 1:
        raise RuntimeError(f"比較用データソースのprefixは1つにしてください: {prefixes}")
    return s3_config["bucketArn"].split(":::", 1)[1], prefixes[0]


def s3_manifest(s3: Any, bucket: str, prefix: str) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            manifest[key[len(prefix) :]] = {
                "size": obj["Size"],
                "etag": obj["ETag"].strip('"'),
            }
    return manifest


def validate_corpus(
    opensearch_manifest: dict[str, dict[str, Any]],
    s3vectors_manifest: dict[str, dict[str, Any]],
    expected_count: int,
) -> list[str]:
    for label, manifest in (
        ("OpenSearch Serverless", opensearch_manifest),
        ("S3 Vectors", s3vectors_manifest),
    ):
        documents = [key for key in manifest if not key.endswith(".metadata.json")]
        if len(documents) != expected_count or any(
            not key.endswith(".md") for key in documents
        ):
            raise RuntimeError(
                f"{label}の検索対象がMarkdown {expected_count}文書ではありません"
            )
    if opensearch_manifest != s3vectors_manifest:
        raise RuntimeError(
            "両Knowledge BaseのS3オブジェクト一覧または内容が一致しません"
        )
    return sorted(key for key in opensearch_manifest if key.endswith(".md"))


def validate_knowledge_bases(
    agent: Any,
    s3vectors: Any,
    opensearch_kb_id: str,
    s3vectors_kb_id: str,
) -> dict[str, Any]:
    opensearch = agent.get_knowledge_base(knowledgeBaseId=opensearch_kb_id)[
        "knowledgeBase"
    ]
    s3 = agent.get_knowledge_base(knowledgeBaseId=s3vectors_kb_id)["knowledgeBase"]
    if (
        opensearch.get("storageConfiguration", {}).get("type")
        != "OPENSEARCH_SERVERLESS"
    ):
        raise RuntimeError(
            f"{opensearch_kb_id}はOpenSearch Serverless KBではありません"
        )
    if s3.get("storageConfiguration", {}).get("type") != "S3_VECTORS":
        raise RuntimeError(f"{s3vectors_kb_id}はS3 Vectors KBではありません")
    opensearch_model = opensearch["knowledgeBaseConfiguration"][
        "vectorKnowledgeBaseConfiguration"
    ]["embeddingModelArn"]
    s3_model = s3["knowledgeBaseConfiguration"]["vectorKnowledgeBaseConfiguration"][
        "embeddingModelArn"
    ]
    if opensearch_model != s3_model:
        raise RuntimeError("埋め込みモデルが一致しません")

    index_arn = s3["storageConfiguration"]["s3VectorsConfiguration"]["indexArn"]
    vector_bucket, index_name = index_arn.split(":bucket/", 1)[1].split("/index/", 1)
    index = s3vectors.get_index(
        vectorBucketName=vector_bucket,
        indexName=index_name,
    )["index"]
    actual_keys = set(
        index.get("metadataConfiguration", {}).get("nonFilterableMetadataKeys", [])
    )
    required_keys = {"AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"}
    if not required_keys.issubset(actual_keys):
        raise RuntimeError(
            f"S3 Vector Indexのnon-filterable設定が不足しています: {actual_keys}"
        )
    return {"opensearch": opensearch, "s3vectors": s3, "s3_vector_index": index}


def normalize_source(source: str) -> str:
    return urllib.parse.unquote(source).rsplit("/", 1)[-1].removesuffix(".md")


def normalize_results(response: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for result in response.get("retrievalResults", []):
        uri = result.get("location", {}).get("s3Location", {}).get("uri", "")
        results.append(
            {
                "file": urllib.parse.unquote(uri).rsplit("/", 1)[-1],
                "score": round(float(result.get("score", 0)), 4),
                "uri": uri,
                "text": result.get("content", {}).get("text", ""),
            }
        )
    return results


def retrieve(
    runtime: Any,
    kb_id: str,
    query: str,
    search_type: str,
    number_of_results: int,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    response = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": number_of_results,
                "overrideSearchType": search_type,
            }
        },
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return normalize_results(response), latency_ms


def score_results(
    results: list[dict[str, Any]], expected_sources: list[str]
) -> dict[str, Any]:
    expected = {normalize_source(source) for source in expected_sources}
    actual = [normalize_source(result["file"]) for result in results]
    found = expected.intersection(actual)
    return {
        "contains_any_expected_source": bool(found),
        "contains_all_expected_sources": found == expected,
        "expected_source_coverage": round(len(found) / len(expected), 4),
        "results": results,
    }


def evaluate(
    runtime: Any,
    model_runtime: Any,
    args: argparse.Namespace,
    questions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    totals = {
        name: {
            "contains_any": 0,
            "contains_all": 0,
            "source_coverage": [],
            "latency_ms": [],
            **{metric: [] for metric in METRICS},
        }
        for name in VARIANTS
    }
    rows = []
    for question in questions:
        variants = {}
        for name, kb_id, search_type in (
            ("opensearch_semantic", args.opensearch_kb_id, "SEMANTIC"),
            ("opensearch_hybrid", args.opensearch_kb_id, "HYBRID"),
            ("s3vectors_semantic", args.s3vectors_kb_id, "SEMANTIC"),
        ):
            results, latency_ms = retrieve(
                runtime,
                kb_id,
                question["question"],
                search_type,
                args.number_of_results,
            )
            scored = score_results(results, question["expected_sources"])
            scored["latency_ms"] = latency_ms
            if args.answer_model_id:
                scored["answer_quality"] = evaluate_answer(
                    model_runtime,
                    answer_model_id=args.answer_model_id,
                    judge_model_id=args.judge_model_id,
                    question=question,
                    results=results,
                )
            variants[name] = scored
            totals[name]["contains_any"] += int(scored["contains_any_expected_source"])
            totals[name]["contains_all"] += int(scored["contains_all_expected_sources"])
            totals[name]["source_coverage"].append(scored["expected_source_coverage"])
            totals[name]["latency_ms"].append(latency_ms)
            if "answer_quality" in scored:
                for metric in METRICS:
                    totals[name][metric].append(
                        scored["answer_quality"]["judge"]["scores"][metric]
                    )
        rows.append(
            {
                "id": question["id"],
                "question": question["question"],
                "tags": question.get("tags", []),
                "expected_sources": question["expected_sources"],
                "variants": variants,
            }
        )
        print(
            f"{question['id']:<4} "
            + " ".join(
                f"{name}={data['results'][0]['file'] if data['results'] else 'N/A'}"
                for name, data in variants.items()
            )
        )

    summary = {"total": len(questions), "variants": {}}
    for name, values in totals.items():
        summary["variants"][name] = {
            "contains_any_expected_source": values["contains_any"],
            "contains_all_expected_sources": values["contains_all"],
            "mean_expected_source_coverage": round(
                statistics.mean(values["source_coverage"]), 4
            ),
            "median_latency_ms": round(statistics.median(values["latency_ms"]), 1),
        }
        if values["correctness"]:
            summary["variants"][name]["answer_quality"] = {
                metric: round(statistics.mean(values[metric]), 3)
                for metric in METRICS
            }
    return rows, summary


def tag_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for row in rows:
        for raw_tag in row["tags"]:
            tag = (
                "metadata-filter" if raw_tag.startswith("metadata-filter") else raw_tag
            )
            current = stats.setdefault(
                tag,
                {
                    "n": 0,
                    **{
                        name: {"contains_any": 0, "contains_all": 0}
                        for name in VARIANTS
                    },
                },
            )
            current["n"] += 1
            for name in VARIANTS:
                current[name]["contains_any"] += int(
                    row["variants"][name]["contains_any_expected_source"]
                )
                current[name]["contains_all"] += int(
                    row["variants"][name]["contains_all_expected_sources"]
                )
    return stats


def summary_markdown(payload: dict[str, Any]) -> str:
    total = payload["summary"]["total"]
    lines = [
        "# S3 Vectors / OpenSearch Serverless 検索比較",
        "",
        f"- 実行日時: {payload['timestamp']}",
        f"- 検索対象: 同一のMarkdown {len(payload['corpus']['documents'])}文書",
        f"- 埋め込みモデル: `{payload['embedding_model_arn']}`",
        f"- OpenSearch Serverless KB: `{payload['opensearch_kb_id']}`",
        f"- S3 Vectors KB: `{payload['s3vectors_kb_id']}`",
        "- メタデータフィルター: 比較条件から除外",
        "- リランキング: 比較条件から除外",
        "",
        "| 構成 | 取得した5件に期待文書を含む質問 | 期待文書がすべて揃った質問 | 期待文書の取得率（平均） | レイテンシー中央値 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, label in VARIANTS.items():
        result = payload["summary"]["variants"][name]
        lines.append(
            f"| {label} | {result['contains_any_expected_source']}/{total} "
            f"({result['contains_any_expected_source'] / total:.1%}) "
            f"| {result['contains_all_expected_sources']}/{total} "
            f"({result['contains_all_expected_sources'] / total:.1%}) "
            f"| {result['mean_expected_source_coverage']:.1%} "
            f"| {result['median_latency_ms']:.1f} ms |"
        )
    if payload.get("answer_model_id"):
        lines.extend(
            [
                "",
                "## 生成回答の評価",
                "",
                f"- 回答生成モデル: `{payload['answer_model_id']}`",
                f"- 判定モデル: `{payload['judge_model_id']}`",
                "",
                "| 構成 | 正確性 | 完全性 | 根拠性 |",
                "|---|---:|---:|---:|",
            ]
        )
        for name, label in VARIANTS.items():
            scores = payload["summary"]["variants"][name]["answer_quality"]
            lines.append(
                f"| {label} | {scores['correctness']:.3f} "
                f"| {scores['completeness']:.3f} | {scores['groundedness']:.3f} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    if bool(args.answer_model_id) != bool(args.judge_model_id):
        raise SystemExit(
            "--answer-model-idと--judge-model-idは同時に指定してください。"
        )
    now = datetime.now(timezone.utc)
    output_dir = args.output_dir or (
        Path(__file__).resolve().parent
        / "logs"
        / "20260825-vector-store-comparison"
        / f"{now:%Y%m%dT%H%M%SZ}"
    )
    agent = boto3.client("bedrock-agent", region_name=args.region)
    runtime = boto3.client("bedrock-agent-runtime", region_name=args.region)
    model_runtime = boto3.client("bedrock-runtime", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)
    s3vectors = boto3.client("s3vectors", region_name=args.region)

    knowledge_bases = validate_knowledge_bases(
        agent,
        s3vectors,
        args.opensearch_kb_id,
        args.s3vectors_kb_id,
    )
    ingestion = {
        "opensearch": validate_ingestion(
            agent,
            args.opensearch_kb_id,
            args.opensearch_data_source_id,
            args.expected_document_count,
        ),
        "s3vectors": validate_ingestion(
            agent,
            args.s3vectors_kb_id,
            args.s3vectors_data_source_id,
            args.expected_document_count,
        ),
    }
    opensearch_location = data_source_location(
        agent, args.opensearch_kb_id, args.opensearch_data_source_id
    )
    s3vectors_location = data_source_location(
        agent, args.s3vectors_kb_id, args.s3vectors_data_source_id
    )
    opensearch_manifest = s3_manifest(s3, *opensearch_location)
    s3vectors_manifest = s3_manifest(s3, *s3vectors_location)
    documents = validate_corpus(
        opensearch_manifest,
        s3vectors_manifest,
        args.expected_document_count,
    )

    questions = [
        json.loads(line)
        for line in args.questions.read_text().splitlines()
        if line.strip()
    ]
    questions.extend(EXTRA_QUESTIONS)
    rows, summary = evaluate(runtime, model_runtime, args, questions)
    embedding_model_arn = knowledge_bases["opensearch"]["knowledgeBaseConfiguration"][
        "vectorKnowledgeBaseConfiguration"
    ]["embeddingModelArn"]
    payload = {
        "timestamp": now.isoformat(),
        "region": args.region,
        "opensearch_kb_id": args.opensearch_kb_id,
        "opensearch_data_source_id": args.opensearch_data_source_id,
        "s3vectors_kb_id": args.s3vectors_kb_id,
        "s3vectors_data_source_id": args.s3vectors_data_source_id,
        "embedding_model_arn": embedding_model_arn,
        "answer_model_id": args.answer_model_id,
        "judge_model_id": args.judge_model_id,
        "knowledge_bases": knowledge_bases,
        "ingestion": ingestion,
        "corpus": {
            "documents": documents,
            "opensearch_location": opensearch_location,
            "s3vectors_location": s3vectors_location,
            "manifest": opensearch_manifest,
        },
        "summary": summary,
        "tag_statistics": tag_statistics(rows),
        "results": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "comparison-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    (output_dir / "summary.md").write_text(summary_markdown(payload))
    print(f"\n保存先: {output_dir}")


if __name__ == "__main__":
    main()
