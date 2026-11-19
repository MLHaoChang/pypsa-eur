// The preference comparison of the asset-sizing loop (AS-10), `POST /api/projects/{name}/intent-comparison`.
import client from './client'

export type DesignIntent = 'A' | 'B' | 'C' | 'D' | 'E'
/** The preset table of `backend/models/design_intent.py`; `designIntents.parity.test.ts` fails when they drift. */
export const DESIGN_INTENTS: { id: DesignIntent; label: string }[] = [
  { id: 'A', label: 'Replace diesel' },
  { id: 'B', label: 'Keep diesel + MV smoothing BESS' },
  { id: 'C', label: 'Keep diesel, no new BESS' },
  { id: 'D', label: 'No UPS (BESS-EPS in parallel)' },
  { id: 'E', label: 'MV UPS (series)' },
]

/** `pass` only for a converged loop with no failing or unresolved check. */
export type Outcome = 'pass' | 'fail' | 'not_converged' | 'unresolved' | 'failed' | 'aborted'
export type CheckState = 'ok' | 'fail' | 'unresolved' | 'not_run'

export interface IntentSpecIn { intent: DesignIntent; backup_autonomy_h?: number }
export interface IntentRow {
  intent: DesignIntent
  status: string
  outcome: Outcome
  verdict: string
  cost_eur: number | null
  assets: Record<string, { value: number; unit: 'MW' | 'MWh' | 'Mvar' }>
  transformer_mva: number | null
  statcom_mvar: number | null
  checks: { name: string; state: CheckState; value: number | null; limit: number | null; note: string }[]
  basis_note: string
  warnings: string[]
  backup_autonomy_h: number | null
  backup_energy_mwh: number | null
}
export interface IntentComparisonRequest {
  intents: IntentSpecIn[]
  critical_mw: number
  inputs: Record<string, unknown>
  /** The campus study's grid-code profile id; null = none set. */
  grid_code: string | null
}
export type JobStatus = 'running' | 'done' | 'failed' | 'aborted'
export type RowProgress = 'queued' | 'running' | 'done'
/** The point-of-connection values the PQ and Pst checks read (`intent_inputs.PQ_COLUMNS`). */
export type PqField = 'x_pu' | 'pf' | 'pf_req' | 's_n_mva' | 'p_cap_mw' | 'statcom_capital_cost'
/** One project-held input: its value, where it came from in words, and its kind (`intent_inputs.KINDS`). */
export interface Sourced<T> { value: T | null; source: string | null; kind?: SourceKind; reason?: string }
export type SourceKind = 'project' | 'assumed' | 'load_envelope' | 'grid_code'
export interface Envelope {
  phases: unknown[]
  max: { swing_mw: number; oscillation_amplitude_mw: number; oscillation_period_s: number }
}
/** `GET .../intent-comparison/defaults`: what a run takes for each input the request leaves out. */
export interface IntentInputDefaults {
  link: Sourced<string>
  ramp_mw_per_min: Sourced<number>
  envelope: Sourced<Envelope>
  pq: Partial<Record<PqField, Sourced<number>>>
  /** The grid code the campus study applies: its saved one, else its default. */
  grid_code: { value: string | null; title: string | null; source: 'saved' | 'default' }
  /** That code's Pst limit (shipped or the project's own code), or a reason. */
  pst_limit: Sourced<number>
}
export interface IntentJob {
  status: JobStatus
  progress: Record<string, RowProgress>
  rows: IntentRow[]
  error: string | null
  /** The inputs the run used, each with its source ("project", "request", or an "assumed ..." note). */
  inputs?: { values: Record<string, unknown>; sources: Record<string, string | Record<string, string>> } | null
}

const base = (project: string) => `/projects/${encodeURIComponent(project)}/intent-comparison`
const quiet = { skipErrorToast: true } as const

export const intentComparisonApi = {
  start: (project: string, body: IntentComparisonRequest) =>
    client.post<IntentJob>(base(project), body, quiet).then(r => r.data),
  /** null: no comparison has run for this project. */
  get: (project: string) =>
    client.get<IntentJob>(base(project), quiet).then(r => r.data).catch(e => {
      if ((e as { response?: { status?: number } })?.response?.status === 404) return null
      throw e
    }),
  /** `link`: the link the user chose; `pq` is then that link's, the values a run on it takes. */
  defaults: (project: string, link?: string) =>
    client.get<IntentInputDefaults>(`${base(project)}/defaults`, { ...quiet, params: link ? { link } : undefined })
      .then(r => r.data),
  abort: (project: string) => client.post<{ status: string; aborting: boolean }>(`${base(project)}/abort`, {}, quiet).then(r => r.data),
}
