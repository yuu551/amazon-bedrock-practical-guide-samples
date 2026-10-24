#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第2章サンプル用 Knowledge Base を S3 Vectors で構築するスクリプト。
#
# 構成（setup.sh の AOSS 版に対する S3 Vectors 版）:
#   S3(sample-data) --(sync)--> Knowledge Base (S3 Vectors)
#     - ベクトルストア: S3 vector bucket + vector index
#     - 埋め込みモデル: amazon.titan-embed-text-v2:0 (1024次元, cosine)
#     - データソース: 本リポジトリの sample-code/02/sample-data/ を S3 経由で取り込み
#
# S3 Vectors は AOSS と比べてインフラ常駐費が発生しないため、検証用途や
# サブ秒応答で足りる RAG では大幅にコストを抑えられる（セマンティック検索のみ）。
#
# 前提:
#   - AWS CLI v2、jq、uv、Python 3.10+ が利用可能
#   - Anthropic Claude Sonnet 4.6 のサブスクリプション完了 + First Time Use form 提出済み（Titan Embeddings V2 は既定で利用可）
#   - 実行ロールに IAM / S3 / Bedrock / Bedrock-Agent / s3vectors の権限
#   - S3 Vectors 対応リージョン（us-east-1 等）
#
# 削除時は teardown-s3vectors.sh を実行。
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${SUFFIX:=$(python3 -c 'import secrets; print(secrets.token_hex(3))')}"
: "${ENABLE_RERANKING:=false}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"

if [[ "$ENABLE_RERANKING" != "true" && "$ENABLE_RERANKING" != "false" ]]; then
  echo "error: ENABLE_RERANKING must be 'true' or 'false'" >&2
  exit 2
fi

BUCKET="ch02-kbsv-sample-data-${SUFFIX}"
KB_ROLE="ch02-kbsv-service-role-${SUFFIX}"
VECTOR_BUCKET="ch02-kbsv-vectors-${SUFFIX}"
VECTOR_INDEX="ch02-kbsv-index"
KB_NAME="ch02-kbsv-${SUFFIX}"
EMBED_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}::foundation-model/amazon.titan-embed-text-v2:0"
RERANK_MODEL_ARN="arn:aws:bedrock:${AWS_REGION}::foundation-model/cohere.rerank-v3-5:0"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.s3vectors"
# shellcheck source=../../common/kb-ingestion.sh
source "${SCRIPT_DIR}/../../common/kb-ingestion.sh"

KB_ID=""
DS_ID=""
JOB_ID=""
EXPECTED_DOCUMENT_COUNT=33
DATA_SOURCE_PREFIX="sample-data/"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

write_env() {
  umask 077
  cat > "$ENV_FILE" <<EOF
AWS_REGION=${AWS_REGION}
SUFFIX=${SUFFIX}
ENABLE_RERANKING=${ENABLE_RERANKING}
EXPECTED_DOCUMENT_COUNT=${EXPECTED_DOCUMENT_COUNT}
DATA_SOURCE_PREFIX=${DATA_SOURCE_PREFIX}
BUCKET=${BUCKET}
KB_ROLE=${KB_ROLE}
VECTOR_BUCKET=${VECTOR_BUCKET}
VECTOR_INDEX=${VECTOR_INDEX}
VECTOR_INDEX_ARN=${VECTOR_INDEX_ARN:-}
KB_NAME=${KB_NAME}
KB_ID=${KB_ID}
DS_ID=${DS_ID}
JOB_ID=${JOB_ID}
RERANK_MODEL_ARN=${RERANK_MODEL_ARN}
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
# 2. S3 vector bucket + vector index 作成（AOSS の代替）
# -----------------------------------------------------------------------------
log "(2/6) S3 vector bucket 作成 (${VECTOR_BUCKET})"
aws s3vectors create-vector-bucket \
  --vector-bucket-name "$VECTOR_BUCKET" \
  --region "$AWS_REGION" >/dev/null
write_env

log "(3/6) vector index 作成 (${VECTOR_INDEX}, 1024dim, cosine)"
# Titan Embed Text V2 の既定出力次元 1024 / cosine / float32
# Bedrock Knowledge Bases が使うチャンク本文とメタデータをnon-filterableにする。
# OpenSearch Serverlessのフィールド名 AMAZON_BEDROCK_TEXT_CHUNK とは異なる。
aws s3vectors create-index \
  --vector-bucket-name "$VECTOR_BUCKET" \
  --index-name "$VECTOR_INDEX" \
  --data-type float32 \
  --dimension 1024 \
  --distance-metric cosine \
  --metadata-configuration '{"nonFilterableMetadataKeys":["AMAZON_BEDROCK_TEXT","AMAZON_BEDROCK_METADATA"]}' \
  --region "$AWS_REGION" >/dev/null

VECTOR_INDEX_ARN="arn:aws:s3vectors:${AWS_REGION}:${AWS_ACCOUNT_ID}:bucket/${VECTOR_BUCKET}/index/${VECTOR_INDEX}"
write_env

# -----------------------------------------------------------------------------
# 3. KB 用 IAM ロール（S3 + Bedrock + s3vectors 権限）
# -----------------------------------------------------------------------------
log "(4/6) Knowledge Base 用サービスロール作成 (${KB_ROLE})"
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
RERANK_POLICY_STATEMENTS=""
if [ "$ENABLE_RERANKING" = "true" ]; then
  # bedrock:Rerank はリソースレベル権限に未対応のため Resource は "*" が必要。
  # 実際に呼び出せるリランキングモデルは InvokeModel の ARN で限定する。
  RERANK_POLICY_STATEMENTS=$(cat <<EOF
    {"Sid":"BedrockRerankingModel","Effect":"Allow","Action":"bedrock:InvokeModel",
     "Resource":"${RERANK_MODEL_ARN}"},
    {"Sid":"BedrockReranking","Effect":"Allow","Action":"bedrock:Rerank",
     "Resource":"*"},
EOF
)
fi

POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid":"S3Read","Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],
     "Resource":["arn:aws:s3:::${BUCKET}","arn:aws:s3:::${BUCKET}/*"],
     "Condition":{"StringEquals":{"aws:ResourceAccount":"${AWS_ACCOUNT_ID}"}}},
    {"Sid":"BedrockEmbeddingModel","Effect":"Allow","Action":"bedrock:InvokeModel",
     "Resource":"${EMBED_MODEL_ARN}"},
