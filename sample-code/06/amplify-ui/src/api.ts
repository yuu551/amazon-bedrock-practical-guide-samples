import { fetchAuthSession } from "aws-amplify/auth"
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

import outputs from "../amplify_outputs.json"

const customOutputs = (outputs as Record<string, any>).custom ?? {}
const REGION = (outputs as Record<string, any>).auth?.aws_region ?? "us-east-1"
const AGENT_RUNTIME_ARN = customOutputs.agentCoreRuntimeArn
if (!AGENT_RUNTIME_ARN) {
  console.warn("AgentCore Runtime ARN が未設定です。pnpm exec ampx sandbox を実行してください。")
}
const QUALIFIER = "DEFAULT"
const E2E_MODE = import.meta.env.MODE === "e2e"

async function getAccessToken(): Promise<{ token: string; actorId?: string }> {
  if (E2E_MODE) return { token: "e2e-token" }

  let session = await fetchAuthSession()
  const exp = session.tokens?.accessToken?.payload?.exp

  // 残り TTL が 2 分未満なら強制リフレッシュ
  if (exp && exp * 1000 - Date.now() < 120_000) {
    session = await fetchAuthSession({ forceRefresh: true })
  }

  const token = session.tokens?.accessToken?.toString()
  if (!token) throw new Error("認証トークンの取得に失敗しました")
  const actorId = session.tokens?.accessToken?.payload?.sub
  return { token, actorId: typeof actorId === "string" ? actorId : undefined }
}

// ── SSE ストリーミング呼び出し ──────────────────────────

export async function invokeSupportAgent(
  question: string,
  sessionId: string,
  actorId: string,
  onEvent: (event: StreamEvent) => void,
  onComplete: () => void,
  onError: (error: Error) => void,
): Promise<void> {
  try {
    const auth = await getAccessToken()

    // ARN内のコロンやスラッシュはパスパラメータとしてエンコードが必要
    const url = E2E_MODE
      ? "/__e2e/agent"
      : `https://bedrock-agentcore.${REGION}.amazonaws.com/runtimes/${encodeURIComponent(AGENT_RUNTIME_ARN)}/invocations?qualifier=${QUALIFIER}`

    const payload = {
      prompt: question,
      session_id: sessionId,
      actor_id: auth.actorId ?? actorId,
    }

    const runtimeSessionHeader = {
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
    }

    let res = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${auth.token}`,
        "Content-Type": "application/json",
        ...runtimeSessionHeader,
      },
      body: JSON.stringify(payload),
    })

    // 401 → トークン再取得して 1 回だけリトライ
    if (res.status === 401) {
      const freshToken = (await fetchAuthSession({ forceRefresh: true }))
        .tokens?.accessToken?.toString()
      if (!freshToken) throw new Error("認証トークンの再取得に失敗しました")

      res = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${freshToken}`,
          "Content-Type": "application/json",
          ...runtimeSessionHeader,
        },
        body: JSON.stringify(payload),
      })
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
