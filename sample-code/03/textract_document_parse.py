#!/usr/bin/env python3
"""Amazon TextractでPDFをマークダウンへ変換する。"""

from __future__ import annotations

import argparse
import io
import re
import time
from pathlib import Path
from typing import Any

import boto3
from pypdf import PdfReader, PdfWriter

DEFAULT_REGION = "ap-northeast-1"
# 東京リージョンの1ページあたりの単価（100万ページまで、2026年11月時点）
PRICE_PER_PAGE = {"text": 0.0015, "layout": 0.015}
SKIP_LAYOUT = {"LAYOUT_HEADER", "LAYOUT_FOOTER", "LAYOUT_PAGE_NUMBER"}

CJK = r"　-ヿ㐀-鿿＀-￯"
SPACE_AROUND_CJK = re.compile(rf"(?<=[{CJK}])\s+|\s+(?=[{CJK}])")


def tidy(text: str) -> str:
    """日本語の前後に入る空白を詰める。英数字どうしの空白は残す。"""
    return SPACE_AROUND_CJK.sub("", text).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Amazon Textractで文書をマークダウンへ変換する")
    parser.add_argument("--input-dir", type=Path, default=Path("sample-pdfs"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/textract-markdown"))
    parser.add_argument("--mode", choices=["layout", "text"], default="layout",
                        help="layout: 表とレイアウトも分析する / text: 文字だけを検出する")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--force", action="store_true", help="出力済みのファイルも変換し直す")
    return parser.parse_args()


def split_pages(path: Path) -> list[bytes]:
    """同期APIは1ページずつ受け付けるので、PDFをページごとのPDFに分ける。"""
    if path.suffix.lower() != ".pdf":
        return [path.read_bytes()]
    pages = []
    for page in PdfReader(path).pages:
        writer = PdfWriter()
        writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        pages.append(buffer.getvalue())
    return pages


def line_text(block: dict[str, Any], blocks: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for rel in block.get("Relationships", []):
        if rel["Type"] == "CHILD":
            lines += [blocks[i]["Text"] for i in rel["Ids"] if blocks[i]["BlockType"] == "LINE"]
    return lines


def overlap(a: dict[str, float], b: dict[str, float]) -> float:
    w = min(a["Left"] + a["Width"], b["Left"] + b["Width"]) - max(a["Left"], b["Left"])
    h = min(a["Top"] + a["Height"], b["Top"] + b["Height"]) - max(a["Top"], b["Top"])
    return max(w, 0) * max(h, 0)


def table_markdown(table: dict[str, Any], blocks: dict[str, dict[str, Any]]) -> str:
    cells: dict[tuple[int, int], str] = {}
    for rel in table.get("Relationships", []):
        if rel["Type"] != "CHILD":
            continue
        for cid in rel["Ids"]:
            cell = blocks[cid]
            if cell["BlockType"] != "CELL":
                continue
            words = []
            for crel in cell.get("Relationships", []):
                if crel["Type"] == "CHILD":
                    for w in crel["Ids"]:
                        child = blocks[w]
                        if child["BlockType"] == "WORD":
                            words.append(child["Text"])
                        elif child["BlockType"] == "SELECTION_ELEMENT":
                            words.append("☑" if child["SelectionStatus"] == "SELECTED" else "☐")
            cells[(cell["RowIndex"], cell["ColumnIndex"])] = tidy(" ".join(words)).replace("|", "\\|")
    if not cells:
        return ""
    rows = max(r for r, _ in cells)
    cols = max(c for _, c in cells)
    lines = []
    for r in range(1, rows + 1):
        lines.append("| " + " | ".join(cells.get((r, c), "") for c in range(1, cols + 1)) + " |")
        if r == 1:
            lines.append("|" + "---|" * cols)
    return "\n".join(lines)


def layout_markdown(response: dict[str, Any]) -> str:
    blocks = {b["Id"]: b for b in response["Blocks"]}
    tables = [b for b in response["Blocks"] if b["BlockType"] == "TABLE"]
    parts = []
    for block in response["Blocks"]:  # LAYOUT_* は読む順に並んで返る
        kind = block["BlockType"]
        if not kind.startswith("LAYOUT_") or kind in SKIP_LAYOUT:
            continue
        text = tidy(" ".join(line_text(block, blocks)))
        if kind == "LAYOUT_TITLE":
            parts.append(f"# {text}")
        elif kind == "LAYOUT_SECTION_HEADER":
            parts.append(f"## {text}")
        elif kind == "LAYOUT_TABLE":
            box = block["Geometry"]["BoundingBox"]
            best = max(tables, key=lambda t: overlap(box, t["Geometry"]["BoundingBox"]), default=None)
            parts.append(table_markdown(best, blocks) if best else text)
        elif kind == "LAYOUT_FIGURE":
            labels = [tidy(t) for t in line_text(block, blocks)]
            if labels:
                parts.append("図中の文字:\n" + "\n".join(f"- {t}" for t in labels))
        elif text:
            parts.append(text)
    return "\n\n".join(p for p in parts if p)


def text_markdown(response: dict[str, Any]) -> str:
    return "\n".join(tidy(b["Text"]) for b in response["Blocks"] if b["BlockType"] == "LINE")


def main() -> None:
    args = parse_args()
    client = boto3.client("textract", region_name=args.region)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    total_pages = 0
    for path in sorted(args.input_dir.iterdir()):
        if path.suffix.lower() != ".pdf":
            continue
        out = args.output_dir / f"{path.stem}.md"
        if out.exists() and not args.force:
            print(f"スキップ（出力済み）: {out}")
            continue
        start = time.perf_counter()
        pages = []
        for data in split_pages(path):
            if args.mode == "layout":
                res = client.analyze_document(Document={"Bytes": data}, FeatureTypes=["TABLES", "LAYOUT"])
                pages.append(layout_markdown(res))
            else:
                res = client.detect_document_text(Document={"Bytes": data})
                pages.append(text_markdown(res))
        out.write_text("\n\n".join(pages) + "\n", encoding="utf-8")
        total_pages += len(pages)
        print(f"{path.name}: {len(pages)}ページ {time.perf_counter() - start:.2f}秒 -> {out}")
    cost = total_pages * PRICE_PER_PAGE[args.mode]
    print(f"合計 {total_pages}ページ。東京の単価で約${cost:.3f}")


if __name__ == "__main__":
    main()