${RERANK_POLICY_STATEMENTS}
    {"Sid":"S3VectorsAccess","Effect":"Allow",
     "Action":[
       "s3vectors:PutVectors",
       "s3vectors:GetVectors",
       "s3vectors:DeleteVectors",
       "s3vectors:QueryVectors",
       "s3vectors:GetIndex"
     ],
     "Resource":"${VECTOR_INDEX_ARN}"}
  ]
}
EOF
)
aws iam create-role --role-name "$KB_ROLE" \
  --assume-role-policy-document "$TRUST_DOC" \
  --description "Chapter 2 KB (S3 Vectors) service role" >/dev/null
aws iam put-role-policy --role-name "$KB_ROLE" \
  --policy-name ch02-kbsv-inline --policy-document "$POLICY_DOC"
KB_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${KB_ROLE}"
write_env

# IAM ロール伝播待ち
sleep 10

# -----------------------------------------------------------------------------
# 4. Knowledge Base + データソース
# -----------------------------------------------------------------------------
log "(5/6) Knowledge Base + データソース作成"
STORAGE_CFG=$(cat <<EOF
{
  "type":"S3_VECTORS",
  "s3VectorsConfiguration":{
    "indexArn":"${VECTOR_INDEX_ARN}"
  }
}
EOF
)

KB_ID="$(aws bedrock-agent create-knowledge-base \
  --name "$KB_NAME" \
  --description "Chapter 2 sample KB (S3 Vectors)" \
  --role-arn "$KB_ROLE_ARN" \
  --knowledge-base-configuration "{\"type\":\"VECTOR\",\"vectorKnowledgeBaseConfiguration\":{\"embeddingModelArn\":\"${EMBED_MODEL_ARN}\"}}" \
  --storage-configuration "$STORAGE_CFG" \
  --region "$AWS_REGION" --query 'knowledgeBase.knowledgeBaseId' --output text)"
write_env

DS_ID="$(aws bedrock-agent create-data-source \
  --knowledge-base-id "$KB_ID" \
  --name "ch02-s3-source" \
  --data-source-configuration "{\"type\":\"S3\",\"s3Configuration\":{\"bucketArn\":\"arn:aws:s3:::${BUCKET}\",\"inclusionPrefixes\":[\"${DATA_SOURCE_PREFIX}\"]}}" \
  --region "$AWS_REGION" --query 'dataSource.dataSourceId' --output text)"
write_env

log "(6/6) 取り込みジョブ実行"
JOB_ID="$(aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" \
  --region "$AWS_REGION" --query 'ingestionJob.ingestionJobId' --output text)"
write_env
wait_for_kb_ingestion \
  "$KB_ID" "$DS_ID" "$JOB_ID" "s3-vectors-markdown" \
  "$AWS_REGION" 30 15 "$EXPECTED_DOCUMENT_COUNT"

# -----------------------------------------------------------------------------
# .env 保存（teardown-s3vectors が参照する）
# -----------------------------------------------------------------------------
write_env

printf '\n\033[1;32m%s\033[0m\n' "完了しました。"
cat <<EOF
  KB_ID          = ${KB_ID}
  DS_ID          = ${DS_ID}
  BUCKET         = ${BUCKET}
  VECTOR_BUCKET  = ${VECTOR_BUCKET}
  VECTOR_INDEX   = ${VECTOR_INDEX}
  CORPUS         = Markdown ${EXPECTED_DOCUMENT_COUNT}文書（faq.xlsxは除外）
  RERANKING      = ${ENABLE_RERANKING}

CLI 動作確認（sample-code/02 で実行。以降の章でも同じ変数を使います）:
  export KNOWLEDGE_BASE_ID=${KB_ID}
  export DATA_SOURCE_ID=${DS_ID}
  export MODEL_ARN="arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-6"
  export MODEL_ID=us.anthropic.claude-sonnet-4-6
  uv run retrieve_and_generate.py --question "SAML SSOでログインできない場合の確認項目を教えてください"

削除（sample-code/02 で実行）: bash infra/teardown-s3vectors.sh
EOF
