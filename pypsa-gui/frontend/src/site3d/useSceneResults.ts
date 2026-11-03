// SC-03 (D07): the scene's results at the timeline's snapshot, read from
// `GET …/scene/results`. No three.
//
// - The snapshot is the native label (and period) `/network/snapshots` lists
//   at `resultsSnapshotIdx` (the timeline's own query, shared cache).
// - Freshness has one source, the server's gate: the request is made whatever
//   the local status says, and an out-of-date, running or failed solve comes
//   back as unavailable with its reason. The key carries the solve
//   (`freshSince`), the local current flag and the scene revision, so a new
//   solve, an edit or a scene change asks again; within one solve a snapshot
//   is read once (staleTime Infinity).
// - The timeline only reads: a step is a GET, never a proposal, a solve or a
//   regeneration. The previous snapshot's answer is held while the next one
//   loads (same solve only), and the next PREFETCH steps are prefetched, so
//   playback does not wait on one request per step.
import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { siteSceneResultsApi, type SceneResults, type SceneResultsQuery } from '../api/siteSceneResults'
import { nk } from '../utils/queryKeys'
import { snapshotQueryAt } from './sceneResults'

/** Steps prefetched ahead of the shown one (D12's per-request cost is unmeasured; a few steps hide it in playback). */
export const PREFETCH = 2

export interface SceneResultsInput {
  project: string | null
  siteId: string | null
  /** The Eye on and the scene enabled: off → nothing is fetched. */
  enabled: boolean
  current: boolean
  freshSince: number
  sceneRevision: number | null
  idx: number
}

export interface SceneResultsState {
  data: SceneResults | null
  /** The snapshot asked for; null when the timeline's index is not a snapshot of the model. */
  query: SceneResultsQuery | null
  loading: boolean
  error: unknown
}

export const sceneResultsKey = (i: Omit<SceneResultsInput, 'enabled' | 'idx'>, q: SceneResultsQuery): QueryKey =>
  nk(i.project, 'sceneResults', i.siteId, i.sceneRevision, i.freshSince, i.current, q.snapshot, q.period)

export function useSceneResults(input: SceneResultsInput): SceneResultsState {
  const { project, siteId, enabled, current, freshSince, sceneRevision, idx } = input
  const active = enabled && !!project && !!siteId
  const { data: snap } = useQuery({
    queryKey: nk(project, 'snapshots'), queryFn: networkApi.getSnapshots, enabled: active, staleTime: 5_000,
  })
  const query = useMemo(() => snapshotQueryAt(snap, idx), [snap, idx])
  const base = { project, siteId, sceneRevision, freshSince, current }
  const solve = `${project}|${siteId}|${sceneRevision}|${freshSince}|${current}`
  const res = useQuery({
    queryKey: query ? sceneResultsKey(base, query) : nk(project, 'sceneResults', siteId, 'none'),
    queryFn: () => siteSceneResultsApi.get(project!, siteId!, query!),
    enabled: active && !!query,
    staleTime: Infinity,
    retry: false,
    // Hold the last snapshot while the next loads, only for the same solve and scene.
    placeholderData: (prev, prevQuery) => {
      const k = prevQuery?.queryKey as unknown[] | undefined
      return k && `${k[1]}|${k[2]}|${k[3]}|${k[4]}|${k[5]}` === solve ? prev : undefined
    },
  })

  const qc = useQueryClient()
  useEffect(() => {
    if (!active || !query || !snap) return
    for (let k = 1; k <= PREFETCH; k++) {
      const next = snapshotQueryAt(snap, idx + k)
      if (!next) break
      void qc.prefetchQuery({
        queryKey: sceneResultsKey({ project, siteId, sceneRevision, freshSince, current }, next),
        queryFn: () => siteSceneResultsApi.get(project!, siteId!, next),
        staleTime: Infinity, retry: false,
      })
    }
  }, [active, query, snap, idx, qc, project, siteId, sceneRevision, freshSince, current])

  if (!active) return { data: null, query: null, loading: false, error: null }
  if (!query) return { data: null, query: null, loading: false, error: null }
  return { data: res.data ?? null, query, loading: res.isFetching && !res.data, error: res.error }
}
