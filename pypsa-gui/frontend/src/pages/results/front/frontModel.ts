// The multi-objective front in words and numbers (DC review 01, UX 28): one
// model for the Expert panel and the Guided section-10 tile, so the two read
// the same rows (Guided/Expert parity). Pure: no React, no fetching.
import type { FrontPoint, FrontRecord, FrontSource } from '../../../api/front'
import { SYMBOL } from '../../../utils/money'

/** Owner ruling (ag), verbatim: the figures after "Use this design" are the old network's. */
export const OUT_OF_DATE_TEXT = 'Out of date: the inputs changed after the last run. Run again to see the figures.'

export const SOURCE_WORDS: Record<FrontSource, string> = {
  front: 'Front study',
  eh_study: 'Energy Hub study',
}

/** Which record a surface shows: the one the user picked, else the Expert
 *  study (the one a design can be used from), else the Energy Hub study's.
 *  Both surfaces call this, so they open on the same front. */
export function pickSource(
  front: FrontRecord | null | undefined, eh: FrontRecord | null | undefined, picked?: FrontSource | null,
): FrontRecord | null {
  if (picked === 'front' && front) return front
  if (picked === 'eh_study' && eh) return eh
  return front ?? eh ?? null
}

const sig = (v: number, digits = 3): string =>
  (Math.abs(v) < 1e-9 ? 0 : v).toLocaleString('en-GB', Math.abs(v) >= 100
    ? { maximumFractionDigits: 0 } : { maximumSignificantDigits: digits })

export const fmtNum = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? 'not read' : sig(v)

export interface CostScale {
  /** "million", "thousand" or "" (plain units). */
  word: string
  /** The one unit every cost in the view carries: "million €/yr", in the case's currency. */
  unit: string
  /** A cost in the scale, bare ("1.12", "0.70"): the header carries the unit. */
  cell: (v: number | null | undefined) => string
  /** An axis tick in the scale, bare ("1", "1.05"). */
  tick: (v: number) => string
}

/** One cost scale per view, picked from the largest cost, so the chart's axis,
 *  the table's column and the capex/opex line always agree (UX 28: one unit
 *  per header, the numbers bare). */
export function costScale(rows: FrontRow[], currency?: string | null): CostScale {
  const vals = rows.flatMap(r => [r.cost, r.capex, r.opex]).filter((v): v is number => v != null && Number.isFinite(v))
  const top = vals.length ? Math.max(...vals.map(Math.abs)) : 0
  const [div, word] = top >= 1e6 ? [1e6, 'million'] : top >= 1e3 ? [1e3, 'thousand'] : [1, '']
  // The case's currency (the result's `currency`), in money()'s symbols.
  const cur = currency ? (SYMBOL[currency] ?? currency) : SYMBOL.EUR
  const unit = `${word ? `${word} ` : ''}${cur}/yr`
  const cell = (v: number | null | undefined) => {
    if (v == null || !Number.isFinite(v)) return 'not read'
    const x = v / div
    const a = Math.abs(x)
    const digits = a < 10 ? 2 : a < 100 ? 1 : 0
    return x.toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  }
  const tick = (v: number) => (v / div).toLocaleString('en-GB', { maximumFractionDigits: 3 })
  return { word, unit, cell, tick }
}

const fmtCi = (ci: [number, number] | null | undefined): string | null =>
  ci ? `${sig(ci[0])}–${sig(ci[1])}` : null

export interface FrontUnits {
  /** 'mc': sampled-outage EUE; 'lp': the dispatch model's ENS. */
  basis: 'mc' | 'lp'
  /** The x axis label, unit included, and the table header: the same string. */
  relHeader: string
  relUnit: string
  loleUnit: string
  levelHeader: string
}

