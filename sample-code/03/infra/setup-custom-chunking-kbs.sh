#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第3章 カスタムチャンキング検証用 Knowledge Base を構築するスクリプト。
#
# 2つの戦略で KB を立てる:
#   - fixed        : FIXED_SIZE (300 tokens, 20% overlap)
#   - custom       : NONE + customTransformationConfiguration (POST_CHUNKING Lambda)
#
# 対象データは sample-faq/sampleconnect-faq.md（25件のFAQ）。
# customは「## Q??.」見出し単位でLambdaが分割する。
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${SUFFIX:=$(python3 -c 'import secrets; print(secrets.token_hex(3))')}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
: "${RETRIEVE_MODEL_ARN:=arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-6}"

BUCKET="ch03-custom-sample-${SUFFIX}"
INTERMEDIATE_BUCKET="ch03-custom-intermediate-${SUFFIX}"
KB_ROLE="ch03-custom-kb-role-${SUFFIX}"
LAMBDA_ROLE="ch03-custom-lambda-role-${SUFFIX}"
LAMBDA_NAME="ch03-custom-chunking-${SUFFIX}"
AOSS_COLLECTION="ch03-custom-${SUFFIX}"
EMBED_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0"

INDEX_FIXED="ch03-custom-fixed"
INDEX_CUSTOM="ch03-custom-lambda"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../common/kb-ingestion.sh
source "${SCRIPT_DIR}/../../common/kb-ingestion.sh"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FAQ_DIR="${CHAPTER_DIR}/sample-faq"
LAMBDA_DIR="${SCRIPT_DIR}/lambda/custom_chunking"
ENV_FILE="${SCRIPT_DIR}/.env.custom"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [ ! -f "${FAQ_DIR}/sampleconnect-faq.md" ]; then
  echo "sample-faq/sampleconnect-faq.md が見つかりません。" >&2
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
log "(1/9) S3 バケット作成（データソース + intermediate は別バケット）"
aws s3 mb "s3://${BUCKET}" --region "$AWS_REGION"
aws s3 mb "s3://${INTERMEDIATE_BUCKET}" --region "$AWS_REGION"
aws s3 cp "${FAQ_DIR}/sampleconnect-faq.md" "s3://${BUCKET}/sample-faq/sampleconnect-faq.md" \
  --region "$AWS_REGION"

# -----------------------------------------------------------------------------
log "(2/9) Lambda 実行ロール作成 (${LAMBDA_ROLE})"
LAMBDA_TRUST=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
)
LAMBDA_POLICY=$(cat <<EOF
{
  "Version":"2012-10-17",
  "Statement":[
    {"Sid":"Logs","Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"arn:aws:logs:${AWS_REGION}:${AWS_ACCOUNT_ID}:*"},
    {"Sid":"S3IntermediateRW","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket"],"Resource":["arn:aws:s3:::${INTERMEDIATE_BUCKET}","arn:aws:s3:::${INTERMEDIATE_BUCKET}/*"]}
  ]
}
EOF
)
aws iam create-role --role-name "$LAMBDA_ROLE" \
  --assume-role-policy-document "$LAMBDA_TRUST" \
  --description "Chapter 3 custom chunking Lambda role" >/dev/null
aws iam put-role-policy --role-name "$LAMBDA_ROLE" \
  --policy-name ch03-custom-lambda-inline --policy-document "$LAMBDA_POLICY"
LAMBDA_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${LAMBDA_ROLE}"
log "IAM ロール伝播待機 (10秒)"
sleep 10

# -----------------------------------------------------------------------------
log "(3/9) Lambda 関数デプロイ (${LAMBDA_NAME})"
ZIP_PATH="$(mktemp -d)/custom_chunking.zip"
(cd "$LAMBDA_DIR" && zip -q -r "$ZIP_PATH" main.py)
LAMBDA_ARN=$(aws lambda create-function \
  --function-name "$LAMBDA_NAME" \
  --runtime python3.12 \
  --handler main.handler \
  --role "$LAMBDA_ROLE_ARN" \
  --timeout 60 \
  --memory-size 512 \
  --zip-file "fileb://${ZIP_PATH}" \
  --region "$AWS_REGION" \
  --query 'FunctionArn' --output text)
rm -f "$ZIP_PATH"

