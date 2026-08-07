// Shared helpers used by every Results sub-tab. Includes a tiny KPI card
// component used at the top of each tab's overview row, hence the .tsx ext.

import { useMemo, useState, type RefObject, type ReactNode, type CSSProperties } from 'react'
import {
  ResponsiveContainer, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, Legend,
} from 'recharts'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { useQuery } from '@tanstack/react-query'
import { isRenewableCarrier } from '../../utils/carriers'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { networkApi } from '../../api/network'

// ── Asset-group categorisation (mirrors TopologyCanvas / MapCanvas) ──────────
export type Group = 'Thermal' | 'Renewables' | 'Storage' | 'Load'

// `isRenewableCarrier` now lives in utils/carriers.ts (single null-safe source);
// re-exported so existing `import { isRenewableCarrier } from './shared'`
// consumers keep working unchanged.
export { isRenewableCarrier }
export function generatorGroup(carrier: string | undefined): 'Thermal' | 'Renewables' {
  return isRenewableCarrier(carrier ?? '') ? 'Renewables' : 'Thermal'
}

// Descending duration curve: sort values high→low, x = "% of hours" (0..100).
// `(i / max(n-1, 1)) * 100` puts the first point at 0% and the last at 100%;
// the `max(…, 1)` guards the single-element case. Shared by the price- and
// load-duration curves (CompareView builds its own from backend-pre-sorted
// arrays, so it doesn't use this).
export function durationCurvePoints(values: number[]): { pct: number; value: number }[] {
  const arr = [...values].sort((a, b) => b - a)
  const n = arr.length
  return arr.map((v, i) => ({ pct: (i / Math.max(n - 1, 1)) * 100, value: v }))
}

// ── Time-series payload returned by /api/results/* endpoints ─────────────────
export interface TSPayload {
  index: string[]
  columns: string[]
  data: number[][]
  // Multi-period runs only. Parallel to `index`: periods[i] is the investment
  // period that index[i]'s timestep belongs to. Always an integer in practice
  // (PyPSA's convention is year-as-int) but the backend falls back to string
  // if a user assigns non-int labels, hence the union type.
  // Absent on single-period (flat) snapshots — the backend omits the field.
  periods?: Array<number | string>
  // Present only on RANGED responses (services/serialization.py::slice_ts /
  // ts_payload) — a request that passed `from`/`to` query params. Absent
  // means this payload is the whole series, the pre-range shape unconverted
  // callers still receive.
  //
  // `complete` is true when the served window covers the whole series
  // (`from === 0 && to === total - 1`), false for a partial window.
  // `capped` is true when the server truncated the request because it
  // exceeded MAX_RESPONSE_VALUES.
  //
  // A consumer that renders a HORIZON TOTAL (sums every row to report an
  // MWh / € figure across the whole run) MUST check `!ts.range || ts.range
  // .complete` before treating `data` as the whole series — a windowed
  // payload silently summing only its chunk understates the total. Per-
  // snapshot consumers (a single row, a chart of the served window) don't
  // need this check.
  range?: { from: number; to: number; total: number; complete: boolean; capped: boolean }
}

// True when any payload's `range` reports the server ACTUALLY TRUNCATED the
// response — i.e. `capped: true`. `capped` alone is the signal.
//
// `complete` is NOT a truncation signal — do not add it back in. Re-review
// finding (results-tabs-window final review, second wave): `services/
// serialization.py::slice_ts` computes `complete = (lo == 0 and hi == total -
// 1)`, which is `false` for EVERY window that isn't the whole horizon —
// including the plan's own default views (`filterContext.tsx`'s first-period
// multi-period default, and its rows-0-719 flat->8760 default). Those are
// ordinary, correctly-served requests: the user (or the default window) asked
// for less than the whole horizon and got exactly that. The original
// predicate here (`capped || complete === false`) treated "this is a window"
// as "this was truncated" and fired the banner below on every multi-period
// and every large flat network, permanently — the exact false-positive
// pattern this whole plan exists to avoid, and worse than silent: wrong copy
// on screen, and a warning users learn to ignore within a day, which erases
// its value for the case (`capped: true`) it exists to catch.
//
// This is the counterpart to `AggregatedOverview.isPartialPayload`, not a
// duplicate of it:
//   • `isPartialPayload` (AggregatedOverview.tsx) guards a tab that must
//     NEVER receive a ranged payload at all — ANY `range` key there is
//     already a bug, because that tab reports whole-horizon totals. It
//     correctly uses `!complete` for that purpose: on that tab, ANY window
//     (not just a capped one) is the bug.
//   • `isTruncatedPayload` is for the six WINDOWING tabs (Dispatch,
//     Curtailment, LostLoadTab, LoadFlow, Prices, StorageCycling), which
//     legitimately request ranged payloads. A `range` key — and `complete:
//     false` alongside it — is expected and fine there. Only `capped: true`
//     means the server gave back FEWER rows than the window itself asked
//     for, which is the one case worth surfacing.
// Kept in this one shared spot (not inlined per-tab) so the six call sites
// can't drift, and so it's unit-testable on its own — see shared.test.ts.
export function isTruncatedPayload(
  payloads: Array<TSPayload | null | undefined>,
): boolean {
  return payloads.some(p => p?.range?.capped === true)
}

// Drop-in warning banner for the six windowing tabs — renders nothing unless
// `isTruncatedPayload(payloads)` is true (i.e. some payload was `capped`).
// Centralised alongside the predicate so the banner copy (and its tone)
// can't drift per tab either. Wording mirrors AggregatedOverview's
// partial-payload message: plain-language statement of what happened,
// phrased as a fact about the data rather than an alarm — and specifically
// describes the SERVER returning fewer rows than the WINDOW requested, not
// "you are viewing a window" (viewing a window is normal and must not be
// described as a problem; being served less than the window asked for is).
export function WindowCapBanner(
  { payloads }: { payloads: Array<TSPayload | null | undefined> },
) {
  if (!isTruncatedPayload(payloads)) return null
  return (
    <div className="rounded border border-warn/40 bg-warn/5 px-3 py-2 text-[11px] text-warn">
      <span className="font-semibold">Results are truncated.</span>{' '}
      The selected window was too large for the server to return in full, so
      it sent back fewer snapshots than the window covers — the totals and
      charts on this tab reflect only part of it. Narrow the Horizon filter
      further, or reduce the number of assets in scope, to see the full
      window.
    </div>
  )
}

// True when the payload is multi-period (server emitted a parallel periods
// array). Single-period payloads omit the field; treat them as one big period.
export function isMultiPeriod(ts: TSPayload | null | undefined): boolean {
  return !!(ts && Array.isArray(ts.periods) && ts.periods.length > 0)
}

// Sorted unique periods from a multi-period payload. Empty list otherwise.
// Numeric periods sort numerically; strings fall back to lex order.
export function listPeriods(ts: TSPayload | null | undefined): Array<number | string> {
  if (!isMultiPeriod(ts)) return []
  const seen = new Set<number | string>()
  for (const p of ts!.periods!) seen.add(p)
  const arr = [...seen]
  const allNumeric = arr.every(p => typeof p === 'number')
  return allNumeric
    ? (arr as number[]).sort((a, b) => a - b)
    : arr.map(String).sort()
}

