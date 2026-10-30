"""取り込みジョブの競合回避を検証する。"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

import tools


def _job(job_id: str, status: str) -> dict:
    return {
        "ingestionJobId": job_id,
        "status": status,
        "statistics": {
            "numberOfDocumentsScanned": 2,
            "numberOfNewDocumentsIndexed": 1,
            "numberOfModifiedDocumentsIndexed": 1,
            "numberOfDocumentsDeleted": 0,
            "numberOfDocumentsFailed": 0,
        },
    }


class SyncSerializationTest(unittest.TestCase):
    def test_waits_for_conflicting_job_before_starting_one_new_sync(self) -> None:
        client = Mock()
        conflict = ClientError(
            {
                "Error": {
                    "Code": "ConflictException",
                    "Message": "ongoing ingestion job",
                }
            },
            "StartIngestionJob",
        )
        client.start_ingestion_job.side_effect = [
            conflict,
            {"ingestionJob": _job("new-job", "STARTING")},
        ]
        client.list_ingestion_jobs.return_value = {
            "ingestionJobSummaries": [_job("existing-job", "IN_PROGRESS")]
        }
        client.get_ingestion_job.side_effect = [
            {"ingestionJob": _job("existing-job", "COMPLETE")},
            {"ingestionJob": _job("new-job", "COMPLETE")},
        ]

        with patch.object(tools.boto3, "client", return_value=client):
            result = tools.start_and_wait_for_sync(
                "kb", "ds", poll_interval_sec=0, timeout_sec=10
            )

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["ingestion_job_id"], "new-job")
        self.assertEqual(
            result["waited_for_ingestion_job_ids"], ["existing-job"]
        )
        self.assertEqual(client.start_ingestion_job.call_count, 2)
        client.list_ingestion_jobs.assert_called_once()

    def test_non_conflict_error_is_not_retried(self) -> None:
        client = Mock()
        denied = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "StartIngestionJob",
        )
        client.start_ingestion_job.side_effect = denied

        with patch.object(tools.boto3, "client", return_value=client):
            with self.assertRaises(ClientError):
                tools.start_and_wait_for_sync(
                    "kb", "ds", poll_interval_sec=0, timeout_sec=10
                )
        self.assertEqual(client.start_ingestion_job.call_count, 1)
        client.list_ingestion_jobs.assert_not_called()


HAIKU_5_5_ARN = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-haiku-5-5"
HAIKU_4_5_ARN = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0"


class ModelSettingsTest(unittest.TestCase):
    """Haiku 5.5には温度を送らずKBのプロンプトを渡し、Haiku 4.5は従来どおりにする。"""

    def _kb_config(self, model_arn: str) -> dict:
        client = Mock()
        client.retrieve_and_generate.return_value = {"output": {"text": "a"}}
        with patch.object(tools.boto3, "client", return_value=client):
            tools._retrieve_and_generate("KB", model_arn, "q")
        request = client.retrieve_and_generate.call_args.kwargs
        return request["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]

    def _judge_request(self, model_id: str, content: list) -> dict:
        runtime = Mock()
        runtime.converse.return_value = {"output": {"message": {"content": content}}}
        result = tools._judge_single(
            runtime, judge_model_id=model_id, question="q", expected_points=[], answer="a", references=[]
        )
        self.assertEqual(result, {"ok": 1})
        return runtime.converse.call_args.kwargs["inferenceConfig"]

    def test_haiku_5_5_passes_prompts_and_omits_temperature(self) -> None:
        config = self._kb_config(HAIKU_5_5_ARN)
        generation = config["generationConfiguration"]
        orchestration = config["orchestrationConfiguration"]
        for placeholder in ("$query$", "$search_results$", "$output_format_instructions$"):
            self.assertIn(placeholder, generation["promptTemplate"]["textPromptTemplate"])
        for placeholder in ("$conversation_history$", "$query$", "$output_format_instructions$"):
            self.assertIn(placeholder, orchestration["promptTemplate"]["textPromptTemplate"])
        self.assertNotIn("temperature", generation["inferenceConfig"]["textInferenceConfig"])
        content = [{"reasoningContent": {"reasoningText": {"text": "..."}}}, {"text": '{"ok": 1}'}]
        for model_id in ("us.anthropic.claude-haiku-5-5", "jp.anthropic.claude-haiku-5-5"):
            with self.subTest(model_id=model_id):
                self.assertEqual(self._judge_request(model_id, content), {"maxTokens": 4096})

    def test_haiku_4_5_unchanged(self) -> None:
        self.assertEqual(
            self._kb_config(HAIKU_4_5_ARN),
            {"knowledgeBaseId": "KB", "modelArn": HAIKU_4_5_ARN},
        )
        self.assertEqual(
            self._judge_request("us.anthropic.claude-haiku-4-5-20251001-v1:0", [{"text": '{"ok": 1}'}]),
            {"temperature": 0, "maxTokens": 800},
        )


if __name__ == "__main__":
    unittest.main()
