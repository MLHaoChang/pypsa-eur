// A2 — detect a tab / backend project mismatch (deferred spec 2026-09-28 §2.1,
// "Detection"). `GET /network/meta` carries the backend's binding
// (`loaded_project`); this tab believes it shows `currentProject`. The
// mismatch is raised only when ALL of:
//   • no project switch is in flight (`projectSwitchInProgress === false`),
//   • the sample is settled (a completed fetch, not one in progress),
//   • `loaded_project != null && loaded_project !== currentProject` held on
//     TWO consecutive settled samples.
// One disagreeing sample is ignored: the open path, `switchToProject` and the
// chat's `project_rebound` handler all have a window where the backend has
// moved and `currentProject` has not. An agreeing sample (or a null binding)
// clears it. A study tab (planning → dynamics: no network by design) and a
// tab with no project never raise it.
import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import type { NetworkMeta } from '../api/types'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

/** Meta poll while nothing is suspect (`idleMs`), and while a first
 *  disagreeing sample waits for its confirmation (`confirmMs`). The meta
 *  route is a cheap in-memory read. Mutable so a test can take the timer out
 *  and drive the samples itself. */
export const mismatchPoll = { idleMs: 5_000, confirmMs: 1_000 }

/** `at` is the query's `dataUpdateCount` for the sample: two samples are
 *  distinct fetches even when they land in the same millisecond. */
interface Pending { tab: string; backend: string; at: number }

export function useProjectMismatchDetection(isStudy: boolean): void {
  const currentProject = useUIStore(s => s.currentProject)
  const pending = useRef<Pending | null>(null)
  const qc = useQueryClient()
  const key = nk(currentProject, 'meta')
  // The interval function sees neither props nor effects: hand it the
  // study flag through a ref.
  const studyRef = useRef(isStudy)
  studyRef.current = isStudy
  const { data, isFetching } = useQuery({
    queryKey: key,
    queryFn: networkApi.getMeta,
    enabled: !!currentProject,
    // Re-sample quickly only inside the confirm window: a disagreeing sample
    // that COULD raise the mismatch and has not yet. A raised mismatch, a
    // study tab (never raisable) and a switch in flight all poll at the idle
    // rate (P27b gate note 1: a study tab polled at 1 Hz forever). Read from
    // the sample itself: React Query computes the interval when the data
    // lands, before this hook's effect has recorded it as pending.
    refetchInterval: (q) => {
      const backend = (q.state.data as NetworkMeta | undefined)?.loaded_project ?? null
      const ui = useUIStore.getState()
      const unconfirmed = backend != null && ui.currentProject != null
        && backend !== ui.currentProject && ui.projectMismatch == null
        && !studyRef.current && !ui.projectSwitchInProgress
      return unconfirmed ? mismatchPoll.confirmMs : mismatchPoll.idleMs
    },
  })
  // Each completed fetch is one sample — counted, not timed, so two samples
  // landing in the same millisecond are still two.
  const seq = qc.getQueryState(key)?.dataUpdateCount ?? 0

  useEffect(() => {
    if (!data || isFetching) return
    const ui = useUIStore.getState()
    const tab = ui.currentProject
    const backend = data.loaded_project ?? null
    const clear = () => {
      pending.current = null
      if (ui.projectMismatch) ui.setProjectMismatch(null)
    }
    if (!tab || isStudy) { clear(); return }
    // A sample taken mid-switch never counts, either way.
    if (ui.projectSwitchInProgress) { pending.current = null; return }
    if (backend == null || backend === tab) { clear(); return }
    const p = pending.current
    if (ui.projectMismatch?.tab === tab && ui.projectMismatch.backend === backend) return
    if (p && p.tab === tab && p.backend === backend && p.at !== seq) {
      pending.current = null
      ui.setProjectMismatch({ tab, backend })
      return
    }
    pending.current = { tab, backend, at: seq }
  }, [data, seq, isFetching, isStudy, currentProject])
}
