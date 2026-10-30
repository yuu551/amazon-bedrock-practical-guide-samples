"""AgentCore Gateway RESPONSE インターセプター

サンプルコネクト社のカスタマーサポートAIシステムにおいて、
ターゲット呼び出しの**後**、レスポンスを返す前に実行されるインターセプターです。

tools/list のレスポンスをフィルタリングし、オペレーターの役職に
応じて利用可能なツールだけを返します。エージェントに見えない
ツールは呼ぼうとしないため、REQUEST 側の認可エラーを未然に防げます。

REQUEST Interceptor と組み合わせることで多層防御になります。
  - RESPONSE Interceptor: そもそも見せない（ツール候補から除外）
  - REQUEST Interceptor: 呼ばれても拒否（認可チェック）
"""
from __future__ import annotations

import base64
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# =============================================================================
# ロール定義 — request_handler.py と同じ定義を使う
# NOTE: サンプルでは各Lambda関数を独立して動かせるよう定義を複製しています。
# 実運用では共通モジュールやLambda Layerに切り出して一元管理してください。
# =============================================================================

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "senior-operator": {
        "get_service_status",
        "search_knowledge",
        "get_customer_info",
        "list_recent_tickets",
        "create_issue",
        "update_crm_record",
    },
    "operator": {
        "get_service_status",
        "search_knowledge",
        "get_customer_info",
        "list_recent_tickets",
    },
    "guest": {
        "search_knowledge",
    },
}

DEFAULT_ROLE = "guest"


# =============================================================================
# JWT デコード（request_handler.py と同じロジック）
# =============================================================================

def decode_jwt_payload(auth_header: str) -> dict:
    token = auth_header.removeprefix("Bearer ").strip()
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload_b64 = parts[1]
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def extract_role(jwt_payload: dict) -> str:
    groups = jwt_payload.get("cognito:groups", [])
    for group in groups:
        if group in ROLE_PERMISSIONS:
            return group
    role = jwt_payload.get("custom:role", "")
    if role in ROLE_PERMISSIONS:
        return role
    return DEFAULT_ROLE


# =============================================================================
# ツールフィルタリング
# =============================================================================

def filter_tools(tools: list[dict], allowed_tools: set[str]) -> list[dict]:
    """ツール一覧からロールに許可されたものだけを残す。

    ツール名は Gateway の "{target}___{tool_name}" 形式。
    末尾の tool_name 部分で判定する。
    """
    filtered = []
    for tool in tools:
        name = tool.get("name", "")
        # "{target}___{tool_name}" → tool_name を抽出
        tool_name = name.split("___")[-1] if "___" in name else name
        if tool_name in allowed_tools:
            filtered.append(tool)
    return filtered


# =============================================================================
# Lambda ハンドラー
# =============================================================================

def lambda_handler(event: dict, context) -> dict:
    """RESPONSE インターセプターのエントリーポイント。

    処理フロー:
      1. tools/list 以外のレスポンスはそのまま通過
      2. JWT からオペレーターのロールを抽出
      3. レスポンスのツール一覧をロールに応じてフィルタリング
    """
    mcp = event.get("mcp", {})

    # 元リクエストのメソッドを確認
    request_body = mcp.get("gatewayRequest", {}).get("body", {})
    method = request_body.get("method", "")

    # ターゲットからのレスポンス
    gateway_response = mcp.get("gatewayResponse", {})
    status_code = gateway_response.get("statusCode", 200)
    response_body = gateway_response.get("body", {})

    # tools/list 以外、またはエラーレスポンスはそのまま返す
    if method != "tools/list" or status_code != 200:
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "statusCode": status_code,
                    "body": response_body,
                }
            },
        }

    # --- JWT からロールを取得 ---
    headers = mcp.get("gatewayRequest", {}).get("headers", {})
    auth_header = headers.get("Authorization", headers.get("authorization", ""))
    jwt_payload = decode_jwt_payload(auth_header) if auth_header else {}
    role = extract_role(jwt_payload)

    allowed_tools = ROLE_PERMISSIONS.get(role, set())

    # --- ツール一覧をフィルタリング ---
    result = response_body.get("result", {})
    original_tools = result.get("tools", [])
    filtered = filter_tools(original_tools, allowed_tools)

    logger.info(json.dumps({
        "event_type": "response_intercept",
        "role": role,
        "original_tool_count": len(original_tools),
        "filtered_tool_count": len(filtered),
        "removed": [
            t.get("name", "") for t in original_tools if t not in filtered
        ],
    }, ensure_ascii=False))

    # フィルタ後のレスポンスを返す
    filtered_body = {**response_body}
    filtered_body["result"] = {**result, "tools": filtered}

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "body": filtered_body,
            }
        },
    }
