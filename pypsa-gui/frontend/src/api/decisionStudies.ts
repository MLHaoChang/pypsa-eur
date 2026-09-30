// Decision studies (guided investment study MVP-1, plan S8): the client and
// the types, mirroring `backend/models/study.py` and the payloads of
// `backend/routers/studies.py`. Field names are the WIRE names (the model's
// `class_` is `class` on the wire).
//
// Two rules every payload obeys, and the UI must too:
// * ADR-0001 — a figure the engine could not resolve is `null` AND carries a
//   flag (`unavailable`). A `null` is never shown as `0`.
// * Sections carry `status: ok | not_established | skipped`.
//
// Every route lives under `/api/projects/{project}/studies`. In auth
// (multi-user) mode, and in local mode without `PYPSAGUI_DECISION_STUDIES=1`,
// EVERY route answers 404 with `detail.code` `decision_studies_unavailable`
// or `decision_studies_disabled` (OPEN-ITEMS 1; review v2 BC-6) — before the
// project is resolved, so `availability()` can probe with any project name.
import axios from 'axios'
import client from './client'

// ── enums and small shapes ────────────────────────────────────────────────

export type SectionStatus = 'ok' | 'not_established' | 'skipped'
export type VerdictClass = 'recommended' | 'marginal' | 'not_recommended'
export type Fidelity = 'quick_screen' | 'full_study'
export type MaturityClass = 'screening' | 'feasibility' | 'design'
export type LedgerStatus = 'default' | 'customised' | 'needs_attention'
export type Provenance = 'library' | 'user' | 'imported' | 'measured'
/** Every engine a figure may name (`models/study.py::Engine`), including the
 * S7 `method_constant` (the verdict's break-even tolerance). */
export type Engine =
  | 'lp' | 'lp_duals' | 'bill_calculator' | 'contract' | 'mc_resilience'
  | 'cash_flow_expander' | 'ledger' | 'method_constant'
export type SolveStatus = 'not_run' | 'queued' | 'running' | 'ok' | 'infeasible' | 'failed' | 'aborted'
export type ValueStreamsBasis = 'baseline' | 'pv_only_reference'

export interface FinancialBasis {
  terms: 'real' | 'nominal'
  tax: 'pre' | 'post'
  subsidy: 'excl' | 'incl'
}

/** One reportable figure with its provenance (`models/study.py::Figure`). */
export interface Figure {
  key: string
  label: string
  value: number | null
  unit: string
  basis: FinancialBasis | null
  currency_year: number | null
  engine: Engine
  fidelity: Fidelity | null
  unavailable: string | null
}

export interface LedgerRange { low: number; high: number; source?: 'source' | 'assumed' | null }
export interface LedgerDomain { low: number | null; high: number | null; low_open: boolean; high_open: boolean }

export interface LedgerRow {
  key: string
  label: string
  technical_name: string | null
  help: string | null
  value: number | null
  unit: string
  basis: 'real' | 'nominal'
  currency_year: number | null
  source: string
  source_year: number | null
  source_url: string | null
  range: LedgerRange | null
  provenance: Provenance
  status: LedgerStatus
  sensitivity_flag: boolean
  changed_by: string | null
  changed_at: string | null
  domain: LedgerDomain | null
  unavailable: Record<string, string>
}

export interface AssumptionsLedger {
  ledger_version: string
  rows: LedgerRow[]
  honesty_notes: string[]
}

export interface AccuracyBand {
  low_pct: number
  high_pct: number
  low_pct_narrow: number | null
  high_pct_narrow: number | null
  reference: string | null
}

export interface StudyMaturity {
  status: SectionStatus
  class: MaturityClass | null
  accuracy_band: AccuracyBand | null
  reasons: string[]
  unavailable: Record<string, string>
}

// ── the tariff (`models/study.py::Tariff`) ────────────────────────────────

