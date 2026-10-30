"""
Managed Knowledge Base 複数KB横断検索の検証
S3専用KBとConfluence専用KBを別々に作成し、
Agentic RetrieveStreamで横断検索する。

使い方:
  # 新規作成から横断検索まで一括実行
  uv run python managed_kb_cross_search.py --create \
    --role-arn <Knowledge BaseサービスロールARN> \
    --s3-bucket <データバケット> \
    --confluence-secret-arn <Secrets Manager ARN> \
    --confluence-host https://your-instance.atlassian.net \
    --confluence-space <space-key> \
    --cleanup

  # 既存KBで横断検索のみ実行
  S3_KB_ID=xxx CONFLUENCE_KB_ID=yyy uv run python managed_kb_cross_search.py

  # クリーンアップ
  S3_KB_ID=xxx CONFLUENCE_KB_ID=yyy uv run python managed_kb_cross_search.py --cleanup
"""

import argparse
import json
import os
import time
import urllib.parse
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "20260622-cross-search")


def wait_kb_active(agent_client: Any, kb_id: str, name: str, timeout: int = 120):
    for i in range(timeout // 5):
        resp = agent_client.get_knowledge_base(knowledgeBaseId=kb_id)
        if resp["knowledgeBase"]["status"] == "ACTIVE":
            print(f"  {name} KB: ACTIVE")
            return
        time.sleep(5)
    raise TimeoutError(f"{name} KB did not become ACTIVE")


def wait_ingestion(
    agent_client: Any,
    kb_id: str,
    ds_id: str,
    job_id: str,
    name: str,
    timeout: int = 300,
):
    for i in range(timeout // 5):
        job = agent_client.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )
        status = job["ingestionJob"]["status"]
        if status == "COMPLETE":
            stats = job["ingestionJob"].get("statistics", {})
            print(
                f"  {name}: COMPLETE  scanned={stats.get('numberOfDocumentsScanned', '?')}"
                f"  indexed={stats.get('numberOfNewDocumentsIndexed', '?')}"
                f"  failed={stats.get('numberOfDocumentsFailed', '?')}"
            )
            if stats.get("numberOfDocumentsFailed") != 0:
                raise RuntimeError(f"{name}: completed with failed documents: {job['ingestionJob']}")
            return stats
        elif status == "FAILED":
            raise RuntimeError(
                f"{name}: FAILED: {job['ingestionJob'].get('failureReasons', [])}"
            )
        if i % 6 == 0:
            print(f"  {name}: {status} ({(i+1)*5}s)")
        time.sleep(5)
    raise TimeoutError(f"{name} ingestion timed out")


def create_kbs(
    agent_client: Any,
    *,
    account_id: str,
    role_arn: str,
    s3_bucket: str,
    confluence_secret_arn: str,
    confluence_host: str,
    confluence_space: str,
    name_suffix: str = "",
    cleanup_on_failure: bool = False,
) -> tuple[str, str]:
    suffix = f"-{name_suffix}" if name_suffix else ""
    s3_kb_id: str | None = None
    conf_kb_id: str | None = None
    try:
        print("=== S3専用KB作成 ===")
        s3_kb = agent_client.create_knowledge_base(
            name=f"sampleconnect-s3-only-kb{suffix}",
            description="SampleConnect Platform S3 FAQ専用",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "MANAGED",
                "managedKnowledgeBaseConfiguration": {"embeddingModelType": "MANAGED"},
            },
        )
        s3_kb_id = s3_kb["knowledgeBase"]["knowledgeBaseId"]
        print(f"  KB ID: {s3_kb_id}")

        print("\n=== Confluence専用KB作成 ===")
        conf_kb = agent_client.create_knowledge_base(
            name=f"sampleconnect-confluence-only-kb{suffix}",
            description="SampleConnect Platform Confluence運用メモ専用",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "MANAGED",
                "managedKnowledgeBaseConfiguration": {
                    "embeddingModelType": "MANAGED"
                },
            },
        )
        conf_kb_id = conf_kb["knowledgeBase"]["knowledgeBaseId"]
        print(f"  KB ID: {conf_kb_id}")

        print("\nKBのACTIVE待ち...")
        wait_kb_active(agent_client, s3_kb_id, "S3")
        wait_kb_active(agent_client, conf_kb_id, "Confluence")

        print("\n=== S3データソース追加 ===")
        s3_ds = agent_client.create_data_source(
            knowledgeBaseId=s3_kb_id,
            name="s3-sample-data",
            dataSourceConfiguration={
                "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                "managedKnowledgeBaseConnectorConfiguration": {
                    "deletionProtectionConfiguration": {
                        "deletionProtectionStatus": "DISABLED",
                    },
                    "connectorParameters": {
                        "type": "S3",
                        "version": "1",
                        "connectionConfiguration": {
                            "bucketName": s3_bucket,
                            "bucketOwnerAccountId": account_id,
                        },
                        "inclusionPrefixes": [
                            "sample-data/faq/",
                            "sample-data/manuals/",
                            "sample-data/policies/",
                            "sample-data/product/",
                        ],
                    },
                },
            },
            dataDeletionPolicy="DELETE",
        )
        s3_ds_id = s3_ds["dataSource"]["dataSourceId"]

        print("\n=== Confluenceデータソース追加 ===")
        conf_ds = agent_client.create_data_source(
            knowledgeBaseId=conf_kb_id,
            name="confluence-sampleteam",
            dataSourceConfiguration={
                "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                "managedKnowledgeBaseConnectorConfiguration": {
                    "connectorParameters": {
                        "type": "CONFLUENCE",
                        "version": "1",
                        "aclEnabled": False,
                        "connectionConfiguration": {
                            "secretArn": confluence_secret_arn,
                            "type": "SAAS",
                            "authType": "BASIC",
                            "hostUrl": confluence_host,
                        },
                        "dataEntityConfiguration": {
                            "crawlPage": True,
                            "crawlBlog": False,
                            "crawlPageAttachment": True,
                            "crawlBlogAttachment": False,
                        },
                        "filterConfiguration": {
                            "inclusionSpaceKeys": [confluence_space],
                        },
                    },
                },
            },
            dataDeletionPolicy="DELETE",
        )
        conf_ds_id = conf_ds["dataSource"]["dataSourceId"]

        print("\n=== 同期開始 ===")
        s3_job = agent_client.start_ingestion_job(
            knowledgeBaseId=s3_kb_id, dataSourceId=s3_ds_id
        )
        conf_job = agent_client.start_ingestion_job(
            knowledgeBaseId=conf_kb_id, dataSourceId=conf_ds_id
        )
        wait_ingestion(
            agent_client,
            s3_kb_id,
            s3_ds_id,
            s3_job["ingestionJob"]["ingestionJobId"],
            "S3",
        )
        wait_ingestion(
            agent_client,
            conf_kb_id,
            conf_ds_id,
            conf_job["ingestionJob"]["ingestionJobId"],
            "Confluence",
        )

        return s3_kb_id, conf_kb_id
    except Exception:
        if cleanup_on_failure and (s3_kb_id or conf_kb_id):
            cleanup(agent_client, s3_kb_id, conf_kb_id)
        raise


