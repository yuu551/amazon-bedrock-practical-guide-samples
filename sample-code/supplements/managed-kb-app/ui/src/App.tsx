import { FormEvent, useEffect, useRef, useState } from "react"
import { ArrowUp, MessageSquareText } from "lucide-react"
import { Streamdown } from "streamdown"
import { WorkspaceHeader } from "./WorkspaceHeader"

type Doc = { index: number; title: string; text: string; source: string }
type Metrics = {
  first_event_seconds?: number
  first_text_seconds?: number
  total_seconds?: number
  trace_events?: number
  text_events?: number
}
type Entry = {
  id: string
  question: string
  answer: string
  docs: Doc[]
  streaming: boolean
  note: string
}
type Trace = {
  elapsed_seconds: number
  step: string
  status: string
  queries: string[]
  warnings: number
  failures: number
}
type Event = {
  type: string
  message?: string
  code?: string
  text?: string
  answer?: string
  documents?: Doc[]
  has_more_results?: boolean
  truncated?: boolean
  model?: string
  metrics?: Metrics
  usage?: { input_tokens: number; output_tokens: number }
  citations?: { references?: { resultIndex: number }[] }[]
} & Partial<Trace>
const suggestions = [
  "SAML認証でログインできない場合の確認項目",
  "監査ログの保持期間と出力方法",
  "Enterpriseプランの監査ログの保存期間と、SAML SSOでログインできない場合の確認手順を教えてください。",
]
const stepLabel: Record<string, string> = {
  SpeculativeRetrieval: "最初の検索",
  Planning: "追加の検索が必要か確認",
}