export interface TimeRule { months: number[]; weekdays: number[]; hours: number[] }
export interface EnergyBand { label: string; price_per_mwh: number; applies: TimeRule }
export interface Tariff {
  tariff_id: string
  name: string
  source: string
  source_year: number | null
  currency: string
  currency_year: number | null
  billing_period: 'month' | 'year'
  energy_bands: EnergyBand[]
  demand_charge: { price_per_mw_per_period: number; basis: string } | null
  capacity_charge: { price_per_mw_per_year: number; basis: string } | null
  fixed_charge_per_period: number
  network_charges: Array<{ label: string; price: number; basis: string }>
  export: { price_per_mwh: number | null; series_ref: string | null; cap_mw: number | null }
  connection_limit_mw: number | null
  /** Snake_case codes; the sentence behind each is in `honesty_help`. */
  honesty_notes: string[]
  honesty_help: Record<string, string>
}

export interface BillComponents {
  energy: number | null
  demand: number | null
  capacity: number | null
  fixed: number | null
  network: number | null
  export_credit: number | null
  unavailable: Record<string, string>
}

export interface Bill {
  total: number | null
  annual_bill: number | null
  by_component: BillComponents
  peak_mw_by_billing_period: Record<string, number>
  billing_periods: string[]
  horizon_hours: number
  basis: FinancialBasis
  currency: string
  currency_year: number | null
  engine: 'bill_calculator'
  fidelity: Fidelity | null
  honesty_notes: string[]
  partial_billing_periods: string[]
  unavailable: Record<string, string>
}

// ── the study record (`models/study.py::DecisionStudy`) ──────────────────

/** The guided flow's answers, keyed by intake step (what `services/study/packs.py` reads). */
export interface StudyIntake {
  site?: { zone?: string; connection_mw?: number; year?: number; latitude?: number }
  load?: {
    source?: 'upload' | 'sector_profile'
    upload_id?: string
    unit?: 'kW' | 'MW'
    filename?: string
    profile?: string
    annual_mwh?: number
  }
  tariff?: { tariff_id?: string; custom?: Tariff; provenance?: 'user' | 'imported' }
  pv?: { enabled?: boolean; kind?: 'rooftop' | 'utility' }
  [step: string]: unknown
}

export interface DecisionStudy {
  schema_version: number
  study_id: string
  name: string
  question_id: string
  base_project: string
  option_projects: string[]
  perspective: string
  basis: FinancialBasis
  currency_year: number | null
  intake: StudyIntake
  ledger_version: string | null
  ledger: AssumptionsLedger | null
  fidelity_last_run: Fidelity | null
  budget: { solves_max: number; solves_used: number }
  maturity: StudyMaturity
  findings_ref: string | null
  report_ref: string | null
  pack_project: string | null
  created_by: string | null
  created_at: string
  updated_at: string
  stale: boolean
  stale_reasons: string[]
  honesty_notes: string[]
  /** Only on the POST that created a question-pack study. */
  base_project_name?: string
}

export interface LedgerPayload {
  study_id: string
  stored: boolean
  ledger: AssumptionsLedger
  maturity: StudyMaturity
}

// ── run and tornado records (`services/study/runner.py`, `tornado_runner.py`) ──

export type RunStatus = 'running' | 'done' | 'aborted' | 'failed'

export interface CampaignInfo {
  active: boolean
  budget_solves?: number
  spent_solves?: number
  [k: string]: unknown
}

export interface RunRecord {
  status: RunStatus
  study_id: string
  fidelity: Fidelity
  options: string[]
  solved: string[]
  current: string | null
  pending: string[]
  solves_charged: number
  campaign: CampaignInfo | null
  error: string | null
  started_at: number
  finished_at: number | null
}

export interface TornadoRecord {
  status: RunStatus
  kind: 'tornado'
  study_id?: string
  keys?: string[]
  current?: string | null
  solves_estimated?: number
  solves_charged?: number
  campaign?: CampaignInfo | null
  error?: string | null
  started_at?: number
  finished_at?: number | null
  robustness?: Robustness
}

// ── findings (`models/study.py::Findings`) ────────────────────────────────

export interface AssetSize { asset: string; p_nom_opt: number | null; e_nom_opt: number | null; unavailable: Record<string, string> }

