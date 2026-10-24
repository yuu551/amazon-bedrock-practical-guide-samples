#!/usr/bin/env python
from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import boto3

_secrets_client = boto3.client("secretsmanager")
_credentials_cache: dict[str, str] | None = None


def get_atlassian_credentials() -> dict[str, str]:
    """Atlassian認証情報をSecrets Managerから取得しコンテナ生存中はキャッシュする。"""
    global _credentials_cache
    if _credentials_cache is None:
        secret_id = os.environ["ATLASSIAN_SECRET_NAME"]
        response = _secrets_client.get_secret_value(SecretId=secret_id)
        _credentials_cache = json.loads(response["SecretString"])
    return _credentials_cache


def build_auth_header() -> str:
    creds = get_atlassian_credentials()
    token = base64.b64encode(
        f"{creds['email']}:{creds['apiToken']}".encode("utf-8")
    ).decode("utf-8")
    return f"Basic {token}"


def atlassian_request(*, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = os.environ["ATLASSIAN_BASE_URL"].rstrip("/")
    request = Request(
        f"{base_url}{path}",
        method=method,
        headers={
            "Authorization": build_auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_tool_name(context: Any) -> str:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None) if client_context else None
    visible_name = custom.get("bedrockAgentCoreToolName", "") if isinstance(custom, dict) else ""
    if "___" in visible_name:
        return visible_name.split("___", 1)[1]
    return visible_name


def search_confluence(event: dict[str, Any]) -> dict[str, Any]:
    space_key = os.environ["CONFLUENCE_SPACE_KEY"]
    query = str(event["query"]).strip().replace('"', '\\"')
    limit = int(event.get("limit", 5))
    cql = f'type = page AND space = "{space_key}" AND text ~ "{query}"'
    # /wiki/rest/api/search は excerpt を確実に返すので、
    # /content/search ではなくこちらを使う。
    encoded = urlencode({"cql": cql, "limit": limit})
    payload = atlassian_request(
        method="GET",
        path=f"/wiki/rest/api/search?{encoded}",
    )

    base_url = os.environ["ATLASSIAN_BASE_URL"].rstrip("/")
    results = []
    for item in payload.get("results", []):
        content = item.get("content", {})
        webui = item.get("url", "") or content.get("_links", {}).get("webui", "")
        url = webui if webui.startswith("http") else f"{base_url}/wiki{webui}"
        results.append(
            {
                "title": content.get("title", "") or item.get("title", ""),
                "url": url,
                "excerpt": item.get("excerpt", "") or "",
            }
        )

    return {
        "count": len(results),
        "results": results,
    }


def adf_paragraph(text: str) -> dict[str, Any]:
    return {
        "type": "paragraph",
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ],
    }


def create_jira_ticket(event: dict[str, Any]) -> dict[str, Any]:
    project_key = os.environ["JIRA_PROJECT_KEY"]
    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
    summary = str(event["summary"]).strip()
    description = str(event["description"]).strip()

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "description": {
            "type": "doc",
            "version": 1,
            "content": [adf_paragraph(description)],
        },
    }

    priority = str(event.get("priority", "")).strip()
    if priority:
        fields["priority"] = {"name": priority}

    payload = atlassian_request(
        method="POST",
        path="/rest/api/3/issue",
        payload={"fields": fields},
    )
    issue_key = payload["key"]
    browse_path = quote(issue_key)
    base_url = os.environ["ATLASSIAN_BASE_URL"].rstrip("/")
    return {
        "status": "created",
        "issueKey": issue_key,
        "issueUrl": f"{base_url}/browse/{browse_path}",
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    tool_name = extract_tool_name(context)
    if tool_name == "confluence_search":
        return search_confluence(event)
    if tool_name == "create_jira_ticket":
        return create_jira_ticket(event)
    raise ValueError(f"Unsupported tool: {tool_name}")
