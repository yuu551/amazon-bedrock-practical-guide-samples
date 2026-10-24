#!/usr/bin/env bash
# 言い換え質問ごとにRetrieve APIの検索結果だけを比較する。
#
# 使い方:
#   bash sample-code/02/scripts/run-paraphrase-retrieval-eval.sh
#
# 前提:
#   - sample-code/02/infra/setup.sh（OpenSearch Serverless版）または
#     infra/setup-s3vectors.sh（S3 Vectors版）が完了していること
#   - AWS CLIとjqが利用できること
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# OpenSearch Serverless版の infra/.env を優先し、なければS3 Vectors版の infra/.env.s3vectors を使う
ENV_FILE="${ENV_FILE:-${CHAPTER_DIR}/infra/.env}"
if [ ! -f "$ENV_FILE" ] && [ -f "${CHAPTER_DIR}/infra/.env.s3vectors" ]; then
  ENV_FILE="${CHAPTER_DIR}/infra/.env.s3vectors"
fi
LOG_DIR="${CHAPTER_DIR}/logs/$(date +%Y%m%d)"
LOG_FILE="${LOG_DIR}/paraphrase-retrieval-eval.log"

if [ ! -f "$ENV_FILE" ]; then
  echo "infra/.env と infra/.env.s3vectors が見つかりません。先に infra/setup.sh か infra/setup-s3vectors.sh を実行してください。" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
mkdir -p "$LOG_DIR"

AWS_REGION="${AWS_REGION:-us-east-1}"
RETRIEVAL_CONFIG='{"vectorSearchConfiguration":{"numberOfResults":5}}'
QUESTIONS=(
  "SAML SSOでログインできない場合の確認項目を教えてください"
  "認証が通らなくて画面が戻される"
  "ログインできない"
  "ログインに失敗する"
  "シングルサインオンが通らない"
  "ACS URLが間違っているかも"
  "IdP証明書が切れた"
  "SAMLログインのトラブル全般"
)

{
  echo "=== paraphrase retrieval eval ($(date -Iseconds)) ==="
  echo "KNOWLEDGE_BASE_ID=$KB_ID"
  echo

  for question in "${QUESTIONS[@]}"; do
    echo "------------------------------------------------------------"
    echo "QUESTION: $question"
    echo "------------------------------------------------------------"

    retrieval_query="$(jq -nc --arg text "$question" '{text: $text}')"
    aws bedrock-agent-runtime retrieve \
      --knowledge-base-id "$KB_ID" \
      --retrieval-query "$retrieval_query" \
      --retrieval-configuration "$RETRIEVAL_CONFIG" \
      --region "$AWS_REGION" \
      --output json \
      | jq -r '
          .retrievalResults
          | to_entries[]
          | .key as $rank
          | .value as $result
          | ($result.location.s3Location.uri
             // $result.location.webLocation.url
             // $result.location.confluenceLocation.url
             // $result.location.salesforceLocation.url
             // $result.location.sharePointLocation.url
             // "") as $uri
          | ($uri | sub("^s3://[^/]+/sample-data/"; "")) as $source
          | [$rank + 1, ($result.score // 0), $source]
          | @tsv
        ' \
      | awk -F '\t' '{printf "[%d] スコア %.4f  %s\n", $1, $2, $3}'
    echo
  done
} | tee "$LOG_FILE"

echo
echo "ログ保存先: $LOG_FILE"
