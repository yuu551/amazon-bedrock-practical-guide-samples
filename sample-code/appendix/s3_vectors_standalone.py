#!/usr/bin/env python3
"""S3 Vectorsをスタンドアロンで操作するサンプルスクリプト。

ベクトルバケットの作成、埋め込みベクトルの登録、セマンティック検索、
アプリケーション側でのハイブリッド検索、メタデータフィルタリング、
RAG回答生成までの一連の流れを示します。
題材はSampleConnect PlatformサポートFAQで、第2章・第3章のサンプルデータと
題材をそろえています。

使い方:
    # セットアップ（バケット・インデックス作成 + ベクトル登録）
    python s3_vectors_standalone.py setup

    # 類似検索
    python s3_vectors_standalone.py query \
        --question "SAML SSOでログインできない場合の確認項目を教えてください"

    # ハイブリッド検索（S3 Vectors + 文字n-gram BM25 + RRF）
    python s3_vectors_standalone.py hybrid \
        --question "Enterpriseプランの監査ログは何日保存されますか"

    # メタデータフィルタ付き検索
    python s3_vectors_standalone.py query \
        --question "SSOログインできないときの確認ポイントを教えてください" \
        --filter '{"department": {"$eq": "Support"}}'

    # RAG回答生成（検索 + モデルで回答）
    python s3_vectors_standalone.py rag \
        --question "SAML SSOでログインできない場合の確認項目を教えてください" \
        --model-id us.anthropic.claude-sonnet-4-6

    # クリーンアップ（バケット・インデックス削除）
    python s3_vectors_standalone.py cleanup
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
import textwrap
import time
from typing import Any
import unicodedata

import boto3


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

VECTOR_BUCKET_NAME = os.environ.get(
    "VECTOR_BUCKET_NAME", "sampleconnect-support-faq-vectors"
)
INDEX_NAME = os.environ.get("INDEX_NAME", "support-faq-index")
DIMENSION = 1024
DISTANCE_METRIC = "cosine"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
REGION = os.environ.get("AWS_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# サンプルデータ
# ---------------------------------------------------------------------------

SAMPLE_DOCUMENTS = [
    {
        "key": "faq-saml-login-check",
        "text": (
            "SAML SSOでログインできない場合は、まずIdP側で対象ユーザーが"
            "SampleConnect Platform用のSAMLアプリに割り当てられているかを確認します。"
            "次に、SampleConnect管理コンソールに表示されるACS URLとEntity ID"
            "（SP EntityID）がIdP側の設定と一致しているかを確認します。"
            "あわせて、IdP証明書の有効期限が切れていないかも確認してください。"
            "失敗の詳細は管理コンソールの「SSO設定」→「SAMLログ」から、"
            "直近30件のSAMLレスポンスとエラー理由を参照できます。"
        ),
        "metadata": {
            "document_type": "faq",
            "department": "Support",
            "audience": "support",
            "category": "saml",
        },
    },
    {
        "key": "faq-api-rate-limit",
        "text": (
            "APIレート制限に達したリクエストは、HTTPステータス429を返します。"
            "レスポンスヘッダーには、再試行までの推奨待機秒数を示すRetry-After、"
            "現在のウィンドウでの残り回数を示すX-RateLimit-Remaining、"
            "ウィンドウのリセット時刻を示すX-RateLimit-Resetが含まれます。"
            "直近24時間のレート超過が累計1万回を超えた場合は、"
            "テナント単位で1時間の一時停止が自動適用されます。"
        ),
        "metadata": {
            "document_type": "faq",
            "department": "Product",
            "audience": "admin",
            "category": "api_rate_limit",
        },
    },
    {
        "key": "faq-sla-credit-deadline",
        "text": (
            "サービスクレジットの申請期限は、対象障害が発生した月の翌月末日"
            "（23:59 JST）までです。期限を過ぎた申請は受け付けられません。"
            "申請は、SampleConnectサポートポータルの「請求」→「サービスクレジット申請」"
            "フォーム、もしくはサポート窓口へのメールで受け付けます。"
            "申請受領後、5営業日以内にBilling部門が判定結果を返答します。"
        ),
        "metadata": {
            "document_type": "faq",
            "department": "Billing",
            "audience": "support",
            "category": "service_credit",
        },
    },
    {
        "key": "faq-audit-log-retention",
        "text": (
            "SampleConnect Platformの監査ログ保持期間は、プランによって異なります。"
            "Freeプランは7日間、Standardプランは90日間、"
            "Enterpriseプランは730日間（24ヶ月）です。"
            "保持期間を超えたログは自動的に削除されます。"
            "保持期間内のログは管理コンソールの「セキュリティ」→「監査ログ」から"
            "閲覧でき、長期保管が必要な場合は監査ログのエクスポート機能で"
            "CSVまたはJSON Lines形式に書き出せます。"
        ),
        "metadata": {
            "document_type": "faq",
            "department": "Security",
            "audience": "user",
            "category": "audit_log",
        },
    },
    {
        "key": "policy-sla-2026",
        "text": (
            "2026年4月適用版のSLAポリシーでは、Enterpriseプランの月間稼働率保証値を"
            "99.95%、Standardプランの月間稼働率保証値を99.9%と定めます。"
            "保証値を下回った場合のサービスクレジット率は、"
            "99.0%以上保証値未満で10%、95.0%以上99.0%未満で25%、"
            "95.0%未満で50%です。サービスクレジットは現金返金ではなく、"
            "翌月以降の請求から差し引きで反映します。"
        ),
        "metadata": {
            "document_type": "policy",
            "department": "Billing",
            "audience": "user",
            "category": "sla",
        },
    },
]


# ---------------------------------------------------------------------------
# クライアント初期化
# ---------------------------------------------------------------------------

def get_s3vectors_client() -> Any:
    return boto3.client("s3vectors", region_name=REGION)


def get_bedrock_runtime_client() -> Any:
    return boto3.client("bedrock-runtime", region_name=REGION)


# ---------------------------------------------------------------------------
# 埋め込みベクトルの生成
# ---------------------------------------------------------------------------

def embed_text(text: str) -> list[float]:
    """テキストをTitan Text Embeddings V2でベクトルに変換する。"""
    client = get_bedrock_runtime_client()
    response = client.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=json.dumps({
            "inputText": text,
            "dimensions": DIMENSION,
            "normalize": True,
        }),
    )
    return json.loads(response["body"].read())["embedding"]


# ---------------------------------------------------------------------------
# セットアップ: バケット・インデックス作成 + ベクトル登録
# ---------------------------------------------------------------------------

def setup() -> None:
    """ベクトルバケットとインデックスを作成し、サンプルデータを登録する。"""
    s3v = get_s3vectors_client()

    # ベクトルバケットの作成
    print(f"ベクトルバケット '{VECTOR_BUCKET_NAME}' を作成します...")
    s3v.create_vector_bucket(vectorBucketName=VECTOR_BUCKET_NAME)
    print("  作成完了")

    # ベクトルインデックスの作成（非フィルタリング対象メタデータにsource_textを指定）
    print(f"ベクトルインデックス '{INDEX_NAME}' を作成します...")
    s3v.create_index(
        vectorBucketName=VECTOR_BUCKET_NAME,
        indexName=INDEX_NAME,
        dataType="float32",
        dimension=DIMENSION,
        distanceMetric=DISTANCE_METRIC,
        metadataConfiguration={
            "nonFilterableMetadataKeys": ["source_text"],
        },
    )

    # インデックス作成完了の確認（S3 Vectors はステータスを返さないため、存在確認のみで十分）
    s3v.get_index(
        vectorBucketName=VECTOR_BUCKET_NAME,
        indexName=INDEX_NAME,
    )
    print("  作成完了")

    # ベクトルの登録
    print(f"サンプルデータ {len(SAMPLE_DOCUMENTS)} 件を登録します...")
    vectors = []
    for doc in SAMPLE_DOCUMENTS:
        print(f"  埋め込み生成中: {doc['key']}")
        embedding = embed_text(doc["text"])
        vectors.append({
            "key": doc["key"],
            "data": {"float32": embedding},
            "metadata": {
                **doc["metadata"],
                "source_text": doc["text"],
            },
        })

    s3v.put_vectors(
        vectorBucketName=VECTOR_BUCKET_NAME,
        indexName=INDEX_NAME,
        vectors=vectors,
    )
    print("  登録完了")
    print()
    print("セットアップが完了しました。")
    print(f"  バケット: {VECTOR_BUCKET_NAME}")
    print(f"  インデックス: {INDEX_NAME}")
    print(f"  ベクトル数: {len(vectors)}")


# ---------------------------------------------------------------------------
# クエリ: 類似検索
# ---------------------------------------------------------------------------

def query(
    question: str,
    filter_expr: dict | None = None,
    top_k: int = 3,
) -> list[dict]:
    """質問文でベクトル類似検索を行い、結果を返す。"""
    s3v = get_s3vectors_client()
    query_vector = embed_text(question)

    kwargs: dict[str, Any] = {
        "vectorBucketName": VECTOR_BUCKET_NAME,
        "indexName": INDEX_NAME,
        "queryVector": {"float32": query_vector},
        "topK": top_k,
        "returnMetadata": True,
        "returnDistance": True,
    }
    if filter_expr:
        kwargs["filter"] = filter_expr

    result = s3v.query_vectors(**kwargs)

    results = []
    for v in result.get("vectors", []):
        metadata = v.get("metadata", {})
        results.append({
            "key": v["key"],
            "distance": v.get("distance", 0),
            "source_text": metadata.pop("source_text", ""),
            "metadata": metadata,
        })
    return results


def print_query_results(results: list[dict]) -> None:
    """検索結果を表示する。"""
    if not results:
        print("検索結果がありません。")
        return

    print(f"検索結果: {len(results)} 件")
    print("-" * 60)
    for i, r in enumerate(results, 1):
        print(f"[{i}] キー: {r['key']}")
        print(f"    距離: {r['distance']:.4f}")
        print(f"    メタデータ: {r['metadata']}")
        if r["source_text"]:
            wrapped = textwrap.fill(r["source_text"], width=72)
            for line in wrapped.splitlines():
                print(f"    {line}")
        print()


# ---------------------------------------------------------------------------
# ハイブリッド検索: S3 Vectors + キーワード検索 + RRF
# ---------------------------------------------------------------------------

def character_ngrams(text: str, size: int = 3) -> list[str]:
    """日本語と英数字を同じ方法で扱える文字n-gramへ変換する。"""
    if size < 1:
        raise ValueError("sizeは1以上で指定してください。")

    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(char for char in normalized if char.isalnum())
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]
    return [normalized[i:i + size] for i in range(len(normalized) - size + 1)]


def keyword_search(
    question: str,
    top_k: int | None = None,
    documents: list[dict] | None = None,
) -> list[dict]:
    """サンプル文書を文字3-gramのBM25で検索する。

    S3 Vectorsにはキーワード検索用の転置インデックスがないため、
    この関数は別の検索経路を最小構成で再現します。本番環境では、同じ役割を
    OpenSearchなどのキーワード検索基盤に置き換えます。
    """
    docs = SAMPLE_DOCUMENTS if documents is None else documents
    query_terms = set(character_ngrams(question))
    if not docs or not query_terms:
        return []

    tokenized_docs = [character_ngrams(doc["text"]) for doc in docs]
    document_count = len(tokenized_docs)
    average_length = sum(map(len, tokenized_docs)) / document_count

    document_frequency: Counter[str] = Counter()
    for terms in tokenized_docs:
        document_frequency.update(set(terms))

    k1 = 1.5
    b = 0.75
    ranked: list[dict] = []
    for doc, terms in zip(docs, tokenized_docs, strict=True):
        term_frequency = Counter(terms)
        document_length = len(terms)
        score = 0.0
        for term in query_terms:
            frequency = term_frequency[term]
            if frequency == 0:
                continue
            frequency_in_docs = document_frequency[term]
            inverse_document_frequency = math.log(
                1 + (document_count - frequency_in_docs + 0.5)
                / (frequency_in_docs + 0.5)
            )
            denominator = frequency + k1 * (
                1 - b + b * document_length / average_length
            )
            score += inverse_document_frequency * frequency * (k1 + 1) / denominator

        if score > 0:
            ranked.append({
                "key": doc["key"],
                "keyword_score": score,
                "source_text": doc["text"],
                "metadata": dict(doc["metadata"]),
            })

    ranked.sort(key=lambda result: (-result["keyword_score"], result["key"]))
    return ranked if top_k is None else ranked[:top_k]


def reciprocal_rank_fusion(
    semantic_results: list[dict],
    keyword_results: list[dict],
    top_k: int = 3,
    rrf_k: int = 60,
) -> list[dict]:
    """2つの独立した検索順位をReciprocal Rank Fusionで統合する。"""
    if top_k < 1:
        raise ValueError("top_kは1以上で指定してください。")
    if rrf_k < 1:
        raise ValueError("rrf_kは1以上で指定してください。")

    fused_scores: defaultdict[str, float] = defaultdict(float)
    combined: dict[str, dict] = {}

    for rank, result in enumerate(semantic_results, 1):
        key = result["key"]
        fused_scores[key] += 1 / (rrf_k + rank)
        combined[key] = {
            "key": key,
            "source_text": result.get("source_text", ""),
            "metadata": dict(result.get("metadata", {})),
            "semantic_rank": rank,
            "semantic_distance": result.get("distance"),
            "keyword_rank": None,
            "keyword_score": None,
        }

    for rank, result in enumerate(keyword_results, 1):
        key = result["key"]
        fused_scores[key] += 1 / (rrf_k + rank)
        entry = combined.setdefault(key, {
            "key": key,
            "source_text": result.get("source_text", ""),
            "metadata": dict(result.get("metadata", {})),
            "semantic_rank": None,
            "semantic_distance": None,
            "keyword_rank": None,
            "keyword_score": None,
        })
        entry["keyword_rank"] = rank
        entry["keyword_score"] = result.get("keyword_score")
        if not entry["source_text"]:
            entry["source_text"] = result.get("source_text", "")
        if not entry["metadata"]:
            entry["metadata"] = dict(result.get("metadata", {}))

    for key, result in combined.items():
        result["rrf_score"] = fused_scores[key]

    return sorted(
        combined.values(),
        key=lambda result: (-result["rrf_score"], result["key"]),
    )[:top_k]


def hybrid_search(question: str, top_k: int = 3, rrf_k: int = 60) -> list[dict]:
    """S3 Vectorsの検索順位と独立したキーワード検索順位を統合する。"""
    candidate_count = max(top_k, len(SAMPLE_DOCUMENTS))
    semantic_results = query(question, top_k=candidate_count)
    keyword_results = keyword_search(question)
    return reciprocal_rank_fusion(
        semantic_results,
        keyword_results,
        top_k=top_k,
        rrf_k=rrf_k,
    )


def print_hybrid_results(results: list[dict]) -> None:
    """ハイブリッド検索結果と各検索経路の順位を表示する。"""
    if not results:
        print("検索結果がありません。")
        return

    print(f"ハイブリッド検索結果: {len(results)} 件")
    print("-" * 60)
    for i, result in enumerate(results, 1):
        semantic_rank = result["semantic_rank"] or "-"
        keyword_rank = result["keyword_rank"] or "-"
        print(f"[{i}] キー: {result['key']}")
        print(f"    RRFスコア: {result['rrf_score']:.6f}")
        print(f"    セマンティック順位: {semantic_rank}")
        print(f"    キーワード順位: {keyword_rank}")
        print(f"    メタデータ: {result['metadata']}")
        if result["source_text"]:
            wrapped = textwrap.fill(result["source_text"], width=72)
            for line in wrapped.splitlines():
                print(f"    {line}")
        print()


# ---------------------------------------------------------------------------
# RAG: 検索 + 回答生成
# ---------------------------------------------------------------------------

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


def rag(question: str, model_id: str, filter_expr: dict | None = None) -> None:
    """S3 Vectorsで検索し、結果をモデルに渡して回答を生成する。"""
    # 検索
    results = query(question, filter_expr=filter_expr)
    print_query_results(results)

    if not results:
        print("検索結果がないため、回答を生成できません。")
        return

    # 検索結果をコンテキストとして整形
    context_parts = []
    for i, r in enumerate(results, 1):
        context_parts.append(f"[{i}] {r['key']}\n{r['source_text']}")
    context = "\n\n".join(context_parts)

    # モデルに渡して回答を生成
    print("=" * 60)
    print("回答生成中...")
    print()

    bedrock = get_bedrock_runtime_client()
    response = bedrock.converse(
        modelId=model_id,
        system=[
            {
                "text": (
                    "あなたはSampleConnect PlatformサポートFAQ用の回答アシスタントです。"
                    "顧客サポート担当者を支援する立場で、SAML、APIキー、APIレート制限、"
                    "監査ログ、SLAなどの問い合わせに、社内ナレッジを根拠として回答します。"
                    "検索結果に根拠がある場合だけ回答してください。"
                    "検索結果に根拠がない質問には"
                    "「このシステムでは回答できません。サポート窓口で確認してください。」"
                    "と答えてください。"
                    "一般知識で補完しないでください。推測しないでください。"
                )
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"質問:\n{question}\n\n"
                            f"参照情報:\n{context}"
                        )
                    }
                ],
            }
        ],
        inferenceConfig=(
            # 新しいモデルは温度を受け付けず、思考の分も出力上限に含める
            {"maxTokens": 4096}
            if model_id.endswith(REASONING_MODELS)
            else {"maxTokens": 600, "temperature": 0.1}
        ),
    )

    # 思考ブロックが先頭に来るモデルもあるため、テキストだけをつなぐ
    answer = "".join(
        block.get("text", "") for block in response["output"]["message"]["content"]
    )
    print("回答:")
    print(answer)


# ---------------------------------------------------------------------------
# クリーンアップ: バケット・インデックス削除
# ---------------------------------------------------------------------------

def cleanup() -> None:
    """ベクトルインデックスとバケットを削除する。"""
    s3v = get_s3vectors_client()

    print(f"ベクトルインデックス '{INDEX_NAME}' を削除します...")
    try:
        s3v.delete_index(
            vectorBucketName=VECTOR_BUCKET_NAME,
            indexName=INDEX_NAME,
        )
        # インデックスの削除完了を待機
        while True:
            try:
                s3v.get_index(
                    vectorBucketName=VECTOR_BUCKET_NAME,
                    indexName=INDEX_NAME,
                )
                time.sleep(2)
            except s3v.exceptions.NotFoundException:
                break
        print("  削除完了")
    except s3v.exceptions.NotFoundException:
        print("  インデックスは存在しません（スキップ）")

    print(f"ベクトルバケット '{VECTOR_BUCKET_NAME}' を削除します...")
    try:
        s3v.delete_vector_bucket(vectorBucketName=VECTOR_BUCKET_NAME)
        print("  削除完了")
    except s3v.exceptions.NotFoundException:
        print("  バケットは存在しません（スキップ）")

    print("クリーンアップが完了しました。")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="S3 Vectorsをスタンドアロンで操作するサンプルスクリプト。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # setup
    subparsers.add_parser("setup", help="バケット・インデックス作成 + ベクトル登録")

    # query
    query_parser = subparsers.add_parser("query", help="類似検索")
    query_parser.add_argument(
        "--question", required=True, help="検索する質問文。"
    )
    query_parser.add_argument(
        "--filter", default=None,
        help=(
            "メタデータフィルタ（JSON文字列）。"
            "例: '{\"department\": {\"$eq\": \"Support\"}}' "
            "または '{\"document_type\": {\"$eq\": \"faq\"}}'"
        ),
    )
    query_parser.add_argument(
        "--top-k", type=int, default=3, help="返す結果数（デフォルト: 3）。"
    )

    # hybrid
    hybrid_parser = subparsers.add_parser(
        "hybrid",
        help="S3 Vectorsと文字n-gram BM25の検索順位をRRFで統合",
    )
    hybrid_parser.add_argument(
        "--question", required=True, help="検索する質問文。"
    )
    hybrid_parser.add_argument(
        "--top-k", type=int, default=3, help="返す結果数（デフォルト: 3）。"
    )
    hybrid_parser.add_argument(
        "--rrf-k",
        type=int,
        default=60,
        help="RRFの順位定数（デフォルト: 60）。",
    )

    # rag
    rag_parser = subparsers.add_parser("rag", help="検索 + モデルで回答生成")
    rag_parser.add_argument(
        "--question", required=True, help="質問文。"
    )
    rag_parser.add_argument(
        "--model-id",
        default=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        help="生成モデルのID（デフォルト: us.anthropic.claude-sonnet-4-6）。",
    )
    rag_parser.add_argument(
        "--filter", default=None,
        help="メタデータフィルタ（JSON文字列）。",
    )

    # cleanup
    subparsers.add_parser("cleanup", help="バケット・インデックス削除")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "setup":
        setup()
    elif args.command == "query":
        filter_expr = json.loads(args.filter) if args.filter else None
        results = query(args.question, filter_expr=filter_expr, top_k=args.top_k)
        print_query_results(results)
    elif args.command == "hybrid":
        results = hybrid_search(args.question, top_k=args.top_k, rrf_k=args.rrf_k)
        print_hybrid_results(results)
    elif args.command == "rag":
        filter_expr = json.loads(args.filter) if args.filter else None
        rag(args.question, model_id=args.model_id, filter_expr=filter_expr)
    elif args.command == "cleanup":
        cleanup()


if __name__ == "__main__":
    main()
