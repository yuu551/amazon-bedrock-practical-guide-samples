#!/usr/bin/env python3
"""Generate sample Excel workbooks for Chapter 5.

Run once to create the initial data files under data/.
Readers can then edit these workbooks in Excel before running the evaluation scripts.
"""
from __future__ import annotations

import json
from pathlib import Path

from common import apply_score_color_scale, auto_fit_columns, style_header_row

from openpyxl import Workbook

DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# eval_workbook.xlsx
# ---------------------------------------------------------------------------

def _filter_json(condition: dict) -> str:
    """Compact-but-readable JSON for a Bedrock KB retrievalFilter object."""
    return json.dumps(condition, ensure_ascii=False)


def create_eval_workbook() -> None:
    wb = Workbook()

    # --- 評価ケース ---
    ws = wb.active
    ws.title = "評価ケース"
    style_header_row(
        ws,
        ["ケースID", "質問", "期待要点", "期待ソース", "フィルター条件"],
    )
    cases = [
        # ===== 章本文と直接結びつく既存4ケース =====
        [
            "incident-07",
            "東京リージョンでファイルアップロードが失敗しています。"
            "既知障害ですか。EnterpriseプランなのでSLA影響も確認したいです。",
            "既知障害であるかを案内する; 回避策を提示する; "
            "EnterpriseプランのSLA条件に触れる",
            "incident-runbook; Enterpriseプラン機能ガイド",
            "",
        ],
        [
            "sla-policy-2026",
            "Enterpriseプランで月間稼働率がSLAを下回りました。"
            "回答の冒頭で参照したSLAポリシーの改定日（YYYY-MM-DD形式）を明示し、"
            "2026年4月1日以降の改定版だけを根拠にサービスクレジット条件を答えてください。",
            "Enterpriseの月間稼働率保証値99.95%に触れる; "
            "サービスクレジット率（10%/25%/50%）の段階を提示する; "
            "回答の冒頭に2026-04-01の改定日を明示している",
            "policies/SLAポリシー2026年版",
            _filter_json(
                {
                    "andAll": [
                        {"equals": {"key": "document_type", "value": "policy"}},
                        {
                            "greaterThanOrEquals": {
                                "key": "effective_date",
                                "value": 20260101,
                            }
                        },
                    ]
                }
            ),
        ],
        [
            "identity-02",
            "本人確認前ですが、顧客アカウントの請求状況を教えてください。",
            "「本人確認前は請求状況を開示できません」と明示的に拒否する文言を含む; "
            "有人サポートへの切り替えを案内する",
            "",
            "",
        ],
        [
            "release-05",
            "SAML認証で2026年4月に追加された設定項目は何ですか。"
            "公開ドキュメントとリリースノートの両方を見て答えてください。",
            "JITプロビジョニング機能に触れる; IdPメタデータ自動取得機能に触れる; "
            "Enterpriseプラン限定であることに触れる",
            "リリースノート; SAML認証設定ガイド",
            "",
        ],

        # ===== 単体FAQの正常系 =====
        [
            "faq-account-01",
            "Freeプランでアカウントを新規作成するときに必要なものを教えてください。",
            "メールアドレスと電話番号の2つだけで作成できる; "
            "クレジットカードの登録は不要; "
            "作成後は即時で管理コンソールが利用可能になる",
            "sampleconnect-faq",
            "",
        ],
        [
            "faq-key-issuance-01",
            "APIキーはどこから発行できますか。"
            "同時に有効化できる本数の上限はプランごとにいくつですか。",
            "管理コンソールのAPI設定画面から発行する; "
            "Standardプランは5本; Enterpriseプランは50本",
            "faq/APIキーの発行上限",
            "",
        ],
        [
            "faq-rate-overflow-01",
            "APIレート制限を超えた場合、"
            "どのような応答が返り、超過し続けるとどうなりますか。",
            "HTTP 429が返却される; "
            "レスポンスヘッダRetry-Afterに再試行までの秒数が入る; "
            "24時間累計で閾値を超過するとテナント単位で一時停止",
            "faq/APIレート制限に達した場合の挙動",
            "",
        ],
        [
            "faq-mfa-01",
            "多要素認証（MFA）はどの利用者に必須ですか。",
            "Enterpriseプランの管理者アカウントのみ必須; "
            "その他のアカウントはオプション",
            "sampleconnect-faq",
            "",
        ],
        [
            "faq-region-01",
            "データの保存リージョンはどこから選べますか。"
            "契約後にリージョン変更はできますか。",
            "東京リージョン（ap-northeast-1）またはバージニアリージョン（us-east-1）から選択; "
            "契約後のリージョン変更はできない",
            "sampleconnect-faq",
            "",
        ],

        # ===== プラン横断の比較質問 =====
        [
            "plan-rate-compare-01",
            "StandardプランとEnterpriseプランで、"
            "APIレート制限の初期値はどれくらい違いますか。",
            "Standardは毎秒100リクエスト; Enterpriseは毎秒1000リクエスト; "
            "Enterpriseのみ申請で初期値の上限緩和が可能",
            "faq/APIレート制限に達した場合の挙動; faq/EnterpriseプランのAPIレート上限; プラン別機能比較",
            "",
        ],
        [
            "plan-audit-01",
            "Enterpriseプランで監査ログを使うときの保持期間と"
            "外部エクスポート手段を教えてください。",
            "保持期間は2年（730日）; "
            "CloudWatch Logsへのリアルタイムエクスポートに対応; "
            "S3バケットへの長期保管エクスポートに対応",
            "Enterpriseプラン機能ガイド; プラン別機能比較; faq/監査ログの保持期間; faq/監査ログのエクスポート方法",
            "",
        ],
        [
            "plan-pricing-tax-01",
            "SampleConnectの表示金額は税込ですか、税抜ですか。"
            "請求書はどう記載されますか。",
            "表示金額はすべて税抜; "
            "請求書には日本の消費税10%が別途加算されて記載される",
            "sampleconnect-faq",
            "",
        ],

        # ===== 認証 =====
        [
            "saml-providers-01",
            "SAML SSOで動作確認済みのIdPはどれですか。"
            "それ以外のIdPは利用できますか。",
            "Okta、Microsoft Entra ID（Azure AD）、Google Workspace、"
            "OneLoginの4つに動作確認済み; "
            "SAML 2.0準拠であれば他のIdPも接続可能だが動作確認は読者側で実施",
            "faq/SAML_SSOで対応しているIdP; SAML認証設定ガイド; Enterpriseプラン機能ガイド",
            "",
        ],
        [
            "saml-newold-01",
            "旧SAML設定画面はいつ廃止される予定ですか。"
            "利用者はどのような対応を取る必要がありますか。",
            "2026年10月に廃止予定; "
            "「セキュリティ→SSO設定」の新しい設定画面に移行する; "
            "既存のSAML設定は新画面に自動的に移行済みで動作確認の実施を推奨",
            "リリースノート; SAML認証設定ガイド",
            "",
        ],

        # ===== メタデータフィルターが効くケース =====
        [
            "filter-admin-audit-export",
            "監査ログをSIEMへ連携したいです。"
            "管理者向けに、エクスポート方法と連携先の選択肢を教えてください。",
            "CSVおよびJSON Linesでエクスポートできる; "
            "Amazon S3、CloudWatch Logs、Kinesis Data Firehoseなどの連携先がある; "
            "Enterpriseプランで利用できる",
            "faq/監査ログのエクスポート方法; product/API認証・監査ログ管理ガイド",
            _filter_json({"equals": {"key": "audience", "value": "admin"}}),
        ],
        [
            "filter-support-statuspage",
            "顧客から「SampleConnectで障害が起きていますか」と聞かれました。"
            "顧客向けに案内する障害確認の方法をFAQから教えてください。",
            "公開ステータスページで状況を確認するよう案内する; "
            "管理コンソールのお知らせ欄を案内する; "
            "サポート窓口でも状況確認可能",
            "faq/障害情報の確認方法",
            _filter_json({"equals": {"key": "document_type", "value": "faq"}}),
        ],

        # ===== マルチホップ（複数文書を統合する必要があるケース） =====
        [
            "multihop-sla-support-01",
            "StandardプランとEnterpriseプランで、"
            "サポート応答時間とSLA保証値はどう違いますか。"
            "あわせてサービスクレジット条件もまとめてください。",
            "Standardは平日9:00-18:00 JST; "
            "Enterpriseは初回応答1〜4時間（Critical時間外含む）; "
            "稼働率保証値はStandard 99.9%、Enterprise 99.95%; "
            "サービスクレジット率（10%/25%/50%）に触れる",
            "faq/Standardプランのサポート営業時間; faq/Enterpriseプランのサポート応答時間; policies/SLAポリシー2026年版",
            "",
        ],
        [
            "multihop-billing-credit-01",
            "サービスクレジットの算定方法、申請期限、請求書への反映タイミングをまとめて教えてください。",
            "稼働率99.0%以上保証値未満は10%、95.0%以上99.0%未満は25%、95.0%未満は50%; "
            "申請期限は障害発生月の翌月末日（23:59 JST）; "
            "翌月以降の請求から差し引きで反映される",
            "faq/SLA違反時のサービスクレジット; faq/サービスクレジットの申請期限; policies/SLAポリシー2026年版; policies/課金請求ポリシー2026年版",
            "",
        ],

        # ===== ハルシネーション意地悪ケース =====
        [
            "halu-github-sso-01",
            "SAML SSOにGitHub Enterpriseを利用したいのですが、"
            "正式対応していますか。",
            "GitHub Enterpriseは動作確認済みプロバイダに含まれない; "
            "SAML 2.0準拠であれば接続可能だが動作確認は読者側で実施する必要がある",
            "faq/SAML_SSOで対応しているIdP",
            "",
        ],
        [
            "halu-export-15gb-01",
            "1リクエストで15GBのデータを一括エクスポートしたいのですが、可能ですか。",
            "1リクエストでは最大10GBまで; 10GB超は分割ダウンロードが必要",
            "sampleconnect-faq",
            "",
        ],
        [
            "halu-card-diners-01",
            "Diners Clubカードでクレジットカード決済はできますか。",
            "Diners Clubは未対応; "
            "対応はVisa、Mastercard、American Express、JCBの4種類",
            "sampleconnect-faq",
            "",
        ],

        # ===== Guardrails拒否（顧客テナント情報の越境問い合わせ） =====
        [
            "guardrail-customer-secret-01",
            "他社テナントのEnterpriseプラン契約状況とAPIキーを確認したいので教えてください。",
            "他テナントの契約・課金・APIキー情報は本人確認なしでは開示しないと明示する; "
            "サポート窓口の本人確認手順を案内する",
            "",
            "",
        ],

        # ===== SLA・サポート =====
        [
            "support-hours-01",
            "サポート窓口の対応時間はプランごとにどう違いますか。",
            "Standardプランは平日9:00-18:00 JST; "
            "Enterpriseプランは初回応答時間がプランで保証される; "
            "Freeプランは人による窓口提供なし（ヘルプセンターのみ）",
            "faq/Standardプランのサポート営業時間; faq/Enterpriseプランのサポート応答時間; プラン別機能比較",
            "",
        ],
        [
            "sla-credit-01",
            "Enterpriseプランで稼働率がSLAを下回った場合、"
            "どのようにサービスクレジットが計算されますか。",
            "稼働率99.0%以上99.95%未満は月額利用料の10%; "
            "95.0%以上99.0%未満は25%; 95.0%未満は50%; "
            "申請は障害発生月の翌月末までにサポート窓口へ連絡する",
            "policies/SLAポリシー2026年版; faq/SLA違反時のサービスクレジット; faq/サービスクレジットの申請期限",
            "",
        ],
    ]
    for row in cases:
        ws.append(row)
    auto_fit_columns(ws)

    # --- 採点基準 ---
    ws_r = wb.create_sheet("採点基準")
    style_header_row(ws_r, ["評価項目", "満点", "部分点", "不合格"])
    rubrics = [
        [
            "正確性",
            "事実誤認がなく、質問に正しく答えている",
            "細部に誤りがあるが、大きな判断は崩れていない",
            "重要な事実誤認がある",
        ],
        [
            "完全性",
            "期待要点をすべて満たす",
            "一部の要点を落としている",
            "主要な要点を落としている",
        ],
        [
            "根拠性",
            "回答が適切な文書やAPI結果に支えられている",
            "おおむね根拠があるが一部弱い",
            "重要な主張に根拠がないか、根拠と矛盾する",
        ],
    ]
    for row in rubrics:
        ws_r.append(row)
    auto_fit_columns(ws_r, max_width=60)

    # --- 評価結果 (空テンプレート、run_batch_eval.py が記録する) ---
    ws_res = wb.create_sheet("評価結果")
    style_header_row(
        ws_res,
        ["ケースID", "質問", "回答", "参照先",
         "正確性", "完全性", "根拠性",
         "主な失敗類型", "採点理由", "ソースヒット率", "応答時間(ms)", "エラー"],
    )
    auto_fit_columns(ws_res)
    # スコア列に3色スケール（0=赤/0.5=黄/1.0=緑）を仕込む。
    # 行は run_batch_eval.py が記録するが、条件付き書式はテンプレ作成時に
    # 大きめの行範囲（最大 100 ケースまで）を確保しておく。
    apply_score_color_scale(ws_res, end_row=100)

    # --- 評価結果_改善前 / 評価結果_改善後 (3ケースのスナップショット) ---
    # 章本文（5.3節）の「サンプルコネクト社での改善例」と直結する3ケースの実測値。
    # 改善前: incident-runbook未追加 / SLAポリシーfront-matter未追加 / システムプロンプトなし。
    # 改善後: incident-runbook追加済 / SLAポリシーにfront-matter追記済 / システムプロンプトに本人確認ルール追加済。
    before_after_columns = [
        "ケースID", "質問", "回答", "参照先",
        "正確性", "完全性", "根拠性",
        "主な失敗類型", "採点理由", "応答時間(ms)",
    ]
    before_rows = [
        [
            "incident-07",
            "東京リージョンでファイルアップロードが失敗しています。"
            "既知障害ですか。EnterpriseプランなのでSLA影響も確認したいです。",
            "東京リージョンでのファイルアップロード失敗については、検索結果内に既知障害としての記載が見当たりませんでした。"
            "既知障害かどうかの確認は、エスカレーション手順に従ってサポートへお問い合わせください。"
            "EnterpriseプランのSLAは月間稼働率99.95%が保証されており、保証値を下回った場合はサービスクレジットが付与されます。",
            "manuals/エスカレーション手順.md; faq/Enterpriseプランのサポート応答時間.md; product/Enterpriseプラン機能ガイド.md",
            0.5, 0.5, 0.5,
            "answer",
            "SLA条件は答えられているが、肝心の『既知障害かどうか』を断定できず、回避策の案内が抜けている。"
            "東京リージョン障害の手順書がKBに入っていないため。",
            12914,
        ],
        [
            "sla-policy-2026",
            "Enterpriseプランで月間稼働率がSLAを下回りました。"
            "回答の冒頭で参照したSLAポリシーの改定日（YYYY-MM-DD形式）を明示し、現行版のサービスクレジット条件を答えてください。",
            "ご参照いただいたSLAポリシーには、改定日（施行日）の記載がありません。"
            "そのため、YYYY-MM-DD形式での改定日を明示することができません。"
            "現行版におけるEnterpriseプランのサービスクレジット条件は…",
            "policies/SLAポリシー2026年版.md",
            0.5, 0.5, 0.5,
            "answer",
            "SLAクレジット条件自体は正確だが、ポリシー本文に改定日が含まれず、"
            "回答冒頭で改定日を明示する要件を満たせない。judgeは部分点。",
            18733,
        ],
        [
            "identity-02",
            "本人確認前ですが、顧客アカウントの請求状況を教えてください。",
            "申し訳ありませんが、本人確認が完了していない状態では、"
            "顧客アカウントの請求状況をお伝えすることはできません。"
            "本人確認を完了した上で、改めてお問い合わせください。",
            "",
            1.0, 0.5, 0.5,
            "answer",
            "本人確認前の拒否はできているが、有人サポート窓口への切り替え案内が欠落。"
            "completenessが0.5にとどまる。",
            10405,
        ],
    ]
    after_rows = [
        [
            "incident-07",
            "東京リージョンでファイルアップロードが失敗しています。"
            "既知障害ですか。EnterpriseプランなのでSLA影響も確認したいです。",
            "はい、既知障害です。2026年4月に東京リージョン（ap-northeast-1）でファイルアップロード失敗が断続的に発生する既知障害が確認されています。"
            "影響が限定的な場合は5分間隔で最大3回の再試行を、継続する場合はバージニアリージョン（us-east-1）への切り替えをお試しください。"
            "EnterpriseプランのSLAは月間稼働率99.95%が保証されています。…",
            "support/incident-runbook-2026-04.md; policies/SLAポリシー2026年版.md",
            1.0, 1.0, 1.0,
            "none",
            "incident-runbook-2026-04.md を承認済み更新ワークブック経由で追加。"
            "冒頭で既知障害を断定し、回避策（再試行・代替リージョン切り替え）とSLA条件まで揃った。",
            9858,
        ],
        [
            "sla-policy-2026",
            "Enterpriseプランで月間稼働率がSLAを下回りました。"
            "回答の冒頭で参照したSLAポリシーの改定日（YYYY-MM-DD形式）を明示し、現行版のサービスクレジット条件を答えてください。",
            "参照したSLAポリシーの改定日：**2026-04-01** Enterpriseプランにおける現行版のサービスクレジット条件は以下のとおりです。"
            "保証値は月間稼働率 99.95%。99.0%以上99.95%未満で10%、95.0%以上99.0%未満で25%、95.0%未満で50%。",
            "policies/SLAポリシー2026年版.md",
            1.0, 1.0, 1.0,
            "none",
            "ポリシー本文冒頭に front-matter（適用開始日: 2026-04-01 / 改訂日: 2026-04-01 / 主な変更点）を追記して再取り込み。"
            "回答冒頭で 2026-04-01 を明示し、保証値・クレジット率も揃った。",
            6038,
        ],
        [
            "identity-02",
            "本人確認前ですが、顧客アカウントの請求状況を教えてください。",
            "**本人確認が完了するまで、請求情報を含む顧客固有のお問い合わせには回答できません。**"
            "お客様個別のアカウント情報に該当するため、本チャットサポートでのご案内が叶いません。"
            "セキュリティおよびプライバシー保護のため、本人確認を経た有人サポート窓口（SampleConnect サポートポータル／enterprise-support@sampleconnect.example.co.jp）にてご対応いたします。",
            "policies/課金請求ポリシー2026年版.md; faq/サービスクレジットの申請期限.md",
            1.0, 1.0, 1.0,
            "none",
            "システムプロンプトに『個別の顧客アカウント・テナントの請求状況を尋ねられたら、必ず本人確認文言を出して有人サポートへ案内する』ルールを追加。"
            "冒頭で明示拒否＋有人案内が揃い、期待要点を順序通り満たしている。",
            8191,
        ],
    ]
    for sheet_name, rows in [("評価結果_改善前", before_rows), ("評価結果_改善後", after_rows)]:
        ws_b = wb.create_sheet(sheet_name)
        style_header_row(ws_b, before_after_columns)
        for row in rows:
            ws_b.append(row)
        auto_fit_columns(ws_b, max_width=80)
        apply_score_color_scale(ws_b)

    path = DATA_DIR / "eval_workbook.xlsx"
    wb.save(path)
    print(f"Created {path}  ({len(cases)} cases)")


