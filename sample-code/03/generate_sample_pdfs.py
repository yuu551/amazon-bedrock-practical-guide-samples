"""Generate sample PDFs with tables, diagrams, and charts for parser comparison.

Outputs (under ``sample-code/03/sample-pdfs/``):
  - ``sampleconnect-plan-matrix.pdf``
      Multi-column plan comparison table with tick marks and merged rows.
      Default parser flattens the columns; the FM parser preserves the grid.
  - ``sampleconnect-architecture.pdf``
      Reference architecture diagram rendered as an image.
      Default parser sees only the caption; the FM parser can describe
      the components and their relationships.
  - ``sampleconnect-retention-chart.pdf``
      Bar chart of audit log retention by plan. Default parser loses the
      numeric values that only appear as bar labels.

Usage:
    uv sync
    uv run generate_sample_pdfs.py
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patches
from matplotlib import font_manager as fm
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "sample-pdfs"


CJK_FONT_PREFERENCE = [
    # Cross-platform — install via your OS font manager or pip package.
    "Noto Sans CJK JP",
    "Noto Sans JP",
    # Linux / Debian / Ubuntu
    "IPAexGothic",
    "IPAGothic",
    "TakaoGothic",
    # macOS (TrueType outlines that reportlab can embed)
    "BIZ UDGothic",
    "Toppan Bunkyu Gothic",
    "YuMincho",
    "AppleGothic",
    # Windows
    "Yu Gothic",
    "Meiryo",
    "MS Gothic",
    # Last-resort macOS fallback (CFF outlines — may fail to embed)
    "Hiragino Sans",
    "Hiragino Maru Gothic Pro",
]


def _try_register_ttf(path: str) -> tuple[str, str] | None:
    """Try to register a TTF/TTC at ``path``. Returns (body, bold) on success."""
    if not path or not os.path.exists(path):
        return None
    try:
        if path.lower().endswith((".ttc", ".otc")):
            pdfmetrics.registerFont(TTFont("JPBody", path, subfontIndex=0))
            face_count = _ttc_face_count(path)
            bold_index = 1 if face_count > 1 else 0
            pdfmetrics.registerFont(
                TTFont("JPBold", path, subfontIndex=bold_index)
            )
        else:
            pdfmetrics.registerFont(TTFont("JPBody", path))
            pdfmetrics.registerFont(TTFont("JPBold", path))
        return "JPBody", "JPBold"
    except Exception as exc:  # noqa: BLE001
        print(f"[info] Skipping font {path}: {exc}")
        return None


def register_fonts() -> tuple[str, str, str | None]:
    """Register a CJK TTF (preferred) or fall back to the CID placeholder.

    Returns (body_font, bold_font, matplotlib_family).
    """
    installed = {f.name: f.fname for f in fm.fontManager.ttflist}
    for name in CJK_FONT_PREFERENCE:
        path = installed.get(name)
        if not path:
            continue
        registered = _try_register_ttf(path)
        if registered:
            body, bold = registered
            return body, bold, name

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    return "HeiseiKakuGo-W5", "HeiseiMin-W3", None


def _ttc_face_count(path: str) -> int:
    try:
        from fontTools.ttLib import TTCollection

        with open(path, "rb") as f:
            tc = TTCollection(f)
            return len(tc.fonts)
    except Exception:  # noqa: BLE001
        return 1


def _styles(body_font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "JPTitle",
        parent=base["Title"],
        fontName=bold_font,
        fontSize=18,
        leading=24,
        spaceAfter=12,
    )
    heading = ParagraphStyle(
        "JPHeading",
        parent=base["Heading2"],
        fontName=bold_font,
        fontSize=13,
        leading=18,
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "JPBody",
        parent=base["BodyText"],
        fontName=body_font,
        fontSize=10.5,
        leading=17,
    )
    caption = ParagraphStyle(
        "JPCaption",
        parent=base["BodyText"],
        fontName=body_font,
        fontSize=9,
        leading=14,
        textColor=colors.grey,
    )
    small = ParagraphStyle(
        "JPSmall",
        parent=base["BodyText"],
        fontName=body_font,
        fontSize=9,
        leading=14,
    )
    return {
        "title": title,
        "heading": heading,
        "body": body,
        "caption": caption,
        "small": small,
    }


def build_plan_matrix_pdf(path: Path, body_font: str, bold_font: str) -> None:
    styles = _styles(body_font, bold_font)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="SampleConnect Platform Plan Matrix",
    )

    story: list = []
    story.append(Paragraph("SampleConnect Platform プラン別機能マトリクス", styles["title"]))
    story.append(
        Paragraph(
            "2026年4月改定版。組織規模と要件に応じた3プランを提供しています。"
            "本資料は営業・導入検討のための比較資料で、表の列と行の対応をそのまま参照できる体裁で整理しています。",
            styles["body"],
        )
    )
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("1. プラン別機能一覧", styles["heading"]))

    check = "●"
    dash = "—"

    header = ["カテゴリ", "機能", "Free", "Standard", "Enterprise"]
    rows = [
        ["基本", "ユーザー数上限", "5名", "100名", "無制限"],
        ["基本", "ストレージ容量", "5GB", "100GB", "500GB"],
        ["基本", "APIレート制限", "10 req/sec", "100 req/sec", "1,000 req/sec"],
        ["運用", "監査ログ保持期間", "30日", "90日", "2年間"],
        ["運用", "SLA（稼働率保証）", dash, "99.9%", "99.95%"],
        ["運用", "サポート応答時間", "72時間", "24時間", "4時間"],
        ["セキュリティ", "SSO / SAML認証", dash, dash, check],
        ["セキュリティ", "IPアドレス制限", dash, dash, check],
        ["セキュリティ", "カスタムロール", dash, "5個まで", "無制限"],
        ["連携", "CloudWatch Logsエクスポート", dash, dash, check],
        ["連携", "S3エクスポート", dash, dash, check],
        ["サポート", "専任カスタマーサクセス", dash, dash, check],
    ]

    table_data = [header] + rows
    table = Table(
        table_data,
        colWidths=[28 * mm, 52 * mm, 24 * mm, 28 * mm, 32 * mm],
        repeatRows=1,
    )
    style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, 0), bold_font),
            ("FONTNAME", (0, 1), (-1, -1), body_font),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (2, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94A3B8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )
    # Merge the category column where consecutive values repeat.
    category_spans: list[tuple[int, int]] = []
    start = 1
    for i in range(2, len(rows) + 1):
        if rows[i - 1][0] != rows[start - 1][0]:
            if i - 1 > start:
                category_spans.append((start, i - 1))
            start = i
    if len(rows) >= start:
        category_spans.append((start, len(rows)))
    for s, e in category_spans:
        if s == e:
            continue
        style.add("SPAN", (0, s), (0, e))
        style.add("VALIGN", (0, s), (0, e), "MIDDLE")
    table.setStyle(style)
    story.append(table)

    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "※ 表中の「●」は標準提供を、「—」は非対応を示します。"
            "Enterpriseプランの詳細条件は別紙「Enterpriseプラン機能ガイド」に従います。",
            styles["small"],
        )
    )

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("2. 料金と適用条件", styles["heading"]))

    pricing_header = ["プラン", "月額（税別）", "最低契約期間", "支払い方法"]
    pricing_rows = [
        ["Free", "0円", "なし", "クレジットカード不要"],
        ["Standard", "48,000円", "1ヶ月", "クレジットカード / 請求書"],
        ["Enterprise", "個別見積もり", "12ヶ月", "請求書 / 銀行振込"],
    ]
    pricing_table = Table(
        [pricing_header] + pricing_rows,
        colWidths=[30 * mm, 40 * mm, 30 * mm, 60 * mm],
    )
    pricing_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), body_font),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94A3B8")),
                ("ALIGN", (1, 1), (2, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(pricing_table)

    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "Enterpriseプランには30日間の無料トライアルがあります。"
            "トライアル期間中はすべてのEnterprise機能を利用でき、トライアル終了後に契約手続きが行われない場合はFreeプランに移行します。",
            styles["body"],
        )
    )

    doc.build(story)


def _render_architecture_diagram(png_path: Path, jp_font: str | None) -> None:
    """Render a reference architecture diagram and save as PNG."""
    if jp_font:
        plt.rcParams["font.family"] = jp_font

    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    def box(x: float, y: float, w: float, h: float, label: str, color: str) -> None:
        ax.add_patch(
            patches.FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.05,rounding_size=0.12",
                linewidth=1.2,
                edgecolor="#1F3A5F",
                facecolor=color,
            )
        )
        ax.text(
            x + w / 2,
            y + h / 2,
            label,
            ha="center",
            va="center",
            fontsize=11,
            color="#0F172A",
            wrap=True,
        )

    def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "") -> None:
        ax.annotate(
            "",
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(arrowstyle="->", color="#1F3A5F", lw=1.6),
        )
        if label:
            ax.text(
                (x1 + x2) / 2,
                (y1 + y2) / 2 + 0.15,
                label,
                ha="center",
                va="bottom",
                fontsize=9,
                color="#475569",
            )

    box(0.3, 4.3, 2.0, 1.0, "ユーザー入力", "#E0F2FE")
    box(3.1, 4.3, 2.2, 1.0, "API Gateway", "#FEF3C7")
    box(6.1, 4.3, 2.6, 1.0, "Knowledge Bases", "#DCFCE7")

    box(3.1, 2.2, 2.2, 1.0, "Lambda関数", "#FEF3C7")
    box(6.1, 2.2, 2.6, 1.0, "OpenSearch Serverless", "#DCFCE7")

    box(0.3, 0.4, 2.0, 1.0, "S3（文書格納）", "#E0F2FE")
    box(3.1, 0.4, 2.2, 1.0, "メタデータ生成", "#FEF3C7")
    box(6.1, 0.4, 2.6, 1.0, "Bedrock基盤モデル", "#FCE7F3")

    arrow(2.3, 4.8, 3.1, 4.8, "質問")
    arrow(5.3, 4.8, 6.1, 4.8, "Retrieve")
    arrow(4.2, 4.3, 4.2, 3.2, "認可")
    arrow(5.3, 2.7, 6.1, 2.7, "ベクトル検索")
    arrow(2.3, 0.9, 3.1, 0.9, "アップロード")
    arrow(5.3, 0.9, 6.1, 0.9, "メタデータ抽出")
    arrow(4.2, 1.4, 4.2, 2.2, "トリガー")
    arrow(7.4, 1.4, 7.4, 2.2, "埋め込み")
    arrow(7.4, 3.2, 7.4, 4.3, "チャンク格納")

    ax.text(
        5.0,
        5.7,
        "SampleConnect RAG 参考アーキテクチャ（2026年4月版）",
        ha="center",
        va="center",
        fontsize=13,
        color="#0F172A",
    )

    fig.tight_layout()
    fig.savefig(png_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_architecture_pdf(
    path: Path, body_font: str, bold_font: str, jp_font: str | None
) -> None:
    styles = _styles(body_font, bold_font)
    png_path = path.with_suffix(".diagram.png")
    _render_architecture_diagram(png_path, jp_font)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="SampleConnect RAG Architecture",
    )
    story: list = []
    story.append(Paragraph("SampleConnect RAG 参考アーキテクチャ", styles["title"]))
    story.append(
        Paragraph(
            "本資料では、SampleConnectの問い合わせ対応を支えるRAG基盤の構成要素と、"
            "主要なデータフローをまとめます。図で全体像を示したうえで、各コンポーネントの役割を補足します。",
            styles["body"],
        )
    )
    story.append(Spacer(1, 4 * mm))

    img = Image(str(png_path), width=170 * mm, height=95 * mm)
    story.append(img)
    story.append(
        Paragraph(
            "図1. SampleConnect RAG 参考アーキテクチャ。"
            "ユーザー入力はAPI Gatewayを経由してKnowledge Basesに渡り、"
            "OpenSearch Serverlessでベクトル検索、Bedrock基盤モデルで埋め込みと回答生成を行います。",
            styles["caption"],
        )
    )
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("コンポーネントの役割", styles["heading"]))
    components_header = ["コンポーネント", "役割", "補足"]
    components_rows = [
        ["API Gateway", "認証済みの質問を受け付ける入口", "Cognito連携によりユーザー属性を付与"],
        ["Lambda関数", "リクエスト整形とKB呼び出し", "audience属性に応じてフィルタを切り替え"],
        ["Knowledge Bases", "検索・回答生成の統合", "RetrieveAndGenerate APIを使用"],
        ["OpenSearch Serverless", "ベクトルインデックスの格納", "VECTORSEARCH コレクション"],
        ["S3", "原本ドキュメントの格納", ".metadata.jsonサイドカーを同梱"],
        ["メタデータ生成Lambda", "サイドカーの自動生成", "Structured Output方式を優先"],
        ["Bedrock基盤モデル", "埋め込み・応答生成・メタデータ抽出", "Titan Embed v2 + Claude系"],
    ]
    components_table = Table(
        [components_header] + components_rows,
        colWidths=[40 * mm, 55 * mm, 65 * mm],
    )
    components_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), body_font),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94A3B8")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(components_table)

    doc.build(story)
    # Keep the PNG next to the PDF so readers can inspect it directly.


def _render_retention_chart(png_path: Path, jp_font: str | None) -> None:
    if jp_font:
        plt.rcParams["font.family"] = jp_font

    plans = ["Free", "Standard", "Enterprise"]
    retention_days = [30, 90, 730]
    throughput = [10, 100, 1000]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.4))

    bars1 = ax1.bar(
        plans,
        retention_days,
        color=["#94A3B8", "#0EA5E9", "#9333EA"],
        edgecolor="#1F3A5F",
    )
    ax1.set_title("監査ログ保持期間（日）", fontsize=12)
    ax1.set_ylabel("保持日数", fontsize=10)
    ax1.set_yscale("log")
    ax1.set_ylim(10, 2000)
    for bar, value in zip(bars1, retention_days):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() * 1.08,
            f"{value}日",
            ha="center",
            va="bottom",
            fontsize=11,
            color="#0F172A",
        )

    bars2 = ax2.bar(
        plans,
        throughput,
        color=["#94A3B8", "#0EA5E9", "#9333EA"],
        edgecolor="#1F3A5F",
    )
    ax2.set_title("APIレート制限（req/sec）", fontsize=12)
    ax2.set_ylabel("リクエスト毎秒", fontsize=10)
    ax2.set_yscale("log")
    ax2.set_ylim(5, 2000)
    for bar, value in zip(bars2, throughput):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() * 1.08,
            f"{value:,} req/sec",
            ha="center",
            va="bottom",
            fontsize=11,
            color="#0F172A",
        )

    fig.suptitle(
        "SampleConnect Platform プラン別 運用指標（2026年4月時点）", fontsize=13
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(png_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_retention_chart_pdf(
    path: Path, body_font: str, bold_font: str, jp_font: str | None
) -> None:
    styles = _styles(body_font, bold_font)
    png_path = path.with_suffix(".chart.png")
    _render_retention_chart(png_path, jp_font)

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="SampleConnect Retention Metrics",
    )
    story: list = []
    story.append(Paragraph("SampleConnect プラン別 運用指標ダッシュボード", styles["title"]))
    story.append(
        Paragraph(
            "本書は、営業・導入検討用に公開している運用指標の抜粋です。"
            "監査ログ保持期間とAPIレート制限は、プランによって桁が異なるためログスケールで表示しています。"
            "数値は各棒の上部ラベルに記載しており、本文中には具体値を繰り返し記載していません。",
            styles["body"],
        )
    )
    story.append(Spacer(1, 4 * mm))

    img = Image(str(png_path), width=170 * mm, height=72 * mm)
    story.append(img)
    story.append(
        Paragraph(
            "図2. 左：監査ログ保持期間の比較。右：APIレート制限の比較。"
            "どちらも縦軸は対数スケールで、ラベルにプランごとの値が記載されています。",
            styles["caption"],
        )
    )
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("読み解きのポイント", styles["heading"]))
    story.append(
        Paragraph(
            "保持期間の差は、監査要件の厳しさに応じてプランを選ぶ際の主要な判断材料になります。"
            "上位プランほど長期保持を前提に設計されており、各プランでの具体的な保持日数は上の左側のグラフのラベルを参照してください。",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "APIレート制限については、バッチ処理や大規模連携を行う場合に上位プランの上限が必要になるケースが多く、"
            "下位プランではピーク時に制限に達する事例が報告されています。各プランの上限値は上の右側のグラフのラベルを参照してください。",
            styles["body"],
        )
    )

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("補足情報", styles["heading"]))
    story.append(
        Paragraph(
            "本資料の数値は2026年4月時点のものです。正式な契約条件は別紙「プラン別機能比較」および「SampleConnect利用規約」に従います。",
            styles["small"],
        )
    )

    doc.build(story)


def build_incident_report_pdf(path: Path, body_font: str, bold_font: str) -> None:
    """Long-form incident report used to showcase semantic chunking.

    複数セクションの意味的まとまりが重要な長文解説資料。固定長で切ると
    「原因と対策」「前提条件と例外条件」が別チャンクに割れやすくなる。
    """
    styles = _styles(body_font, bold_font)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="SampleConnect Incident Report 2026-03-18",
    )
    story: list = []
    story.append(Paragraph("SampleConnect Platform 2026年3月18日 API停止障害 詳細報告書", styles["title"]))
    story.append(
        Paragraph(
            "発生日: 2026年3月18日（火） 13:05 JST〜14:47 JST（約1時間42分）。影響: 一部テナントにおけるSampleConnect API応答停止。"
            "本報告書は、営業担当および技術担当のお客様向けに、原因と対策、影響範囲、再発防止策を段階的に説明します。",
            styles["body"],
        )
    )
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("1. インシデント概要", styles["heading"]))
    story.append(
        Paragraph(
            "2026年3月18日13:05 JSTから、SampleConnect Platformの `/v1/search` および `/v1/agent` エンドポイントで、"
            "一部テナントに対するAPI応答が断続的に停止する事象が発生しました。影響範囲はEnterpriseプラン契約の一部テナント、"
            "特に米国東部リージョン（us-east-1）にベクトルデータを配置しているテナントに集中しました。"
            "通常の運用監視で検知した後、オンコール体制による対応に移行し、14:47 JSTに全テナントで完全復旧を確認しました。"
            "本報告書の前半では事実関係と原因を、後半では再発防止策と補償対応を記載しています。",
            styles["body"],
        )
    )

    story.append(Paragraph("2. タイムライン", styles["heading"]))
    story.append(
        Paragraph(
            "13:05 JST: 特定テナントの `/v1/search` で 5xx 応答が断続発生。内部メトリクスでエラーレートが1.2%から18.4%まで急増。"
            "13:09 JST: オンコールエンジニアが自動ページングを受領し一次対応を開始。"
            "13:16 JST: 認証トークンの検証レイヤで `ExpiredToken` 例外が大量発生していることを確認。"
            "13:28 JST: 認証サービスの内部キャッシュをフラッシュする手順を実行したが症状は改善せず、さらに別のレイヤで障害が起きている可能性を検討。"
            "13:42 JST: OpenSearch ServerlessへのSigV4署名生成に用いる共有ライブラリが、18日00:00 UTCからの時刻同期ずれで一部ノードでリクエストを拒否していたと判明。"
            "14:05 JST: 影響ノードのローリング再起動を開始。再起動中のテナントでは一時的に応答遅延が発生することをサポート窓口から案内。"
            "14:31 JST: ローリング再起動完了。エラーレートが平常値（0.04%）まで低下したのを確認。"
            "14:47 JST: 全リージョンで監視ダッシュボード上のSLOを満たしたことを確認し、全面復旧を宣言。",
            styles["body"],
        )
    )

    story.append(Paragraph("3. 根本原因", styles["heading"]))
    story.append(
        Paragraph(
            "根本原因は、SigV4署名生成に用いる共有認証ライブラリ `sampleconnect-authx` v2.4.1 のバグです。"
            "このバージョンは、18日00:00 UTCを境にシステムクロックと認証トークンの発行時刻に100ミリ秒以上のドリフトが発生すると、"
            "署名検証を自動再試行するロジックが有効化される設計でした。再試行は最大3回行われ、それでも失敗するとフォールバックとしてトークンを無効化します。"
            "しかし、実装上の欠陥により、再試行ロジックが無効化判定を終えるまで該当リクエストがブロックされ、結果としてスレッドプールが枯渇しました。",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "このバグは、通常の単体試験および負荷試験では再現しませんでした。再現条件は、(1) 時刻ドリフトが100ミリ秒を超えること、"
            "(2) 同一テナントから毎秒100リクエスト以上の並行トラフィックがあること、(3) キャッシュヒット率が90%未満に低下していること、の3条件が同時に成立することです。"
            "本番環境の特定テナントでのみこの条件がそろいました。なお、Standard / Freeプランのテナントは独立したスレッドプールを利用しているため影響を受けませんでした。",
            styles["body"],
        )
    )

    story.append(Paragraph("4. 影響範囲", styles["heading"]))
    story.append(
        Paragraph(
            "影響を受けたのは、Enterpriseプランに契約しているテナントのうち、以下の条件をすべて満たすテナントに限られます。"
            "(A) us-east-1リージョンにベクトルデータを配置していること、"
            "(B) 13:00〜14:30 JSTの時間帯に `/v1/search` または `/v1/agent` への並行リクエスト数が毎秒100以上あったこと、"
            "(C) 専用SigV4キャッシュを有効化していること（デフォルト有効）。"
            "なお、Standard / Freeプランのテナント、および他リージョン（us-west-2、ap-northeast-1）にデータを配置していたテナントは影響を受けていません。"
            "さらに、Enterpriseプランでも上記(B)の条件を満たさない（低トラフィック）テナントは、自動再試行の範囲内で応答が返却されていたため体感上の停止は発生していません。",
            styles["body"],
        )
    )

    story.append(Paragraph("5. 初動対応と復旧手順", styles["heading"]))
    story.append(
        Paragraph(
            "初動では、オンコールエンジニアが認証トークンの異常を疑い、キャッシュフラッシュを実施しました。"
            "この手順は過去の類似事象で有効だったため最初に試みましたが、今回のケースでは原因レイヤが異なっていたため効果がありませんでした。"
            "続いてSigV4署名エラーのログパターン解析を行い、時刻ドリフトとの相関を特定したうえで、影響ノードのローリング再起動を決定しました。",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "ローリング再起動の具体的な手順は次のとおりです。まず、負荷分散層（ALB）でunhealthy判定となっているノードを切り離します。"
            "次に、該当ノードで `sampleconnect-authx` を v2.4.1 から v2.3.9 へロールバックし、ローカルキャッシュをクリアしたうえでサービスを再起動します。"
            "ノードの再起動後にヘルスチェックが通ることを確認したら、ALBに戻します。この作業を1ノードずつ順次実施し、30分で12ノードの切り替えを完了しました。"
            "並行して、サポート窓口から影響テナントへ「応答遅延が発生する可能性あり」の案内を送信しています。",
            styles["body"],
        )
    )

    story.append(Paragraph("6. 再発防止策", styles["heading"]))
    story.append(
        Paragraph(
            "短期対応として、 `sampleconnect-authx` を v2.3.9 にロールバックし、問題のあった自動再試行ロジックを無効化しました。"
            "この対応は本障害の2時間後、3月18日17:00 JSTまでに全ノードへ適用完了しています。"
            "ただし、v2.3.9では時刻ドリフト時の自動復旧機能がないため、中期的な解決にはなりません。",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "中期対応として、 `sampleconnect-authx` v2.4.2 を4月末までにリリースします。v2.4.2では、再試行ロジックをスレッドプール外の非同期タスクに移し、"
            "スレッドプール枯渇を防止します。v2.4.2は、v2.4.1と同じ自動復旧機能を持ちながらも、今回の障害パターンに耐える設計に変更されています。"
            "長期的な再発防止としては、時刻ドリフト検知をNTP層で閉じ込め、認証レイヤまで伝播しない構造を5月の定期リリースで導入する計画です。"
            "また、負荷試験では時刻ドリフトをシミュレートするテストケースを新規に追加し、CI/CDパイプラインで自動実行するように変更しました。",
            styles["body"],
        )
    )

    story.append(Paragraph("7. お客様への補償対応", styles["heading"]))
    story.append(
        Paragraph(
            "EnterpriseプランのSLA（99.95%）に基づき、影響を受けたテナントに対してはSLAクレジットを自動適用します。"
            "具体的には、3月度の月額利用料の10%をクレジットとして翌月請求から減算します。対象テナントには3月24日までに個別にメール通知を送付しています。"
            "Standard / Freeプランのテナントについては、今回の障害による影響が確認されていないため、補償対応の対象外としています。"
            "ただし、Standardプランで今回の事象に類似する応答遅延を観測したテナントからの申告があった場合は、サポート窓口で個別に調査します。"
            "なお、この補償対応はSLA契約書の第14条および別紙「障害対応規程」に準拠した内容であり、特例措置ではない通常のフローに従った運用です。",
            styles["body"],
        )
    )

    doc.build(story)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    body_font, bold_font, jp_font = register_fonts()
    if not jp_font:
        print(
            "[warn] No Japanese font detected. Using CID fallback; "
            "PDFs may not render in all viewers, and figure labels may be tofu. "
            "Install 'Noto Sans CJK JP' or 'IPAexGothic' to fix."
        )

    plan_pdf = OUTPUT_DIR / "sampleconnect-plan-matrix.pdf"
    arch_pdf = OUTPUT_DIR / "sampleconnect-architecture.pdf"
    chart_pdf = OUTPUT_DIR / "sampleconnect-retention-chart.pdf"
    incident_pdf = OUTPUT_DIR / "sampleconnect-incident-report.pdf"

    build_plan_matrix_pdf(plan_pdf, body_font, bold_font)
    build_architecture_pdf(arch_pdf, body_font, bold_font, jp_font)
    build_retention_chart_pdf(chart_pdf, body_font, bold_font, jp_font)
    build_incident_report_pdf(incident_pdf, body_font, bold_font)

    for pdf in (plan_pdf, arch_pdf, chart_pdf, incident_pdf):
        size_kb = pdf.stat().st_size / 1024
        print(f"wrote {pdf.relative_to(SCRIPT_DIR)} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
