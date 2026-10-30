#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第2章サンプル用 Knowledge Base を CLI で構築するスクリプト。
#
# 構成:
#   S3(sample-data) --(sync)--> Knowledge Base (OpenSearch Serverless)
#     - ベクトルストア: AOSS VECTORSEARCH コレクション（自作のインデックス）
#     - 埋め込みモデル: amazon.titan-embed-text-v2:0 (1024次元, l2, faiss+HNSW)
#     - データソース: 本リポジトリの sample-code/02/sample-data/ をS3経由で取り込み
#
# 前提:
#   - AWS CLI v2、jq、uv、Python 3.10+ が利用可能
#   - Anthropic Claude Sonnet 4.6 のサブスクリプション完了 + First Time Use form 提出済み（Titan Embeddings V2 は既定で利用可）
#   - 実行ロールに IAM / S3 / Bedrock / Bedrock-Agent / OpenSearchServerless の権限
#
# 削除時は teardown.sh を実行。
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${SUFFIX:=$(python3 -c 'import secrets; print(secrets.token_hex(3))')}"
: "${AOSS_PROPAGATION_WAIT_SECONDS:=60}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"

BUCKET="ch02-kb-sample-data-${SUFFIX}"
KB_ROLE="ch02-kb-service-role-${SUFFIX}"
AOSS_COLLECTION="ch02-kb-${SUFFIX}"
AOSS_INDEX="ch02-kb-index"
KB_NAME="ch02-kb-${SUFFIX}"
EMBED_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0"

# 実行ロールのベースARN（assumed-role の場合は base-role を抽出）
CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text --region "$AWS_REGION")"
if [[ "$CALLER_ARN" == *"assumed-role"* ]]; then
  ROLE_NAME="$(echo "$CALLER_ARN" | awk -F'/' '{print $2}')"
  CALLER_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"
else
  CALLER_ROLE_ARN="$CALLER_ARN"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
# shellcheck source=../../common/kb-ingestion.sh
source "${SCRIPT_DIR}/../../common/kb-ingestion.sh"

KB_ID=""
DS_ID=""
JOB_ID=""

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

write_env() {
  umask 077
  cat > "$ENV_FILE" <<EOF
AWS_REGION=${AWS_REGION}
SUFFIX=${SUFFIX}
AOSS_PROPAGATION_WAIT_SECONDS=${AOSS_PROPAGATION_WAIT_SECONDS}
BUCKET=${BUCKET}
KB_ROLE=${KB_ROLE}
AOSS_COLLECTION=${AOSS_COLLECTION}
AOSS_INDEX=${AOSS_INDEX}
KB_NAME=${KB_NAME}
KB_ID=${KB_ID}
DS_ID=${DS_ID}
JOB_ID=${JOB_ID}
EOF
  chmod 600 "$ENV_FILE"
}

# -----------------------------------------------------------------------------
# 1. S3 バケット作成 + サンプルデータのアップロード
# -----------------------------------------------------------------------------
log "(1/6) S3 バケット作成とサンプルデータのアップロード (${BUCKET})"
aws s3 mb "s3://${BUCKET}" --region "$AWS_REGION"
write_env
# faq.xlsxはFAQ Markdownの変換元で内容が重複するため、検索対象から除外する。
aws s3 sync "${CHAPTER_DIR}/sample-data/" "s3://${BUCKET}/sample-data/" \
  --exclude 'faq/source/*' \
  --region "$AWS_REGION"

# -----------------------------------------------------------------------------
# 2. KB 用 IAM ロール
# -----------------------------------------------------------------------------
log "(2/6) Knowledge Base 用サービスロール作成 (${KB_ROLE})"
TRUST_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "bedrock.amazonaws.com"},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {"aws:SourceAccount": "${AWS_ACCOUNT_ID}"},
      "ArnLike": {"aws:SourceArn": "arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:knowledge-base/*"}
    }
  }]
}
EOF
)
POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid":"S3Read","Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],
     "Resource":["arn:aws:s3:::${BUCKET}","arn:aws:s3:::${BUCKET}/*"],
     "Condition":{"StringEquals":{"aws:ResourceAccount":"${AWS_ACCOUNT_ID}"}}},
    {"Sid":"BedrockEmbedding","Effect":"Allow","Action":"bedrock:InvokeModel",
     "Resource":"${EMBED_MODEL_ARN}"},
    {"Sid":"AOSSAPIAccessAll","Effect":"Allow","Action":"aoss:APIAccessAll",
     "Resource":"arn:aws:aoss:${AWS_REGION}:${AWS_ACCOUNT_ID}:collection/*"}
  ]
}
EOF
)
aws iam create-role --role-name "$KB_ROLE" \
  --assume-role-policy-document "$TRUST_DOC" \
  --description "Chapter 2 KB service role" >/dev/null
