import { Bot } from "lucide-react"

type Size = "sm" | "md" | "lg"

type Props = {
  size?: Size
  breathing?: boolean
  tone?: "calm" | "working" | "alert"
}

const SIZE_PX: Record<Size, number> = { sm: 28, md: 40, lg: 56 }
const ICON_PX: Record<Size, number> = { sm: 16, md: 22, lg: 30 }

export default function AgentAvatar({
  size = "md",
  breathing = false,
  tone = "calm",
}: Props) {
  const px = SIZE_PX[size]
  const iconPx = ICON_PX[size]

  return (
    <span
      className={`agent-avatar agent-avatar--${size} agent-avatar--${tone}${
        breathing ? " agent-avatar--breathing" : ""
      }`}
      style={{ width: px, height: px }}
      aria-hidden="true"
    >
      <Bot size={iconPx} strokeWidth={1.75} />
    </span>
  )
}
