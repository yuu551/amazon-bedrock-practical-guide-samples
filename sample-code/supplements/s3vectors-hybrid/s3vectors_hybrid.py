#!/usr/bin/env python3
"""S3 Vectorsのハイブリッド検索を付録のKnowledge Baseで試すスクリプト。

付録の sample-code/02/infra/setup-s3vectors.sh で作った環境を使います。

    uv run s3vectors_hybrid.py kb-hybrid         # Knowledge BasesのRetrieveでHYBRIDを試す
    uv run s3vectors_hybrid.py enable            # チャンク本文に日本語の全文検索を追加
    uv run s3vectors_hybrid.py copy-standard     # 比較用に標準アナライザーのインデックスを作る
    uv run s3vectors_hybrid.py search --text "730日"           # 全文検索だけ
    uv run s3vectors_hybrid.py search --text "730日" --mode hybrid
    uv run s3vectors_hybrid.py query --question "429"            # 検索方式ごとの上位5件
    uv run s3vectors_hybrid.py rag --question "..."              # ハイブリッド検索の結果から回答を生成
    uv run s3vectors_hybrid.py keywords          # キーワードのヒット件数を比べる
    uv run s3vectors_hybrid.py evaluate          # 付録と同じ23問で採点
    uv run s3vectors_hybrid.py cleanup           # copy-standardで作ったインデックスを削除
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENV_FILE = ROOT / "02" / "infra" / ".env.s3vectors"
QUESTIONS = ROOT / "03" / "data" / "questions.jsonl"
RESULTS = HERE / "results"

# 付録の比較と同じ質問・採点関数と、付録で自作したハイブリッド検索の関数を使う
sys.path.insert(0, str(ROOT / "appendix"))
from managed_kb_comparison import HARD_CASES  # noqa: E402
from s3_vectors_standalone import REASONING_MODELS, keyword_search, reciprocal_rank_fusion  # noqa: E402
from s3vectors_opensearch_comparison import EXTRA_QUESTIONS, score_results  # noqa: E402

TEXT_KEY = "AMAZON_BEDROCK_TEXT"  # Knowledge Basesがチャンク本文を入れる欄
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
KEYWORD_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
KEYWORD_PROMPT = "次の質問を社内文書の全文検索にかけます。検索語として有効な名詞・固有名詞・数字・記号だけをスペース区切りで出力してください。説明は不要です。\n質問: "
ANSWER_MODEL_ID = "us.anthropic.claude-sonnet-4-6"  # 本編と同じ回答生成モデル
# 付録のs3_vectors_standalone.pyのragと同じシステムプロンプト
SYSTEM_PROMPT = (
    "あなたはSampleConnect PlatformサポートFAQ用の回答アシスタントです。"
    "顧客サポート担当者を支援する立場で、SAML、APIキー、APIレート制限、"
    "監査ログ、SLAなどの問い合わせに、社内ナレッジを根拠として回答します。"
    "検索結果に根拠がある場合だけ回答してください。"
    "検索結果に根拠がない質問には"
    "「このシステムでは回答できません。サポート窓口で確認してください。」"
    "と答えてください。"
    "一般知識で補完しないでください。推測しないでください。"
)
SAVED_LOGS = ROOT / "appendix" / "logs" / "20260825-answer-quality-comparison"
KEYWORDS = ["監査ログ", "保持期間", "エスカレーション", "サービスクレジット", "IPアドレス制限", "一次対応", "請求書払い", "申請"]


def load_env() -> dict[str, str]:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


ENV = load_env()
REGION = ENV.get("AWS_REGION", "us-east-1")
BUCKET = ENV["VECTOR_BUCKET"]
INDEX = ENV["VECTOR_INDEX"]
STANDARD_INDEX = f"{INDEX}-standard"
s3v = boto3.client("s3vectors", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
kb = boto3.client("bedrock-agent-runtime", region_name=REGION)


def text_search_status(index: str) -> list[dict[str, Any]]:
    got = s3v.get_index(vectorBucketName=BUCKET, indexName=index)["index"]
    return got.get("metadataConfiguration", {}).get("textSearchConfigurations", [])


def wait_active(index: str) -> dict[str, Any]:
    started = time.monotonic()
    history = []
    while True:
        fields = text_search_status(index)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        history.append({"time": now, "textSearchConfigurations": fields})
        print(now, json.dumps(fields, ensure_ascii=False), flush=True)
        if fields and all(f.get("status") == "ACTIVE" for f in fields):
            return {"seconds_to_active": round(time.monotonic() - started, 1), "history": history}
        time.sleep(15)


def enable(args: argparse.Namespace) -> None:
    index = s3v.get_index(vectorBucketName=BUCKET, indexName=INDEX)["index"]
    print("indexMode:", index.get("indexMode"))
    # 全文検索はENHANCEDのインデックスだけで使える
    if index.get("indexMode") != "ENHANCED":
        sys.exit("先に aws s3vectors update-index-mode で ENHANCED に切り替えてください")
    s3v.update_metadata_configuration(
        vectorBucketName=BUCKET,
        indexName=INDEX,
        add={"textSearchConfigurations": [{"metadataKey": TEXT_KEY, "language": args.language}]},
    )
    result = wait_active(INDEX)
    print("ACTIVEまで", result["seconds_to_active"], "秒")
    dump("enable.json", {"index": INDEX, "language": args.language, **result})


def all_vectors(index: str) -> list[dict[str, Any]]:
    vectors, token = [], None
    while True:
        page = s3v.list_vectors(
            vectorBucketName=BUCKET,
            indexName=index,
            returnData=True,
            returnMetadata=True,
            **({"nextToken": token} if token else {}),
        )
        vectors += page["vectors"]
        token = page.get("nextToken")
        if not token:
            return vectors


def copy_standard(args: argparse.Namespace) -> None:
    s3v.create_index(
        vectorBucketName=BUCKET,
        indexName=STANDARD_INDEX,
        dataType="float32",
        dimension=1024,
        distanceMetric="cosine",
        metadataConfiguration={
            "nonFilterableMetadataKeys": [TEXT_KEY, "AMAZON_BEDROCK_METADATA"],
            "textSearchConfigurations": [{"metadataKey": TEXT_KEY}],  # languageなし＝標準
        },
    )
    vectors = all_vectors(INDEX)
    for i in range(0, len(vectors), 500):
        s3v.put_vectors(vectorBucketName=BUCKET, indexName=STANDARD_INDEX, vectors=vectors[i : i + 500])
    print(f"{len(vectors)}件を{STANDARD_INDEX}へコピー")
    dump("copy-standard.json", {"index": STANDARD_INDEX, "vectors": len(vectors), **wait_active(STANDARD_INDEX)})


def embed(text: str) -> list[float]:
    body = json.dumps({"inputText": text, "dimensions": 1024, "normalize": True})
    res = bedrock.invoke_model(modelId=EMBED_MODEL_ID, body=body)
    return json.loads(res["body"].read())["embedding"]


def extract_keywords(question: str) -> str:
    # 全文検索には質問文より短い検索語を渡す
    res = bedrock.converse(
        modelId=KEYWORD_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": KEYWORD_PROMPT + question}]}],
        inferenceConfig={"maxTokens": 100, "temperature": 0},
    )
    return res["output"]["message"]["content"][0]["text"].strip()


def query(index: str, question: str, mode: str, vector: list[float] | None, top_k: int = 5) -> tuple[list[dict[str, Any]], float]:
    params: dict[str, Any] = {"vectorBucketName": BUCKET, "indexName": index, "topK": top_k, "returnMetadata": True, "returnScores": True}
    if mode in ("vector", "hybrid"):
        params["queryVector"] = {"float32": vector}
    if mode in ("text", "hybrid"):
        params["queryTexts"] = [{"text": question, "metadataKey": TEXT_KEY}]
    started = time.perf_counter()
    rows = s3v.query_vectors(**params)["vectors"]
    latency = round((time.perf_counter() - started) * 1000, 1)
    return [
        {
            "file": source_uri(row).rsplit("/", 1)[-1],
            "uri": source_uri(row),
            "scores": row.get("scores", {}),
            "text": row["metadata"][TEXT_KEY],
        }
        for row in rows
    ], latency


def source_uri(row: dict[str, Any]) -> str:
    # 出典はKnowledge BasesがJSON文字列で入れるAMAZON_BEDROCK_METADATAの中にある
    meta = json.loads(row["metadata"]["AMAZON_BEDROCK_METADATA"])
    return meta["source"]["sourceLocation"]


def handmade_hybrid(question: str, vector: list[float], chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """付録のs3_vectors_standalone.pyと同じ方法（文字3-gramのBM25＋RRF）で統合する。"""
    started = time.perf_counter()
    semantic, token = [], None
    while True:  # 付録と同じく全チャンクをベクトルの近い順に取る
        page = s3v.query_vectors(
            vectorBucketName=BUCKET, indexName=INDEX, topK=len(chunks),
            queryVector={"float32": vector}, returnMetadata=True,
            **({"nextToken": token} if token else {}),
        )
        semantic += [{"key": v["key"], "metadata": v["metadata"]} for v in page["vectors"]]
        token = page.get("nextToken")
        if not token:
            break
    # キーワード検索のために全チャンクの本文をアプリケーション側に持っておく必要がある
    docs = [{"key": c["key"], "text": c["metadata"][TEXT_KEY], "metadata": c["metadata"]} for c in chunks]
    fused = reciprocal_rank_fusion(semantic, keyword_search(question, documents=docs), top_k=5)
    latency = round((time.perf_counter() - started) * 1000, 1)
    return [
        {"file": source_uri(r).rsplit("/", 1)[-1], "uri": source_uri(r), "rrf_score": r["rrf_score"], "text": r["metadata"][TEXT_KEY]}
        for r in fused
    ], latency


def kb_retrieve(question: str, search_type: str) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    res = kb.retrieve(
        knowledgeBaseId=ENV["KB_ID"],
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5, "overrideSearchType": search_type}},
    )
    latency = round((time.perf_counter() - started) * 1000, 1)
    return [
        {
            "file": r["location"]["s3Location"]["uri"].rsplit("/", 1)[-1],
            "uri": r["location"]["s3Location"]["uri"],
            "score": r.get("score"),
            "text": r["content"]["text"],
        }
        for r in res["retrievalResults"]
    ], latency


def kb_hybrid(args: argparse.Namespace) -> None:
    """Knowledge BasesのRetrieveでSEMANTICとHYBRIDを指定し、結果かエラーを記録する。"""
    record = {"knowledge_base": "<Knowledge Base ID>", "question": args.question, "results": {}}
    for search_type in ("SEMANTIC", "HYBRID"):
        try:
            rows, ms = kb_retrieve(args.question, search_type)
            record["results"][search_type] = {"latency_ms": ms, "files": [r["file"] for r in rows]}
        except ClientError as e:
            record["results"][search_type] = {"error": e.response["Error"]}
        print(search_type, json.dumps(record["results"][search_type], ensure_ascii=False))
    # 回答生成までKnowledge Basesに任せるRetrieveAndGenerateも同じ検索設定を使う
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    model_arn = f"arn:aws:bedrock:{REGION}:{account}:inference-profile/{ANSWER_MODEL_ID}"
    try:
        res = kb.retrieve_and_generate(
            input={"text": args.question},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": ENV["KB_ID"],
                    "modelArn": model_arn,
                    "retrievalConfiguration": {
                        "vectorSearchConfiguration": {"numberOfResults": 5, "overrideSearchType": "HYBRID"}
                    },
                },
            },
        )
        record["results"]["RetrieveAndGenerate（HYBRID）"] = {"answer": res["output"]["text"]}
    except ClientError as e:
        record["results"]["RetrieveAndGenerate（HYBRID）"] = {"error": e.response["Error"]}
    print("RetrieveAndGenerate（HYBRID）", json.dumps(record["results"]["RetrieveAndGenerate（HYBRID）"], ensure_ascii=False))
    text = json.dumps(record, ensure_ascii=False).replace(ENV["KB_ID"], "<Knowledge Base ID>").replace(account, "<account-id>")
    dump("kb-hybrid.json", json.loads(text))


def rag(args: argparse.Namespace) -> None:
    """ハイブリッド検索で取った5チャンクをConverseに渡して回答を作る。"""
    vector = embed(args.question)
    keywords = extract_keywords(args.question)
    hits, _ = query(INDEX, keywords, "hybrid", vector)
    context = "\n\n".join(f"[{i}] {h['file']}\n{h['text']}" for i, h in enumerate(hits, 1))
    res = bedrock.converse(
        modelId=args.model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": f"質問:\n{args.question}\n\n参照情報:\n{context}"}]}],
        inferenceConfig=(
            {"maxTokens": 4096} if args.model_id.endswith(REASONING_MODELS)
            else {"maxTokens": 600, "temperature": 0.1}
        ),
    )
    # 思考ブロックが先頭に来るモデルもあるため、テキストだけをつなぐ
    answer = "".join(b.get("text", "") for b in res["output"]["message"]["content"])
    sources = [h["file"] for h in hits]
    print("検索語:", keywords)
    print("参照したチャンク:", json.dumps(sources, ensure_ascii=False))
    print("回答:")
    print(answer)
    dump("rag.json", {
        "question": args.question, "keywords": keywords, "model_id": args.model_id,
        "sources": [{"file": h["file"], "scores": h["scores"]} for h in hits],
        "answer": answer, "usage": res.get("usage"),
    })


def run_query(args: argparse.Namespace) -> None:
    vector = embed(args.question)
    for label, index, mode in VARIANTS:
        rows, ms = query(index, args.question, mode, vector)
        print(f"\n## {label}（{ms} ms）")
        for row in rows:
            print(row["file"], json.dumps(row["scores"]))


def search(args: argparse.Namespace) -> None:
    """1回のQueryVectorsのリクエストとレスポンスを表示して保存する。"""
    params: dict[str, Any] = {
        "vectorBucketName": BUCKET, "indexName": INDEX, "topK": args.top_k,
        "returnMetadata": True, "returnScores": True,
    }
    if args.mode in ("text", "hybrid"):
        params["queryTexts"] = [{"text": args.text, "metadataKey": TEXT_KEY}]
    if args.mode in ("vector", "hybrid"):
        params["queryVector"] = {"float32": embed(args.text)}
    started = time.perf_counter()
    res = s3v.query_vectors(**params)
    latency = round((time.perf_counter() - started) * 1000, 1)
    res.pop("ResponseMetadata", None)
    request = dict(params)
    if "queryVector" in request:  # 1024個の数値は表示しない
        request["queryVector"] = {"float32": f"<{len(params['queryVector']['float32'])}次元のベクトル>"}
    summary = {"mode": args.mode, "text": args.text, "latency_ms": latency}
    if args.mode == "text":
        summary["hits"] = len(text_hits(INDEX, args.text, 10000))  # topKで切らずに数える
    print("# QueryVectorsのリクエスト")
    print(json.dumps(request, ensure_ascii=False, indent=2))
    print("# 結果の概要")
    print(json.dumps(summary, ensure_ascii=False))
    print("# レスポンス（長い文字列は切り詰めて表示）")
    print(json.dumps(shorten(res), ensure_ascii=False, indent=2))
    dump(f"search-{args.mode}.json", {"request": request, **summary, "response": res})


def shorten(obj: Any, limit: int = 40) -> Any:
    # 表示用に長い本文やメタデータを切り詰める（保存するファイルは切り詰めない）
    if isinstance(obj, dict):
        return {k: shorten(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shorten(v, limit) for v in obj]
    if isinstance(obj, str) and len(obj) > limit:
        return obj[:limit] + "…"
    return obj


def text_hits(index: str, word: str, top_k: int) -> list[str]:
    # 1ページは100件までなのでnextTokenでたどる
    keys, token = [], None
    while True:
        page = s3v.query_vectors(
            vectorBucketName=BUCKET, indexName=index, topK=top_k,
            queryTexts=[{"text": word, "metadataKey": TEXT_KEY}],
            **({"nextToken": token} if token else {}),
        )
        keys += [v["key"] for v in page["vectors"]]
        token = page.get("nextToken")
        if not token:
            return keys


def keywords(args: argparse.Namespace) -> None:
    chunks = all_vectors(INDEX)
    report = []
    for word in KEYWORDS:
        expected = {c["key"] for c in chunks if word in c["metadata"][TEXT_KEY]}
        row = {"keyword": word, "contains": len(expected)}
        for label, index in (("ja", INDEX), ("standard", STANDARD_INDEX)):
            ranked = text_hits(index, word, len(chunks))
            keys = set(ranked)
            row[label] = {
                "hits": len(keys),
                "missed": len(expected - keys),
                # キーワードを含むチャンクの数だけ上位を取り、実際に含む件数を数える
                "top_n_contains": len(expected & set(ranked[: len(expected)])),
            }
        report.append(row)
        print(json.dumps(row, ensure_ascii=False))
    dump("keywords.json", {"chunks": len(chunks), "keywords": report})


VARIANTS = [
    ("ベクトルのみ", INDEX, "vector"),
    ("全文検索のみ（ja）", INDEX, "text"),
    ("ハイブリッド（ja）", INDEX, "hybrid"),
    ("全文検索のみ（標準）", STANDARD_INDEX, "text"),
    ("ハイブリッド（標準）", STANDARD_INDEX, "hybrid"),
]


def evaluate(args: argparse.Namespace) -> None:
    questions = [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]
    questions += EXTRA_QUESTIONS
    chunks = all_vectors(INDEX)
    rows = []
    totals: dict[str, dict[str, list]] = {}
    for q in questions:
        vector = embed(q["question"])
        variants = {}
        runs = [(label, lambda i=index, m=mode: query(i, q["question"], m, vector)) for label, index, mode in VARIANTS]
        q["keywords"] = extract_keywords(q["question"])
        runs.append(("ハイブリッド（ja・キーワード）", lambda: query(INDEX, q["keywords"], "hybrid", vector)))
        runs.append(("自作のハイブリッド（付録）", lambda: handmade_hybrid(q["question"], vector, chunks)))
        # KBのHYBRIDはS3 Vectorsでは ValidationException になるのでSEMANTICだけ比べる
        runs.append(("KB Retrieve（SEMANTIC）", lambda: kb_retrieve(q["question"], "SEMANTIC")))
        for label, run in runs:
            results, ms = run()
            scored = score_results(results, q["expected_sources"])
            scored["latency_ms"] = ms
            variants[label] = scored
            t = totals.setdefault(label, {"any": [], "all": [], "coverage": [], "latency": []})
            t["any"].append(scored["contains_any_expected_source"])
            t["all"].append(scored["contains_all_expected_sources"])
            t["coverage"].append(scored["expected_source_coverage"])
            t["latency"].append(ms)
        rows.append({"id": q["id"], "question": q["question"], "keywords": q["keywords"], "tags": q.get("tags", []), "expected_sources": q["expected_sources"], "variants": variants})
        print(q["id"], " ".join(f"{k}={v['expected_source_coverage']}" for k, v in variants.items()), flush=True)
    summary = {
        label: {
            "contains_any": f"{sum(t['any'])}/{len(t['any'])}",
            "contains_all": f"{sum(t['all'])}/{len(t['all'])}",
            "mean_coverage": round(statistics.mean(t["coverage"]), 3),
            "median_latency_ms": statistics.median(t["latency"]),
        }
        for label, t in totals.items()
    }
    summary.update(saved_summary(questions))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # Managed KBの付録で1位の文書を確かめた短い質問
    hard = []
    for case in HARD_CASES:
        vector = embed(case["question"])
        top = {label: query(index, case["question"], mode, vector)[0][0]["file"] for label, index, mode in VARIANTS}
        top["自作のハイブリッド（付録）"] = handmade_hybrid(case["question"], vector, chunks)[0][0]["file"]
        hard.append({**case, "top1": top})
        print(case["question"], json.dumps(top, ensure_ascii=False))
    dump("evaluate.json", {"summary": summary, "rows": rows, "hard_cases": hard})


def saved_summary(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """付録の保存済みログ（OpenSearch・Managed KB）を今の期待文書で採点し直す。"""
    expected = {q["id"]: q["expected_sources"] for q in questions}
    logs = {
        "OpenSearch（セマンティック・保存済み）": ("s3vectors-opensearch", lambda r: r["variants"]["opensearch_semantic"]),
        "OpenSearch（ハイブリッド・保存済み）": ("s3vectors-opensearch", lambda r: r["variants"]["opensearch_hybrid"]),
        "Managed KB（保存済み）": ("managed-opensearch-explicit", lambda r: r["managed"]),
    }
    out = {}
    for label, (name, pick) in logs.items():
        rows = json.loads((SAVED_LOGS / name / "comparison-results.json").read_text())["results"]
        scored = [score_results(pick(r)["results"], expected[r["id"]]) for r in rows]
        out[label] = {
            "contains_any": f"{sum(x['contains_any_expected_source'] for x in scored)}/{len(scored)}",
            "contains_all": f"{sum(x['contains_all_expected_sources'] for x in scored)}/{len(scored)}",
            "mean_coverage": round(statistics.mean(x["expected_source_coverage"] for x in scored), 3),
        }
    return out


def cleanup(args: argparse.Namespace) -> None:
    s3v.delete_index(vectorBucketName=BUCKET, indexName=STANDARD_INDEX)
    print("削除:", STANDARD_INDEX)


def dump(name: str, obj: Any) -> None:
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS / f"{stamp}-{name}"
    path.write_text(json.dumps({"run_at": stamp, "region": REGION, **obj}, ensure_ascii=False, indent=2))
    print("保存:", path.relative_to(HERE))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("enable")
    p.add_argument("--language", default="ja")
    sub.add_parser("copy-standard")
    p = sub.add_parser("kb-hybrid")
    p.add_argument("--question", default="APIエラーコード一覧")
    p = sub.add_parser("search")
    p.add_argument("--text", required=True)
    p.add_argument("--mode", choices=["text", "hybrid", "vector"], default="text")
    p.add_argument("--top-k", type=int, default=3)
    p = sub.add_parser("rag")
    p.add_argument("--question", required=True)
    p.add_argument("--model-id", default=ANSWER_MODEL_ID)
    p = sub.add_parser("query")
    p.add_argument("--question", required=True)
    sub.add_parser("keywords")
    sub.add_parser("evaluate")
    sub.add_parser("cleanup")
    args = parser.parse_args()
    {
        "enable": enable,
        "copy-standard": copy_standard,
        "kb-hybrid": kb_hybrid,
        "search": search,
        "rag": rag,
        "query": run_query,
        "keywords": keywords,
        "evaluate": evaluate,
        "cleanup": cleanup,
    }[args.command](args)


if __name__ == "__main__":
    main()
