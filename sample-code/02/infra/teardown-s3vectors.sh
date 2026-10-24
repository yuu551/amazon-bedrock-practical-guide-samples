#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# S3 Vectors 版 第2章サンプル用リソースを削除するスクリプト。
# setup-s3vectors.sh で生成された .env.s3vectors を読み取って依存順に削除。
#
# 削除順（逆依存順）:
#   1. Knowledge Base の Data Source
#   2. Knowledge Base 本体
#   3. S3 Vectors の vector index
#   4. S3 Vectors の vector bucket
#   5. IAM ロール（インラインポリシー削除後）
#   6. S3 バケット（中身を含む）
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.s3vectors"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: ${ENV_FILE} が見つかりません。setup-s3vectors.sh 実行後のみ使えます。"
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

is_not_found() {
  [[ "$1" == *"NotFoundException"* ||
     "$1" == *"NoSuchEntity"* ||
     "$1" == *"NoSuchBucket"* ||
     "$1" == *"Not Found"* ||
     "$1" == *"404"* ]]
}

delete_or_confirm_absent() {
  local label="$1"
  shift
  local output
  if output="$("$@" 2>&1)"; then
    echo "  ${label}: 削除リクエスト送信済み"
    return 0
  fi
  if is_not_found "$output"; then
    echo "  ${label}: すでに存在しません"
    return 0
  fi
  echo "  ${label}: 削除失敗" >&2
  echo "$output" >&2
  return 1
}

wait_until_absent() {
  local label="$1"
  shift
  local output
  for _ in {1..60}; do
    if output="$("$@" 2>&1)"; then
      sleep 5
      continue
    fi
    if is_not_found "$output"; then
      echo "  ${label}: 削除確認済み"
      return 0
    fi
    echo "  ${label}: 削除確認中にエラー" >&2
    echo "$output" >&2
    return 1
  done
  echo "  ${label}: 削除確認がタイムアウト" >&2
  return 1
}

read -r -p "リソースを削除しますか？ (yes/[no]): " CONFIRM
if [ "${CONFIRM:-no}" != "yes" ]; then
  echo "中止しました。"
  exit 0
fi

# -----------------------------------------------------------------------------
log "(1/6) Data Source 削除"
if [ -n "${KB_ID:-}" ] && [ -n "${DS_ID:-}" ]; then
  delete_or_confirm_absent "data-source" aws bedrock-agent delete-data-source \
    --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" --region "$AWS_REGION"
  wait_until_absent "data-source" aws bedrock-agent get-data-source \
    --knowledge-base-id "$KB_ID" --data-source-id "$DS_ID" --region "$AWS_REGION"
else
  echo "  data-source: IDが未保存のためスキップ"
fi

log "(2/6) Knowledge Base 削除"
if [ -n "${KB_ID:-}" ]; then
  delete_or_confirm_absent "knowledge-base" aws bedrock-agent delete-knowledge-base \
    --knowledge-base-id "$KB_ID" --region "$AWS_REGION"
  wait_until_absent "knowledge-base" aws bedrock-agent get-knowledge-base \
    --knowledge-base-id "$KB_ID" --region "$AWS_REGION"
else
  echo "  knowledge-base: IDが未保存のためスキップ"
fi

log "(3/6) S3 Vectors index 削除"
delete_or_confirm_absent "vector-index" aws s3vectors delete-index \
  --vector-bucket-name "$VECTOR_BUCKET" --index-name "$VECTOR_INDEX" --region "$AWS_REGION"
wait_until_absent "vector-index" aws s3vectors get-index \
  --vector-bucket-name "$VECTOR_BUCKET" --index-name "$VECTOR_INDEX" --region "$AWS_REGION"

log "(4/6) S3 Vectors bucket 削除"
delete_or_confirm_absent "vector-bucket" aws s3vectors delete-vector-bucket \
  --vector-bucket-name "$VECTOR_BUCKET" --region "$AWS_REGION"
wait_until_absent "vector-bucket" aws s3vectors get-vector-bucket \
  --vector-bucket-name "$VECTOR_BUCKET" --region "$AWS_REGION"

log "(5/6) IAM ロール削除"
delete_or_confirm_absent "role-policy" aws iam delete-role-policy \
  --role-name "$KB_ROLE" --policy-name ch02-kbsv-inline
delete_or_confirm_absent "role" aws iam delete-role --role-name "$KB_ROLE"
wait_until_absent "role" aws iam get-role --role-name "$KB_ROLE"

log "(6/6) S3 バケット削除"
delete_or_confirm_absent "s3-bucket" aws s3 rb "s3://${BUCKET}" \
  --force --region "$AWS_REGION"

rm -f "$ENV_FILE"
echo -e "\n\033[1;32m削除完了\033[0m"
