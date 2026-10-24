#!/usr/bin/env python3
"""AWS Agent Registryにスキルを登録し、エージェントが実行時に読み込む流れを1段ずつ試す。

  uv run python register_and_search_registry.py setup     # レジストリとレコードを下書きで作る
  uv run python register_and_search_registry.py ask       # Registryからスキルを探して回答する
  uv run python register_and_search_registry.py approve   # 下書き・審査中のレコードを承認する
  uv run python register_and_search_registry.py revise    # スキルの次のバージョンを下書きで作る
  uv run python register_and_search_registry.py cleanup   # レコードとレジストリを削除する
  uv run python register_and_search_registry.py demo --yes  # 上の流れを通しで実行して削除する
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Callable

import boto3
from strands import Agent
from strands.models import BedrockModel
from strands.vended_plugins.skills import AgentSkills

from registry_skill_loader import RegistrySkillLoader


DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
TERMINAL_FAILURES = {"CREATE_FAILED", "UPDATE_FAILED", "REJECTED"}
HERE = Path(__file__).parent
STATE_FILE = HERE / ".registry_state.json"
SKILL_PATH = HERE / "skills" / "障害一次対応" / "SKILL.md"
DEFAULT_QUESTION = (
    "10:05から複数の利用者がログインできません。影響範囲と原因はまだ調査中です。"
    "障害一次対応の案内を作ってください。"
)


def json_data(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def id_from_arn(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


def agent_descriptor(suffix: str) -> dict[str, Any]:
    """AGENTレコードへ保存するA2A Agent Cardを作る。"""

    agent_card = {
        "name": f"sampleconnect-incident-agent-{suffix}",
        "description": "SampleConnectの障害報告を整理する一次対応エージェント",
        "version": "1.0.0",
        "protocolVersion": "0.3.0",
        "url": "https://support-agent.example.com/a2a",
        "capabilities": {},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "incident-first-response",
                "name": "障害一次対応",
                "description": "事実、未確認事項、次の対応を分けて整理する",
                "tags": ["incident", "support"],
            }
        ],
    }
    return {
        "a2aAgentCard": {
            "data": json_data(agent_card),
            "dataSchemaVersion": "0.3",
        }
    }


def mcp_descriptor(suffix: str) -> dict[str, Any]:
    server = {
        "name": f"sampleconnect/support-{suffix}",
        "description": "SampleConnectの問い合わせ対応用MCPサーバー",
        "version": "1.0.0",
    }
    tools = {
        "tools": [
            {
                "name": "search_support_knowledge",
                "description": "問い合わせ文から承認済みナレッジを検索する",
                "inputSchema": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
            }
        ]
    }
    return {
        "mcpServer": {
            "data": json_data(server),
            "dataSchemaVersion": "2025-12-11",
            "additionalData": {
                "tools": {
                    "data": json_data(tools),
                    "dataSchemaVersion": "2025-11-25",
                }
            },
        }
    }


def skill_descriptor(skill_markdown: str) -> dict[str, Any]:
    definition = {
        "name": "incident-first-response",
        "description": "障害報告の確認事項と初動を整理する",
    }
    return {
        "agentSkillsDefinition": {
            "data": json_data(definition),
            "dataSchemaVersion": "0.1.0",
            "additionalData": {
                "skillMd": {
                    "data": skill_markdown,
                }
            },
        }
    }


def updated_skill_descriptor(skill_markdown: str) -> dict[str, Any]:
    """更新API用に、変更する各フィールドをoptionalValueで包む。"""

    definition = {
        "name": "incident-first-response",
        "description": "障害報告の確認事項と初動を整理する",
    }
    return {
        "optionalValue": {
            "agentSkillsDefinition": {
                "optionalValue": {
                    "data": {"optionalValue": json_data(definition)},
                    "dataSchemaVersion": {"optionalValue": "0.1.0"},
                    "additionalData": {
                        "optionalValue": {
                            "skillMd": {
                                "optionalValue": {
                                    "data": {"optionalValue": skill_markdown},
                                }
                            }
                        }
                    },
                }
            }
        }
    }


def revised_skill_markdown(skill_markdown: str) -> str:
    """再承認の確認用に、引き継ぎ時の注意を追加した次のバージョンを作る。"""

    return (
        skill_markdown.rstrip()
        + "\n\n## 引き継ぎ時の注意\n\n"
        + "担当チームへ渡す情報にも、確認済みと未確認を明記します。\n"
    )


def next_version(version: str) -> str:
    major, minor, _ = (int(part) for part in version.split("."))
    return f"{major}.{minor + 1}.0"


def wait_until(
    description: str,
    predicate: Callable[[], bool],
    timeout_seconds: int,
    interval_seconds: int = 2,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval_seconds)
    raise TimeoutError(f"{description}を{timeout_seconds}秒以内に確認できませんでした")


# ---------------------------------------------------------------------------
# 状態ファイル（作ったレジストリとレコードのID）
# ---------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        raise SystemExit("先に setup を実行してください。")
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Registry:
    def __init__(self, region: str, timeout_seconds: int) -> None:
        self.region = region
        self.timeout = timeout_seconds
        self.control = boto3.client("agent-registry-control", region_name=region)

    def record_status(self, registry_id: str, record_id: str) -> dict[str, Any]:
        record = self.control.get_registry_record(registryId=registry_id, recordId=record_id)
        if record["status"] in TERMINAL_FAILURES:
            raise RuntimeError(f"レコード{record_id}の状態遷移に失敗しました（{record['status']}）")
        return record

    def wait_status(self, registry_id: str, record_id: str, status: str) -> None:
        wait_until(
            f"レコード{record_id}が{status}になること",
            lambda: self.record_status(registry_id, record_id)["status"] == status,
            self.timeout,
        )

    # --- setup ---------------------------------------------------------------
    def setup(self) -> None:
        if STATE_FILE.exists():
            raise SystemExit(f"{STATE_FILE.name}が残っています。cleanupしてから実行してください。")
        suffix = f"{int(time.time())}-{secrets.token_hex(2)}"
        created = self.control.create_registry(
            name=f"book-agent-registry-{suffix}",
            description="書籍補足の動作確認用レジストリ",
            discoveryConfiguration={"authorizerType": "AWS_IAM"},
            approvalConfiguration={"autoApprovalRules": []},
            tags={"Purpose": "book-agent-registry-supplement"},
        )
        registry_arn = created["registryArn"]
        registry_id = id_from_arn(registry_arn)
        state = {"region": self.region, "registry_arn": registry_arn,
                 "registry_id": registry_id, "records": {}}
        save_state(state)
        wait_until(
            "レジストリがREADYになること",
            lambda: self.control.get_registry(registryId=registry_id)["status"] == "READY",
            self.timeout,
        )
        print(f"レジストリ: READY ({registry_id})")

        records = {
            "AGENT": ("sampleconnect-incident-agent", "SampleConnect障害一次対応Agent",
                      "障害報告を整理するA2Aエージェント", agent_descriptor(suffix)),
            "MCP": ("sampleconnect-support-mcp", "SampleConnect問い合わせ対応MCP",
                    "承認済みナレッジを検索する問い合わせ対応ツール", mcp_descriptor(suffix)),
            "SKILL": ("sampleconnect-incident-skill", "SampleConnect障害一次対応Skill",
                      "障害報告の確認事項と初動を整理するAgent Skill",
                      skill_descriptor(SKILL_PATH.read_text(encoding="utf-8"))),
        }
        for record_type, (name, display_name, description, descriptors) in records.items():
            response = self.control.create_registry_record(
                registryId=registry_id,
                name=f"{name}-{suffix}",
                displayName=display_name,
                description=description,
                recordType=record_type,
                recordVersion="1.0.0",
                descriptors=descriptors,
                tags={"Purpose": "book-agent-registry-supplement"},
            )
            record_id = id_from_arn(response["recordArn"])
            state["records"][record_type] = record_id
            save_state(state)
            self.wait_status(registry_id, record_id, "DRAFT")
            print(f"{record_type}: DRAFT 1.0.0 ({record_id})")
        print("下書きのレコードは、まだ検索APIには出てきません。")

    # --- approve -------------------------------------------------------------
    def approve(self) -> None:
        state = load_state()
        registry_id = state["registry_id"]
        for record_type, record_id in state["records"].items():
            record = self.record_status(registry_id, record_id)
            if record["status"] == "APPROVED":
                continue
            if record["status"] == "DRAFT":
                self.control.submit_registry_record_for_approval(
                    registryId=registry_id, recordId=record_id
                )
                self.wait_status(registry_id, record_id, "PENDING_APPROVAL")
            self.control.update_registry_record_status(
                registryId=registry_id,
                recordId=record_id,
                status="APPROVED",
                statusReason="内容と接続先を確認済み",
            )
            self.wait_status(registry_id, record_id, "APPROVED")
            print(f"{record_type}: APPROVED {record['recordVersion']}")

    # --- revise --------------------------------------------------------------
    def revise(self) -> None:
        state = load_state()
        registry_id, skill_id = state["registry_id"], state["records"]["SKILL"]
        current = self.record_status(registry_id, skill_id)
        if current["status"] != "APPROVED":
            raise SystemExit("スキルが承認済みではありません。先に approve を実行してください。")
        version = next_version(current["recordVersion"])
        markdown = current["descriptors"]["agentSkillsDefinition"]["additionalData"][
            "skillMd"
        ]["data"]
        self.control.update_registry_record(
            registryId=registry_id,
            recordId=skill_id,
            recordVersion=version,
            # 更新APIでは、新規作成時と異なり変更する各値をoptionalValueで包む
            descriptors=updated_skill_descriptor(revised_skill_markdown(markdown)),
        )
        self.wait_status(registry_id, skill_id, "DRAFT")
        print(f"SKILL: {version}を下書きで作成（検索APIは承認済みの{current['recordVersion']}のまま）")

    # --- cleanup -------------------------------------------------------------
    def cleanup(self) -> None:
        state = load_state()
        registry_id = state["registry_id"]
        for record_id in state["records"].values():
            try:
                self.control.delete_registry_record(registryId=registry_id, recordId=record_id)
            except self.control.exceptions.ResourceNotFoundException:
                pass

        def records_deleted() -> bool:
            return not self.control.list_registry_records(registryId=registry_id).get(
                "registryRecords"
            )

        wait_until("レコードの削除", records_deleted, self.timeout)
        try:
            self.control.delete_registry(registryId=registry_id)
        except self.control.exceptions.ResourceNotFoundException:
            pass
        STATE_FILE.unlink()
        print(f"削除: レコード{len(state['records'])}件とレジストリ{registry_id}")


# ---------------------------------------------------------------------------
# エージェントが実行時にスキルを読み込んで回答する
# ---------------------------------------------------------------------------

def ask(region: str, model_id: str, question: str, query: str = "障害一次対応") -> str:
    state = load_state()
    loaded = RegistrySkillLoader(state["registry_arn"], region).load(query)
    if loaded:
        for item in loaded:
            print(f"読み込んだスキル: {item.skill.name} {item.version} ({item.record_id})")
    else:
        print("承認済みのスキルが見つからないため、スキルなしで回答します。")

    agent = Agent(
        model=BedrockModel(model_id=model_id, region_name=region, streaming=False),
        system_prompt="あなたはSampleConnectのサポート担当エージェントです。",
        plugins=[AgentSkills(skills=[item.skill for item in loaded])] if loaded else [],
        callback_handler=None,
    )
    answer = str(agent(question)).strip()
    print(answer)
    return answer


def demo(region: str, timeout: int, model_id: str) -> None:
    registry = Registry(region, timeout)
    try:
        registry.setup()
        print("\n--- 承認前に質問する ---")
        ask(region, model_id, DEFAULT_QUESTION)
        print("\n--- 承認する ---")
        registry.approve()
        wait_for_discoverable_version(region, timeout, "1.0.0")
        print("\n--- 承認後に質問する ---")
        ask(region, model_id, DEFAULT_QUESTION)
        print("\n--- 次のバージョンを作る ---")
        registry.revise()
        print("\n--- 次のバージョンの審査中に質問する ---")
        ask(region, model_id, DEFAULT_QUESTION)
        print("\n--- 次のバージョンを承認する ---")
        registry.approve()
        wait_for_discoverable_version(region, timeout, "1.1.0")
        print("\n--- 承認後に質問する ---")
        ask(region, model_id, DEFAULT_QUESTION)
    finally:
        if STATE_FILE.exists():
            registry.cleanup()


def wait_for_discoverable_version(region: str, timeout: int, version: str) -> None:
    """承認から検索への反映には少し時間がかかるので、指定のバージョンが見えるまで待つ。"""

    state = load_state()
    loader = RegistrySkillLoader(state["registry_arn"], region)
    wait_until(
        f"検索APIでスキル{version}が見えること",
        lambda: any(item.version == version for item in loader.load("障害一次対応")),
        timeout,
        interval_seconds=3,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["setup", "ask", "approve", "revise", "cleanup", "demo"])
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION,
                        help="ask で送る質問（省略時は障害一次対応の例）")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--yes", action="store_true", help="demo で一時リソースの作成と削除を許可する")
    args = parser.parse_args()

    if args.command == "demo":
        if not args.yes:
            parser.error("demo を実行するには--yesを指定してください")
        demo(args.region, args.timeout, args.model_id)
    elif args.command == "ask":
        ask(args.region, args.model_id, args.question)
    else:
        getattr(Registry(args.region, args.timeout), args.command)()


if __name__ == "__main__":
    main()
