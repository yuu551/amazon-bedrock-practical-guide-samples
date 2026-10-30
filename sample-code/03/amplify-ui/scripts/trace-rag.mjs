// 第3章のLambdaハンドラーをローカルから呼び、同じBedrock処理を実行する。
import { build } from "esbuild"
import { Writable } from "node:stream"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import { randomUUID } from "node:crypto"
import { fileURLToPath, pathToFileURL } from "node:url"
import path from "node:path"
import { parseArgs } from "node:util"

const { values } = parseArgs({ options: {
  question: { type: "string", default: "Enterpriseプランの監査ログの保存期間と、エクスポート方法を教えてください。" },
  hybrid: { type: "boolean", default: false },
  orchestration: { type: "boolean", default: false },
  output: { type: "string", default: "rag-trace-result.json" },
} })
if (!process.env.KNOWLEDGE_BASE_ID || !process.env.MODEL_ID) throw new Error("KNOWLEDGE_BASE_IDとMODEL_IDを設定してください。")
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const directory = await mkdtemp(path.join(root, ".rag-trace-"))
try {
  await build({ entryPoints: [path.join(root, "amplify/functions/rag-handler/handler.ts")], outfile: path.join(directory, "handler.mjs"), bundle: true, packages: "external", platform: "node", format: "esm", target: "node24" })
  globalThis.awslambda = { streamifyResponse: (fn) => fn, HttpResponseStream: { from: (stream) => stream } }
  const { handleRequest } = await import(pathToFileURL(path.join(directory, "handler.mjs")).href)
  let response = ""
  const stream = new Writable({ write(chunk, _encoding, callback) { response += chunk.toString(); callback() } })
  const requestId = randomUUID()
  await handleRequest({ body: JSON.stringify({ question: values.question, session_id: requestId, config: { useOrchestration: values.orchestration, searchType: values.hybrid ? "HYBRID" : "SEMANTIC" } }) }, stream, { awsRequestId: requestId })
  const events = response.trim().split("\n\n").filter(Boolean).map((entry) => {
    const [event, data] = entry.split("\n")
    return { event: event.slice(7), data: JSON.parse(data.slice(6)) }
  })
  const result = { requestId, destination: process.env.RAG_TRACE_DESTINATION ?? "none", question: values.question, answer: events.filter((e) => e.event === "chunk").map((e) => e.data.text).join(""), events }
  await writeFile(values.output, JSON.stringify(result, null, 2) + "\n")
  console.log(JSON.stringify({ output: values.output, summary: events.find((e) => e.event === "done")?.data, errors: events.filter((e) => e.event === "error").map((e) => e.data) }))
  if (events.some((e) => e.event === "error") || !result.answer) process.exitCode = 1
} finally {
  await rm(directory, { recursive: true, force: true })
}
