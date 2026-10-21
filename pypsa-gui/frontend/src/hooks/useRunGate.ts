import type { FlowNote, FlowRunBlocker } from '../api/flow'
import { useProjectFlow } from './useProjectFlow'

/** The Run's gate, read from `/flow` (design §3.3): disabled exactly when the
 *  server would refuse (`run_ready === false`), with the reason by section;
 *  display-only gaps come back as `notes` beside an enabled Run. A flow that
 *  could not be read, or is deferred while a solve holds the network, never
 *  disables the Run: the server keeps refusing on its own. */
export interface RunGate {
  blocked: boolean
  /** "Connection & grid: no import connection for the reliability study". */
  reason: string | null
  blocker: FlowRunBlocker | null
  notes: FlowNote[]
}

/** `optimisation` is the gate of the header's Run (it queues the optimisation);
 *  `all` is the AND over every requested kind (`run_ready`), which the
 *  readiness section reports. */
export function useRunGate(kind: 'optimisation' | 'all' = 'optimisation'): RunGate {
  const { flow } = useProjectFlow()
  if (!flow || flow.deferred) return { blocked: false, reason: null, blocker: null, notes: [] }
  const verdict = kind === 'optimisation' ? flow.verdicts?.optimisation : null
  const ready = verdict ? verdict.ready : flow.run_ready
  if (ready) return { blocked: false, reason: null, blocker: null, notes: flow.notes }
  const blocker = verdict ? verdict.blocker : flow.run_blocker
  return {
    blocked: true,
    reason: blocker?.message ?? 'The server would refuse this run.',
    blocker,
    notes: flow.notes,
  }
}
