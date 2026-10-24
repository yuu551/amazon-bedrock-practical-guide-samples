#!/usr/bin/env python
"""AgentCore Runtime コンテナのエントリーポイント

環境変数:
  KNOWLEDGE_BASE_ID    : Knowledge Base ID
  MODEL_ID             : Bedrock モデル ID（INFERENCE_PROFILE_ARN 未指定時に使用）
  INFERENCE_PROFILE_ARN: アプリケーション推論プロファイル ARN（指定時はこちらを優先）
  GUARDRAIL_ID         : Guardrail ID（任意、Converse 呼び出しで適用）
  GUARDRAIL_VERSION    : Guardrail バージョン（任意、未指定なら "DRAFT"）
  GATEWAY_URL          : AgentCore Gateway MCP エンドポイント（任意）
  AGENTCORE_MEMORY_ID  : AgentCore Memory リソース ID（セッション継続用）

ストリーミング出力は BedrockAgentCoreApp が async generator を SSE として
フロントへ流す。Strands のイベントを UI 向けに変換して流す。
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

import boto3
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient


# ── ツール定義 ─────────────────────────────────────────────


def format_reference_location(location: dict[str, Any]) -> str:
    if "s3Location" in location:
        return location["s3Location"]["uri"]
    if "webLocation" in location:
        return location["webLocation"]["url"]
    if "confluenceLocation" in location:
        return location["confluenceLocation"].get(
            "url", json.dumps(location, ensure_ascii=False)
        )
    return json.dumps(location, ensure_ascii=False)


def _optional_string(value: Any) -> str | None:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    return None


@lru_cache(maxsize=1)
def _source_prefixes() -> tuple[str, ...]:
    return tuple(
        prefix.strip("/")
        for prefix in os.environ.get("KNOWLEDGE_BASE_SOURCE_PREFIXES", "").split(",")
        if prefix.strip("/")
    )


@lru_cache(maxsize=1)
def _s3_client():
    return boto3.client("s3", region_name=_region())


def _download_url(uri: str) -> str | None:
    if not uri.startswith("s3://"):
        return None
    bucket, separator, key = uri.removeprefix("s3://").partition("/")
    allowed_bucket = os.environ.get("KNOWLEDGE_BASE_SOURCE_BUCKET_NAME", "")
    prefixes = _source_prefixes()
    if (
        not separator
        or bucket != allowed_bucket
        or not any(key.startswith(f"{prefix}/") for prefix in prefixes)
    ):
        return None
    try:
        return _s3_client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                **({"ResponseContentType": "text/plain; charset=utf-8"}
                   if key.lower().endswith((".md", ".txt")) else {}),
            },
            ExpiresIn=900,
        )
    except Exception:
        return None


def _citation_from_result(item: dict[str, Any]) -> dict[str, Any]:
    uri = format_reference_location(item["location"])
    metadata = item.get("metadata") or {}
    source_uri = _optional_string(metadata.get("sourceUri"))
    target = source_uri or uri
    download_url = _download_url(target)
    access = (_optional_string(metadata.get("access_status")) or "").lower()
    status = (
        "restricted"
        if access == "restricted"
        else "available"
        if target.startswith(("http://", "https://")) or download_url
        else "unavailable"
    )
    return {
        "uri": uri,
        "snippet": item.get("content", {}).get("text", "").strip(),
        "chunkId": _optional_string(metadata.get("x-amz-bedrock-kb-chunk-id")),
        "title": _optional_string(metadata.get("title"))
        or _optional_string(metadata.get("document_title"))
        or _optional_string(metadata.get("file_name")),
        "sourceUri": source_uri,
        "downloadUrl": download_url,
        "updatedAt": _optional_string(metadata.get("updated_at"))
        or _optional_string(metadata.get("last_modified"))
        or _optional_string(metadata.get("effective_date")),
        "status": status,
    }


def _citation_key(citation: dict[str, Any]) -> tuple[str, str]:
    source = citation.get("sourceUri") or citation.get("uri")
    if source:
        return ("source", source)
    chunk_id = citation.get("chunkId")
    if chunk_id:
        return ("chunk", chunk_id)
    snippet = " ".join(citation["snippet"].split())
    return ("content", f'{citation["uri"]}::{snippet}')


def _find_confluence_results(value: Any):
    if isinstance(value, dict):
        results = value.get("results")
        if isinstance(results, list):
            for item in results:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("url"), str)
                    and item["url"].startswith(("http://", "https://"))
                ):
                    yield item
        for nested in value.values():
            yield from _find_confluence_results(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _find_confluence_results(nested)


def collect_confluence_citations(
    event: Any,
    citation_sink: list[dict[str, Any]],
) -> None:
    """Confluenceツールの検索結果を、画面下部の引用元へ追加する。"""
    if not hasattr(event, "get"):
        return
    message = event.get("message")
    if not isinstance(message, dict):
        return

    payloads: list[Any] = []
    for block in message.get("content", []):
        if not isinstance(block, dict):
            continue
        tool_result = block.get("toolResult")
        if not isinstance(tool_result, dict):
            continue
        structured = tool_result.get("structuredContent")
        if structured is not None:
            payloads.append(structured)
        for content in tool_result.get("content", []):
            if not isinstance(content, dict):
                continue
            if "json" in content:
                payloads.append(content["json"])
            text = content.get("text")
            if isinstance(text, str):
                try:
                    payloads.append(json.loads(text))
                except json.JSONDecodeError:
                    pass

    known_keys = {_citation_key(citation) for citation in citation_sink}
    for payload in payloads:
        for item in _find_confluence_results(payload):
            url = item["url"]
            raw_snippet = str(item.get("excerpt") or item.get("body") or "")
            snippet = re.sub(r"@@@(?:hl|endhl)@@@", "", raw_snippet)
            snippet = " ".join(snippet.split())[:360]
            citation = {
                "uri": url,
                "chunkId": f"confluence:{url}",
                "title": _optional_string(item.get("title")),
                "snippet": snippet,
                "sourceUri": url,
                "downloadUrl": None,
                "updatedAt": None,
                "status": "available",
            }
            key = _citation_key(citation)
            if key not in known_keys:
                citation_sink.append(citation)
                known_keys.add(key)


def build_support_kb_tool(
    *,
    knowledge_base_id: str,
    region: str,
    citation_sink: list[dict[str, Any]],
) -> Any:
    runtime_client = boto3.client("bedrock-agent-runtime", region_name=region)

    @tool
    def search_support_kb(query: str) -> str:
        """障害一次対応手順、既知事象、運用FAQを検索し、根拠候補を返します。"""
        response = runtime_client.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": 5}
            },
        )
        snippets: list[str] = []
        for item in response.get("retrievalResults", []):
            citation = _citation_from_result(item)
            key = _citation_key(citation)
            existing_index = next(
                (
                    index
                    for index, existing in enumerate(citation_sink)
                    if _citation_key(existing) == key
                ),
                None,
            )
            if existing_index is None:
                citation_sink.append(citation)
                citation_number = len(citation_sink)
            else:
                citation_number = existing_index + 1
            location = citation["sourceUri"] or citation["uri"]
            content = item["content"]["text"].strip()
            score = item.get("score")
            if score is not None:
                snippets.append(
                    f"[参照{citation_number}] [score={score:.3f}] {location}\n{content}"
                )
            else:
                snippets.append(f"[参照{citation_number}] {location}\n{content}")
        return "\n\n".join(snippets) or "該当する根拠を見つけられませんでした。"

    return search_support_kb


SYSTEM_PROMPT = """\
あなたはB2B SaaSの障害問い合わせ対応エージェントです。
次の方針で行動してください。

