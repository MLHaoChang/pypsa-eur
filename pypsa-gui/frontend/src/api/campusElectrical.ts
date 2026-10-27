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
import type { EditCard, EditReceipt } from '../components/EditCard'

/** `unknown`: the profile sets no limit for the check (or its base is unknown), never pass or fail. */
export type CheckStatus = 'pass' | 'fail' | 'not_rated' | 'not_rechecked' | 'unknown'

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
  /** Who moves the PCC transformer's on-load taps (plan C15); absent in a run
   *  saved before it existed, which means the campus. */
  pcc_tap_control: PccTapControl
}

export type PccTapControl = 'campus' | 'grid_operator'

export interface ComplianceRow {
  check: 'pcc_reactive' | 'pcc_voltage' | 'campus_voltage' | 'transformer_loading' | 'switchgear' | 'cable_loading'
    | 'pcc_q_band' | 'pcc_power_factor'
  status_as_is: CheckStatus
  status_with_measures: CheckStatus
  value: number | null
  limit: number | null
  unit: string
  worst_period: number | null
  worst_hour: number | null
  /** The case of the worst reading (`intact`, `N-1:<unit>`); null for the
   *  reactive exchange and the fault level, absent in an older run. */
  worst_case?: string | null
  /** The band a voltage row's limit is an edge of; null for the other checks,
   *  absent in an older run. */
  limit_min?: number | null
  limit_max?: number | null
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

/** What a row does (plan C13): a new asset, units added next to existing ones,
 *  existing units kept, or a group or cable bought where existing ones stood;
 *  plan C14: `renew`, an asset bought again at the end of its life.
 *  Absent in a run from before it, and empty for a need that needs nothing. */
export type InvestAction = 'buy' | 'add' | 'keep' | 'replace' | 'renew'

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
  action?: InvestAction | '' | null
  // Plan C14, one row per purchase; absent in a run from before it.
  /** 0 for a kept existing asset, 1 for the first buy, k for later ones. */
  purchase?: number | null
  /** The year bought (an existing asset's: its commissioning year). */
  build_year?: number | null
  /** The year its successor starts, or the horizon's end. */
  service_to?: number | null
  /** The row it replaces, as `lineage|purchase`. */
  replaces?: string | null
  lineage?: string | null
  commissioned?: number | null
  /** How the commissioning year is known: a source tag, or `assumed: ...`. */
  commissioned_source?: string | null
}

/** The electrical cost of one investment period (`campus_cost.csv`);
 *  `capex_replacement_eur` (plan C14) is the part bought again. */
export interface CostRow { period: number; capex_eur: number; annualised_eur_per_a: number; capex_replacement_eur?: number }

/** The study horizon (plan C14): each period's span in years and where it came
 *  from (`project`, or `assumed: ...`), the horizon's end and the run's notes. */
export interface HorizonScope {
  period_years: Record<string, number>
  source: Record<string, string>
  horizon_end: number
  notes: string[]
}

/** The compliance table re-solved with the assets: `status_with_measures` and
 *  `value_with_measures` are an AC result, not a recommendation. */
export interface InvestedComplianceRow extends ComplianceRow {
  value_with_measures: number | null
  detail_with_measures: string
  /** Where the re-solved value was read, which need not be the as-is worst
   *  hour; absent in a run from before they were kept. */
  worst_period_with_measures?: number | null
  worst_hour_with_measures?: number | null
  worst_case_with_measures?: string | null
}

export interface HistoryRow { iteration: number; need: string; from: string; to: string; check: string; detail: string }
export interface UnresolvedNeed { need: string; reason: string }

/** A tolerance of the parallel rules, tagged as every engine figure is. */
export interface TaggedRule { value: number; source: string; note?: string }

/** The rules a unit added next to existing transformers had to meet (plan C13),
 *  written with the run; the panel shows them and does not edit them. */
export interface ParallelScope {
  ratio_tol: TaggedRule
  uk_tol: TaggedRule
  step_tol_pct: TaggedRule
  unknown_vector_group: 'assume_compatible' | 'refuse'
  rating_ratio: string
}

/** Who buys the PCC switchgear, and (plan C13; absent in older runs) the rules
 *  for adding units in parallel, (plan C15; absent in older runs) who moves
 *  the PCC transformer's taps, and (plan C14; absent in older runs) the study
 *  horizon. */