export function aggregateTS(
  ts: TSPayload | null | undefined,
  pickCols: Set<string>,
  rowRange?: { from: number; to: number },
):
  Array<{ snapshot: string; value: number }> | null
{
  if (!ts || ts.data.length === 0 || ts.columns.length === 0) return null
  const idxs = ts.columns
    .map((c, i) => ({ c, i }))
    .filter(o => pickCols.has(o.c))
    .map(o => o.i)
  if (idxs.length === 0) return null
  // Inclusive [from, to] slice — defaults to the full series. Out-of-range
  // values clamp; if `from > to` we return an empty array rather than
  // silently flipping bounds.
  const rFrom = Math.max(0, Math.min(rowRange?.from ?? 0, ts.data.length - 1))
  const rTo   = Math.max(0, Math.min(rowRange?.to ?? ts.data.length - 1, ts.data.length - 1))
  if (rFrom > rTo) return []
  const out: Array<{ snapshot: string; value: number }> = []
  for (let row = rFrom; row <= rTo; row++) {
    const r = ts.data[row]
    let s = 0
    for (const i of idxs) {
      const v = r[i]
      if (typeof v === 'number' && Number.isFinite(v)) s += v
    }
    out.push({ snapshot: ts.index[row], value: s })
  }
  return out
}

// ── Weighting helpers (snapshot_weightings + investment_period_weightings) ──
// Extensive metrics (energy MWh, cost €, emissions tCO2) on representative-week
// or multi-period runs need scaling so the displayed total reads as "annual"
// rather than "solved-hours only". PyPSA's own n.statistics() applies these
// weightings internally for the OPEX/CAPEX KPIs — the frontend hand-summed
// energy totals (kpis.thermal = Σ p_t with no weight) drift by the same factor.
//
// For a representative week with snapshot_weightings.generators = 52.14, the
// raw sum is 52× too small. Same for multi-period with years=10 on a period.

// Per-snapshot weight (`generators` column from snapshot_weightings) keyed by
// the snapshot ISO. Falls back to 1.0 when the API hasn't loaded yet.
export interface SnapshotWeightRow {
  // Single-period (flat) networks emit `snapshot`; multi-period (MultiIndex)
  // networks emit `period` + `timestep` instead. Both flavours are accepted.
  snapshot?: string
  timestep?: string
  period?: number | string
  generators?: number
  objective?: number
  stores?: number
}
export interface InvestmentPeriodWeightRow {
  period?: number | string
  years?: number
  objective?: number
}
export interface WeightCtx {
  /** snapshots from /api/network/snapshots — needed to map index position to
   *  the matching weights row. */
  snapshots?: string[]
  /** Parallel `periods` array on multi-period; absent on flat. */
  snapshotPeriods?: Array<number | string>
  /** Per-snapshot weights (rows aligned to `snapshots`). Pulled from
   *  /network/snapshots `.weightings`. */
  snapshotWeights?: SnapshotWeightRow[]
  /** Per-period weights (rows keyed by `period`). Pulled from
   *  /network/investment_periods `.weightings`. */
  periodWeights?: InvestmentPeriodWeightRow[]
}

// Assemble the WeightCtx every results tab needs: fetch /network/snapshots +
// /network/investment_periods (project-keyed) and fold them — together with a
// reference TS payload (the tab's primary result series) — into the standard
// WeightCtx shape. Previously this exact recipe (including the fragile
// `as unknown as` casts) was copy-pasted across Dispatch / Curtailment /
// LostLoadTab with per-tab var-name drift. Pass the already-resolved `refTs`
// (each tab picks its own primary series / fallback chain). Returns the
// weightCtx plus the derived `refIndex` / `refPeriods` (timeline for
// resolveRange) so a tab can destructure only what it uses.
export function useWeightCtx(refTs: TSPayload | null): {
  weightCtx: WeightCtx
  refIndex: string[]
  refPeriods: Array<number | string> | undefined
} {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: snap } = useQuery({
    queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots,
  })
  const { data: inv } = useQuery({
    queryKey: nk(currentProject, 'investmentPeriods'), queryFn: networkApi.getInvestmentPeriods,
  })
  const weightCtx: WeightCtx = useMemo(() => ({
    snapshots: snap?.snapshots ?? refTs?.index,
    snapshotPeriods: refTs?.periods ?? snap?.periods,
    snapshotWeights: (snap?.weightings as unknown) as WeightCtx['snapshotWeights'],
    periodWeights: inv?.weightings as unknown as WeightCtx['periodWeights'],
  }), [snap, inv, refTs])
  const refIndex: string[] = refTs?.index ?? snap?.snapshots ?? []
  const refPeriods = refTs?.periods ?? snap?.periods
  return { weightCtx, refIndex, refPeriods }
}

// Period equality for the weight-row lookup below. Loose (string-coerced)
// comparison because one side comes from a TSPayload's `periods` array
// (usually numbers) and the other from a weightings row (sometimes read
// back off JSON as the same numeric type, but not guaranteed) — matches the
// template-literal key StorageCycling.tsx builds (`${period}|${timestep}`),
// which coerces both sides to string implicitly.
function _samePeriod(
  a: number | string | null | undefined,
  b: number | string | null | undefined,
): boolean {
  if (a == null || b == null) return a == null && b == null
  return String(a) === String(b)
}

// ── Weight-row index (the fallback lookup, made O(1)) ───────────────────────
// The fallback below used to be a linear `sw.find(...)`. That is O(n) per row
// and the positional fast path MISSES on essentially every row of a windowed
// multi-period payload (`sw[0]` is period 1's row; `iso`/`period` belong to
// period 3), so the whole scan ran once per payload row — O(n²) overall.
// Measured on the real shape (8,760-row window against a 26,280-row weightings
// array, 12 columns): 5,853 ms for a SINGLE `weightedSum` call, versus 6 ms
// for the whole 26,280-row horizon. The app froze 17–33 s per period switch.
//
// The index is built once per `sw` ARRAY and memoised in a module-level
// WeakMap, so the cost is amortised across every call site sharing the same
// `snapshotWeights` reference (which they all do — `useWeightCtx` hands out
// `snap.weightings` unchanged) and the entry is collected with the array.
//
// Key namespaces are disjoint so the two match kinds cannot alias each other:
//   snapshot row              ->  "s" NUL <snapshot>
//   timestep row (+ period)   ->  "t" NUL <period>    NUL <timestep>
//   timestep row (no period)  ->  "t" NUL <NO_PERIOD> NUL <timestep>
// The U+0000 separator cannot occur in an ISO stamp or a period label, so
// the two namespaces can never alias. This preserves rule 3 of the semantics
// below: a `timestep` row that carries a period is NOT reachable by a bare
// iso, matching `_samePeriod`'s refusal to pair a null period with a non-null
// one. NO_PERIOD is a control character rather than "" so a (pathological)
// row whose period really IS the empty string still keys apart from a row
// with no period at all — `_samePeriod` treats those two as different.
const NO_PERIOD = '\u0001'
const _weightIndexCache = new WeakMap<SnapshotWeightRow[], Map<string, SnapshotWeightRow>>()