export default function App() {
  const [history, setHistory] = useState<Entry[]>([])
  const [question, setQuestion] = useState("")
  const [mode, setMode] = useState("agentic-model")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [status, setStatus] = useState("質問を入力してください。")
  const [traces, setTraces] = useState<Trace[]>([])
  const [metrics, setMetrics] = useState<Metrics>({})
  const [usage, setUsage] = useState<Event["usage"]>()
  const [config, setConfig] = useState({
    demo: false,
    model: "",
  })
  const [answerModel, setAnswerModel] = useState("")
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    fetch("/config")
      .then((r) => {
        if (!r.ok) throw new Error()
        return r.json()
      })
      .then(setConfig)
      .catch(() =>
        setError("設定を取得できませんでした。画面を再読み込みしてください。"),
      )
  }, [])
  useEffect(() => {
    if (history.length)
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [history.length])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const text = question.trim()
    if (!text || loading) return
    const id = crypto.randomUUID()
    setLoading(true)
    setError("")
    setQuestion("")
    setTraces([])
    setMetrics({})
    setUsage(undefined)
    setAnswerModel("")
    setStatus("検索しています…")
    setHistory((h) => [
      ...h,
      { id, question: text, answer: "", docs: [], streaming: true, note: "" },
    ])
    const update = (change: (entry: Entry) => Entry) =>
      setHistory((h) => h.map((e) => (e.id === id ? change(e) : e)))
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
    let completed = false
    function receive(e: Event) {
      if (e.type === "error") throw new Error(`${e.message} (${e.code})`)
      if (e.type === "status") setStatus(e.message || "処理中…")
      if (e.type === "text")
        update((entry) => ({ ...entry, answer: entry.answer + (e.text || "") }))
      if (e.type === "trace") {
        setTraces((rows) => [...rows, e as Trace])
        setStatus(`${stepLabel[e.step || ""] || e.step}…`)
      }
      if (e.type === "sources")
        update((entry) => ({
          ...entry,
          docs: e.documents || [],
          note: e.has_more_results
            ? "検索結果は最初のページを表示しています。"
            : "",
        }))
      if (e.type === "context" && e.truncated)
        update((entry) => ({
          ...entry,
          note: entry.note + "回答生成に渡す資料の一部を省略しました。",
        }))
      if (e.type === "done") {
        const ids = [
          ...new Set(
            (e.citations || []).flatMap((c) =>
              (c.references || []).map((r) => r.resultIndex + 1),
            ),
          ),
        ]
        const note =
          e.model === "AWS managed"
            ? `APIが返した引用：${ids.length ? ids.map((n) => `文書 ${n}`).join("、") : "なし"}`
            : "回答中の文書番号はモデルが付けたものです。資料本文と照合してください。"
        update((entry) => ({
          ...entry,
          answer: e.answer || "",
          note: [entry.note, note].filter(Boolean).join("\n"),
          streaming: false,
        }))
        setMetrics(e.metrics || {})
        setUsage(e.usage)
        setAnswerModel(e.model || "")
        setStatus("完了しました。")
        completed = true
      }
    }
    try {
      const response = await fetch("/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, mode }),
      })
      if (!response.ok || !response.body)
        throw new Error(`リクエストに失敗しました。HTTP ${response.status}`)
      reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""
      while (true) {
        const part = await reader.read()
        buffer += decoder.decode(part.value || new Uint8Array(), {
          stream: !part.done,
        })
        let pos: number
        while ((pos = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, pos)
          buffer = buffer.slice(pos + 1)
          if (line.trim()) receive(JSON.parse(line))
        }
        if (part.done) break
      }
      if (buffer.trim() || !completed)
        throw new Error(
          "完了通知の前に通信が終了しました。表示中の回答は途中の内容です。",
        )
    } catch (e) {
      setError(e instanceof Error ? e.message : "処理が完了しませんでした。")
      setStatus("処理が中断しました。")
      update((entry) => ({ ...entry, streaming: false }))
      if (reader) await reader.cancel().catch(() => {})
    } finally {
      setLoading(false)
    }
  }
  const reset = () => {
    if (loading) return
    setHistory([])
    setQuestion("")
    setError("")
    setTraces([])
    setMetrics({})
    setUsage(undefined)
    setAnswerModel("")
    setStatus("質問を入力してください。")
  }
  const modelLabel = "Claude Sonnet 4.6"
  return (
    <div className="workspace-app">
      <WorkspaceHeader
        chapterLabel="付録・Managed KB"
        onReset={reset}
        resetLabel="会話を消去"
      />
      <main className="workspace-grid workspace-grid--with-context">
        <section className="thread-panel" aria-label="問い合わせスレッド">
          <div className="thread-timeline">
            <div className="timeline-inner">
              {config.demo && (
                <p className="demo-banner">
                  画面確認用デモです。AWSには接続していません。
                </p>
              )}
              {!history.length && (
                <div className="empty-thread">
                  <MessageSquareText
                    className="empty-icon"
                    aria-hidden="true"
                  />
                  <h3>問い合わせ内容を入力してください</h3>
                  <div className="empty-suggestions">
                    {suggestions.map((s) => (
                      <button key={s} onClick={() => setQuestion(s)}>
                        {s}
                        <span aria-hidden="true">→</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {history.map((entry) => (
                <div className="qa-pair" key={entry.id}>
                  <article className="message message-user">
                    <span className="message-role">問い合わせ</span>
                    <p>{entry.question}</p>
                  </article>
                  <article className="message message-assistant">
                    <span className="message-role">
                      回答
                      {entry.streaming && (
                        <span className="streaming-dot" aria-hidden="true" />
                      )}
                    </span>
                    {!entry.answer && entry.streaming && (
                      <p className="status-line">{status}</p>
                    )}
                    <Streamdown
                      className="markdown-content"
                      parseIncompleteMarkdown
                      // 表やコードのコピー・全画面ボタンは使わないので出さない
                      controls={{ table: false, code: false }}
                      components={{ img: () => null }}
                    >
                      {entry.answer}
                    </Streamdown>
                    {!!entry.docs.length && (
                      <section
                        className="citation-block"
                        aria-label="取得した文書"
                      >
                        <div className="citation-heading">
                          <h3>取得した文書</h3>
                          <span>{entry.docs.length}件</span>
                        </div>
                        <ol className="citation-list">
                          {entry.docs.map((d) => (
                            <li key={d.index}>
                              <span className="citation-index">
                                {d.index + 1}
                              </span>
                              <div className="citation-copy">
                                <details>
                                  <summary>
                                    {d.title === `文書 ${d.index + 1}`
                                      ? d.text.match(/^#+\s+(.+)/m)?.[1] ||
                                        d.title
                                      : d.title}
                                  </summary>
                                  <pre>{d.text || "テキスト本文なし"}</pre>
                                  {d.source && <code>{d.source}</code>}
                                </details>
                              </div>
                            </li>
                          ))}
                        </ol>
                      </section>
                    )}
                    {entry.note && (
                      <p className="citation-empty supplement-note">
                        {entry.note}
                      </p>
                    )}
                  </article>
                </div>
              ))}
              <div ref={endRef} />
            </div>
          </div>
          <div className="composer-wrap">
            {error && (
              <div className="error-banner" role="alert">
                <p>{error}</p>
              </div>
            )}
            <form className="composer" onSubmit={submit} aria-busy={loading}>
              <textarea
                aria-label="質問"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                    e.preventDefault()
                    e.currentTarget.closest("form")?.requestSubmit()
                  }
                }}
                rows={3}
                maxLength={4000}
                placeholder="製品資料や障害対応手順について質問する"
              />
              <button
                type="submit"
                disabled={loading || !question.trim()}
                aria-label="質問を送信"
              >
                <ArrowUp size={17} aria-hidden="true" />
              </button>
            </form>
            <p className="request-note">
              1件ずつ独立した質問として検索します。前の会話はモデルに渡しません。
            </p>
          </div>
        </section>
        <aside className="context-panel" aria-label="検索設定と実行状況">
          <section className="inspector-section">
            <div className="inspector-heading">
              <h2>検索と回答</h2>
            </div>
            <label className="option-label" htmlFor="mode">
              検索方式
            </label>
            <select
              id="mode"
              value={mode}
              disabled={loading}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="agentic-model">Agentic Retrieval</option>
              <option value="retrieve-model">通常検索（Retrieve）</option>
              <option value="agentic-managed">
                Agentic Retrieval・回答もAWSに任せる
              </option>
            </select>
            <p className="status-line">
              回答モデル：
              {mode === "agentic-managed" ? "AWSが選択" : modelLabel}
            </p>
            <p className="citation-empty">
              回答は届いた部分から表示します。実行時はAWSの利用料金が発生します。
            </p>
          </section>
          <section className="inspector-section">
            <div className="inspector-heading">
              <h2>検索の経過</h2>
            </div>
            <p className="status-line" role="status" aria-live="polite">
              {status}
            </p>
            <ol className="trace-list">
              {traces.map((t, i) => (
                <li key={i}>
                  <strong>{stepLabel[t.step] || t.step}</strong>
                  <span>
                    {t.elapsed_seconds}秒 / {t.status}
                  </span>
                  {t.queries.length > 0 && <span>{t.queries.join("、")}</span>}
                  {(t.warnings > 0 || t.failures > 0) && (
                    <span>
                      警告 {t.warnings}件 / 失敗 {t.failures}件
                    </span>
                  )}
                </li>
              ))}
            </ol>
          </section>
          <section className="inspector-section">
            <div className="inspector-heading">
              <h2>実行時間と使用量</h2>
            </div>
            <dl className="case-facts">
              {[
                ["最初のイベント", metrics.first_event_seconds, "秒"],
                ["最初の回答", metrics.first_text_seconds, "秒"],
                ["全体", metrics.total_seconds, "秒"],
                ["回答の断片", metrics.text_events, "件"],
              ].map(([label, value, unit]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value === undefined ? "—" : `${value} ${unit}`}</dd>
                </div>
              ))}
            </dl>
            {answerModel && <p className="model-id">{answerModel}</p>}
            {usage && (
              <p className="status-line">
                入力 {usage.input_tokens} / 出力 {usage.output_tokens} トークン
              </p>
            )}
            <p className="citation-empty">
              検索・計画用モデルのトークン使用量は含みません。
            </p>
          </section>
        </aside>
      </main>
    </div>
  )
}
