import type { Citation } from "./workspace"

export type { SupportAgentResponse, StreamEvent } from "./api"

export type HistoryItem = {
  id: string
  role: "user" | "assistant"
  text: string
  citations?: Citation[]
}