function _snapKey(iso: string): string { return `s\u0000${iso}` }
function _stepKey(period: number | string | null | undefined, iso: string): string {
  return `t\u0000${period == null ? NO_PERIOD : String(period)}\u0000${iso}`
}

function _weightIndex(sw: SnapshotWeightRow[]): Map<string, SnapshotWeightRow> {
  const cached = _weightIndexCache.get(sw)
  if (cached) return cached
  const map = new Map<string, SnapshotWeightRow>()
  // Forward walk with FIRST-WINS so duplicate keys resolve exactly the way the
  // `.find()` this replaced did (it returned the first matching row).
  for (const row of sw) {
    // Indexed under BOTH kinds when a row carries both fields — the old `||`
    // predicate matched on either, so restricting to one would lose matches.
    if (row.snapshot != null) {
      const k = _snapKey(row.snapshot)
      if (!map.has(k)) map.set(k, row)
    }
    if (row.timestep != null) {
      const k = _stepKey(row.period, row.timestep)
      if (!map.has(k)) map.set(k, row)
    }
    // Rows with neither field are unreachable by key; they stay on the
    // positional-only path, same as before.
  }
  _weightIndexCache.set(sw, map)
  return map
}

// Single-row weighting lookup. Multi-period emits `timestep` instead of
// `snapshot` so we match on both. Falls back to positional alignment when
// the row's key field can't be matched — both `n.snapshots` and
// `n.snapshot_weightings` come from the same MultiIndex so positional
// alignment is exact when lengths agree (the common case).
//
// `period` MUST be threaded through and checked alongside `timestep` on
// multi-period payloads: PyPSA replicates one operational year across every
// investment period, so `timestep` (`.isoformat()`) alone is AMBIGUOUS — the
// same ISO string appears once per period. Without the period check, a
// WINDOWED payload starting at period 2 has `i=0` (the window-relative row
// index) collide positionally against `sw[0]`, which is period 1's weight
// row — same iso, wrong period, wrong weight, and the keyed fallback below
// is never even reached because the positional branch matched first.
// `StorageCycling.tsx`'s `wAt` keys on the same `period|timestep` pair —
// match its approach so the two stop disagreeing.
//
// Semantics preserved from the `.find()` version:
//   1. The positional fast path runs FIRST and still wins when it matches.
//   2. First-wins on duplicate keys (see `_weightIndex`).
//   3. Precedence is `snapshot` then `(period|timestep)` — the `||` order of
//      the old predicate.
//   4. Rows with neither key field keep the positional-only path.
function _snapshotWeightRow(
  sw: SnapshotWeightRow[],
  i: number,
  iso: string,
  period: number | string | null | undefined,
): SnapshotWeightRow | undefined {
  const r = sw[i]
  if (r && ((r.snapshot && r.snapshot === iso)
          || (r.timestep && r.timestep === iso && _samePeriod(r.period, period))
          || (!r.snapshot && !r.timestep))) {
    // Positional match: name field absent, or present and matching BOTH the
    // timestep AND (when this is a multi-period row) its period.
    return r
  }
  // Keyed fallback (was a linear scan) — same (timestep, period) pairing and
  // the same snapshot-before-timestep precedence as the positional branch.
  const idx = _weightIndex(sw)
  return idx.get(_snapKey(iso)) ?? idx.get(_stepKey(period, iso))
}

// True if any non-trivial weight (≠ 1.0) is present. UX uses this to decide
// whether to show the "× scaling" tooltip on KPI cards.
export function hasScaling(w: WeightCtx | undefined): boolean {
  if (!w) return false
  const sw = w.snapshotWeights ?? []
  for (const r of sw) {
    if (Math.abs((r.generators ?? 1) - 1) > 1e-9) return true
    if (Math.abs((r.objective  ?? 1) - 1) > 1e-9) return true
  }
  const pw = w.periodWeights ?? []
  for (const r of pw) {
    if (Math.abs((r.years     ?? 1) - 1) > 1e-9) return true
    if (Math.abs((r.objective ?? 1) - 1) > 1e-9) return true
  }
  return false
}

// Effective weight for index position `i` of a TSPayload, combining the
// snapshot's own weight (column = which?) and the period's `years` weight.
// `column` choice:
//   • 'generators' — for energy (MWh) totals. PyPSA scales energy balance by
//     this weight when summing dispatch.
//   • 'objective'  — for cost (€) totals. Matches n.statistics()'s €/yr scaling.
//
// EXPORTED so there is exactly ONE weight-resolution path in the Results tabs.
// `Dispatch.tsx`'s per-port link-flow loop used to index `ctx.snapshotWeights`
// by the payload row instead, with no snapshot/period check at all — which is
// wrong on every windowed payload (the weights array is full-horizon; the row
// index is window-relative) and is exactly the drift this export prevents.
// Callers pass the payload's own `index`/row; the period is read off
// `ctx.snapshotPeriods[i]`, so the ctx handed in must have `snapshotPeriods`
// parallel to the payload being summed.
export function effectiveWeightAt(
  ctx: WeightCtx | undefined,
  index: string[],
  i: number,
  column: 'generators' | 'objective' | 'stores',
  iso: string,
): number {
  if (!ctx) return 1
  // Computed BEFORE the snapshot-weight lookup (not after, as before) so it
  // can be threaded into `_snapshotWeightRow` — see that function's doc
  // comment for why the period is required to disambiguate `timestep` on
  // multi-period payloads.
  const periodVal = ctx.snapshotPeriods?.[i]
  const sw = ctx.snapshotWeights
  let w = 1
  if (sw && sw.length > 0) {
    const row = _snapshotWeightRow(sw, i, iso, periodVal)
    if (row) {
      const v = row[column]
      if (typeof v === 'number' && Number.isFinite(v)) w *= v
    }
  }
  if (periodVal != null && ctx.periodWeights) {
    const pr = ctx.periodWeights.find(r => r.period === periodVal)
    if (pr) {
      // Use `years` — the multiplier that converts one period's solved
      // hours into the equivalent annual figure across the period's
      // lifespan. `objective` is the cost-side equivalent and would
      // double-count when applied here (n.statistics() already uses it).
      const v = pr.years
      if (typeof v === 'number' && Number.isFinite(v)) w *= v
    }
  }
  // suppress unused-arg warning when called with `index` from callers but
  // looked up via positional row — keep the param for future flexibility.
  void index
  return w
}

/** Sum extensive series with per-snapshot + per-period weights applied.
 *  Returns a single scalar (Σ p_t × w_t) — the annualised total. */
export function weightedSum(
  ts: TSPayload | null | undefined,
  pickCols: Set<string>,
  ctx?: WeightCtx,
  rowRange?: { from: number; to: number },
  column: 'generators' | 'objective' | 'stores' = 'generators',
): number | null {
  if (!ts || ts.data.length === 0 || ts.columns.length === 0) return null
  const idxs = ts.columns.flatMap((c, i) => pickCols.has(c) ? [i] : [])
  if (idxs.length === 0) return null
  const rFrom = Math.max(0, Math.min(rowRange?.from ?? 0, ts.data.length - 1))
  const rTo   = Math.max(0, Math.min(rowRange?.to ?? ts.data.length - 1, ts.data.length - 1))
  if (rFrom > rTo) return 0
  let total = 0
  for (let row = rFrom; row <= rTo; row++) {
    const r = ts.data[row]
    let s = 0
    for (const i of idxs) {
      const v = r[i]
      if (typeof v === 'number' && Number.isFinite(v)) s += v
    }
    const w = effectiveWeightAt(ctx, ts.index, row, column, ts.index[row])
    total += s * w
  }
  return total
}

