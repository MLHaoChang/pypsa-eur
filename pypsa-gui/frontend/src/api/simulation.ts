import client from './client'
import type { SolverConfig, SimulationStatus, PreflightResult } from './types'

export const simulationApi = {
  getSolverConfig: () => client.get<SolverConfig>('/simulation/solver_config').then(r => r.data),
  updateSolverConfig: (cfg: Partial<SolverConfig>) => client.put<SolverConfig>('/simulation/solver_config', cfg).then(r => r.data),
  checkSolvers: () => client.get<Record<string,boolean>>('/simulation/check_solvers').then(r => r.data),
  // Operator-controlled feature flags. Currently exposes `user_code_enabled`
  // which the UI uses to disable the `extra_functionality_code` textarea
  // unless the operator has set PYPSA_GUI_ALLOW_USER_CODE=1 server-side.
  getCapabilities: () => client.get<{ user_code_enabled: boolean }>('/simulation/capabilities').then(r => r.data),
  preflight: () => client.post<PreflightResult>('/simulation/preflight').then(r => r.data),
  // Periodized per-asset capital cost (PyPSA's annuity applied where the
  // user typed overnight_cost). Keyed as {component_attr: {asset_name: value}}.
  // Use this in capacity-expansion CAPEX views to avoid €0 rows on assets
  // that have overnight_cost set but capital_cost=0.
  getAssetCosts: () => client.get<AssetCostMap>('/simulation/asset_costs').then(r => r.data),
  run: () => client.post('/simulation/run'),
  // Stage 2 standalone trigger. Requires that a LOPF (or SCLOPF) run has
  // already populated n.generators_t.p. Returns immediately ({status:
  // 'started'}); progress streams via the existing /log_stream SSE, and
  // the new convergence info appears in /results/ac_pf/status.
  runAcPf: () => client.post('/simulation/run_ac_pf'),
  abort: () => client.post('/simulation/abort'),
  // Disown a stuck "running" state when the worker thread is still alive but
  // we want to give up on it (e.g. the user lost the SSE log and decides the
  // solve is genuinely hung). Backend marks the old worker as orphaned so its
  // final state write is suppressed, and a new /run can start. The old solver
  // still holds the PyPSA lock until it finishes natively — if the new /run
  // blocks on that lock, only a backend restart frees it.
  forceReset: () => client.post('/simulation/force_reset'),
  getStatus: () => client.get<SimulationStatus>('/simulation/status').then(r => r.data),
  // Timeout-aware variants for `abortRunningSim` in projectActions, which
  // needs to bail out fast when the backend is hung in solver native code —
  // axios' default 30 s timeout would otherwise turn every probe in the
  // poll loop into a 30 s wait. Separate methods (rather than an optional
  // arg on the existing ones) so React Query queryFn signatures stay
  // unchanged.
  abortFast: (timeoutMs: number) =>
    client.post('/simulation/abort', undefined, { timeout: timeoutMs }),
  getStatusFast: (timeoutMs: number) =>
    client.get<SimulationStatus>('/simulation/status', { timeout: timeoutMs }).then(r => r.data),
  // Authoritative lock-released check — the `/status.running` flag flips
  // to `false` the moment `/abort` is called (just a flag set), but the
  // PyPSA lock isn't released until the solver's HiGHS/Gurobi native
  // code finishes the current iteration. Project-switch flows MUST wait
  // for this to return `lock_held: false` before attempting load.
  // Returns instantly thanks to non-blocking `lock.acquire(blocking=False)`
  // — safe to poll every 500 ms without measurable backend load.
  getLockStatus: (timeoutMs: number) =>
    client.get<{ lock_held: boolean; worker_alive: boolean }>(
      '/simulation/lock_status', { timeout: timeoutMs },
    ).then(r => r.data),
  // Replay buffer of solver log lines. Backend keeps a ring of ~5k lines per
  // solve so the frontend can reconstruct the log after a page reload or SSE
  // reconnect (the in-memory Zustand log store doesn't persist across page
  // refreshes). `running` mirrors /status.running for a single round-trip.
  getLogHistory: () =>
    client.get<{ lines: string[]; running: boolean }>('/simulation/log_history').then(r => r.data),
}

export interface CostBreakdown {
  // capex           — annualised cost of ALL installed capacity
  // capex_expansion — annualised cost of NEW capacity built this run
  // capex_lifetime  — same as capex but × per-asset lifetime (sum over assets)
  // capex_expansion_lifetime — same as capex_expansion × per-asset lifetime
  // The "_lifetime" fields back the "Total over lifetime" toggle on the
  // CapacityExpansion tab. OPEX stays per-year — multiplying it by lifetime
  // would mix construction cost with operating cost and break LCOE intuition.
  capex: number
  capex_lifetime: number
  capex_expansion: number
  capex_expansion_lifetime: number
  opex: number
  total: number
  // Σ curtailment_t × curtailment_cost over renewables that opted in.
  // Already weighting-aware (snapshot × period years). Zero unless the
  // user set curtailment_cost > 0 on a renewable generator.
  curtailment_cost: number
  // CAPEX-expansion bucket for StorageUnit + Store only — quick "how much
  // of the new investment goes into storage" KPI for the cost overview.
  storage_capex_expansion: number
  storage_capex_expansion_lifetime: number
  by_component: Array<{
    component: string
    capex: number
    capex_lifetime: number
    capex_expansion: number
    capex_expansion_lifetime: number
    opex: number
    total: number
  }>
  by_carrier:   Array<{ component: string; carrier: string; capex: number; opex: number; total: number }>
  // Multi-period only: per-period roll-up of capex + opex with the per-period
  // investment_period_weightings.years multiplier baked in. Sum across this
  // list equals the top-level `capex` / `opex` numbers. Single-period
  // networks emit an empty list.
  by_period: Array<{
    period: number | string
    capex: number
    opex: number
    total: number
    by_component: Array<{ component: string; capex: number; opex: number }>
    // Per-carrier breakdown WITHIN the period. Lets the Dispatch tab's
    // "OPEX by carrier" section respect the period selector (when a
    // specific period is picked, use this; otherwise fall back to the
    // horizon-wide `by_carrier` at the root).
    by_carrier?: Array<{ carrier: string; capex: number; opex: number }>
  }>
}

