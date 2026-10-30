#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第3章 Parser 比較用 Knowledge Base を CLI で構築するスクリプト。
#
# 構成:
#   S3(sample-pdfs) --> [KB default parser]  --> AOSS index ch03-parser-default
#                   \-> [KB fm parser]       --> AOSS index ch03-parser-fm
#
#   どちらも同じ sample-pdfs/ を取り込むが、データソースの parsingConfiguration
#   がデフォルト / 基盤モデルパーサーで異なる。
#
# 前提:
#   - AWS CLI v2、jq、uv、Python 3.10+ が利用可能
#   - Bedrock で Titan Text Embeddings V2 と、指定する基盤モデルパーサー用モデル
#     （既定: Claude Haiku 4.5）へのアクセスが有効。FM パーサーに指定できるのは
#     Claude・Nova・Llama 4 のビジョン対応モデル。GPT-5.6 Luna は対象外
#   - 実行ロールに IAM / S3 / Bedrock / Bedrock-Agent / OpenSearchServerless の権限
#   - sample-code/03/generate_sample_pdfs.py を実行済みで
#     sample-pdfs/*.pdf が生成されている
#
# 削除時は teardown-parser-kbs.sh を実行。
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${SUFFIX:=$(python3 -c 'import secrets; print(secrets.token_hex(3))')}"
: "${PARSER_SAMPLE_FILE:=}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
: "${FM_PARSER_MODEL_ARN:=arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0}"

BUCKET="ch03-parser-sample-${SUFFIX}"
KB_ROLE="ch03-parser-kb-role-${SUFFIX}"
AOSS_COLLECTION="ch03-parser-${SUFFIX}"
AOSS_INDEX_DEFAULT="ch03-parser-default"
AOSS_INDEX_FM="ch03-parser-fm"
KB_NAME_DEFAULT="ch03-kb-default-parser-${SUFFIX}"
KB_NAME_FM="ch03-kb-fm-parser-${SUFFIX}"
EMBED_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=../../common/kb-ingestion.sh
source "${SCRIPT_DIR}/../../common/kb-ingestion.sh"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PDF_DIR="${CHAPTER_DIR}/sample-pdfs"
ENV_FILE="${SCRIPT_DIR}/.env"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [ ! -d "$PDF_DIR" ] || [ -z "$(ls -1 "$PDF_DIR"/*.pdf 2>/dev/null)" ]; then
  echo "sample-pdfs/*.pdf が見つかりません。先に uv run generate_sample_pdfs.py を実行してください。" >&2
  exit 1
fi
if [ -n "$PARSER_SAMPLE_FILE" ] && [ ! -f "$PDF_DIR/$PARSER_SAMPLE_FILE" ]; then
  echo "指定したPDFが見つかりません: $PDF_DIR/$PARSER_SAMPLE_FILE" >&2
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
# 1. S3 バケット作成 + サンプル PDF のアップロード
# -----------------------------------------------------------------------------
log "(1/7) S3 バケット作成と PDF アップロード (${BUCKET})"
aws s3 mb "s3://${BUCKET}" --region "$AWS_REGION"
if [ -n "$PARSER_SAMPLE_FILE" ]; then
  aws s3 cp "$PDF_DIR/$PARSER_SAMPLE_FILE" \
    "s3://${BUCKET}/sample-pdfs/$PARSER_SAMPLE_FILE" --region "$AWS_REGION"
else
  aws s3 sync "${PDF_DIR}/" "s3://${BUCKET}/sample-pdfs/" \
    --exclude "*.png" --exclude ".*" --region "$AWS_REGION"
fi

# -----------------------------------------------------------------------------
# 2. KB 用 IAM ロール
# -----------------------------------------------------------------------------
log "(2/7) Knowledge Base 用サービスロール作成 (${KB_ROLE})"
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
     "Resource":[
       "${EMBED_MODEL_ARN}",
       "${FM_PARSER_MODEL_ARN}",
       "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
       "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
       "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
     ]},
    {"Sid":"BedrockInferenceProfile","Effect":"Allow",
     "Action":["bedrock:GetInferenceProfile","bedrock:ListInferenceProfiles"],
     "Resource":"*"},
    {"Sid":"AOSSAPIAccessAll","Effect":"Allow","Action":"aoss:APIAccessAll",
     "Resource":"arn:aws:aoss:${AWS_REGION}:${AWS_ACCOUNT_ID}:collection/*"}
  ]
}
EOF
)
aws iam create-role --role-name "$KB_ROLE" \
  --assume-role-policy-document "$TRUST_DOC" \
  --description "Chapter 3 parser comparison KB service role" >/dev/null
