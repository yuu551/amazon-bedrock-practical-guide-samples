#!/usr/bin/env python3
"""トピックフォルダ単位の網羅型RAGを実行するスクリプト。

3段構成:
  Step 1. クエリ分析（Sonnet）: 質問から対象トピックと検索クエリを抽出
  Step 2. KB Retrieveでチャンク取得 → topic_idで集約 → 上位N topicの全ファイルを
          S3から取得し、Haikuで並列に関連情報抽出
  Step 3. 抽出情報を Sonnet で統合し最終回答を生成
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3

from common import (
    converse_inference_config,
    dump_json,
    extract_json_object,
    extract_text_from_converse_response,
    load_json,
)


# ---------------------------------------------------------------------------
# 引数
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a topic-folder oriented RAG: retrieve chunks, group by topic_id, "
            "read all files in the topic folder, extract per file with Haiku in "
            "parallel, and synthesize with Sonnet."
        )
    )
    parser.add_argument(
        "--question",
        required=True,
        help="ユーザーからの質問。",
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
        help="トピックフォルダを取り込んだKnowledge Base ID。",
    )
    parser.add_argument(
        "--data-bucket",
        default=os.environ.get("TOPIC_DATA_BUCKET"),
        help="トピックフォルダを保存しているS3バケット名。",
    )
    parser.add_argument(
        "--topic-prefix",
        default=os.environ.get("TOPIC_DATA_PREFIX", "sample-data/topics/"),
        help="S3バケット内のトピックルートプレフィックス（末尾スラッシュ込み）。",
    )
    parser.add_argument(
        "--topic-config",
        type=Path,
        default=Path("sample-code/03/data/topic_config.json"),
        help="トピックカタログ JSON（Step1のクエリ分析に渡す）。",
    )
    parser.add_argument(
        "--analysis-model-id",
        default=os.environ.get("ANALYSIS_MODEL_ID"),
        help="Step1（クエリ分析）に使うConverseモデルID。Sonnet推奨。",
    )
    parser.add_argument(
        "--extraction-model-id",
        default=os.environ.get("EXTRACTION_MODEL_ID"),
        help="Step2（ファイル単位の情報抽出）に使うConverseモデルID。Haiku推奨。",
    )
    parser.add_argument(
        "--final-model-id",
        default=os.environ.get("FINAL_MODEL_ID"),
        help="Step3（統合）に使うConverseモデルID。Sonnet推奨。",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--retrieve-top-k",
        type=int,
        default=20,
        help="トピックグルーピングに使うKB Retrieveの取得チャンク数。",
    )
    parser.add_argument(
        "--top-n-topics",
        type=int,
        default=2,
        help="ファイル全読みの対象とする上位トピック数。",
    )
    parser.add_argument(
        "--max-files-per-topic",
        type=int,
        default=10,
        help="1トピックあたりの最大ファイル数（保護的な上限）。",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=8,
        help="ファイル単位抽出の最大並列度。",
    )
    parser.add_argument(
        "--search-type",
        choices=["HYBRID", "SEMANTIC"],
        default="HYBRID",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sample-code/03/output/topic-rag"),
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Step 1: クエリ分析
# ---------------------------------------------------------------------------


def _analysis_schema(topic_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "intent_summary": {
                "type": "string",
                "description": "ユーザーが何を網羅的に知りたいかの簡潔な要約。",
            },
            "target_topics": {
                "type": "array",
                "description": "回答に必要なトピックID。1〜3個まで。",
                "items": {"type": "string", "enum": topic_ids},
            },
            "retrieval_query": {
                "type": "string",
                "description": "Knowledge Base Retrieveに渡す検索クエリ。質問の核となるキーワードに整える。",
            },
        },
        "required": ["intent_summary", "target_topics", "retrieval_query"],
        "additionalProperties": False,
    }


def analyze_question(
    runtime_client: Any,
    model_id: str,
    question: str,
    topics: list[dict[str, Any]],
) -> dict[str, Any]:
    topic_ids = [t["topic_id"] for t in topics]
    catalog = "\n".join(
        f"- {t['topic_id']}: {t['display_name']} — {t['description']}" for t in topics
    )
    request = {
        "modelId": model_id,
        "system": [
            {
                "text": "\n".join(
                    [
                        "あなたはトピックフォルダ検索システムのプランナーです。",
                        "質問に答えるために確認すべきトピックを、カタログから1〜3個選んでください。",
                        "retrieval_queryを検索に適した短い日本語に書き換えてください。",
                        "質問が複数のトピックにまたがる場合は、target_topicsにすべて含めてください。",
                        "JSONだけを返してください。",
                    ]
                )
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"質問:\n{question}\n\n"
                            f"トピックカタログ:\n{catalog}"
                        )
                    }
                ],
            }
        ],
        "inferenceConfig": converse_inference_config(model_id, 800, 0),
        "outputConfig": {
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "name": "topic_rag_plan",
                        "description": "トピックフォルダ検索の実行計画。",
                        "schema": json.dumps(_analysis_schema(topic_ids), ensure_ascii=False),
                    }
                },
            }
        },
    }

    try:
        response = runtime_client.converse(**request)
    except Exception:
        request.pop("outputConfig", None)
        response = runtime_client.converse(**request)

    content = response.get("output", {}).get("message", {}).get("content", [])
    for block in content:
        payload = block.get("json")
        if isinstance(payload, dict):
            return payload
    return extract_json_object(extract_text_from_converse_response(response))


# ---------------------------------------------------------------------------
# Step 2a: KB Retrieveしてtopic_idで集約
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicCandidate:
    topic_id: str
    max_score: float
    hit_count: int


def retrieve_chunks(
    agent_runtime_client: Any,
    knowledge_base_id: str,
    query: str,
    top_k: int,
    search_type: str,
) -> list[dict[str, Any]]:
    response = agent_runtime_client.retrieve(
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": top_k,
                "overrideSearchType": search_type,
            }
        },
    )
    return response.get("retrievalResults", [])


def group_chunks_by_topic(
    chunks: list[dict[str, Any]],
    explicit_target_topics: list[str],
    top_n_topics: int,
) -> list[TopicCandidate]:
    """チャンクを topic_id メタデータでグルーピングし、上位 top_n_topics を返す。

    explicit_target_topics（Step1の分析結果）にあるtopic_idは、retrieveでヒット
    していなくても候補に含める。
    """
    aggregates: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        topic_id = metadata.get("topic_id")
        if not topic_id:
            continue
        score = float(chunk.get("score") or 0.0)
        bucket = aggregates.setdefault(
            topic_id, {"topic_id": topic_id, "max_score": 0.0, "hit_count": 0}
        )
        bucket["max_score"] = max(bucket["max_score"], score)
        bucket["hit_count"] += 1

    # explicit に指定されたものを必ず含める
    for topic_id in explicit_target_topics:
        aggregates.setdefault(
            topic_id, {"topic_id": topic_id, "max_score": 0.0, "hit_count": 0}
        )

    candidates = sorted(
        aggregates.values(),
        key=lambda r: (r["max_score"], r["hit_count"]),
        reverse=True,
    )
    return [
        TopicCandidate(
            topic_id=row["topic_id"],
            max_score=row["max_score"],
            hit_count=row["hit_count"],
        )
        for row in candidates[:top_n_topics]
    ]


# ---------------------------------------------------------------------------
# Step 2b: トピックフォルダの全ファイルをS3から取得
# ---------------------------------------------------------------------------


def list_topic_files(
    s3_client: Any,
    bucket: str,
    topic_prefix: str,
    topic_id: str,
    max_files: int,
) -> list[dict[str, str]]:
    """S3 から topics/{topic_id}/ 配下の .md ファイル一覧を取得（メタデータJSONは除く）"""
    prefix = f"{topic_prefix}{topic_id}/"
    paginator = s3_client.get_paginator("list_objects_v2")
    files: list[dict[str, str]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            if not key.endswith(".md") or key.endswith(".metadata.json"):
                continue
            files.append({"bucket": bucket, "key": key, "name": key.rsplit("/", 1)[-1]})
            if len(files) >= max_files:
                return files
    return files


def get_file_text(s3_client: Any, bucket: str, key: str) -> str:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    return body.decode("utf-8")


# ---------------------------------------------------------------------------
# Step 2c: ファイル単位の関連情報抽出（Haiku × 並列）
# ---------------------------------------------------------------------------


def extract_relevant_info(
    runtime_client: Any,
    model_id: str,
    question: str,
    file_name: str,
    file_text: str,
) -> dict[str, Any]:
    """1ファイルから質問に関連する情報のみを抽出する。"""
    started = time.perf_counter()
    response = runtime_client.converse(
        modelId=model_id,
        system=[
            {
                "text": "\n".join(
                    [
                        "ユーザーの質問に答えるために必要な情報だけを抽出してください。",
                        "無関係な情報は除き、数値、日付、識別子、手順などの具体的な事実を残してください。",
                        "関連情報がない場合は、何も出力しないでください。",
                        "必ず日本語で回答してください。",
                    ]
                )
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"質問:\n{question}\n\n"
                            f"ファイル名: {file_name}\n\n"
                            f"ファイル内容:\n{file_text}"
                        )
                    }
                ],
            }
        ],
        inferenceConfig=converse_inference_config(model_id, 1200, 0),
    )
    text = extract_text_from_converse_response(response).strip()
    return {
        "file_name": file_name,
        "relevant_excerpt": text,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def extract_in_parallel(
    runtime_client: Any,
    model_id: str,
    question: str,
    files: list[dict[str, str]],
    s3_client: Any,
    max_concurrency: int,
) -> list[dict[str, Any]]:
    """ファイルごとにHaiku抽出を並列実行する。"""
    results: list[dict[str, Any]] = []

    def _run(file_info: dict[str, str]) -> dict[str, Any]:
        text = get_file_text(s3_client, file_info["bucket"], file_info["key"])
        extraction = extract_relevant_info(
            runtime_client=runtime_client,
            model_id=model_id,
            question=question,
            file_name=file_info["name"],
            file_text=text,
        )
        extraction["s3_key"] = file_info["key"]
        return extraction

    with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as executor:
        futures = {executor.submit(_run, f): f for f in files}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                file_info = futures[future]
                results.append(
                    {
                        "file_name": file_info["name"],
                        "s3_key": file_info["key"],
                        "relevant_excerpt": "",
                        "error": str(exc),
                    }
                )
    # ファイル名で安定ソート
    results.sort(key=lambda r: r.get("file_name", ""))
    return results


# ---------------------------------------------------------------------------
# Step 3: 抽出情報をSonnetで統合
# ---------------------------------------------------------------------------


def synthesize_answer(
    runtime_client: Any,
    model_id: str,
    question: str,
    plan: dict[str, Any],
    topic_extractions: list[dict[str, Any]],
) -> str:
    sections: list[str] = []
    for topic_block in topic_extractions:
        sections.append(f"# トピック: {topic_block['topic_id']}")
        for extraction in topic_block["extractions"]:
            excerpt = extraction.get("relevant_excerpt") or ""
            if not excerpt:
                continue
            sections.append(f"## ファイル: {extraction['file_name']}\n{excerpt}")

    response = runtime_client.converse(
        modelId=model_id,
        system=[
            {
                "text": "\n".join(
                    [
                        "ファイルごとの抽出結果を統合し、漏れのない1つの回答を作成してください。",
                        "抽出結果に含まれる事実だけを使い、推測しないでください。",
                        "ファイル単位ではなく、ユーザーの質問に沿って回答を構成してください。",
                        "抽出結果が矛盾する場合は、隠さずに指摘してください。",
                        "具体的な事実には[filename.md]の形式で出典ファイル名を付けてください。",
                        "日本語で回答してください。",
                    ]
                )
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": "\n\n".join(
                            [
                                f"元の質問:\n{question}",
                                f"意図の要約: {plan.get('intent_summary', '')}",
                                f"対象トピック: {', '.join(plan.get('target_topics', []))}",
                                "ファイルごとの抽出結果:",
                                "\n\n".join(sections),
                            ]
                        )
                    }
                ],
            }
        ],
        inferenceConfig=converse_inference_config(model_id, 2000, 0.2),
    )
    return extract_text_from_converse_response(response)


# ---------------------------------------------------------------------------
# 出力整形
# ---------------------------------------------------------------------------


def render_markdown(
    question: str,
    plan: dict[str, Any],
    topic_candidates: list[TopicCandidate],
    topic_extractions: list[dict[str, Any]],
    final_answer: str,
) -> str:
    lines: list[str] = [
        "# Topic-Folder RAG",
        "",
        f"Question: {question}",
        "",
        "## Plan",
        "",
        f"- intent_summary: {plan.get('intent_summary', '')}",
        f"- target_topics: {', '.join(plan.get('target_topics', []))}",
        f"- retrieval_query: {plan.get('retrieval_query', '')}",
        "",
        "## Topic Candidates",
        "",
        "| topic_id | max_score | hit_count |",
        "|---|---:|---:|",
    ]
    for cand in topic_candidates:
        lines.append(f"| {cand.topic_id} | {cand.max_score:.3f} | {cand.hit_count} |")

    lines.extend(["", "## Per-Topic Extractions", ""])
    for topic_block in topic_extractions:
        lines.append(f"### {topic_block['topic_id']}")
        lines.append("")
        for extraction in topic_block["extractions"]:
            excerpt = extraction.get("relevant_excerpt") or "(empty)"
            error = extraction.get("error")
            lines.append(f"#### {extraction['file_name']}")
            lines.append("")
            if error:
                lines.append(f"_error_: {error}")
            else:
                latency = extraction.get("latency_ms")
                if latency is not None:
                    lines.append(f"_latency_: {latency} ms")
                lines.append("")
                lines.append(excerpt)
            lines.append("")

    lines.extend(["## Final Answer", "", final_answer, ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    missing = [
        name
        for name, value in [
            ("--knowledge-base-id", args.knowledge_base_id),
            ("--data-bucket", args.data_bucket),
            ("--analysis-model-id", args.analysis_model_id),
            ("--extraction-model-id", args.extraction_model_id),
            ("--final-model-id", args.final_model_id),
        ]
        if not value
    ]
    if missing:
        raise SystemExit("Missing required args: " + ", ".join(missing))

    topic_config = load_json(args.topic_config)
    topics = topic_config["topics"]

    runtime_client = boto3.client("bedrock-runtime", region_name=args.region)
    agent_runtime_client = boto3.client("bedrock-agent-runtime", region_name=args.region)
    s3_client = boto3.client("s3", region_name=args.region)

    started = time.perf_counter()

    # Step 1: 質問分析
    plan = analyze_question(
        runtime_client=runtime_client,
        model_id=args.analysis_model_id,
        question=args.question,
        topics=topics,
    )

    # Step 2a: チャンクをretrieveし、topic_idで集約
    chunks = retrieve_chunks(
        agent_runtime_client=agent_runtime_client,
        knowledge_base_id=args.knowledge_base_id,
        query=plan.get("retrieval_query", args.question),
        top_k=args.retrieve_top_k,
        search_type=args.search_type,
    )
    topic_candidates = group_chunks_by_topic(
        chunks=chunks,
        explicit_target_topics=plan.get("target_topics", []),
        top_n_topics=args.top_n_topics,
    )

    # Step 2b/2c: トピックごとにファイル全読み + Haikuで並列抽出
    topic_extractions: list[dict[str, Any]] = []
    for cand in topic_candidates:
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
            question=args.question,
            files=files,
            s3_client=s3_client,
            max_concurrency=args.max_concurrency,
        )
        topic_extractions.append({"topic_id": cand.topic_id, "extractions": extractions})

    # Step 3: 統合
    final_answer = synthesize_answer(
        runtime_client=runtime_client,
        model_id=args.final_model_id,
        question=args.question,
        plan=plan,
        topic_extractions=topic_extractions,
    )

    total_latency_ms = round((time.perf_counter() - started) * 1000, 1)

    run_dir = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    dump_json(
        run_dir / "plan.json",
        {
            "plan": plan,
            "topic_candidates": [c.__dict__ for c in topic_candidates],
            "total_latency_ms": total_latency_ms,
        },
    )
    dump_json(run_dir / "extractions.json", {"topics": topic_extractions})
    (run_dir / "answer.md").write_text(
        render_markdown(args.question, plan, topic_candidates, topic_extractions, final_answer),
        encoding="utf-8",
    )

    print(f"Wrote plan to {run_dir / 'plan.json'}")
    print(f"Wrote extractions to {run_dir / 'extractions.json'}")
    print(f"Wrote synthesized answer to {run_dir / 'answer.md'}")
    print(f"Total latency: {total_latency_ms} ms")


if __name__ == "__main__":
    main()
