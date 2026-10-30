#!/usr/bin/env bash
# 言い換え検証用: 「SAML SSOでログインできない場合の確認項目」の言い換え
# バリエーションで Knowledge Base のセマンティック検索がどう振る舞うかを確認する。
#
# 使い方:
#   bash sample-code/02/scripts/run-paraphrase-eval.sh
#
# 前提:
#   - sample-code/02/infra/setup.sh（OpenSearch Serverless版）または
#     infra/setup-s3vectors.sh（S3 Vectors版）が完了していること
#   - sample-code/02 で `uv sync` 済み
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHAPTER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# OpenSearch Serverless版の infra/.env を優先し、なければS3 Vectors版の infra/.env.s3vectors を使う
ENV_FILE="${ENV_FILE:-${CHAPTER_DIR}/infra/.env}"
if [ ! -f "$ENV_FILE" ] && [ -f "${CHAPTER_DIR}/infra/.env.s3vectors" ]; then
  ENV_FILE="${CHAPTER_DIR}/infra/.env.s3vectors"
fi
LOG_DIR="${CHAPTER_DIR}/logs/$(date +%Y%m%d)"
LOG_FILE="${LOG_DIR}/paraphrase-eval.log"

if [ ! -f "$ENV_FILE" ]; then
  echo "infra/.env と infra/.env.s3vectors が見つかりません。先に infra/setup.sh か infra/setup-s3vectors.sh を実行してください。" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
mkdir -p "$LOG_DIR"

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")"
MODEL_ARN="arn:aws:bedrock:${AWS_REGION}:${AWS_ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-6"

QUESTIONS=(
  "SAML SSOでログインできない場合の確認項目を教えてください"  # ベースライン（正規形）
  "認証が通らなくて画面が戻される"                           # 言い換え（口語・語彙ずれ）— セマンティック検索の優位性確認
  "ログインできない"                                       # 口語 — どの認証方式か不明
  "ログインに失敗する"                                     # 口語+曖昧 — SAML/APIキー/MFAのどれか不明
  "シングルサインオンが通らない"                           # 言い換え — IdP連携・SAML設定どちらに寄るか不明
  "ACS URLが間違っているかも"                              # 固有語ピンポイント — 設定ガイド側が引きやすい
  "IdP証明書が切れた"                                     # 期限切れ FAQ 狙いの曖昧表現
  "SAMLログインのトラブル全般"                             # 一般化された問い — 関連FAQが複数並ぶ
)

cd "$CHAPTER_DIR"

{
  echo "=== paraphrase eval ($(date -Iseconds)) ==="
  echo "KNOWLEDGE_BASE_ID=$KB_ID"
  echo "MODEL_ARN=$MODEL_ARN"
  echo

  for q in "${QUESTIONS[@]}"; do
    echo "------------------------------------------------------------"
    echo "QUESTION: $q"
    echo "------------------------------------------------------------"
    uv run retrieve_and_generate.py \
      --knowledge-base-id "$KB_ID" \
      --model-arn "$MODEL_ARN" \
      --question "$q" 2>&1 || true
    echo
  done
} | tee "$LOG_FILE"

echo
echo "ログ保存先: $LOG_FILE"