# BedrockがLambdaを呼べるようにリソースベースポリシーを付与
aws lambda add-permission \
  --function-name "$LAMBDA_NAME" \
  --statement-id BedrockKBInvoke \
  --action lambda:InvokeFunction \
  --principal bedrock.amazonaws.com \
  --source-account "$AWS_ACCOUNT_ID" \
  --region "$AWS_REGION" >/dev/null

# -----------------------------------------------------------------------------
log "(4/9) KB 用 IAM ロール作成 (${KB_ROLE})"
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
    {"Sid":"S3RW","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:ListBucket"],
     "Resource":["arn:aws:s3:::${BUCKET}","arn:aws:s3:::${BUCKET}/*","arn:aws:s3:::${INTERMEDIATE_BUCKET}","arn:aws:s3:::${INTERMEDIATE_BUCKET}/*"],
     "Condition":{"StringEquals":{"aws:ResourceAccount":"${AWS_ACCOUNT_ID}"}}},
    {"Sid":"BedrockEmbedding","Effect":"Allow","Action":"bedrock:InvokeModel",
     "Resource":"${EMBED_MODEL_ARN}"},
    {"Sid":"AOSSAPIAccessAll","Effect":"Allow","Action":"aoss:APIAccessAll",
     "Resource":"arn:aws:aoss:${AWS_REGION}:${AWS_ACCOUNT_ID}:collection/*"},
    {"Sid":"LambdaInvoke","Effect":"Allow","Action":"lambda:InvokeFunction",
     "Resource":["${LAMBDA_ARN}","${LAMBDA_ARN}:*"]}
  ]
}
EOF
)
aws iam create-role --role-name "$KB_ROLE" \
  --assume-role-policy-document "$TRUST_DOC" \
  --description "Chapter 3 custom chunking KB service role" >/dev/null
aws iam put-role-policy --role-name "$KB_ROLE" \
  --policy-name ch03-custom-kb-inline --policy-document "$POLICY_DOC"
KB_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${KB_ROLE}"

# -----------------------------------------------------------------------------
log "(5/9) AOSS ポリシーとコレクション作成"
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
  --description "Chapter 3 custom chunking vector store" \
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
log "(6/9) ベクトルインデックスを2つ作成"
uv pip install --quiet 'opensearch-py>=2.8.0'
AOSS_COLLECTION="$AOSS_COLLECTION" AWS_REGION="$AWS_REGION" \
INDEX_FIXED="$INDEX_FIXED" INDEX_CUSTOM="$INDEX_CUSTOM" \
  uv run python - <<'PY'
import os, time, boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
region = os.environ["AWS_REGION"]
collection = os.environ["AOSS_COLLECTION"]
indices = [os.environ["INDEX_FIXED"], os.environ["INDEX_CUSTOM"]]
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
log "(7/9) Knowledge Base 2つを作成"
COLLECTION_ARN="$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" \
  --region "$AWS_REGION" --query 'collectionDetails[0].arn' --output text)"
sleep 20

create_kb() {
  local name="$1"; local index="$2"
  aws bedrock-agent create-knowledge-base \
    --name "$name" \
    --description "Chapter 3 custom chunking KB (${index})" \
    --role-arn "$KB_ROLE_ARN" \
    --knowledge-base-configuration "{\"type\":\"VECTOR\",\"vectorKnowledgeBaseConfiguration\":{\"embeddingModelArn\":\"${EMBED_MODEL_ARN}\"}}" \
    --storage-configuration "{\"type\":\"OPENSEARCH_SERVERLESS\",\"opensearchServerlessConfiguration\":{\"collectionArn\":\"${COLLECTION_ARN}\",\"vectorIndexName\":\"${index}\",\"fieldMapping\":{\"vectorField\":\"bedrock-knowledge-base-default-vector\",\"textField\":\"AMAZON_BEDROCK_TEXT_CHUNK\",\"metadataField\":\"AMAZON_BEDROCK_METADATA\"}}}" \
    --region "$AWS_REGION" --query 'knowledgeBase.knowledgeBaseId' --output text
}

KB_ID_FIXED=$(create_kb "ch03-faq-fixed-${SUFFIX}" "$INDEX_FIXED")
KB_ID_CUSTOM=$(create_kb "ch03-faq-custom-${SUFFIX}" "$INDEX_CUSTOM")

