import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

/**
 * Re-read `/simulation/status` when a study the caller is polling leaves
 * `running` (bug 2, guided-mode spec §2.2).
 *
 * A live-network study (FMEA sweep, frontier, coupling / margin loop) ends
 * with a closing base re-solve that leaves fresh dispatch on the network.
 * Every reader of `simulationStatus` — the dock greeting, the status bar,
 * SnapshotPicker — kept its cached "not solved" until something else
 * invalidated it. Each panel that already polls its study calls this with the
 * polled status; only a transition out of `running` seen while mounted fires
 * (a mount on a finished study does not, and `undefined` — the query still
 * loading — is not treated as a status).
 */
export function useStudyFinishedInvalidation(status: string | null | undefined): void {
  const qc = useQueryClient()
  const prev = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (status === undefined) return
    const before = prev.current
    prev.current = status
    if (before === 'running' && status !== 'running') {
      const project = useUIStore.getState().currentProject
      void qc.invalidateQueries({ queryKey: nk(project, 'simulationStatus') })
    }
  }, [status, qc])
}
