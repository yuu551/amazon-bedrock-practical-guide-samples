// 比較用。第4章をAmplify Hostingで公開したアプリを再ビルドし、ジョブの時間を記録する。
//   node scripts/measure-amplify-hosting.mjs --app-id YOUR_APP_ID --branch main --runs 3
//   node scripts/measure-amplify-hosting.mjs --app-id ... --runs 3 --change web --repo-dir /path/to/repo
// --change を付けないと同じコミットを再ビルドする。
// --change web / agent では、接続したリポジトリの目印（v1, v2, ...）を進めてpushし、そのコミットをビルドする
import { execFileSync } from "node:child_process"
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, v, i, a) => (i % 2 ? acc : [...acc, [v.replace(/^--/, ""), a[i + 1]]]), []),
)
const appId = args["app-id"]
const branch = args.branch ?? "main"
const runs = Number(args.runs ?? 1)
const region = process.env.AWS_REGION ?? "us-east-1"
const change = args.change
const repoDir = args["repo-dir"]
const markers = {
  web: { file: "sample-code/04/amplify-ui/index.html", pattern: /(name="app-version" content=")v(\d+)(")/ },
  agent: { file: "sample-code/04/amplify-ui/amplify/agent/main.py", pattern: /(AGENT_VERSION = ")v(\d+)(")/ },
}

const git = (...a) => execFileSync("git", ["-C", repoDir, ...a], { encoding: "utf8" }).trim()

// 目印を1つ進めてpushし、そのコミットIDを返す
function pushChange(kind) {
  const m = markers[kind]
  const file = path.join(repoDir, m.file)
  const text = readFileSync(file, "utf8")
  const match = text.match(m.pattern)
  if (!match) throw new Error(`${m.file} に目印が見つかりません`)
  const next = `v${Number(match[2]) + 1}`
  writeFileSync(file, text.replace(m.pattern, `$1${next}$3`))
  git("commit", "-q", "-am", `計測: ${kind} を ${next} にする`)
  git("push", "-q", "origin", "HEAD:main")
  return { commitId: git("rev-parse", "HEAD"), version: next }
}

const aws = (...a) =>
  JSON.parse(execFileSync("aws", [...a, "--region", region, "--output", "json"], { encoding: "utf8" }))
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

mkdirSync(path.join(root, "results"), { recursive: true })

for (let i = 0; i < runs; i++) {
  const pushed = change ? pushChange(change) : undefined
  const started = Date.now()
  const { jobSummary } = aws(
    "amplify", "start-job", "--app-id", appId, "--branch-name", branch, "--job-type", "RELEASE",
    ...(pushed ? ["--commit-id", pushed.commitId, "--commit-message", `measure ${change} ${pushed.version}`] : []),
  )
  let job
  while (true) {
    await sleep(10_000)
    job = aws("amplify", "get-job", "--app-id", appId, "--branch-name", branch, "--job-id", jobSummary.jobId).job
    if (["SUCCEED", "FAILED", "CANCELLED"].includes(job.summary.status)) break
  }
  const steps = Object.fromEntries(
    job.steps.map((s) => [s.stepName, Math.round((new Date(s.endTime) - new Date(s.startTime)) / 1000)]),
  )
  const record = {
    tool: "amplify-hosting",
    scenario: args.scenario ?? (change ?? "rebuild"),
    note: args.note ?? (change ? `${change} の目印を ${pushed.version} にしたコミット` : "同じコミットの再ビルド（RELEASE）"),
    jobId: jobSummary.jobId,
    status: job.summary.status,
    startedAt: job.summary.startTime,
    commandSeconds: Math.round((new Date(job.summary.endTime) - new Date(job.summary.startTime)) / 1000),
    waitedSeconds: Math.round((Date.now() - started) / 1000),
    steps,
    commitId: job.summary.commitId,
  }
  appendFileSync(path.join(root, "results", "deploy-times.jsonl"), `${JSON.stringify(record)}\n`)
  console.log(JSON.stringify(record))
}
