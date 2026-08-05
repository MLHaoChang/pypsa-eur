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
  //
  // `snap` pending means we do not yet know the horizon. resolveRange's
  // empty-index fallback returns {0,0}, which is a sensible no-op for
  // slicing already-fetched data but reads as a VALID one-row window here —
  // so every query would fire for snapshot 0, cache, then refire. Gate on the
  // snapshot query having resolved, not just on the bounds being ordered.
  return { win, winValid: !!snap && win.from <= win.to }
}
