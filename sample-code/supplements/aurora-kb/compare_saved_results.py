"""同一文書・質問の保存ログを使い、3ストアを同じ検索指標で比較する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean, median
from urllib.parse import unquote

from aurora_kb import DATA, HERE, QUESTIONS, dump, retrieval_metrics

SAMPLE = HERE.parents[1]
ARCHIVE = (
    SAMPLE
    / "appendix/logs/20260825-answer-quality-comparison/s3vectors-opensearch/comparison-results.json"
)


def compare(archive, aurora_rows, questions):
    qs = {q["id"]: q for q in questions}
    selected = [r for r in archive["results"] if r["id"] in qs]
    if len(selected) != len(qs) or len({r["id"] for r in selected}) != len(qs):
        raise ValueError("比較元の質問が不足または重複しています")
    rows = []
    for row in selected:
        q = qs[row["id"]]
        if row["question"] != q["question"]:
            raise ValueError(f"{q['id']}: 質問文が異なります")
        for config, variant in row["variants"].items():
            sources = [
                unquote(r["uri"]).split("/sample-data/", 1)[1]
                for r in variant["results"]
            ]
            results = [
                {"location": {"s3Location": {"uri": "/documents/" + s}}}
                for s in sources
            ]
            rows.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "config": config,
                    "filter": None,
                    "expected_sources": q["expected_sources"],
                    "sources": sources,
                    "latency_ms": variant["latency_ms"],
                    **retrieval_metrics(results, q["expected_sources"]),
                }
            )
    for row in aurora_rows:
        if row["config"] not in ["semantic", "hybrid"]:
            continue
        q = qs[row["id"]]
        if row["question"] != q["question"] or row["filter"] is not None:
            raise ValueError(f"{q['id']}: Auroraの検索条件が異なります")
        if row["expected_sources"] != q["expected_sources"]:
            raise ValueError("Auroraの保存結果をrescore.pyで再採点してください")
        rows.append({**row, "config": "aurora_" + row["config"]})
    summary = {}
    for config in sorted({r["config"] for r in rows}):
        group = [r for r in rows if r["config"] == config]
        if len(group) != len(qs) or len({r["id"] for r in group}) != len(qs):
            raise ValueError(f"{config}: 質問の不足または重複があります")
        summary[config] = {
            "questions": len(group),
            "hit_at_5": mean(r["hit"] for r in group),
            "recall_at_5": mean(r["recall"] for r in group),
            "mrr_at_5": mean(r["reciprocal_rank"] for r in group),
            "median_retrieve_ms": median(r["latency_ms"] for r in group),
        }
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appendix-log", type=Path, default=ARCHIVE)
    parser.add_argument("--aurora-dir", type=Path, default=HERE / "results/2026-09-19")
    parser.add_argument("--output-dir", type=Path, default=HERE / "results/2026-09-19")
    args = parser.parse_args()
    archive = json.loads(args.appendix_log.read_text())
    # 当時のS3 ETag（小さい通常アップロード）と元ファイルのMD5を照合する。
    manifest = archive["corpus"]["manifest"]
    files = {
        p.relative_to(DATA).as_posix(): p
        for p in DATA.rglob("*")
        if p.is_file()
        and (p.name.endswith(".md") or p.name.endswith(".md.metadata.json"))
    }
    if set(files) != set(manifest):
        raise ValueError("文書・メタデータのファイル一覧が異なります")
    for rel, p in files.items():
        if hashlib.md5(p.read_bytes()).hexdigest() != manifest[rel]["etag"]:
            raise ValueError(f"文書・メタデータの内容が異なります: {rel}")
    questions = [
        json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()
    ]
    aurora_path = args.aurora_dir / "evaluation.json"
    rows, summary = compare(archive, json.loads(aurora_path.read_text()), questions)
    dump(args.output_dir / "cross-store-evaluation.json", rows)
    dump(
        args.output_dir / "cross-store-summary.json",
        {
            "questions_sha256": hashlib.sha256(QUESTIONS.read_bytes()).hexdigest(),
            "source_logs": {
                "opensearch_s3vectors": {
                    "file": args.appendix_log.name,
                    "sha256": hashlib.sha256(
                        args.appendix_log.read_bytes()
                    ).hexdigest(),
                    "evaluated_at": archive["timestamp"],
                },
                "aurora": {
                    "file": aurora_path.name,
                    "sha256": hashlib.sha256(aurora_path.read_bytes()).hexdigest(),
                    "evaluated_at": json.loads(
                        (args.aurora_dir / "evaluation-summary.json").read_text()
                    )["evaluated_at"],
                },
            },
            "matching_source_files": len(files),
            "documents": len(archive["corpus"]["documents"]),
            "corpus_sha256": {
                rel: hashlib.sha256(p.read_bytes()).hexdigest()
                for rel, p in files.items()
            },
            "region": archive["region"],
            "embedding_model": "amazon.titan-embed-text-v2:0",
            "dimensions": 1024,
            "number_of_results": 5,
            "filter": None,
            "reranking": False,
            "configs": summary,
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
