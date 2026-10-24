import unittest

from register_and_search_registry import (
    agent_descriptor,
    next_version,
    revised_skill_markdown,
    skill_descriptor,
    updated_skill_descriptor,
)
from registry_skill_loader import skill_markdown_from_record
from strands.vended_plugins.skills import Skill


class RegistryDemoTest(unittest.TestCase):
    def test_agent_descriptor_contains_a2a_card_and_skill(self) -> None:
        card = agent_descriptor("demo")["a2aAgentCard"]
        self.assertEqual(card["dataSchemaVersion"], "0.3")
        self.assertIn('"protocolVersion":"0.3.0"', card["data"])
        self.assertIn('"id":"incident-first-response"', card["data"])

    def test_skill_markdown_round_trip_becomes_strands_skill(self) -> None:
        markdown = "---\nname: demo-skill\ndescription: 手順の例\n---\n\n# Skill\n\n手順です。"
        record = {"recordType": "SKILL", "descriptors": skill_descriptor(markdown)}
        loaded = skill_markdown_from_record(record)
        self.assertEqual(loaded, markdown)
        self.assertEqual(Skill.from_content(loaded).name, "demo-skill")

    def test_empty_skill_markdown_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            skill_markdown_from_record({"descriptors": skill_descriptor(" ")})

    def test_next_version_increments_minor(self) -> None:
        self.assertEqual(next_version("1.0.0"), "1.1.0")
        self.assertEqual(next_version("1.1.0"), "1.2.0")

    def test_revised_skill_adds_handoff_rule(self) -> None:
        original = "# 障害一次対応\n"
        revised = revised_skill_markdown(original)
        self.assertTrue(revised.startswith(original))
        self.assertIn("## 引き継ぎ時の注意", revised)

    def test_updated_skill_descriptor_wraps_each_changed_value(self) -> None:
        skill = updated_skill_descriptor("# Skill\n")["optionalValue"]["agentSkillsDefinition"][
            "optionalValue"
        ]
        self.assertEqual(skill["dataSchemaVersion"]["optionalValue"], "0.1.0")
        markdown = skill["additionalData"]["optionalValue"]["skillMd"]["optionalValue"]["data"][
            "optionalValue"
        ]
        self.assertEqual(markdown, "# Skill\n")


if __name__ == "__main__":
    unittest.main()
