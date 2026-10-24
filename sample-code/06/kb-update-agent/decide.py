#!/usr/bin/env python3
"""レビュー依頼判定が出た更新を、運用担当が後から手動で確定する CLI。

レビュー依頼判定時に AgentCore Runtime が ``/mnt/workspace/pending.json`` に
保留状態を残している。本 CLI は同じ ``runtimeSessionId`` で Runtime を再呼び
出し、``payload.action`` に ``approve`` / ``rollback`` を指定する。Runtime 側
がセッションストレージから保留状態を読み戻し、対応する副作用処理
（save_baseline_impl または rollback_documents_impl）を実行する。

使い方:
    export KB_UPDATE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:...:runtime/...

    # 承認
    uv run decide.py approve --session-id <レビュー依頼通知に書かれた33文字以上のID>

    # ロールバック
    uv run decide.py rollback --session-id <同上>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import boto3


def _invoke(runtime_arn: str, session_id: str, action: str, region: str) -> dict[str, Any]:
    client = boto3.client("bedrock-agentcore", region_name=region)
    payload = json.dumps(
        {"action": action, "session_id": session_id}, ensure_ascii=False
    ).encode("utf-8")
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        payload=payload,
    )

    chunks: list[bytes] = []
    body_stream = resp.get("response")
    if body_stream is not None:
        if hasattr(body_stream, "iter_chunks"):
            for chunk in body_stream.iter_chunks():
                chunks.append(chunk)
        else:
            chunks.append(body_stream.read())
    raw = b"".join(chunks).decode("utf-8", errors="replace")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "レビュー依頼判定が出た更新を、運用担当が承認またはロールバック"
            "で確定するための CLI。同じ runtimeSessionId で AgentCore Runtime "
            "を再呼び出し、Runtime 側のセッションストレージに残る pending.json "
            "を消化させる。"
        )
    )
    p.add_argument(
        "decision",
        choices=("approve", "rollback"),
        help="approve=今回スコアをベースラインに保存 / rollback=更新バージョン削除＋再同期",
    )
    p.add_argument(
        "--session-id",
        required=True,
        help="レビュー依頼通知に記載されている runtimeSessionId（33文字以上）",
    )
    p.add_argument(
        "--runtime-arn",
        default=os.environ.get("KB_UPDATE_RUNTIME_ARN"),
        help="AgentCore Runtime の ARN（環境変数 KB_UPDATE_RUNTIME_ARN でも可）",
    )
    p.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS リージョン（デフォルト: AWS_REGION 環境変数または us-east-1）",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.runtime_arn:
        raise SystemExit(
            "Runtime ARN が未指定です。--runtime-arn か KB_UPDATE_RUNTIME_ARN 環境変数を設定してください。"
        )
    if len(args.session_id) < 33:
        raise SystemExit(
            "AgentCore Runtime の runtimeSessionId は 33 文字以上必要です。"
        )

    print(
        f"[{args.decision}] Runtime を再呼び出します ── sessionId={args.session_id}",
        file=sys.stderr,
    )
    result = _invoke(
        runtime_arn=args.runtime_arn,
        session_id=args.session_id,
        action=args.decision,
        region=args.region,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
