#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第3章 トピックフォルダ追加同期スクリプト。
#
# 既存のKnowledge Baseのデータソース用S3バケットに、
# sample-code/03/sample-data/topics/ 配下のトピックフォルダ群を同期し、
# Knowledge Baseのインジェスチョンを起動する。
#
# 既存KBに対して追加データを乗せる用途のため、第2章の元データには手を入れない。
#
# 必要な環境変数:
#   KB_DATA_BUCKET        - KBのデータソース用S3バケット名
#   KNOWLEDGE_BASE_ID     - 既存KBのID
#   DATA_SOURCE_ID        - 既存KBのデータソースID
#   AWS_REGION (任意)     - デフォルト us-east-1
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${KB_DATA_BUCKET:?KB_DATA_BUCKET is required}"
: "${KNOWLEDGE_BASE_ID:?KNOWLEDGE_BASE_ID is required}"
: "${DATA_SOURCE_ID:?DATA_SOURCE_ID is required}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOPICS_DIR="$(cd "${SCRIPT_DIR}/../sample-data/topics" && pwd)"
# shellcheck source=../../common/kb-ingestion.sh
source "${SCRIPT_DIR}/../../common/kb-ingestion.sh"

if [ ! -d "${TOPICS_DIR}" ]; then
  echo "Topic directory not found: ${TOPICS_DIR}" >&2
  exit 1
fi

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

: "${TOPIC_S3_PREFIX:=sample-data/topics/}"

log "Topics source: ${TOPICS_DIR}"
log "Target prefix: s3://${KB_DATA_BUCKET}/${TOPIC_S3_PREFIX}"

log "(1/3) Sync topic folders to S3"
aws s3 sync "${TOPICS_DIR}/" "s3://${KB_DATA_BUCKET}/${TOPIC_S3_PREFIX}" \
  --region "$AWS_REGION" \
  --exclude '*.DS_Store' \
  --delete

log "(2/3) Start Knowledge Base ingestion job"
INGESTION_JOB_ID=$(aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$KNOWLEDGE_BASE_ID" \
  --data-source-id "$DATA_SOURCE_ID" \
  --region "$AWS_REGION" \
  --query 'ingestionJob.ingestionJobId' \
  --output text)

log "(3/3) Wait for ingestion job: ${INGESTION_JOB_ID}"
wait_for_kb_ingestion \
  "$KNOWLEDGE_BASE_ID" "$DATA_SOURCE_ID" "$INGESTION_JOB_ID" \
  "topic-data" "$AWS_REGION" 60 15

echo "トピックフォルダの取り込みが、失敗文書0件で完了しました。"