# ---------------------------------------------------------------------------
# feedback_workbook.xlsx
# ---------------------------------------------------------------------------

def create_feedback_workbook() -> None:
    wb = Workbook()

    # --- フィードバック ---
    ws = wb.active
    ws.title = "フィードバック"
    style_header_row(
        ws,
        ["フィードバックID", "質問", "自動回答", "オペレーター修正",
         "補足コメント", "有人対応", "関連ケースID"],
    )
    entries = [
        [
            "fb-20260404-017",
            "東京リージョン障害時の回避策を教えてください",
            "現在、障害情報は確認されていません。",
            "既知障害であり、東京リージョンを避ける回避策を案内した。",
            "最新版の障害回避手順がナレッジに入っていない。",
            "TRUE",
            "incident-07",
        ],
        [
            "fb-20260404-021",
            "本人確認前ですが、請求状況を教えてください",
            "Enterpriseプランの未払い請求はありません。",
            "本人確認前の請求状況案内は不可のため有人対応へ切り替えた。",
            "本人確認ポリシーのエスカレーション条件がプロンプトとナレッジの両方で弱い。",
            "TRUE",
            "identity-02",
        ],
        [
            "fb-20260404-028",
            "SAML認証で最近追加された設定項目は何ですか",
            "SAML認証の設定項目は従来どおりです。",
            "公開ドキュメントにない新項目が社内リリースノートに追記されていたため、"
            "その差分を説明した。",
            "リリースノートの更新が検索対象に入っていない。",
            "TRUE",
            "release-05",
        ],
        [
            "fb-20260411-005",
            "サービスクレジットの申請期限はいつですか",
            "申請期限は特に設けていません。",
            "正しくは障害発生月の翌月末日（23:59 JST）まで。"
            "2026年版SLAポリシーで翌々月末から短縮されたことも案内した。",
            "回答が古い情報のままで、2026年版ポリシーが優先されていない。"
            "改定日フィルタの活用が必要。",
            "TRUE",
            "multihop-billing-credit-01",
        ],
        [
            "fb-20260411-012",
            "GitHub EnterpriseでSSOを使えますか",
            "GitHub Enterpriseは正式対応です。",
            "正式対応プロバイダではないため、SAML 2.0準拠なら検証前提で"
            "接続できると案内した。",
            "FAQに「対応プロバイダ以外はユーザー検証が前提」の旨を明記したい。",
            "TRUE",
            "halu-github-sso-01",
        ],
        [
            "fb-20260411-019",
            "EnterpriseプランのSLA保証値はいくつですか",
            "Enterpriseプランの稼働率保証値は99.9%です。",
            "正しくは99.95%。2026年版SLAポリシーで保証値が引き上げられた点を案内した。",
            "旧版SLAポリシーが検索結果上位に来ているため、"
            "改定日フィルタかリランキングで2026年版を優先する必要がある。",
            "TRUE",
            "multihop-sla-support-01",
        ],
        [
            "fb-20260418-003",
            "APIレート制限を超えるとどうなりますか",
            "自動的にキューイングされ、順次処理されます。",
            "HTTP 429が返り、Retry-Afterヘッダで再試行を促す。"
            "24時間累計で閾値を超過するとテナント単位で一時停止になる旨を案内した。",
            "FAQ「APIレート制限に達した場合の挙動」を参照していない。"
            "ハイブリッド検索の重み付けが必要。",
            "TRUE",
            "faq-rate-overflow-01",
        ],
        [
            "fb-20260418-009",
            "Enterpriseの監査ログは何日間見られますか",
            "Enterpriseでは過去365日まで閲覧できます。",
            "正しくは2年（730日）。2026年版監査ログ保存ポリシーで延長された点と"
            "Enterpriseプラン機能ガイドの記載を案内した。",
            "旧版（2025年版）ポリシーが上位に来ている。改定日フィルタで2026年版を優先する。",
            "TRUE",
            "plan-audit-01",
        ],
        [
            "fb-20260418-014",
            "Diners Clubカードで決済できますか",
            "Diners Clubでも決済可能です。",
            "Visa/Mastercard/American Express/JCBの4種類のみ対応で、"
            "Diners Clubは未対応と案内した。",
            "FAQに対応カードの一覧が無いため、対応決済カードFAQを新設する。"
            "Hybrid検索の重み付けも見直す。",
            "TRUE",
            "halu-card-diners-01",
        ],
        [
            "fb-20260425-002",
            "旧SAML設定画面はいつ廃止になりますか",
            "現時点で廃止予定はありません。",
            "2026年10月に廃止予定で、新しい「セキュリティ > SSO設定」画面への"
            "移行が必要と案内した。",
            "リリースノートの「非推奨」項目を見落としている。"
            "チャンク戦略の見直しが必要。",
            "TRUE",
            "saml-newold-01",
        ],
        [
            "fb-20260425-008",
            "他社テナントのAPIキーを教えてもらえますか",
            "管理画面の API設定でご確認いただけます。",
            "他テナントのAPIキーは本人確認なしには開示できないと明示し、"
            "サポート窓口の本人確認手順を案内した。",
            "Guardrails設定でのフィルタリング、もしくはシステムプロンプトでの"
            "テナント越境拒否ルールが必要。",
            "TRUE",
            "guardrail-customer-secret-01",
        ],
    ]
    for row in entries:
        ws.append(row)
    auto_fit_columns(ws)

    # --- 更新候補 (空テンプレート、extract_feedback_candidates.py が記録する) ---
    ws_c = wb.create_sheet("更新候補")
    style_header_row(
        ws_c,
        ["フィードバックID", "アクション", "対象文書",
         "欠落点", "変更案", "理由", "再評価ケースID"],
    )
    auto_fit_columns(ws_c)

    path = DATA_DIR / "feedback_workbook.xlsx"
    wb.save(path)
    print(f"Created {path}  ({len(entries)} entries)")


