#!/usr/bin/env python3
"""同一のKnowledge Bases検索結果でHaiku 4.5とGPT-5.6 Lunaを比較する。

品質比較では質問ごとにRetrieve APIを1回だけ呼び出し、取得した同一チャンクを
両モデルのConverse APIへ渡す。RetrieveAndGenerateとそのストリーミング版は、
内部検索結果を固定できないため、品質比較ではなくAPI互換性の確認として分けて記録する。

使い方:
    uv run compare_low_cost_models.py \
        --knowledge-base-id ABC123 \
        --data-source-id DEF456
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from retrieve_and_converse import DEFAULT_SYSTEM_PROMPT, inference_config


REGION = "us-east-1"
HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
LUNA_MODEL_ID = "us.openai.gpt-5.6-luna"
PRICING_DATE = "2026-08-24"
PRICING_SOURCES = {
    "haiku": "https://aws.amazon.com/bedrock/pricing/",
    "luna": (
        "https://docs.aws.amazon.com/bedrock/latest/userguide/"
        "model-card-openai-gpt-56-luna.html"
    ),
}

# US Geo、Standard tier、100万トークン当たり。Lunaは272K以下の短コンテキスト料金。
MODELS = {
    "haiku_4_5": {
        "model_id": HAIKU_MODEL_ID,
        "input_usd_per_million": 1.00,
        "cache_write_input_usd_per_million": 1.25,
        "cache_read_input_usd_per_million": 0.10,
        "output_usd_per_million": 5.00,
    },
    "gpt_5_6_luna": {
        "model_id": LUNA_MODEL_ID,
        "input_usd_per_million": 0.22,
        "cache_write_input_usd_per_million": 0.275,
        "cache_read_input_usd_per_million": 0.022,
        "output_usd_per_million": 1.32,
    },
}

SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

RAG_ORCHESTRATION_PROMPT = """\
会話履歴と今回の質問から、Knowledge Baseを検索するための検索クエリを作成してください。

会話履歴:
$conversation_history$

今回の質問:
$query$

$output_format_instructions$"""

RAG_GENERATION_PROMPT = """\
あなたは社内ドキュメントに基づいて回答するアシスタントです。
検索結果に根拠がある場合だけ日本語で回答し、根拠がなければ
「お探しの情報は見つかりませんでした」と回答してください。
推測や一般的な知識で補完しないでください。

検索結果:
$search_results$

質問:
$query$

