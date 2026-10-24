#!/usr/bin/env python3
"""Managed KBとCustomer-managed KBを、同一の文書群で比較する。

比較前に取り込み統計、S3オブジェクト一覧、S3 Vector Indexの
non-filterableキーを検証する。条件が一致しない場合は検索を実行しない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
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

HARD_CASES = [
    {"question": "SLAポリシー2026を教えて", "expected": "SLAポリシー2026年版.md"},
    {"question": "429", "expected": "APIレート制限に達した場合の挙動.md"},
    {
        "question": "サービスクレジットの申請期限はいつ",
        "expected": "サービスクレジットの申請期限.md",
    },
    {
        "question": "SSOが使えないときの確認ポイント",
        "expected": "SAML_SSOでログインできない場合の確認項目.md",
    },
    {"question": "APIエラーコード一覧", "expected": "API認証・監査ログ管理ガイド.md"},
]

REQUIRED_NON_FILTERABLE_KEYS = {"AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managed-kb-id", required=True)
    parser.add_argument("--managed-data-source-id", required=True)
    parser.add_argument("--customer-kb-id", required=True)
    parser.add_argument("--customer-data-source-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--expected-document-count", type=int, default=33)
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
    parser.add_argument(
        "--customer-rerank-model-arn",
        default=None,
        help="指定時のみS3 Vectorsにリランキングを適用する。サービスロールへの追加IAM権限が必要。",
    )
    parser.add_argument(
        "--use-question-filters",
        action="store_true",
        help="質問セットにfilterがある場合、両方のKnowledge Baseへ適用する。",
    )
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def filename_from_result(result: dict[str, Any]) -> str:
    uri = result.get("location", {}).get("s3Location", {}).get("uri", "?")
    return urllib.parse.unquote(uri.rsplit("/", 1)[-1])


def normalize_results(response: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for result in response.get("retrievalResults", []):
        uri = result.get("location", {}).get("s3Location", {}).get("uri", "?")
        normalized.append(
            {
                "file": filename_from_result(result),
                "score": round(result.get("score", 0), 4),
                "uri": uri,
                "text": result.get("content", {}).get("text", ""),
            }
        )
    return normalized


def matches_expected(result_file: str, expected_sources: list[str]) -> bool:
    return any(source.rsplit("/", 1)[-1] in result_file for source in expected_sources)


def latest_ingestion_job(agent: Any, kb_id: str, data_source_id: str) -> dict[str, Any]:
    jobs = agent.list_ingestion_jobs(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
        maxResults=10,
    ).get("ingestionJobSummaries", [])
    if not jobs:
        raise RuntimeError(f"No ingestion jobs found for {kb_id}/{data_source_id}")
    return max(jobs, key=lambda job: job["startedAt"])


def validate_ingestion(
    agent: Any, kb_id: str, data_source_id: str, expected_count: int
) -> dict[str, Any]:
    job = latest_ingestion_job(agent, kb_id, data_source_id)
    stats = job.get("statistics", {})
    failed = stats.get("numberOfDocumentsFailed")
    scanned = stats.get("numberOfDocumentsScanned")
    if job.get("status") != "COMPLETE":
        raise RuntimeError(f"Latest ingestion job is not COMPLETE: {job}")
    if failed != 0:
        raise RuntimeError(
            f"Latest ingestion job has {failed} failed document(s): {job}"
        )
    if scanned != expected_count:
        raise RuntimeError(
            f"Expected {expected_count} scanned documents, got {scanned}: {job}"
        )
    return job


def data_source_location(
    data_source: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    config = data_source["dataSource"]["dataSourceConfiguration"]
    if config["type"] == "S3":
        s3_config = config["s3Configuration"]
        return (
            s3_config["bucketArn"].split(":::", 1)[1],
            s3_config.get("inclusionPrefixes", [""]),
            [],
        )

    connector = config["managedKnowledgeBaseConnectorConfiguration"][
        "connectorParameters"
    ]
    if isinstance(connector, str):
        connector = json.loads(connector)
    if connector.get("type") != "S3":
        raise RuntimeError("The comparison data source must be an S3 connector")
    return (
        connector["connectionConfiguration"]["bucketName"],
        connector.get("inclusionPrefixes", [""]),
        connector.get("exclusionPrefixes", []),
    )


def included(key: str, prefixes: list[str], exclusions: list[str]) -> bool:
    return any(key.startswith(prefix) for prefix in prefixes) and not any(
        key.startswith(prefix) for prefix in exclusions
    )


def s3_manifest(
    s3: Any, bucket: str, prefixes: list[str], exclusions: list[str]
) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    paginator = s3.get_paginator("list_objects_v2")
    root = min(prefixes, key=len) if prefixes else ""
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not included(key, prefixes, exclusions):
                continue
            relative_key = key[len(root) :] if root and key.startswith(root) else key
            manifest[relative_key] = {
                "size": obj["Size"],
                "etag": obj["ETag"].strip('"'),
            }
    if root == "" and manifest and all(
        key.startswith("sample-data/") for key in manifest
    ):
        manifest = {
            key.removeprefix("sample-data/"): value
            for key, value in manifest.items()
        }
    return manifest


def validate_corpus(
    managed_manifest: dict[str, dict[str, Any]],
    customer_manifest: dict[str, dict[str, Any]],
    expected_count: int,
) -> list[str]:
    for label, manifest in (
        ("managed", managed_manifest),
        ("customer", customer_manifest),
    ):
        documents = [key for key in manifest if not key.endswith(".metadata.json")]
        non_markdown = [key for key in documents if not key.endswith(".md")]
        if len(documents) != expected_count:
            raise RuntimeError(
                f"{label} corpus has {len(documents)} documents, expected {expected_count}"
            )
        if non_markdown:
            raise RuntimeError(
                f"{label} corpus contains non-Markdown documents: {non_markdown}"
            )
    if managed_manifest != customer_manifest:
        managed_only = sorted(set(managed_manifest) - set(customer_manifest))
        customer_only = sorted(set(customer_manifest) - set(managed_manifest))
        changed = sorted(
            key
            for key in set(managed_manifest) & set(customer_manifest)
            if managed_manifest[key] != customer_manifest[key]
        )
        raise RuntimeError(
            "Corpus manifests differ: "
            f"managed_only={managed_only}, customer_only={customer_only}, changed={changed}"
        )
    return sorted(key for key in managed_manifest if not key.endswith(".metadata.json"))


def validate_kb_types_and_index(
    agent: Any,
    s3vectors: Any,
    managed_kb_id: str,
    customer_kb_id: str,
) -> dict[str, Any]:
    managed = agent.get_knowledge_base(knowledgeBaseId=managed_kb_id)["knowledgeBase"]
    customer = agent.get_knowledge_base(knowledgeBaseId=customer_kb_id)["knowledgeBase"]
    if managed["knowledgeBaseConfiguration"]["type"] != "MANAGED":
        raise RuntimeError(f"{managed_kb_id} is not a Managed Knowledge Base")
    storage_type = customer.get("storageConfiguration", {}).get("type")
    if storage_type not in {"OPENSEARCH_SERVERLESS", "S3_VECTORS"}:
        raise RuntimeError(
            f"{customer_kb_id} is not backed by OpenSearch Serverless or S3 Vectors"
        )

    result = {"managed": managed, "customer": customer}
    if storage_type == "S3_VECTORS":
        index_arn = customer["storageConfiguration"]["s3VectorsConfiguration"][
            "indexArn"
        ]
        bucket_name, index_name = index_arn.split(":bucket/", 1)[1].split("/index/", 1)
        index = s3vectors.get_index(vectorBucketName=bucket_name, indexName=index_name)[
            "index"
        ]
        actual_keys = set(
            index.get("metadataConfiguration", {}).get("nonFilterableMetadataKeys", [])
        )
        missing = REQUIRED_NON_FILTERABLE_KEYS - actual_keys
        if missing:
            raise RuntimeError(
                f"S3 Vector Index is missing non-filterable keys: {sorted(missing)}"
            )
        result["s3_vector_index"] = index
    return result


def retrieve_managed(
    runtime: Any,
    kb_id: str,
    query: str,
    n: int = 5,
    metadata_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config: dict[str, Any] = {
        "numberOfResults": n,
        "rerankingModelType": "MANAGED",
    }
    if metadata_filter:
        config["filter"] = metadata_filter
    response = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"managedSearchConfiguration": config},
    )
    return normalize_results(response)


def retrieve_customer(
    runtime: Any,
    kb_id: str,
    query: str,
    n: int = 5,
    rerank_model_arn: str | None = None,
    metadata_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config: dict[str, Any] = {
        "numberOfResults": 10 if rerank_model_arn else n,
        "overrideSearchType": "SEMANTIC",
    }
    if metadata_filter:
        config["filter"] = metadata_filter
    if rerank_model_arn:
        config["rerankingConfiguration"] = {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": rerank_model_arn},
                "numberOfRerankedResults": n,
            },
        }
    response = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": config},
    )
    return normalize_results(response)


def retrieval_summary(
    results: list[dict[str, Any]], expected: list[str]
) -> dict[str, Any]:
    found = {
        source
        for source in expected
        if any(matches_expected(result["file"], [source]) for result in results)
    }
    return {
        "results": results,
        "contains_any_expected_source": bool(found),
        "contains_all_expected_sources": found == set(expected),
        "expected_source_coverage": round(len(found) / len(expected), 4),
    }


def evaluate(
    runtime: Any,
    model_runtime: Any,
    args: argparse.Namespace,
    questions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def empty_totals() -> dict[str, Any]:
        return {
            "contains_any": 0,
            "contains_all": 0,
            "source_coverage": [],
            **{metric: [] for metric in METRICS},
        }
    totals = {"managed": empty_totals(), "customer_semantic": empty_totals()}
    if args.customer_rerank_model_arn:
        totals["customer_reranked"] = empty_totals()
    rows = []
    for question in questions:
        expected = question.get("expected_sources", [])
        metadata_filter = question.get("filter") if args.use_question_filters else None
        managed = retrieval_summary(
            retrieve_managed(
                runtime,
                args.managed_kb_id,
                question["question"],
                metadata_filter=metadata_filter,
            ),
            expected,
        )
        semantic = retrieval_summary(
            retrieve_customer(
                runtime,
                args.customer_kb_id,
                question["question"],
                metadata_filter=metadata_filter,
            ),
            expected,
        )
        variants = {
            "managed": managed,
            "customer_semantic": semantic,
        }
        if args.customer_rerank_model_arn:
            variants["customer_reranked"] = retrieval_summary(
                retrieve_customer(
                    runtime,
                    args.customer_kb_id,
                    question["question"],
                    rerank_model_arn=args.customer_rerank_model_arn,
                    metadata_filter=metadata_filter,
                ),
                expected,
            )
        for name, result in variants.items():
            if args.answer_model_id:
                result["answer_quality"] = evaluate_answer(
                    model_runtime,
                    answer_model_id=args.answer_model_id,
                    judge_model_id=args.judge_model_id,
                    question=question,
                    results=result["results"],
                )
            totals[name]["contains_any"] += int(
                result["contains_any_expected_source"]
            )
            totals[name]["contains_all"] += int(
                result["contains_all_expected_sources"]
            )
            totals[name]["source_coverage"].append(
                result["expected_source_coverage"]
            )
            if "answer_quality" in result:
                for metric in METRICS:
                    totals[name][metric].append(
                        result["answer_quality"]["judge"]["scores"][metric]
                    )
        rows.append(
            {
                "id": question["id"],
                "question": question["question"],
                "tags": question.get("tags", []),
                "expected_sources": expected,
                **variants,
            }
        )
        message = (
            f"{question['id']:<4} "
            f"managed={managed['results'][0]['file'] if managed['results'] else 'N/A'} "
            f"customer={semantic['results'][0]['file'] if semantic['results'] else 'N/A'}"
        )
        if "customer_reranked" in variants:
            reranked = variants["customer_reranked"]
            message += f" customer+rerank={reranked['results'][0]['file'] if reranked['results'] else 'N/A'}"
        print(message)
    summary: dict[str, Any] = {"total": len(questions)}
    for name, values in totals.items():
        summary[name] = {
            "contains_any_expected_source": values["contains_any"],
            "contains_all_expected_sources": values["contains_all"],
            "mean_expected_source_coverage": round(
                statistics.mean(values["source_coverage"]), 4
            ),
        }
        if values["correctness"]:
            summary[name]["answer_quality"] = {
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
                tag, {"n": 0, "managed": 0, "customer_semantic": 0}
            )
            if "customer_reranked" in row:
                current.setdefault("customer_reranked", 0)
            current["n"] += 1
            for variant in ("managed", "customer_semantic", "customer_reranked"):
                if variant not in row:
                    continue
                current[variant] += int(
                    row[variant]["contains_all_expected_sources"]
                )
    return stats


def evaluate_hard_cases(runtime: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = []
    for case in HARD_CASES:
        semantic = retrieve_customer(runtime, args.customer_kb_id, case["question"])
        row = {**case, "customer_semantic": semantic}
        if args.customer_rerank_model_arn:
            row["customer_reranked"] = retrieve_customer(
                runtime,
                args.customer_kb_id,
                case["question"],
                rerank_model_arn=args.customer_rerank_model_arn,
            )
        rows.append(row)
    return rows


def git_sha(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def working_tree_fingerprint(repo_root: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout
    tracked_diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    ).stdout
    return {
        "dirty": bool(status),
        "status_sha256": sha256_bytes(status),
        "tracked_diff_sha256": sha256_bytes(tracked_diff),
    }


def summary_markdown(payload: dict[str, Any]) -> str:
    totals = payload["summary"]
    total = totals["total"]
    lines = [
        "# Managed Knowledge BaseとCustomer-managed Knowledge Baseの比較",
        "",
        f"- 実行日時: {payload['timestamp']}",
        f"- Git SHA: `{payload['git_sha']}`",
        f"- スクリプトのSHA-256: `{payload['script_sha256']}`",
        f"- 作業ツリーに未コミットの変更あり: `{payload['working_tree']['dirty']}`",
        f"- 検索対象: Markdown {len(payload['corpus']['documents'])}文書（faq.xlsxは除外）",
        f"- Managed Knowledge Base: `{payload['managed_kb_id']}`",
        f"- Customer-managed Knowledge Base: `{payload['customer_kb_id']}`",
        f"- 質問別メタデータフィルター: `{payload['use_question_filters']}`",
        "",
        "| 構成 | 取得した5件に期待文書を含む質問 | 期待文書がすべて揃った質問 | 期待文書の取得率（平均） |",
        "|---|---:|---:|---:|",
    ]
    customer_storage_type = payload["knowledge_bases"]["customer"][
        "storageConfiguration"
    ]["type"]
    customer_label = {
        "OPENSEARCH_SERVERLESS": "OpenSearch Serverless（セマンティック検索）",
        "S3_VECTORS": "S3 Vectors（セマンティック検索）",
    }[customer_storage_type]
    labels = {
        "managed": "Managed Knowledge Base（ハイブリッド検索＋マネージドリランカー）",
        "customer_semantic": customer_label,
        "customer_reranked": f"{customer_label}＋Cohere Rerank 3.5",
    }
    for name, label in labels.items():
        if name not in totals:
            continue
        any_count = totals[name]["contains_any_expected_source"]
        all_count = totals[name]["contains_all_expected_sources"]
        lines.append(
            f"| {label} | {any_count}/{total} ({any_count / total:.1%}) "
            f"| {all_count}/{total} ({all_count / total:.1%}) "
            f"| {totals[name]['mean_expected_source_coverage']:.1%} |"
        )
    rerank_ran = "customer_reranked" in totals
    category_header = "| カテゴリ | 質問数 | Managed | Customer-managed（セマンティック検索） |"
    category_rule = "|---|---:|---:|---:|"
    if rerank_ran:
        category_header = "| カテゴリ | 質問数 | Managed | Customer-managed（セマンティック検索） | Customer-managed（リランキングあり） |"
        category_rule = "|---|---:|---:|---:|---:|"
    lines.extend(["", "## カテゴリ別に期待文書がすべて揃った質問数", "", category_header, category_rule])
    for tag, stats in sorted(payload["tag_stats"].items()):
        line = (
            f"| {tag} | {stats['n']} | {stats['managed']}/{stats['n']} | "
            f"{stats['customer_semantic']}/{stats['n']} |"
        )
        if rerank_ran:
            line = line[:-1] + f" {stats['customer_reranked']}/{stats['n']} |"
        lines.append(line)
    hard_header = "| 質問 | 期待文書 | セマンティック検索で最初に取得した文書 |"
    hard_rule = "|---|---|---|"
    if rerank_ran:
        hard_header = "| 質問 | 期待文書 | セマンティック検索で最初に取得した文書 | リランキング後に最初に取得した文書 |"
        hard_rule = "|---|---|---|---|"
    lines.extend(["", "## Customer-managed構成で順位を確認する質問", "", hard_header, hard_rule])
    for row in payload["hard_cases"]:
        semantic = (
            row["customer_semantic"][0]["file"] if row["customer_semantic"] else "N/A"
        )
        line = f"| {row['question']} | {row['expected']} | {semantic} |"
        if rerank_ran:
            reranked = (
                row["customer_reranked"][0]["file"]
                if row["customer_reranked"]
                else "N/A"
            )
            line = line[:-1] + f" {reranked} |"
        lines.append(line)
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
        for name, label in labels.items():
            if name not in totals:
                continue
            scores = totals[name]["answer_quality"]
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
    repo_root = Path(__file__).resolve().parents[2]
    now = datetime.now(timezone.utc)
    output_dir = args.output_dir or (
        Path(__file__).resolve().parent
        / "logs"
        / f"{now:%Y%m%d}-managed-kb-comparison"
        / f"{now:%Y%m%dT%H%M%SZ}"
    )

    agent = boto3.client("bedrock-agent", region_name=args.region)
    runtime = boto3.client("bedrock-agent-runtime", region_name=args.region)
    model_runtime = boto3.client("bedrock-runtime", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)
    s3vectors = boto3.client("s3vectors", region_name=args.region)

    kb_config = validate_kb_types_and_index(
        agent, s3vectors, args.managed_kb_id, args.customer_kb_id
    )
    managed_job = validate_ingestion(
        agent,
        args.managed_kb_id,
        args.managed_data_source_id,
        args.expected_document_count,
    )
    customer_job = validate_ingestion(
        agent,
        args.customer_kb_id,
        args.customer_data_source_id,
        args.expected_document_count,
    )

    managed_ds = agent.get_data_source(
        knowledgeBaseId=args.managed_kb_id,
        dataSourceId=args.managed_data_source_id,
    )
    customer_ds = agent.get_data_source(
        knowledgeBaseId=args.customer_kb_id,
        dataSourceId=args.customer_data_source_id,
    )
    managed_location = data_source_location(managed_ds)
    customer_location = data_source_location(customer_ds)
    managed_manifest = s3_manifest(s3, *managed_location)
    customer_manifest = s3_manifest(s3, *customer_location)
    documents = validate_corpus(
        managed_manifest, customer_manifest, args.expected_document_count
    )

    questions = [
        json.loads(line)
        for line in args.questions.read_text().splitlines()
        if line.strip()
    ]
    questions.extend(EXTRA_QUESTIONS)
    rows, totals = evaluate(runtime, model_runtime, args, questions)
    hard_cases = evaluate_hard_cases(runtime, args)

    payload = {
        "timestamp": now.isoformat(),
        "git_sha": git_sha(repo_root),
        "working_tree": working_tree_fingerprint(repo_root),
        "script_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "questions_sha256": sha256_bytes(args.questions.read_bytes()),
        "boto3_version": boto3.__version__,
        "region": args.region,
        "managed_kb_id": args.managed_kb_id,
        "managed_data_source_id": args.managed_data_source_id,
        "customer_kb_id": args.customer_kb_id,
        "customer_data_source_id": args.customer_data_source_id,
        "customer_rerank_model_arn": args.customer_rerank_model_arn,
        "answer_model_id": args.answer_model_id,
        "judge_model_id": args.judge_model_id,
        "use_question_filters": args.use_question_filters,
        "ingestion": {"managed": managed_job, "customer": customer_job},
        "knowledge_bases": kb_config,
        "corpus": {
            "documents": documents,
            "managed_location": managed_location,
            "customer_location": customer_location,
            "manifest": managed_manifest,
        },
        "summary": totals,
        "tag_stats": tag_statistics(rows),
        "results": rows,
        "hard_cases": hard_cases,
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "comparison-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    (output_dir / "summary.md").write_text(summary_markdown(payload))
    print(f"\nSaved auditable results to {output_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
