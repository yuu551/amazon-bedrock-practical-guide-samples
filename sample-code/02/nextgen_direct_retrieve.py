#!/usr/bin/env python3
"""同期済みのOpenSearch Serverless NextGenインデックスを直接検索する。

Knowledge BasesのRetrieve APIを使わず、Titan Text Embeddings V2で質問を
ベクトル化してOpenSearch APIへ直接送る診断用サンプルです。セマンティック検索と、
キーワード検索を検索パイプラインで統合するハイブリッド検索に対応します。

このスクリプトは文書の取り込みを行いません。既に同期済みのNextGenインデックス、
フィールド名、OpenSearch Serverlessのアクセス権限が必要です。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import quote, urlparse

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection


DEFAULT_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
DEFAULT_VECTOR_FIELD = "bedrock-knowledge-base-default-vector"
DEFAULT_TEXT_FIELD = "AMAZON_BEDROCK_TEXT_CHUNK"
DEFAULT_METADATA_FIELD = "AMAZON_BEDROCK_METADATA"
DEFAULT_SEARCH_PIPELINE = "nextgen-hybrid-search"


def endpoint_host(endpoint: str) -> str:
    """OpenSearch Serverlessのエンドポイントからホスト名を取り出す。"""
    value = endpoint.strip()
    if not value:
        raise ValueError("エンドポイントが空です。")
    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("OpenSearch ServerlessのHTTPSエンドポイントを指定してください。")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("エンドポイントにはパス、クエリ、フラグメントを含めないでください。")
    return parsed.hostname


def create_opensearch_client(
    endpoint: str,
    region: str,
    session: boto3.Session,
) -> OpenSearch:
    """一時認証情報を使って署名するOpenSearchクライアントを作る。"""
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS認証情報を取得できませんでした。")

    return OpenSearch(
        hosts=[{"host": endpoint_host(endpoint), "port": 443}],
        http_auth=AWSV4SignerAuth(credentials, region, "aoss"),
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=60,
    )


def embed_question(
    client: Any,
    question: str,
    model_id: str,
    dimensions: int,
) -> list[float]:
    """質問文をTitan Text Embeddings V2でベクトル化する。"""
    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(
            {
                "inputText": question,
                "dimensions": dimensions,
                "normalize": True,
            }
        ),
    )
    payload = json.loads(response["body"].read())
    embedding = payload.get("embedding")
    if not isinstance(embedding, list) or len(embedding) != dimensions:
        raise RuntimeError(
            f"埋め込みベクトルを取得できませんでした（期待次元: {dimensions}）。"
        )
    return embedding


def source_fields(text_field: str, metadata_field: str) -> list[str]:
    """検索結果として取得するフィールド名を返す。"""
    return list(dict.fromkeys([text_field, metadata_field]))


def semantic_query(
    embedding: list[float],
    vector_field: str,
    text_field: str,
    metadata_field: str,
    top_k: int,
    candidate_count: int,
) -> dict[str, Any]:
    """k-NN検索のリクエスト本文を作る。"""
    return {
        "size": top_k,
        "_source": source_fields(text_field, metadata_field),
        "query": {
            "knn": {
                vector_field: {
                    "vector": embedding,
                    "k": candidate_count,
                }
            }
        },
    }


def hybrid_query(
    question: str,
    embedding: list[float],
    vector_field: str,
    text_field: str,
    metadata_field: str,
    top_k: int,
    candidate_count: int,
) -> dict[str, Any]:
    """キーワード検索とk-NN検索を統合するリクエスト本文を作る。"""
    return {
        "size": top_k,
        "_source": source_fields(text_field, metadata_field),
        "query": {
            "hybrid": {
                "queries": [
                    {"match": {text_field: {"query": question}}},
                    {
                        "knn": {
                            vector_field: {
                                "vector": embedding,
                                "k": candidate_count,
                            }
                        }
                    },
                ]
            }
        },
    }


def search_pipeline_body(keyword_weight: float) -> dict[str, Any]:
    """2種類の検索スコアを正規化して重み付き平均する設定を作る。"""
    if not 0 < keyword_weight < 1:
        raise ValueError("キーワード検索の重みは0より大きく1より小さくしてください。")

    return {
        "description": "Normalize and combine keyword and vector search scores",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {
                        "technique": "arithmetic_mean",
                        "parameters": {
                            "weights": [keyword_weight, 1 - keyword_weight]
                        },
                    },
                }
            }
        ],
    }


def put_search_pipeline(
    client: OpenSearch,
    pipeline_name: str,
    keyword_weight: float,
) -> None:
    """明示指定された検索パイプラインを作成または更新する。"""
    path = f"/_search/pipeline/{quote(pipeline_name, safe='')}"
    client.transport.perform_request(
        "PUT",
        path,
        body=search_pipeline_body(keyword_weight),
    )


def normalize_hits(
    response: dict[str, Any],
    text_field: str,
    metadata_field: str,
) -> list[dict[str, Any]]:
    """OpenSearchのレスポンスから表示に必要な項目だけを取り出す。"""
    results: list[dict[str, Any]] = []
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        metadata = source.get(metadata_field)
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                pass
        results.append(
            {
                "id": hit.get("_id", ""),
                "score": hit.get("_score"),
                "text": source.get(text_field, ""),
                "metadata": metadata,
            }
        )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="同期済みNextGenインデックスをOpenSearch APIで直接検索する。"
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("AOSS_ENDPOINT"),
        help="NextGenコレクションのエンドポイント（環境変数AOSS_ENDPOINTでも指定可）。",
    )
    parser.add_argument(
        "--index",
        default=os.environ.get("AOSS_INDEX_NAME"),
        help="同期済みインデックス名（環境変数AOSS_INDEX_NAMEでも指定可）。",
    )
    parser.add_argument("--question", required=True, help="検索する質問文。")
    parser.add_argument(
        "--search-type",
        choices=["semantic", "hybrid"],
        default="semantic",
        help="検索方式（デフォルト: semantic）。",
    )
    parser.add_argument(
        "--search-pipeline",
        default=os.environ.get("AOSS_SEARCH_PIPELINE", DEFAULT_SEARCH_PIPELINE),
        help="ハイブリッド検索で使う検索パイプライン名。",
    )
    parser.add_argument(
        "--create-search-pipeline",
        action="store_true",
        help="指定した検索パイプラインを実行前に作成または更新する。",
    )
    parser.add_argument(
        "--keyword-weight",
        type=float,
        default=0.3,
        help="ハイブリッド検索におけるキーワード検索の重み（デフォルト: 0.3）。",
    )
    parser.add_argument("--top-k", type=int, default=4, help="返す件数。")
    parser.add_argument(
        "--candidate-count",
        type=int,
        default=50,
        help="k-NN検索で比較する候補数。",
    )
    parser.add_argument(
        "--vector-field",
        default=os.environ.get("AOSS_VECTOR_FIELD", DEFAULT_VECTOR_FIELD),
        help="ベクトルフィールド名。",
    )
    parser.add_argument(
        "--text-field",
        default=os.environ.get("AOSS_TEXT_FIELD", DEFAULT_TEXT_FIELD),
        help="本文フィールド名。",
    )
    parser.add_argument(
        "--metadata-field",
        default=os.environ.get("AOSS_METADATA_FIELD", DEFAULT_METADATA_FIELD),
        help="メタデータフィールド名。",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="質問のベクトル化に使う埋め込みモデルID。",
    )
    parser.add_argument("--dimensions", type=int, default=1024, help="ベクトルの次元数。")
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWSリージョン（デフォルト: us-east-1）。",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.endpoint:
        raise SystemExit("--endpointまたは環境変数AOSS_ENDPOINTを指定してください。")
    if not args.index:
        raise SystemExit("--indexまたは環境変数AOSS_INDEX_NAMEを指定してください。")
    if args.top_k < 1:
        raise SystemExit("--top-kは1以上にしてください。")
    if args.candidate_count < args.top_k:
        raise SystemExit("--candidate-countは--top-k以上にしてください。")
    if args.create_search_pipeline and args.search_type != "hybrid":
        raise SystemExit("--create-search-pipelineはhybrid検索でのみ指定できます。")
    if args.search_type == "hybrid" and not 0 < args.keyword_weight < 1:
        raise SystemExit("--keyword-weightは0より大きく1より小さくしてください。")


def main() -> None:
    args = parse_args()
    validate_args(args)

    session = boto3.Session(region_name=args.region)
    bedrock_runtime = session.client("bedrock-runtime")
    opensearch = create_opensearch_client(args.endpoint, args.region, session)

    if args.create_search_pipeline:
        put_search_pipeline(opensearch, args.search_pipeline, args.keyword_weight)
        print(
            f"検索パイプラインを作成または更新しました: {args.search_pipeline}",
            file=sys.stderr,
        )

    embedding = embed_question(
        bedrock_runtime,
        args.question,
        args.embedding_model,
        args.dimensions,
    )

    if args.search_type == "hybrid":
        body = hybrid_query(
            args.question,
            embedding,
            args.vector_field,
            args.text_field,
            args.metadata_field,
            args.top_k,
            args.candidate_count,
        )
        response = opensearch.search(
            index=args.index,
            body=body,
            params={"search_pipeline": args.search_pipeline},
        )
    else:
        body = semantic_query(
            embedding,
            args.vector_field,
            args.text_field,
            args.metadata_field,
            args.top_k,
            args.candidate_count,
        )
        response = opensearch.search(index=args.index, body=body)

    print(
        json.dumps(
            {
                "question": args.question,
                "searchType": args.search_type,
                "results": normalize_hits(
                    response,
                    args.text_field,
                    args.metadata_field,
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
