#!/usr/bin/env python3
"""Probe the FM parser model on the architecture diagram multiple times.

Sends the architecture PDF directly to the specified Bedrock Sonnet model
with the same parsingPrompt the KB uses, and reports the extracted text.
Run several times to observe variance in the model's reading.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3

PARSING_PROMPT = (
    "このページには表・図・グラフが含まれる場合があります。"
    "表は列見出しと各セルの対応を保ったまま Markdown のテーブル形式で書き出してください。"
    "図やグラフは、主要な要素（ラベル・矢印の流れ・凡例・数値ラベル）を箇条書きで要約してください。"
    "本文の一般的な段落はそのまま抽出してください。"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", type=Path, default=Path("sample-pdfs/sampleconnect-architecture.pdf"))
    p.add_argument("--model-id", default="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--runs", type=int, default=3)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not args.pdf.exists():
        sys.exit(f"PDF not found: {args.pdf}")
    pdf_bytes = args.pdf.read_bytes()
    client = boto3.client("bedrock-runtime", region_name=args.region)
    for i in range(1, args.runs + 1):
        print(f"\n========== run {i}/{args.runs} ({args.model_id}) ==========")
        response = client.converse(
            modelId=args.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"document": {
                            "format": "pdf",
                            "name": "architecture",
                            "source": {"bytes": pdf_bytes},
                        }},
                        {"text": PARSING_PROMPT},
                    ],
                }
            ],
            inferenceConfig={"temperature": 0.0, "maxTokens": 2000},
        )
        for block in response["output"]["message"]["content"]:
            if "text" in block:
                print(block["text"])


if __name__ == "__main__":
    main()
