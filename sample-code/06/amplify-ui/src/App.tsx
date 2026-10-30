import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react"
import { Authenticator, translations } from "@aws-amplify/ui-react"
import { I18n } from "aws-amplify/utils"
import { AlertCircle, ArrowUp, MessageSquareText } from "lucide-react"

import {
  type StreamEvent,
  type SupportAgentResponse,
  type ToolEvent,
  invokeSupportAgent,
} from "./api"
import type { HistoryItem } from "./types"
import MessageBubble from "./components/MessageBubble"
import StreamingMessage from "./components/StreamingMessage"
import Sidebar from "./components/Sidebar"
import {
  ContextPanel,
  WorkspaceHeader,
} from "./components/WorkspaceChrome"
import { citationKey, type Citation } from "./workspace"

I18n.putVocabularies(translations)
I18n.setLanguage("ja")

const suggestions = [
  "エンタープライズプランの初回応答目標を確認したい",
  "SAML証明書の期限切れ時の対応を教えてほしい",
  "既知の障害か確認し、必要なら担当チームへ引き継いでほしい",
]

function toolEventKey(toolName: string | undefined) {
  const name = (toolName ?? "").toLowerCase()
  if (name.includes("support_kb") || name.includes("support-kb") || name.includes("knowledge")) return "kb"
  if (name.includes("confluence")) return "confluence"
  if (name.includes("jira") || name.includes("ticket")) return "jira"
  return name || "other"
}

function fallbackResult(answer: string, citations: Citation[], toolEvents: ToolEvent[]): SupportAgentResponse {
  const jiraUsed = toolEvents.some((event) => /jira|ticket/i.test(event.toolName ?? ""))
  const issueKey = answer.match(/\b([A-Z][A-Z0-9]+-\d+)\b/)?.[1] ?? null
  return {
    answer,
    citations,
    jira: {
      escalated: jiraUsed || issueKey != null,
      issueKey,
      reason: jiraUsed || issueKey
        ? "根拠の確認または有人判断が必要なため、担当チームへ引き継ぎました。"
        : null,
    },
  }
}

export default function App() {
  if (import.meta.env.MODE === "e2e") {
    const actorId = new URLSearchParams(window.location.search).get("actor") ?? "e2e-user"
    return <Portal actorId={actorId} />
  }

  return (
    <Authenticator>
      {({ signOut, user }) => (
        <Portal actorId={user?.userId ?? "authenticated-user"} signOut={signOut} />
      )}
    </Authenticator>
  )
}

