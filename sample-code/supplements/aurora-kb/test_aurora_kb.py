import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from aurora_kb import Run, generation_settings, retrieval_metrics, source_path
from infrastructure import template


def result(path):
    return {"location": {"s3Location": {"uri": "s3://bucket/documents/" + path}}}


class MetricsTests(unittest.TestCase):
    def test_duplicate_chunks_do_not_inflate_document_recall(self):
        got = retrieval_metrics(
            [result("policy/a.md"), result("policy/a.md"), result("policy/b.md")],
            ["policy/a", "policy/b", "policy/c"],
        )
        self.assertEqual(got, {"hit": 1, "recall": 2 / 3, "reciprocal_rank": 1})

    def test_rank_is_chunk_rank_and_no_substring_false_positive(self):
        self.assertEqual(
            retrieval_metrics(
                [result("policy/aa.md"), result("policy/a.md")], ["policy/a"]
            )["reciprocal_rank"],
            0.5,
        )

    def test_empty_results(self):
        self.assertEqual(
            retrieval_metrics([], ["policy/a"]),
            {"hit": 0, "recall": 0, "reciprocal_rank": 0},
        )

    def test_encoded_source(self):
        self.assertEqual(
            source_path(result("policy/%E7%9B%A3%E6%9F%BB.md")), "policy/監査.md"
        )


class InfrastructureTests(unittest.TestCase):
    def test_disposable_database_is_private_and_bounded(self):
        r = template()["Resources"]
        self.assertFalse(r["Writer"]["Properties"]["PubliclyAccessible"])
        self.assertNotIn("SecurityGroupIngress", r["SecurityGroup"]["Properties"])
        self.assertEqual(r["Cluster"]["DeletionPolicy"], "Delete")
        self.assertEqual(
            r["Cluster"]["Properties"]["ServerlessV2ScalingConfiguration"],
            {"MinCapacity": 0, "MaxCapacity": 2, "SecondsUntilAutoPause": 3600},
        )
        self.assertNotIn("AWS::EC2::NatGateway", [v["Type"] for v in r.values()])

    def test_kb_role_cannot_read_master_secret(self):
        statements = template()["Resources"]["KbRole"]["Properties"]["Policies"][0][
            "PolicyDocument"
        ]["Statement"]
        self.assertEqual(
            next(
                s for s in statements if "secretsmanager:GetSecretValue" in s["Action"]
            )["Resource"],
            {"Ref": "KbSecret"},
        )


class CleanupTests(unittest.TestCase):
    def test_refuses_kb_from_a_different_stack(self):
        run = Run.__new__(Run)
        run.args = SimpleNamespace(yes=True)
        run.state = {"kb_id": "ABC123", "KbRoleArn": "expected-role"}
        agent = Mock()
        agent.exceptions.ResourceNotFoundException = type("NotFound", (Exception,), {})
        agent.get_knowledge_base.return_value = {
            "knowledgeBase": {"roleArn": "other-role"}
        }
        run.client = lambda _: agent
        with self.assertRaisesRegex(ValueError, "一致しません"):
            run.cleanup()
        agent.delete_knowledge_base.assert_not_called()


class GenerationSettingsTest(unittest.TestCase):
    """Haiku 5.5には温度を送らず2つのプロンプトを渡し、Haiku 4.5は従来どおりにする。"""

    def test_haiku_5_5_passes_prompts_without_temperature(self):
        for arn in (
            "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-haiku-5-5",
            "arn:aws:bedrock:ap-northeast-1:123456789012:inference-profile/jp.anthropic.claude-haiku-5-5",
            "us.anthropic.claude-haiku-5-5",
        ):
            with self.subTest(arn=arn):
                settings = generation_settings(arn)
                generation = settings["generationConfiguration"]
                orchestration = settings["orchestrationConfiguration"]
                for placeholder in ("$query$", "$search_results$", "$output_format_instructions$"):
                    self.assertIn(placeholder, generation["promptTemplate"]["textPromptTemplate"])
                for placeholder in ("$conversation_history$", "$query$", "$output_format_instructions$"):
                    self.assertIn(placeholder, orchestration["promptTemplate"]["textPromptTemplate"])
                self.assertNotIn("temperature", generation["inferenceConfig"]["textInferenceConfig"])

    def test_haiku_4_5_keeps_temperature(self):
        arn = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0"
        self.assertEqual(
            generation_settings(arn),
            {"generationConfiguration": {"inferenceConfig": {"textInferenceConfig": {"maxTokens": 600, "temperature": 0}}}},
        )


if __name__ == "__main__":
    unittest.main()
