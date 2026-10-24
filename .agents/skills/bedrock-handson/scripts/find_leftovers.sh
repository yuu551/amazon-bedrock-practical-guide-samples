#!/usr/bin/env bash
# ハンズオンで作ったAWSリソースの残りを一覧にする。読み取りだけで、何も削除しない。
# 使い方: bash .agents/skills/bedrock-handson/scripts/find_leftovers.sh [リージョン]
# ほかの用途で作ったリソースも表示される。名前から判断できないものは、削除しないこと。
set -u
export AWS_PAGER=""
region="${1:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}}"

run() {
  local title="$1"; shift
  echo "== $title"
  if out="$("$@" 2>&1)"; then
    if [ -n "$out" ]; then echo "$out"; else echo "(なし)"; fi
  else
    echo "(取得できませんでした: $(echo "$out" | head -1))"
  fi
  echo
}

run "OpenSearch Serverlessのコレクション（料金への影響が最も大きい）" \
  aws opensearchserverless list-collections --region "$region" --query 'collectionSummaries[].[name,status]' --output text
run "Knowledge Base" \
  aws bedrock-agent list-knowledge-bases --region "$region" --query 'knowledgeBaseSummaries[].[name,status]' --output text
run "S3 Vectorsのベクトルバケット" \
  aws s3vectors list-vector-buckets --region "$region" --query 'vectorBuckets[].vectorBucketName' --output text
run "AgentCore Runtime" \
  aws bedrock-agentcore-control list-agent-runtimes --region "$region" --query 'agentRuntimes[].[agentRuntimeName,status]' --output text
run "AgentCore Gateway" \
  aws bedrock-agentcore-control list-gateways --region "$region" --query 'items[].[name,status]' --output text
run "Guardrail" \
  aws bedrock list-guardrails --region "$region" --query 'guardrails[].[name,id]' --output text
run "CloudFormationのスタック（SupportAi、KbUpdateAgent、amplify-、AgentCore- で始まるもの）" \
  aws cloudformation list-stacks --region "$region" \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED ROLLBACK_COMPLETE \
    --query "StackSummaries[?starts_with(StackName,'SupportAi')||starts_with(StackName,'KbUpdateAgent')||starts_with(StackName,'amplify-')||starts_with(StackName,'AgentCore-')].[StackName,StackStatus]" --output text
run "Secrets Manager（support-agent）" \
  aws secretsmanager list-secrets --region "$region" --query "SecretList[?starts_with(Name,'support-agent')].Name" --output text