function Portal({ actorId, signOut }: { actorId: string; signOut?: () => void }) {
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [result, setResult] = useState<SupportAgentResponse | null>(null)
  const [toolEvents, setToolEvents] = useState<ToolEvent[]>([])
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID())
  const [question, setQuestion] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null)
  const [streamingText, setStreamingText] = useState("")
  const [streamingCitations, setStreamingCitations] = useState<Citation[]>([])
  const streamingTextRef = useRef("")
  const citationRef = useRef<Citation[]>([])
  const resultRef = useRef<SupportAgentResponse | null>(null)
  const toolEventsRef = useRef<ToolEvent[]>([])
  const anchorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (history.length || streamingMessageId) {
      anchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
    }
  }, [history.length, streamingMessageId, streamingText])

  function resetConversation() {
    if (loading) return
    setHistory([])
    setResult(null)
    setToolEvents([])
    setSessionId(crypto.randomUUID())
    setQuestion("")
    setError(null)
    setFailedQuestion(null)
    setStreamingMessageId(null)
    setStreamingText("")
    setStreamingCitations([])
    streamingTextRef.current = ""
    citationRef.current = []
    resultRef.current = null
    toolEventsRef.current = []
  }

  function replaceToolEvents(updater: (current: ToolEvent[]) => ToolEvent[]) {
    toolEventsRef.current = updater(toolEventsRef.current)
    setToolEvents(toolEventsRef.current)
  }

  function handleStreamEvent(event: StreamEvent) {
    if (event.type === "text" && typeof event.data === "string") {
      streamingTextRef.current += event.data
      setStreamingText(streamingTextRef.current)
      return
    }

    if (event.type === "citation" && event.data && typeof event.data === "object" && "uri" in event.data) {
      const citation = event.data as Citation
      const key = citationKey(citation)
      if (!citationRef.current.some((item) => citationKey(item) === key)) {
        citationRef.current = [...citationRef.current, citation]
        setStreamingCitations(citationRef.current)
      }
      return
    }

    if (event.type === "tool_use") {
      const toolEvent: ToolEvent = {
        id: event.tool_use_id ?? crypto.randomUUID(),
        toolName: event.tool_name,
        status: "running",
        startedAt: Date.now(),
      }
      replaceToolEvents((current) => {
        const completed = current.map((item) =>
          item.status === "running" ? { ...item, status: "done" as const } : item,
        )
        return completed.some((item) => toolEventKey(item.toolName) === toolEventKey(toolEvent.toolName))
          ? completed
          : [...completed, toolEvent]
      })
      return
    }

    if (event.type === "tool_result") {
      replaceToolEvents((current) =>
        current.map((item) => item.status === "running" ? { ...item, status: "done" as const } : item),
      )
      return
    }

    if (event.type === "complete" && event.data && typeof event.data === "object" && "jira" in event.data) {
      const completed = event.data as SupportAgentResponse
      const completedCitations = completed.citations?.filter((citation, index, items) =>
        items.findIndex((item) => citationKey(item) === citationKey(citation)) === index,
      ) ?? []
      resultRef.current = {
        ...completed,
        citations: completedCitations.length ? completedCitations : citationRef.current,
      }
      setStreamingCitations(resultRef.current.citations)
    }
  }

  async function submitQuestion(rawQuestion: string) {
    const trimmed = rawQuestion.trim()
    if (!trimmed) {
      setError("問い合わせ内容を入力してください。")
      return
    }
    if (loading) return

    const assistantId = crypto.randomUUID()
    streamingTextRef.current = ""
    citationRef.current = []
    resultRef.current = null
    toolEventsRef.current = []
    setStreamingText("")
    setStreamingCitations([])
    setStreamingMessageId(assistantId)
    setToolEvents([])
    setResult(null)
    setLoading(true)
    setError(null)
    setFailedQuestion(null)
    setQuestion("")
    setHistory((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", text: trimmed },
    ])

    await invokeSupportAgent(
      trimmed,
      sessionId,
      actorId,
      handleStreamEvent,
      () => {
        const completedTools = toolEventsRef.current.map((item) =>
          item.status === "running" ? { ...item, status: "done" as const } : item,
        )
        const finalText = streamingTextRef.current
        const finalResult = resultRef.current ??
          fallbackResult(finalText, citationRef.current, completedTools)
        setHistory((current) => [
          ...current,
          {
            id: assistantId,
            role: "assistant",
            text: finalText,
            citations: finalResult.citations,
          },
        ])
        toolEventsRef.current = completedTools
        setToolEvents(completedTools)
        setResult(finalResult)
        setStreamingMessageId(null)
        setStreamingText("")
        setStreamingCitations([])
        setLoading(false)
      },
      (caught) => {
        setStreamingMessageId(null)
        setStreamingText("")
        setStreamingCitations([])
        setError(caught.message)
        setFailedQuestion(trimmed)
        setLoading(false)
      },
    )
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submitQuestion(question)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault()
      event.currentTarget.closest("form")?.requestSubmit()
    }
  }

  return (
    <div className="workspace-app">
      <WorkspaceHeader
        chapterLabel="第6章・本番運用"
        onReset={resetConversation}
        resetLabel="会話を消去"
        signOut={signOut}
      />
      <main className="workspace-grid workspace-grid--with-context">
        <section className="thread-panel" aria-label="問い合わせスレッド">
          <div className="thread-timeline" aria-live="polite">
            <div className="timeline-inner">
              {history.length === 0 && !streamingMessageId ? (
                <div className="empty-thread">
                  <MessageSquareText className="empty-icon" aria-hidden="true" />
                  <h3>問い合わせ対応を開始</h3>
                  <div className="empty-suggestions">
                    {suggestions.map((suggestion) => (
                      <button key={suggestion} type="button" onClick={() => setQuestion(suggestion)}>
                        {suggestion}<span aria-hidden="true">→</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              {history.map((item) => <MessageBubble key={item.id} item={item} />)}
              {streamingMessageId ? (
                <StreamingMessage
                  messageId={streamingMessageId}
                  content={streamingText}
                  citations={streamingCitations}
                  isStreaming={loading}
                />
              ) : null}
              <div ref={anchorRef} aria-hidden="true" />
            </div>
          </div>

          <div className="composer-wrap">
            {error ? (
              <div className="error-banner" role="status">
                <AlertCircle size={16} aria-hidden="true" /><p>{error}</p>
                {failedQuestion ? <button className="retry-button" type="button" onClick={() => void submitQuestion(failedQuestion)}>再試行</button> : null}
              </div>
            ) : null}
            <form onSubmit={onSubmit} className="composer" aria-busy={loading}>
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleKeyDown} rows={3} placeholder="問い合わせ内容を入力する" />
              <button type="submit" disabled={loading} aria-label="問い合わせを送信"><ArrowUp size={17} aria-hidden="true" /></button>
            </form>
          </div>
        </section>

        <ContextPanel>
          <Sidebar result={result} toolEvents={toolEvents} />
        </ContextPanel>
      </main>
    </div>
  )
}