export interface InvestScope {
  pcc_switchgear: 'campus' | 'grid_operator'
  parallel?: ParallelScope
  pcc_tap_control?: PccTapControl
  horizon?: HorizonScope
}

/** One transformer's tap in one hour and case of the re-check (plan C15,
 *  `campus_invest_taps.csv`): `moved_by` is the change from the campus file's
 *  position, `ratio_pct` the ratio against the rated one. */
export interface TapRow {
  period: number
  hour: number
  case: string
  transformer: string
  group: string
  tap_pos: number
  ratio_pct: number
  moved_by: number
}

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

/** The detail of an `unknown` row whose limit the profile does not set (gridspine `NO_LIMIT`). Any other
 *  `unknown` row lacks an input (P_ref, the PCC P, an hour on a side with a limit). */
export const NO_LIMIT_DETAIL = 'The grid code sets no limit for this.'

/** What the grid code requires that no steady-state check can show (status `reported`, never a verdict). */
export interface ReportedRequirement {
  id: string
  requirement: string
  value: string
  status: 'reported'
  detail: string
  clause: string
  source: GridLimit['source']
}

export interface CampusResults {
  selection: SelectedHour[]
  transformers: TransformerSizing[]
  compensation: CompensationSizing[]
  short_circuit: FaultLevel[]
  compliance: ComplianceRow[]
  requirement: Requirement
  /** Needs an RMS study; empty for a run from before it was kept. */
  reported_requirements?: ReportedRequirement[]
  /** Null throughout when the run did not invest. */
  investment: InvestmentRow[] | null
  cost: CostRow[] | null
  compliance_invested: InvestedComplianceRow[] | null
  history: HistoryRow[] | null
  unresolved: UnresolvedNeed[] | null
  scope: InvestScope | null
  /** Null when the run did not invest. */
  cost_basis?: CostBasis | null
  /** The on-load taps of the re-check and how many critical hours moved one
   *  (plan C15); null when the run did not invest, absent in older runs. */
  taps?: TapRow[] | null
  tap_hours_moved?: number | null
}

// ── the joint optimisation (MILP) as a background job (plan C12) ────────────
// `POST /{name}/milp` starts it (202 with the record), `GET /{name}/milp` is the
// record (null when no job has run since the backend started), `POST
// /{name}/milp/cancel` asks it to stop, keeping the best point found so far.

export type MilpJobState = 'running' | 'done' | 'cancelled' | 'failed'
/** Why the loop ended (`summary.stop`). */
export type MilpStop = 'converged' | 'stalled' | 'delta_floor' | 'max_iter' | 'cancelled'

/** The job's record (`campus_electrical_service.start_milp_job`). `iteration`,
 *  `max_iter` and `best_cost` are null until the loop's warm start is solved;
 *  `best_cost` is the cheapest AC-feasible point so far, `c8_cost` the least-cost pick's. */
export interface MilpJobRecord {
  state: MilpJobState
  project: string
  started_at: number
  finished_at: number | null
  iteration: number | null
  max_iter: number | null
  best_cost: number | null
  c8_cost: number | null
  stop: MilpStop | null
  error: string | null
  message: string
}

/** One iteration of the loop (`campus_milp_history.csv`). Iteration 0 is the warm start. */
export interface MilpHistoryRow {
  iteration: number
  cost: number
  feasible: boolean
  accepted: boolean
  worst_violation?: number | null
  worst_lin_error?: number | null
  delta?: number | null
  rho?: number | null
  choice: string
  note?: string | null
}

/** Per need, the least-cost choice against the joint one (`campus_milp_comparison.csv`). */
export interface MilpComparisonRow {
  need: string
  c8_choice: string
  milp_choice: string
  c8_annualised_eur_per_a: number
  milp_annualised_eur_per_a: number
}

export interface MilpSummary {
  method: 'milp'
  fallback: boolean
  reason: string
  stop: MilpStop
  c8_cost: number
  milp_cost: number
  c8_feasible: boolean
  iterations: number
}

/** What the last finished job chose (`run_milp/`). `fallback` is the reason the
 *  least-cost choice was kept, or null. */
export interface MilpResults {
  investment: InvestmentRow[] | null
  cost: CostRow[] | null
  compliance_invested: InvestedComplianceRow[] | null
  history: MilpHistoryRow[] | null
  comparison: MilpComparisonRow[] | null
  summary: MilpSummary
  fallback: string | null
  stop: MilpStop
}

