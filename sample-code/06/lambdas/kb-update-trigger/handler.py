"""S3 → EventBridge → Lambda → AgentCore Runtime のトリガー。

連続するオブジェクト作成イベント（``Object Created``）は、Lambda の予約済み同時実行数1で直列処理する。
S3 バケットのバージョニングを前提とし、文書本体のイベントを起点に、
文書と直前に配置したメタデータの Version ID を品質ゲートへ渡す。
ロールバック時はそのバージョンだけを削除するため、
同名キーの上書きでも更新前の内容へ戻せる。

環境変数:

- ``KB_UPDATE_RUNTIME_ARN``: 起動先の AgentCore Runtime ARN
- ``KB_SYNC_BUCKET``: 想定するデータソースバケット
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


_RUNTIME_CLIENT_CONFIG = Config(
    connect_timeout=10,
    read_timeout=840,
    retries={"total_max_attempts": 1, "mode": "standard"},
)


def _new_session_id() -> str:
    """``runtimeSessionId`` は 33 文字以上が必須なので 64 文字で生成する。"""
    return (uuid.uuid4().hex + uuid.uuid4().hex)[:64]


def _collect_changes(
    event: dict[str, Any], expected_bucket: str | None
) -> tuple[str | None, list[str], dict[str, list[str]], int]:
    """文書キーと実物キーの Version ID を取り出す。"""
    bucket_name: str | None = None
    document_keys: list[str] = []
    object_versions: dict[str, list[str]] = {}
    accepted = 0

    for item in [event]:
        detail = item.get("detail", {})
        bucket = detail.get("bucket", {}).get("name")
        obj = detail.get("object", {})
        key = obj.get("key")
        version_id = obj.get("version-id")
        if not (bucket and key):
            continue
        if expected_bucket and bucket != expected_bucket:
            continue
        # メタデータ単独では同期・評価を始めない。
        # メタデータを先に配置し、文書本体のイベントで両方を取り込む。
        if key.endswith(".metadata.json"):
            continue
        if not version_id:
            raise ValueError(
                f"S3 Object Created event for {key!r} has no version-id; "
                "enable bucket versioning before running the quality gate"
            )
        bucket_name = bucket
        accepted += 1

        versions = object_versions.setdefault(key, [])
        if version_id not in versions:
            versions.append(version_id)

        if key not in document_keys:
            document_keys.append(key)

    return bucket_name, document_keys, object_versions, accepted


def _include_metadata_versions(
    s3: Any,
    bucket: str,
    document_keys: list[str],
    object_versions: dict[str, list[str]],
) -> None:
    """文書本体より先に配置したメタデータの現行Version IDを追加する。"""
    for document_key in document_keys:
        metadata_key = f"{document_key}.metadata.json"
        try:
            response = s3.head_object(Bucket=bucket, Key=metadata_key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                continue
            raise
        version_id = response.get("VersionId")
        if not version_id:
            raise ValueError(
                f"S3 object {metadata_key!r} has no Version ID; "
                "enable bucket versioning before running the quality gate"
            )
        object_versions.setdefault(metadata_key, []).append(version_id)


def _read_runtime_response(response: dict[str, Any]) -> dict[str, Any]:
    chunks: list[bytes] = []
    body_stream = response.get("response")
    if body_stream is not None:
        if hasattr(body_stream, "iter_chunks"):
            chunks.extend(body_stream.iter_chunks())
        else:
            chunks.append(body_stream.read())
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:500]}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    expected_bucket = os.environ.get("KB_SYNC_BUCKET")
    bucket, changed_document_keys, object_versions, accepted = _collect_changes(
        event, expected_bucket
    )
    if not accepted:
        return {"skipped": True, "reason": "no matching S3 object events"}
    if bucket is None:
        raise ValueError("S3 bucket could not be resolved from the event")

    _include_metadata_versions(
        boto3.client("s3"),
        bucket,
        changed_document_keys,
        object_versions,
    )

    runtime_arn = os.environ["KB_UPDATE_RUNTIME_ARN"]
    session_id = _new_session_id()
    payload = json.dumps(
        {
            "action": "process",
            "changed_document_keys": changed_document_keys,
            "object_versions": object_versions,
            "bucket": bucket,
            "session_id": session_id,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    # Runtime は同期・評価・原因分析まで実行するため、処理時間が SDK の
    # 既定 read timeout（60秒）を超える。Lambda の15分タイムアウトより
    # 少し短い時間まで待ち、EventBridge に同じイベントを再送させない。
    runtime = boto3.client(
        "bedrock-agentcore",
        config=_RUNTIME_CLIENT_CONFIG,
    )
    response = runtime.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        payload=payload,
    )
    return {
        "sessionId": session_id,
        "eventCount": accepted,
        "body": _read_runtime_response(response),
    }
