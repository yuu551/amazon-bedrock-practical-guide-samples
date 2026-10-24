#!/usr/bin/env bash
# ハンズオンの環境を確認する。読み取りだけで、AWSのリソースは作らない。
# 使い方: bash .agents/skills/bedrock-handson/scripts/check_env.sh
set -u

region="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"

line() { printf '%-28s %s\n' "$1" "$2"; }

echo "== ツール"
for cmd in python3 uv node pnpm aws agentcore docker; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ver="$("$cmd" --version 2>&1 | head -1)"
    line "$cmd" "$ver"
  else
    line "$cmd" "(見つかりません)"
  fi
done

echo
echo "== AWS認証"
if command -v aws >/dev/null 2>&1; then
  if arn="$(aws sts get-caller-identity --query Arn --output text 2>&1)"; then
    line "認証" "$arn"
  else
    line "認証" "失敗: $(echo "$arn" | head -1)"
  fi
fi
line "AWS_PROFILE" "${AWS_PROFILE:-(未設定)}"
line "リージョン" "${region:-(未設定。サンプルは us-east-1 が前提です)}"

echo
echo "== 章をまたぐ値（設定されているかだけを表示）"
for v in KNOWLEDGE_BASE_ID DATA_SOURCE_ID MODEL_ARN MODEL_ID APP_API_URL GATEWAY_URL GUARDRAIL_ID JUDGE_MODEL_ID; do
  if [ -n "${!v:-}" ]; then line "$v" "設定済み"; else line "$v" "未設定"; fi
done

echo
echo "== 作成済みの設定ファイル"
for f in sample-code/02/infra/.env sample-code/02/infra/.env.s3vectors \
         sample-code/03/infra/.env.chunking sample-code/03/infra/.env \
         sample-code/02/amplify-ui/amplify_outputs.json sample-code/03/amplify-ui/amplify_outputs.json \
         sample-code/04/amplify-ui/amplify_outputs.json sample-code/06/amplify-ui/amplify_outputs.json; do
  if [ -f "$f" ]; then line "$f" "あります"; fi
done
