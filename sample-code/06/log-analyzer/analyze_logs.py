#!/usr/bin/env python3
"""Bedrock モデル呼び出しログの分析ツール。

CloudWatch Logs Insights を使ってモデル呼び出しログを集計し、
コスト推定とレイテンシ分析のレポートを出力します。
第6章のサンプルコードとして、本番運用でのログ分析パターンを示します。

使い方:
    python analyze_logs.py \\
        --log-group /aws/bedrock/modelinvocations \\
        --start-time 2026-04-01T00:00:00 \\
        --end-time 2026-04-11T00:00:00 \\
        --group-by model
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import boto3

from queries import (
    QUERY_BY_HOUR,
    QUERY_BY_MODEL,
    QUERY_BY_PROFILE,
    QUERY_ERRORS,
    QUERY_KB_INGESTION,
)

# --------------------------------------------------------------------------
# トークン単価（USD / 1,000 トークン）
# Bedrock オンデマンド料金の参考値。
# 実際の料金は AWS の価格表ページで最新値を確認すること。
# Sonnet はエイリアスID、Haiku はフルバージョンIDで記載。
# ログに記録されるモデルIDの形式に合わせてキーを設定する。
# --------------------------------------------------------------------------
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # Claude Sonnet 4.6 (US cross-region inference)
    "us.anthropic.claude-sonnet-4-6": {
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
    },
    # Claude Sonnet 4.5 (US cross-region inference)
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
    },
    # Claude Haiku 4 (US cross-region inference)
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {
        "input_per_1k": 0.0008,
        "output_per_1k": 0.004,
    },
    # Claude Sonnet 4.6 (us-east-1 direct)
    "anthropic.claude-sonnet-4-6": {
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
    },
    # Claude Sonnet 4.5 (us-east-1 direct)
    "anthropic.claude-sonnet-4-5-20250929-v1:0": {
        "input_per_1k": 0.003,
        "output_per_1k": 0.015,
    },
    # Claude Haiku 4 (us-east-1 direct)
    "anthropic.claude-haiku-4-5-20251001-v1:0": {
        "input_per_1k": 0.0008,
        "output_per_1k": 0.004,
    },
    # Amazon Titan Embeddings v2 (input only)
    "amazon.titan-embed-text-v2:0": {
        "input_per_1k": 0.00002,
        "output_per_1k": 0.0,
    },
}

# クエリ結果のポーリング間隔（秒）
POLL_INTERVAL_SEC = 1.0
# ポーリング最大待機時間（秒）
POLL_TIMEOUT_SEC = 120


# ======================================================================
# CloudWatch Logs Insights へのクエリ実行
# ======================================================================

def run_insights_query(
    client,
    log_group: str,
    query: str,
    start_epoch: int,
    end_epoch: int,
) -> list[list[dict]]:
    """Logs Insights クエリを実行し、結果が返るまでポーリングする。"""

    resp = client.start_query(
        logGroupName=log_group,
        startTime=start_epoch,
        endTime=end_epoch,
        queryString=query,
        limit=10_000,
    )
    query_id = resp["queryId"]
    print(f"  クエリ開始: {query_id}")

    # ポーリングで完了を待つ
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        result = client.get_query_results(queryId=query_id)
        status = result["status"]

        if status == "Complete":
            rows = result.get("results", [])
            print(f"  完了: {len(rows)} 行取得")
            return rows

        if status in ("Failed", "Cancelled", "Timeout"):
            raise RuntimeError(f"クエリ失敗 (status={status}): {query_id}")

        time.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError(
        f"クエリがタイムアウトしました ({POLL_TIMEOUT_SEC}秒): {query_id}"
    )


def rows_to_dicts(rows: list[list[dict]]) -> list[dict[str, str]]:
    """Logs Insights の結果行を {field: value} の辞書リストに変換する。"""
    results = []
    for row in rows:
        d = {}
        for field in row:
            if field["field"] != "@ptr":
                d[field["field"]] = field["value"]
        results.append(d)
    return results


# ======================================================================
# コスト推定
# ======================================================================

def short_model_id(model_id: str) -> str:
    """ARN 形式の modelId を表示用に短縮する。

    例:
      arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0
        → amazon.titan-embed-text-v2:0
      arn:aws:bedrock:us-east-1:111122223333:inference-profile/us.anthropic.claude-sonnet-4-6
        → us.anthropic.claude-sonnet-4-6
    """
    if not isinstance(model_id, str):
        return str(model_id)
    if "/" in model_id:
        return model_id.rsplit("/", 1)[-1]
    return model_id


def estimate_cost(
    model_id: str,
    input_tokens: float,
    output_tokens: float,
    pricing: dict[str, dict[str, float]],
) -> float | None:
    """トークン数から推定コスト（USD）を計算する。

    料金テーブルに該当モデルがない場合は None を返す。
    """
    price = pricing.get(model_id)
    if price is None:
        # アプリケーション推論プロファイル の ARN から末尾のモデル名を取り出す
        for key in pricing:
            if model_id.endswith(key) or key in model_id:
                price = pricing[key]
                break
    if price is None:
        return None

    cost = (
        input_tokens / 1_000 * price["input_per_1k"]
        + output_tokens / 1_000 * price["output_per_1k"]
    )
    return cost


# ======================================================================
# レポート出力
# ======================================================================

def _fmt_number(value: str | float, decimals: int = 1) -> str:
    """数値を読みやすい形式にフォーマットする。"""
    try:
        v = float(value)
    except (ValueError, TypeError):
        return str(value)
    if v == 0:
        return "0"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:,.{decimals}f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:,.{decimals}f}K"
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.{decimals}f}"


def _fmt_cost(cost: float | None) -> str:
    """コストを USD 表記にフォーマットする。"""
    if cost is None:
        return "---"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def print_table(headers: list[str], rows: list[list[str]], title: str) -> None:
    """シンプルなテーブルを標準出力に表示する。"""
    # 各列の最大幅を計算
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    separator = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    header_line = "|" + "|".join(f" {h:<{widths[i]}} " for i, h in enumerate(headers)) + "|"

    print()
    print(f"=== {title} ===")
    print(separator)
    print(header_line)
    print(separator)
    for row in rows:
        line = "|" + "|".join(f" {c:<{widths[i]}} " for i, c in enumerate(row)) + "|"
        print(line)
    print(separator)


def _to_float(value: str | float | None) -> float:
    """空文字列や None を 0 とみなして float へ変換する。"""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _fmt_latency(value: float, count: float) -> str:
    """レイテンシ値が記録されていない場合（InvokeModel 等）は ─ を返す。"""
    if value <= 0 or count <= 0:
        return "─"
    return _fmt_number(value, 0)


def merge_by_short_model_id(data: list[dict[str, str]]) -> list[dict[str, float | str]]:
    """ARN 形式と短縮形を同一視し、modelId 末尾のセグメントで集約する。"""
    merged: dict[str, dict[str, float]] = {}
    for d in data:
        key = short_model_id(d.get("modelId", "不明"))
        m = merged.setdefault(key, {
            "modelId": key,
            "invocations": 0.0,
            "total_input_tokens": 0.0,
            "total_output_tokens": 0.0,
            "_lat_sum": 0.0,
            "_lat_count": 0.0,
            "_p50_max": 0.0,
            "_p90_max": 0.0,
            "_p99_max": 0.0,
        })
        invocations = _to_float(d.get("invocations"))
        avg_lat = _to_float(d.get("avg_latency_ms"))

        m["invocations"] += invocations
        m["total_input_tokens"] += _to_float(d.get("total_input_tokens"))
        m["total_output_tokens"] += _to_float(d.get("total_output_tokens"))
        if avg_lat > 0 and invocations > 0:
            m["_lat_sum"] += avg_lat * invocations
            m["_lat_count"] += invocations
            m["_p50_max"] = max(m["_p50_max"], _to_float(d.get("p50_latency_ms")))
            m["_p90_max"] = max(m["_p90_max"], _to_float(d.get("p90_latency_ms")))
            m["_p99_max"] = max(m["_p99_max"], _to_float(d.get("p99_latency_ms")))

    result: list[dict[str, float | str]] = []
    for m in merged.values():
        avg_lat = m["_lat_sum"] / m["_lat_count"] if m["_lat_count"] else 0.0
        result.append({
            "modelId": m["modelId"],
            "invocations": m["invocations"],
            "total_input_tokens": m["total_input_tokens"],
            "total_output_tokens": m["total_output_tokens"],
            "avg_latency_ms": avg_lat,
            "_lat_count": m["_lat_count"],
            "p50_latency_ms": m["_p50_max"],
            "p90_latency_ms": m["_p90_max"],
            "p99_latency_ms": m["_p99_max"],
        })
    result.sort(key=lambda r: r["invocations"], reverse=True)
    return result


def report_by_model(
    data: list[dict[str, str]],
    pricing: dict[str, dict[str, float]],
) -> None:
    """モデル別集計レポートを出力する。"""
    headers = [
        "モデル ID",
        "呼出回数",
        "入力トークン(合計)",
        "出力トークン(合計)",
        "平均レイテンシ(ms)",
        "p50(ms)",
        "p90(ms)",
        "p99(ms)",
        "推定コスト(USD)",
    ]
    rows = []
    total_cost = 0.0
    for d in merge_by_short_model_id(data):
        model_id = str(d["modelId"])
        input_tokens = _to_float(d.get("total_input_tokens"))
        output_tokens = _to_float(d.get("total_output_tokens"))
        cost = estimate_cost(model_id, input_tokens, output_tokens, pricing)
        if cost is not None:
            total_cost += cost
        lat_count = _to_float(d.get("_lat_count"))

        rows.append([
            model_id,
            _fmt_number(d.get("invocations", 0), 0),
            _fmt_number(input_tokens, 1),
            _fmt_number(output_tokens, 1),
            _fmt_latency(_to_float(d.get("avg_latency_ms")), lat_count),
            _fmt_latency(_to_float(d.get("p50_latency_ms")), lat_count),
            _fmt_latency(_to_float(d.get("p90_latency_ms")), lat_count),
            _fmt_latency(_to_float(d.get("p99_latency_ms")), lat_count),
            _fmt_cost(cost),
        ])

    print_table(headers, rows, "モデル別 呼び出し集計")
    print(f"\n  推定コスト合計: {_fmt_cost(total_cost)}")
    print("  ※ コストは参考値です。実際の請求額は AWS Cost Explorer で確認してください。")


def report_by_profile(
    data: list[dict[str, str]],
    pricing: dict[str, dict[str, float]],
) -> None:
    """Inference Profile 別集計レポートを出力する。"""
    headers = [
        "Inference Profile ID",
        "呼出回数",
        "入力トークン(合計)",
        "出力トークン(合計)",
        "平均レイテンシ(ms)",
        "p90(ms)",
        "推定コスト(USD)",
    ]
    rows = []
    total_cost = 0.0
    for d in data:
        profile_id = short_model_id(d.get("inferenceProfileId", "不明"))
        input_tokens = _to_float(d.get("total_input_tokens"))
        output_tokens = _to_float(d.get("total_output_tokens"))
        invocations = _to_float(d.get("invocations"))
        avg_lat = _to_float(d.get("avg_latency_ms"))
        cost = estimate_cost(profile_id, input_tokens, output_tokens, pricing)
        if cost is not None:
            total_cost += cost

        rows.append([
            profile_id,
            _fmt_number(invocations, 0),
            _fmt_number(input_tokens, 1),
            _fmt_number(output_tokens, 1),
            _fmt_latency(avg_lat, invocations if avg_lat > 0 else 0),
            _fmt_latency(_to_float(d.get("p90_latency_ms")), invocations if avg_lat > 0 else 0),
            _fmt_cost(cost),
        ])

    print_table(headers, rows, "Inference Profile 別 呼び出し集計")
    print(f"\n  推定コスト合計: {_fmt_cost(total_cost)}")
    print("  ※ アプリケーション推論プロファイル を使うと、アプリケーション単位でのコスト配賦が可能です。")


def report_by_hour(data: list[dict[str, str]]) -> None:
    """時間帯別集計レポートを出力する。"""
    headers = [
        "時間帯",
        "呼出回数",
        "入力トークン(合計)",
        "出力トークン(合計)",
        "平均レイテンシ(ms)",
        "p90(ms)",
        "エラー数",
    ]
    rows = []
    for d in data:
        rows.append([
            d.get("time_bucket", "---"),
            _fmt_number(d.get("invocations", "0"), 0),
            _fmt_number(d.get("total_input_tokens", "0"), 1),
            _fmt_number(d.get("total_output_tokens", "0"), 1),
            _fmt_number(d.get("avg_latency_ms", "0"), 0),
            _fmt_number(d.get("p90_latency_ms", "0"), 0),
            _fmt_number(d.get("error_count", "0"), 0),
        ])

    print_table(headers, rows, "時間帯別 呼び出し推移")

    # レイテンシの傾向分析
    if len(data) >= 2:
        latencies = [float(d.get("p90_latency_ms", 0)) for d in data]
        valid = [v for v in latencies if v > 0]
        if valid:
            avg = sum(valid) / len(valid)
            peak = max(valid)
            peak_idx = latencies.index(peak)
            peak_time = data[peak_idx].get("time_bucket", "不明")
            print(f"\n  p90レイテンシ: 平均 {avg:.0f}ms / ピーク {peak:.0f}ms ({peak_time})")


def report_errors(data: list[dict[str, str]]) -> None:
    """エラー集計レポートを出力する。"""
    if not data:
        print("\n=== エラー集計 ===")
        print("  対象期間内にエラーは検出されませんでした。")
        return

    headers = ["エラーコード", "モデル ID", "件数"]
    rows = []
    for d in data:
        rows.append([
            d.get("errorCode", "不明"),
            d.get("modelId", "不明"),
            _fmt_number(d.get("error_count", "0"), 0),
        ])

    print_table(headers, rows, "エラー集計")


def report_kb_ingestion(data: list[dict[str, str]]) -> None:
    """Knowledge Bases 取り込みジョブレポートを出力する。"""
    if not data:
        print("\n=== Knowledge Bases 取り込み集計 ===")
        print("  対象期間内に取り込みログは検出されませんでした。")
        return

    headers = [
        "Knowledge Base ID",
        "データソース ID",
        "スキャン数",
        "インデックス済",
        "失敗数",
        "メタデータ更新",
    ]
    rows = []
    for d in data:
        failed = int(float(d.get("total_failed", 0)))
        rows.append([
            d.get("knowledgeBaseId", "不明"),
            d.get("dataSourceId", "不明"),
            _fmt_number(d.get("total_scanned", "0"), 0),
            _fmt_number(d.get("total_indexed", "0"), 0),
            _fmt_number(d.get("total_failed", "0"), 0),
            _fmt_number(d.get("total_metadata_updated", "0"), 0),
        ])
        if failed > 0:
            print(f"  [警告] データソース {d.get('dataSourceId', '?')} で {failed} 件の取り込み失敗があります。")

    print_table(headers, rows, "Knowledge Bases 取り込み集計")


# ======================================================================
# CLI
# ======================================================================

def parse_timestamp(s: str) -> int:
    """ISO 8601 形式の文字列を UNIX エポック秒に変換する。"""
    dt = datetime.fromisoformat(s)
    # タイムゾーンが指定されていない場合は UTC として扱う
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bedrock モデル呼び出しログを分析し、コスト・レイテンシのレポートを出力します。",
    )
    parser.add_argument(
        "--log-group",
        default="/aws/bedrock/modelinvocations",
        help="CloudWatch Logs のロググループ名（デフォルト: /aws/bedrock/modelinvocations）",
    )
    parser.add_argument(
        "--kb-log-group",
        default="/aws/bedrock/knowledgebases/APPLICATION_LOGS",
        help="Knowledge Bases APPLICATION_LOGS のロググループ名",
    )
    parser.add_argument(
        "--start-time",
        required=True,
        help="分析開始日時（ISO 8601 形式、例: 2026-04-01T00:00:00）",
    )
    parser.add_argument(
        "--end-time",
        required=True,
        help="分析終了日時（ISO 8601 形式、例: 2026-04-11T00:00:00）",
    )
    parser.add_argument(
        "--group-by",
        choices=["model", "profile", "hour"],
        default="model",
        help="集計軸: model=モデル別, profile=Inference Profile別, hour=時間帯別",
    )
    parser.add_argument(
        "--include-errors",
        action="store_true",
        help="エラー集計も出力する",
    )
    parser.add_argument(
        "--include-kb",
        action="store_true",
        help="Knowledge Bases の取り込み集計も出力する",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS リージョン（デフォルト: us-east-1）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    start_epoch = parse_timestamp(args.start_time)
    end_epoch = parse_timestamp(args.end_time)

    print("Bedrock モデル呼び出しログ分析")
    print(f"  期間: {args.start_time} ~ {args.end_time}")
    print(f"  ロググループ: {args.log_group}")
    print(f"  集計軸: {args.group_by}")
    print()

    client = boto3.client("logs", region_name=args.region)
    pricing = DEFAULT_PRICING

    # メインクエリの実行
    if args.group_by == "model":
        print("[1/1] モデル別集計クエリを実行中...")
        raw = run_insights_query(
            client, args.log_group, QUERY_BY_MODEL, start_epoch, end_epoch,
        )
        data = rows_to_dicts(raw)
        report_by_model(data, pricing)

    elif args.group_by == "profile":
        print("[1/1] Inference Profile 別集計クエリを実行中...")
        raw = run_insights_query(
            client, args.log_group, QUERY_BY_PROFILE, start_epoch, end_epoch,
        )
        data = rows_to_dicts(raw)
        report_by_profile(data, pricing)

    elif args.group_by == "hour":
        print("[1/1] 時間帯別集計クエリを実行中...")
        raw = run_insights_query(
            client, args.log_group, QUERY_BY_HOUR, start_epoch, end_epoch,
        )
        data = rows_to_dicts(raw)
        report_by_hour(data)

    # オプション: エラー集計
    if args.include_errors:
        print("\nエラー集計クエリを実行中...")
        raw = run_insights_query(
            client, args.log_group, QUERY_ERRORS, start_epoch, end_epoch,
        )
        data = rows_to_dicts(raw)
        report_errors(data)

    # オプション: Knowledge Bases 取り込み集計
    if args.include_kb:
        print("\nKnowledge Bases 取り込み集計クエリを実行中...")
        print(f"  ロググループ: {args.kb_log_group}")
        raw = run_insights_query(
            client, args.kb_log_group, QUERY_KB_INGESTION, start_epoch, end_epoch,
        )
        data = rows_to_dicts(raw)
        report_kb_ingestion(data)


if __name__ == "__main__":
    main()
