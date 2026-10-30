"""保存した検索結果を現在の質問セットで再採点する（AWS呼び出しなし）。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from aurora_kb import QUESTIONS, dump, retrieval_metrics


def rescore(rows, questions):
    by_id = {q["id"]: q for q in questions}
    updated = []
    for row in rows:
        q = by_id[row["id"]]
        if row["question"] != q["question"]:
            raise ValueError(f"{row['id']}: 質問文が変わっているため再検索が必要です")
        expected_filter = q.get("filter") if row["config"].endswith("_filter") else None
        if row["filter"] != expected_filter:
            raise ValueError(f"{row['id']}: フィルタが変わっているため再検索が必要です")
        results = [
            {"location": {"s3Location": {"uri": "/documents/" + s}}}
            for s in row["sources"]
        ]
        updated.append(
            {
                **row,
                "expected_sources": q["expected_sources"],
                **retrieval_metrics(results, q["expected_sources"]),
            }
        )
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path(".state"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.input_dir.resolve() == args.output_dir.resolve():
        parser.error("元の実測記録を残すため、別の出力先を指定してください")
    rows = json.loads((args.input_dir / "evaluation.json").read_text())
    questions = [
        json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()
    ]
    updated = rescore(rows, questions)
    summary = json.loads((args.input_dir / "evaluation-summary.json").read_text())
    summary["original_questions_sha256"] = summary["questions_sha256"]
    summary["questions_sha256"] = hashlib.sha256(QUESTIONS.read_bytes()).hexdigest()
    summary["rescored_at"] = datetime.now(timezone.utc).isoformat()
    summary["configs"] = {}
    for name in sorted({r["config"] for r in updated}):
        group = [r for r in updated if r["config"] == name]
        summary["configs"][name] = {
            "questions": len(group),
            **{
                metric: sum(r[key] for r in group) / len(group)
                for metric, key in [
                    ("hit_at_5", "hit"),
                    ("recall_at_5", "recall"),
                    ("mrr_at_5", "reciprocal_rank"),
                ]
            },
        }
    dump(args.output_dir / "evaluation.json", updated)
    dump(args.output_dir / "evaluation-summary.json", summary)
    print(json.dumps(summary["configs"], indent=2))


if __name__ == "__main__":
    main()
