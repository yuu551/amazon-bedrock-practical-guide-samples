import test from "node:test"
import assert from "node:assert/strict"
import { build } from "esbuild"
import { mkdtemp, rm } from "node:fs/promises"
import { Writable } from "node:stream"
import { pathToFileURL } from "node:url"
import { setTimeout as delay } from "node:timers/promises"
import { BedrockAgentRuntimeClient } from "@aws-sdk/client-bedrock-agent-runtime"
import { BedrockRuntimeClient } from "@aws-sdk/client-bedrock-runtime"
import { LangfuseSpanProcessor } from "@langfuse/otel"

// AWS呼び出しと送信先を差し替え、実際のOTel spanの階層・内容・終了順を検証する。
const spans = []
let flushCount = 0
LangfuseSpanProcessor.prototype.onEnd = function(span) { spans.push(span) }
LangfuseSpanProcessor.prototype.forceFlush = async function() { flushCount++ }
process.env.KNOWLEDGE_BASE_ID = "test-kb"
process.env.MODEL_ID = "test-model"
process.env.LANGFUSE_PUBLIC_KEY = "test-public"
process.env.LANGFUSE_SECRET_KEY = "test-secret"
process.env.KNOWLEDGE_BASE_SOURCE_BUCKET_NAME = "test-source-bucket"
process.env.KNOWLEDGE_BASE_SOURCE_PREFIXES = "sample-data"
process.env.AWS_ACCESS_KEY_ID = "test-access"
process.env.AWS_SECRET_ACCESS_KEY = "test-secret"
process.env.AWS_REGION = "us-east-1"
delete process.env.LANGFUSE_SECRET_ARN
delete process.env.AWS_SESSION_TOKEN
const directory = await mkdtemp(new URL("../.rag-trace-", import.meta.url))
await build({ entryPoints: [new URL("../amplify/functions/rag-handler/handler.ts", import.meta.url).pathname], outfile: `${directory}/handler.mjs`, bundle: true, packages: "external", platform: "node", format: "esm", target: "node24" })
globalThis.awslambda = { streamifyResponse: fn => fn, HttpResponseStream: { from: stream => stream } }
const { handleRequest } = await import(pathToFileURL(`${directory}/handler.mjs`).href)
const ref = { location: { s3Location: { uri: "s3://test-source-bucket/sample-data/example.md" } }, content: { text: "監査ログは730日保存する。" } }
async function invoke(config = {}) {
  spans.length = 0
  let raw = ""
  const stream = new Writable({ write(chunk, _encoding, callback) { raw += chunk.toString(); callback() } })
  await handleRequest({ body: JSON.stringify({ question: "監査ログは何日保存する？", config }) }, stream, { awsRequestId: "test-request" })
  return raw.trim().split("\n\n").map(block => { const [event, data] = block.split("\n"); return { event: event.slice(7), data: JSON.parse(data.slice(6)) } })
}
function normalResponse() {
  return { stream: (async function*() {
    yield { output: { text: "730" } }
    await delay(25)
    yield { output: { text: "日" } }
    yield { citation: { citation: { retrievedReferences: [ref] } } }
  })() }
}
function attrs(name) { return spans.find(s => s.name === name).attributes }
await test("RAG tracing", async t => {
  try {
    await t.test("既定ではトレースを作らず、回答と引用を返す", async () => {
      delete process.env.RAG_TRACE_DESTINATION
      BedrockAgentRuntimeClient.prototype.send = async () => normalResponse()
      const events = await invoke()
      assert.equal(events.filter(e => e.event === "chunk").map(e => e.data.text).join(""), "730日")
      assert.equal(events.find(e => e.event === "done").data.traceId, undefined)
      assert.equal(spans.length, 0)
    })
    await t.test("通常検索は全ストリームを計測し、署名URLを保存しない", async () => {
      process.env.RAG_TRACE_DESTINATION = "langfuse"
      const events = await invoke()
      assert.deepEqual(spans.map(s => s.name), ["retrieve-and-generate", "rag-request"])
      const [child, root] = spans
      assert.equal(child.parentSpanContext.spanId, root.spanContext().spanId)
      assert.equal(child.spanContext().traceId, events.find(e => e.event === "done").data.traceId)
      assert.ok(child.duration[0] * 1e3 + child.duration[1] / 1e6 >= 20)
      assert.match(attrs("rag-request")["langfuse.observation.output"], /730日/)
      assert.ok(events.find(e => e.event === "citation").data.downloadUrl.includes("X-Amz-Signature"))
      assert.ok(!JSON.stringify(spans.map(s => s.attributes)).includes("X-Amz-Signature"))
      assert.equal(root.attributes["gen_ai.usage.input_tokens"], undefined)
      assert.ok(flushCount > 0)
    })
    await t.test("資料横断は検索方式を各検索へ渡し、使用量を該当する生成に記録する", async () => {
      const searches = []
      BedrockRuntimeClient.prototype.send = async command => command.constructor.name === "ConverseCommand"
        ? { output: { message: { content: [{ text: JSON.stringify([{ route: "faq", query: "ログ FAQ" }, { route: "policy", query: "ログ 規程" }]) }] } }, usage: { inputTokens: 20, outputTokens: 10 } }
        : { stream: (async function*() { yield { contentBlockDelta: { delta: { text: "統合した回答" } } }; yield { metadata: { usage: { inputTokens: 40, outputTokens: 15 } } } })() }
      BedrockAgentRuntimeClient.prototype.send = async command => { searches.push(command.input.retrieveAndGenerateConfiguration.knowledgeBaseConfiguration.retrievalConfiguration.vectorSearchConfiguration); return { output: { text: "730日" }, citations: [{ retrievedReferences: [ref] }] } }
      await invoke({ useOrchestration: true, searchType: "SEMANTIC" })
      assert.deepEqual(spans.map(s => s.name), ["analyze-question", "retrieve-and-generate:faq", "retrieve-and-generate:policy", "synthesize", "rag-request"])
      const root = spans.at(-1)
      assert.ok(spans.slice(0, -1).every(s => s.parentSpanContext.spanId === root.spanContext().spanId))
      assert.deepEqual(searches.map(s => [s.overrideSearchType, s.filter.equals.value]), [["SEMANTIC", "faq"], ["SEMANTIC", "policy"]])
      assert.equal(attrs("analyze-question")["gen_ai.usage.input_tokens"], 20)
      assert.equal(attrs("synthesize")["gen_ai.usage.output_tokens"], 15)
      assert.equal(attrs("rag-request")["gen_ai.usage.input_tokens"], undefined)
      assert.equal(attrs("retrieve-and-generate:faq")["gen_ai.usage.output_tokens"], undefined)
    })
    await t.test("途中でストリームが失敗したら親子にエラーを記録し、doneを返さない", async () => {
      BedrockAgentRuntimeClient.prototype.send = async () => ({ stream: (async function*() { yield { output: { text: "途中" } }; throw new Error("stream failed") })() })
      const events = await invoke()
      assert.deepEqual(events.map(e => e.event), ["chunk", "error"])
      assert.ok(spans.every(s => s.status.code === 2))
      assert.equal(spans.length, 2)
    })
    await t.test("ストリームがない応答もエラーとして記録する", async () => {
      BedrockAgentRuntimeClient.prototype.send = async () => ({})
      const events = await invoke()
      assert.deepEqual(events.map(e => e.event), ["error"])
      assert.ok(spans.every(s => s.status.code === 2))
    })
  } finally { await rm(directory, { recursive: true, force: true }) }
})