export interface OptionResult {
  option_id: string
  label: string
  project_ref: string | null
  solve_status: SolveStatus
  sizes: AssetSize[]
  system_cost: number | null
  bill: number | null
  case_ref: string | null
  engine: Engine
  fidelity: Fidelity | null
  basis: FinancialBasis
  currency_year: number | null
  unavailable: Record<string, string>
}

export interface Verdict {
  status: SectionStatus
  class: VerdictClass | null
  sentence: string | null
  sentence_template: string | null
  facts: Record<string, Figure>
  headline_kpis: Figure[]
  drivers: string[]
  main_caveat: string | null
  option_id: string | null
  disclosures: string[]
  reasons: string[]
}

export interface TornadoRow {
  key: string
  label: string
  unit: string | null
  centre_value: number | null
  low_value: number
  high_value: number
  npv_low: number | null
  npv_high: number | null
  swing: number | null
  evaluation: 'redispatch' | 'capex_only' | 'rate_only' | null
  notes: string[]
  unavailable: Record<string, string>
}

export interface Robustness {
  status: SectionStatus
  method: 'redispatch_fixed_sizes'
  tornado: TornadoRow[]
  pending: string[]
  note: string | null
  option_id: string | null
  npv_centre: number | null
  /** ledger key -> skip code */
  skipped: Record<string, string>
  solves_charged: number
}

export interface BatteryAttribution {
  option_id: string
  status: SectionStatus
  method: 'battery_only' | 'battery_removed_same_pv'
  battery_p_nom_mw: number | null
  battery_npv: number | null
  option_npv: number | null
  reference_npv: number | null
  battery_payback_simple: number | null
  currency_year: number | null
  basis: FinancialBasis
  fidelity: Fidelity | null
  engine: Engine
  notes: string[]
  unavailable: Record<string, string>
}

export interface ValueStream {
  key: string | null
  label: string
  annual_value: number | null
  share: number | null
  engine: Engine
  basis: FinancialBasis
  unavailable: Record<string, string>
}

export interface Findings {
  /** ADR-0003: false when the run did not solve its options or the baseline. */
  available: boolean
  options: OptionResult[]
  options_status: SectionStatus
  pending_options: string[]
  hashes: { ledger_hash: string | null; base_network_hash: string | null; option_network_hashes: Record<string, string>; intake_hash: string | null }
  baseline: { project_ref: string | null; solve_status: SolveStatus; bill: number | null; case_ref: string | null; unavailable: Record<string, string> }
  verdict: Verdict
  robustness: Robustness
  explain: Array<Record<string, unknown>>
  completeness: Record<string, SectionStatus>
  honesty_notes: string[]
  battery_attribution: BatteryAttribution[]
  value_streams: ValueStream[]
  value_streams_option: string | null
  value_streams_status: SectionStatus
  /** S8: what the streams are measured against (null when not established). */
  value_streams_basis?: ValueStreamsBasis | null
}

// ── the investment case (`models/study.py::InvestmentCase`) ──────────────

export interface CashFlowYear {
  year: number
  capex: number
  replacements: number
  opex_fixed: number
  opex_variable: number
  savings: number
  salvage: number | null
  net_cash_flow: number
  discounted_cash_flow: number
  cumulative_discounted: number
  [k: string]: unknown
}

export interface CaseKpis {
  npv: number
  irr: number | null
  payback_simple: number | null
  payback_discounted: number | null
  lcos: number | null
  capex_total: number
  salvage_eur: number | null
  unavailable: Record<string, string>
  [k: string]: unknown
}

export interface InvestmentCase {
  available: boolean
  case_id: string
  study_id: string
  option_id: string
  status: SectionStatus
  basis: FinancialBasis
  currency_year: number | null
  fidelity: Fidelity | null
  engine: Engine
  horizon_years: number
  discount_rate: number
  years: CashFlowYear[]
  kpis: CaseKpis | null
  value_streams: ValueStream[]
  honesty_notes: string[]
}

// ── the report (`models/study.py::DecisionReport`) ───────────────────────

