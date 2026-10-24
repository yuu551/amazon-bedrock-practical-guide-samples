import { Streamdown } from "streamdown"
import { streamdownPlugins } from "../lib/streamdownPlugins"
import { useStreamBuffer } from "../hooks/useStreamBuffer"
import AgentAvatar from "./AgentAvatar"
import { withCitationLinks, type Citation } from "../workspace"
import { CitationList } from "./WorkspaceChrome"

type Props = {
  messageId?: string
  content: string
  isStreaming: boolean
  citations?: Citation[]
}

export default function StreamingMessage({
  messageId = "streaming",
  content,
  isStreaming,
  citations = [],
}: Props) {
  const { displayed, isDripping } = useStreamBuffer(content, isStreaming)
  const citationId = `citation-${messageId}`

  return (
    <article className="message message-assistant message-assistant--streaming">
      <span className="message-role">
        <AgentAvatar size="sm" tone="working" breathing={isStreaming} />
        回答
        {isStreaming ? <span className="streaming-dot" aria-hidden="true" /> : null}
      </span>
      {displayed ? (
        <>
          <Streamdown
            animated={{ animation: "blurIn", duration: 600, sep: "word" }}
            isAnimating={isDripping}
            className="markdown-content"
            plugins={streamdownPlugins}
            parseIncompleteMarkdown
            linkSafety={{ enabled: false }}
          >
            {withCitationLinks(displayed, citations, citationId)}
          </Streamdown>
          <CitationList citations={citations} loading={isStreaming} idPrefix={citationId} />
        </>
      ) : (
        <div className="typing-indicator" aria-label="回答を生成中">
          <span className="dot" />
          <span className="dot" />
          <span className="dot" />
        </div>
      )}
    </article>
  )
}
