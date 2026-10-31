// AgentCore Runtimeへzipで置くエージェントを agent/.build に組み立てる（AGENT_ARTIFACT=zip のときだけ）。
// arm64 Linux向けのwheelを uv で集めるので、Dockerは使わない
import { execFileSync } from "node:child_process"
import { chmodSync, cpSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

if (process.env.AGENT_ARTIFACT !== "zip") {
  console.log("agent: AGENT_ARTIFACT が zip ではないので、zipは作らない（コンテナはCDKがビルドする）")
  process.exit(0)
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const agentDir = path.join(root, "agent")
const outDir = path.join(agentDir, ".build")

rmSync(outDir, { recursive: true, force: true })
mkdirSync(outDir, { recursive: true })

execFileSync(
  "uv",
  [
    "pip",
    "install",
    "--quiet",
    "--python-platform",
    "aarch64-manylinux2014",
    "--python-version",
    "3.13",
    "--only-binary=:all:",
    "--target",
    outDir,
    "-r",
    path.join(agentDir, "requirements.txt"),
  ],
  { stdio: "inherit" },
)

cpSync(path.join(agentDir, "main.py"), path.join(outDir, "main.py"))

// Runtimeが読めるように、ディレクトリと実行ファイルは755、それ以外は644にそろえる。
// __pycache__ は中身が毎回変わり、アセットのハッシュがずれるので消す
function normalize(dir) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) {
      if (name === "__pycache__") {
        rmSync(p, { recursive: true, force: true })
        continue
      }
      chmodSync(p, 0o755)
      normalize(p)
    } else {
      chmodSync(p, dir.endsWith(`${path.sep}bin`) ? 0o755 : 0o644)
    }
  }
}
normalize(outDir)

console.log(`agent bundle: ${path.relative(root, outDir)}`)
