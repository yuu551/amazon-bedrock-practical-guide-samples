"""架空の問い合わせをGrokで処理し、Strandsのトレースを記録する。"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class FileExporter(SpanExporter):
    """短いデモ用。架空データを含むspanをローカルへ保存する。"""
    def __init__(self, path: Path):
        self.file = path.open("x", encoding="utf-8")
        self.count = 0

    def export(self, spans):
        for span in spans:
            self.file.write(json.dumps(json.loads(span.to_json()), ensure_ascii=False) + "\n")
            self.count += 1
        self.file.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.file.close()


def langfuse_config(environ):
    """送信先と認証ヘッダーを作る。秘密値は表示しない。"""
    base = environ.get("LANGFUSE_BASE_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise ValueError("LANGFUSE_BASE_URLにはHTTPSの接続先をパスなしで指定してください。")
    public = environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = environ.get("LANGFUSE_SECRET_KEY", "")
    if not public or not secret:
        raise ValueError("LANGFUSE_PUBLIC_KEYとLANGFUSE_SECRET_KEYを設定してください。")
    auth = base64.b64encode(f"{public}:{secret}".encode()).decode()
    return base + "/api/public/otel/v1/traces", {
        "Authorization": "Basic " + auth,
        "x-langfuse-ingestion-version": "4",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Langfuseにも送る。省略時はローカル保存のみ。")
    parser.add_argument("--output", type=Path, default=Path("traces.jsonl"))
    parser.add_argument("--region", default="us-west-2")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("出力先が存在します。--outputで別名を指定してください。")
    remote_config = langfuse_config(os.environ) if args.send else None
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental,gen_ai_span_attributes_only"
    provider = TracerProvider(resource=Resource.create({"service.name": "sampleconnect-langfuse-demo"}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    local = FileExporter(args.output)
    provider.add_span_processor(SimpleSpanProcessor(local))
    if remote_config:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        endpoint, headers = remote_config
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=15)))
    # Agent作成前に設定し、エージェント・モデル・ツールのspanを同じproviderへ集める。
    trace.set_tracer_provider(provider)
    from aws_bedrock_token_generator import provide_token
    from strands import Agent, tool
    from strands.models.openai_responses import OpenAIResponsesModel

    @tool
    def lookup_support_plan(plan: str) -> dict:
        """プラン名に対応するサポート条件を架空の表から返す。

        Args:
            plan: EnterpriseまたはStandard。
        """
        plans = {"Enterprise": {"initial_response_hours": 1, "audit_log_days": 365},
                 "Standard": {"initial_response_hours": 24, "audit_log_days": 30}}
        return plans.get(plan, {"error": "該当するプランがありません。"})

    model = OpenAIResponsesModel(
        model_id="xai.grok-4.6",
        client_args={"base_url": f"https://bedrock-mantle.{args.region}.api.aws/openai/v1",
                     "api_key": provide_token(region=args.region), "timeout": 120, "max_retries": 0},
        params={"reasoning": {"effort": "low"}, "max_output_tokens": 2048},
    )
    agent = Agent(model=model, tools=[lookup_support_plan], callback_handler=None,
                  system_prompt="サポート条件は必ずlookup_support_planで確認し、結果から日本語で簡潔に答えてください。")
    try:
        with trace.get_tracer("sampleconnect.supplement").start_as_current_span("support-inquiry") as root:
            root.set_attribute("sampleconnect.case_id", "support-plan-01")
            print("trace_id:", format(root.get_span_context().trace_id, "032x"))
            answer = agent("Enterpriseプランの初動対応時間と監査ログの保存期間を教えてください。")
            print(answer)
        provider.force_flush(timeout_millis=30000)
        print(f"ローカル記録: {local.count} spans → {args.output}")
        if args.send:
            print("送信処理を終了しました。エラーログとLangfuseの画面で受信を確認してください。")
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
