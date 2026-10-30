#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# spin-up-cheap-rag.sh で立てた検証用 RAG を片付けるスクリプト。
#
# やること:
#   1. ch03 の Amplify sandbox を破棄（pnpm exec ampx sandbox delete）
#   2. ch02 の S3 Vectors KB を破棄（teardown-s3vectors.sh）
#   3. ch03/amplify-ui/.env を削除
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CH02_INFRA="${REPO_ROOT}/sample-code/02/infra"
CH03_UI="${REPO_ROOT}/sample-code/03/amplify-ui"

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

read -r -p "ch03-cheap sandbox + KB を削除しますか？ (yes/[no]): " CONFIRM
if [ "${CONFIRM:-no}" != "yes" ]; then
  echo "中止しました。"
  exit 0
fi

log "(1/3) Amplify sandbox を削除"
if [ -d "$CH03_UI" ]; then
  (cd "$CH03_UI" && pnpm exec ampx sandbox delete --identifier ch03-cheap --yes 2>/dev/null || \
    echo "  (sandbox 既に無し or pnpm 未インストール)")
fi

log "(2/3) S3 Vectors KB を削除"
if [ -f "${CH02_INFRA}/.env.s3vectors" ]; then
  yes yes | bash "${CH02_INFRA}/teardown-s3vectors.sh" || true
else
  echo "  (.env.s3vectors なし — KB 既に削除済み？)"
fi

log "(3/3) ch03/amplify-ui/.env を削除"
rm -f "${CH03_UI}/.env"

echo -e "\n\033[1;32m片付け完了\033[0m"
