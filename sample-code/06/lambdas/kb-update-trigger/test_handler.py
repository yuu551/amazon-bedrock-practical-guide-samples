"""EventBridge イベントと S3 Version ID 引き渡しのテスト。"""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

import handler


def _event(key: str, version_id: str | None, bucket: str = "docs") -> dict:
    obj = {"key": key}
    if version_id is not None:
        obj["version-id"] = version_id
    return {"detail": {"bucket": {"name": bucket}, "object": obj}}


class TriggerHandlerTest(unittest.TestCase):
    def test_event_invokes_runtime_with_object_version(self) -> None:
        runtime = Mock()
        s3 = Mock()
        s3.head_object.return_value = {"VersionId": "meta-v2"}
        runtime.invoke_agent_runtime.return_value = {
            "response": io.BytesIO(b'{"decision":"approved"}')
        }
        with (
            patch.dict(
                os.environ,
                {
                    "KB_SYNC_BUCKET": "docs",
                    "KB_UPDATE_RUNTIME_ARN": "arn:runtime",
                },
                clear=False,
            ),
            patch.object(
                handler.boto3,
                "client",
                side_effect=lambda service, **_: {
                    "s3": s3,
                    "bedrock-agentcore": runtime,
                }[service],
            ) as client_factory,
        ):
            result = handler.handler(_event("policies/a.md", "doc-v2"), None)

        self.assertEqual(result["eventCount"], 1)
        runtime.invoke_agent_runtime.assert_called_once()
        payload = json.loads(
            runtime.invoke_agent_runtime.call_args.kwargs["payload"].decode("utf-8")
        )
        self.assertEqual(payload["changed_document_keys"], ["policies/a.md"])
        self.assertEqual(
            payload["object_versions"],
            {
                "policies/a.md": ["doc-v2"],
                "policies/a.md.metadata.json": ["meta-v2"],
            },
        )
        runtime_client_call = next(
            call
            for call in client_factory.call_args_list
            if call.args == ("bedrock-agentcore",)
        )
        self.assertEqual(runtime_client_call.kwargs["config"].read_timeout, 840)
        self.assertEqual(
            runtime_client_call.kwargs["config"].retries["total_max_attempts"],
            1,
        )

    def test_metadata_event_is_skipped_without_invoking_runtime(self) -> None:
        with patch.object(handler.boto3, "client") as client_factory:
            result = handler.handler(
                _event("policies/a.md.metadata.json", "meta-v2"), None
            )

        self.assertTrue(result["skipped"])
        client_factory.assert_not_called()

    def test_document_without_metadata_is_still_processed(self) -> None:
        runtime = Mock()
        s3 = Mock()
        s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        runtime.invoke_agent_runtime.return_value = {
            "response": io.BytesIO(b'{"decision":"approved"}')
        }
        with (
            patch.dict(
                os.environ,
                {
                    "KB_SYNC_BUCKET": "docs",
                    "KB_UPDATE_RUNTIME_ARN": "arn:runtime",
                },
                clear=False,
            ),
            patch.object(
                handler.boto3,
                "client",
                side_effect=lambda service, **_: {
                    "s3": s3,
                    "bedrock-agentcore": runtime,
                }[service],
            ),
        ):
            handler.handler(_event("policies/a.md", "doc-v2"), None)

        payload = json.loads(
            runtime.invoke_agent_runtime.call_args.kwargs["payload"].decode("utf-8")
        )
        self.assertEqual(
            payload["object_versions"], {"policies/a.md": ["doc-v2"]}
        )

    def test_missing_version_id_fails_before_runtime_call(self) -> None:
        runtime = Mock()
        with (
            patch.dict(
                os.environ,
                {
                    "KB_SYNC_BUCKET": "docs",
                    "KB_UPDATE_RUNTIME_ARN": "arn:runtime",
                },
                clear=False,
            ),
            patch.object(handler.boto3, "client", return_value=runtime),
        ):
            with self.assertRaisesRegex(ValueError, "enable bucket versioning"):
                handler.handler(_event("policies/a.md", None), None)
        runtime.invoke_agent_runtime.assert_not_called()

    def test_unexpected_bucket_is_skipped(self) -> None:
        with patch.dict(os.environ, {"KB_SYNC_BUCKET": "docs"}, clear=False):
            result = handler.handler(_event("a.md", "v1", bucket="other"), None)
        self.assertTrue(result["skipped"])


if __name__ == "__main__":
    unittest.main()
