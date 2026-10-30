import { FormEvent, KeyboardEvent, useEffect, useRef } from "react"
import { Send, MessageSquareDashed } from "lucide-react"
import { HistoryItem } from "../types"
import type { ToolEvent } from "../api"
import MessageBubble from "./MessageBubble"
import StreamingMessage from "./StreamingMessage"
import LoadingSpinner from "./LoadingSpinner"
import ToolActivity from "./ToolActivity"

type Props = {
  sessionId: string
  question: string
  onQuestionChange: (value: string) => void
  history: HistoryItem[]
  loading: boolean
  error: string | null
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  streamingMessageId: string | null
  streamingText: string
  toolEvents: ToolEvent[]
}

export default function ChatPanel({
  question,
  onQuestionChange,
  history,
  loading,
  error,
  onSubmit,
  streamingMessageId,
  streamingText,
  toolEvents,
}: Props) {
  const anchorRef = useRef<HTMLDivElement>(null)

  // Auto-scroll on message/stream changes only — tool activity updates inline
  // and doesn't need to yank the viewport.
  useEffect(() => {
    if (history.length === 0 && !streamingMessageId) return
    anchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [history.length, streamingMessageId])

  // While streaming, keep the tail visible with a gentler throttle.
  useEffect(() => {
    if (!streamingMessageId) return
    const id = window.requestAnimationFrame(() => {
      anchorRef.current?.scrollIntoView({ behavior: "auto", block: "end" })
    })
    return () => window.cancelAnimationFrame(id)
  }, [streamingText, streamingMessageId])

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault()
      const form = event.currentTarget.closest("form")
      if (form) form.requestSubmit()
    }
  }

  const showActivity = toolEvents.length > 0

  return (
    <section className="panel panel-chat" aria-label="問い合わせ入力">
      <div className="panel-header">
        <h2>問い合わせ入力</h2>
        <span className="session-tag">会話を継続中</span>
      </div>

      <form onSubmit={onSubmit} className="composer" aria-busy={loading}>
        <textarea
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          onKeyDown={handleKeyDown}
          rows={6}
          placeholder="問い合わせ内容を入力する"
        />
        <button type="submit" disabled={loading} aria-label="問い合わせを送信">
          {loading ? (
            <>
              <LoadingSpinner />
              確認中...
            </>
          ) : (
            <>
              <Send size={16} aria-hidden="true" />
              問い合わせる
            </>
          )}
        </button>
      </form>

      {error ? (
        <p className="error-text" role="status" aria-live="polite">
          {error}
        </p>
      ) : null}

      {showActivity ? (
        <div className="activity-wrap" aria-label="回答処理の状況">
          <ToolActivity events={toolEvents} />
        </div>
      ) : null}

      <div className="timeline" aria-live="polite">
        {history.length === 0 && !streamingMessageId ? (
          <div className="empty-state">
            <MessageSquareDashed size={24} aria-hidden="true" />
            <p>ここに問い合わせ履歴が表示されます。</p>
            <p>資料だけで回答した場合と、担当チームへ引き継いだ場合の両方を確認できます。</p>
          </div>
        ) : null}

        {history.map((item) => (
          <MessageBubble key={item.id} item={item} />
        ))}

        {streamingMessageId ? (
          <StreamingMessage content={streamingText} isStreaming={loading} />
        ) : null}

        <div ref={anchorRef} aria-hidden="true" />
      </div>
    </section>
  )
}
