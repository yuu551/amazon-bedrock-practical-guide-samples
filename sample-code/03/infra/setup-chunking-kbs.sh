#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第3章 チャンク戦略比較用 Knowledge Base を構築するスクリプト。
#
# 3つのチャンク戦略でそれぞれ KB を立てる:
#   - fixed        : FIXED_SIZE (300 tokens, 20% overlap) — デフォルト相当
#   - hierarchical : HIERARCHICAL (parent 1500 / child 300 tokens)
#   - semantic     : SEMANTIC (maxTokens 300, bufferSize 1, threshold 95%)
#
# いずれもデフォルトパーサーで取り込む（Parser 比較スクリプトと独立）。
# S3 バケットと PDF は setup-parser-kbs.sh と同じ前提（sample-pdfs/*.pdf を使用）。
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${SUFFIX:=$(python3 -c 'import secrets; print(secrets.token_hex(3))')}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
: "${RETRIEVE_MODEL_ARN:=arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-6}"

BUCKET="ch03-chunking-sample-${SUFFIX}"
KB_ROLE="ch03-chunking-kb-role-${SUFFIX}"
AOSS_COLLECTION="ch03-chunking-${SUFFIX}"
EMBED_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0"
: "${SEMANTIC_EMBED_MODEL_ARN:=${EMBED_MODEL_ARN}}"

INDEX_FIXED="ch03-chunk-fixed"
INDEX_HIER="ch03-chunk-hierarchical"
INDEX_SEM="ch03-chunk-semantic"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../common/kb-ingestion.sh
source "${SCRIPT_DIR}/../../common/kb-ingestion.sh"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PDF_DIR="${CHAPTER_DIR}/sample-pdfs"
ENV_FILE="${SCRIPT_DIR}/.env.chunking"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [ ! -d "$PDF_DIR" ] || [ -z "$(ls -1 "$PDF_DIR"/*.pdf 2>/dev/null)" ]; then
  echo "sample-pdfs/*.pdf が見つかりません。先に uv run generate_sample_pdfs.py を実行してください。" >&2
  exit 1
fi

CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text --region "$AWS_REGION")"
if [[ "$CALLER_ARN" == *"assumed-role"* ]]; then
  ROLE_NAME="$(echo "$CALLER_ARN" | awk -F'/' '{print $2}')"
  CALLER_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"
else
  CALLER_ROLE_ARN="$CALLER_ARN"
fi

# -----------------------------------------------------------------------------
log "(1/7) S3 バケット作成と PDF アップロード (${BUCKET})"
aws s3 mb "s3://${BUCKET}" --region "$AWS_REGION"
aws s3 sync "${PDF_DIR}/" "s3://${BUCKET}/sample-pdfs/" \
  --exclude "*.png" --exclude ".*" --region "$AWS_REGION"

# -----------------------------------------------------------------------------
log "(2/7) KB 用 IAM ロール作成 (${KB_ROLE})"
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
     "Resource":["${EMBED_MODEL_ARN}","${SEMANTIC_EMBED_MODEL_ARN}"]},
    {"Sid":"AOSSAPIAccessAll","Effect":"Allow","Action":"aoss:APIAccessAll",
     "Resource":"arn:aws:aoss:${AWS_REGION}:${AWS_ACCOUNT_ID}:collection/*"}
  ]
}
EOF
)
aws iam create-role --role-name "$KB_ROLE" \
  --assume-role-policy-document "$TRUST_DOC" \
  --description "Chapter 3 chunking comparison KB service role" >/dev/null
aws iam put-role-policy --role-name "$KB_ROLE" \
  --policy-name ch03-chunking-inline --policy-document "$POLICY_DOC"
KB_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${KB_ROLE}"

# -----------------------------------------------------------------------------
log "(3/7) AOSS ポリシーとコレクション作成"
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
  --description "Chapter 3 chunking comparison vector store" \
  --region "$AWS_REGION" >/dev/null

log "コレクション ACTIVE 待機..."
STATUS=""
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
log "(4/7) 3つのベクトルインデックスを作成"
uv pip install --quiet 'opensearch-py>=2.8.0'
AOSS_COLLECTION="$AOSS_COLLECTION" AWS_REGION="$AWS_REGION" \
INDEX_FIXED="$INDEX_FIXED" \
INDEX_HIER="$INDEX_HIER" \
INDEX_SEM="$INDEX_SEM" \
  uv run python - <<'PY'
import os, time, boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
region = os.environ["AWS_REGION"]
collection = os.environ["AOSS_COLLECTION"]
indices = [os.environ["INDEX_FIXED"], os.environ["INDEX_HIER"], os.environ["INDEX_SEM"]]
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
for idx in indices:
    for attempt in range(10):
        try:
            client.indices.create(index=idx, body=body)
            print(f"index {idx} created"); break
        except Exception as e:
            if "resource_already_exists" in str(e):
                print(f"index {idx} already exists"); break
            print(f"[{attempt+1}] retry {idx}: {str(e)[:120]}")
            time.sleep(10)
PY

# -----------------------------------------------------------------------------
log "(5/7) Knowledge Base 3つを作成"
COLLECTION_ARN="$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" \
  --region "$AWS_REGION" --query 'collectionDetails[0].arn' --output text)"
sleep 20

