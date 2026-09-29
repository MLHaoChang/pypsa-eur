// Is the solver's dispatch fresh (it matches the network as it is now)?
// Phase 2 spec E6/E12, plan Tasks 4.2 and 5.1. No three.
//
// The 3D view owns this poll: the status bar polls `/simulation/status`
// only after an in-session solve, so a loaded, solved project (or one
// solved through the queue) would otherwise never be seen as fresh — or
// as stale after an edit. Same query key as the status bar, so the two
// share one request; the status bar toasts only from its own solve
// states, never from data this poll brings in.
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import type { SimulationStatus } from '../api/types'
import { nk } from '../utils/queryKeys'

export const STATUS_POLL_MS = 3000
/** A cached status answer this recent is trusted on first sight (someone is polling it). */
const RECENT_STATUS_MS = 2 * STATUS_POLL_MS

export interface DispatchFreshness {
  fresh: boolean
  dispatch: SimulationStatus['dispatch']
  /** Which solve is fresh (its objective and solve time); null when not fresh, undefined before the first answer. */
  solveId: string | null | undefined
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
  return { fresh: data?.dispatch === 'fresh', dispatch: data?.dispatch, solveId: solveIdOf(data) }
}

/** A fresh solve's identity (objective and solve time); null when not fresh; undefined before an answer. */
export function solveIdOf(data: SimulationStatus | undefined): string | null | undefined {
  if (!data) return undefined
  return data.dispatch === 'fresh' ? `${data.objective ?? ''}|${data.solve_time ?? ''}` : null
}

export interface SolveSettled {
  /**
   * The lists were refetched for this fresh solve and still hold that data:
   * an edit (a list refetch that brings different data) ends it at once,
   * before the status poll catches up; a refetch with identical data
   * (structural sharing keeps the reference) does not.
   */
  current: boolean
  /** When this solve was first seen (ms), or 0 when it was already fresh as the view opened (a warm cache is valid). */
  freshSince: number
}

/**
 * Settle the component lists on each fresh solve. A solve writes
 * `*_nom_opt` into them and no solve path refetches them (only results and
 * status); an edit refetches them but never the status. So on every solve
 * seen fresh — the first sight included, since the cached lists may predate
 * it — the lists are refetched, and the view is current only while they
 * still hold what that refetch brought. Sizing (E6) and results (E12) both
 * gate on it.
 */
export function useSolveSettled(project: string | null, solveId: string | null | undefined, lists: readonly { key: string; data: unknown }[]): SolveSettled {
  const qc = useQueryClient()
  const [settled, setSettled] = useState<{ project: string | null; solveId: string; refs: unknown[]; freshSince: number } | null>(null)
  // The first solve seen for this project (set once the effect ran).
  const first = useRef<{ project: string | null; solveId: string | null }>({ project, solveId: null })
  const keys = lists.map(l => l.key)
  const keysRef = useRef(keys)
  keysRef.current = keys
  useEffect(() => {
    if (!solveId) return
    if (first.current.project !== project || first.current.solveId === null) first.current = { project, solveId }
    // A later solve than the first: its chunks must postdate it (spec §6.3 rule 3).
    const freshSince = first.current.solveId !== solveId ? Date.now() : 0
    let cancelled = false
    const ks = keysRef.current
    const statusKey = nk(project, 'simulationStatus')
    // The status is refetched too: settle only on a confirmed, current answer
    // (a cached "fresh" may predate an edit made while the view was closed).
    Promise.all([statusKey, ...ks.map(k => nk(project, k))].map(queryKey => qc.refetchQueries({ queryKey, exact: true }))).then(() => {
      if (cancelled || solveIdOf(qc.getQueryData<SimulationStatus>(statusKey)) !== solveId) return
      setSettled({ project, solveId, refs: ks.map(k => qc.getQueryData(nk(project, k))), freshSince })
    }, () => { /* a failed refetch leaves the view not current: installed sizes, no results */ })
    return () => { cancelled = true }
  }, [project, solveId, qc])
  if (!solveId) return { current: false, freshSince: 0 }
  if (settled && settled.project === project && settled.solveId === solveId) {
    const current = lists.length === settled.refs.length && lists.every((l, i) => l.data === settled.refs[i])
    return { current, freshSince: current ? settled.freshSince : 0 }
  }
  // Not settled yet. The first solve seen as the view opens is trusted at
  // once when the cached status is recent — another view polls it, so the
  // warm cache opens filled while the settle refetch runs; an old cached
  // answer, or a later solve, waits for the settle.
  const firstSight = first.current.project !== project || first.current.solveId === null || first.current.solveId === solveId
  const statusAt = qc.getQueryState(nk(project, 'simulationStatus'))?.dataUpdatedAt ?? 0
  return { current: firstSight && Date.now() - statusAt <= RECENT_STATUS_MS, freshSince: 0 }
}