aws iam put-role-policy --role-name "$KB_ROLE" \
  --policy-name ch02-kb-inline --policy-document "$POLICY_DOC"
KB_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${KB_ROLE}"

# -----------------------------------------------------------------------------
# 3. OpenSearch Serverless: 暗号化・ネットワーク・コレクション・データアクセス
# -----------------------------------------------------------------------------
log "(3/6) AOSS ポリシーとコレクション作成"
aws opensearchserverless create-security-policy \
  --name "${AOSS_COLLECTION}-enc" --type encryption \
  --policy "{\"Rules\":[{\"ResourceType\":\"collection\",\"Resource\":[\"collection/${AOSS_COLLECTION}\"]}],\"AWSOwnedKey\":true}" \
  --region "$AWS_REGION" >/dev/null
aws opensearchserverless create-security-policy \
  --name "${AOSS_COLLECTION}-net" --type network \
  --policy "[{\"Rules\":[{\"ResourceType\":\"collection\",\"Resource\":[\"collection/${AOSS_COLLECTION}\"]},{\"ResourceType\":\"dashboard\",\"Resource\":[\"collection/${AOSS_COLLECTION}\"]}],\"AllowFromPublic\":true}]" \
  --region "$AWS_REGION" >/dev/null
aws opensearchserverless create-collection \
  --name "$AOSS_COLLECTION" --type VECTORSEARCH \
  --description "Chapter 2 KB vector store" \
  --region "$AWS_REGION" >/dev/null

log "コレクション ACTIVE 待機..."
for _ in {1..60}; do
  STATUS="$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" \
    --region "$AWS_REGION" --query 'collectionDetails[0].status' --output text)"
  [ "$STATUS" = "ACTIVE" ] && break
  sleep 10
done
[ "$STATUS" = "ACTIVE" ] || { echo "コレクションがACTIVEになりませんでした"; exit 1; }

aws opensearchserverless create-access-policy \
  --name "${AOSS_COLLECTION}-data" --type data \
  --policy "[{\"Rules\":[{\"ResourceType\":\"collection\",\"Resource\":[\"collection/${AOSS_COLLECTION}\"],\"Permission\":[\"aoss:*\"]},{\"ResourceType\":\"index\",\"Resource\":[\"index/${AOSS_COLLECTION}/*\"],\"Permission\":[\"aoss:*\"]}],\"Principal\":[\"${CALLER_ROLE_ARN}\",\"${KB_ROLE_ARN}\"]}]" \
  --region "$AWS_REGION" >/dev/null

# -----------------------------------------------------------------------------
# 4. ベクトルインデックスを作成（opensearch-py + SigV4）
# -----------------------------------------------------------------------------
log "(4/6) ベクトルインデックス作成 (${AOSS_INDEX})"
# opensearch-py と boto3 は本スクリプト内でのみ一時的に利用する。
# 既存の仮想環境を前提にせず、uvが管理する一時環境で実行する。
AOSS_COLLECTION="$AOSS_COLLECTION" AOSS_INDEX="$AOSS_INDEX" AWS_REGION="$AWS_REGION" \
  uv run --with 'opensearch-py>=2.8.0' --with boto3 python - <<'PY'
import os, boto3, time
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
region = os.environ["AWS_REGION"]
collection = os.environ["AOSS_COLLECTION"]
index = os.environ["AOSS_INDEX"]
host = boto3.client("opensearchserverless", region_name=region) \
    .batch_get_collection(names=[collection])["collectionDetails"][0]["collectionEndpoint"].replace("https://","")
