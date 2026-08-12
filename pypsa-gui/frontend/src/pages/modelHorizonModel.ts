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

/**
 * Steps of the guided Model Horizon flow, split along PyPSA's two-level
 * `(period, timestep)` snapshot MultiIndex: `mode` / `years` / `economics`
 * are period-level and disappear entirely in single-period mode (there is no
 * investment period to configure); `window` / `sampling` / `weights` are
 * timestep-level and are always present.
 */
export type HorizonStepId = 'mode' | 'years' | 'economics' | 'window' | 'sampling' | 'weights'

/**
 * Which steps a project shows, in rail order. Multi-period gets all six;
 * single-period drops the three period-level steps and keeps the four
 * timestep-level ones.
 */
export function visibleSteps(isMultiPeriod: boolean): HorizonStepId[] {
  const timestepSteps: HorizonStepId[] = ['window', 'sampling', 'weights']
  return isMultiPeriod
    ? ['mode', 'years', 'economics', ...timestepSteps]
    : ['mode', ...timestepSteps]
}

/**
 * Whether the horizon is still PyPSA's default — a single "now" snapshot —
 * rather than something a user actually configured. Decides whether a
 * returning user meets the guided flow at step 1 or lands on the summary.
 *
 * A count of exactly 1 is unset, not a one-snapshot horizon: this mirrors
 * the heuristic `ModelHorizon.tsx` already uses (`snap.count <= 1`) to decide
 * whether to hydrate its constructor defaults. `undefined` (data not loaded
 * yet) also reads as unset, so the summary never flashes before data arrives.
 */
export function isHorizonUnset(snapshotCount: number | undefined): boolean {
  return snapshotCount === undefined || snapshotCount <= 1
}

/**
 * Plain data `stepSummary` needs to write its sentences, all of it already
 * computed by the page. Deliberately NOT the raw `SnapshotInfo` /
 * `SolverConfig` API payloads — that would couple this pure module to
 * response shapes and force every test to build a full fake payload just to
 * exercise a string. `rangeLabel` is `horizonRangeLabel`'s output, composed
 * here rather than re-derived.
 */
export interface HorizonSummaryContext {
  isMultiPeriod: boolean
  /** Investment period years, e.g. `[2030, 2040, 2050]`. Empty outside multi-period. */
  periods: number[]
  snapshotCount: number | undefined
  freq: string | null | undefined
  /** Pre-computed by `horizonRangeLabel` — compose it, don't re-derive it. */
  rangeLabel: string
  canSampleWeeks: boolean
  weightsAreDefault: boolean
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/**
 * `2030…2040…2050` — every configured year, not just the endpoints.
 *
 * A lo/hi span (`2030–2050`) would render `[2030,2035,2050]` and
 * `[2030,2040,2050]` identically despite them being genuinely different
 * configurations — PyPSA's discount weighting is sensitive to inter-period
 * spacing, not just count and range. Listing every year is the only
 * representation that can't alias two different period sets together.
 *
 * `periods` is an investment-period list (single/low-double-digit years),
 * never the large per-snapshot array `horizonRangeLabel` guards against, so
 * there is no call-stack-size concern here — a plain join is fine.
 *
 * Uses the same `…` separator as `horizonRangeLabel`'s span notation rather
 * than an en dash, so the two step summaries that both describe a range of
 * years read as one convention, not two.
 */
function periodList(periods: number[]): string {
  return periods.join('…')
}

/** `"26,280 snapshots"`, or a pending-load phrasing when the count hasn't arrived yet. */
function snapshotCountLabel(count: number | undefined): string {
  if (count === undefined) return 'snapshot count pending'
  return `${count.toLocaleString()} snapshot${count === 1 ? '' : 's'}`
}

/**
 * One-sentence summary of a step's current configuration, for the
 * summary-first landing view. A returning user reads this instead of
 * opening the step, so it must name the actual configured value (a count, a
 * year, a resolution) — never a placeholder or a bare label.
 */
export function stepSummary(step: HorizonStepId, ctx: HorizonSummaryContext): string {
  switch (step) {
    case 'mode':
      return ctx.isMultiPeriod
        ? `Multi-period, ${plural(ctx.periods.length, 'investment year')}`
        : 'Single-period'

    case 'years':
      return ctx.periods.length === 0
        ? 'No investment years set'
        : `${plural(ctx.periods.length, 'investment year')}: ${ctx.periods.join(', ')}`

    case 'economics':
      if (ctx.periods.length === 0) return 'No investment years to weight yet'
      if (ctx.periods.length === 1) {
        return `Single period (${ctx.periods[0]}) — no discounting between periods`
      }
      return `Objective weighting set across ${plural(ctx.periods.length, 'year')} (${periodList(ctx.periods)})`

    case 'window':
      return `${ctx.rangeLabel}, ${resolutionLabel(ctx.freq)}`

    case 'sampling': {
      // `ctx` carries no signal for "representative weeks were actually
      // sampled" — the backend's SnapshotInfo has no such field, and the
      // page only learns it transiently from a mutation response, which is
      // not plain persisted data this module is allowed to depend on. So
      // this sentence must not assert a sampling state in EITHER direction:
      // not "sampled", not "not sampled" — both would be an unconditional
      // claim from data that cannot support one. (An earlier version of
      // this code said "Not sampled" unconditionally, which is simply false
      // for a network that was sampled — the exact defect this comment now
      // warns against repeating.)
      //
      // Instead it reports what IS knowable: the snapshot count and whether
      // weights are still default — both already in `ctx`, and together the
      // observable shape a sampled network actually has (a small count with
      // non-default weights). It never claims that shape was CAUSED by
      // sampling, only states the two facts, plus the separate and fully
      // supportable fact of whether sampling is available going forward.
      // A post-sampling network and a pristine one with the same count and
      // weights state are indistinguishable here; that is the honest
      // ceiling of this interface, not an oversight.
      const weights = ctx.weightsAreDefault ? 'default weights' : 'custom weights'
      const capability = ctx.canSampleWeeks
        ? 'representative weeks available'
        : 'upload an hourly profile to enable sampling'
      return `${snapshotCountLabel(ctx.snapshotCount)} · ${weights} · ${capability}`
    }

    case 'weights':
      return ctx.weightsAreDefault
        ? 'Default weights (every snapshot weighted 1)'
        : 'Custom weights applied'
  }
}
