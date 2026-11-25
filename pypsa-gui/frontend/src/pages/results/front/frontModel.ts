// The multi-objective front in words and numbers (DC review 01, UX 28): one
// model for the Expert panel and the Guided section-10 tile, so the two read
// the same rows (Guided/Expert parity). Pure: no React, no fetching.
import type { FrontPoint, FrontRecord, FrontSource } from '../../../api/front'
import { money } from '../../../utils/money'
import { componentLabel } from '../../../utils/componentLabels'

/** Owner ruling (ag), verbatim: the figures after "Use this design" are the old network's. */
export const OUT_OF_DATE_TEXT = 'Out of date: the inputs changed after the last run. Run again to see the figures.'

export const SOURCE_WORDS: Record<FrontSource, string> = {
  front: 'Front study',
  eh_study: 'Energy Hub study',
}

/** The capacity table a point names ("generators:gas") → the component class. */
const TABLE_CLASS: Record<string, string> = {
  generators: 'Generator', storage_units: 'StorageUnit', stores: 'Store',
  links: 'Link', lines: 'Line', transformers: 'Transformer',
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

const NBSP = '\u00a0'

/** Money in money()'s notation ("1.2 m", "350 k"), without the symbol (the
 *  header carries "€/yr"), to three figures: the spread on a front is a few
 *  per cent of the cost, which two figures would round away. */
export function fmtCost(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'not read'
  const a = Math.abs(v)
  const body = a >= 1e6 ? `${sig(a / 1e6)}${NBSP}m` : a >= 1e3 ? `${sig(a / 1e3)}${NBSP}k` : money(a)
  return `${v < 0 ? '-' : ''}${body}`
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
    levelHeader: res?.lever === 'ens_cap' ? 'ENS target (‱ of demand)' : 'Reserve margin (%)',
  }
}

export interface BuiltAsset { key: string; kind: string; name: string; size: string }

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

function builtAssets(caps: Record<string, number | null> | undefined): BuiltAsset[] {
  return Object.entries(caps ?? {}).map(([key, v]) => {
    const [table, ...rest] = key.split(':')
    const cls = TABLE_CLASS[table] ?? table
    const unit = cls === 'Store' ? 'MWh' : 'MW'
    return {
      key, kind: componentLabel(cls), name: rest.join(':'),
      size: v == null || !Number.isFinite(v) ? 'not read' : `${sig(v)} ${unit}`,
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
      built: ok ? builtAssets(v?.capacities) : [],
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

// The CO2 colour scale: low emissions teal, high emissions orange. Literal
// hex (an SVG fill in a Recharts shape), readable on the light and dark
// surfaces alike; hue and lightness both change, so it does not rest on hue.
export const CO2_LOW = '#0f766e'
export const CO2_HIGH = '#ea580c'

const hex = (c: string) => [1, 3, 5].map(k => parseInt(c.slice(k, k + 2), 16))

export interface Co2Scale { min: number | null; max: number | null; colour: (v: number | null) => string }

export function co2Scale(rows: FrontRow[]): Co2Scale {
  const vals = rows.filter(r => r.ok && r.co2 != null && Number.isFinite(r.co2)).map(r => r.co2 as number)
  const min = vals.length ? Math.min(...vals) : null
  const max = vals.length ? Math.max(...vals) : null
  const [a, b] = [hex(CO2_LOW), hex(CO2_HIGH)]
  const colour = (v: number | null) => {
    if (v == null || min == null || max == null) return 'var(--color-muted)'
    const t = max > min ? (v - min) / (max - min) : 0
    const c = a.map((x, k) => Math.round(x + (b[k] - x) * t).toString(16).padStart(2, '0'))
    return `#${c.join('')}`
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
