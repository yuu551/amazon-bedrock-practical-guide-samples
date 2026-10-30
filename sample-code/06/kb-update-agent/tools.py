"""Knowledge Base 更新パイプラインの処理と副作用を実装する関数群。

handler.py が決定論的な品質ゲートの判定に従って呼び出す。エージェントには
これらの関数をツールとして渡さない。
"""
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_METRICS = ("correctness", "completeness", "groundedness")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """``s3://bucket/key`` を ``(bucket, key)`` に分解する。"""
    if not uri.startswith("s3://"):
        raise ValueError(f"S3 URI ではありません: {uri}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"S3 URI の形式が不正です: {uri}")
    return bucket, key


# ---------------------------------------------------------------------------
# 決定論的ヘルパー ── main.py から順次呼び出す
# ---------------------------------------------------------------------------


_ACTIVE_INGESTION_STATUSES = {"STARTING", "IN_PROGRESS", "STOPPING"}
_TERMINAL_INGESTION_STATUSES = {"COMPLETE", "FAILED", "STOPPED"}


def _wait_for_ingestion_job(
    client: Any,
    knowledge_base_id: str,
    data_source_id: str,
    ingestion_job_id: str,
    *,
    deadline: float,
    poll_interval_sec: int,
) -> dict[str, Any]:
    """指定した取り込みジョブを終端状態まで待つ。"""
    while True:
        job = client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id,
            dataSourceId=data_source_id,
            ingestionJobId=ingestion_job_id,
        )["ingestionJob"]
        if job["status"] in _TERMINAL_INGESTION_STATUSES:
            return job
        if time.monotonic() >= deadline:
            return job
        time.sleep(poll_interval_sec)


def _find_active_ingestion_job(
    client: Any, knowledge_base_id: str, data_source_id: str
) -> dict[str, Any] | None:
    """競合中の取り込みジョブを新しい順に探す。"""
    response = client.list_ingestion_jobs(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=data_source_id,
        sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        maxResults=10,
    )
    return next(
        (
            job
            for job in response.get("ingestionJobSummaries", [])
            if job.get("status") in _ACTIVE_INGESTION_STATUSES
        ),
        None,
    )


def _sync_result(
    job: dict[str, Any], *, waited_for_ingestion_job_ids: list[str]
) -> dict[str, Any]:
    stats = job.get("statistics", {})
    return {
        "ingestion_job_id": job["ingestionJobId"],
        "status": job["status"],
        "statistics": {
            "scanned": stats.get("numberOfDocumentsScanned", 0),
            "indexed": stats.get("numberOfNewDocumentsIndexed", 0),
            "updated": stats.get("numberOfModifiedDocumentsIndexed", 0),
            "deleted": stats.get("numberOfDocumentsDeleted", 0),
            "failed": stats.get("numberOfDocumentsFailed", 0),
        },
        "failure_reasons": job.get("failureReasons", []),
        "waited_for_ingestion_job_ids": waited_for_ingestion_job_ids,
    }


