import { LogOut, RotateCcw } from "lucide-react"

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
