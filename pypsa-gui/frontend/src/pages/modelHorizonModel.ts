// Pure derivations for the Model Horizon page. No React, no network calls —
// everything here is a function of the `GET /api/network/snapshots` payload
// plus the solver config, so it can be tested without rendering the 1,489-line
// page. Same split as `topologyLayoutStore.ts` for TopologyCanvas.

/** One row of `SnapshotInfo.weightings`, i.e. `df_to_json(n.snapshot_weightings)`. */
export type WeightingRow = Record<string, unknown>

/**
 * The key that identifies one snapshot-weighting row to
 * `PATCH /api/network/snapshots/weightings`.
 *
 * Flat networks: the index is named `snapshot`, so the row carries an ISO
 * string under that key and the bare ISO is unambiguous.
 *
 * Multi-period networks: the index is a MultiIndex named `period` / `timestep`,
 * so there is NO `snapshot` key. The bare timestep is ambiguous — the backend
 * registers it once per period and last-write-wins, meaning a bare key always
 * resolves to the LAST period. Emit the period-qualified `period|iso` form,
 * which the backend documents as canonical for multi-period clients.
 */
export function snapshotWeightKey(
  row: WeightingRow,
  isMultiPeriod: boolean,
  fallbackIso: string,
): string {
  if (isMultiPeriod) {
    const period = row.period
    const timestep = row.timestep
    if (period != null && timestep != null) {
      return `${String(period)}|${String(timestep)}`
    }
  }
  const flat = row.snapshot ?? row.name
  if (flat != null) return String(flat)
  return fallbackIso
}

/** A weightings-table row, ready to render. */
export interface WeightingTableRow {
  /** PATCH key and React key. Distinct per (period, timestep). */
  key: string
  /** Investment period as a display string, or null on flat networks. */
  period: string | null
  /** Timestamp shown in the Snapshot column. */
  iso: string
  objective: number
  generators: number
  stores: number
}

function weight(row: WeightingRow, col: string): number {
  const v = row[col]
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : 1
}

/**
 * Turn one page of `SnapshotInfo.weightings` into render-ready rows.
 *
 * `allSnapshots` is the full `SnapshotInfo.snapshots` array and `pageStart` the
 * index of the page's first row within it — together they supply the positional
 * fallback for rows that carry no timestamp of their own.
 */
export function buildWeightingRows(
  pageRows: WeightingRow[],
  allSnapshots: string[],
  isMultiPeriod: boolean,
  pageStart: number,
): WeightingTableRow[] {
  return pageRows.map((row, i) => {
    const fallbackIso = String(allSnapshots[pageStart + i] ?? '')
    const iso = String(row.timestep ?? row.snapshot ?? row.name ?? fallbackIso)
    const period = isMultiPeriod && row.period != null ? String(row.period) : null
    return {
      key: snapshotWeightKey(row, isMultiPeriod, fallbackIso),
      period,
      iso,
      objective: weight(row, 'objective'),
      generators: weight(row, 'generators'),
      stores: weight(row, 'stores'),
    }
  })
}

/**
 * The frequency options the snapshot constructor offers, and the labels the
 * Resolution stat card renders. Kept here rather than in the page so both the
 * card and the `<select>` read one list.
 */
export const FREQ_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'h',   label: 'Hourly (h)' },
  { value: '3h',  label: '3-hourly' },
  { value: '6h',  label: '6-hourly' },
  { value: 'D',   label: 'Daily (D)' },
  { value: 'W',   label: 'Weekly (W)' },
  { value: 'MS',  label: 'Monthly (MS)' },
]

/**
 * Human label for the resolution reported by `GET /snapshots`.
 *
 * Matching is case-insensitive because pandas has emitted both "h" and "H" for
 * hourly across versions. An alias we don't recognise passes through verbatim —
 * showing "17min" is honest; mapping it to "Hourly" is not. `null` means the
 * backend could not infer one, which is a real state (a two-snapshot network,
 * or a genuinely irregular index) and reads as "Irregular".
 */
export function resolutionLabel(freq: string | null | undefined): string {
  if (!freq) return 'Irregular'
  const hit = FREQ_OPTIONS.find(o => o.value.toLowerCase() === freq.toLowerCase())
  return hit ? hit.label : freq
}

/**
 * The `investment_period_weightings.objective` value that auto-discount will
 * write for one period at solve time.
 *
 * This MIRRORS `solver_service.py::_apply_modelling_assumptions` step 4 and
 * must stay in step with it. It exists so the period table can show the user
 * what the checkbox will do before they solve, rather than making them run a
 * solve to find out.
 *
 * The real rate uses the exact Fisher relation, not nominal − inflation, and
 * is clamped at -0.999 so `(1 + r)` stays positive under a pathological
 * inflation > nominal.
 */
export function pvFactor(args: {
  period: number
  refPeriod: number
  years: number
  discountRate: number
  inflationRate: number
}): number {
  const { period, refPeriod, years, discountRate, inflationRate } = args
  const nominal = Number.isFinite(discountRate) ? discountRate : 0
  const infl = Number.isFinite(inflationRate) ? inflationRate : 0
  let r = 1 + infl > 0 ? (1 + nominal) / (1 + infl) - 1 : nominal
  if (r <= -0.999) r = -0.999
  const pv = Math.pow(1 + r, -(period - refPeriod))
  return pv * (Number.isFinite(years) ? years : 1)
}

/**
 * The sub-label under the Snapshots stat card.
 *
 * Multi-period networks replicate ONE operational year under every investment
 * period, so the raw first/last timestep carries the BASE year and says nothing
 * about the horizon — a 2030/2040/2050 model read as "2024-01-01 → 2024-12-31".
 * Lead with the period span and reduce the operational window to MM-DD, which
 * is the part that actually varies. Same reasoning as the `toDisplay` remap in
 * `pages/results/asset/HorizonFilter.tsx`.
 */
export function horizonRangeLabel(
  snapshots: string[] | undefined,
  periods: Array<number | string> | undefined,
  isMultiPeriod: boolean,
): string {
  if (!snapshots || snapshots.length === 0) {
    return isMultiPeriod ? 'multi-period horizon' : 'flat horizon'
  }
  const first = snapshots[0]
  const last = snapshots[snapshots.length - 1]
  // Single pass, no intermediate array and no spread — callers may hand this
  // the small (2-3 element) investment-period list OR the full per-snapshot
  // parallel array (periods[i] = the period snapshots[i] belongs to), which
  // on a multi-decade hourly model can run into six figures. `Math.min(...x)`
  // / `Math.max(...x)` on an array that size throws `RangeError: Maximum
  // call stack size exceeded`; a loop has no such ceiling.
  let lo = Infinity
  let hi = -Infinity
  let sawFinite = false
  for (const p of periods ?? []) {
    const num = Number(p)
    if (!Number.isFinite(num)) continue
    sawFinite = true
    if (num < lo) lo = num
    if (num > hi) hi = num
  }
  if (!sawFinite) {
    return `${first.slice(0, 10)} → ${last.slice(0, 10)}`
  }
  const span = lo === hi ? `${lo}` : `${lo}…${hi}`
  // MM-DD only — the operational year is a base year, not a planning year.
  return `${span} × op. ${first.slice(5, 10)}→${last.slice(5, 10)}`
}
