import { fetchAuthSession } from "aws-amplify/auth"
import { loadConfig } from "./config"
import type { Citation } from "./workspace"

// ── 型定義 ─────────────────────────────────────────────

export type SupportAgentResponse = {
  answer: string
  citations: Citation[]
  jira: {
    escalated: boolean
    issueKey: string | null
    reason: string | null
  }
}

export type StreamEvent = {
  type: "text" | "citation" | "tool_use" | "tool_result" | "error" | "complete"
  data?: string | Citation | SupportAgentResponse | Record<string, unknown>
  tool_name?: string
  tool_use_id?: string
}

export type ToolEvent = {
  id: string
  toolName: string | undefined
  status: "running" | "done"
  startedAt: number
}

// ── 設定 ───────────────────────────────────────────────

// 既定はAgentCore Runtimeを直接呼ぶ。RuntimeがCognitoのトークンを検証する。
// config.json に agentRuntimeArn がないときは、同じCloudFrontの /api/chat（Hono）へ送る
const CHAT_URL = "/api/chat"
const QUALIFIER = "DEFAULT"

async function chatEndpoint(sessionId: string): Promise<{ url: string; headers: Record<string, string> }> {
  const config = await loadConfig()
  if (!config.agentRuntimeArn) return { url: CHAT_URL, headers: {} }
  // ARN内のコロンやスラッシュはパスに入れるためエンコードする
  return {
    url: `https://bedrock-agentcore.${config.region}.amazonaws.com/runtimes/${encodeURIComponent(config.agentRuntimeArn)}/invocations?qualifier=${QUALIFIER}`,
    headers: { "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId },
  }
}

const E2E_MODE = import.meta.env.MODE === "e2e"

async function getAccessToken(forceRefresh = false): Promise<string> {
  if (E2E_MODE) return "e2e-token"

  let session = await fetchAuthSession({ forceRefresh })
  const exp = session.tokens?.accessToken?.payload?.exp

  // 残り TTL が 2 分未満なら強制リフレッシュ
  if (!forceRefresh && exp && exp * 1000 - Date.now() < 120_000) {
    session = await fetchAuthSession({ forceRefresh: true })
  }

  const token = session.tokens?.accessToken?.toString()
  if (!token) throw new Error("認証トークンの取得に失敗しました")
  return token
}

// ── SSE ストリーミング呼び出し ──────────────────────────

export async function invokeSupportAgent(
  question: string,
  sessionId: string,
  onEvent: (event: StreamEvent) => void,
  onComplete: () => void,
  onError: (error: Error) => void,
): Promise<void> {
  try {
    const endpoint = E2E_MODE ? { url: "/__e2e/agent", headers: {} } : await chatEndpoint(sessionId)
    // actor_id は送らない。エージェントかHonoが、検証済みのトークンの sub を使う
    const body = JSON.stringify({ prompt: question, session_id: sessionId })
    const send = (token: string) =>
      fetch(endpoint.url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          ...endpoint.headers,
        },
        body,
      })

    let res = await send(await getAccessToken())

    // 401 → トークン再取得して 1 回だけリトライ
    if (res.status === 401) {
      res = await send(await getAccessToken(true))
    }

    if (!res.ok) {
      throw new Error(`問い合わせに失敗しました（${res.status}）`)
    }

    // SSE ストリームを読み取る
    if (!res.body) throw new Error("レスポンスボディが空です")
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    let streamError: string | null = null

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue
        const data = line.slice(6)
        if (data === "[DONE]") continue

        try {
          const event = JSON.parse(data) as StreamEvent
          if (event.type === "error") {
            streamError =
              (typeof event.data === "string" ? event.data : null) ??
              "エラーが発生しました"
          }
          onEvent(event)
        } catch {
          // JSON パースエラーは無視
        }
      }
    }

    if (streamError) {
      onError(new Error(streamError))
    } else {
      onComplete()
    }
  } catch (error) {
    onError(error instanceof Error ? error : new Error("不明なエラーです。"))
  }
}