export function frontUnits(record: FrontRecord): FrontUnits {
  const res = record.result
  const pts = res?.points ?? []
  const perHorizon = pts.some(p => p.mc?.time_basis === 'hours_per_horizon')
  const per = perHorizon ? ' per modelled horizon' : '/yr'
  const mc = res?.reliability_basis !== 'lp_ens'
  const relUnit = mc ? `MWh${per}` : 'MWh'
  return {
    basis: mc ? 'mc' : 'lp',
    relUnit,
    relHeader: mc ? `EUE (${relUnit})` : `ENS in the dispatch model (${relUnit})`,
    loleUnit: `h${per}`,
    levelHeader: res?.lever === 'ens_cap' ? 'ENS target (per 10,000 of demand)' : 'Reserve margin (%)',
  }
}

export interface BuiltAsset { key: string; name: string; size: string }

export interface FrontRow {
  /** Position in the record's points: what the apply route takes. */
  index: number
  label: string
  ok: boolean
  /** A failed point in words; null when it solved. */
  failure: string | null
  level: string
  co2Cap: string
  cost: number | null
  capex: number | null
  opex: number | null
  rel: number | null
  relCi: [number, number] | null
  lole: number | null
  loleCi: [number, number] | null
  co2: number | null
  /** true on the front, false dominated, null not judged. */
  onFront: boolean | null
  dominatedBy: string[]
  chosen: boolean
  built: BuiltAsset[]
}

const label = (i: number) => `#${i + 1}`

function failureWords(p: FrontPoint): string {
  const s = String(p.status ?? '')
  if (s.includes('infeasible')) return 'No design meets this level'
  if (s === 'error') return 'This point could not be solved'
  return 'This point did not solve'
}

/** Each built asset by name, with its size in the unit the backend read off
 *  its bus (`capacity_units`: MW, t/h on a tonne bus…). No unit when none was
 *  sent: never a guess. */
function builtAssets(caps: Record<string, number | null> | undefined,
  units: Record<string, string | null> | undefined): BuiltAsset[] {
  return Object.entries(caps ?? {}).map(([key, v]) => {
    const unit = units?.[key]
    return {
      key, name: key.split(':').slice(1).join(':'),
      size: v == null || !Number.isFinite(v) ? 'not read' : unit ? `${sig(v)} ${unit}` : sig(v),
    }
  })
}

export function frontRows(record: FrontRecord): FrontRow[] {
  const res = record.result
  const pts = res?.points ?? []
  const mc = res?.reliability_basis !== 'lp_ens'
  const chosen = record.applied?.index
  return pts.map((p, i) => {
    const v = p.point
    const ok = p.status === 'ok' && !!v
    const level = p.lever === 'ens_cap' ? sig(p.level) : sig(p.level * 100)
    return {
      index: i,
      label: label(i),
      ok,
      failure: ok ? null : failureWords(p),
      level,
      co2Cap: p.co2_cap_t_per_yr == null ? 'none' : sig(p.co2_cap_t_per_yr),
      cost: v?.total_eur_per_yr ?? null,
      capex: v?.capex_eur_per_yr ?? null,
      opex: v?.opex_eur_per_yr ?? null,
      rel: mc ? p.mc?.eue_mwh ?? null : v?.lp_ens_mwh ?? null,
      relCi: mc ? p.mc?.eue_ci ?? null : null,
      lole: p.mc?.lole_hours ?? null,
      loleCi: p.mc?.lole_ci ?? null,
      co2: v?.co2_t_per_yr ?? null,
      onFront: ok ? (p.on_front ?? null) : null,
      dominatedBy: (p.dominated_by ?? []).map(label),
      chosen: chosen === i,
      built: ok ? builtAssets(v?.capacities, v?.capacity_units) : [],
    }
  })
}

export const ciText = fmtCi

/** About `count` round ticks covering [lo, hi] (1, 2 or 5 × 10^k apart), and
 *  the domain they span, so the axis ends on a tick. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return []
  if (hi <= lo) {
    // One value: centre it.
    const d = (Math.abs(lo) || 1) * 0.05
    lo -= d
    hi += d
  }
  const raw = (hi - lo) / Math.max(1, count - 1)
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 5, 10].map(f => f * mag).find(st => st >= raw) ?? 10 * mag
  const start = Math.floor(lo / step) * step
  const out: number[] = []
  for (let v = start; v < hi + step * 0.999; v += step) out.push(Number(v.toPrecision(12)))
  return out
}

/** "On the front", or which points dominate it. */
export function frontWords(r: FrontRow): string {
  if (!r.ok || r.onFront == null) return 'not judged'
  if (r.onFront) return 'on the front'
  return `dominated by ${r.dominatedBy.join(', ')}`
}