// Per-asset CAPEX inputs.
//   capital_cost     — annualised cost per unit (LP-objective value).
//   overnight_cost   — nominal upfront lump-sum per unit (PyPSA's
//                       `comp.overnight_cost`, either as-typed or back-
//                       calculated). Same value for every asset
//                       regardless of when it's built.
//   overnight_cost_pv — present value of overnight_cost, discounted from
//                       the asset's build_year back to the model's
//                       reference year (= min build_year across assets)
//                       at the per-asset discount_rate. For year-0 builds
//                       this equals overnight_cost. The "Total investment"
//                       toggle uses this so future-year capex is shown
//                       in today's money — e.g. a 2035 build at 7% looks
//                       smaller than a 2025 build of the same nominal €/MW.
//   lifetime         — years, kept for tooltips / CSV.
//   build_year       — optional; only present when the asset carries one.
export type AssetCostMap = Record<string, Record<string, {
  capital_cost: number
  overnight_cost: number
  overnight_cost_pv: number
  lifetime: number
  build_year?: number
}>>

// Result-source: which result set to read for time-series endpoints.
// Forwarded as `?source=lopf|ac_pf` so the backend's helper `_result_df`
// picks the right snapshot. Defaults to 'lopf' so calls without an explicit
// arg keep current behaviour. Exported — CanvasResultsContext.tsx imports
// this rather than redeclaring a duplicate local type.
export type ResultSource = 'lopf' | 'ac_pf'

export interface TSRange { from: number; to: number }

/**
 * Query params for a time-series result request.
 *
 * Omitting `range` produces exactly the request shape that existed before
 * ranges: no `from`, no `to`, and a response with no `range` block. That is
 * what lets unconverted callers keep working untouched.
 */
const tsParams = (s?: ResultSource, range?: TSRange) => {
  const params: Record<string, string | number> = {}
  if (s) params.source = s
  if (range) { params.from = range.from; params.to = range.to }
  return Object.keys(params).length > 0 ? { params } : undefined
}

// Per-period entry in the LCOH payload. One per investment period in
// multi-period runs (flat runs return `by_period: undefined`).
export interface LcohPeriodEntry {
  period: number
  h2_produced_mwh: number
  capex_eur: number
  vom_cost_eur: number
  electricity_cost_eur: number
  lcoh_eur_per_mwh_h2: number | null
  lcoh_eur_per_kg_h2: number | null
}

// ── GET/POST /results/mc — the sequential-MC study (adequacy spec §4/§5) ────
//
// A SIBLING payload, deliberately not folded into AdequacyReport: the MC is an
// engine-local study like the COPT, so its keys mirror
// `services/adequacy/mc.py`'s metrics dict and `services/adequacy/elcc.py`'s
// nine-key row VERBATIM. Renaming anything here would fork the contract.

/** §2.5 metrics dict. Both intervals arrive as 2-element lists over JSON. */
export interface McMetrics {
  lole_hours: number
  /** [lo, hi] — an INTERVAL, not a half-width. May be asymmetric. */
  lole_ci: [number, number] | null
  eue_mwh: number
  eue_ci: [number, number] | null
  by_period?: Record<string, { lole_hours: number; eue_mwh: number }>
  n_samples: number
  converged?: boolean
  /** "hours_per_year" only when the modelled horizon really is a year. */
  time_basis: string
  horizon_years?: number | null
  /**
   * Smallest NONZERO LOLE this many draws can resolve, in the SAME units as
   * `lole_hours`. `null` when the horizon carries no positive weight — a
   * horizon of unknown length cannot state a floor, and saying so beats
   * printing an infinity.
   */
  resolution_floor_h: number | null
  warning?: string
}

/** One ELCC row — nine keys, always all present (spec §3, [v1.2]). */
export interface ElccRow {
  kind: string
  name: string
  nameplate_mw: number
  /** null on every non-"ok" status; `reason` carries the refusal instead. */
  elcc_mw: number | null
  elcc_share: number | null
  status: 'ok' | 'unidentifiable' | 'not_bracketed'
  /** null iff status === "ok". */
  reason: string | null
  baseline_lole_h: number
  baseline_lole_ci: [number, number]
}

export interface McResult {
  engine: string
  fidelity: string
  metrics: McMetrics
  elcc: ElccRow[]
  /** MC_WARNING_V1, shipped with every payload — render it, never inline it. */
  warning: string
}

export interface McStatus {
  status: 'running' | 'done' | 'failed'
  result: McResult | null
  error: string | null
  started_at?: number
  finished_at?: number
}

export interface McRequestBody {
  draws?: number
  seed?: number
  cov_target?: number
  elcc_assets?: Array<{ kind: string; name: string }>
}

/**
 * One row of GET /results/mc/elcc_candidates — an asset the study may be asked
 * to price. `kind` is the ELCC kind, NOT a component class: an electrical
 * generator is `"generator"` when it carries occurrence data and `"vre"` when
 * it does not (must-take, netted into the residual), and the removal semantics
 * differ. `nameplate_mw` is the bracket top the bisection actually prices —
 * capacity for a unit, p_nom for a store, and the PEAK must-take contribution
 * (profile × capacity) for a vre asset, which is why it can be well below the
 * installed capacity.
 */
export interface ElccCandidate {
  kind: string
  name: string
  nameplate_mw: number
}

export interface ElccCandidatesPayload {
  /** Sorted by nameplate descending, ties by name. Possibly empty. */
  assets: ElccCandidate[]
  /** services/adequacy/elcc.py MAX_ELCC_ASSETS — never hardcode it here. */
  max_assets: number
}

// ── GET/POST /results/coupling_loop — the adequacy-coupled planning loop ────
//
// Phase 7. Solve the LP under an energy cap, run the sequential MC on the PLAN
// it produced, retune the cap, re-solve — until the plan meets the user's
// target on the MC's own LOLE rather than on the LP proxy's shed energy.
//
// Keys are copied VERBATIM from routers/results.py `post_coupling_loop`'s
// record and services/adequacy/coupling.py's `_row` / `_mc_block`. Renaming
// one here would fork the contract silently, and this payload is the only
// place several of these quantities exist at all.
//
// NOTE the deliberate absence of a top-level `engine` / `fidelity` pair
// ([N4]): the study's product is a CAP and a VERDICT, not a metric, so the
// sibling convention would misdescribe it. The engine labels live on each
// iterate's own `mc` block, which is where a metric actually is.

/** One iterate's MC evaluation — a PROJECTION of mc_adequacy's dict. */
export interface CouplingMcBlock {
  engine: string
  fidelity: string
  lole_hours: number
  /** [lo, hi] — an INTERVAL, not a half-width. May be asymmetric. */
  lole_ci: [number, number] | null
  eue_mwh: number
  eue_ci: [number, number] | null
  n_samples: number
  /** Rides on every evaluated iterate: on a multi-period network it is the
   *  only way to see WHICH period drives a miss ([N4]/[N5]). */
  by_period: Record<string, { lole_hours: number; eue_mwh: number }>
}