export interface CampusMilp {
  status: MilpJobRecord | null
  results: MilpResults | null
  /** The least-cost run it was made from has changed since. */
  stale: boolean
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
  /** Which result those owner assets come from (owner decision 2026-10-12): the joint
   *  optimisation when it is fresh and found its own choice, else the least-cost run;
   *  null when there are none. */
  owner_assets_method?: 'milp' | 'least_cost' | null
  /** How many needs that same run could not meet; they are passed to the investment case,
   *  which flags the case (owner decision 2026-10-13). */
  owner_assets_unresolved?: number
  /** The joint optimisation's job and results (plan C12). */
  milp?: CampusMilp
  /** A study run is going on in the backend now (its own request, another
   *  tab's, the copilot's, or the joint optimisation's least-cost run): the
   *  results are the previous run's until it ends. Absent from an older backend. */
  run_in_progress?: boolean
  /** The project's latest campus run job (E2E demo plan 2026-10-14, D1): null
   *  when none has run since the backend started. */
  run_job?: CampusRunJob | null
}

/** The run's stages, as the backend names them (`RUN_STAGES`), then `done`. */
export type CampusRunStage = 'prepare' | 'rank' | 'island_checks' | 'cuts' | 'size' | 'invest' | 'gfm_settings' | 'done'

/** The campus run's solve-queue job, with the stage it reached. */
export interface CampusRunJob {
  id: string
  kind: 'campus'
  status: 'queued' | 'running' | 'completed' | 'failed' | 'aborted' | 'interrupted'
  position: number | null
  error: string | null
  enqueued_at: number
  started_at: number | null
  finished_at: number | null
  stage?: CampusRunStage | null
  stage_done?: number | null
  stage_total?: number | null
}

export const isActiveRun = (j: CampusRunJob | null | undefined): boolean =>
  j != null && (j.status === 'queued' || j.status === 'running')

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
  /** The infrequent tier of a two-tier rapid-voltage-change limit, %. */
  rvc_limit_infrequent_pct?: GridLimit
  /** A P-dependent Q band at the PCC: `points` [p_pu, q_min_pu, q_max_pu], per unit of P_ref. */
  q_capability_pq?: GridLimit & { points: [number, number, number][] }
  pf_min_lagging?: GridLimit
  pf_min_leading?: GridLimit
  /** Total harmonic voltage distortion at the PCC, % of the fundamental. */
  thd_limit_pct?: GridLimit
  /** Harmonic voltage limits per order at the PCC: `orders` {h: % of the fundamental}. */
  harmonic_limits_pct?: GridLimit & { orders: Record<string, number> }
  /** Short-term and long-term flicker severity limits at the PCC. */
  /** Resonance clearance, in harmonic orders, the scan needs from each order that counts. */
  harmonic_resonance_clearance_h?: GridLimit
  pst_limit?: GridLimit
  plt_limit?: GridLimit
  /** Negative-sequence voltage unbalance at the PCC, % of the positive sequence. */
  unbalance_limit_pct?: GridLimit
  /** The current-distortion standard whose tables give the TDD limits (`ieee519-2014`). */
  current_tdd_standard?: Omit<GridLimit, 'value'> & { value: string }
  /** Each generating unit's reactive capability: `points` [p_pu, q_min_pu, q_max_pu], per unit of the unit's
   * rated P, + = the unit delivers (over-excited). Not the PCC band above. */
  q_capability_unit?: GridLimit & { points: [number, number, number][] }
  /** Voltage-control deadband, % of nominal voltage either side. Reported, never checked. */
  voltage_deadband_pct?: GridLimit
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
  /** The pages the upload selected (`1-40,120-180`); absent when the whole file is read. */
  page_ranges?: string
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

// ── the island study (campus island plan, I9a) ──────────────────────────────

export type IslandScenario = 'ups_bridged' | 'seamless' | 'planned'
export const ISLAND_SCENARIOS: IslandScenario[] = ['ups_bridged', 'seamless', 'planned']
export type IslandSource = 'measured' | 'datasheet' | 'assumed'
/** Every number in the sidecar is `{value, source}`. */
export interface Tagged<T = number> { value: T; source: IslandSource }

