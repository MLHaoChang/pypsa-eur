import { FLOW_SECTIONS, type FlowNote, type FlowRunBlocker, type FlowVerdict } from '../api/flow'
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
  /** The flow is deferred while a solve holds the network:
   *  "Checking whether the project is ready…". Never a tick, never a block. */
  checking: boolean
}

/** `optimisation` is the gate of the header's Run (it queues the optimisation,
 *  and with Load flow on its Stage 2, so the load flow's verdict too);
 *  `all` is the AND over every requested kind (`run_ready`), which the
 *  readiness section reports. */
export function useRunGate(kind: 'optimisation' | 'all' = 'optimisation'): RunGate {
  const { flow } = useProjectFlow()
  if (!flow || flow.deferred) {
    return { blocked: false, reason: null, blocker: null, notes: [], checking: !!flow?.deferred }
  }
  const verdict = kind === 'optimisation' ? headerVerdict(flow.verdicts) : null
  const ready = verdict ? verdict.ready : flow.run_ready
  if (ready) return { blocked: false, reason: null, blocker: null, notes: flow.notes, checking: false }
  const blocker = verdict ? verdict.blocker : flow.run_blocker
  return {
    blocked: true,
    reason: blocker?.message ?? 'The server would refuse this run.',
    blocker,
    notes: flow.notes,
    checking: false,
  }
}

/** The header Run's verdict: the optimisation's AND the load flow's when Load
 *  flow is on, the first blocker in flow order (as `run_separability`'s bundled
 *  load flow answers). */
function headerVerdict(verdicts: { optimisation: FlowVerdict, loadflow?: FlowVerdict | null } | undefined): FlowVerdict | undefined {
  const opt = verdicts?.optimisation
  const lf = verdicts?.loadflow
  if (!opt || !lf || lf.ready) return opt
  if (opt.ready || !opt.blocker || !lf.blocker) return { ready: false, blocker: opt.blocker ?? lf.blocker }
  const at = (b: FlowRunBlocker) => FLOW_SECTIONS.indexOf(b.section)
  return { ready: false, blocker: at(lf.blocker) < at(opt.blocker) ? lf.blocker : opt.blocker }
}
