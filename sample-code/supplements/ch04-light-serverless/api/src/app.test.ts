import { describe, expect, it, vi } from "vitest"
import { createApp, type AgentInvoker } from "./app.js"

async function* sse(...events: object[]) {
  for (const event of events) {
    yield new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)
  }
}

function setup(invokeAgent: AgentInvoker = async () => ({
  contentType: "text/event-stream",
  body: sse({ type: "text", data: "こんにちは" }, { type: "complete", data: {} }),
})) {
  const verifyToken = vi.fn(async (token: string) => {
    if (token !== "good") throw new Error("invalid")
    return { sub: "user-sub-1" }
  })
  const invoke = vi.fn(invokeAgent)
  const app = createApp({ verifyToken, invokeAgent: invoke, version: "test" })
  return { app, invoke, verifyToken }
}

const SESSION_ID = "session-0123456789abcdef0123456789abcdef"

function chat(app: ReturnType<typeof setup>["app"], body: unknown, token = "good") {
  return app.request("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  })
}

describe("/api/health", () => {
  it("バージョンを返す", async () => {
    const { app } = setup()
    const res = await app.request("/api/health")
    expect(await res.json()).toEqual({ ok: true, version: "test" })
  })
})

describe("/api/chat", () => {
  it("トークンがなければ401を返す", async () => {
    const { app, invoke } = setup()
    const res = await app.request("/api/chat", { method: "POST", body: "{}" })
    expect(res.status).toBe(401)
    expect(invoke).not.toHaveBeenCalled()
  })

  it("検証できないトークンは401を返す", async () => {
    const { app, invoke } = setup()
    const res = await chat(app, { prompt: "質問" }, "bad")
    expect(res.status).toBe(401)
    expect(invoke).not.toHaveBeenCalled()
  })

  it("空の質問は400を返す", async () => {
    const { app } = setup()
    const res = await chat(app, { prompt: "  " })
    expect(res.status).toBe(400)
  })

  it("actor_idは画面から受け取らずトークンのsubを使う", async () => {
    const { app, invoke } = setup()
    await chat(app, { prompt: "質問", session_id: SESSION_ID, actor_id: "someone-else" })
    expect(invoke).toHaveBeenCalledWith({
      prompt: "質問",
      sessionId: SESSION_ID,
      actorId: "user-sub-1",
    })
  })

  it("形式に合わないセッションIDは新しく作る", async () => {
    const { app, invoke } = setup()
    const res = await chat(app, { prompt: "質問", session_id: "short" })
    const sessionId = invoke.mock.calls[0][0].sessionId
    expect(sessionId).not.toBe("short")
    expect(sessionId.length).toBeGreaterThanOrEqual(33)
    expect(res.headers.get("x-session-id")).toBe(sessionId)
  })

  it("RuntimeのSSEをそのまま流す", async () => {
    const { app } = setup()
    const res = await chat(app, { prompt: "質問", session_id: SESSION_ID })
    expect(res.headers.get("content-type")).toBe("text/event-stream")
    const text = await res.text()
    expect(text).toContain('data: {"type":"text","data":"こんにちは"}')
    expect(text).toContain('"type":"complete"')
  })
})
