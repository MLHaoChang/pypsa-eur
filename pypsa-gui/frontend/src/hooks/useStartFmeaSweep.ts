// The FMEA B/C sweep start, lifted from FmeaTab (guided-mode spec §5.3,
// §5.9) so the hub-design Improve card starts exactly the same sweep.
// Behaviour unchanged: the project's stress-scenario registry is read first,
// an unreadable registry refuses the sweep, and a refused start toasts the
// backend's own sentence.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { resultsApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { blockerMessage } from '../utils/blockerMessage'

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
