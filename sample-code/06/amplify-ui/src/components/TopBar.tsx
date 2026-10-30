import { Eraser, LogOut } from "lucide-react"
import AgentAvatar from "./AgentAvatar"

type Props = {
  signOut?: () => void
  onReset: () => void
}

export default function TopBar({ signOut, onReset }: Props) {
  return (
    <div className="top-bar">
      <div className="top-bar-brand">
        <AgentAvatar size="sm" tone="calm" />
        <span className="top-bar-title">SampleConnect 問い合わせ対応</span>
      </div>
      <div className="top-bar-actions">
        <button
          type="button"
          className="top-bar-button"
          onClick={onReset}
          title="会話を消去"
        >
          <Eraser size={14} aria-hidden="true" />
          会話を消去
        </button>
        {signOut ? (
          <button
            type="button"
            className="top-bar-button top-bar-button--ghost"
            onClick={signOut}
          >
            <LogOut size={14} aria-hidden="true" />
            サインアウト
          </button>
        ) : null}
      </div>
    </div>
  )
}