def start_and_wait_for_sync(
    knowledge_base_id: str,
    data_source_id: str,
    *,
    poll_interval_sec: int = 10,
    timeout_sec: int = 600,
) -> dict[str, Any]:
    """Knowledge Base の同期を起動し、完了まで待機する。

    COMPLETE / FAILED / STOPPED のいずれかが返るまでポーリングする。
    タイムアウト時は最終状態をそのまま返す。
    """
    deadline = time.monotonic() + timeout_sec
    client = boto3.client("bedrock-agent", region_name=_REGION)
    waited_for: list[str] = []

    # この Lambda は予約済み同時実行数1だが、手動同期など外部のジョブと
    # 競合する可能性は残る。ConflictException の即時再試行はせず、競合中の
    # ジョブを特定して終了を待ってから、今回分の同期を1回起動する。
    while True:
        try:
            started = client.start_ingestion_job(
                knowledgeBaseId=knowledge_base_id,
                dataSourceId=data_source_id,
            )["ingestionJob"]
            break
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConflictException":
                raise
            active = _find_active_ingestion_job(
                client, knowledge_base_id, data_source_id
            )
            if active is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "ConflictException 解消待ちがタイムアウトしました"
                    ) from exc
                time.sleep(poll_interval_sec)
                continue
            active_id = active["ingestionJobId"]
            waited_for.append(active_id)
            finished = _wait_for_ingestion_job(
                client,
                knowledge_base_id,
                data_source_id,
                active_id,
                deadline=deadline,
                poll_interval_sec=poll_interval_sec,
            )
            if (
                finished["status"] not in _TERMINAL_INGESTION_STATUSES
                and time.monotonic() >= deadline
            ):
                return _sync_result(
                    finished, waited_for_ingestion_job_ids=waited_for
                )

    job = _wait_for_ingestion_job(
        client,
        knowledge_base_id,
        data_source_id,
        started["ingestionJobId"],
        deadline=deadline,
        poll_interval_sec=poll_interval_sec,
    )
    return _sync_result(job, waited_for_ingestion_job_ids=waited_for)


_JUDGE_PROMPT = """あなたはRAGの品質評価者です。
質問と期待条件、回答、参照先を読み、採点基準に沿って採点してください。

# 質問
{question}

# 期待要点
{expected_points}

# 回答
{answer}

# 参照先
{references}

# 採点基準
各指標について 0.0 / 0.5 / 1.0 で採点してください。
- correctness: 回答内容が正確か
- completeness: 期待要点を網羅しているか
- groundedness: 参照先に基づいた回答か

JSONオブジェクトのみを返してください。
{{"scores": {{"correctness": 0.0, "completeness": 0.0, "groundedness": 0.0}}, "primary_failure_type": "retrieval | answer | none", "reason": ""}}"""


def _parse_sse_response(text: str) -> dict[str, Any]:
    """SSE 形式のレスポンスを ``answer`` と ``references`` へ組み立てる。

    Lambda Function URL が ``RESPONSE_STREAM`` モードでチャンク配信する
    形式に対応する。``event: chunk`` の ``text`` フィールドを連結して
    ``answer`` を作り、``event: citation`` の ``uri`` を ``references`` に積む。
    """
    answer_parts: list[str] = []
    references: list[str] = []
    event_type = "message"
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            event_type = "message"
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip() or "message"
            continue
        if line.startswith("data:"):
            payload = line[len("data:"):].lstrip()
            try:
                obj = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                obj = None
            if obj is None:
                continue
            if event_type == "chunk":
                text_part = obj.get("text") or obj.get("data") or ""
                if text_part:
                    answer_parts.append(text_part)
            elif event_type == "citation":
                uri = obj.get("uri") or obj.get("source") or ""
                if uri:
                    references.append(uri)
    return {"answer": "".join(answer_parts), "references": references}


def _sigv4_sign(
    method: str,
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    service: str,
    region: str,
) -> dict[str, str]:
    """AWS_IAM 認証エンドポイント向けに SigV4 ヘッダーを付与する。"""
    creds = boto3.Session(region_name=region).get_credentials()
    if creds is None:
        raise RuntimeError(
            "AWS credentials not found for SigV4 signing. "
            "Set AWS_PROFILE or environment credentials."
        )
    frozen = creds.get_frozen_credentials()
    req = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(frozen, service, region).add_auth(req)
    return dict(req.headers)



# 温度を受け付けず、適応的思考が既定で有効なモデル。
# モデルIDと推論プロファイルARNのどちらでも末尾で判定する。
_REASONING_MODELS = (
    "anthropic.claude-sonnet-5",
    "anthropic.claude-sonnet-5-5",
    "anthropic.claude-opus-5",
    "anthropic.claude-opus-5-5",
    "anthropic.claude-haiku-5-5",
    "openai.gpt-6-sol",
    "openai.gpt-6.1-sol",
    "openai.gpt-6-luna",
)