1. まずsupport-kbを検索して、既知事象や一次回避策の候補を集める
2. 直近の障害メモや暫定回避策が必要なら、Gateway配下のConfluence系ツールを使う
3. 根拠が十分に揃った場合だけ回答する
4. 根拠が足りない、重大障害の可能性が高い、または人の判断が必要な場合は、\
Gateway配下のJira系ツールでエスカレーションする
5. 推測で埋めない

回答はMarkdownで簡潔に作成してください。根拠のある文の末尾には、検索結果に付いた番号を
使って [1]、[2] の形式で引用番号を付けてください。参照URLの一覧を本文に再掲する必要は
ありません。回答本文に内部ツール名や検索の途中経過は書かず、確認結果から始めてください。
参照先に触れる場合は「製品資料」「社内文書」と表現します。Jiraへ起票した場合は、担当
チケットのキーと理由を明示してください。"""


# ── Strands イベント → フロント向け SSE 形式への変換 ─────────


def convert_event(event: Any) -> dict | None:
    try:
        if not hasattr(event, "get"):
            return None
        inner = event.get("event")
        if not inner:
            return None

        delta = inner.get("contentBlockDelta")
        if delta:
            text = delta.get("delta", {}).get("text")
            if text:
                return {"type": "text", "data": text}

        start = inner.get("contentBlockStart")
        if start:
            tool_use = start.get("start", {}).get("toolUse")
            if tool_use:
                return {
                    "type": "tool_use",
                    "tool_name": tool_use.get("name", "unknown"),
                    "tool_use_id": tool_use.get("toolUseId"),
                }

        stop = inner.get("contentBlockStop")
        if stop is not None:
            return {"type": "tool_result"}

        return None
    except Exception:
        return None


# ── MCP ツール一覧取得 ──────────────────────────────────────


def _get_full_tools_list(client: MCPClient) -> list[Any]:
    tools: list[Any] = []
    pagination_token: str | None = None
    while True:
        page = client.list_tools_sync(pagination_token=pagination_token)
        tools.extend(page)
        pagination_token = getattr(page, "pagination_token", None)
        if pagination_token is None:
            return tools


# ── AgentCore App ────────────────────────────────────────────


app = BedrockAgentCoreApp()
log = app.logger


def _region() -> str:
    return (
        os.environ.get("BEDROCK_REGION")
        or boto3.session.Session().region_name
        or os.environ.get("AWS_REGION", "us-east-1")
    )


def _build_agent(
    gateway_tools: list[Any] | None = None,
    session_manager: AgentCoreMemorySessionManager | None = None,
    citation_sink: list[dict[str, Any]] | None = None,
) -> Agent:
    kb_id = os.environ["KNOWLEDGE_BASE_ID"]
    # アプリケーション推論プロファイル が指定されていればそちらを優先する
    # （ARN を modelId として渡せば Bedrock 側で推論プロファイル経由のルーティングになる）。
    model_identifier = (
        os.environ.get("INFERENCE_PROFILE_ARN") or os.environ["MODEL_ID"]
    )
    region = _region()

    kb_tool = build_support_kb_tool(
        knowledge_base_id=kb_id,
        region=region,
        citation_sink=citation_sink if citation_sink is not None else [],
    )

    guardrail_id = os.environ.get("GUARDRAIL_ID")
    guardrail_version = os.environ.get("GUARDRAIL_VERSION") or "DRAFT"
    bedrock_kwargs: dict[str, Any] = {
        "model_id": model_identifier,
        "region_name": region,
        "streaming": True,
    }
    if guardrail_id:
        # BedrockModel の guardrail_config は Converse 呼び出し時に適用される
        bedrock_kwargs["guardrail_id"] = guardrail_id
        bedrock_kwargs["guardrail_version"] = guardrail_version
    model = BedrockModel(**bedrock_kwargs)

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[kb_tool, *(gateway_tools or [])],
        session_manager=session_manager,
    )


def _build_session_manager(
    actor_id: str | None,
    session_id: str | None,
) -> AgentCoreMemorySessionManager | None:
    memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
    if not memory_id or not actor_id or not session_id:
        return None
    config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        actor_id=actor_id,
        session_id=session_id,
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=_region(),
    )


@app.entrypoint
async def invoke(payload: dict, context):
    prompt = (payload.get("prompt") or payload.get("question") or "").strip()
    if not prompt:
        yield {"type": "error", "data": "prompt が指定されていません。"}
        return

    actor_id = (payload.get("actor_id") or payload.get("user_id") or "").strip() or None
    session_id = (payload.get("session_id") or "").strip() or None

    gateway_url = os.environ.get("GATEWAY_URL")
    mcp_client: MCPClient | None = None
    gateway_tools: list[Any] = []

    if gateway_url:
        mcp_client = MCPClient(
            lambda: aws_iam_streamablehttp_client(
                endpoint=gateway_url,
                aws_region=_region(),
                aws_service="bedrock-agentcore",
            )
        )
        mcp_client.__enter__()
        try:
            gateway_tools = _get_full_tools_list(mcp_client)
        except Exception as e:
            log.warning(f"Gateway tools unavailable: {e}")

    citations: list[dict[str, Any]] = []
    session_manager = _build_session_manager(actor_id=actor_id, session_id=session_id)
    agent = _build_agent(
        gateway_tools=gateway_tools,
        session_manager=session_manager,
        citation_sink=citations,
    )

    try:
        answer_parts: list[str] = []
        used_tools: list[str] = []
        stream = agent.stream_async(prompt)
        async for event in stream:
            collect_confluence_citations(event, citations)
            converted = convert_event(event)
            if converted:
                if converted["type"] == "text":
                    answer_parts.append(str(converted.get("data") or ""))
                elif converted["type"] == "tool_use":
                    used_tools.append(str(converted.get("tool_name") or "unknown"))
                yield converted
        for citation in citations:
            yield {"type": "citation", "data": citation}

        answer = "".join(answer_parts)
        issue_match = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", answer)
        escalated = any(
            "jira" in tool_name.lower() or "ticket" in tool_name.lower()
            for tool_name in used_tools
        ) or issue_match is not None
        yield {
            "type": "complete",
            "data": {
                "answer": answer,
                "citations": citations,
                "jira": {
                    "escalated": escalated,
                    "issueKey": issue_match.group(1) if issue_match else None,
                    "reason": (
                        "根拠の追加確認または有人判断が必要なため"
                        if escalated
                        else None
                    ),
                },
            },
        }
    except Exception as e:
        log.error(f"Agent error: {e}", exc_info=True)
        yield {"type": "error", "data": "回答中にエラーが発生しました。"}
    finally:
        if mcp_client:
            try:
                mcp_client.__exit__(None, None, None)
            except Exception:
                pass


if __name__ == "__main__":
    app.run()
