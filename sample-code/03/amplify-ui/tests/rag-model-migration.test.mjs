import test from "node:test"
import assert from "node:assert/strict"
import { build } from "esbuild"
import { mkdtemp, rm } from "node:fs/promises"
import { Writable } from "node:stream"
import { pathToFileURL } from "node:url"
import { BedrockAgentRuntimeClient } from "@aws-sdk/client-bedrock-agent-runtime"
import { BedrockRuntimeClient } from "@aws-sdk/client-bedrock-runtime"

process.env.KNOWLEDGE_BASE_ID = "test-kb"
process.env.MODEL_ID = "us.anthropic.claude-sonnet-5"
process.env.ROUTE_MODEL_ARN = "us.anthropic.claude-sonnet-4-6"
process.env.RAG_TRACE_DESTINATION = "none"
const directory = await mkdtemp(new URL("../.rag-models-", import.meta.url))
await build({ entryPoints: [new URL("../amplify/functions/rag-handler/handler.ts", import.meta.url).pathname], outfile: `${directory}/handler.mjs`, bundle: true, packages: "external", platform: "node", format: "esm", target: "node24" })
globalThis.awslambda = { streamifyResponse: fn => fn, HttpResponseStream: { from: stream => stream } }
const { handleRequest } = await import(pathToFileURL(`${directory}/handler.mjs`).href)

async function invoke(config = {}, handler = handleRequest) {
  let raw = ""
  const stream = new Writable({ write(chunk, _encoding, callback) { raw += chunk.toString(); callback() } })
  await handler({ body: JSON.stringify({ question: "監査ログの保存期間とエクスポート方法は？", config }) }, stream)
  return raw.trim().split("\n\n").map(block => {
    const [event, data] = block.split("\n")
    return { event: event.slice(7), data: JSON.parse(data.slice(6)) }
  })
}

await test("新モデルへのRAG移行", async t => {
  try {
    await t.test("通常検索で両プロンプトとクエリ分解を同時に渡す", async () => {
      BedrockAgentRuntimeClient.prototype.send = async command => {
        const config = command.input.retrieveAndGenerateConfiguration.knowledgeBaseConfiguration
        assert.match(config.generationConfiguration.promptTemplate.textPromptTemplate, /\$search_results\$/)
        assert.match(config.generationConfiguration.promptTemplate.textPromptTemplate, /\$output_format_instructions\$/)
        assert.match(config.orchestrationConfiguration.promptTemplate.textPromptTemplate, /\$conversation_history\$/)
        assert.equal(config.orchestrationConfiguration.queryTransformationConfiguration.type, "QUERY_DECOMPOSITION")
        assert.equal(config.generationConfiguration.inferenceConfig.textInferenceConfig.temperature, undefined)
        return { stream: (async function*() { yield { output: { text: "730日" } } })() }
      }
      const events = await invoke({ useQueryDecomposition: true })
      assert.ok(events.some(e => e.event === "done"))
    })
    await t.test("思考ブロックの後に分割された分析結果を読み、指定した別モデルで文書を検索する", async () => {
      const searches = []
      BedrockRuntimeClient.prototype.send = async command => {
        assert.deepEqual(command.input.inferenceConfig, { maxTokens: 4096 })
        if (command.constructor.name === "ConverseCommand") {
          return { output: { message: { content: [
            { reasoningContent: { reasoningText: { text: "", signature: "test-signature" } } },
            { text: '[{"route":"manual","query":"エクスポート"},' },
            { text: '{"route":"policy","query":"保存期間"}]' },
          ] } }, stopReason: "end_turn" }
        }
        return { stream: (async function*() {
          yield { contentBlockDelta: { delta: { text: "統合した回答" } } }
          yield { messageStop: { stopReason: "end_turn" } }
        })() }
      }
      BedrockAgentRuntimeClient.prototype.send = async command => {
        const config = command.input.retrieveAndGenerateConfiguration.knowledgeBaseConfiguration
        assert.equal(config.modelArn, "us.anthropic.claude-sonnet-4-6")
        assert.equal(config.generationConfiguration, undefined)
        searches.push(config.retrievalConfiguration.vectorSearchConfiguration.filter.equals.value)
        return { output: { text: "資料から得た回答" }, citations: [] }
      }
      const events = await invoke({ useOrchestration: true, searchType: "SEMANTIC" })
      assert.deepEqual(searches, ["manual", "policy"])
      assert.equal(events.filter(e => e.event === "chunk").map(e => e.data.text).join(""), "統合した回答")
      assert.ok(events.some(e => e.event === "done"))
    })
    await t.test("分析が出力上限で終わったら検索へ進まずエラーにする", async () => {
      BedrockRuntimeClient.prototype.send = async () => ({ output: { message: { content: [] } }, stopReason: "max_tokens" })
      BedrockAgentRuntimeClient.prototype.send = async () => { throw new Error("検索へ進んではいけない") }
      const events = await invoke({ useOrchestration: true })
      assert.match(events.find(e => e.event === "error").data.message, /質問分析.*max_tokens/)
      assert.ok(!events.some(e => e.event === "done"))
    })
    await t.test("中間回答モデルが空文字なら主モデルで検索と回答を生成する", async () => {
      process.env.ROUTE_MODEL_ARN = ""
      const { handleRequest: defaultRouteHandler } = await import(pathToFileURL(`${directory}/handler.mjs`).href + "?empty-route")
      process.env.ROUTE_MODEL_ARN = "us.anthropic.claude-sonnet-4-6"
      BedrockRuntimeClient.prototype.send = async command => command.constructor.name === "ConverseCommand"
        ? { output: { message: { content: [{ text: '[{"route":"faq","query":"監査ログ"}]' }] } }, stopReason: "end_turn" }
        : { stream: (async function*() { yield { contentBlockDelta: { delta: { text: "730日" } } } })() }
      BedrockAgentRuntimeClient.prototype.send = async command => {
        const config = command.input.retrieveAndGenerateConfiguration.knowledgeBaseConfiguration
        assert.equal(config.modelArn, "us.anthropic.claude-sonnet-5")
        assert.ok(config.generationConfiguration.promptTemplate)
        return { output: { text: "730日" }, citations: [] }
      }
      const events = await invoke({ useOrchestration: true }, defaultRouteHandler)
      assert.ok(events.some(e => e.event === "done"))
    })
    await t.test("最終統合が出力上限で終わったら完了イベントを送らない", async () => {
      BedrockRuntimeClient.prototype.send = async command => command.constructor.name === "ConverseCommand"
        ? { output: { message: { content: [{ text: '[{"route":"faq","query":"監査ログ"}]' }] } }, stopReason: "end_turn" }
        : { stream: (async function*() {
          yield { contentBlockDelta: { delta: { text: "途中の回答" } } }
          yield { messageStop: { stopReason: "max_tokens" } }
        })() }
      BedrockAgentRuntimeClient.prototype.send = async () => ({ output: { text: "資料の回答" }, citations: [] })
      const events = await invoke({ useOrchestration: true })
      assert.match(events.find(e => e.event === "error").data.message, /最終回答.*max_tokens/)
      assert.ok(!events.some(e => e.event === "done"))
    })
  } finally { await rm(directory, { recursive: true, force: true }) }
})
