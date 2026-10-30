#!/usr/bin/env python3
"""Convert documents to Markdown with the Amazon Bedrock Converse API."""

from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common import (
    converse_inference_config,
    extract_text_from_converse_response,
    guess_document_format,
    sanitize_document_name,
)


DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
EXTRACTION_PROMPT = """添付されたドキュメントをマークダウンへ変換してください。

- 見出し、段落、箇条書きの構造を保つ
- 表は、列見出しと各セルの対応を保ったマークダウン表にする
- 図やグラフは、ラベル、矢印の流れ、凡例、数値を箇条書きにする
- 数値、単位、固有名詞は原文どおりに残す
- 画像から読み取れた情報だけを記載し、推測で補完しない
- 前置きや説明、コードフェンスを付けず、変換した本文だけを返す
"""
MAX_THROTTLE_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
RETRYABLE_ERROR_CODES = {"ThrottlingException", "TooManyRequestsException"}


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert documents to Markdown with Amazon Bedrock."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("sample-pdfs"),
        help="Directory containing source documents.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/parsed-markdown"),
        help="Directory for generated Markdown files.",
    )
    parser.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        help="Bedrock model ID or inference profile for Converse.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing Markdown files.",
    )
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=1,
        help="Number of documents to convert in parallel (default: 1).",
    )
    return parser.parse_args()


def normalize_markdown(text: str) -> str:
    """Remove an optional outer Markdown fence and ensure a final newline."""
    stripped = text.strip()
    lines = stripped.splitlines()
    if (
        len(lines) >= 2
        and lines[0].strip().lower() in {"```", "```md", "```markdown"}
        and lines[-1].strip() == "```"
    ):
        stripped = "\n".join(lines[1:-1]).strip()
    return stripped + "\n"


def converse_with_retry(runtime_client: Any, **request: Any) -> dict[str, Any]:
    """Call Converse and retry throttling errors with exponential backoff."""
    for attempt in range(MAX_THROTTLE_RETRIES + 1):
        try:
            return runtime_client.converse(**request)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code not in RETRYABLE_ERROR_CODES or attempt == MAX_THROTTLE_RETRIES:
                raise
            wait_seconds = INITIAL_BACKOFF_SECONDS * (2**attempt)
            print(f"throttled ({code}); retrying in {wait_seconds:.0f}s")
            time.sleep(wait_seconds)
    raise AssertionError("unreachable")


def convert_and_save(
    runtime_client: Any,
    *,
    model_id: str,
    source_path: Path,
    output_path: Path,
    document_format: str,
) -> None:
    started = time.perf_counter()
    markdown, input_tokens, output_tokens = convert_document(
        runtime_client,
        model_id=model_id,
        source_path=source_path,
        document_format=document_format,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    elapsed_seconds = time.perf_counter() - started
    print(
        f"converted {source_path} -> {output_path} "
        f"elapsed={elapsed_seconds:.2f}s "
        f"input_tokens={input_tokens} output_tokens={output_tokens}"
    )


def convert_document(
    runtime_client: Any,
    *,
    model_id: str,
    source_path: Path,
    document_format: str,
) -> tuple[str, int, int]:
    response = converse_with_retry(
        runtime_client,
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "document": {
                            "format": document_format,
                            "name": sanitize_document_name(source_path.name),
                            "source": {"bytes": source_path.read_bytes()},
                        }
                    },
                    {"text": EXTRACTION_PROMPT},
                ],
            }
        ],
        inferenceConfig=converse_inference_config(model_id, 8192, 0.0),
    )
    markdown = extract_text_from_converse_response(response)
    if not markdown:
        raise RuntimeError(f"Model returned no text for {source_path}")

    usage = response.get("usage", {})
    return (
        normalize_markdown(markdown),
        int(usage.get("inputTokens", 0)),
        int(usage.get("outputTokens", 0)),
    )


def main() -> None:
    args = parse_args()
    if not args.input_dir.is_dir():
        raise SystemExit(f"--input-dir must be an existing directory: {args.input_dir}")

    pending: list[tuple[Path, Path, str]] = []
    skipped = 0
    for source_path in sorted(args.input_dir.rglob("*")):
        if not source_path.is_file():
            continue

        document_format = guess_document_format(source_path)
        if document_format is None:
            skipped += 1
            print(f"skipped unsupported {source_path}")
            continue

        relative_path = source_path.relative_to(args.input_dir).with_suffix(".md")
        output_path = args.output_dir / relative_path
        if output_path.exists() and not args.force:
            skipped += 1
            print(f"skipped existing {output_path}")
            continue
        pending.append((source_path, output_path, document_format))

    if not pending:
        print(f"processed=0 skipped={skipped}")
        return

    region = os.environ.get("AWS_REGION", "us-east-1")
    runtime_client = boto3.client("bedrock-runtime", region_name=region)

    def run(item: tuple[Path, Path, str]) -> None:
        source_path, output_path, document_format = item
        convert_and_save(
            runtime_client,
            model_id=args.model_id,
            source_path=source_path,
            output_path=output_path,
            document_format=document_format,
        )

    if args.workers == 1:
        for item in pending:
            run(item)
    else:
        # boto3 clients are thread-safe, so the workers share one client.
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(run, item) for item in pending]
            try:
                for future in futures:
                    future.result()
            except BaseException:
                # Stop documents that have not started yet, as the loop above does.
                for future in futures:
                    future.cancel()
                raise

    print(f"processed={len(pending)} skipped={skipped}")


if __name__ == "__main__":
    main()
