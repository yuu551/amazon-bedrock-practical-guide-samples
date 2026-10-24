import type { ReactNode } from "react"
import {
  ExternalLink,
  LogOut,
  RotateCcw,
} from "lucide-react"
import {
  citationHref,
  citationTitle,
  formatCitationDate,
  type Citation,
} from "../workspace"

type HeaderProps = {
  chapterLabel: string
  onReset: () => void
  resetLabel: string
  signOut?: () => void
}

export function WorkspaceHeader({
  chapterLabel,
  onReset,
  resetLabel,
  signOut,
}: HeaderProps) {
  return (
    <header className="workspace-header">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true">SC</span>
        <div>
          <strong>SampleConnect</strong>
          <span>問い合わせ対応</span>
        </div>
      </div>
      <div className="workspace-heading">
        <span>{chapterLabel}</span>
        <h1>問い合わせ対応</h1>
      </div>
      <div className="workspace-actions">
        <button type="button" className="quiet-button" onClick={onReset} aria-label={resetLabel}>
          <RotateCcw size={15} aria-hidden="true" />
          <span>{resetLabel}</span>
        </button>
        {signOut ? (
          <button type="button" className="icon-button" onClick={signOut} aria-label="サインアウト">
            <LogOut size={16} aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </header>
  )
}

type CitationListProps = {
  citations: Citation[]
  loading?: boolean
  idPrefix?: string
}

export function CitationList({
  citations,
  loading = false,
  idPrefix = "citation",
}: CitationListProps) {
  return (
    <section className="citation-block" aria-label="引用元">
      <div className="citation-heading">
        <h3>引用元</h3>
        {citations.length > 0 ? <span>{citations.length}件</span> : null}
      </div>
      {loading && citations.length === 0 ? (
        <p className="citation-empty" role="status">根拠を確認しています</p>
      ) : citations.length > 0 ? (
        <ol className="citation-list">
          {citations.map((citation, index) => {
            const href = citationHref(citation)
            const status = citation.status ?? (href ? "available" : "unavailable")
            return (
              <li id={`${idPrefix}-${index + 1}`} key={`${citation.uri}-${index}`} tabIndex={-1}>
                <span className="citation-index">{index + 1}</span>
                <div className="citation-copy">
                  <strong>{citationTitle(citation)}</strong>
                  {citation.snippet ? <p>{citation.snippet}</p> : null}
                  <div className="citation-meta">
                    <span>{formatCitationDate(citation.updatedAt)}</span>
                    {status === "restricted" ? <span>閲覧には権限が必要です</span> : null}
                  </div>
                  {href ? (
                    <a href={href} target="_blank" rel="noreferrer">
                      資料を開く
                      <ExternalLink size={13} aria-hidden="true" />
                    </a>
                  ) : (
                    <code>{citation.uri}</code>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      ) : (
        <p className="citation-empty">引用はありません。</p>
      )}
    </section>
  )
}

export function ContextPanel({ children }: { children: ReactNode }) {
  return (
    <aside className="context-panel" aria-label="対応状況">
      <section className="inspector-section">{children}</section>
    </aside>
  )
}
