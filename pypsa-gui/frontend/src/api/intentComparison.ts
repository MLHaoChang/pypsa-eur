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
export interface IntentJob {
  status: JobStatus
  progress: Record<string, RowProgress>
  rows: IntentRow[]
  error: string | null
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
  abort: (project: string) => client.post<{ status: string; aborting: boolean }>(`${base(project)}/abort`, {}, quiet).then(r => r.data),
}