/** The island sidecar as written (`island_config.json`), validated by the
 *  backend's schema on save. Only the fields the form edits are typed. */
export interface IslandConfig {
  units: Record<string, { kind: string; control?: string; governor?: string; [k: string]: unknown }>
  requirements: {
    scenarios: IslandScenario[]
    linearised_uc?: boolean
    [k: string]: unknown
  }
}

/** The report's groups, in the plan's order (I9). */
export type IslandGroup = 'inputs' | 'study' | 'hours' | 'static' | 'frequency'

export interface IslandTable {
  id: string
  group: IslandGroup
  title: string
  /** The model behind the table's verdicts. */
  model: string
  /** "Screening, not a certificate: ..." on every table (plan I9). */
  caveat: string
  columns: string[]
  rows: unknown[][]
  /** Indexes into `rows` of the rows resting on an assumed value. */
  assumed_rows: number[]
}

export interface IslandReport { caveat: string; tables: IslandTable[]; missing: string[] }
export interface IslandState {
  config: IslandConfig | null
  study: Record<string, unknown> | null
  /** When the study was written (UTC, ISO 8601); null without one. Absent from a server that predates it. */
  study_at?: string | null
}

// ── the frequency cuts (campus island plan, I7; owner Q6: manual) ───────────

export type CutKind = 'inertia' | 'headroom_up' | 'headroom_down' | 'droop_gain'

/** One cut the last campus run proposes (gridspine `campus_freq_cuts.csv`),
 * with whether the hub LP can take it and, when it can, its primal estimate. */
export interface ProposedCut {
  period: number
  hour: number
  scenario: string
  event: string
  kind: CutKind
  required: number | 'inf'
  current: number | null
  delta: number | 'inf' | null
  unit: string
  binding: string | null
  derivation: string | null
  heuristic: boolean
  feasible: boolean
  note: string | null
  applicable: boolean
  reason: string
  candidate: string | null
  added_mw: number | null
  estimate: number | null
  label: string
}

/** A cut the hub solve reads (`island_cuts.json`). */
export interface AppliedCut { period: number; hour: number; scenario: string; kind: CutKind; required: number }

/** What a trial brought up to the re-solved hub in the campus file. */
export type RefreshedRating =
  | { unit: string; field: 'p_mw' | 's_mva' | 'e_mwh'; from: number | null; to: number }
  | { unit: string; added: true }
  | { unit: string; added: false; reason: string }

export interface CutTrial {
  trial: number
  added: AppliedCut[]
  objective: number | null
  refreshed?: RefreshedRating[]
  verdict: string
}

/** One GFM converter's settings (gridspine `gfm_tuning._settings_of`). */
/** A null value: the engine's was not finite (an unrated unit's i_max pu, say). */
export interface GfmUnitSettings {
  droop_pct: number | null
  i_max_pu: number | null
  H_equivalent_s: number | null
  tau_f_s?: number | null
  H_v_s?: number | null
}

export type GfmStatus = 'unchanged' | 'recommended' | 'not_achievable' | 'incomplete'

/** gridspine's `campus_gfm_settings.json` (GFM-b), as written, plus `applied` once applied. */
export interface GfmRecommendation {
  label: string
  ledger: string[]
  status: GfmStatus
  achievable: boolean | null
  changed: boolean
  settings: Record<string, { model: string; current: GfmUnitSettings; recommended?: GfmUnitSettings }>
  cases: { key: string; ok_before: boolean; binding_before: string; ok_after: boolean | null; binding_after: string }[]
  /** `delta` null: no finite amount helps (a collapse: the island needs a former). */
  capacity: { case: string; kind: string; delta: number | null; unit: string; binding: string; derivation: string;
              feasible: boolean; note: string }[]
  /** Not rendered. A null worst with `ok` false is a collapse; with `ok` null, the model was skipped (`note`). */
  comparison: { unit: string; model: string; worst_rocof0_hz_per_s: number | null; worst_rocof_hz_per_s: number | null;
                worst_nadir_hz: number | null; ok: boolean | null; note: string | null }[]
  simulations: number
  max_simulations: number
  margin: number
  allow_i_max: boolean
  notes: string[]
  excluded: string[]
  /** Before #310's review the record sat here; it is now `GfmSettingsState.applied`. */
  applied?: GfmApplied
}

