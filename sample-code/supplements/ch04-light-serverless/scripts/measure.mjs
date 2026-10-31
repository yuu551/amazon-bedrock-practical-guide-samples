// デプロイにかかる時間を測る。
//   node scripts/measure.mjs --tool cdkd --scenario api -- pnpm exec cdkd deploy --no-wait
//
// --scenario に web / api / agent を渡すと、目印（v1, v2, ...）を1つ進めてからコマンドを実行する。
// コマンドが終わった時刻と、変更がCloudFrontやRuntimeから見えるようになった時刻を分けて記録する。
// 確認先は results/target.json（appUrl, agentRuntimeArn, region。直接呼ぶ形では login も）から読む。--target で別のファイルも指定できる
import { spawn } from "node:child_process"
import { appendFileSync, createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const resultsDir = path.join(root, "results")
const rawDir = path.join(resultsDir, "raw")
mkdirSync(rawDir, { recursive: true })

const sep = process.argv.indexOf("--")
if (sep < 0) throw new Error("-- のあとに計測するコマンドを書いてください")
const opts = {}
const flags = process.argv.slice(2, sep)
for (let i = 0; i < flags.length; i += 2) {
  opts[flags[i].replace(/^--/, "")] = flags[i + 1]
}
const command = process.argv.slice(sep + 1)
const tool = opts.tool ?? "unknown"
const scenario = opts.scenario ?? "none"
const note = opts.note ?? ""
const verifyTimeoutMs = Number(opts["verify-timeout"] ?? 900) * 1000

const markers = {
  web: { file: "web/index.html", pattern: /(name="app-version" content=")v(\d+)(")/ },
  api: { file: "api/src/version.ts", pattern: /(API_VERSION = ")v(\d+)(")/ },
  agent: { file: "agent/main.py", pattern: /(AGENT_VERSION = ")v(\d+)(")/ },
}

function bump(kind) {
  const m = markers[kind]
  const file = path.join(root, m.file)
  const text = readFileSync(file, "utf8")
  const match = text.match(m.pattern)
  if (!match) throw new Error(`${m.file} に目印が見つかりません`)
  const next = `v${Number(match[2]) + 1}`
  writeFileSync(file, text.replace(m.pattern, `$1${next}$3`))
  return next
}

function target() {
  const file = path.join(resultsDir, opts.target ?? "target.json")
  if (!existsSync(file)) return null
  return JSON.parse(readFileSync(file, "utf8"))
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function currentVersion(kind, t) {
  if (kind === "web") {
    const res = await fetch(`${t.appUrl}/?t=${Date.now()}`, { cache: "no-store" })
    const html = await res.text()
    return html.match(/name="app-version" content="(v\d+)"/)?.[1]
  }
  if (kind === "api") {
    const res = await fetch(`${t.appUrl}/api/health?t=${Date.now()}`)
    if (!res.ok) return undefined
    return (await res.json()).version
  }
  if (kind === "agent" && t.login) {
    // 画面から直接呼ぶ形のRuntimeはCognitoのトークンで呼ぶ。トークンは管理者の認証情報で取る
    const auth = await run(
      "aws",
      [
        "cognito-idp", "admin-initiate-auth", "--region", t.region,
        "--user-pool-id", t.login.userPoolId, "--client-id", t.login.clientId,
        "--auth-flow", "ADMIN_USER_PASSWORD_AUTH",
        "--auth-parameters", `USERNAME=${t.login.username},PASSWORD=${t.login.password}`,
        "--query", "AuthenticationResult.AccessToken", "--output", "text",
      ],
      { quiet: true },
    )
    const token = auth.stdout.trim()
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
  if (kind === "agent") {
    // RuntimeはIAM認証なので、実行中のシェルの認証情報でAWS CLIから呼ぶ
    const outFile = path.join(rawDir, "agent-health.out")
    rmSync(outFile, { force: true })
    const out = await run(
      "aws",
      [
        "bedrock-agentcore",
        "invoke-agent-runtime",
        "--region",
        t.region,
        "--agent-runtime-arn",
        t.agentRuntimeArn,
        "--runtime-session-id",
        `measure-${Date.now()}-${Math.random().toString(36).slice(2)}-health`,
        "--payload",
        Buffer.from(JSON.stringify({ health: true })).toString("base64"),
        outFile,
      ],
      { quiet: true },
    )
    if (out.code !== 0 || !existsSync(outFile)) return undefined
    return readFileSync(outFile, "utf8").match(/"agentVersion":\s*"(v\d+)"/)?.[1]
  }
  return undefined
}

function run(cmd, args, { quiet = false, log } = {}) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd: root, env: process.env, shell: false })
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (d) => {
      stdout += d
      log?.write(d)
      if (!quiet) process.stdout.write(d)
    })
    child.stderr.on("data", (d) => {
      stderr += d
      log?.write(d)
      if (!quiet) process.stderr.write(d)
    })
    child.on("close", (code) => resolve({ code, stdout, stderr }))
  })
}

const expected = markers[scenario] ? bump(scenario) : undefined
const stamp = new Date().toISOString().replace(/[:.]/g, "-")
const logFile = path.join(rawDir, `${stamp}-${tool}-${scenario}.log`)
const log = createWriteStream(logFile)
log.write(`$ ${command.join(" ")}\n`)

const startedAt = Date.now()
const result = await run(command[0], command.slice(1), { log })
const commandEndedAt = Date.now()
log.end()

let visibleAt
let observed
const t = target()
if (result.code === 0 && expected && t) {
  while (Date.now() - commandEndedAt < verifyTimeoutMs) {
    try {
      observed = await currentVersion(scenario, t)
    } catch {
      observed = undefined
    }
    if (observed === expected) {
      visibleAt = Date.now()
      break
    }
    await sleep(2000)
  }
}

const record = {
  tool,
  scenario,
  note,
  command: command.join(" "),
  exitCode: result.code,
  expected,
  observed,
  startedAt: new Date(startedAt).toISOString(),
  commandSeconds: Math.round((commandEndedAt - startedAt) / 100) / 10,
  visibleSeconds: visibleAt ? Math.round((visibleAt - startedAt) / 100) / 10 : null,
  log: path.relative(root, logFile),
}
appendFileSync(path.join(resultsDir, "deploy-times.jsonl"), `${JSON.stringify(record)}\n`)
console.log(`\n[measure] ${JSON.stringify(record)}`)
process.exit(result.code ?? 1)
