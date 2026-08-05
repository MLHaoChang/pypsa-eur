/**
 * Positional bounds for the active Horizon filter.
 *
 * Derived from the SNAPSHOT INDEX, not from a results payload, so a tab can
 * window its fetch before any payload exists. That is why no probe is needed
 * here, unlike the canvas overlay: `/api/network/snapshots` is already fetched
 * by `Results.tsx` under this same key, so every tab reads it from cache.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { nk } from '../utils/queryKeys'
import { useResultsFilter, resolveRange } from '../pages/results/filterContext'

export function useResultsWindow(currentProject: string | null): {
  win: { from: number; to: number }
  winValid: boolean
} {
  const filter = useResultsFilter()
  const { data: snap } = useQuery({
    queryKey: nk(currentProject, 'snapshots'),
    queryFn: networkApi.getSnapshots,
    staleTime: 5_000,
  })
  const win = useMemo(
    () => resolveRange(snap?.snapshots ?? [], filter, snap?.periods),
    [snap, filter],
  )
  // An inverted range means the selected period is absent from this network —
  // "nothing to show", not "fetch everything".
  return { win, winValid: win.from <= win.to }
}
