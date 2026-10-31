// 比較用。GitHub Actionsのワークフロー（github-actions/deploy.yml）でデプロイしたときの時間を記録する。
//   node scripts/measure-actions.mjs --repo OWNER/REPO --repo-dir /path/to/clone --branch develop --change agent --target target-direct-cdkd.json --runs 3
// 接続したリポジトリの目印（v1, v2, ...）を進めてpushし、そのコミットのワークフローが終わるまで待つ。
// 終わったあと、変更が画面やRuntimeから見えるまでも待つ（確認先は results/ の --target のファイル）
import { execFileSync } from "node:child_process"
import { appendFileSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, v, i, a) => (i % 2 ? acc : [...acc, [v.replace(/^--/, ""), a[i + 1]]]), []),
)
const repo = args.repo
const repoDir = args["repo-dir"]
const branch = args.branch ?? "develop"
const change = args.change
const runs = Number(args.runs ?? 1)
const tool = args.tool ?? (branch === "main" ? "actions-cdk" : "actions-cdkd")
const sampleDir = "sample-code/supplements/ch04-light-serverless"
const markers = {
  web: { file: `${sampleDir}/web/index.html`, pattern: /(name="app-version" content=")v(\d+)(")/ },
  agent: { file: `${sampleDir}/agent/main.py`, pattern: /(AGENT_VERSION = ")v(\d+)(")/ },
}

const sh = (cmd, a) => execFileSync(cmd, a, { encoding: "utf8" }).trim()
const git = (...a) => sh("git", ["-C", repoDir, ...a])
const gh = (...a) => JSON.parse(sh("gh", a))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const seconds = (a, b) => Math.round((new Date(b) - new Date(a)) / 1000)
const t = args.target ? JSON.parse(readFileSync(path.join(root, "results", args.target), "utf8")) : undefined

function pushChange(kind) {
  const m = markers[kind]
  const file = path.join(repoDir, m.file)
  const text = readFileSync(file, "utf8")
  const match = text.match(m.pattern)
  if (!match) throw new Error(`${m.file} に目印が見つかりません`)
  const next = `v${Number(match[2]) + 1}`
  writeFileSync(file, text.replace(m.pattern, `$1${next}$3`))
  git("commit", "-q", "-am", `計測: ${kind} を ${next} にする`)
  git("push", "-q", "origin", `HEAD:${branch}`)
  return { sha: git("rev-parse", "HEAD"), version: next }
}

async function currentVersion(kind) {
  if (kind === "web") {
    const html = await (await fetch(`${t.appUrl}/?t=${Date.now()}`, { cache: "no-store" })).text()
    return html.match(/name="app-version" content="(v\d+)"/)?.[1]
  }
  const token = sh("aws", [
    "cognito-idp", "admin-initiate-auth", "--region", t.region,
    "--user-pool-id", t.login.userPoolId, "--client-id", t.login.clientId,
    "--auth-flow", "ADMIN_USER_PASSWORD_AUTH",
    "--auth-parameters", `USERNAME=${t.login.username},PASSWORD=${t.login.password}`,
    "--query", "AuthenticationResult.AccessToken", "--output", "text",
  ])
  const res = await fetch(
    `https://bedrock-agentcore.${t.region}.amazonaws.com/runtimes/${encodeURIComponent(t.agentRuntimeArn)}/invocations?qualifier=DEFAULT`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": `measure-${Date.now()}-${Math.random().toString(36).slice(2)}-health`,
      },
      body: JSON.stringify({ health: true }),
    },
  )
  if (!res.ok) return undefined
  return (await res.text()).match(/"agentVersion":\s*"(v\d+)"/)?.[1]
}

for (let i = 0; i < runs; i++) {
  const pushedAt = new Date()
  const pushed = change ? pushChange(change) : { sha: git("rev-parse", "HEAD") }
  let run
  while (true) {
    await sleep(10_000)
    run = gh("run", "list", "--repo", repo, "--branch", branch, "--limit", "20",
      "--json", "databaseId,headSha,status,conclusion,createdAt,updatedAt").find((r) => r.headSha === pushed.sha)
    if (run?.status === "completed") break
  }
  const { jobs } = gh("api", `repos/${repo}/actions/runs/${run.databaseId}/jobs`)
  const job = jobs[0]
  const steps = Object.fromEntries(
    job.steps.filter((s) => s.started_at && s.completed_at).map((s) => [s.name, seconds(s.started_at, s.completed_at)]),
  )
  let visibleAt
  if (change && t && run.conclusion === "success") {
    const until = Date.now() + 15 * 60_000
    while (Date.now() < until) {
      try {
        if ((await currentVersion(change)) === pushed.version) {
          visibleAt = new Date()
          break
        }
      } catch {}
      await sleep(2000)
    }
  }
  const record = {
    tool,
    scenario: change ?? "rebuild",
    note: args.note ?? `GitHub Actions（${branch}、ubuntu-24.04-arm）`,
    runId: run.databaseId,
    status: run.conclusion === "success" ? "SUCCEED" : run.conclusion,
    startedAt: job.started_at,
    // ジョブの開始から終了まで。キューで待った時間は queuedSeconds に分ける
    commandSeconds: seconds(job.started_at, job.completed_at),
    queuedSeconds: seconds(run.createdAt, job.started_at),
    visibleSeconds: visibleAt ? Math.round((visibleAt - pushedAt) / 1000) : null,
    steps,
    expected: pushed.version,
  }
  appendFileSync(path.join(root, "results/deploy-times.jsonl"), `${JSON.stringify(record)}\n`)
  console.log(JSON.stringify(record))
}
