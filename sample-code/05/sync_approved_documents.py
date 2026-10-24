#!/usr/bin/env python3
"""Publish approved document updates to S3 and sync a Knowledge Base.

Reads approved updates from an Excel workbook, uploads each document
(and its optional metadata sidecar) to S3, then starts an ingestion job.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3

from common import read_approved_updates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish approved updates and sync Knowledge Bases."
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("data/approved_updates.xlsx"),
        help="Excel workbook with the '承認済み更新' sheet.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("KB_SYNC_BUCKET"),
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
    )
    parser.add_argument(
        "--data-source-id",
        default=os.environ.get("DATA_SOURCE_ID"),
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without calling AWS APIs.",
    )
    return parser.parse_args()


def publish_markdown(
    s3_client: boto3.client,
    *,
    bucket: str,
    key: str,
    body: str,
) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="text/markdown",
    )


def publish_metadata_sidecar(
    s3_client: boto3.client,
    *,
    bucket: str,
    key: str,
    metadata_payload: dict,
) -> None:
    s3_client.put_object(
        Bucket=bucket,
        Key=f"{key}.metadata.json",
        Body=json.dumps(metadata_payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def main() -> None:
    args = parse_args()

    if not args.bucket or not args.knowledge_base_id or not args.data_source_id:
        raise SystemExit(
            "Provide --bucket, --knowledge-base-id, and --data-source-id "
            "(flags or environment variables)."
        )

    updates = read_approved_updates(args.workbook)
    s3_client = boto3.client("s3", region_name=args.region)
    bedrock_agent_client = boto3.client("bedrock-agent", region_name=args.region)

    for update in updates:
        key = update["key"]
        body = update["body"]

        if args.dry_run:
            print(f"[dry-run] Would upload s3://{args.bucket}/{key}")
            if update.get("metadata_payload"):
                print(f"[dry-run] Would upload s3://{args.bucket}/{key}.metadata.json")
            continue

        publish_markdown(s3_client, bucket=args.bucket, key=key, body=body)

        if update.get("metadata_payload"):
            publish_metadata_sidecar(
                s3_client,
                bucket=args.bucket,
                key=key,
                metadata_payload=update["metadata_payload"],
            )

    if args.dry_run:
        return

    response = bedrock_agent_client.start_ingestion_job(
        knowledgeBaseId=args.knowledge_base_id,
        dataSourceId=args.data_source_id,
        description=f"Approved document updates ({len(updates)} files)",
    )
    ingestion_job = response.get("ingestionJob", {})
    print(
        f"Started ingestion job {ingestion_job.get('ingestionJobId', '<unknown>')} "
        f"for {len(updates)} files"
    )


if __name__ == "__main__":
    main()
