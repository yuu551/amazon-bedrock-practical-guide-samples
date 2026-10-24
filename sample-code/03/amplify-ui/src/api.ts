import { AwsClient } from "aws4fetch"
import { fetchAuthSession } from "aws-amplify/auth"
import outputs from "../amplify_outputs.json"
import type { Citation } from "./workspace"

const FUNCTION_URL = (outputs as Record<string, any>).custom?.ragFunctionUrl
const E2E_MODE = import.meta.env.MODE === "e2e"
const REQUEST_URL = E2E_MODE ? "/__e2e/rag" : FUNCTION_URL
if (!REQUEST_URL) {
  console.warn("ragFunctionUrl が未設定です。pnpm exec ampx sandbox を実行してください。")
}

export type { Citation } from "./workspace"

export type SearchConfig = {
  searchType?: "SEMANTIC" | "HYBRID"
  useRerank?: boolean
  useQueryDecomposition?: boolean
  useOrchestration?: boolean
}

export type MetadataFilter = {
  equals: { key: string; value: string }
}

export type RoutePlan = { route: string; query: string }

export type StreamHandlers = {
  onChunk: (text: string) => void
  onCitation: (citation: Citation) => void
  onStatus?: (status: { phase: string; message?: string; plans?: RoutePlan[] }) => void
  onDone: (summary: { citationCount?: number; latencyMs?: number; traceId?: string }) => void
  onError: (message: string) => void
}

async function signedFetch(url: string, body: string): Promise<Response> {
  if (E2E_MODE) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    })
  }

  const session = await fetchAuthSession()
  const credentials = session.credentials
  if (!credentials) throw new Error("認証情報の取得に失敗しました")

  const client = new AwsClient({
    accessKeyId: credentials.accessKeyId,
    secretAccessKey: credentials.secretAccessKey,
    sessionToken: credentials.sessionToken,
    service: "lambda",
  })

  return client.fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  })
}

export async function askQuestionStream(
  question: string,
  sessionId: string,
  handlers: StreamHandlers,
  options?: { filter?: MetadataFilter; config?: SearchConfig },
): Promise<void> {
  let response: Response
  try {
    response = await signedFetch(
      REQUEST_URL,
      JSON.stringify({
        question,
        session_id: sessionId,
        ...(options?.filter && { filter: options.filter }),
        ...(options?.config && { config: options.config }),
      }),
    )
  } catch (error) {
    handlers.onError(error instanceof Error ? error.message : "通信エラーが発生しました。")
    return
  }

  if (!response.ok || !response.body) {
    handlers.onError(`問い合わせに失敗しました（${response.status}）`)
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let currentEvent = ""
  let doneEmitted = false

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim()
          continue
        }
        if (!line.startsWith("data: ")) continue
        const payload = line.slice(6)
        try {
          const data = JSON.parse(payload)
          if (currentEvent === "chunk" && typeof data.text === "string") {
            handlers.onChunk(data.text)
          } else if (currentEvent === "citation") {
            handlers.onCitation(data as Citation)
          } else if (currentEvent === "status") {
            handlers.onStatus?.(data)
          } else if (currentEvent === "error") {
            doneEmitted = true
            handlers.onError(data.message ?? "エラーが発生しました。")
          } else if (currentEvent === "done") {
            doneEmitted = true
            handlers.onDone(data)
          }
        } catch {
          // JSONパース失敗は行分割ずれの可能性があるため無視
        }
      }
    }
    if (!doneEmitted) handlers.onDone({})
  } catch (error) {
    handlers.onError(error instanceof Error ? error.message : "ストリーム読み取りに失敗しました。")
  }
}