def run_cross_search(runtime: Any, s3_kb_id: str, conf_kb_id: str):
    queries = [
        {
            "id": "CS-1",
            "question": "Enterpriseプランの顧客から東京リージョンのファイルアップロード障害の"
            "報告がありました。SLAのサービスクレジット条件と、"
            "障害一次対応の手順を教えてください。",
            "expect": "S3（SLAポリシー・障害手順）＋Confluence（ポストモーテム）",
        },
        {
            "id": "CS-2",
            "question": "SAML SSOでログインできない場合の確認手順と、"
            "過去にSAML属性マッピングで問題が起きた事例を教えてください。",
            "expect": "S3（SAML FAQ）＋Confluence（Entra ID記事）",
        },
        {
            "id": "CS-3",
            "question": "HTTP 502 Bad Gatewayの暫定回避策を教えてください。",
            "expect": "Confluence（既知障害ページ）",
        },
    ]

    all_results = []

    for q in queries:
        print(f"\n{'='*80}")
        print(f"[{q['id']}] {q['question'][:60]}...")
        print(f"期待: {q['expect']}")

        response = runtime.agentic_retrieve_stream(
            messages=[{"content": {"text": q["question"]}, "role": "user"}],
            retrievers=[
                {
                    "description": "SampleConnect Platform サポート文書（FAQ・マニュアル・ポリシー）",
                    "configuration": {
                        "knowledgeBase": {"knowledgeBaseId": s3_kb_id}
                    },
                },
                {
                    "description": "SampleConnect Platform Confluence運用メモ（障害対応・ナレッジ）",
                    "configuration": {
                        "knowledgeBase": {"knowledgeBaseId": conf_kb_id}
                    },
                },
            ],
            agenticRetrieveConfiguration={
                "foundationModelType": "MANAGED",
                "rerankingModelType": "MANAGED",
            },
            generateResponse=True,
        )

        answer = ""
        traces = []
        sources = []

        for event in response["stream"]:
            if "traceEvent" in event:
                attrs = event["traceEvent"].get("attributes", {})
                step = attrs.get("step", "?")
                status = attrs.get("status", "?")
                traces.append({"step": step, "status": status})
            elif "responseEvent" in event:
                answer += event["responseEvent"].get("text", "")
            elif "result" in event:
                for r in event["result"].get("results", []):
                    loc = r.get("location", {})
                    loc_type = loc.get("type", "")
                    score = r.get("score", 0)
                    if loc_type == "S3":
                        uri = loc.get("s3Location", {}).get("uri", "?")
                        fname = urllib.parse.unquote(uri.split("/")[-1])
                        sources.append(
                            {"type": "S3", "file": fname, "score": round(score, 4)}
                        )
                    elif loc_type == "CONFLUENCE":
                        url = loc.get("confluenceLocation", {}).get("url", "?")
                        sources.append(
                            {"type": "Confluence", "url": url, "score": round(score, 4)}
                        )

        s3_sources = [s for s in sources if s["type"] == "S3"]
        conf_sources = [s for s in sources if s["type"] == "Confluence"]

        print(f"\nトレース: {len(traces)}ステップ")
        for t in traces:
            print(f"  [{t['step']}] {t['status']}")

        print(f"\n引用: S3={len(s3_sources)}件, Confluence={len(conf_sources)}件")
        for s in s3_sources[:3]:
            print(f"  [S3] {s['file']} (score={s['score']})")
        for s in conf_sources[:3]:
            url_short = s["url"].split("/")[-1] if "/" in s["url"] else s["url"]
            print(f"  [Confluence] {url_short} (score={s['score']})")

        print(f"\n回答（先頭500文字）:")
        print(answer[:500])

        all_results.append(
            {
                "id": q["id"],
                "question": q["question"],
                "expect": q["expect"],
                "traces": traces,
                "answer": answer,
                "sources": {"s3": s3_sources, "confluence": conf_sources},
            }
        )

    return all_results


