// Is the solver's dispatch fresh (it matches the network as it is now)?
// Phase 2 spec E6/E12, plan Tasks 4.2 and 5.1. No three.
//
// The 3D view owns this poll: the status bar polls `/simulation/status`
// only after an in-session solve, so a loaded, solved project (or one
// solved through the queue) would otherwise never be seen as fresh — or
// as stale after an edit. Same query key as the status bar, so the two
// share one request; the status bar toasts only from its own solve
// states, never from data this poll brings in.
import { useQuery } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import type { SimulationStatus } from '../api/types'
import { nk } from '../utils/queryKeys'

export const STATUS_POLL_MS = 3000

export interface DispatchFreshness {
  fresh: boolean
  dispatch: SimulationStatus['dispatch']
}

export function useDispatchFresh(project: string | null): DispatchFreshness {
  // Only `data` is read: react-query re-renders a component for the fields
  // it reads, and `dataUpdatedAt` changes on every poll (render isolation,
  // plan Task 2.2 — the scene must not re-render every 3 s).
  const { data } = useQuery({
    queryKey: nk(project, 'simulationStatus'),
    queryFn: simulationApi.getStatus,
    enabled: !!project,
    refetchInterval: STATUS_POLL_MS,
  })
  return { fresh: data?.dispatch === 'fresh', dispatch: data?.dispatch }
}