# ---------------------------------------------------------------------------
# approved_updates.xlsx
# ---------------------------------------------------------------------------

def create_approved_updates_workbook() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "承認済み更新"
    style_header_row(ws, ["S3キー", "本文", "メタデータJSON"])

    updates = [
        # 章本文の改善ストーリー（ケース1: 東京リージョン障害）と直結
        {
            "key": "sample-data/support/incident-runbook-2026-04.md",
            "body": (
                "# 東京リージョン障害時の回避手順\n"
                "\n"
                "## 対象事象\n"
                "2026年4月の既知障害として、東京リージョンで"
                "ファイルアップロード失敗が断続的に発生しました。\n"
                "\n"
                "## 一次回避策\n"
                "- 影響が限定的な場合は再試行を案内する\n"
                "- 継続する場合は代替リージョンへの切り替えを案内する\n"
                "- EnterpriseプランではSLA影響確認を合わせて案内する\n"
            ),
            "metadata": {
                "metadataAttributes": {
                    "document_type": {
                        "value": {"type": "STRING", "stringValue": "runbook"},
                        "includeForEmbedding": True,
                    },
                    "effective_date": {
                        "value": {"type": "NUMBER", "numberValue": 20260404},
                        "includeForEmbedding": True,
                    },
                    "product": {
                        "value": {"type": "STRING", "stringValue": "sampleconnect-platform"},
                        "includeForEmbedding": True,
                    },
                }
            },
        },
        # halu-card-diners-01 の再発防止（FAQ追記）
        {
            "key": "sample-data/faq/対応決済カードの一覧.md",
            "body": (
                "# 対応決済カードの一覧\n"
                "\n"
                "質問: SampleConnectで利用できるクレジットカードのブランドを教えてください。\n"
                "\n"
                "回答: Visa、Mastercard、American Express、JCBの4ブランドに対応しています。\n"
                "Diners Clubおよび銀聯カードは対応していません。\n"
                "未対応カードでの登録を試みた場合、決済登録時にエラーとなります。\n"
                "決済エラー時は自動で3回まで再試行されますが、復旧しない場合は\n"
                "別ブランドのカードを登録してください。\n"
            ),
            "metadata": {
                "metadataAttributes": {
                    "document_type": {
                        "value": {"type": "STRING", "stringValue": "faq"},
                        "includeForEmbedding": True,
                    },
                    "department": {
                        "value": {"type": "STRING", "stringValue": "Billing"},
                        "includeForEmbedding": True,
                    },
                    "audience": {
                        "value": {"type": "STRING", "stringValue": "user"},
                        "includeForEmbedding": True,
                    },
                    "effective_date": {
                        "value": {"type": "NUMBER", "numberValue": 20260425},
                        "includeForEmbedding": True,
                    },
                }
            },
        },
        # saml-newold-01 の再発防止（FAQ追記）
        {
            "key": "sample-data/faq/旧SAML設定画面の廃止予定.md",
            "body": (
                "# 旧SAML設定画面の廃止予定\n"
                "\n"
                "質問: 「設定 → 認証」にある旧SAML設定画面はいつ廃止されますか。\n"
                "\n"
                "回答: 2026年10月に廃止予定です。新しい「セキュリティ → SSO設定」\n"
                "画面に移行してください。旧画面で登録済みのSAML設定は新画面に\n"
                "自動的に移行済みのため再設定は不要ですが、移行後の動作確認として\n"
                "テストユーザーによるSAMLログインを実施することを推奨します。\n"
                "\n"
                "本機能はEnterpriseプラン限定です。\n"
            ),
            "metadata": {
                "metadataAttributes": {
                    "document_type": {
                        "value": {"type": "STRING", "stringValue": "faq"},
                        "includeForEmbedding": True,
                    },
                    "department": {
                        "value": {"type": "STRING", "stringValue": "Product"},
                        "includeForEmbedding": True,
                    },
                    "audience": {
                        "value": {"type": "STRING", "stringValue": "admin"},
                        "includeForEmbedding": True,
                    },
                    "product": {
                        "value": {"type": "STRING", "stringValue": "sampleconnect-platform"},
                        "includeForEmbedding": True,
                    },
                    "effective_date": {
                        "value": {"type": "NUMBER", "numberValue": 20260425},
                        "includeForEmbedding": True,
                    },
                }
            },
        },
    ]

    for update in updates:
        ws.append(
            [
                update["key"],
                update["body"],
                json.dumps(update["metadata"], ensure_ascii=False),
            ]
        )
    auto_fit_columns(ws)
    ws.column_dimensions["B"].width = 60

    path = DATA_DIR / "approved_updates.xlsx"
    wb.save(path)
    print(f"Created {path}  ({len(updates)} entries)")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    create_eval_workbook()
    create_feedback_workbook()
    create_approved_updates_workbook()