/** One row of `iterations` — services/adequacy/coupling.py `_row`. */
export interface CouplingIteration {
  eps_permyriad: number
  solve_status: string
  condition: string | null
  /** null on every non-solved iterate — an infeasible solve has no cost. */
  cost_eur: number | null
  ens_mwh: number | null
  cap_mwh: number | null
  binding: string | null
  /** true when the plan hash repeated and the metrics were REUSED, not sampled. */
  plateau: boolean
  /** null when the iterate was never evaluated (failed / infeasible solve). */
  mc: CouplingMcBlock | null
}

export type CouplingLoopStatus =
  | 'running' | 'met' | 'unreachable' | 'budget_exhausted' | 'aborted' | 'failed'

export interface CouplingLoopPayload {
  study: 'coupling_loop'
  status: CouplingLoopStatus
  /** HORIZON-basis hours — the panel converts the user's h/yr entry. */
  target_lole_h: number
  /** "hours_per_year" | "hours_per_horizon" — feeds `basisSuffix`. */
  basis: string
  horizon_years?: number | null
  draws: number
  seed: number
  eps0: number
  max_solves: number
  restore: 'base' | 'final'
  base_restored: boolean
  /** 95% CI upper bound cleared the target. Reported, never iterated for. */
  confident: boolean
  eps_star: number | null
  /** Smallest NONZERO LOLE these draws can resolve; null when unknowable. */
  resolution_floor_h: number | null
  solves_used: number
  /** REBOUND by the worker between iterates, so a mid-run GET sees a prefix
   *  of the next one — the list GROWS while the study runs. */
  iterations: CouplingIteration[]
  final: CouplingIteration | null
  /** A ready sentence ([N6]/v1.3 §4) — RENDER IT, never re-word it here. */
  verdict: string | null
  warning: string
  error: string | null
  started_at?: number
  finished_at?: number | null
}

export interface CouplingLoopRequestBody {
  /** REQUIRED and horizon-basis. The h/yr → horizon conversion is the
   *  panel's job (plan [S12]); the wire stays unit-safe. */
  target_lole_h: number
  draws?: number
  seed?: number
  eps0?: number
  max_solves?: number
  restore?: 'base' | 'final'
}

// ── GET/POST /results/margin_loop — the SAME loop on the OTHER lever ────────
//
// Phase 9 (margin-loop spec §2.6). The controller is `coupling.py`, unchanged
// and unmodifiable; only the lever differs. The route substitutes
// `x = 1/(1+m)` so the controller's "smaller is stricter" ordering holds for a
// reserve MARGIN, and translates every row back to a margin before storing it
// — so the controller's internal `x` never reaches this file at all.
//
// ★ THE PAYLOADS ARE NOT THE SAME SHAPE and no alias joins them. The cap loop
// carries `eps_permyriad` / `eps0` / `eps_star`; this one carries
// `lever_value` / `margin0` / `lever_star` plus `probe_solves`, `margin_tight`
// and `margin_ceiling` (amendment v1.1(5)). A "nullable alias" that let one
// row type stand for both would put a `null` where the panel's `compact()` is
// typed `number` — and `isFinite(null)` is TRUE in JS, so the guard passes it
// through to `.toPrecision(2)`, which throws inside `rows.map` and unmounts
// the panel. Both value types below are NON-NULLABLE for that reason.
//
// Keys are copied VERBATIM from routers/results.py `post_margin_loop`'s
// record and its `_translate`.

/** One row of `iterations` — routers/results.py `post_margin_loop._translate`.
 *
 *  The controller's `_row` with `eps_permyriad` REPLACED by `lever_value`.
 *  There is no `eps_permyriad` key on the wire and there must be none here. */
export interface MarginIteration {
  /** The planning reserve margin as a FRACTION (0.15 = 15%), never `x` and
   *  never a per-myriad cap. Always a number: the route translates every row
   *  it stores, so a row without one is a row that does not exist. */
  lever_value: number
  solve_status: string
  condition: string | null
  /** null on every non-solved iterate — an infeasible solve has no cost. */
  cost_eur: number | null
  ens_mwh: number | null
  /** ALWAYS null for this lever (spec §2.2): the margin has no energy cap,
   *  and the route returns `cap_mwh=None` deliberately so the controller's
   *  ENERGY_FLOOR test stays a genuine no-op instead of ending every run
   *  `unreachable` after one solve. */
  cap_mwh: number | null
  binding: string | null
  /** true when the plan hash repeated and the metrics were REUSED. */
  plateau: boolean
  /** null when the iterate was never evaluated (failed / refused solve). */
  mc: CouplingMcBlock | null
}

export type MarginLoopStatus = CouplingLoopStatus

export interface MarginLoopPayload {
  study: 'margin_loop'
  /** ★ THE DISCRIMINATOR (spec §3). The solver-config FIELD this study
   *  writes — `restoreSentence` takes its field name from here, so a margin
   *  run can never tell the user to set the energy cap's field. */
  lever: string
  /** Human copy for the column header, e.g. "planning reserve margin". */
  lever_label: string
  /** The badge/column suffix — "%" here, "‱" on the cap loop. */
  lever_unit: string
  status: MarginLoopStatus
  /** HORIZON-basis hours — the panel converts the user's h/yr entry. */
  target_lole_h: number
  basis: string
  horizon_years?: number | null
  draws: number
  seed: number
  /** The margin the search STARTED from — measured by the probing solve
   *  (spec §2.3), never a user parameter. Null until the probe lands. */
  margin0: number | null
  /** The smallest margin at which the incumbent plan is already tight. */
  margin_tight: number | null
  /** The largest margin the fleet can reach; null = unbounded. */
  margin_ceiling: number | null
  max_solves: number
  restore: 'base' | 'final'
  base_restored: boolean
  confident: boolean
  /** The CERTIFIED MARGIN (a fraction), not `x` and not a cap. */
  lever_star: number | null
  resolution_floor_h: number | null
  solves_used: number
  /** The probing solve is OUTSIDE the controller's budget (amendment
   *  v1.1(5)): folding it into `solves_used` would break the budget's
   *  meaning, hiding it would misreport the wall-clock. */
  probe_solves: number
  /** REBOUND by the worker between iterates — the list GROWS mid-run. */
  iterations: MarginIteration[]
  final: MarginIteration | null
  /** A ready sentence — RENDER IT, never re-word it here. */
  verdict: string | null
  warning: string
  error: string | null
  started_at?: number
  finished_at?: number | null
}

