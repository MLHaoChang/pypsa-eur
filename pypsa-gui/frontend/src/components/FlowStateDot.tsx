import { Check, Clock3, Minus } from 'lucide-react'
import { FLOW_STATE_WORDS, type FlowSectionState } from '../api/flow'

/** One section's state as a mark (design §3.2): a tick, an amber dot, an open
 *  circle, a dash. `null` is "not known yet": the flow is unread or failed, and
 *  an unknown is never drawn as a tick. */
export const RUN_IN_PROGRESS = 'run in progress'

/** `running`: a run holds the network, so an unknown state reads "run in progress"
 *  (template-e2e 25), not "not known yet". A known state is shown as it is. */
export function FlowStateDot({ state, testId, running = false }: { state: FlowSectionState | null; testId?: string; running?: boolean }) {
  const s: FlowSectionState = state ?? 'unknown'
  const busy = running && s === 'unknown'
  const word = busy ? RUN_IN_PROGRESS : FLOW_STATE_WORDS[s]
  const mark = s === 'complete' ? <Check size={11} className="text-success" />
    : s === 'warning' ? <span className="block h-2 w-2 rounded-full bg-warn" />
      : s === 'incomplete' ? <span className="block h-2 w-2 rounded-full border-2 border-danger" />
        : s === 'not_needed' ? <Minus size={11} className="text-muted" />
          : busy ? <Clock3 size={11} className="text-muted" />
          : <span className="text-[10px] font-semibold leading-none text-muted">?</span>
  return (
    <span data-testid={testId} data-state={s} role="img" aria-label={word} title={word}
      className="inline-flex h-4 w-4 shrink-0 items-center justify-center">
      {mark}
    </span>
  )
}