# -----------------------------------------------------------------------------
log "(8/9) データソース作成"
DS_CFG="{\"type\":\"S3\",\"s3Configuration\":{\"bucketArn\":\"arn:aws:s3:::${BUCKET}\",\"inclusionPrefixes\":[\"sample-faq/\"]}}"

# fixed size
FIXED_CFG='{"chunkingConfiguration":{"chunkingStrategy":"FIXED_SIZE","fixedSizeChunkingConfiguration":{"maxTokens":300,"overlapPercentage":20}}}'
DS_ID_FIXED=$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID_FIXED" --name faq-fixed-ds \
  --data-source-configuration "$DS_CFG" \
  --vector-ingestion-configuration "$FIXED_CFG" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)

# custom: NONE + POST_CHUNKING Lambda
CUSTOM_CFG=$(cat <<EOF
{
  "chunkingConfiguration": {"chunkingStrategy": "NONE"},
  "customTransformationConfiguration": {
    "intermediateStorage": {"s3Location": {"uri": "s3://${INTERMEDIATE_BUCKET}/intermediate/"}},
    "transformations": [{
      "stepToApply": "POST_CHUNKING",
      "transformationFunction": {
        "transformationLambdaConfiguration": {"lambdaArn": "${LAMBDA_ARN}"}
      }
    }]
  }
}
EOF
)
DS_ID_CUSTOM=$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID_CUSTOM" --name faq-custom-ds \
  --data-source-configuration "$DS_CFG" \
  --vector-ingestion-configuration "$CUSTOM_CFG" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)

# -----------------------------------------------------------------------------
log "(9/9) 取り込みジョブ実行（2 KB 並行）"
start_ingest() {
  aws bedrock-agent start-ingestion-job \
    --knowledge-base-id "$1" --data-source-id "$2" \
    --region "$AWS_REGION" --query 'ingestionJob.ingestionJobId' --output text
}
JOB_FIXED=$(start_ingest "$KB_ID_FIXED" "$DS_ID_FIXED")
JOB_CUSTOM=$(start_ingest "$KB_ID_CUSTOM" "$DS_ID_CUSTOM")

wait_for_kb_ingestion "$KB_ID_FIXED" "$DS_ID_FIXED" "$JOB_FIXED" fixed "$AWS_REGION" 80 15
wait_for_kb_ingestion "$KB_ID_CUSTOM" "$DS_ID_CUSTOM" "$JOB_CUSTOM" custom "$AWS_REGION" 80 15

# -----------------------------------------------------------------------------
cat > "$ENV_FILE" <<EOF
AWS_REGION=${AWS_REGION}
RETRIEVE_MODEL_ARN=${RETRIEVE_MODEL_ARN}
SUFFIX=${SUFFIX}
BUCKET=${BUCKET}
INTERMEDIATE_BUCKET=${INTERMEDIATE_BUCKET}
KB_ROLE=${KB_ROLE}
LAMBDA_ROLE=${LAMBDA_ROLE}
LAMBDA_NAME=${LAMBDA_NAME}
LAMBDA_ARN=${LAMBDA_ARN}
AOSS_COLLECTION=${AOSS_COLLECTION}
INDEX_FIXED=${INDEX_FIXED}
INDEX_CUSTOM=${INDEX_CUSTOM}
KB_ID_FIXED=${KB_ID_FIXED}
KB_ID_CUSTOM=${KB_ID_CUSTOM}
DS_ID_FIXED=${DS_ID_FIXED}
DS_ID_CUSTOM=${DS_ID_CUSTOM}
EOF
chmod 600 "$ENV_FILE"

cat <<EOF

\033[1;32m完了しました。\033[0m
  KB_ID_FIXED  = ${KB_ID_FIXED}
  KB_ID_CUSTOM = ${KB_ID_CUSTOM}
  BUCKET       = ${BUCKET}
  LAMBDA       = ${LAMBDA_NAME}

比較実行の例:
  set -a; source infra/.env.custom; set +a
  uv run compare_kb_variants.py \\
    --variant fixed=\$KB_ID_FIXED \\
    --variant custom=\$KB_ID_CUSTOM \\
    --model-arn \$RETRIEVE_MODEL_ARN \\
    --questions data/questions_faq.jsonl \\
    --output-dir output/kb-variants

削除: bash $(basename "$SCRIPT_DIR")/teardown-custom-chunking-kbs.sh
EOF
