"""AWS Agent Registryから承認済みのスキルを探し、Strandsのスキルとして読み込む。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from strands.vended_plugins.skills import Skill


@dataclass(frozen=True)
class LoadedSkill:
    skill: Skill
    record_id: str
    version: str


def skill_markdown_from_record(record: dict[str, Any]) -> str:
    """検索APIで取得したSKILLレコードからSKILL.mdの本文を取り出す。"""

    try:
        markdown = record["descriptors"]["agentSkillsDefinition"]["additionalData"][
            "skillMd"
        ]["data"]
    except (KeyError, TypeError) as error:
        raise RuntimeError("SKILLレコードにSKILL.mdがありません") from error
    if not isinstance(markdown, str) or not markdown.strip():
        raise RuntimeError("取得したSKILL.mdが空です")
    return markdown


class RegistrySkillLoader:
    def __init__(self, registry_arn: str, region: str) -> None:
        self.registry_arn = registry_arn
        self.client = boto3.client("agent-registry", region_name=region)

    def load(self, query: str) -> list[LoadedSkill]:
        # 検索APIが返すのは承認済みの版だけ。下書きや審査中の版は含まれない
        found = self.client.search_discoverable_registry_records(
            registryIds=[self.registry_arn],
            searchQuery=query,
            filters={"recordType": {"$eq": "SKILL"}},
            maxResults=5,
        )["registryRecords"]
        if not found:
            return []

        # SKILL.mdの本文は一括取得APIで受け取る
        registry_id = self.registry_arn.rsplit("/", 1)[-1]
        records = self.client.batch_get_discoverable_registry_record(
            entries=[{"registryId": registry_id, "recordIds": [r["recordId"] for r in found]}]
        )["registryRecords"]
        return [
            LoadedSkill(
                skill=Skill.from_content(skill_markdown_from_record(record)),
                record_id=record["recordId"],
                version=record["recordVersion"],
            )
            for record in records
        ]
