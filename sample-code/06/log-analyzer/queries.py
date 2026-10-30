"""CloudWatch Logs Insights クエリテンプレート。

Bedrock モデル呼び出しログおよび Knowledge Bases APPLICATION_LOGS を
分析するためのクエリ集です。CloudWatch コンソールに直接貼り付けても使えます。

前提:
    - モデル呼び出しログが有効化されていること
    - ロググループ例: /aws/bedrock/modelinvocations
    - Knowledge Bases のロググループ例: /aws/bedrock/knowledgebases/APPLICATION_LOGS
"""

# --------------------------------------------------------------------------
# モデル別集計
# modelId ごとに呼び出し回数、レイテンシ（平均・p90）、トークン数を集計する。
# アプリケーション推論プロファイル を使っていない直接呼び出しの分析に適している。
# --------------------------------------------------------------------------
QUERY_BY_MODEL = """\
filter ispresent(modelId)
| stats
    count(*) as invocations,
    avg(input.inputTokenCount) as avg_input_tokens,
    avg(output.outputTokenCount) as avg_output_tokens,
    sum(input.inputTokenCount) as total_input_tokens,
    sum(output.outputTokenCount) as total_output_tokens,
    avg(output.outputBodyJson.metrics.latencyMs) as avg_latency_ms,
    pct(output.outputBodyJson.metrics.latencyMs, 50) as p50_latency_ms,
    pct(output.outputBodyJson.metrics.latencyMs, 90) as p90_latency_ms,
    pct(output.outputBodyJson.metrics.latencyMs, 99) as p99_latency_ms
    by modelId
| sort invocations desc
"""

# --------------------------------------------------------------------------
# Inference Profile 別集計
# アプリケーション推論プロファイル の ARN ごとに集計する。
# 「どのアプリケーションがどれだけ使っているか」を把握するために使う。
# --------------------------------------------------------------------------
QUERY_BY_PROFILE = """\
filter modelId like /inference-profile/
| stats
    count(*) as invocations,
    avg(input.inputTokenCount) as avg_input_tokens,
    avg(output.outputTokenCount) as avg_output_tokens,
    sum(input.inputTokenCount) as total_input_tokens,
    sum(output.outputTokenCount) as total_output_tokens,
    avg(output.outputBodyJson.metrics.latencyMs) as avg_latency_ms,
    pct(output.outputBodyJson.metrics.latencyMs, 50) as p50_latency_ms,
    pct(output.outputBodyJson.metrics.latencyMs, 90) as p90_latency_ms,
    pct(output.outputBodyJson.metrics.latencyMs, 99) as p99_latency_ms
    by modelId as inferenceProfileId
| sort invocations desc
"""

# --------------------------------------------------------------------------
# 時間帯別集計
# 1時間単位で呼び出し回数・トークン数・レイテンシの推移を可視化する。
# レイテンシの増加傾向やトラフィックのピーク時間帯を特定できる。
# --------------------------------------------------------------------------
QUERY_BY_HOUR = """\
filter ispresent(modelId)
| stats
    count(*) as invocations,
    sum(input.inputTokenCount) as total_input_tokens,
    sum(output.outputTokenCount) as total_output_tokens,
    avg(output.outputBodyJson.metrics.latencyMs) as avg_latency_ms,
    pct(output.outputBodyJson.metrics.latencyMs, 90) as p90_latency_ms,
    sum(ispresent(errorCode)) as error_count
    by bin(1h) as time_bucket
| sort time_bucket asc
"""

# --------------------------------------------------------------------------
# エラーレスポンスの抽出
# HTTP ステータスが 200 以外、またはレスポンスボディにエラーを含む呼び出しを抽出する。
# スロットリング（429）やサーバーエラー（5xx）の発生状況を把握できる。
# --------------------------------------------------------------------------
QUERY_ERRORS = """\
filter errorCode like /./
| stats
    count(*) as error_count
    by errorCode, modelId
| sort error_count desc
"""

# --------------------------------------------------------------------------
# Knowledge Bases 取り込みジョブの結果確認
# APPLICATION_LOGS から取り込みジョブの統計情報を取得する。
# ロググループ: /aws/bedrock/knowledgebases/APPLICATION_LOGS に対して実行する。
# --------------------------------------------------------------------------
QUERY_KB_INGESTION = """\
filter eventType = "INGESTION.DOCUMENT_PROCESSING"
| stats
    sum(documentsScanned) as total_scanned,
    sum(documentsIndexed) as total_indexed,
    sum(documentsFailed) as total_failed,
    sum(documentsMetadataUpdated) as total_metadata_updated
    by knowledgeBaseId, dataSourceId
| sort total_scanned desc
"""
