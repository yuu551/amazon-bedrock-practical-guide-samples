import { App } from "aws-cdk-lib"
import { SupportAgentStack } from "../lib/support-agent-stack.js"

const env = process.env

function list(value: string | undefined): string[] {
  return (value ?? "")
    .split(",")
    .map((v) => v.trim().replace(/^\/+|\/+$/g, ""))
    .filter(Boolean)
}

const knowledgeBaseId = env.KNOWLEDGE_BASE_ID ?? ""
if (!knowledgeBaseId) {
  throw new Error("KNOWLEDGE_BASE_ID を設定してください（第2章で作ったKnowledge BaseのID）")
}

const prefix = env.APP_PREFIX ?? "ch04-light"

// WAF_ALLOWED_IPS はカンマ区切りのCIDR。マスクを省いたIPは1台だけを許可する（/32、IPv6は/128）
const allowedIps = (env.WAF_ALLOWED_IPS ?? "")
  .split(",")
  .map((v) => v.trim())
  .filter(Boolean)
  .map((ip) => (ip.includes("/") ? ip : `${ip}/${ip.includes(":") ? 128 : 32}`))
// WAF_ALLOWED_COUNTRIES はカンマ区切りの国コード（例: JP）
const allowedCountries = (env.WAF_ALLOWED_COUNTRIES ?? "")
  .split(",")
  .map((v) => v.trim().toUpperCase())
  .filter(Boolean)
const app = new App()

new SupportAgentStack(app, `${prefix}-stack`, {
  env: { account: env.CDK_DEFAULT_ACCOUNT, region: env.CDK_DEFAULT_REGION ?? "us-east-1" },
  prefix,
  knowledgeBaseId,
  modelId: env.MODEL_ID ?? "us.anthropic.claude-sonnet-4-6",
  sourceBucketName: env.KNOWLEDGE_BASE_SOURCE_BUCKET_NAME?.trim() || undefined,
  sourcePrefixes: list(env.KNOWLEDGE_BASE_SOURCE_PREFIXES),
  agentArtifact: env.AGENT_ARTIFACT === "zip" || env.AGENT_ARTIFACT === "codebuild" ? env.AGENT_ARTIFACT : "docker",
  agentAccess: env.AGENT_ACCESS === "api" ? "api" : "direct",
  waf: {
    enabled: env.ENABLE_WAF === "true" || allowedIps.length > 0 || allowedCountries.length > 0,
    allowedIps,
    allowedCountries,
  },
  atlassian: {
    baseUrl: env.ATLASSIAN_BASE_URL,
    confluenceSpaceKey: env.CONFLUENCE_SPACE_KEY,
    jiraProjectKey: env.JIRA_PROJECT_KEY,
    jiraIssueType: env.JIRA_ISSUE_TYPE,
  },
})