def _is_not_found(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException"


def _wait_data_source_deleted(
    agent_client: Any, kb_id: str, ds_id: str, timeout: int = 300
) -> None:
    for _ in range(timeout // 5):
        try:
            agent_client.get_data_source(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id,
            )
        except ClientError as exc:
            if _is_not_found(exc):
                return
            raise
        time.sleep(5)
    raise TimeoutError(f"Data source {ds_id} did not finish deleting")


def _wait_kb_deleted(agent_client: Any, kb_id: str, timeout: int = 300) -> None:
    for _ in range(timeout // 5):
        try:
            agent_client.get_knowledge_base(knowledgeBaseId=kb_id)
        except ClientError as exc:
            if _is_not_found(exc):
                return
            raise
        time.sleep(5)
    raise TimeoutError(f"Knowledge Base {kb_id} did not finish deleting")


def _list_all_data_sources(agent_client: Any, kb_id: str) -> list[dict[str, Any]]:
    """ページ分割されたデータソース一覧を全件返す。"""
    summaries: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        request: dict[str, Any] = {"knowledgeBaseId": kb_id}
        if next_token:
            request["nextToken"] = next_token
        response = agent_client.list_data_sources(**request)
        summaries.extend(response.get("dataSourceSummaries", []))
        next_token = response.get("nextToken")
        if not next_token:
            return summaries


def cleanup(
    agent_client: Any,
    s3_kb_id: str | None,
    conf_kb_id: str | None,
) -> None:
    print("=== クリーンアップ ===")
    for kb_id, name in [
        (s3_kb_id, "S3"),
        (conf_kb_id, "Confluence"),
    ]:
        if not kb_id:
            continue
        try:
            data_sources = _list_all_data_sources(agent_client, kb_id)
        except ClientError as exc:
            if _is_not_found(exc):
                continue
            raise
        for ds in data_sources:
            print(f"  Deleting DS {ds['dataSourceId']} from {name} KB...")
            agent_client.delete_data_source(
                knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"]
            )
            _wait_data_source_deleted(agent_client, kb_id, ds["dataSourceId"])
        print(f"  Deleting {name} KB {kb_id}...")
        agent_client.delete_knowledge_base(knowledgeBaseId=kb_id)
        _wait_kb_deleted(agent_client, kb_id)
    print("クリーンアップ完了")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Managed Knowledge Baseを2つ作成し、横断検索する"
    )
    parser.add_argument(
        "--create", action="store_true", help="2つのKBとデータソースを作成"
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="対象のKBとデータソースを削除"
    )
    parser.add_argument(
        "--force-cleanup",
        action="store_true",
        help="既存KB IDを--cleanupで削除することを明示的に許可",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWSリージョン（AWS_REGIONでも指定可）",
    )
    parser.add_argument(
        "--role-arn",
        default=os.environ.get("MANAGED_KB_ROLE_ARN"),
        help="KBサービスロールARN（MANAGED_KB_ROLE_ARNでも指定可）",
    )
    parser.add_argument(
        "--s3-bucket",
        default=os.environ.get("S3_BUCKET"),
        help="S3データバケット（S3_BUCKETでも指定可）",
    )
    parser.add_argument(
        "--confluence-secret-arn",
        default=os.environ.get("CONFLUENCE_SECRET_ARN"),
        help="Confluence認証情報のSecret ARN（CONFLUENCE_SECRET_ARNでも指定可）",
    )
    parser.add_argument(
        "--confluence-host",
        default=os.environ.get("CONFLUENCE_HOST"),
        help="ConfluenceホストURL（CONFLUENCE_HOSTでも指定可）",
    )
    parser.add_argument(
        "--confluence-space",
        default=os.environ.get("CONFLUENCE_SPACE"),
        help="Confluenceスペースキー（CONFLUENCE_SPACEでも指定可）",
    )
    parser.add_argument(
        "--name-suffix",
        default=os.environ.get("MANAGED_KB_NAME_SUFFIX", ""),
        help="既存KBとの名前衝突を避ける任意のサフィックス",
    )
    parser.add_argument(
        "--s3-kb-id",
        default=os.environ.get("S3_KB_ID"),
        help="既存S3 KB ID（S3_KB_IDでも指定可）",
    )
    parser.add_argument(
        "--confluence-kb-id",
        default=os.environ.get("CONFLUENCE_KB_ID"),
        help="既存Confluence KB ID（CONFLUENCE_KB_IDでも指定可）",
    )
    parser.add_argument("--log-dir", default=LOG_DIR, help="横断検索結果の出力先")
    return parser.parse_args(argv)


def _require_create_settings(args: argparse.Namespace) -> None:
    required = {
        "--role-arn / MANAGED_KB_ROLE_ARN": args.role_arn,
        "--s3-bucket / S3_BUCKET": args.s3_bucket,
        "--confluence-secret-arn / CONFLUENCE_SECRET_ARN": args.confluence_secret_arn,
        "--confluence-host / CONFLUENCE_HOST": args.confluence_host,
        "--confluence-space / CONFLUENCE_SPACE": args.confluence_space,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(
            "--create に必要な設定が未指定です: " + ", ".join(missing)
        )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.create:
        # 不足をAWS API呼び出しより前に報告する。
        _require_create_settings(args)
    elif not args.s3_kb_id or not args.confluence_kb_id:
        raise SystemExit(
            "--s3-kb-idと--confluence-kb-idを指定するか、"
            "S3_KB_IDとCONFLUENCE_KB_IDを設定するか、--createを使ってください"
        )
    if args.cleanup and not args.create and not args.force_cleanup:
        raise SystemExit(
            "既存のKnowledge Baseを削除するには"
            "--force-cleanupを追加してください"
        )

    session = boto3.Session(region_name=args.region)
    agent_client = session.client("bedrock-agent")

    if args.create:
        account_id = session.client("sts").get_caller_identity()["Account"]
        s3_kb_id, conf_kb_id = create_kbs(
            agent_client,
            account_id=account_id,
            role_arn=args.role_arn,
            s3_bucket=args.s3_bucket,
            confluence_secret_arn=args.confluence_secret_arn,
            confluence_host=args.confluence_host,
            confluence_space=args.confluence_space,
            name_suffix=args.name_suffix,
            cleanup_on_failure=args.cleanup,
        )
    else:
        s3_kb_id = args.s3_kb_id
        conf_kb_id = args.confluence_kb_id

    if args.cleanup and not args.create:
        cleanup(agent_client, s3_kb_id, conf_kb_id)
        return

    try:
        runtime = session.client("bedrock-agent-runtime")
        results = run_cross_search(runtime, s3_kb_id, conf_kb_id)

        os.makedirs(args.log_dir, exist_ok=True)
        log_path = os.path.join(args.log_dir, "cross-search-results.json")
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "s3_kb_id": s3_kb_id,
                    "confluence_kb_id": conf_kb_id,
                    "results": results,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\nログ保存: {log_path}")
    finally:
        if args.cleanup:
            cleanup(agent_client, s3_kb_id, conf_kb_id)


if __name__ == "__main__":
    main()
