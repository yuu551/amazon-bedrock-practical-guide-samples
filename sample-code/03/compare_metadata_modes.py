#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

from common import (
    dump_json,
    generate_metadata_payload,
    guess_document_format,
    load_json,
    markdown_table,
    validate_metadata_payload,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Bedrock metadata generation with and without Structured Output."
        )
    )
    parser.add_argument(
        "--document",
        type=Path,
        required=True,
        help="Input document used for metadata generation.",
    )
    parser.add_argument(
        "--schema-file",
        type=Path,
        default=Path("sample-code/03/data/metadata_schema.json"),
        help="Metadata schema description.",
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("METADATA_MODEL_ID"),
        help="Bedrock model ID or inference profile for Converse.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS Region for the Bedrock runtime client.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sample-code/03/output/metadata-modes"),
        help="Directory for generated comparison artifacts.",
    )
    return parser.parse_args()


def summarize(rows: list[dict[str, object]]) -> str:
    table_rows: list[list[str]] = []
    for row in rows:
        table_rows.append(
            [
                str(row["mode"]),
                "ok" if row["error"] is None else "error",
                str(row["latency_ms"]),
                str(row["issue_count"]),
                str(row["artifact"]),
            ]
        )

    lines = [
        "# Metadata Generation Comparison",
        "",
        markdown_table(
            ["Mode", "Status", "Latency (ms)", "Schema Issues", "Artifact"],
            table_rows,
        ),
        "",
    ]

    for row in rows:
        lines.append(f"## {row['mode']}")
        if row["error"] is not None:
            lines.append("")
            lines.append(f"Error: {row['error']}")
            lines.append("")
            continue

        issues: list[str] = row["issues"]  # type: ignore[assignment]
        lines.append("")
        if issues:
            lines.append("Validation issues:")
            for issue in issues:
                lines.append(f"- {issue}")
        else:
            lines.append("Validation issues: none")
        lines.append("")

    lines.append("Use this report to capture chapter screenshots after replacing sample data.")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    if not args.model_id:
        raise SystemExit(
            "--model-id is required. You can also set METADATA_MODEL_ID."
        )
    if not args.document.is_file():
        raise SystemExit(f"--document must be an existing file: {args.document}")
    if not args.schema_file.is_file():
        raise SystemExit(f"--schema-file must be an existing file: {args.schema_file}")

    document_format = guess_document_format(args.document)
    if document_format is None:
        raise SystemExit(f"Unsupported document format: {args.document}")

    schema = load_json(args.schema_file)
    runtime_client = boto3.client("bedrock-runtime", region_name=args.region)
    run_dir = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for mode_name, use_structured_output in [
        ("prompt_only", False),
        ("structured_output", True),
    ]:
        started = time.perf_counter()
        error: str | None = None
        payload: dict[str, object] | None = None
        issues: list[str] = []
        artifact = run_dir / f"{mode_name}.json"

        try:
            payload = generate_metadata_payload(
                runtime_client=runtime_client,
                model_id=args.model_id,
                schema=schema,
                document_name=args.document.name,
                document_format=document_format,
                document_bytes=args.document.read_bytes(),
                use_structured_output=use_structured_output,
            )
            issues = validate_metadata_payload(schema, payload)
            dump_json(artifact, payload)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        rows.append(
            {
                "mode": mode_name,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "issue_count": len(issues),
                "issues": issues,
                "artifact": artifact,
                "error": error,
            }
        )

    summary_path = run_dir / "summary.md"
    summary_path.write_text(summarize(rows), encoding="utf-8")

    print(f"Wrote comparison summary to {summary_path}")
    for row in rows:
        if row["error"] is None:
            print(f"Wrote {row['mode']} payload to {row['artifact']}")


if __name__ == "__main__":
    main()
