"""
Managed Knowledge Base フルテスト
- KB作成 → データソース接続 → 同期 → Standard Retrieve → Agentic Retrieve → Strands Agent

boto3 1.43.32 で Managed KB のサービスモデル（type: MANAGED、AgenticRetrieveStream、
managedSearchConfiguration 等）が正式に入ったため、すべて boto3 ネイティブで呼び出す。
"""

import boto3
import json
import time
import os
import secrets
import sys
import urllib.parse
from botocore.exceptions import ClientError

REGION = "us-east-1"
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]
RESOURCE_SUFFIX = os.environ.get("RESOURCE_SUFFIX", secrets.token_hex(3))
BUCKET_NAME = f"managed-kb-book-test-{ACCOUNT_ID}-{RESOURCE_SUFFIX}"
ROLE_NAME = f"AmazonBedrockManagedKBRole-{RESOURCE_SUFFIX}"
KB_NAME = f"sampleconnect-managed-kb-{RESOURCE_SUFFIX}"
SAMPLE_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "02", "sample-data")

iam = boto3.client("iam")
s3 = boto3.client("s3", region_name=REGION)
agent_client = boto3.client("bedrock-agent", region_name=REGION)
runtime_client = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ──────────────────────────────────────────────
# 1. IAMロール
# ──────────────────────────────────────────────