/** Weighted positive-only / negative-only sum — for split storage discharge
 *  vs charge KPIs. */
export function weightedSumSplit(
  ts: TSPayload | null | undefined,
  pickCols: Set<string>,
  ctx: WeightCtx | undefined,
  rowRange?: { from: number; to: number },
  column: 'generators' | 'objective' | 'stores' = 'generators',
): { positive: number | null; negative: number | null } {
  if (!ts || ts.data.length === 0 || ts.columns.length === 0)
    return { positive: null, negative: null }
  const idxs = ts.columns.flatMap((c, i) => pickCols.has(c) ? [i] : [])
  if (idxs.length === 0) return { positive: null, negative: null }
  const rFrom = Math.max(0, Math.min(rowRange?.from ?? 0, ts.data.length - 1))
  const rTo   = Math.max(0, Math.min(rowRange?.to ?? ts.data.length - 1, ts.data.length - 1))
  if (rFrom > rTo) return { positive: 0, negative: 0 }
  let pos = 0, neg = 0
  for (let row = rFrom; row <= rTo; row++) {
    const r = ts.data[row]
    let s = 0
    for (const i of idxs) {
      const v = r[i]
      if (typeof v === 'number' && Number.isFinite(v)) s += v
    }
    const w = effectiveWeightAt(ctx, ts.index, row, column, ts.index[row])
    if (s > 0) pos += s * w
    else       neg += -s * w
  }
  return { positive: pos, negative: neg }
}

/** Per-period weighted sum, returning one number per period. Used by the
 *  Aggregated overview's bar charts where each period gets its own bar
 *  (`generation per carrier per year`, `storage charged per period`, etc.).
 *
 *  Periods are inferred from `ts.periods`; flat snapshots return a single
 *  `null` period entry with the horizon total. `pickCols` filters columns
 *  before summing. */
export function perPeriodWeightedSum(
  ts: TSPayload | null | undefined,
  pickCols: Set<string>,
  ctx?: WeightCtx,
  column: 'generators' | 'objective' | 'stores' = 'generators',
): Array<{ period: number | string | null; value: number }> {
  if (!ts || ts.data.length === 0 || ts.columns.length === 0) return []
  const idxs = ts.columns.flatMap((c, i) => pickCols.has(c) ? [i] : [])
  if (idxs.length === 0) return []
  // Group rows by period. Flat (no periods array) collapses to a single null.
  const groups = new Map<number | string | null, number[]>()
  for (let row = 0; row < ts.data.length; row++) {
    const p = ts.periods?.[row] ?? null
    if (!groups.has(p)) groups.set(p, [])
    groups.get(p)!.push(row)
  }
  const out: Array<{ period: number | string | null; value: number }> = []
  for (const [p, rows] of groups) {
    let total = 0
    for (const row of rows) {
      const r = ts.data[row]
      let s = 0
      for (const i of idxs) {
        const v = r[i]
        if (typeof v === 'number' && Number.isFinite(v)) s += v
      }
      const w = effectiveWeightAt(ctx, ts.index, row, column, ts.index[row])
      total += s * w
    }
    out.push({ period: p, value: total })
  }
  // Numeric periods sort numerically; strings fall back to lex order;
  // null (single-period) stays at index 0.
  out.sort((a, b) => {
    if (a.period == null) return -1
    if (b.period == null) return 1
    if (typeof a.period === 'number' && typeof b.period === 'number') {
      return a.period - b.period
    }
    return String(a.period).localeCompare(String(b.period))
  })
  return out
}

/** Per-period weighted split (positive / negative) — for storage charge
 *  vs discharge bars per period. */
export function perPeriodWeightedSumSplit(
  ts: TSPayload | null | undefined,
  pickCols: Set<string>,
  ctx?: WeightCtx,
  column: 'generators' | 'objective' | 'stores' = 'generators',
): Array<{ period: number | string | null; positive: number; negative: number }> {
  if (!ts || ts.data.length === 0 || ts.columns.length === 0) return []
  const idxs = ts.columns.flatMap((c, i) => pickCols.has(c) ? [i] : [])
  if (idxs.length === 0) return []
  const groups = new Map<number | string | null, number[]>()
  for (let row = 0; row < ts.data.length; row++) {
    const p = ts.periods?.[row] ?? null
    if (!groups.has(p)) groups.set(p, [])
    groups.get(p)!.push(row)
  }
  const out: Array<{ period: number | string | null; positive: number; negative: number }> = []
  for (const [p, rows] of groups) {
    let pos = 0, neg = 0
    for (const row of rows) {
      const r = ts.data[row]
      let s = 0
      for (const i of idxs) {
        const v = r[i]
        if (typeof v === 'number' && Number.isFinite(v)) s += v
      }
      const w = effectiveWeightAt(ctx, ts.index, row, column, ts.index[row])
      if (s > 0) pos += s * w
      else       neg += -s * w
    }
    out.push({ period: p, positive: pos, negative: neg })
  }
  out.sort((a, b) => {
    if (a.period == null) return -1
    if (b.period == null) return 1
    if (typeof a.period === 'number' && typeof b.period === 'number') {
      return a.period - b.period
    }
    return String(a.period).localeCompare(String(b.period))
  })
  return out
}

/** Per-period × per-column matrix. Returns one row per period, with column
 *  totals keyed by the column name. Used by the carrier-stacked bar chart
 *  (Σ over carrier per year). */
export function perPeriodPerColumnWeightedSum(
  ts: TSPayload | null | undefined,
  pickCols: Set<string>,
  ctx?: WeightCtx,
  column: 'generators' | 'objective' | 'stores' = 'generators',
): Array<{ period: number | string | null } & Record<string, number | string | null>> {
  if (!ts || ts.data.length === 0 || ts.columns.length === 0) return []
  const colIdxs = ts.columns.flatMap((c, i) => pickCols.has(c) ? [{ c, i }] : [])
  if (colIdxs.length === 0) return []
  const groups = new Map<number | string | null, number[]>()
  for (let row = 0; row < ts.data.length; row++) {
    const p = ts.periods?.[row] ?? null
    if (!groups.has(p)) groups.set(p, [])
    groups.get(p)!.push(row)
  }
  const out: Array<{ period: number | string | null } & Record<string, number | string | null>> = []
  for (const [p, rows] of groups) {
    const totals = new Map<string, number>()
    for (const row of rows) {
      const w = effectiveWeightAt(ctx, ts.index, row, column, ts.index[row])
      for (const { c, i } of colIdxs) {
        const v = ts.data[row][i]
        if (typeof v === 'number' && Number.isFinite(v)) {
          totals.set(c, (totals.get(c) ?? 0) + v * w)
        }
      }
    }
    const entry: { period: number | string | null } & Record<string, number | string | null> = { period: p }
    for (const [c, sum] of totals) entry[c] = sum
    out.push(entry)
  }
  out.sort((a, b) => {
    if (a.period == null) return -1
    if (b.period == null) return 1
    if (typeof a.period === 'number' && typeof b.period === 'number') {
      return (a.period as number) - (b.period as number)
    }
    return String(a.period).localeCompare(String(b.period))
  })
  return out
}

