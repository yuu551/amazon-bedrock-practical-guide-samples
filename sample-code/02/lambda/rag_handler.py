"""RAG APIのLambdaハンドラー。

Lambda Function URLまたはAPI Gateway経由でHTTPリクエストを受け付け、
Knowledge Basesを使って質問に回答します。

環境変数:
    KNOWLEDGE_BASE_ID : Knowledge Base ID（必須）
    MODEL_ID          : 基盤モデルのモデルIDまたはARN（必須）
    AWS_REGION        : AWSリージョン（デフォルト: us-east-1）
    SYSTEM_PROMPT     : システムプロンプト（設定するとRetrieve + Converseパターンを使用）

レスポンス形式:
    {"answer": "...", "citations": [{"uri": "...", "snippet": "..."}]}
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
MODEL_ID = os.environ.get("MODEL_ID", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", "")

# NOTE: 検証用にすべてのオリジンを許可しています。
# 本番環境では特定のオリジン（例: "https://your-app.example.com"）に制限してください。
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)


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


def inference_config(model_id: str) -> dict[str, Any]:
    """モデルが対応する推論パラメーターだけを返す。"""
    if uses_reasoning_defaults(model_id):
        # 思考で消費するトークンも出力上限に含まれるため、上限を広げる。
        return {"maxTokens": 4096}
    config: dict[str, Any] = {"maxTokens": 2048}
    if "openai.gpt-5.6-luna" not in model_id:
        config["temperature"] = 0
    return config


# ---------------------------------------------------------------------------
# 出典情報の抽出ヘルパー
# ---------------------------------------------------------------------------

def _extract_uri(reference: dict[str, Any]) -> str:
    """参照元のURIを取得する。"""
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


# ---------------------------------------------------------------------------
# パターン1: RetrieveAndGenerate（シンプル）
# ---------------------------------------------------------------------------

def handle_retrieve_and_generate(
    question: str,
    metadata_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """RetrieveAndGenerate APIで質問に回答する。"""
    vector_search_config: dict[str, Any] = {}
    if metadata_filter:
        vector_search_config["filter"] = metadata_filter

    kb_config: dict[str, Any] = {
        "knowledgeBaseId": KNOWLEDGE_BASE_ID,
        # modelArn にはモデルID・ARN・推論プロファイルARNのいずれも指定可能
        "modelArn": MODEL_ID,
        **knowledge_base_prompts(MODEL_ID),
    }
    if vector_search_config:
        kb_config["retrievalConfiguration"] = {
            "vectorSearchConfiguration": vector_search_config,
        }

    response = agent_runtime.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": kb_config,
        },
    )

    answer = response.get("output", {}).get("text", "")
    citations = []
    for group in response.get("citations", []):
        for ref in group.get("retrievedReferences", []):
            citations.append({
                "uri": _extract_uri(ref),
                "snippet": ref.get("content", {}).get("text", ""),
            })

    return {"answer": answer, "citations": citations}


# ---------------------------------------------------------------------------
# パターン2: Retrieve + Converse（スコープ制御付き）
# ---------------------------------------------------------------------------

def handle_retrieve_and_converse(
    question: str,
    metadata_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieve APIで検索し、Converse APIで回答を生成する。"""
    # --- 検索 ---
    retrieval_config: dict[str, Any] = {
        "vectorSearchConfiguration": {
            "numberOfResults": 4,
        }
    }
    if metadata_filter:
        retrieval_config["vectorSearchConfiguration"]["filter"] = metadata_filter

    retrieve_response = agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration=retrieval_config,
    )

    chunks = retrieve_response.get("retrievalResults", [])

    # 検索結果をプロンプト用テキストに整形
    context_parts = []
    citations = []
    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("content", {}).get("text", "")
        uri = _extract_uri(chunk)
        context_parts.append(f"[検索結果{i}] (出典: {uri})\n{text}")
        citations.append({"uri": uri, "snippet": text})

    context = "\n\n".join(context_parts)

    # --- 回答生成 ---
    user_message = f"""以下の検索結果を参考に、質問に回答してください。

{context}

質問: {question}"""

    converse_response = bedrock_runtime.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {"role": "user", "content": [{"text": user_message}]},
        ],
        inferenceConfig=inference_config(MODEL_ID),
    )

    content = converse_response.get("output", {}).get("message", {}).get("content", [])
    answer_parts = [block.get("text", "") for block in content if block.get("text")]
    answer = "\n".join(answer_parts).strip()

    return {"answer": answer, "citations": citations}


# ---------------------------------------------------------------------------
# Lambda ハンドラー
# ---------------------------------------------------------------------------

def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda Function URL / API Gatewayからのリクエストを処理する。"""
    # CORS プリフライト
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        # リクエストボディのパース
        body_str = event.get("body", "{}")
        if event.get("isBase64Encoded"):
            import base64
            body_str = base64.b64decode(body_str).decode("utf-8")
        body = json.loads(body_str)

        question = body.get("question", "").strip()
        if not question:
            return {
                "statusCode": 400,
                "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
                "body": json.dumps(
                    {"error": "question フィールドは必須です。"},
                    ensure_ascii=False,
                ),
            }

        metadata_filter = body.get("filter")

        # SYSTEM_PROMPTの有無でパターンを切り替え
        if SYSTEM_PROMPT:
            result = handle_retrieve_and_converse(question, metadata_filter)
        else:
            result = handle_retrieve_and_generate(question, metadata_filter)

        return {
            "statusCode": 200,
            "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
            "body": json.dumps(result, ensure_ascii=False),
        }

    except Exception as e:
        # ログには詳細を出力し、クライアントには汎用メッセージのみ返す
        print(f"[ERROR] {type(e).__name__}: {e}")
        return {
            "statusCode": 500,
            "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "内部エラーが発生しました。"},
                ensure_ascii=False,
            ),
        }
