// The load-shape result (GET /results/load_shape) shaped for the Results →
// Load shape tab: heatmap colours, the week and month chart rows, the annual
// stack. Pure functions; a missing value stays missing, never 0.
import type {
  AnnualRow, HeatmapState, MonthRow, SeriesKey, SeriesState, WeekView,
} from '../../api/loadShape'

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Literal hex on purpose: `var(--…)` does not resolve inside SVG attributes.
export const GROUP_COLOR: Record<string, string> = {
  grid: '#6b7280', export: '#0891b2', solar: '#f59e0b', wind: '#0ea5e9', genset: '#a16207',
  ev: '#16a34a', flexible: '#9333ea', unserved: '#dc2626', excess: '#9ca3af',
}
export const GROUP_LABEL: Record<string, string> = {
  grid: 'Grid import', export: 'Export', solar: 'Solar', wind: 'Wind', genset: 'Genset',
  ev: 'EV charging', flexible: 'Flexible load', battery_soc: 'Battery state of charge',
  unserved: 'Unserved load', excess: 'Excess',
}

export const WEEK_LINES: SeriesKey[] = ['grid', 'export', 'solar', 'wind', 'genset', 'ev', 'flexible', 'unserved']
export const MONTH_STACK = ['solar', 'wind', 'genset', 'grid', 'export', 'excess'] as const

export function fmtMw(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'not established'
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} MW`
}

export function fmtMwh(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return 'not established'
  const a = Math.abs(v)
  if (a >= 1e6) return `${(v / 1e6).toLocaleString(undefined, { maximumFractionDigits: 2 })} TWh`
  if (a >= 1e3) return `${(v / 1e3).toLocaleString(undefined, { maximumFractionDigits: 1 })} GWh`
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })} MWh`
}

/** The largest cell of a heatmap (0 when it has none). */
export function heatMax(h: HeatmapState): number {
  if (h.status !== 'ok') return 0
  let m = 0
  for (const row of h.mean_mw) for (const v of row) if (v != null && v > m) m = v
  return m
}

/** A cell colour: empty (no snapshot) is blank, never the zero shade. */
export function heatColor(v: number | null, max: number, rgb = '59, 130, 246'): string {
  if (v == null || !Number.isFinite(v)) return 'transparent'
  if (max <= 0) return `rgba(${rgb}, 0.05)`
  return `rgba(${rgb}, ${(0.05 + 0.85 * Math.min(1, Math.max(0, v) / max)).toFixed(3)})`
}

export function cellTitle(day: number, hour: number, v: number | null, n: number): string {
  const when = `${WEEKDAYS[day]} ${String(hour).padStart(2, '0')}:00`
  return v == null ? `${when}: no snapshot` : `${when}: ${fmtMw(v)} (${n} snapshot${n === 1 ? '' : 's'})`
}

export interface WeekRow { t: string; flag?: number; [k: string]: number | string | undefined }

/** The week's chart rows: only the groups with data become columns. */
export function weekRows(week: Extract<WeekView, { status: 'ok' }>): { rows: WeekRow[]; keys: SeriesKey[]; top: number } {
  const keys = WEEK_LINES.filter(k => week.series[k].status === 'ok')
  const mw = (k: SeriesKey) => (week.series[k] as Extract<SeriesState, { status: 'ok' }>).mw
  let top = 0
  for (const k of keys) for (const v of mw(k)) if (v > top) top = v
  const flagged = new Set(week.flagged)
  const rows = week.index.map((t, i) => {
    const row: WeekRow = { t }
    for (const k of keys) row[k] = mw(k)[i]
    if (flagged.has(i)) row.flag = top
    return row
  })
  return { rows, keys, top }
}

export function socRows(week: Extract<WeekView, { status: 'ok' }>): Array<{ t: string; soc: number }> | null {
  const s = week.series.battery_soc
  return s.status === 'ok' ? week.index.map((t, i) => ({ t, soc: s.mw[i] })) : null
}

/** The groups a view cannot draw, with the server's reason (never a zero line). */
export function missingGroups(status: Record<string, { status: string; reason?: string }> | undefined) {
  return Object.entries(status ?? {})
    .filter(([, s]) => s.status !== 'ok')
    .map(([key, s]) => ({ key, label: GROUP_LABEL[key] ?? key, kind: s.status, reason: s.reason ?? '' }))
}

export interface MonthChartRow { month: string; daily?: number; [k: string]: number | string | undefined }

/** Monthly stack rows; a group the server did not establish is absent from the row. */
export function monthRows(months: MonthRow[]): MonthChartRow[] {
  return months.map(m => {
    const row: MonthChartRow = { month: m.month }
    for (const k of MONTH_STACK) if (m[k] != null) row[k] = m[k] as number
    if (m.daily_generation_mwh != null) row.daily = m.daily_generation_mwh
    return row
  })
}

export interface AnnualStack {
  segments: Array<{ key: string; label: string; mwh: number; color: string }>
  total: number
  notModelled: string[]
  export: number | null
  excess: number | null
  load: number
}

/** One period's supply stack (solar, wind, genset, grid) and the rest beside it. */
export function annualStack(row: Extract<AnnualRow, { status: 'ok' }>): AnnualStack {
  const segments: AnnualStack['segments'] = []
  const notModelled: string[] = []
  for (const k of ['solar', 'wind', 'genset', 'grid'] as const) {
    const v = row[k]
    if (v == null) notModelled.push(GROUP_LABEL[k])
    else segments.push({ key: k, label: GROUP_LABEL[k], mwh: v, color: GROUP_COLOR[k] })
  }
  return { segments, total: segments.reduce((s, x) => s + x.mwh, 0), notModelled,
           export: row.export, excess: row.excess, load: row.load_served }
}

export const periodLabel = (p: number | null | undefined) => (p == null ? 'Modelled year' : String(p))