/** Average snapshot scaling factor over the [from,to] slice — used to render
 *  a "× N" hint on KPI cards when weights aren't trivial. */
export function averageScaling(
  ctx: WeightCtx | undefined,
  index: string[],
  rowRange?: { from: number; to: number },
  column: 'generators' | 'objective' | 'stores' = 'generators',
): number {
  if (!ctx) return 1
  const rFrom = Math.max(0, rowRange?.from ?? 0)
  const rTo = Math.min(index.length - 1, rowRange?.to ?? index.length - 1)
  if (rFrom > rTo) return 1
  let total = 0, n = 0
  for (let i = rFrom; i <= rTo; i++) {
    total += effectiveWeightAt(ctx, index, i, column, index[i])
    n += 1
  }
  return n > 0 ? total / n : 1
}

// ── Number formatting ────────────────────────────────────────────────────────
// fmtEnergy expects MWh (PyPSA's natural unit when summing MW × hourly
// snapshots). It scales up to GWh / TWh once the magnitude crosses 1000 /
// 1e6 MWh respectively, and down to kWh for sub-MWh values. The previous code
// formatted MWh as if they were Wh, producing absurd labels (e.g. 1234 MWh
// as "1.23 kWh").
export function fmtEnergy(mwh: number | null | undefined, digits = 2): string {
  if (mwh == null || !Number.isFinite(mwh)) return '—'
  const a = Math.abs(mwh)
  if (a >= 1e6) return `${(mwh / 1e6).toFixed(digits)} TWh`
  if (a >= 1e3) return `${(mwh / 1e3).toFixed(digits)} GWh`
  if (a >= 1)   return `${mwh.toFixed(digits)} MWh`
  return `${(mwh * 1e3).toFixed(digits)} kWh`
}

export function fmtCurrency(eur: number | null | undefined, digits = 2): string {
  if (eur == null || !Number.isFinite(eur)) return '—'
  const a = Math.abs(eur)
  if (a >= 1e9) return `€${(eur / 1e9).toFixed(digits)} B`
  if (a >= 1e6) return `€${(eur / 1e6).toFixed(digits)} M`
  if (a >= 1e3) return `€${(eur / 1e3).toFixed(digits)} k`
  return `€${eur.toFixed(digits)}`
}

export function fmtPower(mw: number | null | undefined, digits = 2): string {
  if (mw == null || !Number.isFinite(mw)) return '—'
  const a = Math.abs(mw)
  if (a >= 1e3) return `${(mw / 1e3).toFixed(digits)} GW`
  return `${mw.toFixed(digits)} MW`
}

export function shortStamp(s: string): string {
  // Post-fix backend emits clean ISO timesteps even on multi-period
  // (period info lives in the parallel `periods` array, not embedded in the
  // string). Defensive fallback for legacy tuple-style strings like
  // "(2026, Timestamp('2026-05-11 00:00:00'))" — extract the embedded ISO so
  // old cached payloads or stale responses don't show garbage.
  if (s && s.length > 0 && s[0] === '(') {
    const m = s.match(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/)
    if (m) return m[0].replace('T', ' ').slice(5)
    return s
  }
  return s.slice(5, 16).replace('T', ' ')
}

// Combined formatter for multi-period contexts. When `period` is provided
// (the parallel periods[i] array), prefix the timestep so the user can
// distinguish "May 11" in 2026 from "May 11" in 2030. Falls back to the bare
// timestep for single-period or when the caller doesn't want the prefix.
export function stampWithPeriod(s: string, period?: number): string {
  const ts = shortStamp(s)
  if (period == null) return ts
  return `${period} · ${ts}`
}

