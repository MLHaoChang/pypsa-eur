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
  /**
   * What to actually pass to a `resultsApi.get*` getter's `range` argument.
   *
   * `undefined` when `win` covers the WHOLE horizon (`{0, count-1}`) — sending
   * no `from`/`to` at all keeps the request byte-identical to the pre-window
   * one, and — load-bearing — is the only way to avoid the backend's
   * MAX_RESPONSE_VALUES cap: `routers/results.py::_wants_slice` only slices
   * (and therefore only caps) when the request actually carries bounds. Every
   * tab's default view (small networks, and Reset on any network) resolves to
   * the whole horizon, so without this, those requests would newly risk
   * silent server-side truncation that didn't exist before windowing shipped.
   * Passing `win` (`{0, count-1}`) instead of `undefined` would round-trip
   * the exact same numbers but take the ranged code path and its cap —
   * `undefined` is not a simplification, it changes which path the request
   * takes. `win` itself is untouched — tabs still key their queries off
   * `win.from`/`win.to` so caching by window position keeps working.
   */
  fetchRange: { from: number; to: number } | undefined
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
  const total = snap?.snapshots?.length ?? 0
  const isWhole = win.from === 0 && win.to === total - 1
  // An inverted range means the selected period is absent from this network —
  // "nothing to show", not "fetch everything".
  //
  // `snap` pending means we do not yet know the horizon. resolveRange's
  // empty-index fallback returns {0,0}, which is a sensible no-op for
  // slicing already-fetched data but reads as a VALID one-row window here —
  // so every query would fire for snapshot 0, cache, then refire. Gate on the
  // snapshot query having resolved, not just on the bounds being ordered.
  return {
    win,
    winValid: !!snap && win.from <= win.to,
    // `undefined` when the window IS the whole horizon: sending no bounds keeps
    // the request byte-identical to the pre-window one AND avoids the backend's
    // MAX_RESPONSE_VALUES cap, which only applies on the ranged path. Passing
    // {0, count-1} instead would silently truncate wide networks.
    fetchRange: isWhole ? undefined : win,
  }
}
