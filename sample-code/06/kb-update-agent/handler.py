"""AgentCore Runtime の Knowledge Base 更新品質ゲートのエントリポイント。

S3 → EventBridge → Lambda → AgentCore Runtime の流れで起動される。
``payload.action`` で3つのモードを切り替える。

- ``process``: 同期・評価・ベースライン読み込みを実行し、コードの品質ゲートで
  判定する。Strands Agent は原因仮説と通知文だけを作る。承認・ロールバックなら
  コードが副作用を実行し、レビュー判定なら
  ``/mnt/workspace/pending.json`` に保留状態を書く。``decide.py`` から
  同じ sessionId で再呼び出しすると、AgentCore Runtime のセッション
  ストレージから同じファイルを読み戻せる。
- ``approve``: ``pending.json`` を読んで ``save_baseline_impl`` を実行する。
- ``rollback``: ``pending.json`` を読んで ``rollback_documents_impl`` を実行する。

セッションストレージは Runtime 作成時に ``filesystemConfigurations`` で
``/mnt/workspace`` を mount しておく必要がある（既定では未 mount）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent import build_agent
from quality_gate import evaluate_quality_gate
from tools import (
    load_baseline,
    load_review_revision,
    record_review_revision,
    rollback_documents_impl,
    run_evaluation,
    save_baseline_impl,
    send_notification_impl,
    start_and_wait_for_sync,
)

PENDING_PATH = Path(os.environ.get("PENDING_PATH", "/mnt/workspace/pending.json"))

app = BedrockAgentCoreApp()


def _resolve(payload: dict[str, Any], key: str, env: str, default: Any = None) -> Any:
    """payload 優先、未指定なら環境変数、それでも無ければ default。"""
    if key in payload and payload[key] not in (None, ""):
        return payload[key]
    val = os.environ.get(env)
    return val if val not in (None, "") else default


def _session_id(payload: dict[str, Any], context: Any) -> str:
    """payload か Runtime context からセッションIDを取り出す。"""
    sid = payload.get("session_id")
    if sid:
        return str(sid)
    sid = getattr(context, "session_id", None) or getattr(
        context, "runtime_session_id", None
    )
    if sid:
        return str(sid)
    return "unknown-session"


def _create_analysis(model_id: str, region: str, task: str) -> str:
    """副作用を持たないエージェントで判定理由の説明を作る。"""
    try:
        return str(build_agent(model_id, region)(task))
    except Exception as exc:  # noqa: BLE001
        return f"原因分析の生成に失敗しました。コード側の判定を確認してください: {exc}"


def _notification_subject(decision: str) -> str:
    return {
        "approved": "【自動承認】Knowledge Base 更新を承認しました",
        "rolled_back": "【自動ロールバック】Knowledge Base 更新を差し戻しました",
        "pending_review": "【レビュー依頼】Knowledge Base 更新の確認が必要です",
    }[decision]


def _notification_message(
    *,
    session_id: str,
    decision: str,
    gate: dict[str, Any],
    analysis: str,
    action_result: dict[str, Any] | None,
) -> str:
    lines = [
        f"- セッションID: {session_id}",
        f"- コード側の判定: {decision}",
        f"- 判定理由: {gate['reasons']}",
        f"- 指標平均の低下幅: {gate['average_drops']}",
        f"- 新規0点: {gate['new_zero_scores']}",
    ]
    if action_result is not None:
        lines.append(f"- 実行結果: {action_result}")
    if decision == "pending_review":
        lines.extend(
            [
                "- 承認コマンド: "
                f"uv run decide.py approve --session-id {session_id}",
                "- ロールバックコマンド: "
                f"uv run decide.py rollback --session-id {session_id}",
            ]
        )
    lines.extend(["", "# 原因分析（AI生成）", analysis])
    return "\n".join(lines)


def _process(payload: dict[str, Any], context: Any) -> dict[str, Any]:
    knowledge_base_id = _resolve(payload, "knowledge_base_id", "KNOWLEDGE_BASE_ID")
    data_source_id = _resolve(payload, "data_source_id", "DATA_SOURCE_ID")
    bucket = _resolve(payload, "bucket", "KB_SYNC_BUCKET")
    api_url = _resolve(payload, "api_url", "APP_API_URL")
    judge_model_id = _resolve(payload, "judge_model_id", "JUDGE_MODEL_ID")
    model_id = _resolve(payload, "model_id", "MODEL_ID")
    region = _resolve(payload, "region", "AWS_REGION", "us-east-1")
    eval_cases = _resolve(payload, "eval_cases", "EVAL_CASES_PATH", "eval_cases.json")
    baseline_path = _resolve(
        payload, "baseline_path", "BASELINE_PATH", "baseline.json"
    )
    changed_document_keys = payload.get("changed_document_keys") or []
    object_versions = payload.get("object_versions") or {}
    session_id = _session_id(payload, context)

    review_revision = record_review_revision(baseline_path, session_id)["revision"]
    sync_result = start_and_wait_for_sync(knowledge_base_id, data_source_id)

    if sync_result["status"] != "COMPLETE":
        notify = send_notification_impl(
            subject="【同期失敗】Knowledge Base 更新を中断しました",
            message=(
                f"- セッションID: {session_id}\n"
                "- 自動承認・自動ロールバックは実行していません\n"
                f"# 同期結果\n{json.dumps(sync_result, ensure_ascii=False, indent=2)}\n"
            ),
        )
        return {
            "session_id": session_id,
            "decision": "sync_failed",
            "sync_result": sync_result,
            "notify": notify,
        }

    eval_result = run_evaluation(
        api_url,
        eval_cases,
        judge_model_id,
        knowledge_base_id=knowledge_base_id,
        answer_model_id=model_id,
    )
    baseline = load_baseline(baseline_path)
    gate = evaluate_quality_gate(
        eval_result,
        baseline,
        sync_result=sync_result,
    )
    if gate["decision"] == "rollback" and (
        not changed_document_keys or not object_versions
    ):
        gate = {
            **gate,
            "decision": "pending_review",
            "reasons": [
                *gate["reasons"],
                "S3 Version ID 付きの更新対象がないため自動ロールバックできません",
            ],
        }

    pending_payload = {
        "session_id": session_id,
        "knowledge_base_id": knowledge_base_id,
        "data_source_id": data_source_id,
        "bucket": bucket,
        "changed_document_keys": changed_document_keys,
        "object_versions": object_versions,
        "review_revision": review_revision,
        "baseline_path": baseline_path,
        "sync_result": sync_result,
        "eval_result": eval_result,
        "baseline": baseline,
        "gate": gate,
    }

    task = (
        "コード側の判定は確定済みです。判定を変更せず、差分と原因仮説を整理してください。\n"
        "\n"
        f"- セッションID: {session_id}\n"
        f"- Knowledge Base ID: {knowledge_base_id}\n"
        f"- データソース ID: {data_source_id}\n"
        f"- S3バケット: {bucket}\n"
        f"- 更新対象ファイル: {json.dumps(changed_document_keys, ensure_ascii=False)}\n"
        f"- 更新バージョン: {json.dumps(object_versions, ensure_ascii=False)}\n"
        f"- ベースラインファイル: {baseline_path}\n"
        f"- コード側の判定: {json.dumps(gate, ensure_ascii=False)}\n"
        "\n"
        "# 同期結果\n"
        f"{json.dumps(sync_result, ensure_ascii=False, indent=2)}\n"
        "\n"
        "# 評価結果\n"
        f"{json.dumps(eval_result, ensure_ascii=False, indent=2)}\n"
        "\n"
        "# ベースライン\n"
        f"{json.dumps(baseline, ensure_ascii=False, indent=2)}\n"
    )
    gate_decision = gate["decision"]
    action_result: dict[str, Any] | None = None

    if gate_decision == "approve":
        action_result = save_baseline_impl(
            baseline_path,
            json.dumps(eval_result, ensure_ascii=False),
        )
        decision = "approved"
        PENDING_PATH.unlink(missing_ok=True)
    elif gate_decision == "rollback":
        action_result = rollback_documents_impl(
            bucket=bucket,
            keys_json=json.dumps(changed_document_keys, ensure_ascii=False),
            version_ids_json=json.dumps(object_versions, ensure_ascii=False),
            knowledge_base_id=knowledge_base_id,
            data_source_id=data_source_id,
        )
        decision = "rolled_back"
        PENDING_PATH.unlink(missing_ok=True)
    else:
        decision = "pending_review"
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        PENDING_PATH.write_text(
            json.dumps(pending_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # LLMが停止・失敗しても、コード側の判定と保留状態の保存は完了済み。
    analysis = _create_analysis(model_id, region, task)
    if decision == "pending_review":
        pending_payload["analysis"] = analysis
        PENDING_PATH.write_text(
            json.dumps(pending_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    notify = send_notification_impl(
        subject=_notification_subject(decision),
        message=_notification_message(
            session_id=session_id,
            decision=decision,
            gate=gate,
            analysis=analysis,
            action_result=action_result,
        ),
    )

    return {
        "session_id": session_id,
        "decision": decision,
        "sync_result": sync_result,
        "eval_averages": eval_result.get("averages"),
        "baseline_exists": baseline.get("exists"),
        "gate": gate,
        "action_result": action_result,
        "notify": notify,
        "pending_path": str(PENDING_PATH) if decision == "pending_review" else None,
    }


def _manual_approve(payload: dict[str, Any], context: Any) -> dict[str, Any]:
    session_id = _session_id(payload, context)
    if not PENDING_PATH.exists():
        return {
            "session_id": session_id,
            "error": "no_pending_state",
            "message": (
                f"保留状態ファイルが見つかりません: {PENDING_PATH}\n"
                "同じ sessionId で process アクションを先に実行する必要があります。"
            ),
        }
    pending = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    stale = _stale_pending_response(session_id, pending)
    if stale:
        return stale
    eval_result = pending["eval_result"]
    save_result = save_baseline_impl(
        pending["baseline_path"], json.dumps(eval_result, ensure_ascii=False)
    )
    notify = send_notification_impl(
        subject="【手動承認】Knowledge Base 更新を承認しました",
        message=(
            "運用担当者の判断で、レビュー依頼となっていた更新を承認しました。\n"
            f"- セッションID: {session_id}\n"
            f"- Knowledge Base ID: {pending['knowledge_base_id']}\n"
            f"- 更新対象ファイル: {pending['changed_document_keys']}\n"
            f"- ベースライン保存先: {save_result['path']}\n"
            f"- 評価平均: {eval_result.get('averages')}\n"
        ),
    )
    PENDING_PATH.unlink(missing_ok=True)
    return {
        "session_id": session_id,
        "decision": "manual_approved",
        "save_result": save_result,
        "notify": notify,
    }


def _manual_rollback(payload: dict[str, Any], context: Any) -> dict[str, Any]:
    session_id = _session_id(payload, context)
    if not PENDING_PATH.exists():
        return {
            "session_id": session_id,
            "error": "no_pending_state",
            "message": (
                f"保留状態ファイルが見つかりません: {PENDING_PATH}\n"
                "同じ sessionId で process アクションを先に実行する必要があります。"
            ),
        }
    pending = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    stale = _stale_pending_response(session_id, pending)
    if stale:
        return stale
    rollback = rollback_documents_impl(
        bucket=pending["bucket"],
        keys_json=json.dumps(pending["changed_document_keys"], ensure_ascii=False),
        version_ids_json=json.dumps(pending["object_versions"], ensure_ascii=False),
        knowledge_base_id=pending["knowledge_base_id"],
        data_source_id=pending["data_source_id"],
    )
    notify = send_notification_impl(
        subject="【手動ロールバック】Knowledge Base 更新を差し戻しました",
        message=(
            "運用担当者の判断で、レビュー依頼となっていた更新をロールバックしました。\n"
            f"- セッションID: {session_id}\n"
            f"- Knowledge Base ID: {pending['knowledge_base_id']}\n"
            f"- 復元キー: {pending['changed_document_keys']}\n"
            f"- 再同期ジョブ ID: {rollback['rollback_job_id']}\n"
        ),
    )
    PENDING_PATH.unlink(missing_ok=True)
    return {
        "session_id": session_id,
        "decision": "manual_rolled_back",
        "rollback_result": rollback,
        "notify": notify,
    }


def _stale_pending_response(
    session_id: str, pending: dict[str, Any]
) -> dict[str, Any] | None:
    """後続更新の開始後に古い判断を実行しない。"""
    expected = pending.get("review_revision")
    current = load_review_revision(pending["baseline_path"])
    if expected and current == expected:
        return None
    return {
        "session_id": session_id,
        "error": "stale_pending_state",
        "message": (
            "このレビュー依頼後に別の更新処理が始まったため、"
            "承認・ロールバックを実行しません。"
        ),
        "pending_revision": expected,
        "current_revision": current,
    }


@app.entrypoint
def handler(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"error": "payload must be a JSON object"}
    action = (payload.get("action") or "process").lower()
    if action == "process":
        return _process(payload, context)
    if action == "approve":
        return _manual_approve(payload, context)
    if action == "rollback":
        return _manual_rollback(payload, context)
    return {"error": f"unknown action: {action}"}


if __name__ == "__main__":
    app.run()
