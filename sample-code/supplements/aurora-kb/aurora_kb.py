"""専用Aurora Knowledge Baseの構築、文書同期、検証、削除。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import unquote

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from infrastructure import template

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "02" / "sample-data"
QUESTIONS = HERE.parents[1] / "03" / "data" / "questions.jsonl"
DB = "knowledge"
TABLE = "bedrock_integration.bedrock_kb"
OWNER = "bedrock-book-aurora-supplement"


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


# 温度を受け付けず、適応的思考が既定で有効なモデル。モデルIDとARNの末尾で判定する。
REASONING_MODELS = (
    "anthropic.claude-sonnet-5",
    "anthropic.claude-sonnet-5-5",
    "anthropic.claude-opus-5",
    "anthropic.claude-opus-5-5",
    "anthropic.claude-haiku-5-5",
    "openai.gpt-6-sol",
    "openai.gpt-6.1-sol",
    "openai.gpt-6-luna",
)


def generation_settings(model_arn):
    """RetrieveAndGenerateの生成設定。新しいモデルには温度を送らず、2つのプロンプトを渡す。"""
    if not model_arn.endswith(REASONING_MODELS):
        return {
            "generationConfiguration": {
                "inferenceConfig": {"textInferenceConfig": {"maxTokens": 600, "temperature": 0}}
            }
        }
    # KB既定のプロンプトがないモデルがあるため、出力形式の指示を含めて渡す
    inference = {"textInferenceConfig": {"maxTokens": 4096}}
    return {
        "generationConfiguration": {
            "promptTemplate": {
                "textPromptTemplate": (
                    "検索結果に基づいて日本語で回答してください。資料にないことは推測せず、不明と伝えてください。"
                    "資料の適用時期が異なる場合は区別してください。\n"
                    "質問: $query$\n検索結果:\n$search_results$\n$output_format_instructions$"
                )
            },
            "inferenceConfig": inference,
        },
        "orchestrationConfiguration": {
            "promptTemplate": {
                "textPromptTemplate": (
                    "会話履歴と質問から、関連資料を探す検索クエリを作成してください。\n"
                    "会話履歴:\n$conversation_history$\n質問: $query$\n$output_format_instructions$"
                )
            },
            "inferenceConfig": inference,
        },
    }


def source_path(result):
    uri = result.get("location", {}).get("s3Location", {}).get("uri", "")
    return unquote(uri).split("/documents/", 1)[-1]


def retrieval_metrics(results, expected):
    """チャンク順位のまま採点。Recallの分子は正解文書を重複排除する。"""
    matches = [
        any(source_path(r).removesuffix(".md") == s for s in expected) for r in results
    ]
    found = {
        s
        for s in expected
        if any(source_path(r).removesuffix(".md") == s for r in results)
    }
    return {
        "hit": int(any(matches)),
        "recall": len(found) / len(set(expected)),
        "reciprocal_rank": next((1 / (i + 1) for i, ok in enumerate(matches) if ok), 0),
    }


class Run:
    def __init__(self, args):
        self.args = args
        self.path = args.state
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.region = self.state.get("region", args.region)
        self.session = boto3.Session(region_name=self.region)
        self.clients = {}
        self.account = self.client("sts").get_caller_identity()["Account"]
        if self.state and self.state["account"] != self.account:
            raise ValueError("状態ファイルと現在のAWSアカウントが異なります")

    def client(self, service):
        if service not in self.clients:
            self.clients[service] = self.session.client(
                service,
                config=Config(
                    retries={"mode": "standard", "max_attempts": 4},
                    connect_timeout=10,
                    read_timeout=180,
                ),
            )
        return self.clients[service]

    def save(self):
        dump(self.path, self.state)

    def record(self, name, obj):
        dump(self.path.parent / (name + ".json"), obj)
        return obj

    def sql(self, statement, admin=False, parameters=None):
        return self.client("rds-data").execute_statement(
            resourceArn=self.state["ClusterArn"],
            secretArn=self.state["MasterSecretArn" if admin else "KbSecretArn"],
            database=DB,
            sql=statement,
            parameters=parameters or [],
            formatRecordsAs="JSON",
        )

    def rows(self, statement, admin=False):
        return json.loads(self.sql(statement, admin)["formattedRecords"])

    def wait(self, description, getter, done, failed=lambda x: False, seconds=1800):
        deadline = time.monotonic() + seconds
        last = None
        while time.monotonic() < deadline:
            result = getter()
            label = str(result.get("status", result.get("StackStatus", "waiting")))
            if label != last:
                print(description, label, flush=True)
                last = label
            if done(result):
                return result
            if failed(result):
                raise RuntimeError(f"{description}: {result}")
            time.sleep(15)
        raise TimeoutError(
            f"{description}: timeout; 状態ファイルを残しているためcleanupで削除できます"
        )

    def stack(self):
        stack = self.client("cloudformation").describe_stacks(
            StackName=self.state["stack_id"]
        )["Stacks"][0]
        tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
        if tags.get("Purpose") != OWNER:
            raise ValueError("このサンプルが作成したスタックではありません")
        return stack

    def deploy(self):
        if self.state:
            raise ValueError(
                "状態ファイルが既にあります。setupで再開するかcleanupで削除してください"
            )
        if not self.args.yes:
            raise ValueError("有料リソース作成には --yes を付けてください")
        name = self.args.name
        if not re.fullmatch(r"book-aurora-[a-z0-9-]{1,35}", name):
            raise ValueError(
                "nameはbook-aurora-で始まる英小文字・数字・ハイフンにしてください"
            )
        self.state = {
            "account": self.account,
            "region": self.region,
            "stack_name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine_version": self.args.engine_version,
        }
        self.save()
        result = self.client("cloudformation").create_stack(
            StackName=name,
            TemplateBody=json.dumps(template(self.args.engine_version)),
            Capabilities=["CAPABILITY_IAM"],
            Tags=[{"Key": "Purpose", "Value": OWNER}],
        )
        self.state["stack_id"] = result["StackId"]
        self.save()
        self.setup()

    def setup(self):
        stack = self.wait(
            "CloudFormation",
            self.stack,
            lambda s: s["StackStatus"] == "CREATE_COMPLETE",
            lambda s: "ROLLBACK" in s["StackStatus"] or "FAILED" in s["StackStatus"],
        )
        self.state.update({x["OutputKey"]: x["OutputValue"] for x in stack["Outputs"]})
        self.save()
        for attempt in range(24):
            try:
                self.rows("SELECT 1", admin=True)
                break
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in {
                    "DatabaseResumingException",
                    "DatabaseUnavailableException",
                    "HttpEndpointNotEnabledException",
                }:
                    raise
                time.sleep(10)
        else:
            raise TimeoutError("Data APIの準備が完了しませんでした")
        if not self.rows(
            "SELECT rolname FROM pg_roles WHERE rolname='bedrock_user'", admin=True
        ):
            secret = json.loads(
                self.client("secretsmanager").get_secret_value(
                    SecretId=self.state["KbSecretArn"]
                )["SecretString"]
            )
            password = secret["password"]
            if not re.fullmatch(r"[A-Za-z0-9]{32}", password):
                raise ValueError("専用シークレットの形式が想定外です")
            # SQL文はログへ出さない。生成値は英数字に限定し、秘密値をファイルに保存しない。
            self.sql(
                f"CREATE ROLE bedrock_user LOGIN PASSWORD '{password}'", admin=True
            )
        for statement in (HERE / "schema.sql").read_text().split(";"):
            if statement.strip():
                self.sql(statement, admin=True)
        version = self.rows(
            "SELECT extversion FROM pg_extension WHERE extname='vector'", admin=True
        )[0]["extversion"]
        if tuple(map(int, version.split("."))) >= (0, 8, 0):
            self.sql(
                "ALTER DATABASE knowledge SET hnsw.iterative_scan = 'relaxed_order'",
                admin=True,
            )
        self.state["pgvector_version"] = version
        self.save()
        agent = self.client("bedrock-agent")
        if "kb_id" not in self.state:
            token = hashlib.sha256(
                (self.state["stack_id"] + ":kb").encode()
            ).hexdigest()
            response = agent.create_knowledge_base(
                clientToken=token,
                name=self.state["stack_name"],
                roleArn=self.state["KbRoleArn"],
                knowledgeBaseConfiguration={
                    "type": "VECTOR",
                    "vectorKnowledgeBaseConfiguration": {
                        "embeddingModelArn": f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0",
                        "embeddingModelConfiguration": {
                            "bedrockEmbeddingModelConfiguration": {
                                "dimensions": 1024,
                                "embeddingDataType": "FLOAT32",
                            }
                        },
                    },
                },
                storageConfiguration={
                    "type": "RDS",
                    "rdsConfiguration": {
                        "resourceArn": self.state["ClusterArn"],
                        "credentialsSecretArn": self.state["KbSecretArn"],
                        "databaseName": DB,
                        "tableName": TABLE,
                        "fieldMapping": {
                            "primaryKeyField": "id",
                            "vectorField": "embedding",
                            "textField": "chunks",
                            "metadataField": "metadata",
                            "customMetadataField": "custom_metadata",
                        },
                    },
                },
                tags={"Purpose": OWNER},
            )
            self.state["kb_id"] = response["knowledgeBase"]["knowledgeBaseId"]
            self.save()
        self.wait(
            "Knowledge Base",
            lambda: agent.get_knowledge_base(knowledgeBaseId=self.state["kb_id"])[
                "knowledgeBase"
            ],
            lambda x: x["status"] == "ACTIVE",
            lambda x: x["status"] == "FAILED",
        )
        if "data_source_id" not in self.state:
            token = hashlib.sha256(
                (self.state["stack_id"] + ":ds").encode()
            ).hexdigest()
            response = agent.create_data_source(
                clientToken=token,
                knowledgeBaseId=self.state["kb_id"],
                name="chapter02-documents",
                dataDeletionPolicy="DELETE",
                dataSourceConfiguration={
                    "type": "S3",
                    "s3Configuration": {
                        "bucketArn": f"arn:aws:s3:::{self.state['Bucket']}",
                        "inclusionPrefixes": ["documents/"],
                    },
                },
                vectorIngestionConfiguration={
                    "chunkingConfiguration": {
                        "chunkingStrategy": "FIXED_SIZE",
                        "fixedSizeChunkingConfiguration": {
                            "maxTokens": 300,
                            "overlapPercentage": 20,
                        },
                    }
                },
            )
            self.state["data_source_id"] = response["dataSource"]["dataSourceId"]
            self.save()
        print("構築完了:", self.state["kb_id"], "pgvector", version, flush=True)

    def ingest(self):
        files = sorted(
            p
            for p in DATA.rglob("*")
            if p.is_file()
            and (p.suffix == ".md" or p.name.endswith(".md.metadata.json"))
        )
        if not files:
            raise ValueError(f"サンプルデータが見つかりません: {DATA}")
        for p in files:
            self.client("s3").upload_file(
                str(p),
                self.state["Bucket"],
                "documents/" + p.relative_to(DATA).as_posix(),
            )
        agent = self.client("bedrock-agent")
        ids = {
            "knowledgeBaseId": self.state["kb_id"],
            "dataSourceId": self.state["data_source_id"],
        }
        job = agent.start_ingestion_job(**ids)["ingestionJob"]
        self.state["ingestion_job_id"] = job["ingestionJobId"]
        self.save()
        result = self.wait(
            "Ingestion",
            lambda: agent.get_ingestion_job(
                **ids, ingestionJobId=job["ingestionJobId"]
            )["ingestionJob"],
            lambda x: x["status"] == "COMPLETE",
            lambda x: x["status"] in {"FAILED", "STOPPED"},
        )
        self.record(
            "ingestion",
            {
                "statistics": result["statistics"],
                "document_count": sum(p.suffix == ".md" for p in files),
                "source_sha256": {
                    p.relative_to(DATA).as_posix(): hashlib.sha256(
                        p.read_bytes()
                    ).hexdigest()
                    for p in files
                },
            },
        )
        if result["statistics"].get("numberOfDocumentsFailed", 0):
            raise RuntimeError("取り込みに失敗した文書があります")
        print(json.dumps(result["statistics"], indent=2), flush=True)

    def inspect(self):
        checks = {
            "versions": "SELECT version() AS postgres, (SELECT extversion FROM pg_extension WHERE extname='vector') AS pgvector",
            "counts": f"SELECT count(*) AS chunks, min(vector_dims(embedding)) AS min_dimensions, max(vector_dims(embedding)) AS max_dimensions FROM {TABLE}",
            "documents": f"SELECT custom_metadata->>'document_type' AS document_type, count(*) AS chunks FROM {TABLE} GROUP BY 1 ORDER BY 1",
            "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='bedrock_integration' ORDER BY indexname",
            "audit_chunks": f"SELECT left(chunks, 240) AS text, custom_metadata FROM {TABLE} WHERE custom_metadata->>'topic_id'='audit_log' ORDER BY custom_metadata->>'effective_date' DESC LIMIT 3",
            "tokenization": "SELECT to_tsvector('simple', '監査ログの保存期間 SAML AccessDenied')::text AS tokens",
        }
        result = {name: self.rows(sql) for name, sql in checks.items()}
        self.record("inspection", result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    def retrieve(self, question, mode="SEMANTIC", query_filter=None):
        cfg = {"numberOfResults": 5, "overrideSearchType": mode}
        if query_filter:
            cfg["filter"] = query_filter
        return self.client("bedrock-agent-runtime").retrieve(
            knowledgeBaseId=self.state["kb_id"],
            retrievalQuery={"text": question},
            retrievalConfiguration={"vectorSearchConfiguration": cfg},
        )["retrievalResults"]

    def verify(self):
        question = "2026年4月適用のポリシーでは、Enterpriseプランの監査ログは何日保存されますか"
        filt = {
            "andAll": [
                {"equals": {"key": "topic_id", "value": "audit_log"}},
                {"equals": {"key": "effective_date", "value": 20260401}},
            ]
        }
        runtime = self.client("bedrock-agent-runtime")
        records = []
        for mode in ["SEMANTIC", "HYBRID"]:
            results = self.retrieve(question, mode, filt)
            assert results, f"{mode}: 検索結果が空です"
            assert all(
                r["metadata"].get("effective_date") == 20260401
                and r["metadata"].get("topic_id") == "audit_log"
                for r in results
            )
            assert any("730" in r["content"]["text"] for r in results)
            model_arn = (
                self.args.model_arn
                or f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-sonnet-4-6"
            )
            response = runtime.retrieve_and_generate(
                input={"text": question},
                retrieveAndGenerateConfiguration={
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": self.state["kb_id"],
                        "modelArn": model_arn,
                        "retrievalConfiguration": {
                            "vectorSearchConfiguration": {
                                "numberOfResults": 5,
                                "overrideSearchType": mode,
                                "filter": filt,
                            }
                        },
                        **generation_settings(model_arn),
                    },
                },
            )
            refs = [
                r
                for c in response.get("citations", [])
                for r in c.get("retrievedReferences", [])
            ]
            answer = response["output"]["text"]
            assert "730" in answer and refs, f"{mode}: 回答または出典を確認してください"
            assert any("730" in r["content"]["text"] for r in refs)
            records.append(
                {
                    "mode": mode,
                    "question": question,
                    "answer": answer,
                    "sources": [source_path(r) for r in refs],
                    "retrieved": [
                        {
                            "source": source_path(r),
                            "text": r["content"]["text"],
                            "metadata": {
                                k: v
                                for k, v in r.get("metadata", {}).items()
                                if not k.startswith("x-amz-")
                            },
                        }
                        for r in results
                    ],
                }
            )
            self.record("verification", records)
            print(mode, answer, flush=True)

    def evaluate(self):
        questions = [
            json.loads(line)
            for line in QUESTIONS.read_text().splitlines()
            if line.strip()
        ]
        sources = {
            p.relative_to(DATA).as_posix().removesuffix(".md")
            for p in DATA.rglob("*.md")
        }
        for q in questions:
            if not q["expected_sources"] or not set(q["expected_sources"]).issubset(
                sources
            ):
                raise ValueError(f"{q['id']}: 正解文書がデータに存在しません")
        rows = []
        configs = [
            ("semantic", "SEMANTIC", False),
            ("hybrid", "HYBRID", False),
            ("semantic_filter", "SEMANTIC", True),
            ("hybrid_filter", "HYBRID", True),
        ]
        for i, q in enumerate(questions):
            # 実行順による偏りを減らすため、質問ごとに開始構成をずらす。
            for name, mode, use_filter in configs[i % 4 :] + configs[: i % 4]:
                start = time.monotonic()
                results = self.retrieve(
                    q["question"], mode, q.get("filter") if use_filter else None
                )
                row = {
                    "id": q["id"],
                    "question": q["question"],
                    "tags": q.get("tags", []),
                    "config": name,
                    "expected_sources": q["expected_sources"],
                    "filter": q.get("filter") if use_filter else None,
                    "latency_ms": round((time.monotonic() - start) * 1000, 2),
                    **retrieval_metrics(results, q["expected_sources"]),
                    "sources": [source_path(r) for r in results],
                    "scores": [r["score"] for r in results],
                }
                rows.append(row)
                self.record("evaluation", rows)
            print("評価", q["id"], f"{i + 1}/{len(questions)}", flush=True)
        summary = {}
        for name, _, _ in configs:
            group = [r for r in rows if r["config"] == name]
            summary[name] = {
                "questions": len(group),
                "hit_at_5": sum(r["hit"] for r in group) / len(group),
                "recall_at_5": sum(r["recall"] for r in group) / len(group),
                "mrr_at_5": sum(r["reciprocal_rank"] for r in group) / len(group),
            }
        self.record(
            "evaluation-summary",
            {
                "k": 5,
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "region": self.region,
                "engine_version": self.state["engine_version"],
                "pgvector_version": self.state["pgvector_version"],
                "embedding_model": "amazon.titan-embed-text-v2:0",
                "dimensions": 1024,
                "rank_unit": "chunk",
                "questions_sha256": hashlib.sha256(QUESTIONS.read_bytes()).hexdigest(),
                "configs": summary,
            },
        )
        print(json.dumps(summary, indent=2), flush=True)

    def cleanup(self):
        if not self.args.yes:
            raise ValueError("専用リソースの削除には --yes を付けてください")
        if self.state.get("cleaned_up"):
            print("削除済みです")
            return
        agent = self.client("bedrock-agent")
        if "kb_id" in self.state:
            try:
                kb = agent.get_knowledge_base(knowledgeBaseId=self.state["kb_id"])[
                    "knowledgeBase"
                ]
                if kb["roleArn"] != self.state["KbRoleArn"]:
                    raise ValueError(
                        "Knowledge Baseのロールが専用スタックと一致しません"
                    )
                agent.delete_knowledge_base(knowledgeBaseId=self.state["kb_id"])

                def kb_status():
                    try:
                        return agent.get_knowledge_base(
                            knowledgeBaseId=self.state["kb_id"]
                        )["knowledgeBase"]
                    except agent.exceptions.ResourceNotFoundException:
                        return {"status": "DELETED"}

                self.wait(
                    "Knowledge Base deletion",
                    kb_status,
                    lambda x: x["status"] == "DELETED",
                    lambda x: x["status"] == "DELETE_UNSUCCESSFUL",
                )
            except agent.exceptions.ResourceNotFoundException:
                pass
        if "stack_id" not in self.state:
            raise ValueError(
                "作成済みスタックIDがありません。CloudFormationで作成成否を確認してください"
            )
        cfn = self.client("cloudformation")
        try:
            stack = self.stack()
        except ClientError as exc:
            if "does not exist" not in exc.response["Error"]["Message"]:
                raise
            stack = None
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            # 途中失敗時も実在する物理IDを取得し、専用バケットだけを空にする。
            resources = cfn.list_stack_resources(StackName=self.state["stack_id"])[
                "StackResourceSummaries"
            ]
            buckets = [
                r["PhysicalResourceId"]
                for r in resources
                if r["LogicalResourceId"] == "Bucket"
                and r.get("PhysicalResourceId")
                and r["ResourceStatus"] != "DELETE_COMPLETE"
            ]
            for bucket in buckets:
                s3 = self.client("s3")
                for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                    objects = [{"Key": x["Key"]} for x in page.get("Contents", [])]
                    if objects:
                        response = s3.delete_objects(
                            Bucket=bucket, Delete={"Objects": objects}
                        )
                        if response.get("Errors"):
                            raise RuntimeError(response["Errors"])
            cfn.delete_stack(StackName=self.state["stack_id"])

            def stack_status():
                try:
                    return self.stack()
                except ClientError as exc:
                    if "does not exist" in exc.response["Error"]["Message"]:
                        return {"StackStatus": "DELETE_COMPLETE"}
                    raise

            self.wait(
                "CloudFormation deletion",
                stack_status,
                lambda x: x["StackStatus"] == "DELETE_COMPLETE",
                lambda x: x["StackStatus"] == "DELETE_FAILED",
                seconds=3600,
            )
        self.state["cleaned_up"] = datetime.now(timezone.utc).isoformat()
        self.save()
        self.record(
            "cleanup",
            {
                "knowledge_base": "DELETED",
                "stack": "DELETE_COMPLETE",
                "completed_at": self.state["cleaned_up"],
            },
        )
        print("専用Knowledge Baseとスタックの削除完了", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "deploy",
            "setup",
            "ingest",
            "inspect",
            "verify",
            "evaluate",
            "cleanup",
        ],
    )
    p.add_argument("--state", type=Path, default=HERE / ".state" / "state.json")
    p.add_argument("--region", default="us-east-1")
    p.add_argument(
        "--name",
        default="book-aurora-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
    )
    p.add_argument("--engine-version", default="16.14")
    p.add_argument("--model-arn", help="回答生成用のモデルARNまたは推論プロファイルARN")
    p.add_argument("--yes", action="store_true")
    args = p.parse_args()
    run = Run(args)
    if args.command != "deploy" and not run.state:
        p.error("状態ファイルがありません。先にdeployしてください")
    getattr(run, args.command)()


if __name__ == "__main__":
    main()
