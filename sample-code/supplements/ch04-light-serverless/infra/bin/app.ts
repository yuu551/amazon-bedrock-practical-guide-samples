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
  atlassian: {
    baseUrl: env.ATLASSIAN_BASE_URL,
    confluenceSpaceKey: env.CONFLUENCE_SPACE_KEY,
    jiraProjectKey: env.JIRA_PROJECT_KEY,
    jiraIssueType: env.JIRA_ISSUE_TYPE,
  },
})
