import { Sparkles, BookOpen, FileText, TicketCheck } from "lucide-react"
import type { LucideIcon } from "lucide-react"

export default function Hero() {
  return (
    <header className="hero">
      <div className="hero-main">
        <p className="eyebrow">
          <Sparkles size={14} aria-hidden="true" />
          自動回答と担当者連携
        </p>
        <h1>SampleConnect 問い合わせ対応</h1>
        <p className="hero-copy">
          製品資料と社内文書を調べ、根拠付きで回答します。
          担当者の判断が必要な場合は、担当チケットを起票します。
        </p>
      </div>
      <aside className="hero-tools" aria-label="利用可能なツール">
        <HeroTool
          Icon={BookOpen}
          title="製品資料"
          desc="障害対応・既知の事象・よくある質問"
          tone="kb"
        />
        <HeroTool
          Icon={FileText}
          title="社内文書"
          desc="運用記録・障害の振り返り"
          tone="confluence"
        />
        <HeroTool
          Icon={TicketCheck}
          title="担当チケット"
          desc="担当チームへの引き継ぎ"
          tone="jira"
        />
      </aside>
    </header>
  )
}

type Tone = "kb" | "confluence" | "jira"

type HeroToolProps = {
  Icon: LucideIcon
  title: string
  desc: string
  tone: Tone
}

function HeroTool({ Icon, title, desc, tone }: HeroToolProps) {
  return (
    <div className={`hero-tool hero-tool--${tone}`}>
      <span className="hero-tool-icon">
        <Icon size={16} aria-hidden={true} />
      </span>
      <span className="hero-tool-text">
        <strong>{title}</strong>
        <span>{desc}</span>
      </span>
    </div>
  )
}
