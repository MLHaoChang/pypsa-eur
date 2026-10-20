// GET /results/load_shape (site commercial UX A2): how the site's load and grid
// exchange are shaped in time. A group the network does not model is
// `not_modelled`; one the server cannot establish is `unavailable`. Neither is
// ever drawn as zero (ADR-0001).
import client from './client'

export type SeriesState =
  | { status: 'ok'; mw: number[] }
  | { status: 'not_modelled' | 'unavailable'; reason: string }

export type SeriesKey = 'grid' | 'export' | 'solar' | 'wind' | 'genset' | 'ev' | 'flexible'
  | 'battery_soc' | 'unserved'

export type HeatmapState =
  | { status: 'ok'; mean_mw: Array<Array<number | null>>; snapshots: number[][] }
  | { status: 'not_modelled' | 'unavailable'; reason: string }

export interface WeekInfo { key: string; from: number; to: number; start: string; end: string }

export type WeekView =
  | {
    status: 'ok'
    weeks: WeekInfo[]
    selected: number
    index: string[]
    series: Record<SeriesKey, SeriesState>
    /** Positions within the week. */
    flagged: number[]
    flag_basis: string[]
    import_capacity_mw: number | null
  }
  | { status: 'unavailable'; reason: string }

export interface MonthRow {
  month: string
  solar: number | null; wind: number | null; genset: number | null
  grid: number | null; export: number | null; excess: number | null
  daily_generation_mwh: number | null
}

export type MonthlyView =
  | { status: 'ok'; months: MonthRow[]; average_daily_generation_mwh: number | null }
  | { status: 'unavailable'; reason: string }

export type AnnualRow =
  | {
    status: 'ok'; period: number | null; load_served: number
    solar: number | null; wind: number | null; genset: number | null
    grid: number | null; export: number | null; excess: number | null
  }
  | { status: 'unavailable'; period: number | null; reason: string }

export interface LoadShapePayload {
  status: 'ok' | 'not_established'
  reason?: string
  unit: 'MW'
  period: number | null
  periods: Array<number | null>
  window?: { from: number; to: number; snapshots: number; start: string | null; end: string | null }
  series_status?: Record<string, { status: 'ok' | 'not_modelled' | 'unavailable'; reason?: string }>
  heatmaps?: { import: HeatmapState; export: HeatmapState }
  week?: WeekView
  monthly?: MonthlyView
  annual?: AnnualRow[]
  rule?: string
}

export interface LoadShapeQuery {
  period?: number | null
  from?: number
  to?: number
  week?: number
}

export const loadShapeApi = {
  /** `null` before a solve (204). */
  get: (q: LoadShapeQuery = {}) =>
    client.get<LoadShapePayload>('/results/load_shape', {
      params: {
        ...(q.period != null ? { period: q.period } : {}),
        ...(q.from != null ? { from: q.from } : {}),
        ...(q.to != null ? { to: q.to } : {}),
        ...(q.week != null ? { week: q.week } : {}),
      },
    }).then(r => (r.status === 204 ? null : r.data)),
}