def _uses_reasoning_defaults(model_id: str) -> bool:
    return model_id.endswith(_REASONING_MODELS)


def _knowledge_base_prompts(model_id: str) -> dict[str, Any]:
    """新しいモデルのときだけ、RetrieveAndGenerateに渡す2つのプロンプトを返す。

    新しいモデルではKB既定のプロンプトが用意されていない場合がある。
    引用を返すため、AWSが展開する出力形式の指示も保持する。
    """
    if not _uses_reasoning_defaults(model_id):
        return {}
    return {
        "generationConfiguration": {
            "promptTemplate": {
                "textPromptTemplate": (
                    "検索結果に基づいて日本語で回答してください。資料にないことは推測せず、不明と伝えてください。"
                    "資料の適用時期が異なる場合は区別してください。\n"
                    "質問: $query$\n検索結果:\n$search_results$\n$output_format_instructions$"
                ),
            },
            "inferenceConfig": {"textInferenceConfig": {"maxTokens": 4096}},
        },
        "orchestrationConfiguration": {
            "promptTemplate": {
                "textPromptTemplate": (
                    "会話履歴と質問から、関連資料を探す検索クエリを作成してください。\n"
                    "会話履歴:\n$conversation_history$\n質問: $query$\n$output_format_instructions$"
                ),
            },
            "inferenceConfig": {"textInferenceConfig": {"maxTokens": 4096}},
        },
    }


def _retrieve_and_generate(
    knowledge_base_id: str, model_id: str, question: str
) -> dict[str, Any]:
    """Knowledge Base を直接 ``RetrieveAndGenerate`` で叩いて回答を取得する。

    第3章で構築した検索アプリケーションを介さず、Bedrock Knowledge Bases の
    API を呼ぶことで、品質ゲートエージェントの評価経路を Knowledge Base に
    閉じる。これにより外部 Lambda の認可設定の影響を受けない。
    """
    client = boto3.client("bedrock-agent-runtime", region_name=_REGION)
    model_arn = (
        model_id
        if model_id.startswith("arn:")
        else f"arn:aws:bedrock:{_REGION}:{boto3.client('sts', region_name=_REGION).get_caller_identity()['Account']}:inference-profile/{model_id}"
    )
    resp = client.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": knowledge_base_id,
                "modelArn": model_arn,
                **_knowledge_base_prompts(model_arn),
            },
        },
    )
    answer = resp.get("output", {}).get("text", "")
    references: list[str] = []
    for cite in resp.get("citations", []) or []:
        for ref in cite.get("retrievedReferences", []) or []:
            loc = ref.get("location", {})
            uri = (
                loc.get("s3Location", {}).get("uri")
                or loc.get("webLocation", {}).get("url")
                or ""
            )
            if uri and uri not in references:
                references.append(uri)
    return {"answer": answer, "references": references}


