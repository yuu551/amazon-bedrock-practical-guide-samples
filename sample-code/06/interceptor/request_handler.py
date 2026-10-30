"""AgentCore Gateway REQUEST インターセプター

サンプルコネクト社のカスタマーサポートAIシステムにおいて、
ツール呼び出しの**前**に実行されるインターセプターです。

2つの制御を行います。
  1. JWTベースのロール認可 — オペレーターの役職に応じてツール呼び出しを許可/拒否
  2. 引数バリデーション — 顧客IDブロック、金額上限チェック

JWTの署名検証はGateway側で実施済みのため、
ここではペイロードのデコードだけで十分です。
"""
from __future__ import annotations

import base64
import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# =============================================================================
# ロール定義 — 役職ごとに許可するツール操作を定義
# =============================================================================
# ツール名は Gateway の "{target}___{tool_name}" 形式で届く。
# ここではツール名の末尾部分（tool_name）で判定する。

ROLE_PERMISSIONS: dict[str, set[str]] = {
    # シニアオペレーター: 全ツール利用可能（参照系 + 更新系）
    "senior-operator": {
        "get_service_status",
        "search_knowledge",
        "get_customer_info",
        "list_recent_tickets",
        "create_issue",          # Jiraエスカレーション（更新系）
        "update_crm_record",     # CRM更新（更新系）
    },
    # 一般オペレーター: 参照系のみ
    "operator": {
        "get_service_status",
        "search_knowledge",
        "get_customer_info",
        "list_recent_tickets",
    },
    # ゲスト（閲覧のみ）
    "guest": {
        "search_knowledge",
    },
}

# デフォルトロール（JWTにロール情報がない場合）
DEFAULT_ROLE = "guest"


# =============================================================================
# 設定値の読み込み
# =============================================================================

def _load_blocked_customer_ids() -> set[str]:
    raw = os.environ.get("BLOCKED_CUSTOMER_IDS", "")
    if not raw.strip():
        return set()
    return {cid.strip() for cid in raw.split(",") if cid.strip()}


MAX_AMOUNT: int = int(os.environ.get("MAX_AMOUNT", "1000000"))
BLOCKED_CUSTOMER_IDS: set[str] = _load_blocked_customer_ids()


# =============================================================================
# JWT デコード
# =============================================================================

def decode_jwt_payload(auth_header: str) -> dict:
    """Authorization ヘッダーから JWT ペイロードをデコードする。

    署名検証は Gateway が実施済みのため、ここでは Base64 デコードのみ行う。
    """
    token = auth_header.removeprefix("Bearer ").strip()
    # JWT は header.payload.signature の3パート
    parts = token.split(".")
    if len(parts) != 3:
        return {}

    # Base64URL → Base64 → デコード
    payload_b64 = parts[1]
    # パディング補完
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception:
        return {}


def extract_role(jwt_payload: dict) -> str:
    """JWT ペイロードからオペレーターのロールを取得する。

    Cognito の場合は cognito:groups、カスタム属性の場合は custom:role を確認する。
    """
    # Cognito グループから取得（最初にマッチしたロールを使う）
    groups = jwt_payload.get("cognito:groups", [])
    for group in groups:
        if group in ROLE_PERMISSIONS:
            return group

    # カスタム属性から取得
    role = jwt_payload.get("custom:role", "")
    if role in ROLE_PERMISSIONS:
        return role

    return DEFAULT_ROLE


# =============================================================================
# レスポンス生成
# =============================================================================

def _passthrough(request_body: dict) -> dict:
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": request_body}},
    }


def _block(status_code: int, message: str) -> dict:
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": status_code,
                "body": {
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": message},
                },
            }
        },
    }


# =============================================================================
# バリデーション（引数の検査）
# =============================================================================

def validate_arguments(arguments: dict) -> str | None:
    """ツール呼び出しの引数を検査する。問題なければ None を返す。"""
    # 顧客IDブロック
    customer_id = arguments.get("customer_id")
    if customer_id and str(customer_id) in BLOCKED_CUSTOMER_IDS:
        return "指定された顧客IDへのアクセスは制限されています"

    # 金額上限
    amount = arguments.get("amount")
    if amount is not None:
        try:
            if int(amount) > MAX_AMOUNT:
                return f"金額 {amount} は上限 {MAX_AMOUNT} を超えています"
        except (ValueError, TypeError):
            return f"金額の値が不正です: {amount}"

    return None


# =============================================================================
# 監査ログ
# =============================================================================

def _audit_log(
    correlation_id: str,
    role: str,
    tool_name: str,
    action: str,
    reason: str = "",
) -> None:
    logger.info(json.dumps({
        "event_type": "request_intercept",
        "correlation_id": correlation_id,
        "role": role,
        "tool_name": tool_name,
        "action": action,
        "reason": reason,
    }, ensure_ascii=False))


# =============================================================================
# Lambda ハンドラー
# =============================================================================

def lambda_handler(event: dict, context) -> dict:
    """REQUEST インターセプターのエントリーポイント。

    処理フロー:
      1. tools/call 以外のメソッドはそのまま通過
      2. JWT からオペレーターのロールを抽出
      3. ロールに基づくツール認可を判定
      4. 引数のバリデーション（顧客ID、金額上限）
      5. すべてパスしたらターゲットへ転送
    """
    try:
        request_body = event["mcp"]["gatewayRequest"]["body"]
    except (KeyError, TypeError):
        return _block(400, "不正なリクエスト形式です")

    headers = event.get("mcp", {}).get("gatewayRequest", {}).get("headers", {})
    correlation_id = headers.get("x-request-id", "unknown")

    method = request_body.get("method", "")

    # tools/call 以外はそのまま通す
    if method != "tools/call":
        return _passthrough(request_body)

    # --- JWT からロールを取得 ---
    auth_header = headers.get("Authorization", headers.get("authorization", ""))
    jwt_payload = decode_jwt_payload(auth_header) if auth_header else {}
    role = extract_role(jwt_payload)

    # --- ツール名を取得 ---
    params = request_body.get("params", {})
    # Gateway のツール名形式: "{target}___{tool_name}"
    full_tool_name = params.get("name", "unknown")
    tool_name = full_tool_name.split("___")[-1] if "___" in full_tool_name else full_tool_name

    # --- ロール認可 ---
    allowed_tools = ROLE_PERMISSIONS.get(role, set())
    if tool_name not in allowed_tools:
        _audit_log(correlation_id, role, full_tool_name, "DENIED", "ロール権限なし")
        return _block(
            403,
            f"ツール '{tool_name}' の実行権限がありません",
        )

    # --- 引数バリデーション ---
    arguments = params.get("arguments", {})
    violation = validate_arguments(arguments)
    if violation:
        _audit_log(correlation_id, role, full_tool_name, "BLOCKED", violation)
        return _block(403, violation)

    # --- すべてパス ---
    _audit_log(correlation_id, role, full_tool_name, "ALLOWED")
    return _passthrough(request_body)
