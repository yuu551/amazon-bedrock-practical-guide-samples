import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react"
import { Authenticator, translations } from "@aws-amplify/ui-react"
import { generateClient } from "aws-amplify/data"
import { I18n } from "aws-amplify/utils"
import {
  AlertCircle,
  ArrowUp,
  CheckCircle2,
  GitCompareArrows,
  MessageSquareText,
  SlidersHorizontal,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react"
import { Streamdown } from "streamdown"

import { askQuestionStream, type MetadataFilter, type SearchConfig } from "./api"
import ComparePanel, { type CompareColumnState } from "./ComparePanel"
import Select from "./Select"
import { streamdownPlugins } from "./streamdownPlugins"
import type { Schema } from "../amplify/data/resource"
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

const dataClient = generateClient<Schema>()
const E2E_MODE = import.meta.env.MODE === "e2e"

type FeedbackRating = "UP" | "DOWN"
type FeedbackDraft = {
  rating?: FeedbackRating
  comment: string
  status: "idle" | "submitting" | "submitted" | "error"
  errorMessage?: string
}
type HistoryEntry = {
  id: string
  question: string
  answer: string
  citations: Citation[]
  latencyMs?: number
  traceId?: string
  streaming: boolean
  statusMessage?: string
}

const emptyFeedback: FeedbackDraft = { comment: "", status: "idle" }
const DEPARTMENT_OPTIONS = [
  { label: "全部門", value: "", description: "絞り込みなし" },
  { label: "サポート", value: "Support", description: "障害対応・よくある質問" },
  { label: "プロダクト", value: "Product", description: "製品仕様・設定手順" },
  { label: "請求", value: "Billing", description: "応答目標・課金" },
  { label: "セキュリティ", value: "Security", description: "監査記録・認証" },
  { label: "営業", value: "Sales", description: "プラン・営業資料" },
] as const
const SEARCH_PRESETS: { label: string; description: string; config: SearchConfig }[] = [
  { label: "意味検索（セマンティック検索）", description: "意味の近い資料を検索", config: { searchType: "SEMANTIC" } },
  { label: "複合検索（ハイブリッド検索）", description: "キーワード検索も併用", config: { searchType: "HYBRID" } },
  { label: "順位を調整（ハイブリッド検索＋リランキング）", description: "意味とキーワードで検索し、関連度で並べ替え", config: { searchType: "HYBRID", useRerank: true } },
  { label: "質問を分割（クエリ分解）", description: "複合質問を分割して検索", config: { searchType: "SEMANTIC", useQueryDecomposition: true } },
  { label: "資料を横断（オーケストレーション）", description: "検索先を選び、複数の回答を統合", config: { useOrchestration: true } },
]
const BASELINE_CONFIG: SearchConfig = { searchType: "SEMANTIC" }
const emptyCompareState: CompareColumnState = {
  answer: "",
  citations: [],
  latencyMs: undefined,
  streaming: false,
  started: false,
}

export default function App() {
  if (E2E_MODE) return <Portal />

  return (
    <Authenticator>
      {({ signOut }) => <Portal signOut={signOut} />}
    </Authenticator>
  )
}

function Portal({ signOut }: { signOut?: () => void }) {
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [feedbackByEntryId, setFeedbackByEntryId] = useState<Record<string, FeedbackDraft>>({})
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID())
  const [question, setQuestion] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null)
  const [department, setDepartment] = useState("")
  const [presetIndex, setPresetIndex] = useState(0)
  const [compareMode, setCompareMode] = useState(false)
  const [baseline, setBaseline] = useState<CompareColumnState>(emptyCompareState)
  const [improved, setImproved] = useState<CompareColumnState>(emptyCompareState)
  const [compareQuestion, setCompareQuestion] = useState("")
  const endRef = useRef<HTMLDivElement>(null)

  const currentConfig = SEARCH_PRESETS[presetIndex].config
  const baselineLabel = `${SEARCH_PRESETS[0].label} / フィルタなし`
  const improvedLabel = `${SEARCH_PRESETS[presetIndex].label} / ${department || "フィルタなし"}`
  const filter: MetadataFilter | undefined = department
    ? { equals: { key: "department", value: department } }
    : undefined
  useEffect(() => {
    if (history.length || baseline.started) {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
    }
  }, [history.length, baseline.started])

  function resetCompare() {
    setBaseline(emptyCompareState)
    setImproved(emptyCompareState)
    setCompareQuestion("")
  }

  function resetConversation() {
    if (loading) return
    setHistory([])
    setFeedbackByEntryId({})
    setSessionId(crypto.randomUUID())
    setQuestion("")
    setError(null)
    setFailedQuestion(null)
    resetCompare()
  }

  async function streamIntoHistory(trimmed: string) {
    const entryId = crypto.randomUUID()
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
      onCitation: (citation) => updateEntry((entry) => {
        const key = citationKey(citation)
        if (entry.citations.some((item) => citationKey(item) === key)) return entry
        return { ...entry, citations: [...entry.citations, citation] }
      }),
      onStatus: (status) => updateEntry((entry) => ({ ...entry, statusMessage: status.message ?? status.phase })),
      onDone: (summary) => updateEntry((entry) => ({ ...entry, streaming: false, latencyMs: summary.latencyMs, traceId: summary.traceId })),
      onError: (message) => {
        setError(message)
        setFailedQuestion(trimmed)
        updateEntry((entry) => ({ ...entry, streaming: false }))
      },
    }, { filter, config: currentConfig })
  }

  async function streamIntoCompare(trimmed: string) {
    setCompareQuestion(trimmed)
    const startState = { ...emptyCompareState, streaming: true, started: true }
    setBaseline(startState)
    setImproved(startState)

    const handlersFor = (setter: React.Dispatch<React.SetStateAction<CompareColumnState>>) => ({
      onChunk: (text: string) => setter((state) => ({ ...state, answer: state.answer + text })),
      onCitation: (citation: Citation) => setter((state) => {
        const key = citationKey(citation)
        if (state.citations.some((item) => citationKey(item) === key)) return state
        return { ...state, citations: [...state.citations, citation] }
      }),
      onDone: (summary: { latencyMs?: number; traceId?: string }) => setter((state) => ({ ...state, streaming: false, latencyMs: summary.latencyMs, traceId: summary.traceId })),
      onError: (message: string) => {
        setError(message)
        setFailedQuestion(trimmed)
        setter((state) => ({ ...state, streaming: false }))
      },
    })

    await Promise.allSettled([
      askQuestionStream(trimmed, sessionId, handlersFor(setBaseline), { config: BASELINE_CONFIG }),
      askQuestionStream(trimmed, sessionId, handlersFor(setImproved), { filter, config: currentConfig }),
    ])
  }

  async function submitQuestion(rawQuestion: string) {
    const trimmed = rawQuestion.trim()
    if (!trimmed) {
      setError("質問を入力してください。")
      return
    }
    if (loading) return

    setLoading(true)
    setError(null)
    setFailedQuestion(null)
    setQuestion("")
    try {
      if (compareMode) await streamIntoCompare(trimmed)
      else await streamIntoHistory(trimmed)
    } finally {
      setLoading(false)
    }
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

  function updateFeedback(entryId: string, patch: Partial<FeedbackDraft>) {
    setFeedbackByEntryId((current) => ({
      ...current,
      [entryId]: { ...(current[entryId] ?? emptyFeedback), ...patch },
    }))
  }

  async function submitFeedback(entry: HistoryEntry) {
    const draft = feedbackByEntryId[entry.id]
    if (!draft?.rating) return
    updateFeedback(entry.id, { status: "submitting", errorMessage: undefined })
    try {
      if (!E2E_MODE) {
        const result = await dataClient.models.Feedback.create({
          historyEntryId: entry.id,
          question: entry.question,
          agentAnswer: entry.answer,
          rating: draft.rating,
          comment: draft.comment.trim() || undefined,
          citations: entry.citations.map((citation) => citation.uri),
          searchPresetLabel: SEARCH_PRESETS[presetIndex].label,
          filterDepartment: department || undefined,
          latencyMs: entry.latencyMs,
        })
        if (result.errors?.length) throw new Error(result.errors.map((item) => item.message).join(", "))
      }
      updateFeedback(entry.id, { status: "submitted" })
    } catch (caught) {
      updateFeedback(entry.id, {
        status: "error",
        errorMessage: caught instanceof Error ? caught.message : String(caught),
      })
    }
  }

  return (
    <div className="workspace-app">
      <WorkspaceHeader
        chapterLabel="第3章・検索精度の調整"
        onReset={resetConversation}
        resetLabel="会話を消去"
        signOut={signOut}
      />
      <main className="workspace-grid">
        <section className="thread-panel" aria-label="問い合わせスレッド">
          <div className="thread-top">
            <div className="search-toolbar" role="group" aria-label="検索設定">
              <div className="toolbar-label"><SlidersHorizontal size={14} aria-hidden="true" />検索条件</div>
              <div className="config-item">
                <label htmlFor="dept-filter">部門</label>
                <Select id="dept-filter" value={department} onChange={setDepartment} options={DEPARTMENT_OPTIONS} ariaLabel="部門の絞り込み" />
              </div>
              <div className="config-item">
                <label htmlFor="search-mode">検索方式</label>
                <Select<number>
                  id="search-mode"
                  value={presetIndex}
                  onChange={setPresetIndex}
                  options={SEARCH_PRESETS.map((preset, index) => ({ value: index, label: preset.label, description: preset.description }))}
                  ariaLabel="検索方式"
                />
              </div>
              <label className={`config-toggle${compareMode ? " config-toggle--active" : ""}`}>
                <input
                  type="checkbox"
                  checked={compareMode}
                  onChange={(event) => { setCompareMode(event.target.checked); resetCompare() }}
                  aria-label="比較モードの切り替え"
                />
                <span className="config-toggle-track" aria-hidden="true" />
                <GitCompareArrows size={14} aria-hidden="true" />比較
              </label>
            </div>
          </div>

          <div className="thread-timeline" aria-live="polite">
            <div className={compareMode ? "timeline-inner timeline-inner--wide" : "timeline-inner"}>
              {compareMode ? (
                <ComparePanel
                  baseline={baseline}
                  improved={improved}
                  baselineLabel={baselineLabel}
                  improvedLabel={improvedLabel}
                  question={compareQuestion}
                />
              ) : (
                <>
                  {history.length === 0 ? (
                    <div className="empty-thread">
                      <MessageSquareText className="empty-icon" aria-hidden="true" />
                      <h3>検索条件を選んで質問してください</h3>
                    </div>
                  ) : null}
                  {history.map((entry) => {
                    const citationId = `citation-${entry.id}`
                    return (
                      <div className="qa-pair" key={entry.id}>
                        <article className="message message-user">
                          <span className="message-role">問い合わせ</span><p>{entry.question}</p>
                        </article>
                        <article className="message message-assistant">
                          <span className="message-role">
                            回答
                            {entry.streaming ? <span className="streaming-dot" aria-hidden="true" /> : null}
                            {!entry.streaming && entry.latencyMs != null ? <small>{(entry.latencyMs / 1000).toFixed(1)}秒</small> : null}
                          </span>
                          {entry.streaming && !entry.answer && entry.statusMessage ? <p className="status-copy">{entry.statusMessage}</p> : null}
                          <Streamdown className="markdown-content" parseIncompleteMarkdown plugins={streamdownPlugins} linkSafety={{ enabled: false }}>
                            {withCitationLinks(entry.answer, entry.citations, citationId)}
                          </Streamdown>
                          {entry.traceId ? <details><summary>トレースID</summary><code>{entry.traceId}</code></details> : null}
                          {entry.answer || entry.streaming ? (
                            <CitationList citations={entry.citations} loading={entry.streaming} idPrefix={citationId} />
                          ) : null}
                          {!entry.streaming && entry.answer ? (
                            <FeedbackWidget
                              draft={feedbackByEntryId[entry.id] ?? emptyFeedback}
                              onSelect={(rating) => updateFeedback(entry.id, { rating, status: "idle" })}
                              onCommentChange={(comment) => updateFeedback(entry.id, { comment, status: "idle" })}
                              onSubmit={() => void submitFeedback(entry)}
                            />
                          ) : null}
                        </article>
                      </div>
                    )
                  })}
                </>
              )}
              <div ref={endRef} aria-hidden="true" />
            </div>
          </div>

          <div className="composer-wrap">
            {error ? (
              <div className="error-banner" role="status">
                <AlertCircle size={16} aria-hidden="true" /><p>{error}</p>
                {failedQuestion ? <button type="button" className="retry-button" onClick={() => void submitQuestion(failedQuestion)}>再試行</button> : null}
              </div>
            ) : null}
            <form onSubmit={onSubmit} className="composer" aria-busy={loading}>
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleKeyDown} rows={3} placeholder="製品資料や障害対応手順について質問する" />
              <button type="submit" disabled={loading} aria-label="質問を送信"><ArrowUp size={17} aria-hidden="true" /></button>
            </form>
          </div>
        </section>
      </main>
    </div>
  )
}

