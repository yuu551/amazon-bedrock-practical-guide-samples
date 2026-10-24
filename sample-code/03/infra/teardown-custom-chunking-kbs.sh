#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# setup-custom-chunking-kbs.sh で作ったリソースを一括削除する。
# .env.custom の値をもとに、KB・データソース・AOSS・S3・Lambda・IAMを片付ける。
# -----------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.custom"
if [ ! -f "$ENV_FILE" ]; then
  echo ".env.custom が見つかりません。setup-custom-chunking-kbs.sh を先に実行してください。" >&2
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

log "Knowledge Base 削除"
for kb in "$KB_ID_FIXED" "$KB_ID_CUSTOM"; do
  ds_ids=$(aws bedrock-agent list-data-sources --knowledge-base-id "$kb" \
    --region "$AWS_REGION" --query 'dataSourceSummaries[].dataSourceId' --output text 2>/dev/null || echo "")
  for ds in $ds_ids; do
    aws bedrock-agent delete-data-source --knowledge-base-id "$kb" --data-source-id "$ds" --region "$AWS_REGION" >/dev/null 2>&1 || true
  done
  aws bedrock-agent delete-knowledge-base --knowledge-base-id "$kb" --region "$AWS_REGION" >/dev/null 2>&1 || true
done

log "AOSS コレクション・ポリシー削除"
aws opensearchserverless delete-collection --id "$(aws opensearchserverless batch-get-collection --names "$AOSS_COLLECTION" --region "$AWS_REGION" --query 'collectionDetails[0].id' --output text 2>/dev/null)" --region "$AWS_REGION" >/dev/null 2>&1 || true
for t in data; do
  aws opensearchserverless delete-access-policy --name "${AOSS_COLLECTION}-${t}" --type "$t" --region "$AWS_REGION" >/dev/null 2>&1 || true
done
for t in encryption network; do
  suffix=$([ "$t" = "encryption" ] && echo enc || echo net)
  aws opensearchserverless delete-security-policy --name "${AOSS_COLLECTION}-${suffix}" --type "$t" --region "$AWS_REGION" >/dev/null 2>&1 || true
done

log "Lambda 関数削除"
aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$AWS_REGION" >/dev/null 2>&1 || true

log "IAM ロール削除"
for role in "$KB_ROLE" "$LAMBDA_ROLE"; do
  for p in $(aws iam list-role-policies --role-name "$role" --query 'PolicyNames' --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "$role" --policy-name "$p" >/dev/null 2>&1 || true
  done
  aws iam delete-role --role-name "$role" >/dev/null 2>&1 || true
done

log "S3 バケット削除"
for b in "$BUCKET" "${INTERMEDIATE_BUCKET:-}"; do
  [ -z "$b" ] && continue
  aws s3 rm "s3://${b}" --recursive --region "$AWS_REGION" >/dev/null 2>&1 || true
  aws s3 rb "s3://${b}" --region "$AWS_REGION" >/dev/null 2>&1 || true
done

rm -f "$ENV_FILE"

echo "削除しました。"
