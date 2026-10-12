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
import client, { formatApiDetail } from './client'

export type CheckStatus = 'pass' | 'fail' | 'not_rated' | 'not_rechecked'

export interface CampusSettings {
  k: number
  pf: number | null
  profile: string
  margin: number
  n_minus_1: boolean
  /** Buy the electrical assets from the library after sizing (plan C9). */
  invest: boolean
  /** The grid operator owns the PCC switchgear: it is not bought or costed. */
  pcc_switchgear_by_operator: boolean
}

export interface ComplianceRow {
  check: 'pcc_reactive' | 'pcc_voltage' | 'campus_voltage' | 'transformer_loading' | 'switchgear' | 'cable_loading'
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

// ── the investment step (plan C9) ───────────────────────────────────────────

export type InvestStatus = 'chosen' | 'kept' | 'not_needed' | 'unresolved'

/** One need and the asset bought for it (`campus_investment.csv`). `library_id`
 *  and `invest_period` are null for a need that needs nothing. */
export interface InvestmentRow {
  need: string
  library_id: string | null
  kind: string
  units: number
  length_km: number | null
  invest_period: number | null
  capex_eur: number
  opex_eur_per_a: number
  annualised_eur_per_a: number
  existing: boolean
  status: InvestStatus
  reason: string | null
}

/** The electrical cost of one investment period (`campus_cost.csv`). */
export interface CostRow { period: number; capex_eur: number; annualised_eur_per_a: number }

/** The compliance table re-solved with the assets: `status_with_measures` and
 *  `value_with_measures` are an AC result, not a recommendation. */
export interface InvestedComplianceRow extends ComplianceRow {
  value_with_measures: number | null
  detail_with_measures: string
}

export interface HistoryRow { iteration: number; need: string; from: string; to: string; check: string; detail: string }
export interface UnresolvedNeed { need: string; reason: string }

/** Who buys the PCC switchgear. */
export interface InvestScope { pcc_switchgear: 'campus' | 'grid_operator' }

/** The solved hub's own system cost. `per_period` is EUR per year for a
 *  multi-period project; a single-period project has only `total`, as solved. */
export interface HubCost {
  basis: 'per_period' | 'single_period'
  per_period: Record<string, number> | null
  total: number
}

/** What the electrical costs were annualised on (part three, D1a). The rate is
 *  the project's solver config when it has one, else the library's; the price
 *  year is the project's finance inputs' `currency_year` when stated. A year
 *  that differs from the library's is flagged, never converted: the library's
 *  costs stay in `library_price_year` money. */
export interface CostBasis {
  discount_rate: number
  discount_rate_from: 'project solver config' | 'asset library'
  price_year: number
  price_year_from: 'project finance inputs' | 'asset library'
  library_price_year: number
  price_year_mismatch: boolean
  currency: string
}

export interface CampusResults {
  selection: SelectedHour[]
  transformers: TransformerSizing[]
  compensation: CompensationSizing[]
  short_circuit: FaultLevel[]
  compliance: ComplianceRow[]
  requirement: Requirement
  /** Null throughout when the run did not invest. */
  investment: InvestmentRow[] | null
  cost: CostRow[] | null
  compliance_invested: InvestedComplianceRow[] | null
  history: HistoryRow[] | null
  unresolved: UnresolvedNeed[] | null
  scope: InvestScope | null
  /** Null when the run did not invest. */
  cost_basis?: CostBasis | null
}

export interface CampusState {
  campus_yaml: string | null
  skipped: string[]
  profiles: Record<string, string>
  settings: CampusSettings | null
  results: CampusResults | null
  stale: boolean
  /** Null, with `hub_cost_reason`, when the hub's cost cannot be computed. */
  hub_cost: HubCost | null
  hub_cost_reason: string | null
  /** How many purchased items the last investment run offers as extra owner assets. */
  owner_assets_count?: number
}

/** The asset library: the project's copy, else the shipped default. */
export interface AssetLibrary { yaml: string; is_default: boolean }

// ── the project's own grid codes (plan C10) ─────────────────────────────────
// `/api/campus-electrical/{name}/grid-codes/*`, wrapping
// `services/campus_grid_code_service.py`. A document is uploaded and kept in
// the project; the copilot drafts a profile from it; a person confirms each
// limit against its quote; the draft is then published for the study.

export type LimitSource = 'code' | 'assumed' | 'extracted'

/** One limit of a profile. A band carries kv_min/kv_max and v_min/v_max, the
 *  reactive range and the rapid voltage change a `value`, the campus band
 *  v_min/v_max. `page` and `quote` come with an extracted limit. */
export interface GridLimit {
  value?: number
  kv_min?: number
  kv_max?: number
  kv_max_inclusive?: boolean
  v_min?: number
  v_max?: number
  clause: string
  source: LimitSource
  page?: number
  quote?: string
}

export interface GridCodeProfile {
  title: string
  voltage_bands: GridLimit[]
  q_range_demand: GridLimit
  rvc_limit_pct: GridLimit
  campus_voltage?: GridLimit
  document?: { sha256: string; filename: string; title: string }
}

export interface GridCodeSummary {
  id: string
  title: string
  /** Limit paths still tagged `extracted`. */
  unconfirmed: string[]
  /** The uploaded document's sha256, or null. */
  document: string | null
  error?: string
}

export interface GridCodeDocument {
  id: string
  sha256?: string
  filename: string
  size: number
  uploaded_at: string
  pages: number
}

export interface GridCodeList {
  shipped: Record<string, string>
  published: GridCodeSummary[]
  drafts: GridCodeSummary[]
  documents: GridCodeDocument[]
  extraction_available: boolean
}

/** `quote_found` is null when the document has been deleted. */
export interface QuoteCheck { quote_found: boolean | null; found_on_page: number | null }

export interface GridCodeReview {
  document: string
  model: string
  extracted_at: string
  /** Top-level keys the document did not state, such as `voltage_bands`. */
  filled_from_template: string[]
  limits: Record<string, QuoteCheck>
}

export interface GridCodeDraft {
  id: string
  yaml: string
  profile: GridCodeProfile
  review: GridCodeReview | null
  unconfirmed: string[]
  document: GridCodeDocument | null
}

const quiet = { skipErrorToast: true } as const
const base = (project: string) => `/campus-electrical/${encodeURIComponent(project)}`
const codes = (project: string) => `${base(project)}/grid-codes`

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

