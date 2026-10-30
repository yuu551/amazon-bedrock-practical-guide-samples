#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第2章サンプル用リソースを削除するスクリプト。
# setup.sh で生成された .env を読み取って依存順に削除。
#
# 削除順（逆依存順）:
#   1. Knowledge Base の Data Source
#   2. Knowledge Base 本体
#   3. AOSS コレクション（インデックスは自動削除）
#   4. AOSS アクセスポリシーとセキュリティポリシー
#   5. IAM ロール（インラインポリシーの削除後）
#   6. S3 バケット（中身を含む）
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: ${ENV_FILE} が見つかりません。setup.sh 実行後のみ使えます。"
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

is_not_found() {
  [[ "$1" == *"NotFoundException"* ||
     "$1" == *"ResourceNotFoundException"* ||
     "$1" == *"NoSuchEntity"* ||
     "$1" == *"NoSuchBucket"* ||
     "$1" == *"NOT_FOUND"* ||
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

wait_collection_absent() {
  local count
  for _ in {1..60}; do
    count="$(aws opensearchserverless batch-get-collection \
      --names "$AOSS_COLLECTION" --region "$AWS_REGION" \
      --query 'length(collectionDetails)' --output text)"
    if [ "$count" = "0" ]; then
      echo "  collection: 削除確認済み"
      return 0
    fi
    sleep 5
  done
  echo "  collection: 削除確認がタイムアウト" >&2
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

log "(3/6) AOSS コレクション削除"
COLLECTION_ID="$(aws opensearchserverless batch-get-collection \
  --names "$AOSS_COLLECTION" --region "$AWS_REGION" \
  --query 'collectionDetails[0].id' --output text)"
if [ "$COLLECTION_ID" = "None" ]; then
  echo "  collection: すでに存在しません"
else
  delete_or_confirm_absent "collection" aws opensearchserverless delete-collection \
    --id "$COLLECTION_ID" --region "$AWS_REGION"
  wait_collection_absent
fi

log "(4/6) AOSS ポリシー削除"
delete_or_confirm_absent "data access policy" \
  aws opensearchserverless delete-access-policy \
  --name "${AOSS_COLLECTION}-data" --type data --region "$AWS_REGION"
delete_or_confirm_absent "network security policy" \
  aws opensearchserverless delete-security-policy \
  --name "${AOSS_COLLECTION}-net" --type network --region "$AWS_REGION"
delete_or_confirm_absent "encryption security policy" \
  aws opensearchserverless delete-security-policy \
  --name "${AOSS_COLLECTION}-enc" --type encryption --region "$AWS_REGION"

log "(5/6) IAM ロール削除"
delete_or_confirm_absent "role-policy" aws iam delete-role-policy \
  --role-name "$KB_ROLE" --policy-name ch02-kb-inline
delete_or_confirm_absent "role" aws iam delete-role --role-name "$KB_ROLE"
wait_until_absent "role" aws iam get-role --role-name "$KB_ROLE"

log "(6/6) S3 バケット削除"
delete_or_confirm_absent "s3-bucket" aws s3 rb "s3://${BUCKET}" \
  --force --region "$AWS_REGION"

rm -f "$ENV_FILE"
printf '\n\033[1;32m削除完了\033[0m\n'
