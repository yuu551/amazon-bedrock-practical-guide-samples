"""Managed KB横断検索サンプルの新規アカウント向け設定テスト。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, mock_open, patch

import boto3
from botocore.validate import validate_parameters

import managed_kb_cross_search as target


class ManagedKbCreateTest(unittest.TestCase):
    def test_existing_kb_cleanup_requires_explicit_force(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(target.boto3, "Session") as session,
            self.assertRaisesRegex(SystemExit, "force-cleanup"),
        ):
            target.main(
                [
                    "--cleanup",
                    "--s3-kb-id",
                    "S3EXISTING1",
                    "--confluence-kb-id",
                    "CONFEXIST1",
                ]
            )

        session.assert_not_called()

    def test_cleanup_deletes_data_sources_from_all_pages(self) -> None:
        agent = Mock()
        agent.list_data_sources.side_effect = [
            {
                "dataSourceSummaries": [{"dataSourceId": "DS-PAGE-1"}],
                "nextToken": "page-2",
            },
            {"dataSourceSummaries": [{"dataSourceId": "DS-PAGE-2"}]},
        ]

        with (
            patch.object(target, "_wait_data_source_deleted"),
            patch.object(target, "_wait_kb_deleted"),
        ):
            target.cleanup(agent, "S3KB001", None)

        self.assertEqual(
            [call.kwargs for call in agent.list_data_sources.call_args_list],
            [
                {"knowledgeBaseId": "S3KB001"},
                {"knowledgeBaseId": "S3KB001", "nextToken": "page-2"},
            ],
        )
        self.assertEqual(
            [
                call.kwargs["dataSourceId"]
                for call in agent.delete_data_source.call_args_list
            ],
            ["DS-PAGE-1", "DS-PAGE-2"],
        )
        agent.delete_knowledge_base.assert_called_once_with(
            knowledgeBaseId="S3KB001"
        )

    def test_missing_create_settings_fail_before_aws_client_creation(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(target.boto3, "Session") as session,
        ):
            with self.assertRaisesRegex(SystemExit, "MANAGED_KB_ROLE_ARN"):
                target.main(["--create"])
        session.assert_not_called()

    def test_create_uses_explicit_values_without_reading_an_existing_kb(self) -> None:
        agent = Mock()
        agent.create_knowledge_base.side_effect = [
            {"knowledgeBase": {"knowledgeBaseId": "S3NEWKB001"}},
            {"knowledgeBase": {"knowledgeBaseId": "CONFNEW001"}},
        ]
        agent.create_data_source.side_effect = [
            {"dataSource": {"dataSourceId": "S3DS000001"}},
            {"dataSource": {"dataSourceId": "CONFDS0001"}},
        ]
        agent.start_ingestion_job.side_effect = [
            {"ingestionJob": {"ingestionJobId": "S3JOB00001"}},
            {"ingestionJob": {"ingestionJobId": "CONFJOB001"}},
        ]

        with (
            patch.object(target, "wait_kb_active"),
            patch.object(target, "wait_ingestion"),
        ):
            ids = target.create_kbs(
                agent,
                account_id="111122223333",
                role_arn="arn:aws:iam::111122223333:role/ManagedKbRole",
                s3_bucket="reader-owned-data",
                confluence_secret_arn=(
                    "arn:aws:secretsmanager:us-east-1:111122223333:secret:reader"
                ),
                confluence_host="https://reader.atlassian.net",
                confluence_space="OPS",
                name_suffix="test-run",
            )

        self.assertEqual(ids, ("S3NEWKB001", "CONFNEW001"))
        agent.get_knowledge_base.assert_not_called()
        self.assertEqual(agent.create_knowledge_base.call_count, 2)
        self.assertEqual(
            [call.kwargs["name"] for call in agent.create_knowledge_base.call_args_list],
            [
                "sampleconnect-s3-only-kb-test-run",
                "sampleconnect-confluence-only-kb-test-run",
            ],
        )
        for call in agent.create_knowledge_base.call_args_list:
            self.assertEqual(
                call.kwargs["roleArn"],
                "arn:aws:iam::111122223333:role/ManagedKbRole",
            )

        s3_managed_config = agent.create_data_source.call_args_list[0].kwargs[
            "dataSourceConfiguration"
        ]["managedKnowledgeBaseConnectorConfiguration"]
        self.assertEqual(
            s3_managed_config["deletionProtectionConfiguration"],
            {"deletionProtectionStatus": "DISABLED"},
        )
        s3_config = s3_managed_config["connectorParameters"]
        self.assertNotIn("deletionProtectionConfiguration", s3_config)
        self.assertEqual(
            s3_config["connectionConfiguration"],
            {
                "bucketName": "reader-owned-data",
                "bucketOwnerAccountId": "111122223333",
            },
        )
        confluence_config = agent.create_data_source.call_args_list[1].kwargs[
            "dataSourceConfiguration"
        ]["managedKnowledgeBaseConnectorConfiguration"]["connectorParameters"]
        self.assertEqual(
            confluence_config["connectionConfiguration"]["secretArn"],
            "arn:aws:secretsmanager:us-east-1:111122223333:secret:reader",
        )
        self.assertEqual(
            confluence_config["connectionConfiguration"]["hostUrl"],
            "https://reader.atlassian.net",
        )
        self.assertEqual(
            confluence_config["filterConfiguration"]["inclusionSpaceKeys"],
            ["OPS"],
        )

        # モックの呼び出しが現行boto3のAPI入力形式に合うことも検証する。
        sdk_client = boto3.client(
            "bedrock-agent",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        service_model = sdk_client.meta.service_model
        for call in agent.create_knowledge_base.call_args_list:
            validate_parameters(
                call.kwargs,
                service_model.operation_model("CreateKnowledgeBase").input_shape,
            )
        for call in agent.create_data_source.call_args_list:
            validate_parameters(
                call.kwargs,
                service_model.operation_model("CreateDataSource").input_shape,
            )

    def test_create_cleanup_removes_partial_resources_after_sync_failure(self) -> None:
        agent = Mock()
        agent.create_knowledge_base.side_effect = [
            {"knowledgeBase": {"knowledgeBaseId": "S3NEWKB001"}},
            {"knowledgeBase": {"knowledgeBaseId": "CONFNEW001"}},
        ]
        agent.create_data_source.side_effect = [
            {"dataSource": {"dataSourceId": "S3DS000001"}},
            {"dataSource": {"dataSourceId": "CONFDS0001"}},
        ]
        agent.start_ingestion_job.side_effect = [
            {"ingestionJob": {"ingestionJobId": "S3JOB00001"}},
            {"ingestionJob": {"ingestionJobId": "CONFJOB001"}},
        ]

        with (
            patch.object(target, "wait_kb_active"),
            patch.object(
                target,
                "wait_ingestion",
                side_effect=[{}, RuntimeError("Confluence: FAILED")],
            ),
            patch.object(target, "cleanup") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "Confluence"):
                target.create_kbs(
                    agent,
                    account_id="111122223333",
                    role_arn="arn:aws:iam::111122223333:role/ManagedKbRole",
                    s3_bucket="reader-owned-data",
                    confluence_secret_arn=(
                        "arn:aws:secretsmanager:us-east-1:111122223333:secret:reader"
                    ),
                    confluence_host="https://reader.atlassian.net",
                    confluence_space="OPS",
                    cleanup_on_failure=True,
                )

        cleanup.assert_called_once_with(agent, "S3NEWKB001", "CONFNEW001")

    def test_create_cleanup_runs_cross_search_before_removing_resources(self) -> None:
        session = Mock()
        agent = Mock()
        runtime = Mock()
        sts = Mock()
        sts.get_caller_identity.return_value = {"Account": "111122223333"}
        session.client.side_effect = lambda name: {
            "bedrock-agent": agent,
            "bedrock-agent-runtime": runtime,
            "sts": sts,
        }[name]

        events = []
        with (
            patch.object(target.boto3, "Session", return_value=session),
            patch.object(
                target,
                "create_kbs",
                side_effect=lambda *args, **kwargs: (
                    events.append("create") or ("S3NEWKB001", "CONFNEW001")
                ),
            ) as create,
            patch.object(
                target,
                "run_cross_search",
                side_effect=lambda *args: events.append("search") or [],
            ),
            patch.object(
                target,
                "cleanup",
                side_effect=lambda *args: events.append("cleanup"),
            ),
            patch.object(target.os, "makedirs"),
            patch("builtins.open", mock_open()),
        ):
            target.main(
                [
                    "--create",
                    "--cleanup",
                    "--role-arn",
                    "arn:aws:iam::111122223333:role/ManagedKbRole",
                    "--s3-bucket",
                    "reader-owned-data",
                    "--confluence-secret-arn",
                    "arn:aws:secretsmanager:us-east-1:111122223333:secret:reader",
                    "--confluence-host",
                    "https://reader.atlassian.net",
                    "--confluence-space",
                    "OPS",
                ]
            )

        self.assertEqual(events, ["create", "search", "cleanup"])
        self.assertTrue(create.call_args.kwargs["cleanup_on_failure"])


if __name__ == "__main__":
    unittest.main()
