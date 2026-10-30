import { Clock, Loader2, MessageSquareText } from "lucide-react"
import { Streamdown } from "streamdown"
import { streamdownPlugins } from "./streamdownPlugins"
import { withCitationLinks, type Citation } from "./workspace"
import { CitationList } from "./components/WorkspaceChrome"

export type CompareColumnState = {
  answer: string
  citations: Citation[]
  latencyMs?: number
  traceId?: string
  streaming: boolean
  started: boolean
}

type Props = {
  baseline: CompareColumnState
  improved: CompareColumnState
  baselineLabel: string
  improvedLabel: string
  question?: string
}

function ResultColumn({ label, state, variant }: {
  label: string
  state: CompareColumnState
  variant: "baseline" | "improved"
}) {
  const citationId = `citation-compare-${variant}`
  return (
    <section className={`compare-column compare-column--${variant}`}>
      <header className="compare-header">
        <div>
          <span className="section-kicker">{variant === "baseline" ? "比較元" : "比較先"}</span>
          <h3>{label}</h3>
        </div>
        <span className="compare-metric">
          {state.streaming ? <Loader2 className="spinner" size={13} aria-hidden="true" /> : <Clock size={13} aria-hidden="true" />}
          {state.streaming ? "回答中" : state.latencyMs != null ? `${(state.latencyMs / 1000).toFixed(1)}秒` : "—"}
        </span>
      </header>
      {!state.started ? (
        <div className="compare-empty">
          <MessageSquareText size={20} aria-hidden="true" />
          <p>質問を送信すると結果が表示されます。</p>
        </div>
      ) : (
        <>
          <Streamdown className="markdown-content compare-answer" parseIncompleteMarkdown plugins={streamdownPlugins} linkSafety={{ enabled: false }}>
            {withCitationLinks(state.answer, state.citations, citationId)}
          </Streamdown>
          {state.traceId ? <details><summary>トレースID</summary><code>{state.traceId}</code></details> : null}
          <CitationList citations={state.citations} loading={state.streaming} idPrefix={citationId} />
        </>
      )}
    </section>
  )
}

export default function ComparePanel({ baseline, improved, baselineLabel, improvedLabel, question }: Props) {
  return (
    <section className="compare-panel" aria-label="比較結果" aria-busy={baseline.streaming || improved.streaming}>
      {question ? (
        <div className="compare-question">
          <span>問い合わせ</span><p>{question}</p>
        </div>
      ) : null}
      <div className="compare-grid">
        <ResultColumn label={baselineLabel} variant="baseline" state={baseline} />
        <ResultColumn label={improvedLabel} variant="improved" state={improved} />
      </div>
    </section>
  )
}