// ── CSV export (RFC 4180 dialect, CRLF line endings, formula-injection-safe) ─
// Cells starting with `=`, `+`, `-`, `@`, tab, or carriage-return are prefixed
// with a single quote so Excel/Sheets don't interpret them as formulas (a
// classic CSV-injection vector when names like "=cmd|'/c calc'!A1" sneak in).
export function csvCell(v: unknown): string {
  if (v == null) return ''
  let s = String(v)
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s
  return /[,"\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
export function downloadCSV(filename: string, header: string[], rows: unknown[][]): void {
  const lines = [header.map(csvCell).join(',')]
  for (const r of rows) lines.push(r.map(csvCell).join(','))
  // CRLF per RFC 4180 — Excel on Windows expects \r\n, and \n alone produces
  // a single-line file when opened in some Excel versions.
  const blob = new Blob([lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

// ── SVG export ────────────────────────────────────────────────────────────
// Walks `container` for the first <svg> element (Recharts always emits a
// single SVG per ResponsiveContainer) and serialises it to a downloadable
// .svg blob. Computed styles aren't inlined — Recharts uses presentation
// attributes (fill, stroke, stroke-width) rather than CSS, so the file
// renders correctly outside the React DOM. Falls back to a toast-style
// console warning if no svg is found (e.g. user clicked before the chart
// mounted, or the container is an HTML heatmap).
export function downloadSVG(container: HTMLElement | null, filename: string): boolean {
  if (!container) return false
  const svg = container.querySelector('svg')
  if (!svg) return false
  const clone = svg.cloneNode(true) as SVGSVGElement
  // Recharts omits the namespace on the inner SVG when the parent <div> is
  // mounted — re-add so the file opens standalone in browsers / Illustrator.
  if (!clone.getAttribute('xmlns')) clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  if (!clone.getAttribute('xmlns:xlink')) clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  // Width/height — clone the rendered size so the SVG opens at the same
  // dimensions instead of collapsing to 0 (Recharts sizes via parent).
  const rect = svg.getBoundingClientRect()
  if (rect.width > 0 && rect.height > 0) {
    clone.setAttribute('width',  String(Math.round(rect.width)))
    clone.setAttribute('height', String(Math.round(rect.height)))
  }
  // Whitelist a background so downstream tools don't render transparent
  // checkerboards. Use white — matches every chart's on-screen background.
  if (!clone.querySelector('rect[data-bg]')) {
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    bg.setAttribute('width', '100%')
    bg.setAttribute('height', '100%')
    bg.setAttribute('fill', '#ffffff')
    bg.setAttribute('data-bg', 'true')
    clone.insertBefore(bg, clone.firstChild)
  }
  const serializer = new XMLSerializer()
  const source = '<?xml version="1.0" standalone="no"?>\r\n' + serializer.serializeToString(clone)
  const blob = new Blob([source], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
  return true
}

// ── Chart action buttons ───────────────────────────────────────────────────
// Inline pair of "SVG" + "CSV" links rendered in a chart section's header.
// Both are optional — pass only the handlers / refs you need:
//   • SVG: provide `chartRef` pointing at the wrapping <div>. When clicked,
//     the helper walks the div for the first <svg> (Recharts emits one per
//     ResponsiveContainer) and serialises it. No-op if the ref is null or
//     contains no svg (e.g. HTML-only heatmaps).
//   • CSV: provide `onExportCSV` — a click handler that drives downloadCSV()
//     with the chart's data. Caller owns the data shape so each chart can
//     emit a sensibly-named header row.
// Filename is shared between both formats (different extensions).
export interface ChartActionsProps {
  chartRef?: RefObject<HTMLDivElement | null>
  onExportCSV?: () => void
  filename: string  // base without extension; .svg / .csv appended by the helper
  className?: string
}
export function ChartActions({ chartRef, onExportCSV, filename, className }: ChartActionsProps) {
  return (
    <span className={`flex items-center gap-2 text-[11px] text-muted ${className ?? ''}`}>
      {chartRef && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            const ok = downloadSVG(chartRef.current, `${filename}.svg`)
            if (ok) toast.success('Exported SVG')
            else    toast.error('Chart not ready — try again after it renders')
          }}
          className="flex items-center gap-1 hover:text-accent"
          title="Export the chart as a standalone SVG file"
        >
          <Download size={11} /> SVG
        </button>
      )}
      {onExportCSV && (
        <button
          onClick={(e) => { e.stopPropagation(); onExportCSV() }}
          className="flex items-center gap-1 hover:text-accent"
          title="Export the underlying data as a CSV (RFC 4180, Excel-safe)"
        >
          <Download size={11} /> CSV
        </button>
      )}
    </span>
  )
}

// ── StatCard / KPI ───────────────────────────────────────────────────────────
// The design system's StatCard (Portfolio Optimizer handoff): a 9 px uppercase
// eyebrow, a large mono tnum value with an optional faint unit, an optional
// state-coloured delta, and an optional short sub-line. `accent` adds a 3 px
// red gradient left rail + tinted border for the headline metric of a strip.
//
// Back-compatible with the previous KPI signature ({label, value, hint}). The
// old `hint` — frequently a full-sentence formula explanation — now renders as
// the card's hover `title` so the strip stays visually calm; pass `sub` for a
// short always-visible line.
export function KPI({
  label, value, unit, hint, delta, deltaState, sub, accent,
}: {
  label: string
  value: string
  unit?: string
  hint?: string
  delta?: string
  deltaState?: 'ok' | 'warn' | 'err'
  sub?: string
  accent?: boolean
}) {
  const deltaColor = deltaState === 'ok' ? 'text-success'
    : deltaState === 'warn' ? 'text-warn'
    : deltaState === 'err' ? 'text-danger'
    : 'text-muted'
  return (
    <div
      title={hint}
      className={`relative overflow-hidden rounded-[10px] border bg-bg-2 px-4 py-3
        shadow-[0_1px_0_rgba(10,14,20,0.04)]
        ${accent ? 'border-accent/30' : 'border-border'}`}
    >
      {accent && (
        <span className="absolute left-0 inset-y-0 w-[3px] bg-gradient-to-b from-accent to-accent/70" />
      )}
      <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted">{label}</div>
      <div className="mt-1.5 flex items-baseline gap-1">
        <span className="font-mono text-[20px] font-semibold leading-none text-text tabular-nums">{value}</span>
        {unit && <span className="text-[12px] font-normal text-muted">{unit}</span>}
      </div>
      {delta && (
        <div className={`mt-1.5 inline-flex items-center gap-1 font-mono text-[10.5px] font-medium ${deltaColor}`}>
          {delta}
        </div>
      )}
      {sub && <div className="mt-1 text-[10px] text-muted">{sub}</div>}
    </div>
  )
}

// ── ChartCard ────────────────────────────────────────────────────────────────
// The design system's PageSection: a 10 px-radius card with a header bar in the
// subtle bg-2 surface (title + optional mono count + hint, right-aligned slot
// for toggles / export actions) and a padded body. Wrap every results graph in
// one so the Results panel reads as one consistent family of cards.
export function ChartCard({
  title, hint, count, right, children, className, bodyClassName,
}: {
  title: ReactNode
  hint?: ReactNode
  count?: number | string
  right?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section
      className={`rounded-[10px] border border-border bg-bg overflow-hidden
        shadow-[0_1px_0_rgba(10,14,20,0.04)] ${className ?? ''}`}
    >
      <header className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-border bg-bg-2">
        <div className="flex items-baseline gap-2 min-w-0">
          <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] truncate">{title}</h3>
          {count != null && <span className="font-mono text-[11px] text-muted shrink-0">{count}</span>}
          {hint && <span className="text-[11px] text-muted truncate">{hint}</span>}
        </div>
        {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
      </header>
      <div className={bodyClassName ?? 'p-4'}>{children}</div>
    </section>
  )
}

// ── Seg ──────────────────────────────────────────────────────────────────────
// The design system's `.seg` segmented control — a panel-tinted pill container
// with a white, softly-shadowed active segment. Used for the small per-chart
// view toggles (Absolute/%, LP dual/Merit order, …) so they stop looking like
// ad-hoc accent-filled button pairs.
export function Seg<T extends string>({
  value, onChange, options, title, className,
}: {
  value: T
  onChange: (v: T) => void
  options: Array<{ value: T; label: ReactNode; disabled?: boolean; title?: string }>
  title?: string
  className?: string
}) {
  return (
    <div
      title={title}
      className={`inline-flex items-center rounded-[7px] bg-panel border border-border p-0.5 text-[10px] ${className ?? ''}`}
    >
      {options.map(o => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          disabled={o.disabled}
          title={o.title}
          className={`px-2 h-[22px] rounded-[5px] font-medium transition-colors
            disabled:opacity-40 disabled:cursor-not-allowed
            ${value === o.value
              ? 'bg-bg text-text shadow-[0_1px_0_rgba(10,14,20,0.04)]'
              : 'text-muted hover:text-text'}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ── Recharts theme constants ─────────────────────────────────────────────────
// Spread these into the matching Recharts elements so every graph shares the
// design system's grid / axis / legend / tooltip treatment:
//   <CartesianGrid {...CHART_GRID} />
//   <XAxis dataKey="…" {...CHART_AXIS} />   <YAxis {...CHART_AXIS} />
//   <Legend {...CHART_LEGEND} />
//   <Tooltip {...CHART_TOOLTIP} />
// Colours reference the CSS custom properties added to index.css so they track
// light / dark automatically (SVG `stroke`/`fill` accept `var(--…)`).
export const CHART_GRID = {
  strokeDasharray: '2 4',
  stroke: 'var(--color-grid)',
  vertical: false,
} as const

export const CHART_AXIS = {
  // `tick` here is a set of SVG <text> presentation props (not CSSProperties).
  tick: { fontSize: 10, fill: 'var(--color-axis)' },
  stroke: 'var(--color-grid)',
  tickLine: false,
}

export const CHART_LEGEND = {
  wrapperStyle: { fontSize: 10.5, color: 'var(--color-legend)' } as CSSProperties,
  iconType: 'circle' as const,
  iconSize: 8,
}

// `cursor` is intentionally left at Recharts' per-chart-type default — a line
// for line/area charts, a translucent rect for bar charts — so it stays
// sensible across every graph. Only the tooltip box itself is themed.
export const CHART_TOOLTIP = {
  contentStyle: {
    background: 'var(--color-bg)',
    border: '1px solid var(--color-border)',
    borderRadius: 8,
    fontSize: 11,
    boxShadow: '0 6px 16px rgba(10,14,20,0.10), 0 2px 5px rgba(10,14,20,0.06)',
    fontFamily: 'var(--font-family-mono)',
    padding: '6px 9px',
  } as CSSProperties,
  labelStyle: { color: 'var(--color-muted)', fontSize: 10, marginBottom: 3 } as CSSProperties,
  itemStyle: { color: 'var(--color-text)', padding: '1px 0' } as CSSProperties,
}

// Y-axis label helper — the rotated unit caption every chart repeats. Returns
// the `label` prop object styled to the design's axis treatment.
export function yAxisLabel(value: string) {
  return { value, angle: -90, position: 'insideLeft' as const, offset: 12,
    style: { fontSize: 10, fill: 'var(--color-axis)' } as CSSProperties }
}

// ── Carrier colour palette ───────────────────────────────────────────────────
// Single source of truth for carrier → colour, shared by the dispatch stack and
// the aggregated bar charts (previously each file carried its own slightly
// divergent copy). Anchored on the design system's category hues —
// thermal=red, renewables=green, storage=purple, load=amber, carriers=h2/gas —
// with finer per-fuel variation inside each family.
const CARRIER_PALETTE: Record<string, string> = {
  coal:        '#57534e',
  lignite:     '#78716c',
  oil:         '#1f2937',
  gas:         '#0369a1',  // design --carrier-gas
  ocgt:        '#0369a1',
  ccgt:        '#0ea5e9',
  nuclear:     '#b91c1c',
  biomass:     '#65a30d',
  geothermal:  '#0d9488',
  hydro:       '#2563eb',
  ror:         '#38bdf8',
  wind:        '#16a34a',  // design --cat-renew
  onwind:      '#16a34a',
  offwind:     '#15803d',
  solar:       '#f59e0b',
  pv:          '#f59e0b',
  rooftop:     '#fbbf24',
  load:        '#d97706',  // design --cat-load
  battery:     '#7c3aed',  // design --cat-storage
  storage:     '#7c3aed',
  electricity: '#2563eb',
  heat:        '#ea580c',  // design --carrier-heat
  h2:          '#7c3aed',  // design --carrier-h2
  hydrogen:    '#7c3aed',
}
const FALLBACK_PALETTE = [
  '#6a7384', '#97a0ae', '#0369a1', '#16a34a',
  '#f59e0b', '#7c3aed', '#dc2626', '#0d9488',
]
// Resolve a carrier name to a stable colour. Exact match wins; then a substring
// match (so "offwind-ac" still reads as wind); finally a hashed fallback so any
// custom carrier still gets a deterministic colour.
export function colourForCarrier(carrier: string, hashSeed = 0): string {
  const k = (carrier ?? '').toLowerCase()
  if (CARRIER_PALETTE[k]) return CARRIER_PALETTE[k]
  for (const [match, hex] of Object.entries(CARRIER_PALETTE)) {
    if (k.includes(match)) return hex
  }
  return FALLBACK_PALETTE[Math.abs(hashSeed) % FALLBACK_PALETTE.length]
}

// ── Seasonal view mode ────────────────────────────────────────────────────────
// Shared scaffolding for the "averaged-week / averaged-month / full timeframe"
// toggle that lives on Dispatch, Prices, and LoadFlow. The Dispatch tab uses
// stacked areas (carrier-resolved); Prices / LoadFlow use plain line charts
// over a column list — `SeasonalLineCardGrid` below handles the latter case.

export type ResultsViewMode = 'weekly' | 'monthly' | 'timeframe'
export type Season = 'Winter' | 'Spring' | 'Summer' | 'Autumn'
export const SEASONS: Season[] = ['Winter', 'Spring', 'Summer', 'Autumn']
// Meteorological seasons (month → season).
export const SEASON_OF_MONTH: Record<number, Season> = {
  12: 'Winter', 1: 'Winter', 2: 'Winter',
  3: 'Spring', 4: 'Spring', 5: 'Spring',
  6: 'Summer', 7: 'Summer', 8: 'Summer',
  9: 'Autumn', 10: 'Autumn', 11: 'Autumn',
}
export const SEASON_ACCENT: Record<Season, string> = {
  Winter: '#0ea5e9', Spring: '#16a34a', Summer: '#f59e0b', Autumn: '#d97706',
}
// Min distinct days per season for each mode to be offered. Weekly needs at
// least one week in every season; monthly at least (most of) a month.
export const WEEKLY_MIN_DAYS = 7
export const MONTHLY_MIN_DAYS = 28
export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Parse a PyPSA ISO timestamp ("2026-03-15T14:00:00") into the fields the
// seasonal bucketing needs. Year/month/day/hour are plain string slices;
// day-of-week goes through Date.UTC so there's no timezone ambiguity. `dow`
// is remapped to 0=Mon … 6=Sun so the weekly bucket runs Mon→Sun.
export function parseSeasonStamp(
  iso: string,
): { month: number; dayOfMonth: number; hour: number; dow: number } | null {
  if (!iso || iso.length < 13) return null
  const y = +iso.slice(0, 4), m = +iso.slice(5, 7)
  const d = +iso.slice(8, 10), h = +iso.slice(11, 13)
  if (!Number.isFinite(y)
      || !Number.isFinite(m) || m < 1 || m > 12
      || !Number.isFinite(d) || d < 1 || d > 31
      || !Number.isFinite(h) || h < 0 || h > 23) return null
  const dow = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
  return { month: m, dayOfMonth: d, hour: h, dow }
}

// Hook that owns the view-mode state for a Results tab. Persists the user's
// pick to localStorage under `persistKey`, falls back gracefully when the
// active range doesn't span enough of each season for the chosen mode.
export function useSeasonalViewMode(
  persistKey: string,
  refIndex: string[],
  range: { from: number; to: number },
) {
  const loadInitial = (): ResultsViewMode => {
    try {
      const v = localStorage.getItem(persistKey)
      if (v === 'weekly' || v === 'monthly' || v === 'timeframe') return v
    } catch { /* ignore */ }
    return 'weekly'
  }
  const [viewMode, setViewModeRaw] = useState<ResultsViewMode>(loadInitial)
  const setViewMode = (m: ResultsViewMode) => {
    setViewModeRaw(m)
    try { localStorage.setItem(persistKey, m) } catch { /* ignore */ }
  }

  // Distinct calendar days present per season over [range.from, range.to].
  const seasonDays = useMemo(() => {
    const days: Record<Season, Set<string>> = {
      Winter: new Set(), Spring: new Set(), Summer: new Set(), Autumn: new Set(),
    }
    for (let i = range.from; i <= range.to && i < refIndex.length; i++) {
      const iso = refIndex[i]
      const p = parseSeasonStamp(iso)
      if (!p) continue
      days[SEASON_OF_MONTH[p.month]].add(iso.slice(0, 10))
    }
    return days
  }, [refIndex, range.from, range.to])
  const weeklyAvailable  = SEASONS.every(s => seasonDays[s].size >= WEEKLY_MIN_DAYS)
  const monthlyAvailable = SEASONS.every(s => seasonDays[s].size >= MONTHLY_MIN_DAYS)
  // The mode actually rendered: user pick when available, else fall back
  // weekly → monthly → timeframe.
  const effectiveMode: ResultsViewMode =
    (viewMode === 'weekly' && weeklyAvailable) ? 'weekly'
    : (viewMode === 'monthly' && monthlyAvailable) ? 'monthly'
    : (viewMode === 'timeframe') ? 'timeframe'
    : weeklyAvailable ? 'weekly'
    : monthlyAvailable ? 'monthly'
    : 'timeframe'

  return { viewMode, setViewMode, effectiveMode, weeklyAvailable, monthlyAvailable, seasonDays }
}

// Bucket arbitrary `{snapshot, [col]: number}` rows by season + hour-of-week
// (weekly) or hour-of-month (monthly), averaging every numeric column across
// matching buckets. Returns per-season row arrays with a `bucket` index and
// the same column names — feed straight into a LineChart.
//
// `rows` should already be filtered to the active result-filter range; this
// helper doesn't re-slice. Buckets with no data are left out (the per-row
// trailing-empty-trim mirrors Dispatch's seasonal aggregation so February etc.
// don't leave a 30-day empty tail on a monthly chart).
export function aggregateSeasonalRows(
  rows: ReadonlyArray<Record<string, unknown>>,
  cols: readonly string[],
  mode: 'weekly' | 'monthly',
): Record<Season, Array<Record<string, number>>> {
  const nBuckets = mode === 'weekly' ? 7 * 24 : 31 * 24
  const sums: Record<Season, Record<string, Float64Array>> = {
    Winter: {}, Spring: {}, Summer: {}, Autumn: {},
  }
  const counts: Record<Season, Record<string, Int32Array>> = {
    Winter: {}, Spring: {}, Summer: {}, Autumn: {},
  }
  for (const s of SEASONS) {
    for (const c of cols) {
      sums[s][c] = new Float64Array(nBuckets)
      counts[s][c] = new Int32Array(nBuckets)
    }
  }
  for (const r of rows) {
    const snap = r.snapshot
    if (typeof snap !== 'string') continue
    const p = parseSeasonStamp(snap)
    if (!p) continue
    const season = SEASON_OF_MONTH[p.month]
    const b = mode === 'weekly'
      ? p.dow * 24 + p.hour
      : (p.dayOfMonth - 1) * 24 + p.hour
    if (b < 0 || b >= nBuckets) continue
    for (const c of cols) {
      const v = r[c]
      if (typeof v !== 'number' || !Number.isFinite(v)) continue
      sums[season][c][b] += v
      counts[season][c][b] += 1
    }
  }
  const out: Record<Season, Array<Record<string, number>>> = {
    Winter: [], Spring: [], Summer: [], Autumn: [],
  }
  for (const season of SEASONS) {
    let lastNonEmpty = -1
    const seasonRows: Array<Record<string, number>> = []
    for (let b = 0; b < nBuckets; b++) {
      const row: Record<string, number> = { bucket: b }
      let any = false
      for (const c of cols) {
        if (counts[season][c][b] > 0) {
          row[c] = sums[season][c][b] / counts[season][c][b]
          any = true
        }
      }
      seasonRows.push(row)
      if (any) lastNonEmpty = b
    }
    out[season] = seasonRows.slice(0, lastNonEmpty + 1)
  }
  return out
}

// Tick / tooltip formatters for the bucket x-axis. Weekly buckets read as
// "Mon 14:00"; monthly as "Day 15 · 14:00". One tick per day (`interval=23`).
export function seasonalTickFmt(mode: 'weekly' | 'monthly') {
  return (b: number) =>
    mode === 'weekly'
      ? (WEEKDAYS[Math.floor(b / 24)] ?? '')
      : `D${Math.floor(b / 24) + 1}`
}
export function seasonalLabelFmt(mode: 'weekly' | 'monthly') {
  return (b: number) => {
    const hh = String(b % 24).padStart(2, '0')
    return mode === 'weekly'
      ? `${WEEKDAYS[Math.floor(b / 24)] ?? '?'} ${hh}:00`
      : `Day ${Math.floor(b / 24) + 1} · ${hh}:00`
  }
}

// Four-card grid of LineCharts, one per season. Each card averages the
// supplied `cols` over its season's matching hours-of-week / hours-of-month.
// Used by Prices, LoadFlow, and anywhere else that needs a "seasonal averaged
// profile" view of a multi-line time series.
export function SeasonalLineCardGrid({
  mode, rows, cols, colors, unit, valueFormatter, headerNote, height = 190,
  showLegendThreshold = 8,
}: {
  mode: 'weekly' | 'monthly'
  rows: ReadonlyArray<Record<string, unknown>>
  cols: readonly string[]
  colors: readonly string[]   // cycled if fewer than cols
  unit: string                // e.g. '€/MWh', 'MW', 'p.u.'
  valueFormatter?: (v: number) => string
  headerNote?: string         // appears in muted text next to "averaged week/month"
  height?: number
  showLegendThreshold?: number  // hide legend when cols.length > threshold
}) {
  const data = useMemo(
    () => aggregateSeasonalRows(rows, cols, mode),
    [rows, cols, mode],
  )
  const tickFmt = seasonalTickFmt(mode)
  const labelFmt = seasonalLabelFmt(mode)
  const fmtVal = valueFormatter ?? ((v: number) => `${v.toFixed(2)} ${unit}`)
  const showLegend = cols.length <= showLegendThreshold
  return (
    <div className="grid grid-cols-2 gap-4">
      {SEASONS.map(season => {
        const seasonRows = data[season] ?? []
        const present = cols.filter(c => seasonRows.some(r => r[c] != null))
        return (
          <div key={season}
            className="rounded-[10px] border border-border bg-bg overflow-hidden shadow-[0_1px_0_rgba(10,14,20,0.04)]">
            <header className="flex items-center gap-2 px-4 py-2.5 border-b border-border bg-bg-2">
              <span className="w-2 h-2 rounded-full shrink-0"
                style={{ background: SEASON_ACCENT[season] }} />
              <span className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">{season}</span>
              <span className="text-[10px] text-muted">
                averaged {mode === 'weekly' ? 'week' : 'month'} · {unit}
                {headerNote ? ` · ${headerNote}` : ''}
              </span>
            </header>
            <div className="px-2 py-2">
              {seasonRows.length === 0 || present.length === 0 ? (
                <div className="py-8 text-center text-[11.5px] text-muted italic">
                  No data in {season.toLowerCase()}.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={height}>
                  <LineChart data={seasonRows} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid {...CHART_GRID} />
                    <XAxis dataKey="bucket" {...CHART_AXIS}
                      interval={23} tickFormatter={tickFmt} />
                    <YAxis {...CHART_AXIS} label={yAxisLabel(unit)} />
                    <RTooltip {...CHART_TOOLTIP}
                      labelFormatter={labelFmt}
                      formatter={(v: number, n: string) => [fmtVal(v), n]} />
                    {showLegend && <Legend {...CHART_LEGEND} />}
                    {present.map((c, i) => (
                      <Line key={c} type="monotone" dataKey={c} name={c}
                        stroke={colors[i % colors.length]}
                        strokeWidth={1.4} dot={false} connectNulls />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

