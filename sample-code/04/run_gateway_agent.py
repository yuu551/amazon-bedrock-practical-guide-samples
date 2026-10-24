#!/usr/bin/env python
"""ローカル動作確認用スクリプト

AgentCore Runtime へデプロイする前に、エージェントの挙動をローカルで
検証するためのスクリプトです。デプロイ後の本番動作確認は Amplify 画面
または InvokeAgentRuntime API から行います。

コンテナ版のエントリーポイントは amplify-ui/amplify/agent/main.py です。
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

import boto3
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient


@dataclass(frozen=True)
class Settings:
    region: str
    model_id: str
    knowledge_base_id: str
    gateway_url: str | None
    allowed_gateway_tools: tuple[str, ...]
    memory_id: str | None
    actor_id: str
    session_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Chapter 4 sample agent that combines a Knowledge Base with "
            "AgentCore Gateway tools such as Confluence search and Jira escalation."
        )
    )
    parser.add_argument(
        "--question",
        default=(
            "東京リージョンでファイルアップロードが失敗しているとのお問い合わせがありました。"
            "既知障害かどうかを確認し、一次回避策があれば案内してください。"
            "根拠が足りない、または重大障害の場合はJiraへエスカレーションしてください。"
        ),
        help="Single question to ask. Omit with --interactive to start a prompt loop.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Start an interactive prompt loop instead of sending a single question.",
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("SUPPORT_KB_ID") or os.environ.get("KNOWLEDGE_BASE_ID"),
        help="Knowledge Base ID for the support KB.",
    )
    parser.add_argument(
        "--gateway-url",
        default=os.environ.get("GATEWAY_URL"),
        help="AgentCore Gateway MCP endpoint URL.",
    )
    parser.add_argument(
        "--gateway-tool",
        action="append",
        default=[],
        help=(
            "Optional gateway tool name to keep. Repeat for multiple tools. "
            "If omitted, all Gateway tools are exposed to the agent."
        ),
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("MODEL_ID"),
        help="Bedrock model ID or inference profile ID used by Strands.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS Region.",
    )
    parser.add_argument(
        "--memory-id",
        default=os.environ.get("AGENTCORE_MEMORY_ID"),
        help=(
            "AgentCore Memory ID. If set, conversation history is persisted via "
            "AgentCoreMemorySessionManager."
        ),
    )
    parser.add_argument(
        "--actor-id",
        default=os.environ.get("AGENTCORE_ACTOR_ID", "support-portal-local"),
        help="actor_id used for AgentCore Memory namespacing.",
    )
    parser.add_argument(
        "--session-id",
        default=os.environ.get("AGENTCORE_SESSION_ID", "local-session"),
        help="session_id used for AgentCore Memory namespacing.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> Settings:
    missing: list[str] = []
    if not args.model_id:
        missing.append("MODEL_ID or --model-id")
    if not args.knowledge_base_id:
        missing.append("SUPPORT_KB_ID / KNOWLEDGE_BASE_ID or --knowledge-base-id")

    if missing:
        raise SystemExit("Missing required settings: " + ", ".join(missing))

    return Settings(
        region=args.region,
        model_id=args.model_id,
        knowledge_base_id=args.knowledge_base_id,
        gateway_url=args.gateway_url,
        allowed_gateway_tools=tuple(args.gateway_tool),
        memory_id=args.memory_id,
        actor_id=args.actor_id,
        session_id=args.session_id,
    )


def format_reference_location(location: dict[str, Any]) -> str:
    if "s3Location" in location:
        return location["s3Location"]["uri"]
    if "webLocation" in location:
        return location["webLocation"]["url"]
    if "confluenceLocation" in location:
        return location["confluenceLocation"].get("url", json.dumps(location, ensure_ascii=False))
    return json.dumps(location, ensure_ascii=False)


def build_support_kb_tool(
    *,
    runtime_client: Any,
    knowledge_base_id: str,
) -> Any:
    @tool
    def search_support_kb(query: str) -> str:
        """SampleConnect Platformサポートのナレッジ検索ツール。

        製品FAQ（SAML、APIキー、APIレート制限、監査ログなど）、製品設定ガイド、
        障害一次対応手順、エスカレーション手順、SLA・課金・セキュリティポリシーを
        Knowledge Basesから検索し、出典付きの根拠候補を返します。
        """
        response = runtime_client.retrieve(
            knowledgeBaseId=knowledge_base_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": 5,
                }
            },
        )

        snippets: list[str] = []
        for item in response.get("retrievalResults", []):
            location = format_reference_location(item["location"])
            content = item["content"]["text"].strip()
            score = item.get("score")
            if score is not None:
                snippets.append(f"[score={score:.3f}] {location}\n{content}")
            else:
                snippets.append(f"{location}\n{content}")

        if not snippets:
            return "support-kbでは該当する根拠を見つけられませんでした。"

        return "\n\n".join(snippets)

    return search_support_kb


def get_full_tools_list(client: MCPClient) -> list[Any]:
    tools: list[Any] = []
    pagination_token: str | None = None

    while True:
        page = client.list_tools_sync(pagination_token=pagination_token)
        tools.extend(page)
        pagination_token = getattr(page, "pagination_token", None)
        if pagination_token is None:
            return tools


def select_gateway_tools(tools: list[Any], allowed_names: tuple[str, ...]) -> list[Any]:
    if not allowed_names:
        return tools

    allowed = set(allowed_names)
    selected: list[Any] = []
    matched_names: set[str] = set()

    for tool_item in tools:
        visible_name = tool_item.tool_name
        suffix = visible_name.split("___", 1)[1] if "___" in visible_name else visible_name
        if visible_name in allowed or suffix in allowed:
            selected.append(tool_item)
            if visible_name in allowed:
                matched_names.add(visible_name)
            if suffix in allowed:
                matched_names.add(suffix)

    missing = sorted(allowed - matched_names)
    if missing:
        raise SystemExit(
            "Gateway tool not found: "
            + ", ".join(missing)
            + ". Run without --gateway-tool once to list available visible tool names."
        )
    return selected


def build_session_manager(settings: Settings) -> AgentCoreMemorySessionManager | None:
    if not settings.memory_id:
        return None
    config = AgentCoreMemoryConfig(
        memory_id=settings.memory_id,
        actor_id=settings.actor_id,
        session_id=settings.session_id,
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=settings.region,
    )


def build_agent(
    *,
    settings: Settings,
    gateway_tools: list[Any],
    session_manager: AgentCoreMemorySessionManager | None = None,
) -> Agent:
    runtime_client = boto3.client("bedrock-agent-runtime", region_name=settings.region)
    support_kb_tool = build_support_kb_tool(
        runtime_client=runtime_client,
        knowledge_base_id=settings.knowledge_base_id,
    )

    system_prompt = """
