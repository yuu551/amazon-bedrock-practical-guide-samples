# 監査ログ管理者ガイド：エクスポートとSIEM連携

SampleConnect の監査ログを管理コンソール、API、SIEM 連携の3経路で取得・分析する手順をまとめます。

## 管理コンソールでの確認

「セキュリティ」→「監査ログ」画面を開きます。フィルタで次の条件を組み合わせて絞り込めます。

- 期間（過去1時間／24時間／7日／30日／カスタム）
- イベントカテゴリ（ユーザー／管理操作／API操作）
- アクター（ユーザーID、メールアドレス）
- ターゲットリソース種別
- 結果（成功／失敗）

絞り込み結果は CSV 形式でダウンロードできます（最大10万行）。それを超える場合はエクスポートAPIを使ってください。

## エクスポートAPI

`GET /v1/admin/audit-logs/export` を使い、JSON Lines 形式でダウンロードできます。

```bash
curl -X POST "https://api.sampleconnect.example/v1/admin/audit-logs/export" \
  -H "Authorization: Bearer $SAMPLECONNECT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "from": "2026-04-01T00:00:00Z",
    "to":   "2026-04-30T23:59:59Z",
    "categories": ["admin_action", "api_write"],
    "format": "ndjson"
  }'
```

レスポンスはエクスポートID（`EXP-XXXX-XXXX`）を返します。10分以内に署名付きS3 URLが管理者宛にメール通知されます。URLの有効期限は24時間です。

エクスポートAPIは1テナントあたり毎日5回までの制限があります（Enterpriseプランは50回）。

## SIEM 連携

SampleConnect は次の SIEM プロダクトとの連携をサポートします。

| SIEM | 接続方式 | 遅延 |
|---|---|---|
| Splunk | HTTP Event Collector（HEC） | 約1分 |
| Datadog | Logs API | 約1分 |
| Sumo Logic | HTTP Source | 約2分 |
| Microsoft Sentinel | Common Event Format（CEF）転送 | 約3分 |

「セキュリティ」→「監査ログ」→「SIEM 設定」から、転送先の SIEM タイプ、エンドポイント、認証トークンを登録します。登録後は監査ログがリアルタイムでSIEMに転送されます。

転送に失敗したログは内部的に再送キューに入れられ、最大72時間まで自動再送します。72時間経過後はキューから削除され、エクスポートAPIから手動取得が必要です。

## 整合性検証

第三者監査機関の整合性検証レポートは、四半期末から1か月以内に管理コンソールに掲載されます。「セキュリティ」→「監査ログ」→「整合性レポート」から PDF をダウンロードできます。

自社で独自に整合性を検証したい場合、エクスポート時に各エントリに付与される `prev_hash` フィールドを連鎖検証してください。検証用のサンプルスクリプトを `tools/audit-verify` リポジトリで公開しています。