def _call_app_api(
    api_url: str, question: str, *, timeout: int = 60
) -> dict[str, Any]:
    """アプリケーション API へ質問を送り、回答と参照先を取得する。

    レスポンス形式は2通りに対応:
    - ``application/json`` の場合は ``answer`` / ``references`` キーを読む
    - ``text/event-stream`` の場合は SSE を組み立て直す（Lambda Function
      URL の ``RESPONSE_STREAM`` モードや AgentCore Runtime のストリーム
      レスポンスを想定）

    認証は環境変数 ``APP_API_AUTH`` で切り替える:
    - 未設定または ``none``: そのまま POST する
    - ``sigv4`` または ``aws_iam``: AWS 資格情報で SigV4 署名を付ける
      （Lambda Function URL の ``AuthType: AWS_IAM`` 向け）
    - ``bearer``: 環境変数 ``APP_API_BEARER_TOKEN`` の値を ``Authorization``
      ヘッダーへ載せる（Cognito JWT などを想定）
    """
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    auth_mode = (os.environ.get("APP_API_AUTH") or "").strip().lower()
    if auth_mode in ("sigv4", "aws_iam"):
        service = os.environ.get("APP_API_SIGV4_SERVICE", "lambda")
        region = os.environ.get("APP_API_SIGV4_REGION", _REGION)
        headers = _sigv4_sign(
            "POST", api_url, body, headers, service=service, region=region
        )
    elif auth_mode == "bearer":
        token = os.environ.get("APP_API_BEARER_TOKEN", "")
        if not token:
            raise RuntimeError(
                "APP_API_AUTH=bearer requires APP_API_BEARER_TOKEN env var."
            )
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.post(api_url, data=body, headers=headers, timeout=timeout)
    resp.raise_for_status()

    content_type = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" in content_type:
        return _parse_sse_response(resp.content.decode("utf-8"))

    data = resp.json()
    references = data.get("references")
    if not references and isinstance(data.get("citations"), list):
        references = [
            c.get("uri", "") for c in data["citations"] if isinstance(c, dict)
        ]
    return {"answer": data.get("answer", ""), "references": references or []}