auth = AWSV4SignerAuth(boto3.Session().get_credentials(), region, "aoss")
client = OpenSearch(hosts=[{"host": host, "port": 443}], http_auth=auth,
                    use_ssl=True, verify_certs=True, connection_class=RequestsHttpConnection)
body = {
  "settings": {"index": {"knn": True, "knn.algo_param.ef_search": 512}},
  "mappings": {"properties": {
    "bedrock-knowledge-base-default-vector": {
      "type": "knn_vector", "dimension": 1024,
      "method": {"engine": "faiss", "space_type": "l2", "name": "hnsw",
                 "parameters": {"ef_construction": 512, "m": 16}}},
    "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
    "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False}}}
}
for attempt in range(10):
    try:
        client.indices.create(index=index, body=body)
        print("index created"); break
    except Exception as e:
        if "resource_already_exists" in str(e):
            print("index already exists"); break
        print(f"[{attempt+1}] retry: {str(e)[:120]}")
        time.sleep(10)
PY

# -----------------------------------------------------------------------------
# 5. Knowledge Base + データソース + 同期
# -----------------------------------------------------------------------------
log "(5/6) Knowledge Base + データソース作成"
COLLECTION_ARN="$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" \
  --region "$AWS_REGION" --query 'collectionDetails[0].arn' --output text)"

# AOSSデータポリシー伝播のため少し待つ
sleep 20

STORAGE_CFG=$(cat <<EOF
{
  "type":"OPENSEARCH_SERVERLESS",
  "opensearchServerlessConfiguration":{
    "collectionArn":"${COLLECTION_ARN}",
    "vectorIndexName":"${AOSS_INDEX}",
    "fieldMapping":{
      "vectorField":"bedrock-knowledge-base-default-vector",
      "textField":"AMAZON_BEDROCK_TEXT_CHUNK",
      "metadataField":"AMAZON_BEDROCK_METADATA"
    }
  }
}
EOF
)
KB_ID="$(aws bedrock-agent create-knowledge-base \
  --name "$KB_NAME" \
  --description "Chapter 2 sample KB (OpenSearch Serverless)" \
  --role-arn "$KB_ROLE_ARN" \
  --knowledge-base-configuration "{\"type\":\"VECTOR\",\"vectorKnowledgeBaseConfiguration\":{\"embeddingModelArn\":\"${EMBED_MODEL_ARN}\"}}" \
  --storage-configuration "$STORAGE_CFG" \
  --region "$AWS_REGION" --query 'knowledgeBase.knowledgeBaseId' --output text)"
write_env

DS_ID="$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID" \
  --name "ch02-s3-source" \
  --data-source-configuration "{\"type\":\"S3\",\"s3Configuration\":{\"bucketArn\":\"arn:aws:s3:::${BUCKET}\",\"inclusionPrefixes\":[\"sample-data/\"]}}" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)"
write_env

log "(6/6) 取り込みジョブ実行"
if [ "$AOSS_PROPAGATION_WAIT_SECONDS" -gt 0 ]; then
  echo "OpenSearch Serverlessのアクセス許可伝播を${AOSS_PROPAGATION_WAIT_SECONDS}秒待機します。"
  sleep "$AOSS_PROPAGATION_WAIT_SECONDS"
fi
JOB_ID="$(aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
  --region "$AWS_REGION" --query 'ingestionJob.ingestionJobId' --output text)"
write_env

wait_for_kb_ingestion \
  "$KB_ID" "$DS_ID" "$JOB_ID" "chapter-02-opensearch" \
  "$AWS_REGION" 30 15 33

# teardownが参照する最新IDを保存する。
write_env

printf '\n\033[1;32m%s\033[0m\n' "完了しました。"
cat <<EOF
  KB_ID   = ${KB_ID}
  DS_ID   = ${DS_ID}
  BUCKET  = ${BUCKET}

CLI 動作確認（sample-code/02 で実行。以降の章でも同じ変数を使います）:
  export KNOWLEDGE_BASE_ID=${KB_ID}
  export DATA_SOURCE_ID=${DS_ID}
  export MODEL_ARN="arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-6"
  export MODEL_ID=us.anthropic.claude-sonnet-4-6
  uv run retrieve_and_generate.py --question "SAML SSOでログインできない場合の確認項目を教えてください"

削除（sample-code/02 で実行）: bash infra/teardown.sh
EOF
