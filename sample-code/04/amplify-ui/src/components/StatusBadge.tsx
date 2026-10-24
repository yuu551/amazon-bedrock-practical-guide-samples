import { CheckCircle2, AlertCircle } from "lucide-react"

type Props = {
  ok: boolean
  label: string
}

export default function StatusBadge({ ok, label }: Props) {
  return (
    <span className={`status-badge ${ok ? "status-badge-ok" : "status-badge-alert"}`}>
      {ok ? (
        <CheckCircle2 size={16} aria-hidden="true" />
      ) : (
        <AlertCircle size={16} aria-hidden="true" />
      )}
      {label}
    </span>
  )
}