export interface ReportDisclosure { code: string; text: string; source: 'study' | 'tariff' | 'fallback' }

export interface DecisionReport {
  /** ADR-0003: true when the report's verdict is established. */
  available: boolean
  study_id: string
  question_id: string
  generated_at: string
  study_name: string | null
  question_title: string | null
  facts: Record<string, Figure>
  required_disclosures: ReportDisclosure[]
  evidence_gaps: string[]
  not_established: string[]
  honesty_help: Record<string, string>
  basis: FinancialBasis
  currency_year: number | null
  fidelity: Fidelity | null
  maturity: StudyMaturity
  sections: Record<string, { status: SectionStatus; note: string | null; payload: unknown }>
  completeness: Record<string, SectionStatus>
  /** Recomputed on every read (one hash rule with the case route's 409). */
  stale: boolean
  stale_reasons: string[]
  honesty_notes: string[]
}

// ── the intake's reads (S8 routes) ───────────────────────────────────────

export interface LoadProfileInfo { profile_id: string; label: string; source: string; synthetic: boolean; note: string }

export interface StudyLibrary {
  library_version: string | null
  currency_year: number
  default_tariff_id: string
  tariffs: Tariff[]
  load_profiles: LoadProfileInfo[]
  load_units: Array<'kW' | 'MW'>
}

export type PreviewPart<T> = ({ status: 'ok' } & T) | { status: 'not_established'; error_kind: string; message: string }

export interface LoadPreview {
  hours: number
  unit: 'kW' | 'MW'
  has_timestamps: boolean
  annual_mwh: number
  peak_mw: number
  connection_mw: number | null
  peak_exceeds_connection: boolean
  notes: string[]
  warnings: Array<{ code: string; message: string }>
}

export interface IntakePreview {
  load: PreviewPart<LoadPreview>
  bill: PreviewPart<{ bill: Bill; tariff: Tariff; notes: string[] }>
}

// ── errors ────────────────────────────────────────────────────────────────

/** A refusal the UI renders in words: HTTP status, the typed code, the backend's message. */
export interface StudyError {
  status: number | null
  code: string | null
  message: string
}

/** Read `{detail: {error_kind|code, message}}` (or a string detail) off an axios error. */
export function studyError(e: unknown): StudyError {
  if (axios.isAxiosError(e)) {
    const status = e.response?.status ?? null
    const detail = (e.response?.data as { detail?: unknown } | undefined)?.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const d = detail as { error_kind?: unknown; code?: unknown; message?: unknown }
      const code = typeof d.error_kind === 'string' ? d.error_kind
        : typeof d.code === 'string' ? d.code : null
      return { status, code, message: typeof d.message === 'string' ? d.message : String(e.message) }
    }
    if (typeof detail === 'string') return { status, code: null, message: detail }
    return { status, code: null, message: e.message }
  }
  const s = e as { status?: number; code?: string; message?: string } | null
  return { status: s?.status ?? null, code: s?.code ?? null, message: s?.message ?? String(e) }
}

// ── the client ────────────────────────────────────────────────────────────

// Every call renders its own refusal in place (a typed code with copy), so
// the interceptor's generic toast is skipped.
const Q = { skipErrorToast: true } as const
const base = (project: string) => `/projects/${encodeURIComponent(project)}/studies`
const item = (project: string, studyId: string) => `${base(project)}/${encodeURIComponent(studyId)}`

/** Absolute URLs for downloads (the browser follows them; not axios). */
export const studyUrls = {
  ledgerCsv: (project: string, studyId: string) => `/api${item(project, studyId)}/ledger.csv`,
  caseXlsx: (project: string, studyId: string, optionId: string) =>
    `/api${item(project, studyId)}/options/${encodeURIComponent(optionId)}/case.xlsx`,
  reportHtml: (project: string, studyId: string) => `/api${item(project, studyId)}/report.html`,
  reportDocx: (project: string, studyId: string) => `/api${item(project, studyId)}/report.docx`,
  reportXlsx: (project: string, studyId: string) => `/api${item(project, studyId)}/report.xlsx`,
}

