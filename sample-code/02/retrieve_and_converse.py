#!/usr/bin/env python3
"""Retrieve + Converse APIを使ったスコープ制御付きRAGの実行スクリプト。

RetrieveAndGenerate APIと異なり、検索と生成を分離して制御します。
システムプロンプトで回答範囲を制御し、メタデータフィルターで検索対象を絞り込めます。

使い方:
    python retrieve_and_converse.py \
        --knowledge-base-id ABC123 \
        --model-id us.anthropic.claude-sonnet-4-6 \
        --question "SAML SSOでログインできない場合の確認項目を教えてください"

    # メタデータフィルター付き
    python retrieve_and_converse.py \
        --knowledge-base-id ABC123 \
        --model-id us.anthropic.claude-sonnet-4-6 \
        --question "サービスクレジットの申請期限はいつですか" \
        --filter '{"equals": {"key": "department", "value": "Billing"}}'
"""
from __future__ import annotations

import argparse
import json
import os
import textwrap
from typing import Any

import boto3


# ---------------------------------------------------------------------------
# デフォルトのシステムプロンプト（スコープ制御）
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """\
あなたは社内ドキュメントに基づいて回答するアシスタントです。
以下のルールを守ってください。

- 検索結果に根拠がある場合だけ回答してください。
- 根拠が見つからない場合は「お探しの情報は見つかりませんでした」と回答してください。
- 回答の最後に、根拠となったドキュメントの情報を示してください。
- 推測や一般的な知識での補完は行わないでください。"""


# 温度を受け付けないモデル。モデルIDと推論プロファイルARNのどちらでも末尾で判定する。
NO_TEMPERATURE_MODELS = (
    "openai.gpt-5.6-luna",
    "openai.gpt-6-sol",
    "openai.gpt-6.1-sol",
    "openai.gpt-6-luna",
    "anthropic.claude-sonnet-5",
    "anthropic.claude-sonnet-5-5",
    "anthropic.claude-opus-5",
    "anthropic.claude-opus-5-5",
    "anthropic.claude-haiku-5-5",
)


def inference_config(model_id: str) -> dict[str, Any]:
    """モデルが対応する推論パラメーターだけを返す。"""
    if model_id.endswith(NO_TEMPERATURE_MODELS):
        # 思考で消費するトークンも出力上限に含まれるため、上限を広げる。
        return {"maxTokens": 4096}
    return {"maxTokens": 2048, "temperature": 0}


# ---------------------------------------------------------------------------
# Retrieve API の呼び出し
# ---------------------------------------------------------------------------

def retrieve_chunks(
    client: Any,
    knowledge_base_id: str,
    query: str,
    num_results: int = 4,
    metadata_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Knowledge Basesから関連チャンクを検索する。"""
    retrieval_config: dict[str, Any] = {
        "vectorSearchConfiguration": {
            "numberOfResults": num_results,
        }
    }

    if metadata_filter:
        retrieval_config["vectorSearchConfiguration"]["filter"] = metadata_filter

    response = client.retrieve(
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration=retrieval_config,
    )

    return response.get("retrievalResults", [])


def format_chunks_for_prompt(chunks: list[dict[str, Any]]) -> str:
    """検索結果をConverse APIに渡すためのテキストに整形する。"""
    parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("content", {}).get("text", "")
        uri = _extract_uri(chunk)
        parts.append(f"[検索結果{i}] (出典: {uri})\n{text}")
    return "\n\n".join(parts)


def _extract_uri(result: dict[str, Any]) -> str:
    """検索結果からドキュメントのURIを取得する。"""
    location = result.get("location", {})
    for loc_type, key in [
        ("s3Location", "uri"),
        ("webLocation", "url"),
        ("confluenceLocation", "url"),
        ("salesforceLocation", "url"),
        ("sharePointLocation", "url"),
    ]:
        if loc_type in location:
            return location[loc_type].get(key, "")
    return ""


# ---------------------------------------------------------------------------
# Converse API の呼び出し
# ---------------------------------------------------------------------------

def generate_answer(
    client: Any,
    model_id: str,
    system_prompt: str,
    question: str,
    context: str,
) -> str:
    """検索結果をコンテキストとしてConverse APIで回答を生成する。"""
    user_message = f"""以下の検索結果を参考に、質問に回答してください。

{context}

質問: {question}"""

    response = client.converse(
        modelId=model_id,
        system=[{"text": system_prompt}],
        messages=[
            {"role": "user", "content": [{"text": user_message}]},
        ],
        inferenceConfig=inference_config(model_id),
    )

    content = response.get("output", {}).get("message", {}).get("content", [])
    parts = [block.get("text", "") for block in content if block.get("text")]
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrieve + Converse APIでスコープ制御付きRAGを実行する。"
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
        help="Knowledge Base ID（環境変数 KNOWLEDGE_BASE_ID でも指定可）。",
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("MODEL_ID"),
        help="Converse APIで使うモデルID（環境変数 MODEL_ID でも指定可）。",
    )
    parser.add_argument(
        "--question",
        required=True,
        help="Knowledge Basesに問い合わせる質問文。",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="回答範囲を制御するシステムプロンプト（省略時はデフォルトを使用）。",
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=4,
        help="検索で取得するチャンク数（デフォルト: 4）。",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="メタデータフィルター（JSON文字列）。",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWSリージョン（デフォルト: us-east-1）。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.knowledge_base_id:
        raise SystemExit(
            "--knowledge-base-id または環境変数 KNOWLEDGE_BASE_ID を指定してください。"
        )
    if not args.model_id:
        raise SystemExit(
            "--model-id または環境変数 MODEL_ID を指定してください。"
        )

    system_prompt = args.system_prompt or DEFAULT_SYSTEM_PROMPT

    # メタデータフィルターのパース
    metadata_filter = None
    if args.filter:
        try:
            metadata_filter = json.loads(args.filter)
        except json.JSONDecodeError as e:
            raise SystemExit(f"--filter のJSON形式が不正です: {e}")

    agent_runtime = boto3.client("bedrock-agent-runtime", region_name=args.region)
    bedrock_runtime = boto3.client("bedrock-runtime", region_name=args.region)

    # --- Step 1: 関連チャンクの検索 ---
    print("検索中...")
    chunks = retrieve_chunks(
        agent_runtime,
        args.knowledge_base_id,
        args.question,
        num_results=args.num_results,
        metadata_filter=metadata_filter,
    )

    if not chunks:
        print("検索結果が見つかりませんでした。")
        return

    print(f"{len(chunks)}件のチャンクを取得しました。\n")

    # --- Step 2: 検索結果の表示 ---
    print("取得したチャンク:")
    print("-" * 60)
    for i, chunk in enumerate(chunks, 1):
        uri = _extract_uri(chunk)
        score = chunk.get("score", 0)
        text = chunk.get("content", {}).get("text", "")
        preview = textwrap.shorten(text, width=120, placeholder="...")
        print(f"[{i}] スコア: {score:.4f} | {uri}")
        print(f"    {preview}")
    print("-" * 60)
    print()

    # --- Step 3: 回答の生成 ---
    context = format_chunks_for_prompt(chunks)
    answer = generate_answer(
        bedrock_runtime,
        args.model_id,
        system_prompt,
        args.question,
        context,
    )

    print("回答:")
    print(answer)


if __name__ == "__main__":
    main()