def create_iam_role():
    print("\n=== 1. IAMロールの作成 ===")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:knowledge-base/*"},
            },
        }],
    }
    try:
        role = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))
        arn = role["Role"]["Arn"]
        print(f"  作成: {arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{ROLE_NAME}"
        print(f"  既存: {arn}")

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
             "Resource": [f"arn:aws:s3:::{BUCKET_NAME}", f"arn:aws:s3:::{BUCKET_NAME}/*"]},
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             "Resource": "*"},
        ],
    }
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="ManagedKBAccess", PolicyDocument=json.dumps(policy))
    time.sleep(10)
    return arn


# ──────────────────────────────────────────────
# 2. S3
# ──────────────────────────────────────────────

def setup_s3():
    print("\n=== 2. S3バケットとデータ ===")
    try:
        s3.create_bucket(Bucket=BUCKET_NAME)
        print(f"  作成: {BUCKET_NAME}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"  既存: {BUCKET_NAME}")

    uploaded_count = 0
    document_count = 0
    for root, _, files in os.walk(SAMPLE_DATA_DIR):
        for f in files:
            relative = os.path.relpath(os.path.join(root, f), SAMPLE_DATA_DIR)
            is_xlsx = relative == os.path.join("faq", "source", "faq.xlsx")
            if is_xlsx:
                continue
            local = os.path.join(root, f)
            key = "sample-data/" + relative
            s3.upload_file(local, BUCKET_NAME, key)
            uploaded_count += 1
            if f.endswith(".md"):
                document_count += 1
    if document_count != 33:
        raise RuntimeError(f"Markdown文書数が想定と異なります: {document_count}")
    print(
        f"  Markdown corpus={document_count}文書、関連ファイル={uploaded_count}件"
        " アップロード完了（faq.xlsxは除外）"
    )


# ──────────────────────────────────────────────
# 3. Managed Knowledge Base 作成（boto3ネイティブ）
# ──────────────────────────────────────────────

def create_managed_kb(role_arn):
    print("\n=== 3. Managed Knowledge Baseの作成 ===")
    resp = agent_client.create_knowledge_base(
        name=KB_NAME,
        description="SampleConnect Platform サポート文書 (Managed Knowledge Base テスト)",
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            "type": "MANAGED",
            "managedKnowledgeBaseConfiguration": {
                "embeddingModelType": "MANAGED",
            },
        },
    )
    kb = resp["knowledgeBase"]
    kb_id = kb["knowledgeBaseId"]
    print(f"  作成開始: {kb_id} (status={kb['status']})")

    for _ in range(60):
        time.sleep(5)
        st = agent_client.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"]
        if st == "ACTIVE":
            print(f"  ACTIVE: {kb_id}")
            return kb_id
        if st == "FAILED":
            sys.exit("  FAILED")
        print(f"  ... {st}")
    sys.exit("  タイムアウト")


# ──────────────────────────────────────────────
# 4. データソース & 同期（boto3ネイティブ）
# ──────────────────────────────────────────────

def create_datasource_and_ingest(kb_id):
    print("\n=== 4. データソース & 同期 ===")
    connector_parameters = {
        "type": "S3",
        "version": "1",
        "connectionConfiguration": {
            "bucketName": BUCKET_NAME,
            "bucketOwnerAccountId": ACCOUNT_ID,
        },
        "inclusionPrefixes": ["sample-data/"],
        "exclusionPrefixes": ["sample-data/faq/source/"],
    }
    ds = agent_client.create_data_source(
        knowledgeBaseId=kb_id,
        name="s3-sample-data",
        dataSourceConfiguration={
            "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
            "managedKnowledgeBaseConnectorConfiguration": {
                "deletionProtectionConfiguration": {
                    "deletionProtectionStatus": "DISABLED",
                },
                "connectorParameters": connector_parameters,
            },
        },
        dataDeletionPolicy="DELETE",
    )
    ds_id = ds["dataSource"]["dataSourceId"]
    print(f"  データソース作成開始: {ds_id}")

    for _ in range(60):
        current = agent_client.get_data_source(
            knowledgeBaseId=kb_id, dataSourceId=ds_id
        )["dataSource"]
        status = current["status"]
        if status == "AVAILABLE":
            print(f"  データソース AVAILABLE: {ds_id}")
            break
        if status == "FAILED":
            sys.exit(f"  データソース作成失敗: {current}")
        print(f"  ... data source {status}")
        time.sleep(5)
    else:
        sys.exit("  データソース作成タイムアウト")

    ingest = agent_client.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    job_id = ingest["ingestionJob"]["ingestionJobId"]
    print(f"  同期開始: {job_id}")

    for _ in range(120):
        time.sleep(5)
        j = agent_client.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )["ingestionJob"]
        st = j["status"]
        if st == "COMPLETE":
            stats = j.get("statistics", {})
            print(f"  完了: scanned={stats.get('numberOfDocumentsScanned','?')} indexed={stats.get('numberOfNewDocumentsIndexed','?')} failed={stats.get('numberOfDocumentsFailed','?')}")
            expected = 33
            if stats.get("numberOfDocumentsFailed") != 0:
                sys.exit(f"  失敗文書あり: {j}")
            if stats.get("numberOfDocumentsScanned") != expected:
                sys.exit(f"  文書数不一致: expected={expected}, job={j}")
            return ds_id
        if st == "FAILED":
            sys.exit(f"  失敗: {j}")
        print(f"  ... {st}")
    sys.exit("  タイムアウト")


# ──────────────────────────────────────────────
# 5. Standard Retrieve（boto3ネイティブ）
# ──────────────────────────────────────────────

QUESTIONS = [
    "SAML SSOでログインできない場合の確認項目を教えてください",
    "サービスクレジットの申請期限はいつですか",
    "障害発生時の顧客向け初回案内のテンプレートを教えてください",
    "APIレート制限に達した場合、どうなりますか",
    "監査ログの保持期間は何日間ですか",
    "Enterpriseプランで利用できる機能を教えてください",
    "ログインできない",
    "認証が通らなくて画面が戻される",
]


def retrieve_managed(kb_id, query, reranking="MANAGED", n=5):
    """Managed Knowledge Base の Retrieve を boto3 ネイティブで呼ぶ"""
    resp = runtime_client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "managedSearchConfiguration": {
                "numberOfResults": n,
                "rerankingModelType": reranking,
            }
        },
    )
    results = []
    for r in resp.get("retrievalResults", []):
        uri = r.get("location", {}).get("s3Location", {}).get("uri", "?")
        fname = urllib.parse.unquote(uri.split("/")[-1]) if "/" in uri else uri
        results.append({
            "file": fname,
            "score": round(r.get("score", 0), 4),
            "uri": uri,
            "text": r.get("content", {}).get("text", ""),
        })
    return results


def format_retrieval_results_for_agent(results):
    """検索結果を、出典番号とチャンク本文を含むツール出力へ変換する。"""
    snippets = []
    for result in results:
        text = result.get("text", "").strip()
        if not text:
            continue
        source_number = len(snippets) + 1
        snippets.append(
            f"[{source_number}] {result['file']}\n"
            f"URI: {result['uri']}\n"
            f"スコア: {result['score']:.4f}\n"
            f"本文:\n{text}"
        )
    return "\n\n".join(snippets)


def test_standard_retrieve(kb_id, reranking="MANAGED"):
    label = "リランキングあり" if reranking == "MANAGED" else "リランキングなし"
    print(f"\n=== 5. Standard Retrieve ({label}) ===")
    all_results = []
    for q in QUESTIONS:
        results = retrieve_managed(kb_id, q, reranking)
        all_results.append({"question": q, "results": results})
        print(f"  Q: {q[:50]}")
        for i, r in enumerate(results[:3]):
            print(f"    [{i+1}] {r['score']:.4f}  {r['file'][:50]}")
    return all_results


# ──────────────────────────────────────────────
# 6. Agentic Retrieve（boto3ネイティブ）
# ──────────────────────────────────────────────

def test_agentic_retrieve(kb_id):
    print("\n=== 6. Agentic Retrieve ===")
    test_qs = [
        "SAML SSOでログインできない場合の確認項目を教えてください",
        "サービスクレジットの申請期限はいつですか。EnterpriseプランのSLA条件も合わせて教えてください。",
        "障害発生時に顧客向けの初回案内を送る手順と、エスカレーション先を教えてください",
    ]
    for q in test_qs:
        print(f"\n  Q: {q}")
        try:
            resp = runtime_client.agentic_retrieve_stream(
                messages=[{"content": {"text": q}, "role": "user"}],
                retrievers=[{
                    "description": "SampleConnect Platform サポート文書",
                    "configuration": {"knowledgeBase": {"knowledgeBaseId": kb_id}},
                }],
                agenticRetrieveConfiguration={
                    "foundationModelType": "MANAGED",
                    "rerankingModelType": "MANAGED",
                },
                generateResponse=True,
            )
            answer_parts = []
            trace_count = 0
            for event in resp.get("stream", []):
                if "responseEvent" in event:
                    text = event["responseEvent"].get("text", "")
                    if text:
                        answer_parts.append(text)
                if "traceEvent" in event:
                    trace_count += 1

            answer = "".join(answer_parts)
            print(f"  → トレース: {trace_count}回")
            if answer:
                print(f"  → 回答抜粋: {answer[:300]}...")
            else:
                print(f"  → 回答テキスト取得なし（ストリーム形式の差異の可能性）")
        except Exception as e:
            print(f"  → ERROR: {e}")


# ──────────────────────────────────────────────
# 7. Customer-managed KB との比較
# ──────────────────────────────────────────────

def compare_with_customer_managed(managed_kb_id, customer_kb_id):
    print("\n=== 7. Customer-managed KB との比較 ===")

    cases = [
        ("SAML SSOでログインできない場合の確認項目を教えてください", "SAML_SSOでログインできない場合の確認項目.md"),
        ("サービスクレジットの申請期限はいつですか", "サービスクレジットの申請期限.md"),
        (
            "障害発生時の顧客向け初回案内のテンプレートを教えてください",
            "障害発生時の顧客向け初回案内.md",
        ),
        ("APIレート制限に達した場合、どうなりますか", "APIレート制限に達した場合の挙動.md"),
        ("ログインできない", "SAML_SSOでログインできない場合の確認項目.md"),
    ]
    m_hit = c_hit = 0
    print(f"\n  {'質問':<35} | {'期待':<20} | {'Managed':<30} | {'Customer-managed':<30}")
    print(f"  {'-'*115}")

    for q, expected in cases:
        mr = retrieve_managed(managed_kb_id, q)
        mf = mr[0]["file"] if mr else "N/A"
        ms = mr[0]["score"] if mr else 0
        m_ok = expected in mf
        if m_ok: m_hit += 1

        try:
            cr = runtime_client.retrieve(
                knowledgeBaseId=customer_kb_id,
                retrievalQuery={"text": q},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": 5,
                        "overrideSearchType": "SEMANTIC",
                    }
                },
            )
            ci = cr.get("retrievalResults", [])
            cf = urllib.parse.unquote(ci[0]["location"]["s3Location"]["uri"].split("/")[-1]) if ci else "N/A"
            cs = ci[0].get("score", 0) if ci else 0
        except Exception as e:
            cf, cs = f"ERR:{e}"[:25], 0
        c_ok = expected in cf
        if c_ok: c_hit += 1

        short_q = (q[:33] + "..") if len(q) > 35 else q
        short_e = (expected[:18] + "..") if len(expected) > 20 else expected
        m_mark = " ✓" if m_ok else ""
        c_mark = " ✓" if c_ok else ""
        print(f"  {short_q:<35} | {short_e:<20} | [{ms:.4f}] {mf[:20]}{m_mark:<5} | [{cs:.4f}] {cf[:20]}{c_mark}")

    print(
        "\n  最初に取得した文書が期待文書だった質問: "
        f"Managed={m_hit}/{len(cases)}  Customer-managed={c_hit}/{len(cases)}"
    )


# ──────────────────────────────────────────────
# 8. Strands Agent
# ──────────────────────────────────────────────

def test_strands_agent(kb_id):
    print("\n=== 8. Strands Agent ===")
    from strands import Agent, tool
    from strands.models import BedrockModel

    @tool
    def search_managed_kb(query: str) -> str:
        """SampleConnect Platformサポートの Managed Knowledge Base 検索ツール。
        製品FAQ・設定ガイド・障害一次対応手順・SLA/課金/セキュリティポリシーから根拠候補を取得します。"""
        results = retrieve_managed(kb_id, query)
        if not results:
            return "該当する情報が見つかりませんでした。"
        context = format_retrieval_results_for_agent(results)
        if not context:
            return "検索結果は見つかりましたが、回答に使える本文を取得できませんでした。"
        return context

    model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-6", region_name=REGION)
    agent = Agent(
        model=model,
        tools=[search_managed_kb],
        system_prompt=(
            "あなたはSampleConnect Platformの問い合わせ対応アシスタントです。"
            "検索ツールが返すチャンク本文だけを根拠に回答し、根拠の末尾に[1]のような"
            "出典番号を付けてください。"
            "検索結果に根拠がない質問には「社内ナレッジに該当する情報がありません」と答えてください。"
        ),
    )

    for q in [
        "SAML SSOでログインできない場合の確認項目を教えてください",
        "サービスクレジットの申請期限と手続き方法を教えてください",
        "AWSの最新ニュースを教えてください",
    ]:
        print(f"\n  Q: {q}")
        try:
            result = agent(q)
            ans = str(result)
            print(f"  A: {ans[:400]}{'...' if len(ans)>400 else ''}")
        except Exception as e:
            print(f"  ERROR: {e}")


# ──────────────────────────────────────────────
# 9. クリーンアップ
# ──────────────────────────────────────────────

def delete_or_confirm_absent(label, delete_fn):
    try:
        delete_fn()
        print(f"  {label} 削除リクエスト送信済み")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in {
            "ResourceNotFoundException", "NoSuchEntity", "NoSuchBucket"
        }:
            print(f"  {label} はすでに存在しません")
            return
        raise


def wait_until_absent(label, get_fn, attempts=120, interval=5):
    for _ in range(attempts):
        try:
            get_fn()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {
                "ResourceNotFoundException", "NoSuchEntity", "NoSuchBucket"
            }:
                print(f"  {label} 削除確認済み")
                return
            raise
        time.sleep(interval)
    raise TimeoutError(f"{label} deletion timed out")


def cleanup(kb_id, ds_id):
    print("\n=== 9. クリーンアップ ===")
    delete_or_confirm_absent(
        "DS",
        lambda: agent_client.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id),
    )
    wait_until_absent(
        "DS",
        lambda: agent_client.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id),
    )
    delete_or_confirm_absent(
        "KB",
        lambda: agent_client.delete_knowledge_base(knowledgeBaseId=kb_id),
    )
    wait_until_absent(
        "KB",
        lambda: agent_client.get_knowledge_base(knowledgeBaseId=kb_id),
    )

    try:
        objs = s3.list_objects_v2(Bucket=BUCKET_NAME)
        for o in objs.get("Contents", []):
            s3.delete_object(Bucket=BUCKET_NAME, Key=o["Key"])
        s3.delete_bucket(Bucket=BUCKET_NAME)
        print("  S3 削除OK")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in {"NoSuchBucket", "404"}:
            raise
        print("  S3 はすでに存在しません")

    delete_or_confirm_absent(
        "IAM inline policy",
        lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName="ManagedKBAccess"),
    )
    delete_or_confirm_absent("IAM role", lambda: iam.delete_role(RoleName=ROLE_NAME))
    wait_until_absent("IAM role", lambda: iam.get_role(RoleName=ROLE_NAME))


# ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Managed Knowledge Base フルテスト")
    p.add_argument("--skip-create", action="store_true")
    p.add_argument("--kb-id")
    p.add_argument("--ds-id")
    p.add_argument("--customer-kb-id", help="比較用Customer-managed KB ID")
    p.add_argument("--skip-runtime-tests", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--cleanup-only", action="store_true")
    args = p.parse_args()

    if args.cleanup_only:
        if not args.kb_id or not args.ds_id:
            p.error("--cleanup-only requires --kb-id and --ds-id")
        cleanup(args.kb_id, args.ds_id)
        sys.exit(0)

    if args.skip_create:
        kb_id, ds_id = args.kb_id, args.ds_id
    else:
        role_arn = create_iam_role()
        setup_s3()
        kb_id = create_managed_kb(role_arn)
        ds_id = create_datasource_and_ingest(kb_id)

    print(f"\n{'='*60}\n  KB_ID={kb_id}  DS_ID={ds_id}\n{'='*60}")

    if not args.skip_runtime_tests:
        test_standard_retrieve(kb_id, "MANAGED")
        test_standard_retrieve(kb_id, "NONE")
        test_agentic_retrieve(kb_id)
        test_strands_agent(kb_id)
        if args.customer_kb_id:
            compare_with_customer_managed(kb_id, args.customer_kb_id)

    if args.cleanup:
        cleanup(kb_id, ds_id)
    else:
        print(f"\n後片付け: uv run managed_kb_full_test.py --cleanup-only --kb-id {kb_id} --ds-id {ds_id}")
