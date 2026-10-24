#!/usr/bin/env python3
"""RetrieveAndGenerate APIを使った基本的なRAGの実行スクリプト。

Knowledge Basesに登録したドキュメントを検索し、基盤モデルで回答を生成します。
セッションIDを指定することで、会話のコンテキストを引き継いだ追加質問も可能です。

使い方:
    python retrieve_and_generate.py \
        --knowledge-base-id ABC123 \
        --model-arn arn:aws:bedrock:us-east-1::foundation-model/... \
        --question "SAML SSOでログインできない場合の確認項目を教えてください"
"""
from __future__ import annotations

import argparse
import os
import textwrap
from typing import Any

import boto3


# ---------------------------------------------------------------------------
# 出典情報の抽出
# ---------------------------------------------------------------------------

def extract_citations(response: dict[str, Any]) -> list[dict[str, str]]:
    """レスポンスから出典情報（URI + テキスト抜粋）を抽出する。"""
    citations: list[dict[str, str]] = []
    for citation_group in response.get("citations", []):
        for ref in citation_group.get("retrievedReferences", []):
            uri = _extract_uri(ref)
            snippet = ref.get("content", {}).get("text", "")
            citations.append({"uri": uri, "snippet": snippet})
    return citations


def _extract_uri(reference: dict[str, Any]) -> str:
    """参照元のURIを取得する。S3, Web, Confluenceなど複数のソースに対応。"""
    location = reference.get("location", {})
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


# 温度を受け付けず、適応的思考が既定で有効なモデル。
# モデルIDと推論プロファイルARNのどちらでも末尾で判定する。
REASONING_MODELS = (
    "anthropic.claude-sonnet-5",
    "anthropic.claude-sonnet-5-5",
    "anthropic.claude-opus-5",
    "anthropic.claude-opus-5-5",
    "anthropic.claude-haiku-5-5",
    "openai.gpt-6-sol",
    "openai.gpt-6.1-sol",
    "openai.gpt-6-luna",
)


def uses_reasoning_defaults(model_id: str) -> bool:
    return model_id.endswith(REASONING_MODELS)


def knowledge_base_prompts(model_id: str) -> dict[str, Any]:
    """新しいモデルのときだけ、RetrieveAndGenerateに渡す2つのプロンプトを返す。

    新しいモデルではKB既定のプロンプトが用意されていない場合がある。
    引用を返すため、AWSが展開する出力形式の指示も保持する。
    """
    if not uses_reasoning_defaults(model_id):
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


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RetrieveAndGenerate APIでKnowledge Basesに問い合わせる。"
    )
    parser.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID"),
        help="Knowledge Base ID（環境変数 KNOWLEDGE_BASE_ID でも指定可）。",
    )
    parser.add_argument(
        "--model-arn",
        default=os.environ.get("MODEL_ARN"),
        help="基盤モデルのARNまたは推論プロファイルARN（環境変数 MODEL_ARN でも指定可）。",
    )
    parser.add_argument(
        "--question",
        required=True,
        help="Knowledge Basesに問い合わせる質問文。",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="前回のセッションIDを指定すると会話を継続できる。",
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
    if not args.model_arn:
        raise SystemExit(
            "--model-arn または環境変数 MODEL_ARN を指定してください。"
        )

    client = boto3.client("bedrock-agent-runtime", region_name=args.region)

    # --- リクエストの組み立て ---
    request: dict[str, Any] = {
        "input": {"text": args.question},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": args.knowledge_base_id,
                "modelArn": args.model_arn,
                **knowledge_base_prompts(args.model_arn),
            },
        },
    }

    if args.session_id:
        request["sessionId"] = args.session_id

    # --- API呼び出し ---
    response = client.retrieve_and_generate(**request)

    # --- 結果の表示 ---
    answer = response.get("output", {}).get("text", "")
    session_id = response.get("sessionId", "")
    citations = extract_citations(response)

    print("回答:")
    print(answer)
    print()

    if citations:
        print("出典:")
        for i, cite in enumerate(citations, 1):
            print(f"[{i}] {cite['uri']}")
            if cite["snippet"]:
                # 長いスニペットは80文字で折り返して表示
                wrapped = textwrap.fill(cite["snippet"], width=80)
                for line in wrapped.splitlines():
                    print(f"    {line}")
        print()

    if session_id:
        print(f"セッションID: {session_id}")
        print("（--session-id に指定すると会話を継続できます）")


if __name__ == "__main__":
    main()