$output_format_instructions$"""

QUESTIONS = [
    {
        "id": "saml_login",
        "question": (
            "SAML SSOでログインできない場合、最初に確認する項目と、"
            "失敗理由を確認する場所をすべて教えてください。"
        ),
        "expected_source": "SAML_SSOでログインできない場合の確認項目.md",
        "expected_facts": [
            {"label": "SAMLアプリへのユーザー割り当て", "all_of": ["割り当て"]},
            {"label": "ACS URL", "all_of": ["ACS URL"]},
            {"label": "Entity ID", "all_of": ["Entity ID"]},
            {"label": "IdP証明書の有効期限", "all_of": ["証明書", "有効期限"]},
            {"label": "SAMLログ", "all_of": ["SAMLログ"]},
        ],
    },
    {
        "id": "service_credit",
        "question": (
            "サービスクレジットについて、申請期限、申請経路、添付する情報、"
            "判定結果が返るまでの期間を教えてください。"
        ),
        "expected_source": "サービスクレジットの申請期限.md",
        "expected_facts": [
            {"label": "翌月末日", "all_of": ["翌月末日"]},
            {"label": "23:59 JST", "all_of": ["23:59", "JST"]},
            {"label": "サポートポータル", "all_of": ["サポートポータル"]},
            {"label": "メール", "all_of": ["メール"]},
            {"label": "発生日時", "all_of": ["発生日時"]},
            {"label": "影響範囲", "all_of": ["影響範囲"]},
            {"label": "業務影響", "all_of": ["業務影響"]},
            {"label": "テナントID", "all_of": ["テナントID"]},
            {"label": "請求書番号", "all_of": ["請求書番号"]},
            {"label": "5営業日", "all_of": ["5営業日"]},
        ],
    },
    {
        "id": "api_key_limit",
        "question": (
            "APIキーの同時有効本数をプラン別に示し、失効済みキーの扱いと、"
            "Enterpriseで上限を超えたい場合の手続きを教えてください。"
        ),
        "expected_source": "APIキーの発行上限.md",
        "expected_facts": [
            {"label": "Freeは1本", "all_of": ["Free", "1本"]},
            {"label": "Standardは5本", "all_of": ["Standard", "5本"]},
            {"label": "Enterpriseは50本", "all_of": ["Enterprise", "50本"]},
            {"label": "失効済みは数えない", "all_of": ["失効済み", "カウントされません"]},
            {"label": "サポート窓口から上限緩和", "all_of": ["サポート窓口", "上限緩和"]},
        ],
    },
    {
        "id": "enterprise_support",
        "question": (
            "Enterpriseプランの初回応答SLAを優先度別に示し、"
            "時間外受付の条件と問い合わせ経路を教えてください。"
        ),
        "expected_source": "Enterpriseプランのサポート応答時間.md",
        "expected_facts": [
            {"label": "Criticalは1時間", "all_of": ["Critical", "1時間"]},
            {"label": "Highは4時間", "all_of": ["High", "4時間"]},
            {"label": "Mediumは1営業日", "all_of": ["Medium", "1営業日"]},
            {"label": "Lowは3営業日", "all_of": ["Low", "3営業日"]},
            {"label": "Criticalのみ24時間365日", "all_of": ["Critical", "24時間365日"]},
            {"label": "サポートポータル", "all_of": ["サポートポータル"]},
            {"label": "Slack Connect", "all_of": ["Slack", "コネクト"]},
            {"label": "TAMへのエスカレーション", "all_of": ["TAM", "エスカレーション"]},
        ],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
        required=os.environ.get("KNOWLEDGE_BASE_ID") is None,
    )
    parser.add_argument(
        "--data-source-id",
        default=os.environ.get("DATA_SOURCE_ID"),
        required=os.environ.get("DATA_SOURCE_ID") is None,
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", REGION))
    parser.add_argument("--number-of-results", type=int, default=4)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="省略時はlogs/YYYYMMDD-haiku-luna/<UTC時刻>へ保存する。",
    )
    parser.add_argument(
        "--skip-rag-compatibility",
        action="store_true",
        help="RetrieveAndGenerateとストリーミングの互換性試験を省略する。",
    )
    parser.add_argument(
        "--skip-quality-comparison",
        action="store_true",
        help="同一検索結果を使うConverse品質比較を省略する。",
    )
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def git_metadata(repo_dir: Path) -> dict[str, Any]:
    def run(*command: str) -> str:
        completed = subprocess.run(
            command,
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    status = run("git", "status", "--porcelain=v1")
    diff = run("git", "diff", "--binary", "HEAD")
    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "working_tree_dirty": bool(status),
        "working_tree_status": status.splitlines(),
        "working_tree_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
    }


def extract_uri(item: dict[str, Any]) -> str:
    location = item.get("location", {})
    for loc_type, key in [
        ("s3Location", "uri"),
        ("webLocation", "url"),
        ("confluenceLocation", "url"),
        ("salesforceLocation", "url"),
        ("sharePointLocation", "url"),
    ]:
        if loc_type in location:
            return location[loc_type].get(key, "")
    return ""


def compact_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": chunk.get("score"),
        "uri": extract_uri(chunk),
        "text": chunk.get("content", {}).get("text", ""),
        "metadata": chunk.get("metadata", {}),
    }


def format_context(chunks: list[dict[str, Any]]) -> str:
    parts = []
    for index, chunk in enumerate(chunks, 1):
        parts.append(
            f"[検索結果{index}] (出典: {extract_uri(chunk)})\n"
            f"{chunk.get('content', {}).get('text', '')}"
        )
    return "\n\n".join(parts)


def fact_coverage(answer: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    checks = []
    for fact in facts:
        tokens = fact["all_of"]
        present = all(token.casefold() in answer.casefold() for token in tokens)
        checks.append({**fact, "present": present})
    hit_count = sum(1 for check in checks if check["present"])
    return {
        "hit_count": hit_count,
        "total": len(checks),
        "all_present": hit_count == len(checks),
        "checks": checks,
    }


def converse(
    client: Any,
    model: dict[str, Any],
    question: str,
    chunks: list[dict[str, Any]],
    expected_source: str,
    expected_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    context = format_context(chunks)
    user_message = f"""以下の検索結果だけを参考に、質問に回答してください。

{context}

