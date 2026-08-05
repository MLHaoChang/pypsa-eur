import { createContext, useContext, type ReactNode } from 'react'

// ── Results horizon filter ───────────────────────────────────────────────────
// A simple [fromIdx, toIdx] slice into the snapshots array, propagated via
// context so the three Results tabs (Capacity Expansion / Dispatch / Load Flow)
// can apply the same filter without prop-drilling through every child chart.
//
// `null` for either bound means "no clamp" (use the natural beginning / end).
// Tab components convert to concrete indices using their fetched TSPayload.
// Helpers below shave the time-series and string-index aggregations.

/**
 * Write access to the horizon filter, for tabs that need to expose it inline
 * rather than send the user back up to the collapsed strip at the top of the
 * Results shell.
 *
 * Results.tsx owns the state; this is the only way to drive it, so there is
 * still exactly one filter for the whole panel and a tab-local control and
 * the shell control can never disagree. Every field is already translated
 * into the display year the shell shows (multi-period networks replicate one
 * operational year under every investment period), so a consumer binds
 * `fromInput` straight to a `datetime-local` input and calls `setFromInput`
 * with whatever the user typed.
 */
export interface ResultsFilterControls {
  /** "YYYY-MM-DDTHH:mm", ready for a datetime-local input. */
  fromInput: string
  toInput: string
  setFromInput: (value: string) => void
  setToInput: (value: string) => void
  /** The network's own span, same format — input min/max and Reset target. */
  firstSnap: string
  lastSnap: string
  /** Empty on a flat (single-period) network. */
  periods: Array<number | string>
  selectedPeriod: number | string | 'all'
  setSelectedPeriod: (period: number | string | 'all') => void
  /** True once the user has narrowed off the full horizon. */
  isFiltered: boolean
  /** Restore both bounds to the full simulation horizon. */
  reset: () => void
}

export interface ResultsFilter {
  fromIso: string | null
  toIso:   string | null
  /** Multi-period only. When set, results are sliced to rows where
   *  `periods[i] === selectedPeriod`. Null = "aggregated across all periods".
   *  Ignored on single-period (flat) snapshots — TS payloads don't have a
   *  `periods` array there. */
  selectedPeriod: number | string | null
  /** Absent when a tab is rendered outside the Results shell (tests, and any
   *  future embedding) — consumers must treat the inline control as optional. */
  controls?: ResultsFilterControls
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

/**
 * The view a Results tab opens on.
 *
 * `whole` means "change nothing" — the pre-existing behaviour, and what every
 * network at or below the threshold keeps.
 */
export type DefaultWindow =
  | { kind: 'whole' }
  | { kind: 'period'; period: number | string }
  | { kind: 'iso'; fromIso: string; toIso: string }

/**
 * One hourly year. The largest horizon that renders as a chart without
 * windowing, and the natural unit of this domain — below it, nothing changes.
 */
export const WINDOW_THRESHOLD = 8760

/** One month of hourly snapshots. */
export const DEFAULT_FLAT_WINDOW = 720

/**
 * Choose the opening window from the snapshot index alone.
 *
 * The threshold is tested against the TOTAL horizon BEFORE any structural
 * branch. Branching on multi-period first would narrow a 2-period x
 * 24-snapshot network to 24 rows — a regression on exactly the small models
 * where the point is that nothing changes, and it would alter the golden test
 * fixture's default view.
 *
 * Multi-period returns a PERIOD rather than ISO bounds because `resolveRange`
 * matches the parallel `periods` array natively, whereas ISO bounds on a
 * multi-period network match rows in every period at once — every period
 * replicates the same base operational year. See the comment at the `fromIso`
 * handling below, and CLAUDE.md's note on the Horizon filter's year remap.
 */
export function defaultWindow(
  snapshots: string[],
  periods: Array<number | string> | undefined,
): DefaultWindow {
  if (snapshots.length <= WINDOW_THRESHOLD) return { kind: 'whole' }

  if (periods && periods.length === snapshots.length) {
    const seen = new Set<number | string>(periods)
    const arr = [...seen]
    if (arr.length > 1) {
      const allNumeric = arr.every(p => typeof p === 'number')
      const sorted = allNumeric
        ? (arr as number[]).sort((a, b) => a - b)
        : arr.map(String).sort()
      return { kind: 'period', period: sorted[0] }
    }
  }

  const lastIdx = Math.min(DEFAULT_FLAT_WINDOW, snapshots.length) - 1
  return { kind: 'iso', fromIso: snapshots[0], toIso: snapshots[lastIdx] }
}
