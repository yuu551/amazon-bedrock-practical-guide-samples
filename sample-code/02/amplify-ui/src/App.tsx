import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react"
import { Authenticator, translations } from "@aws-amplify/ui-react"
import { I18n } from "aws-amplify/utils"
import { AlertCircle, ArrowUp, MessageSquareText } from "lucide-react"
import { Streamdown } from "streamdown"

import { askQuestionStream } from "./api"
import { streamdownPlugins } from "./streamdownPlugins"
import {
  citationKey,
  withCitationLinks,
  type Citation,
} from "./workspace"
import {
  CitationList,
  WorkspaceHeader,
} from "./components/WorkspaceChrome"

I18n.putVocabularies(translations)
I18n.setLanguage("ja")

type HistoryEntry = {
  id: string
  question: string
  answer: string
  citations: Citation[]
  streaming: boolean
}

const suggestedQuestions = [
  "SAML認証でログインできない場合の確認項目",
  "エンタープライズプランの初回応答目標",
  "監査ログの保持期間と出力方法",
]

export default function App() {
  if (import.meta.env.MODE === "e2e") return <Portal />

  return (
    <Authenticator>
      {({ signOut }) => <Portal signOut={signOut} />}
    </Authenticator>
  )
}

function Portal({ signOut }: { signOut?: () => void }) {
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID())
  const [question, setQuestion] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!history.length) return
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [history.length])

  function resetConversation() {
    if (loading) return
    setHistory([])
    setSessionId(crypto.randomUUID())
    setQuestion("")
    setError(null)
    setFailedQuestion(null)
  }

  async function submitQuestion(rawQuestion: string) {
    const trimmed = rawQuestion.trim()
    if (!trimmed) {
      setError("質問を入力してください。")
      return
    }
    if (loading) return

    const entryId = crypto.randomUUID()
    setLoading(true)
    setError(null)
    setFailedQuestion(null)
    setQuestion("")
    setHistory((current) => [
      ...current,
      { id: entryId, question: trimmed, answer: "", citations: [], streaming: true },
    ])

    const updateEntry = (updater: (entry: HistoryEntry) => HistoryEntry) => {
      setHistory((current) =>
        current.map((entry) => entry.id === entryId ? updater(entry) : entry),
      )
    }

    await askQuestionStream(trimmed, sessionId, {
      onChunk: (text) => updateEntry((entry) => ({ ...entry, answer: entry.answer + text })),
      onCitation: (citation) =>
        updateEntry((entry) => {
          const key = citationKey(citation)
          if (entry.citations.some((item) => citationKey(item) === key)) {
            return entry
          }
          return { ...entry, citations: [...entry.citations, citation] }
        }),
      onDone: () => {
        updateEntry((entry) => ({ ...entry, streaming: false }))
        setLoading(false)
      },
      onError: (message) => {
        updateEntry((entry) => ({ ...entry, streaming: false }))
        setError(message)
        setFailedQuestion(trimmed)
        setLoading(false)
      },
    })
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
        chapterLabel="第2章・資料検索"
        onReset={resetConversation}
        resetLabel="会話を消去"
        signOut={signOut}
      />
      <main className="workspace-grid">
        <section className="thread-panel" aria-label="問い合わせスレッド">
          <div className="thread-timeline" aria-live="polite">
            <div className="timeline-inner">
              {history.length === 0 ? (
                <div className="empty-thread">
                  <MessageSquareText className="empty-icon" aria-hidden="true" />
                  <h3>問い合わせ内容を入力してください</h3>
                  <div className="empty-suggestions">
                    {suggestedQuestions.map((suggestion) => (
                      <button key={suggestion} type="button" onClick={() => setQuestion(suggestion)}>
                        {suggestion}<span aria-hidden="true">→</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {history.map((entry) => {
                const citationId = `citation-${entry.id}`
                return (
                  <div className="qa-pair" key={entry.id}>
                    <article className="message message-user">
                      <span className="message-role">問い合わせ</span>
                      <p>{entry.question}</p>
                    </article>
                    <article className="message message-assistant">
                      <span className="message-role">
                        回答
                        {entry.streaming ? <span className="streaming-dot" aria-hidden="true" /> : null}
                      </span>
                      <Streamdown
                        className="markdown-content"
                        parseIncompleteMarkdown
                        plugins={streamdownPlugins}
                        linkSafety={{ enabled: false }}
                      >
                        {withCitationLinks(entry.answer, entry.citations, citationId)}
                      </Streamdown>
                      {entry.answer || entry.streaming ? (
                        <CitationList
                          citations={entry.citations}
                          loading={entry.streaming}
                          idPrefix={citationId}
                        />
                      ) : null}
                    </article>
                  </div>
                )
              })}
              <div ref={endRef} aria-hidden="true" />
            </div>
          </div>

          <div className="composer-wrap">
            {error ? (
              <div className="error-banner" role="status">
                <AlertCircle size={16} aria-hidden="true" />
                <p>{error}</p>
                {failedQuestion ? (
                  <button className="retry-button" type="button" onClick={() => void submitQuestion(failedQuestion)}>
                    再試行
                  </button>
                ) : null}
              </div>
            ) : null}
            <form onSubmit={onSubmit} className="composer" aria-busy={loading}>
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={handleKeyDown}
                rows={3}
                placeholder="製品資料や障害対応手順について質問する"
              />
              <button type="submit" disabled={loading} aria-label="質問を送信">
                <ArrowUp size={17} aria-hidden="true" />
              </button>
            </form>
          </div>
        </section>
      </main>
    </div>
  )
}