質問: {question}"""
    started = time.perf_counter()
    try:
        response = client.converse(
            modelId=model["model_id"],
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig=inference_config(model["model_id"]),
        )
    except (BotoCoreError, ClientError) as error:
        error_response = getattr(error, "response", {})
        return {
            "status": "ERROR",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_type": type(error).__name__,
            "error_code": error_response.get("Error", {}).get("Code"),
            "error": str(error),
            "request_id": error_response.get("ResponseMetadata", {}).get("RequestId"),
        }
    elapsed_ms = (time.perf_counter() - started) * 1000
    content = response.get("output", {}).get("message", {}).get("content", [])
    answer = "\n".join(block.get("text", "") for block in content if block.get("text"))
    usage = response.get("usage", {})
    input_tokens = usage.get("inputTokens", 0)
    cache_write_input_tokens = usage.get("cacheWriteInputTokens", 0)
    cache_read_input_tokens = usage.get("cacheReadInputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)
    estimated_cost_breakdown = {
        "input": input_tokens * model["input_usd_per_million"] / 1_000_000,
        "cache_write_input": (
            cache_write_input_tokens
            * model["cache_write_input_usd_per_million"]
            / 1_000_000
        ),
        "cache_read_input": (
            cache_read_input_tokens
            * model["cache_read_input_usd_per_million"]
            / 1_000_000
        ),
        "output": output_tokens * model["output_usd_per_million"] / 1_000_000,
    }
    estimated_cost = sum(estimated_cost_breakdown.values())
    return {
        "status": "OK",
        "answer": answer.strip(),
        "latency_ms": round(elapsed_ms, 1),
        "usage": usage,
        "estimated_cost_usd": round(estimated_cost, 8),
        "estimated_cost_breakdown_usd": {
            key: round(cost, 8)
            for key, cost in estimated_cost_breakdown.items()
        },
        "stop_reason": response.get("stopReason"),
        "metrics": response.get("metrics", {}),
        "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
        "expected_source_mentioned": expected_source in answer,
        "fact_coverage": fact_coverage(answer, expected_facts),
    }


def profile_arn(bedrock: Any, model_id: str) -> str:
    response = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
    return response["inferenceProfileArn"]


def rag_configuration(
    kb_id: str,
    model_arn: str,
    *,
    custom_prompt_templates: bool,
) -> dict[str, Any]:
    text_inference_config = inference_config(model_arn)
    knowledge_base_configuration: dict[str, Any] = {
        "knowledgeBaseId": kb_id,
        "modelArn": model_arn,
        "generationConfiguration": {
            "inferenceConfig": {
                "textInferenceConfig": text_inference_config,
            }
        },
        "retrievalConfiguration": {
            "vectorSearchConfiguration": {"numberOfResults": 4}
        },
    }
    if custom_prompt_templates:
        knowledge_base_configuration["orchestrationConfiguration"] = {
            "promptTemplate": {"textPromptTemplate": RAG_ORCHESTRATION_PROMPT}
        }
        knowledge_base_configuration["generationConfiguration"]["promptTemplate"] = {
            "textPromptTemplate": RAG_GENERATION_PROMPT
        }
    return {
        "type": "KNOWLEDGE_BASE",
        "knowledgeBaseConfiguration": knowledge_base_configuration,
    }


def citation_references(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references = []
    for group in citations:
        for reference in group.get("retrievedReferences", []):
            references.append(compact_chunk(reference))
    return references


def rag_non_stream(
    client: Any,
    kb_id: str,
    model_arn: str,
    question: str,
    *,
    custom_prompt_templates: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = client.retrieve_and_generate(
            input={"text": question},
            retrieveAndGenerateConfiguration=rag_configuration(
                kb_id,
                model_arn,
                custom_prompt_templates=custom_prompt_templates,
            ),
        )
        return {
            "status": "OK",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "answer": response.get("output", {}).get("text", ""),
            "citations": citation_references(response.get("citations", [])),
            "citation_group_count": len(response.get("citations", [])),
            "session_id_present": bool(response.get("sessionId")),
            "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
        }
    except (BotoCoreError, ClientError) as error:
        error_response = getattr(error, "response", {})
        return {
            "status": "ERROR",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_type": type(error).__name__,
            "error_code": error_response.get("Error", {}).get("Code"),
            "error": str(error),
            "request_id": error_response.get("ResponseMetadata", {}).get("RequestId"),
        }


def rag_stream(
    client: Any,
    kb_id: str,
    model_arn: str,
    question: str,
    *,
    custom_prompt_templates: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = client.retrieve_and_generate_stream(
            input={"text": question},
            retrieveAndGenerateConfiguration=rag_configuration(
                kb_id,
                model_arn,
                custom_prompt_templates=custom_prompt_templates,
            ),
        )
        answer_parts: list[str] = []
        references: list[dict[str, Any]] = []
        event_counts: dict[str, int] = {}
        for event in response["stream"]:
            event_type = next(iter(event), "unknown")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            if "output" in event:
                answer_parts.append(event["output"].get("text", ""))
            if "citation" in event:
                citation = event["citation"].get("citation", event["citation"])
                references.extend(
                    citation_references([citation])
                    if "retrievedReferences" in citation
                    else []
                )
        return {
            "status": "OK",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "answer": "".join(answer_parts),
            "citations": references,
            "event_counts": event_counts,
            "session_id_present": bool(response.get("sessionId")),
            "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
        }
    except (BotoCoreError, ClientError) as error:
        error_response = getattr(error, "response", {})
        return {
            "status": "ERROR",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error_type": type(error).__name__,
            "error_code": error_response.get("Error", {}).get("Code"),
            "error": str(error),
            "request_id": error_response.get("ResponseMetadata", {}).get("RequestId"),
        }


def write_summary(result: dict[str, Any], output_path: Path) -> None:
    lines = [
        "# Haiku 4.5 / GPT-5.6 Luna 比較結果",
        "",
        f"- 実行日時（UTC）: {result['run']['started_at']}",
        f"- リージョン: `{result['run']['region']}`",
        f"- Knowledge Base: `{result['run']['knowledge_base_id']}`",
        "- 品質比較: 各質問で1回だけRetrieveし、同一チャンクを両モデルへ入力",
        "- 推論条件: `maxTokens=2048`、Haikuは`temperature=0`、Lunaはモデル既定値、同一system prompt",
        "",
        "## Retrieve + Converse",
        "",
        "| 質問 | モデル | 根拠内条件 | 回答条件 | 期待出典 | 入力tokens | cache write/read | 出力tokens | 推定費用 (USD) | レイテンシ (ms) |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for item in result["quality_comparison"]:
        for model_name, response in item["models"].items():
            if response["status"] != "OK":
                lines.append(
                    f"| {item['id']} | {model_name} "
                    f"| {item['retrieve']['context_fact_coverage']['hit_count']}/"
                    f"{item['retrieve']['context_fact_coverage']['total']} | ERROR "
                    f"| - | - | - | - | - | {response.get('latency_ms', 0):.1f} |"
                )
                continue
            coverage = response["fact_coverage"]
            context_coverage = item["retrieve"]["context_fact_coverage"]
            usage = response.get("usage", {})
            lines.append(
                f"| {item['id']} | {model_name} "
                f"| {context_coverage['hit_count']}/{context_coverage['total']} "
                f"| {coverage['hit_count']}/{coverage['total']} "
                f"| {'Yes' if response['expected_source_mentioned'] else 'No'} "
                f"| {usage.get('inputTokens', 0)} "
                f"| {usage.get('cacheWriteInputTokens', 0)}/"
                f"{usage.get('cacheReadInputTokens', 0)} "
                f"| {usage.get('outputTokens', 0)} "
                f"| {response.get('estimated_cost_usd', 0):.8f} "
                f"| {response.get('latency_ms', 0):.1f} |"
            )
    if not result["quality_comparison"]:
        lines.append("| （省略） | - | - | - | - | - | - | - | - | - |")

    lines.extend(["", "### 集計", ""])
    lines.extend([
        "| モデル | 成功/全件 | 入力tokens | cache write/read | 出力tokens | 推定費用 (USD) | 平均レイテンシ (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for model_name, aggregate in result["quality_aggregates"].items():
        lines.append(
            f"| {model_name} | {aggregate['successful_questions']}/"
            f"{aggregate['total_questions']} | {aggregate['input_tokens']} "
            f"| {aggregate['cache_write_input_tokens']}/"
            f"{aggregate['cache_read_input_tokens']} "
            f"| {aggregate['output_tokens']} | {aggregate['estimated_cost_usd']:.8f} "
            f"| {aggregate['average_latency_ms']:.1f} |"
        )
    lines.extend(["", "## API互換性", ""])
    if result.get("api_compatibility"):
        lines.extend([
            "| モデル | RetrieveAndGenerate | Stream | 非Stream引用数 | Stream引用数 |",
            "|---|---|---|---:|---:|",
        ])
        for model_name, checks in result["api_compatibility"].items():
            non_stream = checks["retrieve_and_generate"]
            stream = checks["retrieve_and_generate_stream"]
            lines.append(
                f"| {model_name} | {non_stream['status']} | {stream['status']} "
                f"| {len(non_stream.get('citations', []))} "
                f"| {len(stream.get('citations', []))} |"
            )
    else:
        lines.append("省略しました。")
    lines.extend([
        "",
        "詳細な回答、検索チャンク、期待条件ごとの判定、Request IDは "
        "`comparison-results.json` を参照してください。",
        "",
    ])
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    started_at = utc_now()
    script_dir = Path(__file__).resolve().parent
    repo_dir = script_dir.parent.parent
    output_dir = args.output_dir or (
        script_dir
        / "logs"
        / f"{started_at.strftime('%Y%m%d')}-haiku-luna"
        / started_at.strftime("%Y%m%dT%H%M%SZ")
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    agent_runtime = boto3.client("bedrock-agent-runtime", region_name=args.region)
    bedrock_runtime = boto3.client("bedrock-runtime", region_name=args.region)
    bedrock = boto3.client("bedrock", region_name=args.region)
    bedrock_agent = boto3.client("bedrock-agent", region_name=args.region)

    kb = bedrock_agent.get_knowledge_base(knowledgeBaseId=args.knowledge_base_id)[
        "knowledgeBase"
    ]
    jobs = bedrock_agent.list_ingestion_jobs(
        knowledgeBaseId=args.knowledge_base_id,
        dataSourceId=args.data_source_id,
        sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        maxResults=1,
    ).get("ingestionJobSummaries", [])
    if not jobs:
        raise SystemExit("取り込みジョブがありません。")
    latest_job = jobs[0]
    statistics = latest_job.get("statistics", {})
    if latest_job.get("status") != "COMPLETE":
        raise SystemExit(f"最新取り込みがCOMPLETEではありません: {latest_job.get('status')}")
    if statistics.get("numberOfDocumentsFailed", 0) != 0:
        raise SystemExit(f"取り込み失敗文書があります: {statistics}")
    if statistics.get("numberOfDocumentsScanned") != 33:
        raise SystemExit(f"検索対象が33件のMarkdown文書ではありません: {statistics}")
    storage_type = kb.get("storageConfiguration", {}).get("type")
    if storage_type != "S3_VECTORS":
        raise SystemExit(f"S3 Vectors KBではありません: {storage_type}")

    run = {
        "started_at": started_at.isoformat(),
        "region": args.region,
        "knowledge_base_id": args.knowledge_base_id,
        "data_source_id": args.data_source_id,
        "knowledge_base_name": kb.get("name"),
        "storage_type": storage_type,
        "latest_ingestion_job": jsonable(latest_job),
        "number_of_results": args.number_of_results,
        "system_prompt": SYSTEM_PROMPT,
        "inference_config_by_model": {
            model_name: inference_config(model["model_id"])
            for model_name, model in MODELS.items()
        },
        "pricing_date": PRICING_DATE,
        "pricing_sources": PRICING_SOURCES,
        "pricing_note": (
            "US Geo, Standard tier, USD/1M tokens; Lunaは272K以下。"
            "cache write/readを含む"
        ),
        "git": git_metadata(repo_dir),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "quality_comparison_skipped": args.skip_quality_comparison,
        "rag_compatibility_skipped": args.skip_rag_compatibility,
        "luna_foundation_model_availability": jsonable(
            bedrock.get_foundation_model_availability(
                modelId="openai.gpt-5.6-luna"
            )
        ),
    }

    quality_comparison = []
    for question in [] if args.skip_quality_comparison else QUESTIONS:
        retrieve_started = time.perf_counter()
        retrieve_response = agent_runtime.retrieve(
            knowledgeBaseId=args.knowledge_base_id,
            retrievalQuery={"text": question["question"]},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": args.number_of_results,
                }
            },
        )
        chunks = retrieve_response.get("retrievalResults", [])
        if not chunks:
            raise SystemExit(f"検索結果がありません: {question['id']}")
        item = {
            **question,
            "retrieve": {
                "latency_ms": round((time.perf_counter() - retrieve_started) * 1000, 1),
                "request_id": retrieve_response.get("ResponseMetadata", {}).get("RequestId"),
                "chunks": [compact_chunk(chunk) for chunk in chunks],
                "expected_source_rank": next(
                    (
                        index
                        for index, chunk in enumerate(chunks, 1)
                        if question["expected_source"] in extract_uri(chunk)
                    ),
                    None,
                ),
                "context_fact_coverage": fact_coverage(
                    "\n".join(
                        chunk.get("content", {}).get("text", "")
                        for chunk in chunks
                    ),
                    question["expected_facts"],
                ),
            },
            "models": {},
        }
        for model_name, model in MODELS.items():
            item["models"][model_name] = converse(
                bedrock_runtime,
                model,
                question["question"],
                chunks,
                question["expected_source"],
                question["expected_facts"],
            )
        quality_comparison.append(item)

    quality_aggregates: dict[str, Any] = {}
    for model_name in MODELS:
        responses = [
            item["models"][model_name]
            for item in quality_comparison
        ]
        successful = [response for response in responses if response["status"] == "OK"]
        quality_aggregates[model_name] = {
            "successful_questions": len(successful),
            "total_questions": len(responses),
            "input_tokens": sum(
                response.get("usage", {}).get("inputTokens", 0)
                for response in successful
            ),
            "cache_write_input_tokens": sum(
                response.get("usage", {}).get("cacheWriteInputTokens", 0)
                for response in successful
            ),
            "cache_read_input_tokens": sum(
                response.get("usage", {}).get("cacheReadInputTokens", 0)
                for response in successful
            ),
            "output_tokens": sum(
                response.get("usage", {}).get("outputTokens", 0)
                for response in successful
            ),
            "estimated_cost_usd": round(
                sum(response.get("estimated_cost_usd", 0) for response in successful),
                8,
            ),
            "average_latency_ms": round(
                sum(response["latency_ms"] for response in successful) / len(successful),
                1,
            )
            if successful
            else 0,
        }

    api_compatibility: dict[str, Any] = {}
    if not args.skip_rag_compatibility:
        compatibility_question = QUESTIONS[0]["question"]
        for model_name, model in MODELS.items():
            model_arn = profile_arn(bedrock, model["model_id"])
            custom_prompt_templates = model_name == "gpt_5_6_luna"
            api_compatibility[model_name] = {
                "model_id": model["model_id"],
                "inference_profile_arn": model_arn,
                "question": compatibility_question,
                "custom_prompt_templates": custom_prompt_templates,
                "prompt_templates": (
                    {
                        "orchestration": RAG_ORCHESTRATION_PROMPT,
                        "generation": RAG_GENERATION_PROMPT,
                    }
                    if custom_prompt_templates
                    else None
                ),
                "retrieve_and_generate": rag_non_stream(
                    agent_runtime,
                    args.knowledge_base_id,
                    model_arn,
                    compatibility_question,
                    custom_prompt_templates=custom_prompt_templates,
                ),
                "retrieve_and_generate_stream": rag_stream(
                    agent_runtime,
                    args.knowledge_base_id,
                    model_arn,
                    compatibility_question,
                    custom_prompt_templates=custom_prompt_templates,
                ),
            }

    result = {
        "run": run,
        "models": MODELS,
        "quality_comparison": quality_comparison,
        "quality_aggregates": quality_aggregates,
        "api_compatibility": api_compatibility,
        "finished_at": utc_now().isoformat(),
    }
    result_path = output_dir / "comparison-results.json"
    result_path.write_text(
        json.dumps(jsonable(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary(result, output_dir / "summary.md")
    print(output_dir)

    quality_errors = sum(
        response["status"] != "OK"
        for item in quality_comparison
        for response in item["models"].values()
    )
    compatibility_errors = sum(
        check["status"] != "OK"
        for model_checks in api_compatibility.values()
        for check_name, check in model_checks.items()
        if check_name in {"retrieve_and_generate", "retrieve_and_generate_stream"}
    )
    if quality_errors or compatibility_errors:
        raise SystemExit(
            f"APIエラーを記録しました: quality={quality_errors}, "
            f"compatibility={compatibility_errors}"
        )


if __name__ == "__main__":
    main()
