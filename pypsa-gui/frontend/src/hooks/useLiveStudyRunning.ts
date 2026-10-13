// A1-FE (deferred spec 2026-09-28 §2.4): is a live-network study running on
// the current project? Since P27a the backend refuses every network edit
// (409 `study_in_flight`) while an FMEA sweep, a frontier or a coupling /
// margin loop re-solves the live network. From Guided the only one reachable
// is the FMEA sweep (the Improve card's "Check risks"), so this reads the
// project's `fmea_modes` — the key and fetcher FmeaTab and the Improve card
// already poll, with the same shared interval — and the hub-design cards
// disable their "Let the assistant do this" / fix buttons with one sentence.
import { useQuery } from '@tanstack/react-query'
import { resultsApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { fmeaModesRefetchInterval } from './useStartFmeaSweep'

/** The plain sentence on the disabled buttons (and the card's note). */
export const LIVE_STUDY_EDIT =
  'A risk check is running — wait for it to finish or abort it before changing the network.'

/** `enabled: false` reads nothing (cards that edit nothing). */
export function useLiveStudyRunning(enabled = true): boolean {
  const project = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(project, 'results', 'fmea_modes'),
    queryFn: () => resultsApi.getFmeaModes(),
    refetchInterval: fmeaModesRefetchInterval,
    enabled: enabled && !!project,
  })
  return enabled && (data as { sweep_status?: string | null } | null | undefined)?.sweep_status === 'running'
}