/** The last apply's record while the hub has not been re-solved since. */
export interface GfmApplied { at: number; changes: GfmChange[]; hub_resolve_needed: boolean }

export interface GfmChange { unit: string; pypsa_name: string; field: string; from: number | null; to: number }

/** `GET …/island/gfm-settings`: the UX ResultState names. */
export interface GfmSettingsState {
  state: 'not_run' | 'ready' | 'stale'
  reason: string
  recommendation: GfmRecommendation | null
  /** Outlives the recommendation (a campus run removes it); null once the hub is re-solved and saved. */
  applied?: GfmApplied | null
  /** Whether the solver settings have island constraints on now; absent from a server that predates it. */
  island_rows_now?: boolean
  /** When the recommendation was made (UTC, ISO 8601); null without one. Absent from a server that predates it. */
  recommended_at?: string | null
}

export interface IslandCuts {
  limit: string
  max_trials: number
  proposed: ProposedCut[]
  applied: AppliedCut[]
  trials: CutTrial[]
}

export type CutJobState = 'running' | 'done' | 'cancelled' | 'failed'

export interface CutJobRecord {
  state: CutJobState
  started_at: number
  finished_at: number | null
  trials: number
  max_trials: number
  stopped: string | null
  error: string | null
  message: string
}

/** The island study solves the project several times: give it the run's time. */
export const ISLAND_STUDY_TIMEOUT_MS = 900_000

const quiet = { skipErrorToast: true } as const

/** A study run is one synchronous request: seconds to minutes of AC load flows
 *  (18 to 31 s on a realistic campus at 3 hours per criterion, growing with the
 *  hours), and starting the joint optimisation makes that run first unless a
 *  fresh one exists. The client's 30 s default would abandon the request while
 *  the backend finishes and saves the run, so these calls override it, as the
 *  bundle download and import do (`projects.ts`). */
// ── The grid-readiness scorecard (DC grid readiness 02) ─────────────────────

export type GridReadinessVerdict = 'pass' | 'warn' | 'fail' | 'unknown'

/** Where a row's input is edited: the project flow's target shape. */
export interface GridReadinessTarget {
  section: string
  panel: string | null
  component: string | null
  entity: string | null
  field: string | null
}

export interface GridReadinessRow {
  id: string
  requirement: string
  limit: number | string | null
  unit: string | null
  source: string
  project_value: number | string | Record<string, unknown> | null
  verdict: GridReadinessVerdict
  assumed: boolean
  note: string | null
  section: string
  target: GridReadinessTarget
  /** DC 03: the value at the PoC once the battery/UPS has smoothed, judged by
   *  the row's own limit; present only with an AI-training load and a
   *  committed battery/UPS. The row's `verdict` stays before mitigation. */
  after_battery?: GridReadinessAfter
  /** The ramp, oscillation and SCR rows only (DC 03 slice 6): the user's own
   *  limit (null when not set) and what a reset returns to. */
  setting?: GridLimitSetting
  /** The harmonic row only (grid-to-process issue 17): the screen's figures at the PoC. */
  harmonics?: GridReadinessHarmonics
  /** The flicker row only (grid-to-process issue 17): the screen's figures at the PoC. */
  flicker?: GridReadinessFlicker
}

export interface GridReadinessFlicker {
  sk_mva: number
  pst: number | null
  plt: number | null
  pst_limit: number | null
  plt_limit: number | null
  no_input_units: string[]
  outside_range_units: string[]
}

export interface GridReadinessHarmonics {
  sk_mva: number
  thd_pct: number | null
  worst_order: number | null
  worst_u_h_pct: number | null
  failing_orders: number[]
  no_input_units: string[]
  demo_spectrum_units: string[]
}

export type GridLimitItem = 'connection_ramp' | 'oscillation' | 'fault_level'
export type GridLimitSource = 'code' | 'datasheet' | 'measured' | 'assumed'

export interface GridLimitSetting {
  item: GridLimitItem
  user: { value: number | null; source: GridLimitSource; note: string; set_at?: string } | null
  default: { value: number | null; source: string; origin: 'template' | 'placeholder' }
}

export interface GridReadinessAfter {
  value: number | null
  verdict: GridReadinessVerdict
  rule: Record<string, unknown>
  units: string[]
  /** Each unit's kind and assumed response latency from its control preset,
   *  and where the unit is (DC 03 slice 5). */
  unit_detail?: GridReadinessAfterUnit[]
  note: string
}