function FeedbackWidget({ draft, onSelect, onCommentChange, onSubmit }: {
  draft: FeedbackDraft
  onSelect: (rating: FeedbackRating) => void
  onCommentChange: (comment: string) => void
  onSubmit: () => void
}) {
  if (draft.status === "submitted") {
    return <div className="feedback feedback--submitted" role="status"><CheckCircle2 size={14} aria-hidden="true" />評価を送信しました</div>
  }
  const submitting = draft.status === "submitting"
  return (
    <div className="feedback" role="group" aria-label="この回答を評価">
      <div className="feedback-buttons">
        <button type="button" className={draft.rating === "UP" ? "feedback-button is-selected" : "feedback-button"} onClick={() => onSelect("UP")} aria-pressed={draft.rating === "UP"} disabled={submitting}><ThumbsUp size={14} aria-hidden="true" />役に立った</button>
        <button type="button" className={draft.rating === "DOWN" ? "feedback-button is-selected" : "feedback-button"} onClick={() => onSelect("DOWN")} aria-pressed={draft.rating === "DOWN"} disabled={submitting}><ThumbsDown size={14} aria-hidden="true" />改善が必要</button>
      </div>
      {draft.rating ? (
        <div className="feedback-detail">
          <textarea value={draft.comment} onChange={(event) => onCommentChange(event.target.value)} rows={2} placeholder="補足コメント（任意）" disabled={submitting} />
          <button type="button" onClick={onSubmit} disabled={submitting}>評価を送信</button>
        </div>
      ) : null}
      {draft.status === "error" ? <p className="error-text">{draft.errorMessage}</p> : null}
    </div>
  )
}
