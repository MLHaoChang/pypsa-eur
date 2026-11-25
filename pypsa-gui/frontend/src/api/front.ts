// The multi-objective front (DC review 01): cost against reliability against
// CO2, one full re-optimisation per point. Two sources share one record shape:
// the Expert study (GET /results/front, which a point can be applied from) and
// the Energy Hub study's opt-in front stage (GET /results/eh_front, read-only).
import client from './client'

export type FrontLever = 'ens_cap' | 'reserve_margin'
export type FrontSource = 'front' | 'eh_study'

export interface FrontMc {
  lole_hours: number | null
  lole_ci: [number, number] | null
  eue_mwh: number | null
  eue_ci: [number, number] | null
  n_samples: number
  converged?: boolean | null
  below_floor?: boolean
  time_basis?: 'hours_per_year' | 'hours_per_horizon' | string | null
}

export interface FrontPointValues {
  capex_eur_per_yr: number | null
  opex_eur_per_yr: number | null
  total_eur_per_yr: number | null
  lp_ens_mwh: number | null
  reliability_binding?: boolean | null
  co2_t_per_yr: number | null
  co2_binding?: boolean | null
  /** "table:name" → built size, MW (MWh for a Store); null when unread. */
  capacities: Record<string, number | null>
  /** Each capacity key's size unit, read off its bus (MW, MWh, t/h, t, MVA); null when not known. */
  capacity_units?: Record<string, string | null>
}

export interface FrontPoint {
  index?: number
  lever: FrontLever
  level: number
  co2_cap_t_per_yr: number | null
  status: string
  error?: string
  point: FrontPointValues | null
  mc: FrontMc | null
  mc_error?: string | null
  dominated_by?: number[]
  on_front?: boolean | null
  reliability_indistinct?: boolean
}

export interface FrontDegenerate {
  axes?: Record<string, { text: string; levers?: string[]; unknown?: boolean }>
  all?: boolean
  text?: string | null
}

export interface FrontResult {
  lever: FrontLever
  lever_reason?: string
  levels: number[]
  co2_caps_t_per_yr?: Array<number | null>
  points: FrontPoint[]
  reliability_basis?: 'mc_eue' | 'lp_ens'
  reliability_basis_note?: string | null
  degenerate?: FrontDegenerate | null
  warning?: string | null
  aborted?: boolean
  reference_co2_t_per_yr?: number | null
  /** The case's currency (ISO code): the network's own costs, labelled in it, not converted. */
  currency?: string | null
  /** True when the case set no valid currency and `currency` is the default. */
  currency_assumed?: boolean
}

/** GET /results/front and GET /results/eh_front: one shape. */
export interface FrontRecord {
  status: 'running' | 'done' | 'aborted' | 'failed' | string
  result: FrontResult | null
  error?: string | null
  edited_since_study?: boolean | null
  finished_at?: number | null
  /** The point last put on the network ("Use this design"), Expert study only. */
  applied?: { index: number; network_revision: number } | null
  /** Set by the reader: which route the record came from. */
  source: FrontSource
  /** Whether a point can be applied from this record (the Expert study only). */
  applicable: boolean
}

export interface FrontStartBody {
  levels: number[]
  co2_caps_t?: Array<number | null>
  lever?: FrontLever
  mc_draws?: number
}

const tag = (source: FrontSource) => (r: { status: number; data: unknown }): FrontRecord | null =>
  r.status === 204 || !r.data
    ? null
    : { ...(r.data as Omit<FrontRecord, 'source' | 'applicable'>), source, applicable: source === 'front' }

const quiet = { skipErrorToast: true }

export const frontApi = {
  get: () => client.get('/results/front').then(tag('front')),
  getEh: () => client.get('/results/eh_front').then(tag('eh_study')),
  // The panel shows a refusal itself, in one plain line: no toast as well.
  start: (body: FrontStartBody) => client.post('/results/front', body, quiet).then(r => r.data),
  abort: () => client.post('/results/front/abort').then(r => r.data),
  apply: (index: number, expectedRevision: number) =>
    client.post<{ applied: number; fixed: number; network_revision: number }>(
      '/results/front/apply', { index, expected_revision: expectedRevision }, quiet,
    ).then(r => r.data),
}
