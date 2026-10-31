import { Hono } from "hono"
import { stream } from "hono/streaming"
import { randomUUID } from "node:crypto"

// 画面とRuntimeの間に置くAPI。ローカルでもLambda Web Functionsでも同じコードで動く

export type VerifiedUser = { sub: string }

export type AgentInvoker = (input: {
  prompt: string
  sessionId: string
  actorId: string
}) => Promise<{ contentType: string; body: AsyncIterable<Uint8Array> }>

export type AppDeps = {
  verifyToken: (token: string) => Promise<VerifiedUser>
  invokeAgent: AgentInvoker
  version: string
}

// AgentCore Runtimeのセッション IDは33文字以上が必要
const SESSION_ID_PATTERN = /^[A-Za-z0-9-_]{33,100}$/
const MAX_PROMPT_LENGTH = 4000

export function createApp(deps: AppDeps) {
  const app = new Hono()

  app.get("/api/health", (c) => c.json({ ok: true, version: deps.version }))

  app.post("/api/chat", async (c) => {
    // OACの署名は別のヘッダーに入るので、利用者のAuthorizationはそのまま届く
    const header = c.req.header("authorization") ?? ""
    const token = header.replace(/^Bearer\s+/i, "")
    if (!token) return c.json({ error: "ログインしてください。" }, 401)

    let user: VerifiedUser
    try {
      user = await deps.verifyToken(token)
    } catch {
      return c.json({ error: "トークンを確認できませんでした。" }, 401)
    }

    const body = await c.req.json<{ prompt?: unknown; session_id?: unknown }>().catch(() => null)
    const prompt = typeof body?.prompt === "string" ? body.prompt.trim() : ""
    if (!prompt) return c.json({ error: "質問を入力してください。" }, 400)
    if (prompt.length > MAX_PROMPT_LENGTH) {
      return c.json({ error: `質問は${MAX_PROMPT_LENGTH}文字以内にしてください。` }, 400)
    }
    const requested = typeof body?.session_id === "string" ? body.session_id : ""
    const sessionId = SESSION_ID_PATTERN.test(requested) ? requested : randomUUID()

    // 記憶の持ち主は画面から受け取らず、検証したトークンのsubで決める
    const result = await deps.invokeAgent({ prompt, sessionId, actorId: user.sub })

    c.header("Content-Type", result.contentType)
    c.header("Cache-Control", "no-store")
    c.header("X-Session-Id", sessionId)
    return stream(c, async (s) => {
      for await (const chunk of result.body) {
        await s.write(chunk)
      }
    })
  })

  return app
}
