#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第3章 Parser 比較用リソースを削除するスクリプト。
# setup-parser-kbs.sh で生成された .env を読み取って依存順に削除。
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "error: ${ENV_FILE} が見つかりません。setup-parser-kbs.sh 実行後のみ使えます。"
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

read -r -p "第3章 Parser 比較用リソースを削除しますか？ (yes/[no]): " CONFIRM
if [ "${CONFIRM:-no}" != "yes" ]; then
  echo "中止しました。"
  exit 0
fi

log "(1/6) Data Source 削除"
aws bedrock-agent delete-data-source \
  --knowledge-base-id "$KB_ID_DEFAULT" --data-source-id "$DS_ID_DEFAULT" \
  --region "$AWS_REGION" >/dev/null 2>&1 || echo "  (default DS 既に無し)"
aws bedrock-agent delete-data-source \
  --knowledge-base-id "$KB_ID_FM" --data-source-id "$DS_ID_FM" \
  --region "$AWS_REGION" >/dev/null 2>&1 || echo "  (fm DS 既に無し)"

log "(2/6) Knowledge Base 削除"
aws bedrock-agent delete-knowledge-base \
  --knowledge-base-id "$KB_ID_DEFAULT" --region "$AWS_REGION" >/dev/null 2>&1 \
  || echo "  (default KB 既に無し)"
aws bedrock-agent delete-knowledge-base \
  --knowledge-base-id "$KB_ID_FM" --region "$AWS_REGION" >/dev/null 2>&1 \
  || echo "  (fm KB 既に無し)"

log "(3/6) AOSS コレクション削除"
COLLECTION_ID="$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" \
  --region "$AWS_REGION" --query 'collectionDetails[0].id' --output text 2>/dev/null)"
if [ -n "$COLLECTION_ID" ] && [ "$COLLECTION_ID" != "None" ]; then
  aws opensearchserverless delete-collection \
    --id "$COLLECTION_ID" --region "$AWS_REGION" >/dev/null
  for _ in {1..30}; do
    COLLECTION_STATUS="$(aws opensearchserverless batch-get-collection \
      --names "$AOSS_COLLECTION" --region "$AWS_REGION" \
      --query 'collectionDetails[0].status' --output text 2>/dev/null)"
    [ -z "$COLLECTION_STATUS" ] || [ "$COLLECTION_STATUS" = "None" ] && break
    sleep 5
  done
else
  echo "  (collection 既に無し)"
fi

log "(4/6) AOSS セキュリティ / アクセスポリシー削除"
aws opensearchserverless delete-access-policy \
  --name "${AOSS_COLLECTION}-data" --type data --region "$AWS_REGION" >/dev/null 2>&1 \
  || true
for TYPE_NAME in net enc; do
  aws opensearchserverless delete-security-policy \
    --name "${AOSS_COLLECTION}-${TYPE_NAME}" \
    --type "$([ "$TYPE_NAME" = "net" ] && echo network || echo encryption)" \
    --region "$AWS_REGION" >/dev/null 2>&1 || true
done

log "(5/6) IAM ロール削除"
aws iam delete-role-policy --role-name "$KB_ROLE" --policy-name ch03-parser-inline 2>/dev/null || true
aws iam delete-role --role-name "$KB_ROLE" 2>/dev/null || echo "  (role 既に無し)"

log "(6/6) S3 バケット削除"
aws s3 rm "s3://${BUCKET}" --recursive --region "$AWS_REGION" 2>/dev/null || true
aws s3 rb "s3://${BUCKET}" --region "$AWS_REGION" 2>/dev/null || echo "  (bucket 既に無し)"

rm -f "$ENV_FILE"
echo -e "\n\033[1;32m削除完了\033[0m"