export type Availability =
  | { available: true }
  | { available: false; code: string; message: string }

export const decisionStudiesApi = {
  /** Whether the study routes answer at all (flag and mode), probed with a
   * list call on `project`: the refusal fires before project resolution. */
  async availability(project: string): Promise<Availability> {
    try {
      await client.get(`${base(project)}/`, Q)
      return { available: true }
    } catch (e) {
      const err = studyError(e)
      if (err.code === 'decision_studies_unavailable' || err.code === 'decision_studies_disabled') {
        return { available: false, code: err.code, message: err.message }
      }
      throw e
    }
  },
  list: (project: string) =>
    client.get<DecisionStudy[]>(`${base(project)}/`, Q).then(r => r.data),
  create: (project: string, body: { question_id: string; name: string; intake: StudyIntake; project_name?: string }) =>
    client.post<DecisionStudy>(`${base(project)}/`, body, Q).then(r => r.data),
  get: (project: string, studyId: string) =>
    client.get<DecisionStudy>(item(project, studyId), Q).then(r => r.data),
  /** Per-step apply (spec decision 18): `step` may only carry `intake[step]`. */
  patchStep: (project: string, studyId: string, step: string, value: unknown) =>
    client.patch<DecisionStudy>(item(project, studyId), { step, intake: { [step]: value } }, Q).then(r => r.data),
  remove: (project: string, studyId: string) =>
    client.delete(item(project, studyId), Q).then(() => undefined),

  library: (project: string) =>
    client.get<StudyLibrary>(`${base(project)}/library`, Q).then(r => r.data),
  preview: (project: string, intake: StudyIntake) =>
    client.post<IntakePreview>(`${base(project)}/preview`, { intake }, Q).then(r => r.data),

  ledger: (project: string, studyId: string) =>
    client.get<LedgerPayload>(`${item(project, studyId)}/ledger`, Q).then(r => r.data),
  putLedger: (project: string, studyId: string, body: {
    rows?: Array<{ key: string; value: number; unit: string; source?: string; source_year?: number; currency_year?: number }>
    reseed?: boolean
    reset?: string[]
  }) => client.put<LedgerPayload>(`${item(project, studyId)}/ledger`, body, Q).then(r => r.data),

  startRun: (project: string, studyId: string, body: { fidelity?: Fidelity; budget_solves?: number } = {}) =>
    client.post<RunRecord>(`${item(project, studyId)}/run`, body, Q).then(r => r.data),
  run: (project: string, studyId: string) =>
    client.get<RunRecord>(`${item(project, studyId)}/run`, Q).then(r => r.data),
  abortRun: (project: string, studyId: string) =>
    client.post<{ status: RunStatus; aborting: boolean }>(`${item(project, studyId)}/run/abort`, {}, Q).then(r => r.data),

  findings: (project: string, studyId: string) =>
    client.get<Findings>(`${item(project, studyId)}/findings`, Q).then(r => r.data),
  startTornado: (project: string, studyId: string, body: { budget_solves?: number } = {}) =>
    client.post<TornadoRecord>(`${item(project, studyId)}/findings/tornado`, body, Q).then(r => r.data),
  tornado: (project: string, studyId: string) =>
    client.get<TornadoRecord>(`${item(project, studyId)}/findings/tornado`, Q).then(r => r.data),
  abortTornado: (project: string, studyId: string) =>
    client.post<{ status: RunStatus; aborting: boolean }>(`${item(project, studyId)}/findings/tornado/abort`, {}, Q).then(r => r.data),

  optionCase: (project: string, studyId: string, optionId: string) =>
    client.get<InvestmentCase>(`${item(project, studyId)}/options/${encodeURIComponent(optionId)}/case`, Q).then(r => r.data),

  assembleReport: (project: string, studyId: string) =>
    client.post<DecisionReport>(`${item(project, studyId)}/report`, {}, Q).then(r => r.data),
  report: (project: string, studyId: string) =>
    client.get<DecisionReport>(`${item(project, studyId)}/report`, Q).then(r => r.data),
}
