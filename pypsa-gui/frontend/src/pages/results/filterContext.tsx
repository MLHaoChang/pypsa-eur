import { createContext, useContext, type ReactNode } from 'react'

// ── Results horizon filter ───────────────────────────────────────────────────
// A simple [fromIdx, toIdx] slice into the snapshots array, propagated via
// context so the three Results tabs (Capacity Expansion / Dispatch / Load Flow)
// can apply the same filter without prop-drilling through every child chart.
//
// `null` for either bound means "no clamp" (use the natural beginning / end).
// Tab components convert to concrete indices using their fetched TSPayload.
// Helpers below shave the time-series and string-index aggregations.

export interface ResultsFilter {
  fromIso: string | null
  toIso:   string | null
  /** Multi-period only. When set, results are sliced to rows where
   *  `periods[i] === selectedPeriod`. Null = "aggregated across all periods".
   *  Ignored on single-period (flat) snapshots — TS payloads don't have a
   *  `periods` array there. */
  selectedPeriod: number | string | null
}

const Ctx = createContext<ResultsFilter>({ fromIso: null, toIso: null, selectedPeriod: null })
export const useResultsFilter = () => useContext(Ctx)

export function ResultsFilterProvider(
  { value, children }: { value: ResultsFilter; children: ReactNode },
) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// Given a TSPayload's index + (optional) parallel periods array + the active
// filter, return the [from, to] inclusive bounds (or null when the selected
// period has no rows). Multi-period mode finds the contiguous slice
// `[firstPeriodRow, lastPeriodRow]`; the iso bounds further clip that slice.
//
// Why contiguous: PyPSA emits MultiIndex snapshots sorted by (period, timestep),
// so all rows for a given period are adjacent in the array. Verified in
// backend/routers/network.py `set_multi_period_snapshots` which builds
// `[period_level, timestep_level]` arrays in period order.
//
// Year normalisation: the iso clamp REWRITES `fromIso` / `toIso`'s year
// prefix to match the index's base year before comparing. Don't remove
// this — PyPSA replicates ONE operational year across every investment
// period (every row's iso starts with the same year), so a stale
// display-year on the filter inputs would otherwise collapse the slice
// to empty. See the inline comment in the iso block for the full story.
export function resolveRange(
  index: string[],
  filter: ResultsFilter,
  periods?: Array<number | string>,
): { from: number; to: number } {
  if (index.length === 0) return { from: 0, to: 0 }
  let from = 0
  let to = index.length - 1
  // Period slice first — narrows to a contiguous block before the iso clamp.
  if (filter.selectedPeriod != null && periods && periods.length === index.length) {
    let pFrom = -1, pTo = -1
    for (let i = 0; i < periods.length; i++) {
      if (periods[i] === filter.selectedPeriod) {
        if (pFrom < 0) pFrom = i
        pTo = i
      }
    }
    if (pFrom < 0) {
      // Selected period not present — return an empty range. Caller checks
      // `from > to` by convention; we use the explicit collapse below.
      return { from: index.length, to: -1 }
    }
    from = pFrom
    to = pTo
  }
  const { fromIso, toIso } = filter
  // Multi-period networks replicate ONE base operational year across
  // every investment period — every row in `index` starts with the same
  // year prefix (e.g. all "2026-…" even when this row belongs to period
  // 2028). If `fromIso` / `toIso` carry a different year (the user typed
  // it while viewing a non-base period and a stale display-year leaked
  // through `toStore()`, OR they switched from period-N view to
  // aggregated and the iso bounds didn't reset), the lexicographic
  // compare collapses the slice to empty: `index[from] < "2027-…"` is
  // true for every "2026-…" row, the `from` cursor walks past the end,
  // and every KPI reads zero with no UX banner.
  //
  // Defence: normalise the year prefix of fromIso/toIso to the index's
  // own year before comparing. Semantically this means "after
  // <month-day-hour> across all rows in the current slice" — the only
  // interpretation that makes sense in aggregated multi-period mode,
  // and a no-op when the year already matches (single-period flat
  // networks, or correctly-stored base-year iso bounds).
  const baseYearPrefix = index[from].slice(0, 4)
  if (fromIso) {
    const normalizedFrom = baseYearPrefix + fromIso.slice(4)
    while (from <= to && index[from] < normalizedFrom) from += 1
  }
  if (toIso) {
    const normalizedTo = baseYearPrefix + toIso.slice(4)
    while (to >= from && index[to] > normalizedTo) to -= 1
  }
  if (from > to) {
    // Filter eliminated everything — return collapsed range (caller checks
    // `from > to` to short-circuit aggregation rather than fall back to full).
    return { from: index.length, to: -1 }
  }
  return { from, to }
}
