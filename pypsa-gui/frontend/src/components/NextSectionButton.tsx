import { ArrowRight } from 'lucide-react'
import type { FlowSectionId } from '../api/flow'
import { nextSectionMeta } from '../layout/flowSections'

/** "Next: <section> →": the visible next step at the end of a flow section
 *  (UX-flow rule 3). The caller says how to move; `label` reads "Continue
 *  anyway" style when items are still open. Renders nothing after section 10. */
export function NextSectionButton({ from, onNext, label = 'Next', testId = 'next-section' }: {
  from: FlowSectionId
  onNext: (to: FlowSectionId) => void
  label?: string
  testId?: string
}) {
  const next = nextSectionMeta(from)
  if (!next) return null
  return (
    <button type="button" data-testid={testId} onClick={() => onNext(next.id)}
      className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1 text-[11px] text-accent hover:border-accent">
      {label}: {next.label} <ArrowRight size={11} />
    </button>
  )
}
