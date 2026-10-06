// The campus electrical study of a hub project, `/api/campus-electrical/*`.
//
// Each thunk maps to ONE handler in `backend/routers/campus_electrical.py`,
// which wraps one function in `services/campus_electrical_service.py`. The
// copilot's tools call those same functions. The panel derives nothing the
// backend does not already report.
//
// The state GET answers 409 for a project of another kind. The panel renders
// that inline, so every call passes `skipErrorToast`: a toast is the wrong
// surface for an expected answer.
import client from './client'

export type CheckStatus = 'pass' | 'fail' | 'not_rated' | 'not_rechecked'

export interface CampusSettings {
  k: number
  pf: number | null
  profile: string
  margin: number
  n_minus_1: boolean
}

export interface ComplianceRow {
  check: 'pcc_reactive' | 'pcc_voltage' | 'campus_voltage' | 'transformer_loading' | 'switchgear'
  status_as_is: CheckStatus
  status_with_measures: CheckStatus
  value: number | null
  limit: number | null
  unit: string
  worst_period: number | null
  worst_hour: number | null
  clause: string
  source: 'code' | 'assumed'
  detail: string
}

export interface TransformerSizing {
  group: string
  units: number
  unit_rating_mva: number
  max_s_intact_mva: number
  max_s_n1_mva: number
  required_unit_mva: number
  recommended_unit_mva: number | null
  adequate: boolean
  worst_period: number | null
  worst_hour: number | null
  unconverged_hours: number
  margin: number
  n_minus_1: boolean
}

export interface CompensationSizing {
  direction: 'capacitive' | 'inductive'
  required_mvar: number
  recommended_mvar: number
  worst_period: number | null
  worst_hour: number | null
  margin: number
}

export interface FaultLevel {
  period: number
  bus: string
  vn_kv: number
  ikss_max_ka: number
  ip_max_ka: number
  ikss_min_ka: number
  rated_ka: number | null
  rating_source: string | null
  adequate: boolean | null
  detail: string
}

export interface SelectedHour { period: number; hour: number; reasons: string[] }

export interface Requirement {
  q_limit_mvar: number
  clause: string
  source: 'code' | 'assumed'
  profile: string
  pf: number | null
  p_ref_mw: number
  p_ref_from: string
}

export interface CampusResults {
  selection: SelectedHour[]
  transformers: TransformerSizing[]
  compensation: CompensationSizing[]
  short_circuit: FaultLevel[]
  compliance: ComplianceRow[]
  requirement: Requirement
}

export interface CampusState {
  campus_yaml: string | null
  skipped: string[]
  profiles: Record<string, string>
  settings: CampusSettings | null
  results: CampusResults | null
  stale: boolean
}

const quiet = { skipErrorToast: true } as const
const base = (project: string) => `/campus-electrical/${encodeURIComponent(project)}`

export const campusApi = {
  state: (project: string) =>
    client.get<CampusState>(base(project), quiet).then(r => r.data),

  /** Draft the campus file from the project's solved network. Refused (409)
   *  rather than overwrite an existing file unless `overwrite`. */
  draft: (project: string, overwrite = false) =>
    client.post<{ campus_yaml: string; skipped: string[] }>(`${base(project)}/draft`, { overwrite }, quiet)
      .then(r => r.data),

  /** Save the user's campus file; the backend builds it and answers 422 naming
   *  the field when it cannot. */
  save: (project: string, yaml: string) =>
    client.put<{ campus_yaml: string }>(`${base(project)}/campus`, { yaml }, quiet).then(r => r.data),

  /** Prepare, rank and size the campus; returns the new state. Seconds of CPU. */
  run: (project: string, settings: CampusSettings) =>
    client.post<CampusState>(`${base(project)}/run`, settings, quiet).then(r => r.data),
}

export function isOtherKind(err: unknown): boolean {
  return (err as { response?: { status?: number } } | null)?.response?.status === 409
}
