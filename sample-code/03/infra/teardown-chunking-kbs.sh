#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第3章 チャンク戦略比較用リソースを削除するスクリプト。
# setup-chunking-kbs.sh で生成された .env.chunking を読んで依存順に削除。
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.chunking"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: ${ENV_FILE} が見つかりません。setup-chunking-kbs.sh 実行後のみ使えます。"
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

read -r -p "第3章 チャンク比較用リソースを削除しますか？ (yes/[no]): " CONFIRM
if [ "${CONFIRM:-no}" != "yes" ]; then
  echo "中止しました。"
  exit 0
fi

delete_ds() {
  aws bedrock-agent delete-data-source \
    --knowledge-base-id "$1" --data-source-id "$2" \
    --region "$AWS_REGION" >/dev/null 2>&1 || echo "  ($2 既に無し)"
}
delete_kb() {
  aws bedrock-agent delete-knowledge-base \
    --knowledge-base-id "$1" --region "$AWS_REGION" >/dev/null 2>&1 \
    || echo "  ($1 既に無し)"
}

log "(1/6) Data Source 削除"
delete_ds "$KB_ID_FIXED" "$DS_ID_FIXED"
delete_ds "$KB_ID_HIER" "$DS_ID_HIER"
delete_ds "$KB_ID_SEM" "$DS_ID_SEM"

log "(2/6) Knowledge Base 削除"
delete_kb "$KB_ID_FIXED"
delete_kb "$KB_ID_HIER"
delete_kb "$KB_ID_SEM"

log "(3/6) AOSS コレクション削除"
aws opensearchserverless delete-collection \
  --id "$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" \
    --region "$AWS_REGION" --query 'collectionDetails[0].id' --output text 2>/dev/null)" \
  --region "$AWS_REGION" >/dev/null 2>&1 || echo "  (collection 既に無し)"

log "(4/6) AOSS セキュリティ / アクセスポリシー削除"
aws opensearchserverless delete-access-policy \
  --name "${AOSS_COLLECTION}-data" --type data --region "$AWS_REGION" >/dev/null 2>&1 \
  || true
for TYPE_NAME in network encryption; do
  aws opensearchserverless delete-security-policy \
    --name "${AOSS_COLLECTION}-${TYPE_NAME}" --type "$TYPE_NAME" \
    --region "$AWS_REGION" >/dev/null 2>&1 || true
done

log "(5/6) IAM ロール削除"
aws iam delete-role-policy --role-name "$KB_ROLE" --policy-name ch03-chunking-inline 2>/dev/null || true
aws iam delete-role --role-name "$KB_ROLE" 2>/dev/null || echo "  (role 既に無し)"

log "(6/6) S3 バケット削除"
aws s3 rm "s3://${BUCKET}" --recursive --region "$AWS_REGION" 2>/dev/null || true
aws s3 rb "s3://${BUCKET}" --region "$AWS_REGION" 2>/dev/null || echo "  (bucket 既に無し)"

rm -f "$ENV_FILE"
echo -e "\n\033[1;32m削除完了\033[0m"
