import { Check, Minus } from 'lucide-react'
import { FLOW_STATE_WORDS, type FlowSectionState } from '../api/flow'

/** One section's state as a mark (design §3.2): a tick, an amber dot, an open
 *  circle, a dash. `null` is "not known yet": the flow is unread or failed, and
 *  an unknown is never drawn as a tick. */
export function FlowStateDot({ state, testId }: { state: FlowSectionState | null; testId?: string }) {
  const s: FlowSectionState = state ?? 'unknown'
  const word = FLOW_STATE_WORDS[s]
  const mark = s === 'complete' ? <Check size={11} className="text-success" />
    : s === 'warning' ? <span className="block h-2 w-2 rounded-full bg-warn" />
      : s === 'incomplete' ? <span className="block h-2 w-2 rounded-full border-2 border-danger" />
        : s === 'not_needed' ? <Minus size={11} className="text-muted" />
          : <span className="text-[10px] font-semibold leading-none text-muted">?</span>
  return (
    <span data-testid={testId} data-state={s} role="img" aria-label={word} title={word}
      className="inline-flex h-4 w-4 shrink-0 items-center justify-center">
      {mark}
    </span>
  )
}