export interface MarginLoopRequestBody {
  /** REQUIRED and horizon-basis; the h/yr conversion is the panel's job. */
  target_lole_h: number
  draws?: number
  seed?: number
  max_solves?: number
  restore?: 'base' | 'final'
  // NO `m0`. The starting margin is a MEASUREMENT, not a parameter
  // (`MarginLoopRequest` in routers/results.py refuses to take one): too
  // small and the search walks a region where the plan does not change, too
  // large and it overshoots the bracket entirely.
}

// ── The firm-capacity (planning reserve margin) standard, Phase 8 §4 ────────
//
// KEY NAMES ARE VERBATIM from the backend and must stay that way: they come
// from `services/adequacy/report.py::reserve_margin_payload` and its
// `sanitize_reserve_margin_payload` wire pass, and the identical shape is
// published a second time as `AdequacyReport.reserve_margin` (amendment
// v1.2(7)) so the two surfaces cannot drift. The panel is the only reader, so
// a rename here would fork the contract with nothing going red.

/** One derating row — per (asset, PERIOD): a must-take credit is measured over
 *  that period's peak hours, so the same unit derates differently per period
 *  (amendment v1.1(3)). */
export interface ReserveMarginAsset {
  name: string
  period: string
  kind: 'generator' | 'storage'
  /** The BUILT capacity in the solved plan — `p_nom_opt` for an extendable
   *  (which is the point of the standard: the capacity it forced into being),
   *  the fixed constant otherwise. `null` when the solve has no number for it. */
  capacity_mw: number | null
  /** (1 − outage rate) × availability, clamped to [0, 1]. A PROXY — see
   *  `basis`/`source`, which are what make it inspectable. */
  derate: number
  /** "FOR" | "EFORd" — never silently converted. `1 − FOR` is not a UCAP
   *  derate: FOR excludes reserve-shutdown hours and is optimistic exactly
   *  for the peakers that sit at the margin. */
  basis: string
  /** "asset" (the user entered it) | "carrier_default" (a class average they
   *  did not) | "missing". */
  source: string
  extendable: boolean
  /** derate × capacity_mw. */
  firm_mw: number
  /** A reservoir takes full POWER credit while its ENERGY limit is what binds
   *  it — recorded, not fixed (plan §1.4). */
  energy_limited: boolean
}

/** One investment period's standard and what met it. */
export interface ReserveMarginPeriod {
  period: string
  /** Unweighted MW maximum of electrical demand — never a weighted sum. */
  peak_mw: number
  required_mw: number
  firm_mw: number
  /** firm_mw / peak_mw − 1; null when the period has no demand. */
  margin_achieved: number | null
  /** The plan REACHES the standard. */
  met: boolean
  /** The standard SHAPED the plan — firm capacity on the bound. SEPARATE from
   *  `met` (amendment v1.2(5)): a margin the fixed fleet already satisfies is
   *  met and NOT binding, and reporting it as binding would credit the margin
   *  for capacity that was always there. */
  binding: boolean
  /** N, the number of peak hours the must-take credit was measured over. */
  n_peak_hours: number
  /** The selected timestamps — longer than N when snapshots tie. Published
   *  because a proxy nobody can inspect is a number nobody can check. */
  peak_snapshots: string[]
  /** null when an active extendable has an unbounded `p_nom_max`: "unbounded"
   *  is not a number and `inf` is not JSON (amendment v1.2(4)). The flag below
   *  says which case the null is — a clamp would have invented a ceiling
   *  nobody entered. */
  max_achievable_mw: number | null
  max_achievable_unbounded: boolean
}

export interface ReserveMarginPayload {
  /** Fraction: 0.15 == 15 %. */
  margin: number
  /** True when the periods share ONE `Generator-p_nom` variable set and the
   *  system degenerates to a single standard at `max_P peak_P`. Calling that
   *  "per period" would be a claim the constraint does not support. */
  horizon_wide: boolean
  by_period: ReserveMarginPeriod[]
  assets: ReserveMarginAsset[]
  /** basis → how many credited assets carried it. */
  derating_bases: Record<string, number>
}

