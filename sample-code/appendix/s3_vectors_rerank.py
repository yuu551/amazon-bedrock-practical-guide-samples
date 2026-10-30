#!/usr/bin/env python3
"""S3 Vectors版Knowledge Basesでリランキングが動作するかを検証するスクリプト。

セマンティック検索のみ（ベースライン）とCohere Rerank 3.5を併用した場合で、
同じ質問に対する検索結果の順序変化を比較します。

使い方:
    export KNOWLEDGE_BASE_ID=<S3 Vectors版KBのID>

    # デフォルト質問で実行
    python s3_vectors_rerank.py

    # 質問を指定して実行
    python s3_vectors_rerank.py \
        --question "サービスクレジットの申請期限はいつまでですか"

    # リランク候補数を変更
    python s3_vectors_rerank.py --reranked-results 5

前提:
    - S3 Vectors版Knowledge Baseが構築済み（setup-s3vectors.shで作成可能）
    - KBのサービスロールにbedrock:Rerankおよびbedrock:InvokeModel権限が付与済み
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import boto3


REGION = os.environ.get("AWS_REGION", "us-east-1")
KB_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
RERANK_MODEL_ARN = os.environ.get(
    "RERANK_MODEL_ARN",
    f"arn:aws:bedrock:{REGION}::foundation-model/cohere.rerank-v3-5:0",
)

QUESTIONS = [
    "Standardプランの月間稼働率の保証値は何%ですか",
    "監査ログの保持期間はプランによってどう違いますか",
    "SAML SSOでログインできない場合の確認項目を教えてください",
]


def retrieve(client, kb_id: str, question: str, num_results: int = 10,
             rerank: bool = False, rerank_model_arn: str = "",
             num_reranked: int = 3) -> dict:
    vector_search_config: dict = {"numberOfResults": num_results}

    if rerank:
        vector_search_config["rerankingConfiguration"] = {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": rerank_model_arn},
                "numberOfRerankedResults": num_reranked,
            },
        }

    start = time.perf_counter()
    response = client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": vector_search_config},
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {"results": response.get("retrievalResults", []), "latency_ms": elapsed_ms}


def format_uri(uri: str) -> str:
    """S3 URIからファイル名部分を抽出する。"""
    return uri.rsplit("/", 1)[-1] if "/" in uri else uri


def print_results(label: str, data: dict, show_text: bool = False) -> None:
    results = data["results"]
    latency = data["latency_ms"]
    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"  レイテンシ: {latency:.0f} ms  |  件数: {len(results)}")
    print(f"{'=' * 70}")

    for i, r in enumerate(results, 1):
        score = r.get("score", 0)
        uri = r.get("location", {}).get("s3Location", {}).get("uri", "")
        filename = format_uri(uri)
        text_snippet = ""
        if show_text:
            text_snippet = r.get("content", {}).get("text", "")[:120]
        print(f"  [{i}] score={score:.4f}  {filename}")
        if text_snippet:
            print(f"      {text_snippet}...")
    print()


def run_comparison(kb_id: str, question: str, num_results: int,
                   num_reranked: int, rerank_model_arn: str) -> dict:
    client = boto3.client("bedrock-agent-runtime", region_name=REGION)

    print(f"\n質問: {question}")
    print(f"一段目取得件数: {num_results}  |  リランク後件数: {num_reranked}")

    baseline = retrieve(client, kb_id, question, num_results=num_results)
    print_results("ベースライン（リランキングなし）", baseline, show_text=True)

    try:
        reranked = retrieve(
            client, kb_id, question,
            num_results=num_results, rerank=True,
            rerank_model_arn=rerank_model_arn,
            num_reranked=num_reranked,
        )
        print_results(f"Cohere Rerank 3.5（上位{num_reranked}件に絞り込み）",
                       reranked, show_text=True)
        rerank_status = "OK"
        rerank_error = None
    except Exception as e:
        rerank_status = type(e).__name__
        rerank_error = str(e)
        reranked = None
        print(f"\n  リランキング失敗: {rerank_status}")
        print(f"  {rerank_error[:200]}")

    baseline_order = [format_uri(r.get("location", {}).get("s3Location", {}).get("uri", ""))
                      for r in baseline["results"]]
    reranked_order = []
    if reranked:
        reranked_order = [format_uri(r.get("location", {}).get("s3Location", {}).get("uri", ""))
                          for r in reranked["results"]]

    order_changed = baseline_order[:num_reranked] != reranked_order if reranked else False

    print("-" * 70)
    print(f"  順序変化: {'あり' if order_changed else 'なし'}")
    if order_changed:
        print(f"  ベースライン上位{num_reranked}: {baseline_order[:num_reranked]}")
        print(f"  リランク後:       {reranked_order}")
    print()

    return {
        "question": question,
        "baseline_latency_ms": baseline["latency_ms"],
        "rerank_latency_ms": reranked["latency_ms"] if reranked else None,
        "rerank_status": rerank_status,
        "rerank_error": rerank_error,
        "order_changed": order_changed,
        "baseline_top": baseline_order[:num_reranked],
        "reranked_top": reranked_order,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S3 Vectors版KBでリランキングの動作を検証する。"
    )
    parser.add_argument(
        "--question", default=None,
        help="検証する質問（省略時はデフォルト3問を順に実行）。",
    )
    parser.add_argument(
        "--num-results", type=int, default=10,
        help="一段目の取得件数（デフォルト: 10）。",
    )
    parser.add_argument(
        "--reranked-results", type=int, default=3,
        help="リランク後に残す件数（デフォルト: 3）。",
    )
    args = parser.parse_args()

    if not KB_ID:
        print("エラー: KNOWLEDGE_BASE_ID 環境変数を設定してください。", file=sys.stderr)
        sys.exit(1)

    questions = [args.question] if args.question else QUESTIONS
    all_results = []

    print("=" * 70)
    print("  S3 Vectors × リランキング 検証")
    print(f"  KB ID: {KB_ID}  |  リージョン: {REGION}")
    print(f"  リランクモデル: {RERANK_MODEL_ARN.rsplit('/', 1)[-1]}")
    print("=" * 70)

    for q in questions:
        result = run_comparison(
            KB_ID, q,
            num_results=args.num_results,
            num_reranked=args.reranked_results,
            rerank_model_arn=RERANK_MODEL_ARN,
        )
        all_results.append(result)

    print("\n" + "=" * 70)
    print("  サマリ")
    print("=" * 70)
    for r in all_results:
        status = "OK" if r["rerank_status"] == "OK" else r["rerank_status"]
        latency_diff = ""
        if r["rerank_latency_ms"] is not None:
            diff = r["rerank_latency_ms"] - r["baseline_latency_ms"]
            latency_diff = f"  (+{diff:.0f}ms)" if diff > 0 else f"  ({diff:.0f}ms)"
        order_mark = " [順序変化あり]" if r["order_changed"] else ""
        print(f"  [{status}] {r['question'][:40]}...{latency_diff}{order_mark}")

    print()
    print(json.dumps(all_results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