export interface GridReadinessAfterUnit {
  unit: string
  kind: string | null
  latency_s: number | null
  /** A UPS's walk-in (its share reaches the grid over it); null for a battery. */
  walk_in_s?: number | null
  basis: string
  target: GridReadinessTarget
}

export interface GridReadiness {
  schema: number
  profile: { id: string; title: string | null; error: string | null }
  template_id: string | null
  envelope: boolean
  rows: GridReadinessRow[]
  summary: Record<GridReadinessVerdict, number> & { assumed: number; red_flags: number
    /** Rows that fail or warn before mitigation and pass after the battery/UPS (DC 03). */
    pass_after_mitigation?: number }
  basis: string
}

export const CAMPUS_RUN_TIMEOUT_MS = 900_000
/** Drafting loads the whole saved network, as a bundle download does: its 180 s. */
export const CAMPUS_DRAFT_TIMEOUT_MS = 180_000
const base = (project: string) => `/campus-electrical/${encodeURIComponent(project)}`
const codes = (project: string) => `${base(project)}/grid-codes`

export const campusApi = {
  state: (project: string) =>
    client.get<CampusState>(base(project), quiet).then(r => r.data),

  /** Draft the campus file from the project's solved network. Refused (409)
   *  rather than overwrite an existing file unless `overwrite`. */
  draft: (project: string, overwrite = false) =>
    client.post<{ campus_yaml: string; skipped: string[] }>(`${base(project)}/draft`, { overwrite },
      { ...quiet, timeout: CAMPUS_DRAFT_TIMEOUT_MS })
      .then(r => r.data),

  /** Save the user's campus file; the backend builds it and answers 422 naming
   *  the field when it cannot. */
  save: (project: string, yaml: string) =>
    client.put<{ campus_yaml: string }>(`${base(project)}/campus`, { yaml }, quiet).then(r => r.data),

  /** Queue the study (prepare, rank, island checks, size, invest): a
   *  solve-queue job of kind `campus` that runs for minutes. Answers the job
   *  (202); its stage is read from the state's `run_job`. 409 while the
   *  project has a queued or running job. */
  run: (project: string, settings: CampusSettings) =>
    client.post<CampusRunJob>(`${base(project)}/run`, settings, quiet).then(r => r.data),

  /** Start the joint optimisation (MILP, minutes) in the background. The
   *  least-cost run is made first unless a fresh one with these settings exists.
   *  409 while a job runs for any project. */
  startMilp: (project: string, settings: CampusSettings) =>
    client.post<MilpJobRecord>(`${base(project)}/milp`, settings, { ...quiet, timeout: CAMPUS_RUN_TIMEOUT_MS })
      .then(r => r.data),

  /** The job's record; null when no job has run since the backend started. */
  milpStatus: (project: string) =>
    client.get<MilpJobRecord | null>(`${base(project)}/milp`, quiet).then(r => r.data ?? null),

  /** Ask the running job to stop; it keeps the best point found so far. */
  cancelMilp: (project: string) =>
    client.post<{ state: MilpJobState; cancelling: boolean }>(`${base(project)}/milp/cancel`, undefined, quiet)
      .then(r => r.data),

  /** The advisory grid-readiness scorecard (DC 02) against `profile`
   *  (default: the campus study's last profile). Reads the saved network. */
  gridReadiness: (project: string, profile?: string) =>
    client.get<GridReadiness>(`${base(project)}/grid-readiness`,
      { ...quiet, params: profile ? { profile } : undefined }).then(r => r.data),

  /** Set the user's own limit (DC 03 slice 6); a fault level takes no value.
   *  422 carries `{error_kind, message}`. */
  setGridLimit: (project: string, item: GridLimitItem,
                 body: { value?: number | null; source: GridLimitSource; note: string }) =>
    client.put(`${base(project)}/grid-readiness/limits/${item}`, body, quiet).then(r => r.data),

  /** Reset the limit to the template's value or the generic placeholder. */
  resetGridLimit: (project: string, item: GridLimitItem) =>
    client.delete(`${base(project)}/grid-readiness/limits/${item}`, quiet).then(r => r.data),

  /** The island sidecar as written and the last island study (null each when absent). */
  island: (project: string) =>
    client.get<IslandState>(`${base(project)}/island`, quiet).then(r => r.data),

  /** Save the island sidecar; 422 names the field, 413 over 256 kB. Drops the old study. */
  saveIsland: (project: string, config: IslandConfig) =>
    client.put<IslandState>(`${base(project)}/island`, { config }, quiet).then(r => r.data),

  /** Solve the islanding variants (minutes on a large project). */
  runIslandStudy: (project: string, scenarios?: IslandScenario[]) =>
    client.post<Record<string, unknown>>(`${base(project)}/island/study`,
      scenarios && scenarios.length ? { scenarios } : {},
      { ...quiet, timeout: ISLAND_STUDY_TIMEOUT_MS }).then(r => r.data),

  /** The proposed cuts with their estimates, the applied cuts and the trials. */
  islandCuts: (project: string) =>
    client.get<IslandCuts>(`${base(project)}/island/cuts`, quiet).then(r => r.data),

  /** Start the apply loop (a background job, 202 with its record). */
  applyIslandCuts: (project: string) =>
    client.post<CutJobRecord>(`${base(project)}/island/cuts/apply`, {}, quiet).then(r => r.data),

  islandCutsStatus: (project: string) =>
    client.get<CutJobRecord | null>(`${base(project)}/island/cuts/apply`, quiet).then(r => r.data ?? null),

  cancelIslandCuts: (project: string) =>
    client.post<CutJobRecord | null>(`${base(project)}/island/cuts/apply/cancel`, {}, quiet)
      .then(r => r.data),

  removeIslandCut: (project: string, cut: Pick<AppliedCut, 'period' | 'hour' | 'kind'>) =>
    client.delete<IslandCuts>(`${base(project)}/island/cuts/${cut.period}/${cut.hour}/${cut.kind}`, quiet)
      .then(r => r.data),

  clearIslandCuts: (project: string) =>
    client.delete<IslandCuts>(`${base(project)}/island/cuts`, quiet).then(r => r.data),

  /** The recommended GFM settings and whether they are still the campus run's. */
  gfmSettings: (project: string) =>
    client.get<GfmSettingsState>(`${base(project)}/island/gfm-settings`, quiet).then(r => r.data),

  /** Queue the recommender as a campus job (202 with the queue job); follow it on `state`'s run_job. */
  recommendGfmSettings: (project: string, allowIMax = false) =>
    client.post<CampusRunJob>(`${base(project)}/island/gfm-settings`, { allow_i_max: allowIMax }, quiet)
      .then(r => r.data),

  /** The edit card for applying the recommendation: old → new per unit, then what follows from it. */
  gfmApplyCard: (project: string) =>
    client.get<EditCard>(`${base(project)}/island/gfm-settings/card`, quiet).then(r => r.data),

  /** Write the recommended result into the island data and the campus file. With the digest
   *  of the card the user approved, the write is refused when the recommendation moved, and
   *  the response carries the receipt read back from both files. */
  applyGfmSettings: (project: string, digest: string) =>
    client.post<{ changes: GfmChange[]; hub_resolve_needed: boolean; receipt: EditReceipt }>(
      `${base(project)}/island/gfm-settings/apply`, { digest }, quiet).then(r => r.data),

  /** The island report: tables with their model, caveat and assumed rows. */
  islandReport: (project: string) =>
    client.get<IslandReport>(`${base(project)}/island/report`, quiet).then(r => r.data),

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
  /** `pageRanges` (`1-40,120-180`) selects the pages to read; the 100-page cap applies to them. */
  uploadGridCodeDocument: (project: string, file: File, pageRanges?: string) => {
    const fd = new FormData()
    fd.append('file', file)
    if (pageRanges?.trim()) fd.append('page_ranges', pageRanges.trim())
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

/** The request got no answer: it timed out or the connection dropped. The
 *  backend may still be running it (a study run saves its results when it
 *  ends), unlike a refusal, which comes with a response. */
export function isNoAnswer(err: unknown): boolean {
  const e = err as { response?: unknown; code?: string } | null
  if (e == null || e.response != null) return false
  return e.code === 'ECONNABORTED' || e.code === 'ETIMEDOUT' || e.code === 'ERR_NETWORK'
}

export function isOtherKind(err: unknown): boolean {
  return (err as { response?: { status?: number } } | null)?.response?.status === 409
}