export const resultsApi = {
  getCostBreakdown: () => client.get<CostBreakdown>('/results/cost_breakdown').then(r => r.status === 204 ? null : r.data),
  getStatistics: () => client.get('/results/statistics').then(r => r.status === 204 ? null : r.data),
  getGeneratorResults: (source?: ResultSource, range?: TSRange) => client.get('/results/generators', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  getStorageResults: (source?: ResultSource, range?: TSRange) => client.get('/results/storage', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  // StorageUnit / Store power-flow time series (signed MW; positive = discharge,
  // negative = charge). Frontend splits these into "production" and "consumption"
  // for display, mirroring how generators/loads are visualised in the Dispatch tab.
  getStorageDispatchResults: (source?: ResultSource, range?: TSRange) => client.get('/results/storage_dispatch', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  getStoreDispatchResults:   (source?: ResultSource, range?: TSRange) => client.get('/results/store_dispatch',   tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  getStoreEnergyResults:     (source?: ResultSource, range?: TSRange) => client.get('/results/store_energy',     tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  getLineResults: (source?: ResultSource, range?: TSRange) => client.get('/results/lines', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  // Per-link p0 (signed MW): positive = bus0 → bus1. Drives the Links
  // section of the Dispatch tab, grouped by link carrier (DC, H2, electrolyser, …).
  getLinkResults: (source?: ResultSource, range?: TSRange) => client.get('/results/links', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  // Per-electrolyser LCOH (€/MWh_H2 and €/kg_H2). One row per electrolyser-
  // like link plus a fleet-aggregated `total`. 204 / empty rows when no
  // qualifying links exist or the run isn't solved.
  getLcoh: () => client.get<{
    rows: Array<{
      name: string
      carrier: string
      p_nom_opt_mw: number
      efficiency: number
      capex_eur_per_year: number
      vom_cost_eur: number
      electricity_cost_eur: number
      h2_produced_mwh: number
      lcoh_eur_per_mwh_h2: number | null
      lcoh_eur_per_kg_h2: number | null
      by_period?: Array<LcohPeriodEntry>
    }>
    total: null | {
      h2_produced_mwh: number
      capex_eur_per_year: number
      vom_cost_eur: number
      electricity_cost_eur: number
      lcoh_eur_per_mwh_h2: number
      lcoh_eur_per_kg_h2: number
      by_period?: Array<LcohPeriodEntry>
    }
    currency: string
  }>('/results/lcoh').then(r => r.status === 204 ? null : r.data),
  getTransformerResults: (source?: ResultSource, range?: TSRange) => client.get('/results/transformers', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  getPrices: (source?: ResultSource, range?: TSRange) => client.get('/results/prices', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  // Per-generator unit-commitment results (status, start/shut counts, on-hours,
  // capacity factor when on, UC costs). Populated only when committable=True
  // on at least one generator; otherwise n_committable=0 and the panel hides.
  // status_grid is the binary on/off matrix (snapshot × committable_gen) used
  // by the heatmap visualisation.
  getUnitCommitment: (range?: TSRange) =>
    client.get<{
      generators: Array<{
        name: string; carrier: string; p_nom_MW: number
        n_starts: number; n_shuts: number
        hours_on: number; energy_mwh: number
        capacity_factor_when_on_pct: number
        total_uc_cost_eur: number
      }>
      status_grid: { index: string[]; columns: string[]; data: number[][] } | null
      n_committable: number
      note?: string
    }>('/results/unit_commitment', tsParams(undefined, range)).then(r => r.status === 204 ? null : r.data),
  // Per-line congestion shadow prices (€/MWh) from the LP duals. Captures
  // binding_hours / max_mu / mean_mu_when_binding / congestion_rent_eur.
  // Requires assign_all_duals=True at solve time (backend default).
  getLineDuals: () =>
    client.get<{
      rows: Array<{
        name: string
        s_nom_MW: number
        binding_hours: number
        // Subset of binding_hours where |mu| ≥ 10,000 €/MWh — typically
        // VOLL-priced load shedding, not physical congestion. UI surfaces
        // separately so users don't mistake it for transmission scarcity.
        voll_bound_hours: number
        max_mu_eur_per_MWh: number
        mean_mu_when_binding_eur_per_MWh: number
        congestion_rent_eur: number
      }>
      total_congestion_rent_eur: number
      n_snapshots: number
      note?: string
    }>('/results/line_duals').then(r => r.status === 204 ? null : r.data),
  // Per-carrier KPIs (capacity factor / curtailment / market value / revenue /
  // energy / capacity) for Generator + StorageUnit + Store + Link. Wraps
  // PyPSA's n.statistics helpers groupby='carrier'. Decimal CF returned as
  // percent (× 100). curtailment_pct is computed against (energy + curtailment).
  getCarrierKpis: () =>
    client.get<{
      rows: Array<{
        component: string
        carrier: string
        capacity_mw: number
        energy_mwh: number
        capacity_factor_pct: number
        curtailment_mwh: number
        curtailment_pct: number
        market_value_eur_per_mwh: number
        revenue_eur: number
      }>
    }>('/results/carrier_kpis').then(r => r.status === 204 ? null : r.data),
  // Per-carrier + per-generator CO₂ emissions over the solved horizon, plus
  // the shadow price of any active primary-energy CO₂ cap. See backend
  // docstring for the computation (Σ p·weight × co2/efficiency).
  getEmissions: () =>
    client.get<{
      total_tCO2: number
      by_carrier: Array<{ carrier: string; tCO2: number; share_pct: number }>
      by_generator: Array<{
        name: string; carrier: string; energy_mwh: number; tCO2: number
        intensity_tCO2_per_MWh_out: number
        component?: string  // present for StorageUnit / Store contributors
      }>
      // Legacy single-cap field — first active cap. Kept for older
      // consumers; new code should iterate `caps[]` to handle per-period
      // caps and horizon-wide caps in one pass.
      cap: {
        active: boolean
        name?: string
        cap_tCO2?: number | null
        shadow_price_eur_per_tCO2?: number
        slack_tCO2?: number | null
      }
      // All active primary_energy + co2_emissions constraints. `scope` =
      // 'period' when investment_period is set on the constraint, 'horizon'
      // otherwise. `binding` is true when slack ~= 0 (the cap is tight).
      caps: Array<{
        active: true
        name: string
        investment_period: number | null
        scope: 'period' | 'horizon'
        cap_tCO2: number | null
        used_tCO2: number
        shadow_price_eur_per_tCO2: number
        slack_tCO2: number | null
        binding: boolean
      }>
      is_multi_period: boolean
      // Per-investment-period breakdown. Empty on flat (single-period)
      // networks. Each entry mirrors the top-level shape but scoped to
      // one period.
      by_period: Array<{
        period: number | string
        total_tCO2: number
        by_carrier: Array<{ carrier: string; tCO2: number; share_pct: number }>
        by_generator: Array<{
          name: string; carrier: string; energy_mwh: number; tCO2: number
          intensity_tCO2_per_MWh_out: number; component?: string
        }>
      }>
    }>('/results/emissions').then(r => r.status === 204 ? null : r.data),
  // AC-PF-only result series. The LP stage doesn't compute v_mag_pu or q —
  // these endpoints return null (204) when no Stage 2 snapshot is available.
  // Frontend uses the null to hide the corresponding LoadFlow sections.
  getVoltages: (source?: ResultSource, range?: TSRange) =>
    client.get('/results/voltages', tsParams(source ?? 'ac_pf', range)).then(r => r.status === 204 ? null : r.data),
  getLineReactive: (source?: ResultSource, range?: TSRange) =>
    client.get('/results/line_reactive', tsParams(source ?? 'ac_pf', range)).then(r => r.status === 204 ? null : r.data),
  getTransformerReactive: (source?: ResultSource, range?: TSRange) =>
    client.get('/results/transformer_reactive', tsParams(source ?? 'ac_pf', range)).then(r => r.status === 204 ? null : r.data),
  // Per-cell diagnosis for prices above a threshold — used by the Load Flow
  // "Price drivers" panel to answer "why is the price 3000 at hour X?".
  // Returns the most-likely marginal generator + a one-word category
  // (load_shedding / thermal_peaker / transmission / unattributed).
  getPriceDrivers: (threshold = 2000, limit = 200) =>
    client.get<{
      threshold: number; total_above_threshold: number; truncated: boolean
      rows: Array<{
        snapshot: string; bus: string; price: number
        marginal_gen: string | null; marginal_cost: number; carrier: string
        dispatch: number; voll_slack_active: boolean; voll_dispatch: number
        diagnosis: 'load_shedding' | 'thermal_peaker' | 'transmission' | 'unattributed'
      }>
    }>(`/results/price_drivers`, { params: { threshold, limit } })
      .then(r => r.status === 204 ? null : r.data),
  // No `source` (LP-only, see `getUnitCommitment`) — `range` is the first
  // param. Every caller that referenced this bare as a `queryFn`
  // (AggregatedOverview.tsx, Dispatch.tsx) has been updated to wrap it in
  // an arrow (`() => resultsApi.getCurtailment()`); calling with no
  // arguments still produces a byte-identical request to before.
  getCurtailment: (range?: TSRange) => client.get('/results/curtailment', tsParams(undefined, range)).then(r => r.status === 204 ? null : r.data),
  // Per-carrier economic roll-up on the LIVE in-memory network. Powers the
  // per-carrier KPI strips in the Results / Dispatch tab. Shape mirrors
  // `CarrierEconomics` in Compare View (revenue_meur, opex_meur split into
  // gen_cost / storage_charge_cost / curtailment_cost / lost_load_cost,
  // capex_meur, dispatch_gwh, lcoe_eur_per_mwh).
  getEconomicsByCarrier: () => client.get<{
    by_carrier?: Record<string, {
      revenue_meur: { total: number; by_period: Record<string, number> }
      opex_meur:    { total: number; by_period: Record<string, number> }
      gen_cost_meur?: { total: number; by_period: Record<string, number> }
      storage_charge_cost_meur?: { total: number; by_period: Record<string, number> }
      curtailment_cost_meur?:    { total: number; by_period: Record<string, number> }
      lost_load_cost_meur?:      { total: number; by_period: Record<string, number> }
      capex_meur:   { total: number; by_period: Record<string, number> }
      dispatch_gwh: { total: number; by_period: Record<string, number> }
      lcoe_eur_per_mwh: { total: number; by_period: Record<string, number> }
    }>
    error?: string
  }>('/results/economics_by_carrier').then(r => r.status === 204 ? null : r.data),
  getLoadResults: (source?: ResultSource, range?: TSRange) => client.get('/results/loads', tsParams(source, range)).then(r => r.status === 204 ? null : r.data),
  // VOLL slack-generator dispatch — only populated when the solver ran with
  // voll > 0 AND the LP shed any load. Returns TSPayload + totals or null.
  // `voll_eur_per_mwh` is the per-MWh VOLL the solver used; consumers
  // should prefer this over re-deriving it via cost/mwh division (which
  // breaks at zero MWh).
  // No `source` (LP-only) — `range` is the first param. Every bare-reference
  // caller (AggregatedOverview.tsx, Dispatch.tsx) has been updated to wrap
  // this in an arrow; calling with no arguments is still byte-identical to
  // before.
  // Minimal AdequacyReport from the last target-constrained solve
  // (GET /results/adequacy; 204 = no target / not solved). Shape:
  // pages/results/adequacy.tsx AdequacyReportPayload.
  getAdequacy: () => client.get('/results/adequacy')
    .then(r => (r.status === 204 ? null : r.data)),
  // COPT screening adequacy + FMECA ranking (Phase 2; 204 = no occurrence
  // data). Shape: pages/results/adequacy.tsx CoptPayload.
  getCopt: () => client.get('/results/copt')
    .then(r => (r.status === 204 ? null : r.data)),
  // The firm-capacity (planning reserve margin) standard the last solve
  // enforced (Phase 8 §4; 204 = nothing solved, no margin set, or no dispatch
  // to judge one against). The endpoint serves the PERSISTED solve-time stash
  // and never a recomputation — the wrapper measured its peaks with the
  // load-scaling transforms applied and the restore has since reverted them.
  // Shape: `ReserveMarginPayload` above.
  getReserveMargin: () => client.get('/results/reserve_margin')
    .then(r => (r.status === 204 ? null : r.data as ReserveMarginPayload)),
  // Cost-vs-availability frontier (Phase 5; 204 = no study run this session).
  // Shape: pages/results/FrontierPanel.tsx FrontierPayload.
  getFrontier: () => client.get('/results/frontier')
    .then(r => (r.status === 204 ? null : r.data)),
  // Starts the epsilon-constraint study in a backend worker thread and
  // returns immediately; poll getFrontier for progress. Omitting targets uses
  // the backend's default spread.
  startFrontier: (targets_permyriad?: number[]) =>
    client.post('/results/frontier',
      targets_permyriad ? { targets_permyriad } : {}).then(r => r.data),
  // Sequential Monte-Carlo adequacy study, optionally with an ELCC table
  // (Phase 6; 204 = no study run this session). Shape: `McStatus` below.
  getMc: () => client.get('/results/mc')
    .then(r => (r.status === 204 ? null : r.data as McStatus)),
  // Starts the sampler in a backend worker thread and returns immediately;
  // poll getMc for progress. A bare POST is the useful default — every field
  // has an engine-side default this client must not fork, so `{}` is sent
  // rather than a hand-rolled set of frontend defaults. Rejects with the
  // axios error on 409 (a solve/sweep/frontier/MC is running); the detail
  // string NAMES the blocker and the panel renders it (see McPanel's
  // `blockerMessage`).
  startMc: (body?: McRequestBody) =>
    client.post('/results/mc', body ?? {}).then(r => r.data),
  // The assets an ELCC study may be asked for, for McPanel's picker. Always
  // 200 — an EMPTY `assets` list is an answer ("nothing in this network has a
  // capacity credit that could be measured"), not a 204, so there is no null
  // case to unwrap here. Membership agrees by construction with what
  // `startMc` accepts as `elcc_assets`, which is the whole reason the endpoint
  // exists: a name the picker offers and the run 404s on would be worse than
  // no picker at all.
  getElccCandidates: () => client.get('/results/mc/elcc_candidates')
    .then(r => r.data as ElccCandidatesPayload),
  // The adequacy-coupled planning loop (Phase 7; 204 = no loop has been run in
  // this session). While the worker runs, the SAME record is served with
  // `status: "running"` and an `iterations` list that GROWS between polls —
  // that is the whole point of the surface, since a run is up to eight full
  // capacity expansions plus an MC evaluation each.
  getCouplingLoop: () => client.get('/results/coupling_loop')
    .then(r => (r.status === 204 ? null : r.data as CouplingLoopPayload)),
  // Starts the loop in a backend worker thread and returns immediately; poll
  // getCouplingLoop for progress. `target_lole_h` is REQUIRED and is the
  // horizon-basis number (LoopPanel's `wireTarget` does the conversion) —
  // every other field has an engine-side default this client must not fork.
  // Rejects with the axios error on 409 (a solve/sweep/frontier/MC/loop is
  // running) and on the route's 422 set; the detail string NAMES the blocker
  // and the panel renders it through McPanel's `blockerMessage`.
  startCouplingLoop: (body: CouplingLoopRequestBody) =>
    client.post('/results/coupling_loop', body).then(r => r.data),
  // Asks a running loop to stop. IDEMPOTENT and 200 even when the run is
  // already finishing: the controller checks the stop event between iterates,
  // so an abort costs at most the iterate in flight and the closing restore
  // still runs. 404 only when no loop has ever been recorded.
  abortCouplingLoop: () => client.post('/results/coupling_loop/abort')
    .then(r => r.data),
  // The MARGIN-driven loop (Phase 9; 204 = no margin loop has been run in
  // this session). A SEPARATE study key from the coupling loop — both are in
  // the 409 mesh in both directions — serving its own record with its own
  // growing `iterations` list.
  getMarginLoop: () => client.get('/results/margin_loop')
    .then(r => (r.status === 204 ? null : r.data as MarginLoopPayload)),
  // Starts the margin loop in a backend worker thread and returns
  // immediately; poll getMarginLoop for progress. `target_lole_h` is REQUIRED
  // and horizon-basis. Rejects with the axios error on 409 (a
  // solve/sweep/frontier/MC/coupling-loop/margin-loop is running) and on the
  // route's 422 set — the unreachable ceiling and the unpriceable-asset
  // refusal both name themselves in the detail, which the panel renders
  // through McPanel's `blockerMessage`.
  startMarginLoop: (body: MarginLoopRequestBody) =>
    client.post('/results/margin_loop', body).then(r => r.data),
  // Asks a running margin loop to stop. IDEMPOTENT and 200 even when the run
  // is already finishing; 404 only when no margin loop has ever been
  // recorded. NOT folded into the coupling loop's abort: that route's stop
  // event belongs to a different record, and pressing it would report success
  // while this loop kept solving.
  abortMarginLoop: () => client.post('/results/margin_loop/abort')
    .then(r => r.data),
  // FMEA worksheet sidecar (Phase 3): manual class-D rows + mitigability
  // overlays, persisted per project. Computed rows come from getCopt and
  // merge client-side (pages/results/fmea.ts).
  getWorksheet: (project: string) =>
    client.get(`/projects/${encodeURIComponent(project)}/worksheet`).then(r => r.data),
  putWorksheet: (project: string, body: {
    manual_rows: Array<Record<string, unknown>>
    overlays: Record<string, { mitigability?: string; notes?: string }>
  }) => client.put(`/projects/${encodeURIComponent(project)}/worksheet`, body).then(r => r.data),
  // All computed failure-mode rows (A + last sweep's B/C) on one list.
  getFmeaModes: () => client.get('/results/fmea_modes')
    .then(r => (r.status === 204 ? null : r.data)),
  // Contingency sweep lifecycle (class B links + class C scenarios).
  getFmeaSweep: () => client.get('/results/fmea_sweep')
    .then(r => (r.status === 204 ? null : r.data)),
  postFmeaSweep: (scenarios: Array<Record<string, unknown>>) =>
    client.post('/results/fmea_sweep', { scenarios }).then(r => r.data),
  getStressScenarios: (project: string) =>
    client.get(`/projects/${encodeURIComponent(project)}/stress_scenarios`).then(r => r.data),
  getLostLoad: (range?: TSRange) => client.get<{
    index: string[]; columns: string[]; data: number[][];
    total_mwh: number; total_cost_eur: number;
    voll_eur_per_mwh: number;
    // Weighted loss-of-load hours, electrical buses, horizon scope (not the
    // sliced range). Absent on payloads from older backends.
    shed_hours?: { total: number; by_period: Record<string, number> };
    // Per-bus carrier ("AC" / "H2" / "heat" / …). Solver adds VOLL slacks on
    // every bus, so lost load is captured for ALL energy carriers; this map
    // lets the frontend split the total per carrier.
    bus_carriers?: Record<string, string>
    // Multi-period: parallel array of period years for each `index` entry.
    periods?: number[]
  }>('/results/lost_load', tsParams(undefined, range)).then(r => r.status === 204 ? null : r.data),
  // Transmission-losses summary. `enabled=false` ⇒ the solve didn't model
  // losses, in which case totals/peak are 0 (intentional: the LoadFlow tab
  // renders "0 MWh" instead of a not-solved placeholder). For source='ac_pf',
  // losses are computed post-hoc from p0+p1 (real losses) — meaningful even
  // when transmission_losses was off during Stage 1.
  getLosses: (source?: ResultSource) => client.get<{
    enabled: boolean
    total_mwh: number
    peak_mw: number
    total_demand_mwh: number
    loss_pct_of_demand: number
    by_branch: Array<{ component: string; name: string; loss_mwh: number; peak_mw: number; share_pct: number }>
  }>('/results/losses', tsParams(source)).then(r => r.status === 204 ? null : r.data),
  // Stage 2 (AC PF) status. `available=false` when no AC PF has run since
  // the last solve — the frontend hides the result-source toggle in that
  // case. When available, the convergence map drives per-snapshot UI
  // (badges in the picker, banner on the canvas for non-converged hours).
  getAcPfStatus: () => client.get<{
    available: boolean
    slack_bus_used?: string | null
    stripped_voll_slacks?: string[]
    // Legacy `{iso: bool}` dict — ambiguous on multi-period (same iso under
    // different periods collapses). Kept for backward compatibility.
    converged_per_snapshot?: Record<string, boolean>
    // Period-aware list — preferred. Each entry carries the original tuple
    // info so the frontend can match snapshot+period unambiguously.
    converged_list?: Array<{ snapshot: string; period?: number | string | null; ok: boolean }>
    converged_count?: number
    total_snapshots?: number
  }>('/results/ac_pf/status').then(r => r.status === 204 ? null : r.data),
  // Per-asset economics: revenue, fixed/variable cost, net profit, LCOE/LCOS.
  // Computed at the asset level so users can compare profitability across
  // individual generators / storage units / stores. The `by_period` arrays
  // are populated only on multi-period runs (empty list otherwise) — flat
  // single-period horizons collapse to the row's top-level totals.
  getAssetEconomics: () =>
    client.get<AssetEconomicsPayload>('/results/asset_economics')
      .then(r => r.status === 204 ? null : r.data),
}

// ── Asset economics types ──────────────────────────────────────────────
export interface GeneratorEconomicsRow {
  name: string
  bus: string
  carrier: string
  p_nom_opt_mw: number
  energy_mwh: number
  capacity_factor: number | null
  revenue_eur: number
  vom_cost_eur: number
  fixed_cost_eur: number       // capital_cost × p_nom_opt (annualised)
  fom_cost_eur: number         // user-typed fom_cost × p_nom_opt (informational)
  net_profit_eur: number       // revenue − fixed − vom
  lcoe_eur_per_mwh: number | null
  avg_price_eur_per_mwh: number | null
  by_period: Array<{
    period: number | string
    energy_mwh: number
    revenue_eur: number
    fixed_cost_eur: number
    fom_cost_eur: number
    vom_cost_eur: number
    net_profit_eur: number
    lcoe_eur_per_mwh: number | null
    avg_price_eur_per_mwh: number | null
  }>
}
export interface StorageUnitEconomicsRow {
  name: string
  bus: string
  carrier: string
  p_nom_opt_mw: number
  max_hours: number
  energy_capacity_mwh: number
  round_trip_efficiency: number | null
  discharge_mwh: number
  charge_mwh: number
  discharge_revenue_eur: number
  charge_cost_eur: number
  vom_cost_eur: number
  fixed_cost_eur: number
  fom_cost_eur: number
  net_profit_eur: number       // discharge_revenue − charge_cost − vom − fixed
  lcos_eur_per_mwh: number | null
  spread_eur_per_mwh: number | null
  avg_discharge_price_eur_per_mwh: number | null
  avg_charge_price_eur_per_mwh: number | null
  by_period: Array<{
    period: number | string
    discharge_mwh: number
    charge_mwh: number
    discharge_revenue_eur: number
    charge_cost_eur: number
    fixed_cost_eur: number
    fom_cost_eur: number
    vom_cost_eur: number
    net_profit_eur: number
    lcos_eur_per_mwh: number | null
    spread_eur_per_mwh: number | null
  }>
}
export interface StoreEconomicsRow {
  name: string
  bus: string
  carrier: string
  e_nom_opt_mwh: number
  discharge_mwh: number
  charge_mwh: number
  discharge_revenue_eur: number
  charge_cost_eur: number
  vom_cost_eur: number
  fixed_cost_eur: number
  fom_cost_eur: number
  net_profit_eur: number
  lcos_eur_per_mwh: number | null
  spread_eur_per_mwh: number | null
  avg_discharge_price_eur_per_mwh: number | null
  avg_charge_price_eur_per_mwh: number | null
  by_period: StorageUnitEconomicsRow['by_period']
}
// Converters — electrolysers, heat pumps, P2X. A Link BUYS at bus0 and SELLS
// at bus1, so it carries both a gross revenue and the cost of the energy it
// consumed. `revenue_eur` is already NET of that input; `gross_revenue_eur`
// and `input_cost_eur` are the halves, so the table can show the same
// two-sided layout it uses for storage.
export interface LinkEconomicsRow {
  name: string
  bus: string                  // bus0 — where the Link buys
  bus1: string                 // where it delivers
  carrier: string
  efficiency: number | null
  p_nom_opt_mw: number
  energy_mwh: number           // OUTPUT at bus1, not input
  input_energy_mwh: number
  capacity_factor: number | null   // measured on the input, which p_nom bounds
  revenue_eur: number          // gross_revenue − input_cost
  gross_revenue_eur: number
  input_cost_eur: number
  vom_cost_eur: number
  fixed_cost_eur: number
  fom_cost_eur: number
  net_profit_eur: number       // revenue − fixed − vom
  lcoe_eur_per_mwh: number | null  // ALL-IN per MWh of output; matches /results/lcoh
  avg_price_eur_per_mwh: number | null
  by_period: Array<{
    period: number | string
    energy_mwh: number
    revenue_eur: number
    gross_revenue_eur: number
    input_cost_eur: number
    fixed_cost_eur: number
    fom_cost_eur: number
    vom_cost_eur: number
    net_profit_eur: number
    lcoe_eur_per_mwh: number | null
    avg_price_eur_per_mwh: number | null
  }>
}
export interface AssetEconomicsPayload {
  currency: string
  is_multi_period: boolean
  periods: Array<number | string>
  generators: GeneratorEconomicsRow[]
  storage_units: StorageUnitEconomicsRow[]
  stores: StoreEconomicsRow[]
  links: LinkEconomicsRow[]
}

export function createLogStream(
  onMessage: (line: string) => void,
  onDone: (data: Record<string,unknown>) => void,
  onError?: (reason: string) => void,
): () => void {
  const es = new EventSource('/api/simulation/log_stream')
  let doneReceived = false
  let lastEventAt = Date.now()
  // EventSource fires `error` for any transient disconnect — browser sleep,
  // brief network blip, server hiccup. Closing on the first error (the old
  // behaviour) silently stranded the UI in "running" forever. Now: only
  // declare failure if no event has arrived for STALE_MS AND we never got a
  // `done` event. Otherwise let the browser's built-in auto-reconnect work.
  const STALE_MS = 30_000

  es.onmessage = (e) => { lastEventAt = Date.now(); onMessage(e.data) }
  es.addEventListener('done', (e) => {
    doneReceived = true
    lastEventAt = Date.now()
    try { onDone(JSON.parse((e as MessageEvent).data)) } catch { onDone({}) }
    es.close()
  })
  es.onerror = () => {
    if (doneReceived) {
      es.close()
      return
    }
    if (es.readyState === EventSource.CLOSED) {
      onError?.('Log stream lost before solve completed')
      return
    }
    // STALE_MS elapsed with no event. Before declaring the solve dead,
    // verify with /status — long native-code phases (HiGHS solving, AC PF
    // iteration) emit NO log lines and look identical to a hung backend
    // over SSE. Only flip to "failed" if the backend agrees the solve is
    // over. Otherwise reset the stale timer and let EventSource keep
    // auto-reconnecting.
    if (Date.now() - lastEventAt > STALE_MS) {
      simulationApi.getStatus()
        .then(s => {
          if (!s.running) {
            es.close()
            onError?.(`Log stream silent for ${Math.round((Date.now() - lastEventAt) / 1000)}s — solve is no longer running`)
          } else {
            lastEventAt = Date.now()
          }
        })
        .catch(() => {
          es.close()
          onError?.('Log stream silent and /status unreachable — connection lost')
        })
    }
  }
  return () => es.close()
}
