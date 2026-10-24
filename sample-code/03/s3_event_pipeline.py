from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import boto3

from common import generate_metadata_payload, load_json


def _guess_format_from_key(key: str) -> str | None:
    return {
        ".csv": "csv",
        ".doc": "doc",
        ".docx": "docx",
        ".html": "html",
        ".md": "md",
        ".pdf": "pdf",
        ".txt": "txt",
        ".xls": "xls",
        ".xlsx": "xlsx",
    }.get(Path(key).suffix.lower())


def _load_schema(
    s3_client: Any,
    bucket: str | None,
    key: str | None,
) -> dict[str, Any] | None:
    if bucket and key:
        payload = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(payload)

    inline = os.environ.get("METADATA_SCHEMA_JSON")
    if inline:
        return json.loads(inline)
    return None


def _generate_and_upload_sidecar(
    *,
    s3_client: Any,
    runtime_client: Any,
    bucket: str,
    key: str,
    body: bytes,
    schema: dict[str, Any],
    metadata_model_id: str,
) -> None:
    document_format = _guess_format_from_key(key)
    if document_format is None:
        return

    metadata_payload = generate_metadata_payload(
        runtime_client=runtime_client,
        model_id=metadata_model_id,
        schema=schema,
        document_name=Path(key).name,
        document_format=document_format,
        document_bytes=body,
    )

    metadata_key = f"{key}.metadata.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=metadata_key,
        Body=json.dumps(metadata_payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    knowledge_base_id = os.environ["KNOWLEDGE_BASE_ID"]
    data_source_id = os.environ["DATA_SOURCE_ID"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    metadata_model_id = os.environ.get("METADATA_MODEL_ID")
    schema_bucket = os.environ.get("METADATA_SCHEMA_BUCKET")
    schema_key = os.environ.get("METADATA_SCHEMA_KEY")

    s3_client = boto3.client("s3", region_name=region)
    bedrock_agent_client = boto3.client("bedrock-agent", region_name=region)
    runtime_client = boto3.client("bedrock-runtime", region_name=region)

    schema = _load_schema(s3_client, schema_bucket, schema_key)
    processed: list[str] = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        if key.endswith(".metadata.json"):
            continue

        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()

        if schema and metadata_model_id:
            _generate_and_upload_sidecar(
                s3_client=s3_client,
                runtime_client=runtime_client,
                bucket=bucket,
                key=key,
                body=body,
                schema=schema,
                metadata_model_id=metadata_model_id,
            )

        # NOTE: インジェスションジョブはデータソース全体を処理するため、
        # 複数ファイルが同時にアップロードされるとジョブが乱立します。
        # 本番環境ではSQSでイベントをバッファリングし、一定間隔でまとめて
        # 1回のインジェスションジョブを起動する設計を推奨します。
        bedrock_agent_client.start_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            description=f"Ingest after S3 update: s3://{bucket}/{key}",
        )
        processed.append(f"s3://{bucket}/{key}")

    return {
        "processed": processed,
        "count": len(processed),
    }
