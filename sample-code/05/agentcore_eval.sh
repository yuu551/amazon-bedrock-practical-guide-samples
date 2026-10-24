#!/usr/bin/env bash
# AgentCore Evaluations on-demand run for the chapter 4 support agent.
#
# 前提:
#   - agentcore CLI がインストールされていること（`npm i -g @aws/agentcore` など）
#   - 第4章の AgentCore Runtime (support_agent) がデプロイ済み
#   - 評価対象のセッショントレースが CloudWatch に記録されていること
#     （ない場合は先に `agentcore invoke` または Amplify UI から1回以上呼び出す）
#
# 参考: `agentcore run eval --help`
set -euo pipefail

RUNTIME_ARN="${RUNTIME_ARN:-}"
REGION="${AWS_REGION:-us-east-1}"
SESSION_ID="${SESSION_ID:-}"
OUTPUT_PATH="${OUTPUT_PATH:-logs/$(date +%Y%m%d)/agentcore_eval_result.json}"
MAX_ATTEMPTS="${AGENTCORE_EVAL_MAX_ATTEMPTS:-6}"

if [[ -z "$RUNTIME_ARN" ]]; then
  echo "RUNTIME_ARN is required. Set it to the AgentCore Runtime ARN to evaluate." >&2
  exit 2
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"

EVALUATOR_ARNS=(
  "arn:aws:bedrock-agentcore:::evaluator/Builtin.Helpfulness"
  "arn:aws:bedrock-agentcore:::evaluator/Builtin.ToolSelectionAccuracy"
)

SESSION_OPT=("--days" "7")
if [[ -n "$SESSION_ID" ]]; then
  SESSION_OPT+=("--session-id" "$SESSION_ID")
fi

evaluation_status=1
for ((attempt = 1; attempt <= MAX_ATTEMPTS; attempt++)); do
  if agentcore run eval \
    --runtime-arn "$RUNTIME_ARN" \
    --evaluator-arn "${EVALUATOR_ARNS[@]}" \
    --region "$REGION" \
    "${SESSION_OPT[@]}" \
    --json \
    --output "$OUTPUT_PATH"; then
    evaluation_status=0
    break
  fi
  if ((attempt < MAX_ATTEMPTS)); then
    echo "AgentCore trace is not ready; retrying in 60 seconds ($attempt/$MAX_ATTEMPTS)" >&2
    sleep 60
  fi
done

if ((evaluation_status != 0)); then
  exit "$evaluation_status"
fi

echo "Wrote evaluation result to $OUTPUT_PATH"
