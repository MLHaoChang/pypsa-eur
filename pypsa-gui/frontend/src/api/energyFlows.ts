// GET /results/energy_flows (site commercial UX A1): where the site's energy
// came from, what it passed through and where it went, in MWh.
import client from './client'

export interface EnergyFlowRow {
  source: string
  via: string
  destination: string
  mwh: number
}

export interface EnergyFlowsPayload {
  /** `not_established` carries no rows: a flow the server cannot prove to
   *  conserve energy is unavailable, never drawn (ADR-0001). */
  status: 'ok' | 'not_established'
  reason?: string
  unit: 'MWh'
  period: number | null
  periods: Array<number | null>
  window?: {
    from: number; to: number; snapshots: number
    start: string | null; end: string | null; first: string | null; last: string | null
  }
  rows: EnergyFlowRow[]
  series: {
    index: string[]
    supply: Record<string, number[]>
    demand: Record<string, number[]>
    bucket_snapshots: number
  } | null
  conservation: {
    status: 'ok' | 'failed'
    checks: string[]
    generation_mwh: number
    destinations_mwh: number
    storage_delta_mwh: number
    stock_mwh: number
    residual_mwh: number
    tolerance_mwh: number
  } | null
  /** Null when the solve did not record unserved demand. */
  unserved_mwh?: number | null
  flags: string[]
  rule?: string
}

export interface EnergyFlowsQuery {
  period?: number | null
  from?: number
  to?: number
}

const params = (q: EnergyFlowsQuery) => ({
  ...(q.period != null ? { period: q.period } : {}),
  ...(q.from != null ? { from: q.from } : {}),
  ...(q.to != null ? { to: q.to } : {}),
})

export const energyFlowsApi = {
  /** `null` before a solve (204). */
  get: (q: EnergyFlowsQuery = {}) =>
    client.get<EnergyFlowsPayload>('/results/energy_flows', { params: params(q) })
      .then(r => (r.status === 204 ? null : r.data)),
  /** The same rows as the chart, as CSV text (the file the server writes). */
  csv: (q: EnergyFlowsQuery = {}) =>
    client.get<string>('/results/energy_flows.csv', { params: params(q), responseType: 'text' })
      .then(r => r.data),
}
