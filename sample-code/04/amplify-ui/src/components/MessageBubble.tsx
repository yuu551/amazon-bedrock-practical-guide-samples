import ToolActivity from "./ToolActivity"
import { User } from "lucide-react"
import { Streamdown } from "streamdown"
import { streamdownPlugins } from "../lib/streamdownPlugins"
import { HistoryItem } from "../types"
import AgentAvatar from "./AgentAvatar"
import { withCitationLinks } from "../workspace"
import { CitationList } from "./WorkspaceChrome"

type Props = {
  item: HistoryItem
}
export default function MessageBubble({ item }: Props) {
  const isUser = item.role === "user"
  const citationId = `citation-${item.id}`

  return (
    <article className={`message message-${item.role}`}>
      <span className="message-role">
        {isUser ? (
          <span className="message-role-icon message-role-icon--user">
            <User size={14} aria-hidden="true" />
          </span>
        ) : (
          <AgentAvatar size="sm" tone="calm" />
        )}
        {isUser ? "利用者" : "回答"}
      </span>
      {isUser ? (
        <p>{item.text}</p>
      ) : (
        <>
          {item.toolEvents?.length ? (
            <details className="message-tools">
              <summary>呼び出したツール ({item.toolEvents.length})</summary>
              <ToolActivity events={item.toolEvents} showToolNames />
            </details>
          ) : null}
          <Streamdown
            className="markdown-content"
            plugins={streamdownPlugins}
            parseIncompleteMarkdown
            linkSafety={{ enabled: false }}
          >
            {withCitationLinks(item.text, item.citations ?? [], citationId)}
          </Streamdown>
          <CitationList citations={item.citations ?? []} idPrefix={citationId} />
        </>
      )}
    </article>
  )
}
