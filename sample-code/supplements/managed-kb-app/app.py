"""localhost専用のManaged KBストリーミング画面とCLI。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import time

from retrieval import MODES, Settings, run, validate

UI = Path(__file__).parent / "ui" / "dist"


def clients(settings):
    import boto3
    from botocore.config import Config
    runtime = boto3.client("bedrock-agent-runtime", region_name=settings.kb_region,
                           config=Config(connect_timeout=10, read_timeout=180,
                                         retries={"total_max_attempts": 1}))
    llm = boto3.client("bedrock-runtime", region_name=settings.model_region,
                       config=Config(read_timeout=180, retries={"total_max_attempts": 1}))
    return runtime, llm


def demo_events():
    """画面だけを確認する架空イベント。実AWSの測定には使わない。"""
    yield {"type": "status", "message": "画面確認用デモ。AWSへは接続していません。"}
    yield {"type": "trace", "step": "Planning", "status": "Success", "queries": ["監査ログ 保存期間"], "warnings": 0, "failures": 0, "elapsed_seconds": 0.1}
    yield {"type": "sources", "documents": [{"index": 0, "title": "デモ用の架空資料", "source": "", "text": "Enterpriseの監査ログは365日保存します。"}], "has_more_results": False}
    for text in ["Enterpriseの", "監査ログは", "365日保存されます。", "[文書 1]"]:
        time.sleep(0.15)
        yield {"type": "text", "text": text}
    yield {"type": "done", "answer": "Enterpriseの監査ログは365日保存されます。[文書 1]", "citations": [], "model": "画面確認用デモ", "usage": None, "metrics": {}}


def error_event(exc):
    # ベンダーのエラーメッセージに含まれるURLや資料本文をブラウザへ流さない。
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code", type(exc).__name__) if isinstance(response, dict) else type(exc).__name__
    print("リクエスト失敗:", code, flush=True)
    return {"type": "error", "code": code, "message": "処理が完了しませんでした。AWS認証、権限、モデルの利用条件、通信状態を確認してください。表示中の回答は途中の内容です。"}


def serve(settings, port, demo=False):
    if not (UI / "index.html").is_file():
        raise SystemExit("uiディレクトリでpnpm install --frozen-lockfileとpnpm buildを実行してください。")
    runtime, llm = (None, None) if demo else clients(settings)
    gate = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def allowed(self):
            hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in hosts and (not origin or origin in {f"http://{h}" for h in hosts})

        def do_GET(self):
            if not self.allowed():
                self.send_error(403)
                return
            if self.path == "/config":
                body = json.dumps({"demo": demo, "model": settings.model, "kb_region": settings.kb_region}, ensure_ascii=False).encode()
                mime = "application/json"
            else:
                target = (UI / ("index.html" if self.path == "/" else self.path.lstrip("/"))).resolve()
                if not target.is_relative_to(UI.resolve()) or not target.is_file():
                    self.send_error(404)
                    return
                body = target.read_bytes()
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/query" or not self.allowed():
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20000 or self.headers.get_content_type() != "application/json":
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                question, mode = body["question"], body["mode"]
                validate(question, mode)
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return
            if not gate.acquire(blocking=False):
                self.send_error(409, "A request is already running")
                return
            self.close_connection = True
            events = demo_events() if demo else run(settings, question, mode, runtime, llm)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for event in events:
                        self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    self.wfile.write((json.dumps(error_event(exc), ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
            finally:
                events.close()
                gate.release()

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port} ({'画面確認用デモ' if demo else 'AWS接続'})", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if llm:
            llm.close()
        if runtime:
            runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-id", default=os.environ.get("MANAGED_KB_ID", ""))
    parser.add_argument("--kb-region", default="us-east-1")
    parser.add_argument("--model-region")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--question", help="指定すると画面を起動せず、CLIで1件実行する")
    parser.add_argument("--mode", choices=MODES, default="agentic-model")
    parser.add_argument("--output", type=Path, help="CLIのイベント保存先。新規ファイルのみ")
    args = parser.parse_args()
    if not args.demo and not args.kb_id:
        parser.error("--kb-id または MANAGED_KB_ID を設定してください。")
    if args.output and (not args.question or args.demo):
        parser.error("--outputは実AWSへのCLI実行だけで使えます。")
    settings = Settings(args.kb_id, args.kb_region, model_region=args.model_region or args.kb_region)
    if not args.question:
        serve(settings, args.port, args.demo)
        return
    validate(args.question, args.mode)
    sink = None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        sink = args.output.open("x", encoding="utf-8")
    runtime = llm = None
    try:
        runtime, llm = (None, None) if args.demo else clients(settings)
        events = demo_events() if args.demo else run(settings, args.question, args.mode, runtime, llm)
        try:
            for event in events:
                row = {"mode": args.mode, "recorded_at": datetime.now(timezone.utc).isoformat(), **event}
                line = json.dumps(row, ensure_ascii=False)
                print(line, flush=True)
                if sink:
                    sink.write(line + "\n")
                    sink.flush()
        except Exception as exc:
            if sink:
                sink.write(json.dumps(error_event(exc), ensure_ascii=False) + "\n")
            raise
        finally:
            events.close()
    finally:
        if sink: sink.close()
        if llm: llm.close()
        if runtime: runtime.close()


if __name__ == "__main__":
    main()