aws iam put-role-policy --role-name "$KB_ROLE" \
  --policy-name ch03-parser-inline --policy-document "$POLICY_DOC"
KB_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${KB_ROLE}"

# -----------------------------------------------------------------------------
# 3. OpenSearch Serverless
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
  --description "Chapter 3 parser comparison vector store" \
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
# 4. ベクトルインデックスを2つ作成
# -----------------------------------------------------------------------------
log "(4/7) ベクトルインデックス作成 (${AOSS_INDEX_DEFAULT}, ${AOSS_INDEX_FM})"
AOSS_COLLECTION="$AOSS_COLLECTION" \
AOSS_INDEX_DEFAULT="$AOSS_INDEX_DEFAULT" \
AOSS_INDEX_FM="$AOSS_INDEX_FM" \
AWS_REGION="$AWS_REGION" \
  uv run python - <<'PY'
import os, time, boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
region = os.environ["AWS_REGION"]
collection = os.environ["AOSS_COLLECTION"]
indices = [os.environ["AOSS_INDEX_DEFAULT"], os.environ["AOSS_INDEX_FM"]]
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
# 5. Knowledge Base 2 つを作成
# -----------------------------------------------------------------------------
log "(5/7) Knowledge Base 作成（デフォルト / 基盤モデル パーサー）"
COLLECTION_ARN="$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" \
  --region "$AWS_REGION" --query 'collectionDetails[0].arn' --output text)"

create_kb() {
  local kb_name="$1"; local index_name="$2"
  local kb_id=""; local client_token="${SUFFIX}-${index_name}-knowledge-base"
  for attempt in {1..12}; do
    if kb_id="$(aws bedrock-agent create-knowledge-base \
      --client-token "$client_token" \
      --name "$kb_name" \
      --description "Chapter 3 parser comparison KB (${index_name})" \
      --role-arn "$KB_ROLE_ARN" \
      --knowledge-base-configuration "{\"type\":\"VECTOR\",\"vectorKnowledgeBaseConfiguration\":{\"embeddingModelArn\":\"${EMBED_MODEL_ARN}\"}}" \
      --storage-configuration "{\"type\":\"OPENSEARCH_SERVERLESS\",\"opensearchServerlessConfiguration\":{\"collectionArn\":\"${COLLECTION_ARN}\",\"vectorIndexName\":\"${index_name}\",\"fieldMapping\":{\"vectorField\":\"bedrock-knowledge-base-default-vector\",\"textField\":\"AMAZON_BEDROCK_TEXT_CHUNK\",\"metadataField\":\"AMAZON_BEDROCK_METADATA\"}}}" \
      --region "$AWS_REGION" --query 'knowledgeBase.knowledgeBaseId' --output text)"; then
      printf '%s\n' "$kb_id"
      return 0
    fi
    echo "[${attempt}/12] AOSSアクセスポリシーの伝播を待って再試行: ${kb_name}" >&2
    sleep 15
  done
  echo "Knowledge Baseを作成できませんでした: ${kb_name}" >&2
  return 1
}

KB_ID_DEFAULT="$(create_kb "$KB_NAME_DEFAULT" "$AOSS_INDEX_DEFAULT")"
KB_ID_FM="$(create_kb "$KB_NAME_FM" "$AOSS_INDEX_FM")"

# -----------------------------------------------------------------------------
# 6. データソース作成（パーサー設定が異なる）
# -----------------------------------------------------------------------------
log "(6/7) データソース作成"

DS_ID_DEFAULT="$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID_DEFAULT" \
  --name "ch03-default-parser" \
  --data-source-configuration "{\"type\":\"S3\",\"s3Configuration\":{\"bucketArn\":\"arn:aws:s3:::${BUCKET}\",\"inclusionPrefixes\":[\"sample-pdfs/\"]}}" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)"

