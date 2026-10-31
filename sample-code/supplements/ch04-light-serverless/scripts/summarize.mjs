// results/deploy-times.jsonl を集計して results/summary.md を作る。
// 各ログから「デプロイ本体」の時間（cdk: Deployment time、cdkd: Duration）も抜き出す
import { existsSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const records = readFileSync(path.join(root, "results/deploy-times.jsonl"), "utf8")
  .trim()
  .split("\n")
  .map((l) => JSON.parse(l))

function deployPhaseSeconds(r) {
  if (r.steps) return undefined
  if (!r.log || !existsSync(path.join(root, r.log))) return undefined
  const text = readFileSync(path.join(root, r.log), "utf8").replace(/\x1b\[[0-9;]*m/g, "")
  const cdk = text.match(/Deployment time: ([\d.]+)s/)
  if (cdk) return Number(cdk[1])
  const cdkd = text.match(/Duration: ([\d.]+)s/)
  if (cdkd) return Number(cdkd[1])
  return undefined
}

const fmt = (xs) => {
  const v = xs.filter((x) => typeof x === "number").sort((a, b) => a - b)
  if (v.length === 0) return "-"
  const med = v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2
  const r = (x) => Math.round(x)
  return v.length === 1 ? `${r(v[0])}` : `${r(med)}（${r(v[0])}〜${r(v.at(-1))}）`
}

// 表に出す順番と名前。ここにない組み合わせは表の最後に出す
const tools = {
  "amplify-hosting": "Amplify Hosting（第4章）",
  "amplify-sandbox": "Amplify sandbox（第4章）",
  "cdk-direct": "cdk deploy・Docker・画面から直接呼ぶ",
  "cdkd-direct": "cdkd deploy・Docker・画面から直接呼ぶ",
  "cdk-direct-codebuild": "cdk deploy・CodeBuild・画面から直接呼ぶ",
  "cdkd-direct-codebuild": "cdkd deploy・CodeBuild・画面から直接呼ぶ",
  "actions-cdk": "GitHub Actions（main）・cdk deploy・画面から直接呼ぶ",
  "actions-cdkd": "GitHub Actions（develop）・cdkd deploy・画面から直接呼ぶ",
  "cdk-docker": "cdk deploy・Docker",
  cdk: "cdk deploy・zip",
  "cdk-container": "cdk deploy・CodeBuild",
  "cdk-hotswap2": "cdk deploy --hotswap-fallback・zip",
  "cdk-express": "cdk deploy --express・zip",
  "cdkd-docker": "cdkd deploy・Docker",
  cdkd: "cdkd deploy・zip",
  "cdkd-no-wait": "cdkd deploy --no-wait・zip",
  "cdkd-container": "cdkd deploy・CodeBuild",
  "cdk-hotswap": "（参考）hotswap・Secretの差分が残っていた回",
}
const scenarios = {
  create: "初回作成",
  rebuild: "変更なしの再ビルド",
  web: "画面",
  api: "API",
  agent: "エージェント",
  "agent-amplify": "エージェント",
  "agent-switch": "エージェントのデプロイ形式の切り替え",
  destroy: "削除",
}

const groups = new Map()
for (const r of records) {
  if (r.derived) continue
  const key = `${r.tool}\t${r.scenario}`
  if (!groups.has(key)) groups.set(key, [])
  groups.get(key).push(r)
}
const order = (key) => {
  const [tool, scenario] = key.split("\t")
  const t = Object.keys(tools).indexOf(tool)
  const sc = Object.keys(scenarios).indexOf(scenario)
  return (t < 0 ? 99 : t) * 100 + (sc < 0 ? 99 : sc)
}

const lines = [
  "| 方式・エージェントのデプロイ形式 | 変更 | 回数 | 成功 | コマンド全体（秒） | デプロイ本体（秒） | 反映を確認できるまで（秒） |",
  "|---|---|---|---|---|---|---|",
]
for (const [key, rs] of [...groups].sort((a, b) => order(a[0]) - order(b[0]))) {
  const [tool, scenario] = key.split("\t")
  const ok = rs.filter((r) => r.exitCode === 0 || r.status === "SUCCEED")
  lines.push(
    `| ${tools[tool] ?? tool} | ${scenarios[scenario] ?? scenario} | ${rs.length} | ${ok.length} | ${fmt(ok.map((r) => r.commandSeconds))} | ${fmt(ok.map(deployPhaseSeconds))} | ${fmt(ok.map((r) => r.visibleSeconds))} |`,
  )
}
lines.push(
  "",
  "値は中央値（最小〜最大）。失敗した回は時間の集計から除く。",
  "Amplifyはジョブの開始から終了まで（Hosting）と ampx sandbox --once の実行時間（sandbox）。それ以外はビルドからコマンドが終わるまで。",
  "Amplify Hostingの「変更なしの再ビルド」は第4章の公開アプリ、それ以外のHostingの行は計測用に作ったアプリで測った。",
  "「画面から直接呼ぶ」と付いた行のほかは、HonoのAPIを挟む形（AGENT_ACCESS=api）で測った。",
)

const derived = records.filter((r) => r.derived)
if (derived.length) {
  lines.push("", "補足の記録:", ...derived.map((r) => `- ${tools[r.tool] ?? r.tool}・${scenarios[r.scenario] ?? r.scenario}: ${r.note}${r.visibleSeconds ? `（反映まで ${r.visibleSeconds}秒）` : ""}`))
}

writeFileSync(path.join(root, "results/summary.md"), `${lines.join("\n")}\n`)
console.log(lines.join("\n"))
