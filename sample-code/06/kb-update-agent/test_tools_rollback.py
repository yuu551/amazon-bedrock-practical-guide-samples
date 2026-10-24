"""S3 Version ID 単位のロールバックを検証する。"""
from __future__ import annotations

import json
import os
import uuid
import unittest
from unittest.mock import Mock, patch

import boto3

import tools


class RollbackUnitTest(unittest.TestCase):
    def test_deletes_only_explicit_versions_then_starts_ingestion(self) -> None:
        s3 = Mock()
        agent = Mock()
        agent.start_ingestion_job.return_value = {
            "ingestionJob": {"ingestionJobId": "job-1", "status": "STARTING"}
        }

        def client(service: str, **kwargs):
            return {"s3": s3, "bedrock-agent": agent}[service]

        with patch.object(tools.boto3, "client", side_effect=client):
            result = tools.rollback_documents_impl(
                bucket="docs",
                keys_json=json.dumps(["policies/a.md"]),
                version_ids_json=json.dumps(
                    {
                        "policies/a.md": ["doc-v2", "doc-v3"],
                        "policies/a.md.metadata.json": ["meta-v2"],
                    }
                ),
                knowledge_base_id="kb",
                data_source_id="ds",
            )

        self.assertEqual(result["restored_document_count"], 1)
        self.assertEqual(result["deleted_version_count"], 3)
        self.assertEqual(
            [call.kwargs for call in s3.delete_object.call_args_list],
            [
                {"Bucket": "docs", "Key": "policies/a.md", "VersionId": "doc-v2"},
                {"Bucket": "docs", "Key": "policies/a.md", "VersionId": "doc-v3"},
                {
                    "Bucket": "docs",
                    "Key": "policies/a.md.metadata.json",
                    "VersionId": "meta-v2",
                },
            ],
        )
        agent.start_ingestion_job.assert_called_once()

    def test_missing_version_id_fails_without_deleting_anything(self) -> None:
        s3 = Mock()
        with self.assertRaisesRegex(ValueError, "Version ID"):
            tools._delete_created_versions(
                s3,
                "docs",
                ["policies/a.md"],
                {},
            )
        s3.delete_object.assert_not_called()


@unittest.skipUnless(
    os.environ.get("RUN_AWS_S3_ROLLBACK_TEST") == "1",
    "set RUN_AWS_S3_ROLLBACK_TEST=1 to exercise a real versioned S3 bucket",
)
class RollbackAwsTest(unittest.TestCase):
    def test_overwritten_document_and_metadata_return_byte_for_byte(self) -> None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        s3 = boto3.client("s3", region_name=region)
        bucket = f"kb-rollback-test-{uuid.uuid4().hex}"
        key = "policies/a.md"
        metadata_key = f"{key}.metadata.json"
        old_document = b"old document\n"
        new_document = b"new document\n"
        old_metadata = b'{"metadataAttributes":{"state":"old"}}\n'
        new_metadata = b'{"metadataAttributes":{"state":"new"}}\n'

        try:
            create_args = {"Bucket": bucket}
            if region != "us-east-1":
                create_args["CreateBucketConfiguration"] = {
                    "LocationConstraint": region
                }
            s3.create_bucket(**create_args)
            s3.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
            s3.put_object(Bucket=bucket, Key=key, Body=old_document)
            s3.put_object(Bucket=bucket, Key=metadata_key, Body=old_metadata)
            new_doc_version = s3.put_object(
                Bucket=bucket, Key=key, Body=new_document
            )["VersionId"]
            new_meta_version = s3.put_object(
                Bucket=bucket, Key=metadata_key, Body=new_metadata
            )["VersionId"]

            tools._delete_created_versions(
                s3,
                bucket,
                [key],
                {key: [new_doc_version], metadata_key: [new_meta_version]},
            )

            restored_document = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            restored_metadata = s3.get_object(Bucket=bucket, Key=metadata_key)[
                "Body"
            ].read()
            self.assertEqual(restored_document, old_document)
            self.assertEqual(restored_metadata, old_metadata)
        finally:
            try:
                listed = s3.list_object_versions(Bucket=bucket)
                objects = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]}
                    for group in ("Versions", "DeleteMarkers")
                    for item in listed.get(group, [])
                ]
                if objects:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                s3.delete_bucket(Bucket=bucket)
            except s3.exceptions.NoSuchBucket:
                pass


if __name__ == "__main__":
    unittest.main()