FM_VAC_CONFIG=$(cat <<EOF
{
  "type": "S3",
  "s3Configuration": {
    "bucketArn": "arn:aws:s3:::${BUCKET}",
    "inclusionPrefixes": ["sample-pdfs/"]
  }
}
EOF
)
FM_PARSING_CONFIG=$(cat <<EOF
{
  "parsingConfiguration": {
    "parsingStrategy": "BEDROCK_FOUNDATION_MODEL",
    "bedrockFoundationModelConfiguration": {
      "modelArn": "${FM_PARSER_MODEL_ARN}",
      "parsingPrompt": {
        "parsingPromptText": "このページには表・図・グラフが含まれる場合があります。表は列見出しと各セルの対応を保ったまま Markdown のテーブル形式で書き出してください。図やグラフは、主要な要素（ラベル・矢印の流れ・凡例・数値ラベル）を箇条書きで要約してください。本文の一般的な段落はそのまま抽出してください。"
      }
    }
  }
}
EOF
)

DS_ID_FM="$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID_FM" \
  --name "ch03-fm-parser" \
  --data-source-configuration "$FM_VAC_CONFIG" \
  --vector-ingestion-configuration "$FM_PARSING_CONFIG" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)"

# -----------------------------------------------------------------------------
# 7. 取り込みジョブ
# -----------------------------------------------------------------------------
log "(7/7) 取り込みジョブ実行（2つのKBで並行）"
start_ingest() {
  aws bedrock-agent start-ingestion-job \
    --knowledge-base-id "$1" --data-source-id "$2" \
    --region "$AWS_REGION" --query 'ingestionJob.ingestionJobId' --output text
}
JOB_DEFAULT="$(start_ingest "$KB_ID_DEFAULT" "$DS_ID_DEFAULT")"
JOB_FM="$(start_ingest "$KB_ID_FM" "$DS_ID_FM")"

wait_for_kb_ingestion "$KB_ID_DEFAULT" "$DS_ID_DEFAULT" "$JOB_DEFAULT" "default-parser" "$AWS_REGION" 60 15
wait_for_kb_ingestion "$KB_ID_FM" "$DS_ID_FM" "$JOB_FM" "fm-parser" "$AWS_REGION" 60 15

# -----------------------------------------------------------------------------
# .env 保存
# -----------------------------------------------------------------------------
cat > "$ENV_FILE" <<EOF
AWS_REGION=${AWS_REGION}
SUFFIX=${SUFFIX}
BUCKET=${BUCKET}
KB_ROLE=${KB_ROLE}
AOSS_COLLECTION=${AOSS_COLLECTION}
AOSS_INDEX_DEFAULT=${AOSS_INDEX_DEFAULT}
AOSS_INDEX_FM=${AOSS_INDEX_FM}
KB_NAME_DEFAULT=${KB_NAME_DEFAULT}
KB_NAME_FM=${KB_NAME_FM}
KB_ID_DEFAULT=${KB_ID_DEFAULT}
KB_ID_FM=${KB_ID_FM}
DS_ID_DEFAULT=${DS_ID_DEFAULT}
DS_ID_FM=${DS_ID_FM}
FM_PARSER_MODEL_ARN=${FM_PARSER_MODEL_ARN}
PARSER_SAMPLE_FILE=${PARSER_SAMPLE_FILE}
EOF
chmod 600 "$ENV_FILE"

cat <<EOF

\033[1;32m完了しました。\033[0m
  KB_ID_DEFAULT = ${KB_ID_DEFAULT}
  KB_ID_FM      = ${KB_ID_FM}
  BUCKET        = ${BUCKET}

比較実行の例:
  export RETRIEVE_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-6"
  uv run compare_kb_variants.py \\
    --variant default_parser=${KB_ID_DEFAULT} \\
    --variant fm_parser=${KB_ID_FM} \\
    --questions data/questions_parser.jsonl

削除: bash $(basename "$SCRIPT_DIR")/teardown-parser-kbs.sh
EOF
