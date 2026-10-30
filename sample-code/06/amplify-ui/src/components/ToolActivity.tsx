import { BookOpen, FileText, TicketCheck, Loader2, Check, Wrench } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import type { ToolEvent } from "../api"

type Props = {
  events: ToolEvent[]
}

type ToolMeta = {
  icon: LucideIcon
  label: string
  runningCopy: string
  doneCopy: string
  color: "kb" | "confluence" | "jira" | "other"
}

function resolveTool(toolName: string | undefined): ToolMeta {
  const name = (toolName ?? "").toLowerCase()
  if (name.includes("support_kb") || name.includes("support-kb") || name.includes("knowledge")) {
    return {
      icon: BookOpen,
      label: "製品資料",
      runningCopy: "製品資料を検索しています",
      doneCopy: "製品資料を確認しました",
      color: "kb",
    }
  }
  if (name.includes("confluence")) {
    return {
      icon: FileText,
      label: "社内文書",
      runningCopy: "社内文書を検索しています",
      doneCopy: "社内文書を確認しました",
      color: "confluence",
    }
  }
  if (name.includes("jira") || name.includes("ticket")) {
    return {
      icon: TicketCheck,
      label: "担当チケット",
      runningCopy: "担当チケットを起票しています",
      doneCopy: "担当チケットを起票しました",
      color: "jira",
    }
  }
  return {
    icon: Wrench,
    label: "外部連携",
    runningCopy: "外部の処理を実行しています",
    doneCopy: "外部の処理が完了しました",
    color: "other",
  }
}

export default function ToolActivity({ events }: Props) {
  if (events.length === 0) return null

  return (
    <ol className="tool-activity" aria-label="回答処理の履歴">
      {events.map((event) => {
        const meta = resolveTool(event.toolName)
        const Icon = meta.icon
        const running = event.status === "running"
        return (
          <li
            key={event.id}
            className={`tool-activity-item tool-activity-item--${meta.color} ${
              running ? "is-running" : "is-done"
            }`}
          >
            <span className="tool-activity-icon">
              <Icon size={14} aria-hidden={true} />
            </span>
            <span className="tool-activity-body">
              <span className="tool-activity-tool">{meta.label}</span>
              <span className="tool-activity-copy">
                {running ? meta.runningCopy : meta.doneCopy}
              </span>
            </span>
            <span className="tool-activity-status" aria-hidden="true">
              {running ? (
                <Loader2 size={14} className="spinner" />
              ) : (
                <Check size={14} />
              )}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