// The CO2 colour scale: the --color-co2-low / --color-co2-high tokens
// (index.css, both themes). A point mixes them by its share of the range;
// hue and lightness both change, so the scale does not rest on hue. SVG takes
// these through `style`, not presentation attributes (var() resolves there).
export const CO2_LOW = 'var(--color-co2-low)'
export const CO2_HIGH = 'var(--color-co2-high)'

export interface Co2Scale { min: number | null; max: number | null; colour: (v: number | null) => string }

export function co2Scale(rows: FrontRow[]): Co2Scale {
  const vals = rows.filter(r => r.ok && r.co2 != null && Number.isFinite(r.co2)).map(r => r.co2 as number)
  const min = vals.length ? Math.min(...vals) : null
  const max = vals.length ? Math.max(...vals) : null
  const colour = (v: number | null) => {
    if (v == null || min == null || max == null) return 'var(--color-muted)'
    const t = max > min ? (v - min) / (max - min) : 0
    if (t <= 0) return CO2_LOW
    if (t >= 1) return CO2_HIGH
    return `color-mix(in srgb, ${CO2_HIGH} ${Math.round(t * 100)}%, ${CO2_LOW})`
  }
  return { min, max, colour }
}

/** The plain-word notices a front carries: the axes that never bound, the
 *  "every point is the same design" finding, the ranking basis, non-convexity. */
export function frontNotices(record: FrontRecord): string[] {
  const res = record.result
  if (!res) return []
  const out: string[] = []
  const d = res.degenerate
  if (d?.text) out.push(d.text)
  for (const a of Object.values(d?.axes ?? {})) if (a?.text) out.push(a.text)
  if (res.reliability_basis_note) out.push(res.reliability_basis_note)
  if (res.warning) out.push(res.warning)
  return out
}

/** True when the front gives no trade-off to read (every point one design). */
export const isDegenerate = (record: FrontRecord): boolean => record.result?.degenerate?.all === true

export interface Refusal { text: string; retry: boolean }

const statusOf = (e: unknown): number | undefined =>
  (e as { response?: { status?: number } } | null)?.response?.status
const detailOf = (e: unknown): string => {
  const d = (e as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return typeof d === 'string' ? d : ''
}

/** An apply refusal as one plain line. A revision refusal can be retried
 *  (the revision is read again); the others say what to do instead. */
export function applyRefusal(e: unknown): Refusal {
  const status = statusOf(e)
  const detail = detailOf(e)
  if (status === 409 && detail.startsWith('expected network revision')) {
    return { text: 'The network changed while you were choosing, so nothing was applied.', retry: true }
  }
  if (status === 409 && detail.includes('changed since the front study')) {
    return { text: 'The network was edited after this study, so nothing was applied. Run the study again to use one of its designs.', retry: false }
  }
  if (status === 409) {
    return { text: 'Another study or solve is running, so nothing was applied.', retry: true }
  }
  if (status === 422) {
    return { text: "This design does not match the network's extendable assets, so nothing was applied.", retry: false }
  }
  return { text: 'The design could not be applied: the server did not answer.', retry: true }
}

/** A start refusal (422: a bad grid or a budget) is the engine's own sentence,
 *  which names levels and counts in words; anything else is one plain line. */
export function startRefusal(e: unknown): string {
  const status = statusOf(e)
  const detail = detailOf(e)
  // The engine's sentence, cut into short sentences (no "; " chains).
  if (status === 422 && detail) {
    const parts = detail.split(/;\s*/).map((p, i) => (i === 0 ? p : p.charAt(0).toUpperCase() + p.slice(1)))
    return `The study was not started: ${parts.join('. ').replace(/\.?$/, '.')}`
  }
  if (status === 409) return 'Another study or solve is running, so the study was not started.'
  return 'The study was not started: the server did not answer.'
}