あなたはSampleConnect Platform（B2B SaaS）のサポート問い合わせ対応エージェントです。
顧客サポート担当者を支援する立場で、SAML、APIキー、APIレート制限、監査ログ、SLA、
障害事象などに関する問い合わせに、社内ナレッジを根拠として回答します。

次の方針で行動してください。

1. まずsupport-kbを検索して、製品FAQ・製品ガイド・障害一次対応手順・SLA/課金/セキュリティポリシーから根拠候補を集める
2. KBで完結する単純なFAQや設定ガイド質問では、追加ツールを呼ばずにそのまま回答する
3. 障害調査・既知事象確認のために、直近の障害メモや暫定回避策が必要なら、Gateway配下のConfluence系ツールを使う
4. 根拠が足りない、重大障害の可能性が高い、または人の判断が必要な場合は、Gateway配下のJira系ツールでサポートチケットを起票してエスカレーションする
5. 推測で埋めない。一般知識で補完しない

回答には必ず次の3点を含めてください。
- 最終回答
- 確認した参照先
- Jira起票の有無
""".strip()

    model = BedrockModel(
        model_id=settings.model_id,
        region_name=settings.region,
        streaming=True,
    )
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[support_kb_tool, *gateway_tools],
        session_manager=session_manager,
    )


def print_available_tools(gateway_tools: list[Any]) -> None:
    if not gateway_tools:
        print("Gateway tools: none")
        return

    print("Gateway tools:")
    for tool_item in gateway_tools:
        print(f"- {tool_item.tool_name}")


def run_single_question(agent: Agent, question: str) -> None:
    print(f"\nQuestion:\n{question}\n")
    result = agent(question)
    content = getattr(result, "message", None)
    if isinstance(content, dict):
        print("\nResponse:")
        print(content.get("content", content))
    else:
        print("\nResponse:")
        print(result)


def run_interactive(agent: Agent) -> None:
    print("\nInteractive mode. Type 'exit' to stop.\n")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"exit", "quit", "bye"}:
            print("Bye.")
            return
        if not user_input:
            continue
        run_single_question(agent, user_input)
        print()


def main() -> None:
    args = parse_args()
    settings = validate_args(args)
    gateway_tools: list[Any] = []
    session_manager = build_session_manager(settings)
    if session_manager is None:
        print("AgentCore Memory not configured. Running without session manager.")
    else:
        print(
            f"AgentCore Memory enabled: actor_id={settings.actor_id}, "
            f"session_id={settings.session_id}"
        )

    if settings.gateway_url:
        mcp_client = MCPClient(lambda: aws_iam_streamablehttp_client(
            endpoint=settings.gateway_url,
            aws_region=settings.region,
            aws_service="bedrock-agentcore",
        ))
        with mcp_client:
            gateway_tools = select_gateway_tools(
                get_full_tools_list(mcp_client),
                settings.allowed_gateway_tools,
            )
            print_available_tools(gateway_tools)
            agent = build_agent(
                settings=settings,
                gateway_tools=gateway_tools,
                session_manager=session_manager,
            )
            if args.interactive:
                run_interactive(agent)
            else:
                run_single_question(agent, args.question)
            return

    print("Gateway URL not set. Running with Knowledge Base search only.")
    agent = build_agent(
        settings=settings,
        gateway_tools=gateway_tools,
        session_manager=session_manager,
    )
    if args.interactive:
        run_interactive(agent)
    else:
        run_single_question(agent, args.question)


if __name__ == "__main__":
    main()