def _extract_json(text: str) -> dict[str, Any]:
    """LLM出力テキストからJSONオブジェクトを抽出する。

    最初の ``{`` から対応する ``}`` までを切り出してパースする。
    文字列リテラル内の括弧は無視する。
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output.")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("No complete JSON object found in model output.")


def _judge_single(
    runtime: Any,
    *,
    judge_model_id: str,
    question: str,
    expected_points: list[str],
    answer: str,
    references: list[str],
) -> dict[str, Any]:
    prompt = _JUDGE_PROMPT.format(
        question=question,
        expected_points=json.dumps(expected_points, ensure_ascii=False),
        answer=answer,
        references=json.dumps(references, ensure_ascii=False),
    )
    resp = runtime.converse(
        modelId=judge_model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        # 新しいモデルは温度を受け付けず、思考の分も出力上限に含める
        inferenceConfig=(
            {"maxTokens": 4096}
            if _uses_reasoning_defaults(judge_model_id)
            else {"temperature": 0, "maxTokens": 800}
        ),
    )
    # 思考ブロックが先頭に来るモデルもあるため、最初のテキストを使う
    text = next(
        block["text"]
        for block in resp["output"]["message"]["content"]
        if "text" in block
    )
    return _extract_json(text)


def run_evaluation(
    api_url: str,
    eval_cases_path: str,
    judge_model_id: str,
    *,
    knowledge_base_id: str | None = None,
    answer_model_id: str | None = None,
) -> dict[str, Any]:
    """評価ケースを一括実行し、ケース別スコアと平均を返す。

    第5章の LLM-as-a-Judge と同じ方式。回答生成の経路は2つから選択する:

    - ``api_url`` が指定されていればアプリケーション API（外部 Lambda 等）を呼ぶ
    - ``api_url`` が空または ``"kb"`` の場合は Knowledge Base を直接
      ``RetrieveAndGenerate`` で叩く（``knowledge_base_id`` と
      ``answer_model_id`` が必要）
    """
    cases = json.loads(Path(eval_cases_path).read_text(encoding="utf-8"))
    runtime = boto3.client("bedrock-runtime", region_name=_REGION)
    use_kb_direct = (not api_url) or api_url.lower() == "kb"
    if use_kb_direct and not (knowledge_base_id and answer_model_id):
        raise RuntimeError(
            "api_url が空のときは knowledge_base_id と answer_model_id が必須です。"
        )
    results: list[dict[str, Any]] = []

    for case in cases:
        started = time.perf_counter()
        try:
            if use_kb_direct:
                app_resp = _retrieve_and_generate(
                    knowledge_base_id, answer_model_id, case["question"]
                )
            else:
                app_resp = _call_app_api(api_url, case["question"])
            answer = app_resp.get("answer", "")
            references = app_resp.get("references", [])
            judge_result = _judge_single(
                runtime,
                judge_model_id=judge_model_id,
                question=case["question"],
                expected_points=case.get("expected_points", []),
                answer=answer,
                references=references,
            )
            results.append(
                {
                    "case_id": case["id"],
                    "question": case["question"],
                    "scores": judge_result.get("scores", {}),
                    "failure_type": judge_result.get(
                        "primary_failure_type", "none"
                    ),
                    "reason": judge_result.get("reason", ""),
                    "latency_ms": round(
                        (time.perf_counter() - started) * 1000
                    ),
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "case_id": case["id"],
                    "question": case["question"],
                    "scores": {m: 0.0 for m in _METRICS},
                    "failure_type": "error",
                    "reason": str(exc),
                    "latency_ms": round(
                        (time.perf_counter() - started) * 1000
                    ),
                    "error": str(exc),
                }
            )

    averages = {}
    for m in _METRICS:
        vals = [r["scores"].get(m, 0.0) for r in results]
        averages[m] = round(statistics.mean(vals), 3) if vals else 0.0

    return {"case_count": len(results), "averages": averages, "cases": results}


def load_baseline(baseline_path: str) -> dict[str, Any]:
    """前回のベースラインスコアを読み込む。

    ローカルファイルパスと ``s3://`` 形式の両方をサポートする。
    存在しない場合は ``{"exists": False, ...}`` を返す。
    """
    if baseline_path.startswith("s3://"):
        bucket, key = _parse_s3_uri(baseline_path)
        s3 = boto3.client("s3", region_name=_REGION)
        try:
            resp = s3.get_object(Bucket=bucket, Key=key)
        except s3.exceptions.NoSuchKey:
            return {
                "exists": False,
                "message": "ベースラインが存在しません。初回実行です。",
            }
        except Exception as exc:  # noqa: BLE001
            if "NoSuchKey" in str(exc) or "Not Found" in str(exc):
                return {
                    "exists": False,
                    "message": "ベースラインが存在しません。初回実行です。",
                }
            raise
        body = resp["Body"].read().decode("utf-8")
        return {"exists": True, **json.loads(body)}

    p = Path(baseline_path)
    if not p.exists():
        return {
            "exists": False,
            "message": "ベースラインが存在しません。初回実行です。",
        }
    return {"exists": True, **json.loads(p.read_text(encoding="utf-8"))}


def _review_revision_path(baseline_path: str) -> str:
    return f"{baseline_path}.review-revision.json"


def record_review_revision(baseline_path: str, revision: str) -> dict[str, str]:
    """後続の更新が始まったことを共有領域に記録する。"""
    path = _review_revision_path(baseline_path)
    body = json.dumps({"revision": revision}, ensure_ascii=False).encode("utf-8")
    if path.startswith("s3://"):
        bucket, key = _parse_s3_uri(path)
        boto3.client("s3", region_name=_REGION).put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
    else:
        local_path = Path(path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(body)
    return {"path": path, "revision": revision}


def load_review_revision(baseline_path: str) -> str | None:
    """最後に更新処理を始めたセッションの世代を読む。"""
    path = _review_revision_path(baseline_path)
    if path.startswith("s3://"):
        bucket, key = _parse_s3_uri(path)
        s3 = boto3.client("s3", region_name=_REGION)
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        body = response["Body"].read()
    else:
        local_path = Path(path)
        if not local_path.exists():
            return None
        body = local_path.read_bytes()
    return json.loads(body.decode("utf-8")).get("revision")


# ---------------------------------------------------------------------------
# 品質ゲートの判定後に handler.py が呼ぶ副作用関数
# ---------------------------------------------------------------------------
# エージェントからは呼び出さず、handler.py の判定結果と decide.py の
# 手動操作だけを入口にする。


def save_baseline_impl(baseline_path: str, evaluation_result_json: str) -> dict[str, Any]:
    """現在の評価結果を新しいベースラインとして保存する。

    ローカルファイルパスと ``s3://`` 形式の両方をサポートする。
    """
    data = json.loads(evaluation_result_json)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    if baseline_path.startswith("s3://"):
        bucket, key = _parse_s3_uri(baseline_path)
        boto3.client("s3", region_name=_REGION).put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        return {
            "saved": True,
            "path": baseline_path,
            "case_count": data.get("case_count", 0),
        }

    p = Path(baseline_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return {
        "saved": True,
        "path": str(p),
        "case_count": data.get("case_count", 0),
    }


def _delete_created_versions(
    s3: Any,
    bucket: str,
    document_keys: list[str],
    object_versions: dict[str, list[str]],
) -> int:
    """今回のオブジェクト作成イベントで生成されたバージョンだけを削除する。

    削除前に対象を全件検証する。上書きなら前バージョンが再び現行になり、
    新規追加ならオブジェクトが見えなくなる。Version ID なしのキー削除は行わない。
    """
    if not document_keys:
        raise ValueError("復元対象の文書キーがありません")
    if not isinstance(object_versions, dict) or not object_versions:
        raise ValueError("S3 Version ID がないため安全に復元できません")

    allowed_objects = {
        object_key
        for key in document_keys
        for object_key in (key, f"{key}.metadata.json")
    }
    unexpected = sorted(set(object_versions) - allowed_objects)
    if unexpected:
        raise ValueError(f"文書一覧にない復元対象です: {unexpected}")

    for key in document_keys:
        if not any(
            object_versions.get(candidate)
            for candidate in (key, f"{key}.metadata.json")
        ):
            raise ValueError(f"{key!r} に対応する S3 Version ID がありません")
    for object_key, version_ids in object_versions.items():
        if not isinstance(version_ids, list) or not version_ids:
            raise ValueError(f"{object_key!r} の Version ID 一覧が不正です")
        if any(
            not isinstance(version_id, str) or not version_id
            for version_id in version_ids
        ):
            raise ValueError(f"{object_key!r} に空の Version ID が含まれています")

    deleted_versions = 0
    for object_key, version_ids in object_versions.items():
        for version_id in version_ids:
            s3.delete_object(Bucket=bucket, Key=object_key, VersionId=version_id)
            deleted_versions += 1
    return deleted_versions


def rollback_documents_impl(
    bucket: str,
    keys_json: str,
    version_ids_json: str,
    knowledge_base_id: str,
    data_source_id: str,
) -> dict[str, Any]:
    """今回作成された S3 バージョンを除き、Knowledge Base を再同期する。"""
    keys = json.loads(keys_json)
    object_versions = json.loads(version_ids_json)
    s3 = boto3.client("s3", region_name=_REGION)
    deleted_versions = _delete_created_versions(s3, bucket, keys, object_versions)

    agent_client = boto3.client("bedrock-agent", region_name=_REGION)
    resp = agent_client.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=data_source_id,
        description=(
            f"Rollback: removed {deleted_versions} event versions for "
            f"{len(keys)} documents"
        ),
    )
    job = resp["ingestionJob"]
    return {
        "restored_document_count": len(keys),
        "deleted_version_count": deleted_versions,
        "rollback_job_id": job["ingestionJobId"],
        "status": job["status"],
    }


def send_notification_impl(subject: str, message: str) -> dict[str, Any]:
    """SNS トピックへ通知を送信する。``SNS_TOPIC_ARN`` 未設定なら標準出力。"""
    topic_arn = os.environ.get("SNS_TOPIC_ARN")
    if not topic_arn:
        print(f"\n[Notification] {subject}\n{message}")
        return {
            "sent": False,
            "reason": "SNS_TOPIC_ARN not set. Printed to stdout.",
        }

    sns = boto3.client("sns", region_name=_REGION)
    resp = sns.publish(TopicArn=topic_arn, Subject=subject[:100], Message=message)
    return {"sent": True, "message_id": resp["MessageId"]}
