#!/usr/bin/env python3
"""略語・同義語辞書を扱う2案のデモ実装。

- normalize_with_llm: 軽量モデルで質問を正式名称へ書き換える前段の正規化関数
- call_rag_with_glossary: RetrieveAndGenerate の promptTemplate に辞書を埋め込み
  生成側で意味を補う形のRAG呼び出し関数

KNOWLEDGE_BASE_ID、RAG_MODEL_ARN、AWS_REGIONを環境変数で設定すれば
`python glossary_demo.py` で前段正規化とpromptTemplate埋め込みRAGの両方を試せる。
"""
from __future__ import annotations

import os
from typing import Any

import boto3

from common import knowledge_base_prompts

NORMALIZATION_PROMPT = """
ユーザーの質問を、SampleConnect Platformサポートのナレッジ検索向けに正式名称へ書き直してください。
- 語の置換にとどめ、質問の意図や条件は変えない
- 辞書に該当しない語はそのまま残す

辞書:
- SSO → SAML SSO
- MFA → 多要素認証
- レート制限 → APIレート制限
- 監査ログ取り出し → 監査ログのエクスポート
- クレジット → サービスクレジット
"""


PROMPT_TEMPLATE = """あなたはSampleConnect Platformのサポート担当です。
以下の検索結果と用語辞書を参考に、ユーザーの質問へ回答してください。

用語辞書:
- SSO は SAML SSO を指します
- MFA は 多要素認証 を指します
- レート制限 は APIレート制限 を指します
- クレジット は サービスクレジット を指します

検索結果:
$search_results$

ユーザーの質問:
$query$

検索結果に該当する情報がない場合は、推測で答えず、その旨を回答してください。
"""


def normalize_with_llm(bedrock_runtime: Any, question: str) -> str:
    response = bedrock_runtime.converse(
        modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        system=[{"text": NORMALIZATION_PROMPT}],
        messages=[{"role": "user", "content": [{"text": question}]}],
    )
    return response["output"]["message"]["content"][0]["text"]


def call_rag_with_glossary(
    client: Any,
    kb_id: str,
    model_arn: str,
    question: str,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "knowledgeBaseId": kb_id,
        "modelArn": model_arn,
        "generationConfiguration": {
            "promptTemplate": {
                "textPromptTemplate": PROMPT_TEMPLATE,
            },
        },
    }
    # 新しいモデルには検索クエリ側のプロンプトと出力形式の指示も渡す
    prompts = knowledge_base_prompts(model_arn)
    if prompts:
        config["orchestrationConfiguration"] = prompts["orchestrationConfiguration"]
        config["generationConfiguration"] = {
            **prompts["generationConfiguration"],
            "promptTemplate": {
                "textPromptTemplate": PROMPT_TEMPLATE + "\n$output_format_instructions$\n",
            },
        }
    return client.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": config,
        },
    )


def main() -> None:
    region = os.environ.get("AWS_REGION", "us-east-1")
    kb_id = os.environ.get("KNOWLEDGE_BASE_ID")
    model_arn = os.environ.get("RAG_MODEL_ARN")
    question = os.environ.get(
        "QUESTION",
        "SSOでログインできないお客様の確認項目を教えてください",
    )

    bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)
    print(f"質問: {question}")

    normalized = normalize_with_llm(bedrock_runtime, question)
    print(f"正規化後: {normalized}")

    if not kb_id or not model_arn:
        print(
            "KNOWLEDGE_BASE_ID または RAG_MODEL_ARN が未設定のため、"
            "RetrieveAndGenerate 呼び出しはスキップします。"
        )
        return

    bedrock_agent_runtime = boto3.client(
        "bedrock-agent-runtime", region_name=region
    )
    response = call_rag_with_glossary(
        bedrock_agent_runtime,
        kb_id=kb_id,
        model_arn=model_arn,
        question=question,
    )
    print("\npromptTemplate埋め込み版のRAG応答:")
    print(response.get("output", {}).get("text", ""))


if __name__ == "__main__":
    main()
