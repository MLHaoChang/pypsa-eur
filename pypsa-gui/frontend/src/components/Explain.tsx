// A one-sentence explanation under a KPI row or a chart (UX overhaul, agreed
// with the guided-demo stream). The owner wants what was computed kept
// visibly apart from what the assistant narrated:
//
//   computed  — fixed-template text from the insight rules; plain muted text.
//               In Expert the ids of the rules that produced it show on hover.
//   assistant — narration from the chat; a small "Assistant" tag and a left
//               border, so it never reads as a computed figure.
import { useUIStore } from '../store/uiStore'

export type ExplainOrigin = 'computed' | 'assistant'

export default function Explain({ text, origin, ruleIds }: {
  text: string
  origin: ExplainOrigin
  ruleIds?: string[]
}) {
  const expert = useUIStore(s => s.uiMode === 'expert')
  if (!text.trim()) return null
  const title = expert && ruleIds?.length ? `Rules: ${ruleIds.join(', ')}` : undefined
  return origin === 'assistant' ? (
    <p
      data-testid="explain"
      data-origin="assistant"
      title={title}
      className="border-l-2 border-info pl-2.5 text-[12.5px] leading-snug text-text"
    >
      <span className="mr-1.5 rounded-[4px] bg-info/12 px-1.5 py-px text-[10.5px] font-medium text-info">
        Assistant
      </span>
      {text}
    </p>
  ) : (
    <p data-testid="explain" data-origin="computed" title={title}
      className="text-[12.5px] leading-snug text-muted">
      {text}
    </p>
  )
}