create_kb() {
  local name="$1"; local index="$2"
  aws bedrock-agent create-knowledge-base \
    --name "$name" \
    --description "Chapter 3 chunking comparison KB (${index})" \
    --role-arn "$KB_ROLE_ARN" \
    --knowledge-base-configuration "{\"type\":\"VECTOR\",\"vectorKnowledgeBaseConfiguration\":{\"embeddingModelArn\":\"${EMBED_MODEL_ARN}\"}}" \
    --storage-configuration "{\"type\":\"OPENSEARCH_SERVERLESS\",\"opensearchServerlessConfiguration\":{\"collectionArn\":\"${COLLECTION_ARN}\",\"vectorIndexName\":\"${index}\",\"fieldMapping\":{\"vectorField\":\"bedrock-knowledge-base-default-vector\",\"textField\":\"AMAZON_BEDROCK_TEXT_CHUNK\",\"metadataField\":\"AMAZON_BEDROCK_METADATA\"}}}" \
    --region "$AWS_REGION" --query 'knowledgeBase.knowledgeBaseId' --output text
}

KB_ID_FIXED=$(create_kb "ch03-kb-fixed-${SUFFIX}" "$INDEX_FIXED")
KB_ID_HIER=$(create_kb "ch03-kb-hier-${SUFFIX}" "$INDEX_HIER")
KB_ID_SEM=$(create_kb "ch03-kb-semantic-${SUFFIX}" "$INDEX_SEM")

# -----------------------------------------------------------------------------
log "(6/7) データソース作成（各チャンク戦略）"
DS_CFG="{\"type\":\"S3\",\"s3Configuration\":{\"bucketArn\":\"arn:aws:s3:::${BUCKET}\",\"inclusionPrefixes\":[\"sample-pdfs/\"]}}"

# fixed size
FIXED_CFG='{"chunkingConfiguration":{"chunkingStrategy":"FIXED_SIZE","fixedSizeChunkingConfiguration":{"maxTokens":300,"overlapPercentage":20}}}'
DS_ID_FIXED=$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID_FIXED" --name fixed-ds \
  --data-source-configuration "$DS_CFG" \
  --vector-ingestion-configuration "$FIXED_CFG" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)

# hierarchical (parent 1500 / child 300, overlap 60)
HIER_CFG='{"chunkingConfiguration":{"chunkingStrategy":"HIERARCHICAL","hierarchicalChunkingConfiguration":{"levelConfigurations":[{"maxTokens":1500},{"maxTokens":300}],"overlapTokens":60}}}'
DS_ID_HIER=$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID_HIER" --name hierarchical-ds \
  --data-source-configuration "$DS_CFG" \
  --vector-ingestion-configuration "$HIER_CFG" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)

# semantic (maxTokens 300, bufferSize 1, threshold 95)
SEM_CFG="{\"chunkingConfiguration\":{\"chunkingStrategy\":\"SEMANTIC\",\"semanticChunkingConfiguration\":{\"maxTokens\":300,\"bufferSize\":1,\"breakpointPercentileThreshold\":95}}}"
DS_ID_SEM=$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID_SEM" --name semantic-ds \
  --data-source-configuration "$DS_CFG" \
  --vector-ingestion-configuration "$SEM_CFG" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)

# -----------------------------------------------------------------------------
log "(7/7) 取り込みジョブ実行（3 KB 並行）"
start_ingest() {
  aws bedrock-agent start-ingestion-job \
    --knowledge-base-id "$1" --data-source-id "$2" \
    --region "$AWS_REGION" --query 'ingestionJob.ingestionJobId' --output text
}
JOB_FIXED=$(start_ingest "$KB_ID_FIXED" "$DS_ID_FIXED")
JOB_HIER=$(start_ingest "$KB_ID_HIER" "$DS_ID_HIER")
JOB_SEM=$(start_ingest "$KB_ID_SEM" "$DS_ID_SEM")

wait_for_kb_ingestion "$KB_ID_FIXED" "$DS_ID_FIXED" "$JOB_FIXED" fixed "$AWS_REGION" 60 15
wait_for_kb_ingestion "$KB_ID_HIER" "$DS_ID_HIER" "$JOB_HIER" hierarchical "$AWS_REGION" 60 15
wait_for_kb_ingestion "$KB_ID_SEM" "$DS_ID_SEM" "$JOB_SEM" semantic "$AWS_REGION" 60 15

# -----------------------------------------------------------------------------
cat > "$ENV_FILE" <<EOF
AWS_REGION=${AWS_REGION}
RETRIEVE_MODEL_ARN=${RETRIEVE_MODEL_ARN}
SUFFIX=${SUFFIX}
BUCKET=${BUCKET}
KB_ROLE=${KB_ROLE}
AOSS_COLLECTION=${AOSS_COLLECTION}
INDEX_FIXED=${INDEX_FIXED}
INDEX_HIER=${INDEX_HIER}
INDEX_SEM=${INDEX_SEM}
KB_ID_FIXED=${KB_ID_FIXED}
KB_ID_HIER=${KB_ID_HIER}
KB_ID_SEM=${KB_ID_SEM}
DS_ID_FIXED=${DS_ID_FIXED}
DS_ID_HIER=${DS_ID_HIER}
DS_ID_SEM=${DS_ID_SEM}
EOF
chmod 600 "$ENV_FILE"

cat <<EOF

\033[1;32m完了しました。\033[0m
  KB_ID_FIXED = ${KB_ID_FIXED}
  KB_ID_HIER  = ${KB_ID_HIER}
  KB_ID_SEM   = ${KB_ID_SEM}
  BUCKET      = ${BUCKET}

比較実行の例:
  set -a; source infra/.env.chunking; set +a
  uv run compare_kb_variants.py \\
    --variant fixed=\$KB_ID_FIXED \\
    --variant hierarchical=\$KB_ID_HIER \\
    --variant semantic=\$KB_ID_SEM \\
    --model-arn \$RETRIEVE_MODEL_ARN \\
    --questions data/questions_chunking.jsonl \\
    --output-dir output/kb-variants

削除: bash $(basename "$SCRIPT_DIR")/teardown-chunking-kbs.sh
EOF