  /** The asset library a study buys from. */
  library: (project: string) =>
    client.get<AssetLibrary>(`${base(project)}/library`, quiet).then(r => r.data),

  /** Keep the text as the project's copy. 422 names the entry and field, 413 over 1 MB. */
  saveLibrary: (project: string, yaml: string) =>
    client.put<AssetLibrary>(`${base(project)}/library`, { yaml }, quiet).then(r => r.data),

  /** Delete the project's copy; the shipped default is used again. */
  resetLibrary: (project: string) =>
    client.post<AssetLibrary>(`${base(project)}/library/reset`, undefined, quiet).then(r => r.data),

  // grid codes: one thunk per route
  gridCodes: (project: string) =>
    client.get<GridCodeList>(`${codes(project)}`, quiet).then(r => r.data),

  /** Multipart field `file`. 415 not a PDF, 413 too big, 422 unreadable. */
  uploadGridCodeDocument: (project: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<GridCodeDocument>(`${codes(project)}/documents`, fd, quiet).then(r => r.data)
  },

  deleteGridCodeDocument: (project: string, documentId: string) =>
    client.delete<{ deleted: string }>(`${codes(project)}/documents/${encodeURIComponent(documentId)}`, quiet)
      .then(r => r.data),

  /** The copilot's draft. 503 no key, 502 the model failed, 409 a draft exists. */
  extractGridCode: (project: string, documentId: string, body: { profile_id?: string; overwrite: boolean }) =>
    client.post<GridCodeDraft>(`${codes(project)}/documents/${encodeURIComponent(documentId)}/extract`, body, quiet)
      .then(r => r.data),

  /** A blank draft to fill in by hand. */
  newGridCodeDraft: (project: string, body: { profile_id: string; title?: string; overwrite: boolean }) =>
    client.post<GridCodeDraft>(`${codes(project)}/drafts`, body, quiet).then(r => r.data),

  getGridCodeDraft: (project: string, profileId: string) =>
    client.get<GridCodeDraft>(`${codes(project)}/drafts/${encodeURIComponent(profileId)}`, quiet).then(r => r.data),

  /** 422 names the field. */
  saveGridCodeDraft: (project: string, profileId: string, yaml: string) =>
    client.put<GridCodeDraft>(`${codes(project)}/drafts/${encodeURIComponent(profileId)}`, { yaml }, quiet)
      .then(r => r.data),

  deleteGridCodeDraft: (project: string, profileId: string) =>
    client.delete<{ deleted: string }>(`${codes(project)}/drafts/${encodeURIComponent(profileId)}`, quiet)
      .then(r => r.data),

  /** `limit` is a path: `voltage_bands[0]`, `q_range_demand`, `rvc_limit_pct`, `campus_voltage`. */
  confirmGridCodeLimit: (project: string, profileId: string, limit: string) =>
    client.post<GridCodeDraft>(`${codes(project)}/drafts/${encodeURIComponent(profileId)}/confirm`, { limit }, quiet)
      .then(r => r.data),

  /** 409 lists the unconfirmed paths unless `allowUnconfirmed`. */
  publishGridCode: (project: string, profileId: string, allowUnconfirmed: boolean) =>
    client.post<{ id: string; unconfirmed: string[]; profiles: Record<string, string> }>(
      `${codes(project)}/drafts/${encodeURIComponent(profileId)}/publish`, { allow_unconfirmed: allowUnconfirmed }, quiet,
    ).then(r => r.data),

  getPublishedGridCode: (project: string, profileId: string) =>
    client.get<GridCodeDraft>(`${codes(project)}/published/${encodeURIComponent(profileId)}`, quiet).then(r => r.data),

  deletePublishedGridCode: (project: string, profileId: string) =>
    client.delete<{ deleted: string }>(`${codes(project)}/published/${encodeURIComponent(profileId)}`, quiet)
      .then(r => r.data),
}

/** The backend's refusal as one line: its `detail`, or the request's message. */
export function errorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return formatApiDetail(detail, (e as Error)?.message ?? 'Request failed')
}

export function isOtherKind(err: unknown): boolean {
  return (err as { response?: { status?: number } } | null)?.response?.status === 409
}
