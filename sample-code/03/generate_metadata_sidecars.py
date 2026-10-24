#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3

from common import (
    dump_json,
    generate_metadata_payload,
    guess_document_format,
    load_json,
    metadata_output_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Amazon Bedrock Knowledge Bases metadata sidecars with a model."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing source documents.",
    )
    parser.add_argument(
        "--schema-file",
        type=Path,
        default=Path("sample-code/03/data/metadata_schema.json"),
        help="JSON schema description for metadata generation.",
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("METADATA_MODEL_ID"),
        help="Bedrock model ID or inference profile for Converse.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory for generated sidecars. Defaults to the source folder.",
    )
    parser.add_argument(
        "--pattern",
        default="**/*",
        help="Glob pattern relative to --input-dir.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing metadata files.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS Region for the Bedrock runtime client.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.model_id:
        raise SystemExit(
            "--model-id is required. You can also set METADATA_MODEL_ID."
        )
    if not args.input_dir.is_dir():
        raise SystemExit(f"--input-dir must be an existing directory: {args.input_dir}")
    if not args.schema_file.is_file():
        raise SystemExit(f"--schema-file must be an existing file: {args.schema_file}")

    schema = load_json(args.schema_file)
    runtime_client = boto3.client("bedrock-runtime", region_name=args.region)

    processed = 0
    skipped = 0
    for path in sorted(args.input_dir.glob(args.pattern)):
        if not path.is_file():
            continue
        if path.name.endswith(".metadata.json"):
            skipped += 1
            continue

        document_format = guess_document_format(path)
        if document_format is None:
            skipped += 1
            continue

        output_path = metadata_output_path(path, args.output_dir)
        if output_path.exists() and not args.overwrite:
            skipped += 1
            continue

        payload = generate_metadata_payload(
            runtime_client=runtime_client,
            model_id=args.model_id,
            schema=schema,
            document_name=path.name,
            document_format=document_format,
            document_bytes=path.read_bytes(),
        )
        dump_json(output_path, payload)
        processed += 1
        print(f"generated {output_path}")

    print(f"processed={processed} skipped={skipped}")


if __name__ == "__main__":
    main()
