import { Activity, ChevronDown, ShieldCheck, Ticket } from "lucide-react"
import type { SupportAgentResponse, ToolEvent } from "../api"
import ToolActivity from "./ToolActivity"

type Props = {
  result: SupportAgentResponse | null
  toolEvents: ToolEvent[]
}

export default function Sidebar({ result, toolEvents }: Props) {
  const visibleEvents = toolEvents.filter((event, index, items) =>
    items.findIndex((item) => item.toolName === event.toolName) === index,
  )
  return (
    <div className="agent-inspector">
      <div className="inspector-heading">
        <div><h2>対応状況</h2></div>
      </div>
      <div className="status-line">
        {result?.jira.escalated ? <Ticket size={16} aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}
        <div>
          <strong>{result ? (result.jira.escalated ? "担当チームへ引き継ぎ済み" : "資料を確認して回答済み") : "回答を待っています"}</strong>
          {result?.jira.reason ? <p>{result.jira.reason}</p> : null}
          {result?.jira.issueKey ? <span className="ticket-key">{result.jira.issueKey}</span> : null}
        </div>
      </div>
      <details className="tool-disclosure">
        <summary><span><Activity size={14} aria-hidden="true" /> 処理履歴 ({visibleEvents.length})</span><ChevronDown size={14} aria-hidden="true" /></summary>
        {toolEvents.length ? <ToolActivity events={visibleEvents} /> : <p className="inspector-empty">処理はまだ始まっていません。</p>}
      </details>
    </div>
  )
}
