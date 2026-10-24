#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 第3章の画面を、ベクトルストアの常時課金なしで試すためのスクリプト。
#
# やること:
#   1. 第2章の setup-s3vectors.sh を呼んで S3 Vectors 版 Knowledge Base を作る
#      （OpenSearch Serverless の OCU 課金がない／取り込むデータは ch02/sample-data/）
#   2. ch03/amplify-ui/.env を生成（KB ID と回答生成モデルを書き込む）
#   3. 続けて実行するコマンドを表示
#
# S3 Vectors はハイブリッド検索に対応しないため、画面の検索モードは
# 「意味検索」系だけを使う。ハイブリッド検索まで試す場合は第2章の setup.sh を使う。
#
# 使い方:
#   bash sample-code/03/infra/spin-up-cheap-rag.sh
#
# 環境変数で上書き可能:
#   AWS_REGION   us-east-1（デフォルト）
#   MODEL_ID     回答生成モデルの推論プロファイルIDまたはARN
#                デフォルト: us.anthropic.claude-sonnet-4-6（本編と同じ）
#
# 後片付け:
#   bash sample-code/03/infra/teardown-cheap-rag.sh
# -----------------------------------------------------------------------------
set -euo pipefail

: "${AWS_REGION:=us-east-1}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CH02_INFRA="${REPO_ROOT}/sample-code/02/infra"
CH03_UI="${REPO_ROOT}/sample-code/03/amplify-ui"

: "${MODEL_ID:=us.anthropic.claude-sonnet-4-6}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# -----------------------------------------------------------------------------
# 1. S3 Vectors KB を作成（ch02 の setup-s3vectors.sh をそのまま使う）
# -----------------------------------------------------------------------------
log "(1/2) S3 Vectors Knowledge Base を作成 (ch02/infra/setup-s3vectors.sh)"
AWS_REGION="$AWS_REGION" bash "${CH02_INFRA}/setup-s3vectors.sh"

# setup-s3vectors.sh が書いた .env.s3vectors を読む
ENV_FROM_KB="${CH02_INFRA}/.env.s3vectors"
if [ ! -f "$ENV_FROM_KB" ]; then
  echo "error: ${ENV_FROM_KB} が見つかりません。" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FROM_KB"; set +a

# -----------------------------------------------------------------------------
# 2. ch03/amplify-ui/.env を生成
# -----------------------------------------------------------------------------
log "(2/2) ${CH03_UI}/.env を生成"
mkdir -p "$CH03_UI"
cat > "${CH03_UI}/.env" <<EOF
KNOWLEDGE_BASE_ID=${KB_ID}
MODEL_ID=${MODEL_ID}
BEDROCK_REGION=${AWS_REGION}
EOF
chmod 600 "${CH03_UI}/.env"

# -----------------------------------------------------------------------------
# 完了メッセージ
# -----------------------------------------------------------------------------
printf '\n\033[1;32m%s\033[0m\n' "準備できました。"
cat <<EOF
  KB_ID         = ${KB_ID}
  MODEL_ID      = ${MODEL_ID}
  AWS_REGION    = ${AWS_REGION}
  .env          = ${CH03_UI}/.env

次の手順（sample-code/03/amplify-ui で実行）:
  pnpm install --frozen-lockfile
  set -a; source .env; set +a
  pnpm exec ampx sandbox --identifier ch03-cheap

  # デプロイが終わったら、別のターミナルで画面を起動
  pnpm run dev

後片付け（リポジトリのルートで実行）:
  bash sample-code/03/infra/teardown-cheap-rag.sh
EOF
