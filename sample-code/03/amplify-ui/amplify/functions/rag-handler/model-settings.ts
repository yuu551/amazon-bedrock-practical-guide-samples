// 補足で確認したモデル。モデルIDと推論プロファイルARNのどちらでも判定する。
const reasoningModels = [
  "anthropic.claude-sonnet-5",
  "anthropic.claude-sonnet-5-5",
  "anthropic.claude-opus-5",
  "anthropic.claude-opus-5-5",
  "anthropic.claude-haiku-5-5",
  "openai.gpt-6-sol",
  "openai.gpt-6.1-sol",
  "openai.gpt-6-luna",
]

export function usesReasoningDefaults(modelId: string): boolean {
  return reasoningModels.some((model) => modelId.endsWith(model))
}

export function converseConfig(modelId: string, maxTokens: number, temperature: number) {
  return usesReasoningDefaults(modelId) ? { maxTokens: 4096 } : { maxTokens, temperature }
}

// 新しいモデルではKB既定のプロンプトが用意されていない場合がある。
// 引用を返すため、AWSが展開する出力形式の指示も保持する。
export function knowledgeBasePrompts(modelId: string) {
  if (!usesReasoningDefaults(modelId)) return {}
  return {
    generationConfiguration: {
      promptTemplate: {
        textPromptTemplate:
          "検索結果に基づいて日本語で回答してください。資料にないことは推測せず、不明と伝えてください。" +
          "資料の適用時期が異なる場合は区別してください。\n" +
          "質問: $query$\n検索結果:\n$search_results$\n$output_format_instructions$",
      },
      inferenceConfig: { textInferenceConfig: { maxTokens: 4096 } },
    },
    orchestrationConfiguration: {
      promptTemplate: {
        textPromptTemplate:
          "会話履歴と質問から、関連資料を探す検索クエリを作成してください。\n" +
          "会話履歴:\n$conversation_history$\n質問: $query$\n$output_format_instructions$",
      },
      inferenceConfig: { textInferenceConfig: { maxTokens: 4096 } },
    },
  }
}
