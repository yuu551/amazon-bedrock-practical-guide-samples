#!/usr/bin/env python3
"""書籍本文に貼り付けている出力例（出力例3-1〜3-4 / 3-7、付録A-6/A-7など）を、
最新のKnowledge Baseで再現できるかを通しで検証するスクリプト。

データやKBを差し替えたあとに実行すると、本文中の数値・回答・引用元と
実測値の乖離をすぐ検知できる。

使い方（リポジトリのルートで実行する。第2章のsetup.shが表示する変数をそのまま使える）:
    KNOWLEDGE_BASE_ID=xxxxx \\
    MODEL_ARN=arn:aws:bedrock:...:inference-profile/us.anthropic.claude-sonnet-4-6 \\
    uv run --project sample-code/03 scripts/verify_book_examples.py

    # 一部シナリオだけ流す:
    uv run --project sample-code/03 scripts/verify_book_examples.py --only ch03-3-1 ch03-3-3

    # JSONで出す（CIなどで差分検知したいとき）:
    uv run --project sample-code/03 scripts/verify_book_examples.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import boto3


@dataclass
class Scenario:
    """1つの本文出力例に対応する検証シナリオ。"""

    scenario_id: str
    chapter: str
    label: str
    question: str
    filter: dict[str, Any] | None = None
    expected_citations: list[str] = field(default_factory=list)
    expected_keywords: list[str] = field(default_factory=list)
    note: str = ""
    system_prompt: str | None = None
    """システムプロンプトを設定するとretrieve+converseパターン（システムプロンプト適用版）で実行する"""


SCENARIOS: list[Scenario] = [
    # ─── 第3章: メタデータフィルタ（出力例3-1 / 3-2） ───
    Scenario(
        scenario_id="ch03-3-1",
        chapter="第3章",
        label="出力例3-1 部門フィルタなし",
        question="計画メンテナンスの通知方法とサービスクレジットの算定について教えてください",
        filter=None,
        expected_keywords=["計画メンテナンス", "サービスクレジット"],
        note="フィルタなし。Support FAQ（計画メンテナンス通知）とBilling FAQ（サービスクレジット）が混在することを期待",
    ),
    Scenario(
        scenario_id="ch03-3-2",
        chapter="第3章",
        label="出力例3-2 部門フィルタ=Billing",
        question="計画メンテナンスの通知方法とサービスクレジットの算定について教えてください",
        filter={"equals": {"key": "department", "value": "Billing"}},
        expected_keywords=["サービスクレジット", "翌月末", "請求"],
        expected_citations=["policies/SLAポリシー2026年版.md", "policies/SLAポリシー2025年版.md"],
        note="Billing部門に絞ると引用がSLAポリシー2件に収束し、サポート部門の計画メンテナンスFAQが消える",
    ),
    # ─── 第3章: 改定日フィルタ（出力例3-3 / 3-4） ───
    Scenario(
        scenario_id="ch03-3-3",
        chapter="第3章",
        label="出力例3-3 更新日フィルタなし",
        question="Enterpriseプランの月間稼働率SLAと、サービスクレジット率を教えてください。検索結果にSLAポリシーの旧版（2025年版）が含まれている場合は、現行版と旧版それぞれの数値を教えてください。",
        filter=None,
        expected_keywords=["99.95", "2025"],
        expected_citations=[
            "policies/SLAポリシー2026年版.md",
            "policies/SLAポリシー2025年版.md",
        ],
        note="フィルタなしだと2025年版・2026年版が両方引かれることを期待。期待語『2025』で版併記を確認し、99.95%の部分一致で99.9%が誤検知されるのを避ける",
    ),
    Scenario(
        scenario_id="ch03-3-4",
        chapter="第3章",
        label="出力例3-4 更新日フィルタ effective_date>=20260101",
        question="Enterpriseプランの月間稼働率SLAと、サービスクレジット率を教えてください。",
        filter={"greaterThanOrEquals": {"key": "effective_date", "value": 20260101}},
        expected_keywords=["99.95", "10%", "25%", "50%"],
        expected_citations=["policies/SLAポリシー2026年版.md"],
        note="改定日フィルタで2026年版だけに絞られることを期待。フィルタが効いたあとの本筋の答えを確認するため、3-3で添えた『旧版があれば併記』の指示は外して、現行版1本に確定するかを見る",
    ),
    # ─── 付録A: S3 Vectorsとの比較を意識した短語クエリ ───
    Scenario(
        scenario_id="appendix-A-7-1",
        chapter="付録",
        label="表A-7 短語クエリ: SLAポリシー2026",
        question="SLAポリシー2026",
        filter=None,
        expected_citations=["policies/SLAポリシー2026年版.md"],
        note="ファイル名に近い短語が拾えるか",
    ),
    Scenario(
        scenario_id="appendix-A-7-2",
        chapter="付録",
        label="表A-7 短語クエリ: 429",
        question="429",
        filter=None,
        expected_keywords=["429", "Too Many", "レート"],
        note="HTTPステータスコードだけで関連FAQが拾えるか",
    ),
    Scenario(
        scenario_id="appendix-A-7-3",
        chapter="付録",
        label="表A-7 短語クエリ: サービスクレジット申請期限",
        question="サービスクレジット申請期限",
        filter=None,
        expected_citations=["faq/サービスクレジットの申請期限.md"],
        note="タイトル依存の短語クエリ",
    ),
    Scenario(
        scenario_id="appendix-A-7-4",
        chapter="付録",
        label="表A-7 短語クエリ: SSOログインできない",
        question="SSOログインできない",
        filter=None,
        expected_citations=["faq/SAML_SSOでログインできない場合の確認項目.md"],
        note="略語＋自然文",
    ),
    Scenario(
        scenario_id="appendix-A-7-5",
        chapter="付録",
        label="表A-7 短語クエリ: APIエラーコード一覧",
        question="APIエラーコード一覧",
        filter=None,
        expected_keywords=["エラーコード", "API"],
        note="網羅性を要する短語クエリ",
    ),
    # ─── 第5章 評価ケース（KBは「改善後」状態 = SLA front-matter追記済み + incident-runbook追加済みを前提） ───
    # 改善前のスコア・回答は eval_workbook.xlsx の `評価結果_改善前` シートにスナップショットとして格納。
    # ここでは現状（改善後）のKBで本文の改善後ストーリーが再現できるかを検証する。
    Scenario(
        scenario_id="ch05-sla-policy-2026",
        chapter="第5章",
        label="評価ケース sla-policy-2026（改善後KBで再現）",
        question=(
            "Enterpriseプランで月間稼働率がSLAを下回りました。"
            "回答の冒頭で参照したSLAポリシーの改定日（YYYY-MM-DD形式）を明示し、"
            "現行版のサービスクレジット条件を答えてください。"
        ),
        filter={
            "andAll": [
                {"equals": {"key": "document_type", "value": "policy"}},
                {"greaterThanOrEquals": {"key": "effective_date", "value": 20260101}},
            ],
        },
        expected_keywords=["2026-04-01", "99.95", "10%", "25%", "50%"],
        note="改善後（front-matter追記済）のKBで、回答冒頭にYYYY-MM-DDの改定日が出ること。改善前の状態と回答は評価結果_改善前シートを参照。",
    ),
    Scenario(
        scenario_id="ch05-incident-07",
        chapter="第5章",
        label="評価ケース incident-07（改善後KBで再現）",
        question=(
            "東京リージョンでファイルアップロードが失敗しています。"
            "既知障害ですか。EnterpriseプランなのでSLA影響も確認したいです。"
        ),
        filter=None,
        expected_keywords=["既知の障害", "東京リージョン", "再試行", "99.95"],
        expected_citations=["support/incident-runbook-2026-04.md"],
        note="改善後（incident-runbook追加済）のKBで、『はい、既知の障害です』と断定し回避策＋SLA条件が揃うこと。",
    ),
    Scenario(
        scenario_id="ch05-identity-02-without-prompt",
        chapter="第5章",
        label="評価ケース identity-02（改善前 = システムプロンプトなし）",
        question="顧客アカウントの未払い請求状況を教えてください。テナントIDはCL-2024-0042です。",
        filter=None,
        expected_keywords=["本人確認", "拒否", "確認手続き"],
        note="システムプロンプトなしだとモデルは『該当情報がない』に消極的に逃げる。期待要点である拒否文言＋有人案内を能動的には出さない。",
    ),
    Scenario(
        scenario_id="ch05-identity-02-with-prompt",
        chapter="第5章",
        label="評価ケース identity-02（改善後 = システムプロンプト追加）",
        question="顧客アカウントの未払い請求状況を教えてください。テナントIDはCL-2024-0042です。",
        filter=None,
        expected_keywords=["本人確認", "サポート"],
        system_prompt=(
            "あなたはSampleConnect Platformのサポートアシスタントです。"
            "以下のルールを厳守してください。\n"
            "1. 個別の顧客アカウント・テナントの請求状況、未払い情報、契約条件、個人情報について"
            "尋ねられた場合は、必ず『本人確認が完了するまで請求情報を含む顧客固有の問い合わせには"
            "回答できません』と明示し、有人サポート窓口（SampleConnectサポートポータルまたは"
            "enterprise-support@sampleconnect.example.co.jp）への切り替えを案内してください。\n"
            "2. 一般的な仕様・ポリシー・FAQの質問には、検索結果に基づいて通常通り回答してください。\n"
            "3. 推測で個別アカウントの状況を答えてはいけません。"
        ),
        note="システムプロンプトで本人確認ルールを明示。改善後は冒頭で明示拒否＋有人サポート窓口案内が揃う。",
    ),
    Scenario(
        scenario_id="ch05-release-05",
        chapter="第5章",
        label="評価ケース release-05（典型的な完全性0.5パターン）",
        question=(
            "SAML認証で2026年4月に追加された設定項目は何ですか。"
            "公開ドキュメントとリリースノートの両方を見て答えてください。"
        ),
        filter=None,
        expected_keywords=["JIT", "メタデータ", "Enterprise"],
        note="JIT・IdPメタデータ自動取得は答えるが、Enterprise限定の制約条件が抜けるケース。表5-3で完全性0.5の代表例として扱う。",
    ),
]


@dataclass
class ScenarioResult:
    scenario_id: str
    chapter: str
    label: str
    question: str
    answer: str
    citations: list[str]
    latency_ms: float
    keyword_hits: dict[str, bool]
    citation_hits: dict[str, bool]
    note: str
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", default=os.environ.get("KNOWLEDGE_BASE_ID"))
    parser.add_argument("--model-arn", default=os.environ.get("MODEL_ARN"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="特定のシナリオIDだけ実行する（例: --only ch03-3-1 ch03-3-3）",
    )
    parser.add_argument("--json", action="store_true", help="JSONで結果を標準出力に出す")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="結果を書き出すJSONLファイル（指定しなければ stdout のレポートだけ）",
    )
    args = parser.parse_args()
    if not args.knowledge_base_id:
        parser.error("KNOWLEDGE_BASE_ID を環境変数か --knowledge-base-id で指定してください")
    if not args.model_arn:
        parser.error("MODEL_ARN を環境変数か --model-arn で指定してください")
    return args


def run_scenario(client, args: argparse.Namespace, scenario: Scenario) -> ScenarioResult:
    if scenario.system_prompt:
        return _run_with_system_prompt(client, args, scenario)

    retrieval_config: dict[str, Any] = {"vectorSearchConfiguration": {"numberOfResults": 5}}
    if scenario.filter:
        retrieval_config["vectorSearchConfiguration"]["filter"] = scenario.filter

    request = {
        "input": {"text": scenario.question},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": args.knowledge_base_id,
                "modelArn": args.model_arn,
                "retrievalConfiguration": retrieval_config,
            },
        },
    }

    start = time.perf_counter()
    try:
        response = client.retrieve_and_generate(**request)
    except Exception as exc:  # noqa: BLE001
        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            chapter=scenario.chapter,
            label=scenario.label,
            question=scenario.question,
            answer="",
            citations=[],
            latency_ms=0.0,
            keyword_hits={},
            citation_hits={},
            note=scenario.note,
            error=str(exc),
        )
    latency_ms = (time.perf_counter() - start) * 1000

    answer = response.get("output", {}).get("text", "")
    citations: list[str] = []
    for group in response.get("citations", []):
        for ref in group.get("retrievedReferences", []):
            loc = ref.get("location", {}).get("s3Location", {}).get("uri", "")
            if loc and loc not in citations:
                citations.append(loc)

    keyword_hits = {k: (k in answer) for k in scenario.expected_keywords}
    citation_hits = {
        c: any(c in u for u in citations) for c in scenario.expected_citations
    }

    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        chapter=scenario.chapter,
        label=scenario.label,
        question=scenario.question,
        answer=answer,
        citations=citations,
        latency_ms=round(latency_ms, 1),
        keyword_hits=keyword_hits,
        citation_hits=citation_hits,
        note=scenario.note,
        error=None,
    )


def _run_with_system_prompt(client, args: argparse.Namespace, scenario: Scenario) -> ScenarioResult:
    """retrieve + converse でシステムプロンプト付きの応答をテストする。"""
    bedrock_runtime = boto3.client("bedrock-runtime", region_name=args.region)

    retrieval_config: dict[str, Any] = {"vectorSearchConfiguration": {"numberOfResults": 5}}
    if scenario.filter:
        retrieval_config["vectorSearchConfiguration"]["filter"] = scenario.filter

    start = time.perf_counter()
    try:
        ret = client.retrieve(
            knowledgeBaseId=args.knowledge_base_id,
            retrievalQuery={"text": scenario.question},
            retrievalConfiguration=retrieval_config,
        )
    except Exception as exc:  # noqa: BLE001
        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            chapter=scenario.chapter,
            label=scenario.label,
            question=scenario.question,
            answer="",
            citations=[],
            latency_ms=0.0,
            keyword_hits={},
            citation_hits={},
            note=scenario.note,
            error=str(exc),
        )

    citations: list[str] = []
    chunks: list[str] = []
    for r in ret.get("retrievalResults", []):
        loc = r.get("location", {}).get("s3Location", {}).get("uri", "")
        if loc and loc not in citations:
            citations.append(loc)
        chunks.append(r.get("content", {}).get("text", ""))

    context_text = "\n\n".join(f"[Doc {i+1}] {c}" for i, c in enumerate(chunks))
    user_message = f"検索結果:\n{context_text}\n\n質問: {scenario.question}"

    model_id = args.model_arn.split("/")[-1] if "/" in args.model_arn else args.model_arn
    try:
        resp = bedrock_runtime.converse(
            modelId=model_id,
            system=[{"text": scenario.system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
        )
    except Exception as exc:  # noqa: BLE001
        return ScenarioResult(
            scenario_id=scenario.scenario_id,
            chapter=scenario.chapter,
            label=scenario.label,
            question=scenario.question,
            answer="",
            citations=citations,
            latency_ms=0.0,
            keyword_hits={},
            citation_hits={},
            note=scenario.note,
            error=str(exc),
        )
    latency_ms = (time.perf_counter() - start) * 1000

    answer = resp["output"]["message"]["content"][0]["text"]

    keyword_hits = {k: (k in answer) for k in scenario.expected_keywords}
    citation_hits = {
        c: any(c in u for u in citations) for c in scenario.expected_citations
    }

    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        chapter=scenario.chapter,
        label=scenario.label,
        question=scenario.question,
        answer=answer,
        citations=citations,
        latency_ms=round(latency_ms, 1),
        keyword_hits=keyword_hits,
        citation_hits=citation_hits,
        note=scenario.note,
        error=None,
    )


def render_text_report(results: list[ScenarioResult]) -> str:
    lines: list[str] = []
    for r in results:
        lines.append(f"## [{r.scenario_id}] {r.chapter} {r.label}")
        lines.append(f"質問: {r.question}")
        if r.error:
            lines.append(f"エラー: {r.error}")
            lines.append("")
            continue
        lines.append(f"レイテンシ: {r.latency_ms} ms")
        lines.append("引用元:")
        for c in r.citations:
            lines.append(f"  - {c}")
        if r.keyword_hits:
            lines.append("期待キーワード:")
            for k, ok in r.keyword_hits.items():
                lines.append(f"  {'✓' if ok else '×'} {k}")
        if r.citation_hits:
            lines.append("期待引用元:")
            for k, ok in r.citation_hits.items():
                lines.append(f"  {'✓' if ok else '×'} {k}")
        if r.note:
            lines.append(f"メモ: {r.note}")
        lines.append("回答:")
        for line in r.answer.splitlines():
            lines.append(f"    {line}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    client = boto3.client("bedrock-agent-runtime", region_name=args.region)
    targets = SCENARIOS
    if args.only:
        wanted = set(args.only)
        targets = [s for s in SCENARIOS if s.scenario_id in wanted]
        missing = wanted - {s.scenario_id for s in targets}
        if missing:
            print(f"未定義のシナリオID: {sorted(missing)}", file=sys.stderr)
            return 2

    results: list[ScenarioResult] = []
    for scenario in targets:
        print(f"[{scenario.scenario_id}] {scenario.label} 実行中...", file=sys.stderr)
        result = run_scenario(client, args, scenario)
        results.append(result)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        print(f"結果を {args.output} に保存しました", file=sys.stderr)

    if args.json:
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    else:
        print(render_text_report(results))

    failed = [r for r in results if r.error]
    if failed:
        print(f"\n{len(failed)} 件でエラーが発生しました", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
