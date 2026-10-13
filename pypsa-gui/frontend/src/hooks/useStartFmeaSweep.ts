// The FMEA B/C sweep start, lifted from FmeaTab (guided-mode spec §5.3,
// §5.9) so the hub-design Improve card starts exactly the same sweep.
// Behaviour unchanged: the project's stress-scenario registry is read first,
// an unreadable registry refuses the sweep, and a refused start toasts the
// backend's own sentence.
import { useMutation, useQueryClient, type Query } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { resultsApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { blockerMessage } from '../utils/blockerMessage'

/** Per-query "one more read" latch (A5). Keyed by the `Query` object React
 *  Query hands to `refetchInterval`; a GC'd query is a fresh object, which is
 *  the intended reset. `extraAt` is the `dataUpdateCount` of the first sample
 *  after `running`: React Query re-evaluates `refetchInterval` several times
 *  per sample (every observer render), so the latch answers per SAMPLE, not
 *  per call — a second evaluation of the same sample must not cancel the
 *  pending extra tick. */
const extraTick = new WeakMap<Query, { running: boolean; extraAt?: number }>()

/** The `fmea_modes` poll FmeaTab, the Improve card and the live-study probe
 *  share (A5, deferred spec 2026-09-28 §2.2): 2 s while the sweep runs, 2 s
 *  ONCE more on the first sample after it leaves `running` (that sample can
 *  still carry the partial rows while the finished ones land), then off. */
export function fmeaModesRefetchInterval(q: Query): number | false {
  const status = (q.state.data as { sweep_status?: string } | null | undefined)?.sweep_status
  if (status === 'running') {
    extraTick.set(q, { running: true })
    return 2000
  }
  const latch = extraTick.get(q)
  if (!latch) return false
  if (latch.running) {
    extraTick.set(q, { running: false, extraAt: q.state.dataUpdateCount })
    return 2000
  }
  if (latch.extraAt === q.state.dataUpdateCount) return 2000
  extraTick.delete(q)
  return false
}

export interface StartFmeaSweepOptions {
  /** Runs once the sweep has started. FmeaTab refetches its modes query
   *  here; without it the project's `fmea_modes` query is invalidated. */
  onStarted?: () => void
}

export function useStartFmeaSweep(options?: StartFmeaSweepOptions) {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const onStarted = options?.onStarted

  return useMutation({
    mutationFn: async () => {
      // E2E review m4: an unreadable registry used to start a class-B-only
      // sweep silently — the class C rows would just be missing.
      let scenarios: Array<Record<string, unknown>> = []
      if (currentProject) {
        let reg: { scenarios?: Array<Record<string, unknown>>; error?: string | null }
        try {
          reg = await resultsApi.getStressScenarios(currentProject)
        } catch (e) {
          throw new Error(
            `could not read this project's stress-scenario registry ` +
            `(${blockerMessage(e)}) — sweep not started, class C would be missing`)
        }
        if (reg?.error) {
          throw new Error(`${reg.error} — sweep not started, class C would be missing`)
        }
        scenarios = reg?.scenarios ?? []
      }
      return resultsApi.postFmeaSweep(scenarios)
    },
    onSuccess: () => {
      if (onStarted) onStarted()
      else void qc.invalidateQueries({
        queryKey: nk(currentProject, 'results', 'fmea_modes') })
    },
    // The backend's own sentence (a 409 names the study that blocks the
    // sweep; a 422 names the missing VOLL), not axios' status-code line —
    // the other panels already read it through `blockerMessage` (M11).
    onError: (e: unknown) => toast.error(`Sweep failed to start: ${blockerMessage(e)}`),
  })
}
