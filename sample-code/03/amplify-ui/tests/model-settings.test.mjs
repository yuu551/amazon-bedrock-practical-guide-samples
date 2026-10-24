import test from "node:test"
import assert from "node:assert/strict"
import { build } from "esbuild"
import { mkdtemp, rm } from "node:fs/promises"
import { pathToFileURL } from "node:url"

// モデルIDと推論プロファイルARNのどちらを渡しても、新モデル向けの設定を選ぶことを確かめる。
const directory = await mkdtemp(new URL("../.model-settings-", import.meta.url))
await build({ entryPoints: [new URL("../amplify/functions/rag-handler/model-settings.ts", import.meta.url).pathname], outfile: `${directory}/model-settings.mjs`, bundle: true, platform: "node", format: "esm", target: "node24" })
const { usesReasoningDefaults, converseConfig, knowledgeBasePrompts } = await import(pathToFileURL(`${directory}/model-settings.mjs`).href)

await test("モデル設定の判定", async t => {
  try {
    await t.test("Sonnet 5.5はIDとARNのどちらでも新モデルとして扱う", () => {
      for (const id of [
        "anthropic.claude-sonnet-5-5",
        "us.anthropic.claude-sonnet-5-5",
        "global.anthropic.claude-sonnet-5-5",
        "arn:aws:bedrock:us-east-1:123456789012:inference-profile/global.anthropic.claude-sonnet-5-5",
      ]) {
        assert.equal(usesReasoningDefaults(id), true, id)
        assert.deepEqual(converseConfig(id, 1024, 0.2), { maxTokens: 4096 })
        assert.ok(knowledgeBasePrompts(id).generationConfiguration)
      }
    })
    await t.test("Haiku 5.5は日本向けのIDでも新モデルとして扱い、Haiku 4.5は従来どおり", () => {
      for (const id of ["us.anthropic.claude-haiku-5-5", "jp.anthropic.claude-haiku-5-5", "global.anthropic.claude-haiku-5-5"]) {
        assert.equal(usesReasoningDefaults(id), true, id)
      }
      assert.equal(usesReasoningDefaults("us.anthropic.claude-haiku-4-5-20251001-v1:0"), false)
    })
    await t.test("GPT-6.1 SolはGPT-6 Solとは別のIDとして判定する", () => {
      assert.equal(usesReasoningDefaults("us.openai.gpt-6.1-sol"), true)
      assert.equal(usesReasoningDefaults("global.openai.gpt-6.1-sol"), true)
    })
    await t.test("Sonnet 4.6は温度と出力上限をそのまま使う", () => {
      assert.equal(usesReasoningDefaults("us.anthropic.claude-sonnet-4-6"), false)
      assert.deepEqual(converseConfig("us.anthropic.claude-sonnet-4-6", 1024, 0.2), { maxTokens: 1024, temperature: 0.2 })
      assert.deepEqual(knowledgeBasePrompts("us.anthropic.claude-sonnet-4-6"), {})
    })
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})
