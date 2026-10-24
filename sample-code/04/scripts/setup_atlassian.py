#!/usr/bin/env python
"""Atlassianサイトの初期セットアップスクリプト。

Confluenceにテスト用ページを投入し、Jiraプロジェクトの設定を確認する。
第4章のサンプルを動かす前の準備として使用する。

必要な環境変数:
    ATLASSIAN_BASE_URL   https://<site>.atlassian.net
    ATLASSIAN_EMAIL      ログインに使うメールアドレス
    ATLASSIAN_API_TOKEN  APIトークン
    CONFLUENCE_SPACE_KEY Confluenceスペースのキー
    JIRA_PROJECT_KEY     Jiraプロジェクトのキー
"""

from __future__ import annotations

import base64
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = os.environ["ATLASSIAN_BASE_URL"].rstrip("/")
EMAIL = os.environ["ATLASSIAN_EMAIL"]
API_TOKEN = os.environ["ATLASSIAN_API_TOKEN"]
SPACE_KEY = os.environ["CONFLUENCE_SPACE_KEY"]
PROJECT_KEY = os.environ["JIRA_PROJECT_KEY"]


def _auth_header() -> str:
    token = base64.b64encode(f"{EMAIL}:{API_TOKEN}".encode()).decode()
    return f"Basic {token}"


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(
        url,
        method=method,
        headers={
            "Authorization": _auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=data,
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


CONFLUENCE_PAGES = [
    {
        "title": "東京リージョン ファイルアップロード障害 ポストモーテム（2026-03）",
        "body": (
            "<h2>障害概要</h2>"
            "<p>2026年3月15日 14:00〜16:30（JST）にかけて、東京リージョン（ap-northeast-1）のファイルアップロード機能で断続的なタイムアウトが発生しました。</p>"
            "<h2>影響範囲</h2>"
            "<p>SampleConnect Platformの全テナントのうち、東京リージョンを利用する約1,200テナントが影響を受けました。ファイルアップロードAPIのエラー率が最大40%に達しました。</p>"
            "<h2>原因</h2>"
            "<p>ストレージバックエンドの内部キュー飽和により、アップロードリクエストがタイムアウトしました。前日のバッチ処理で想定を超えるファイルが投入されたことがトリガーです。</p>"
            "<h2>暫定回避策</h2>"
            "<p>ファイルサイズを10MB以下に分割してアップロードすることで成功率が改善します。また、リトライ間隔を30秒以上に設定することを推奨します。</p>"
            "<h2>恒久対応</h2>"
            "<p>ストレージバックエンドのキュー容量を3倍に拡張し、バッチ処理のスロットリングを追加しました。2026年3月20日にデプロイ済みです。</p>"
        ),
    },
    {
        "title": "HTTP 502 Bad Gateway 既知障害 暫定回避策",
        "body": (
            "<h2>事象</h2>"
            "<p>SampleConnect PlatformのAPIエンドポイントに対して、HTTP 502 Bad Gatewayが断続的に返却される事象が報告されています。</p>"
            "<h2>発生条件</h2>"
            "<p>リクエストボディが5MBを超えるPOSTリクエストで発生しやすい傾向があります。GETリクエストでは再現しません。</p>"
            "<h2>暫定回避策</h2>"
            "<p>1. リクエストボディを5MB以下に分割する</p>"
            "<p>2. 502受信時は5秒待ってリトライする（最大3回）</p>"
            "<p>3. Content-Encodingにgzipを指定して圧縮する</p>"
            "<h2>ステータス</h2>"
            "<p>ロードバランサーのタイムアウト設定の調整を検討中です。次回メンテナンスウィンドウ（2026年4月第1週）で適用予定です。</p>"
        ),
    },
    {
        "title": "SAML属性マッピングの落とし穴（Microsoft Entra ID）",
        "body": (
            "<h2>概要</h2>"
            "<p>Microsoft Entra ID（旧Azure AD）とSampleConnect PlatformのSAML SSO連携で、属性マッピングの設定ミスによりログインに失敗するケースが複数報告されています。</p>"
            "<h2>よくあるミス</h2>"
            "<p>1. NameID形式の不一致: Entra ID側で「メールアドレス」を選択しているのに、SampleConnect側のNameID形式が「persistent」になっている</p>"
            "<p>2. 属性名の大文字小文字: Entra IDのクレーム名は大文字小文字を区別します。「emailaddress」と「EmailAddress」は別の属性として扱われます</p>"
            "<p>3. グループ属性の未設定: ロールマッピングを使う場合、Entra ID側の「グループクレーム」を有効にし忘れると、SampleConnect側でデフォルトロールが割り当てられます</p>"
            "<h2>確認手順</h2>"
            "<p>SampleConnect管理コンソールの「SSO設定」→「SAMLログ」から、直近のSAMLレスポンスを確認できます。Entra ID側の「エンタープライズアプリケーション」→「シングルサインオン」→「属性とクレーム」と突き合わせてください。</p>"
        ),
    },
]


def setup_confluence() -> None:
    print(f"=== Confluence セットアップ (スペース: {SPACE_KEY}) ===\n")

    existing_cql = f'type = page AND space = "{SPACE_KEY}"'
    encoded = urlencode({"cql": existing_cql, "limit": 50})
    existing = _request("GET", f"/wiki/rest/api/search?{encoded}")
    existing_titles = {
        item.get("content", {}).get("title", "")
        for item in existing.get("results", [])
    }

    space_resp = _request("GET", f"/wiki/api/v2/spaces?keys={SPACE_KEY}")
    spaces = space_resp.get("results", [])
    if not spaces:
        print(f"  エラー: スペース '{SPACE_KEY}' が見つかりません。")
        sys.exit(1)
    space_id = spaces[0]["id"]
    print(f"  スペースID: {space_id}")

    for page in CONFLUENCE_PAGES:
        title = page["title"]
        if title in existing_titles:
            print(f"  スキップ（既存）: {title}")
            continue

        payload = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {
                "representation": "storage",
                "value": page["body"],
            },
        }
        result = _request("POST", "/wiki/api/v2/pages", payload)
        page_id = result.get("id", "?")
        print(f"  作成: {title} (ID: {page_id})")

    print()


def verify_jira() -> None:
    print(f"=== Jira プロジェクト確認 (キー: {PROJECT_KEY}) ===\n")

    project = _request("GET", f"/rest/api/3/project/{PROJECT_KEY}")
    print(f"  プロジェクト名: {project['name']}")
    print(f"  プロジェクトキー: {project['key']}")
    print(f"  タイプ: {project.get('projectTypeKey', '?')}")

    issue_types = _request("GET", f"/rest/api/3/project/{PROJECT_KEY}/statuses")
    type_names = [it["name"] for it in issue_types]
    print(f"  課題タイプ: {', '.join(type_names)}")

    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
    if issue_type in type_names:
        print(f"  ✓ 課題タイプ '{issue_type}' は利用可能です")
    else:
        print(f"  ✗ 課題タイプ '{issue_type}' が見つかりません。利用可能: {', '.join(type_names)}")

    print()


def main() -> None:
    setup_confluence()
    verify_jira()
    print("=== 環境変数まとめ ===\n")
    print(f"  ATLASSIAN_BASE_URL={BASE_URL}")
    print(f"  CONFLUENCE_SPACE_KEY={SPACE_KEY}")
    print(f"  JIRA_PROJECT_KEY={PROJECT_KEY}")
    print(f"  JIRA_ISSUE_TYPE={os.environ.get('JIRA_ISSUE_TYPE', 'Task')}")
    print()


if __name__ == "__main__":
    main()
