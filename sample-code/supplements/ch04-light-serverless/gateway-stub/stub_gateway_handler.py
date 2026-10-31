"""Atlassianにつながない、Gateway用のダミーのLambda。

ツールの名前と応答の形は第4章の atlassian_gateway_handler.py と同じで、中身だけを固定にしている。
ATLASSIAN_BASE_URL を設定しないと、CDKはこちらをGatewayの接続先にする。
Confluenceの検索は、第4章の setup_atlassian.py が投入する3ページから語の合うものを返す。
Jiraの起票は、実際には課題を作らずにダミーの課題番号を返す。
"""

from __future__ import annotations

import itertools
import re
from typing import Any

BASE_URL = "https://example.atlassian.net"

PAGES = [
    {
        "id": "1001",
        "title": "東京リージョン ファイルアップロード障害 ポストモーテム（2026-03）",
        "keywords": ["アップロード", "ファイル", "タイムアウト", "東京", "ポストモーテム", "障害"],
        "body": (
            "障害概要: 2026年3月15日 14:00〜16:30（JST）にかけて、東京リージョン（ap-northeast-1）の"
            "ファイルアップロード機能で断続的なタイムアウトが発生しました。"
            "影響範囲: 東京リージョンを利用する約1,200テナントが影響を受け、ファイルアップロードAPIのエラー率が最大40%に達しました。"
            "原因: ストレージバックエンドの内部キュー飽和により、アップロードリクエストがタイムアウトしました。"
            "暫定回避策: ファイルサイズを10MB以下に分割してアップロードすることで成功率が改善します。"
            "リトライ間隔を30秒以上に設定することを推奨します。"
            "恒久対応: ストレージバックエンドのキュー容量を3倍に拡張し、バッチ処理のスロットリングを追加しました。2026年3月20日にデプロイ済みです。"
        ),
    },
    {
        "id": "1002",
        "title": "HTTP 502 Bad Gateway 既知障害 暫定回避策",
        "keywords": ["502", "bad gateway", "gateway", "エラー", "回避策", "既知"],
        "body": (
            "事象: SampleConnect PlatformのAPIエンドポイントに対して、HTTP 502 Bad Gatewayが断続的に返却される事象が報告されています。"
            "発生条件: リクエストボディが5MBを超えるPOSTリクエストで発生しやすい傾向があります。GETリクエストでは再現しません。"
            "暫定回避策: 1. リクエストボディを5MB以下に分割する 2. 502受信時は5秒待ってリトライする（最大3回）"
            " 3. Content-Encodingにgzipを指定して圧縮する。"
            "ステータス: ロードバランサーのタイムアウト設定の調整を検討中です。次回メンテナンスウィンドウ（2026年4月第1週）で適用予定です。"
        ),
    },
    {
        "id": "1003",
        "title": "SAML属性マッピングの落とし穴（Microsoft Entra ID）",
        "keywords": ["saml", "sso", "entra", "azure", "ログイン", "属性"],
        "body": (
            "概要: Microsoft Entra ID（旧Azure AD）とSampleConnect PlatformのSAML SSO連携で、"
            "属性マッピングの設定ミスによりログインに失敗するケースが複数報告されています。"
            "よくあるミス: 1. NameID形式の不一致 2. 属性名の大文字小文字（emailaddressとEmailAddressは別の属性） 3. グループ属性の未設定。"
            "確認手順: SampleConnect管理コンソールの「SSO設定」→「SAMLログ」から直近のSAMLレスポンスを確認し、"
            "Entra ID側の「属性とクレーム」と突き合わせてください。"
        ),
    },
]

# Lambdaの実行環境が残っているあいだは、課題番号を1つずつ進める
_ticket_numbers = itertools.count(101)


def extract_tool_name(context: Any) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    visible_name = custom.get("bedrockAgentCoreToolName", "") if isinstance(custom, dict) else ""
    if "___" in visible_name:
        return visible_name.split("___", 1)[1]
    return visible_name


def search_confluence(event: dict[str, Any]) -> dict[str, Any]:
    query = str(event.get("query", "")).lower()
    limit = int(event.get("limit", 5))
    terms = [t for t in re.split(r"[\s　、,]+", query) if t]

    def score(page: dict[str, Any]) -> int:
        text = (page["title"] + page["body"]).lower()
        hits = sum(1 for k in page["keywords"] if k in query)
        hits += sum(1 for t in terms if t in text)
        return hits

    matched = sorted((p for p in PAGES if score(p) > 0), key=score, reverse=True)[:limit]
    results = [
        {
            "title": p["title"],
            "url": f"{BASE_URL}/wiki/spaces/DEMO/pages/{p['id']}",
            "excerpt": p["body"][:120],
            "body": p["body"],
        }
        for p in matched
    ]
    return {"count": len(results), "results": results}


def create_jira_ticket(event: dict[str, Any]) -> dict[str, Any]:
    issue_key = f"DEMO-{next(_ticket_numbers)}"
    return {
        "status": "created",
        "issueKey": issue_key,
        "issueUrl": f"{BASE_URL}/browse/{issue_key}",
        "note": "ダミーのため、実際のJiraには課題を作っていません",
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    tool_name = extract_tool_name(context)
    if tool_name == "confluence_search":
        return search_confluence(event)
    if tool_name == "create_jira_ticket":
        return create_jira_ticket(event)
    raise ValueError(f"Unsupported tool: {tool_name}")
