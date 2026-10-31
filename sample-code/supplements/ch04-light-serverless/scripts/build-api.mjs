// HonoのAPIをLambda Web Functions用に api/dist へまとめる。
// ランタイムにAWS SDKは入っていないので、依存ごと index.js 1ファイルにする
import { build } from "esbuild"
import { mkdirSync, rmSync, writeFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const outDir = path.join(root, "api", "dist")

rmSync(outDir, { recursive: true, force: true })
mkdirSync(outDir, { recursive: true })

await build({
  entryPoints: [path.join(root, "api", "src", "server.ts")],
  outfile: path.join(outDir, "index.js"),
  bundle: true,
  platform: "node",
  target: "node24",
  format: "esm",
  minify: true,
  legalComments: "none",
  // ESMの出力で require を使う依存のために用意する
  banner: {
    js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);",
  },
})

writeFileSync(
  path.join(outDir, "package.json"),
  `${JSON.stringify({ type: "module" }, null, 2)}\n`,
)

console.log(`api bundle: ${path.relative(root, outDir)}`)
