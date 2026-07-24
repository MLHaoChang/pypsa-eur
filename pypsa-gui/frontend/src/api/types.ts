export interface Bus {
  name: string; v_nom: number; carrier: string; x: number; y: number
  country: string; unit: string; control: string; sub_network: string
}
export interface Carrier {
  name: string; co2_emissions: number; color: string; nice_name: string; unit: string
}
export interface Line {
  name: string; bus0: string; bus1: string; length: number; r: number; x: number
  b: number; s_nom: number; s_nom_extendable: boolean; s_nom_min: number
  s_nom_max: number | null; capital_cost: number; fom_cost: number
  overnight_cost: number | null
  // Per-asset discount rate. null ⇒ fall back to solver config's global
  // rate at solve time (PyPSA needs a finite value when overnight_cost is set).
  discount_rate: number | null
  carrier: string
  // PyPSA vintage attributes — same shape as Generator / Link / Storage.
  // Default build_year=0 ("exists from the start"); null lifetime ⇒ infinite.
  // Surfaced so multi-period validators have the same set of knobs across
  // every component class.
  build_year: number
  lifetime: number | null
}
export interface Link {
  name: string; bus0: string; bus1: string; carrier: string; efficiency: number
  p_nom: number; p_nom_extendable: boolean; p_nom_min: number; p_nom_max: number | null
  p_min_pu: number; p_max_pu: number
  marginal_cost: number; capital_cost: number; fom_cost: number
  overnight_cost: number | null
  discount_rate: number | null
  build_year: number; lifetime: number | null
}
export interface Generator {
  name: string; bus: string; carrier: string; p_nom: number; p_nom_extendable: boolean
  p_nom_min: number; p_nom_max: number | null; p_min_pu: number; p_max_pu: number
  // PyPSA AC control mode. Consumed by n.pf() in Stage 2; defaults to 'PQ'.
  control: 'PQ' | 'PV' | 'Slack'
  marginal_cost: number; capital_cost: number; fom_cost: number
  overnight_cost: number | null
  discount_rate: number | null
  // Custom non-PyPSA-native attribute. Solver-time extra_functionality adds
  // a Σ curtailment_cost × (p_max_pu × p_nom_opt − p) term to the LP objective
  // when this is > 0. Renewable-only in the UI but stored on every generator.
  curtailment_cost: number
  efficiency: number; committable: boolean
  ramp_limit_up: number | null; ramp_limit_down: number | null
  start_up_cost: number; shut_down_cost: number; min_up_time: number; min_down_time: number
  // PyPSA's e_sum_min / e_sum_max: per-generator annual energy floor/ceiling
  // (MWh). Default sentinels are -Infinity / +Infinity ("unbounded"); the
  // backend serialises those as null after `_clean`. Frontend treats null
  // the same as "no bound" and renders an empty field.
  e_sum_min: number | null
  e_sum_max: number | null
  build_year: number; lifetime: number | null; unit: string
}
export interface StorageUnit {
  name: string; bus: string; carrier: string; p_nom: number; p_nom_extendable: boolean
  p_nom_min: number; p_nom_max: number | null
  max_hours: number; efficiency_store: number; efficiency_dispatch: number
  standing_loss: number; cyclic_state_of_charge: boolean; state_of_charge_initial: number
  // Exogenous energy input to the SoC equation (MW). Constant here; the
  // time-varying override lives in `storage_units_t.inflow` via the
  // /timeseries upload path. Required for hydro reservoirs.
  inflow: number
  capital_cost: number; marginal_cost: number; fom_cost: number
  overnight_cost: number | null
  discount_rate: number | null
  build_year: number
  lifetime: number | null
}
export interface Store {
  name: string; bus: string; carrier: string; e_nom: number; e_nom_extendable: boolean
  e_nom_min: number; e_nom_max: number | null
  e_min_pu: number; e_max_pu: number; e_initial: number; e_cyclic: boolean
  capital_cost: number; marginal_cost: number; fom_cost: number
  overnight_cost: number | null
  discount_rate: number | null
  standing_loss: number
  // Used by PyPSA's multi-investment-period planning to gate when the store
  // becomes available (build_year) and when it's retired (build_year +
  // lifetime). Defaults: build_year=0, lifetime=∞ (serialised as null).
  build_year: number
  lifetime: number | null
}
export interface Load {
  name: string; bus: string; carrier: string; p_set: number; q_set: number; sign: number
}
export interface Transformer {
  name: string; bus0: string; bus1: string; type: string; s_nom: number
  r: number; x: number; tap_ratio: number; tap_side: number; phase_shift: number
  s_nom_extendable: boolean; s_nom_min: number; s_nom_max: number | null
  capital_cost: number; fom_cost: number
  overnight_cost: number | null
  discount_rate: number | null
  // Derived from the connected buses on the read path (PyPSA stores voltage
  // on the Bus, not the Transformer). Sent on create/update as the user's
  // declared step, then validated server-side against the buses' v_nom.
  v_nom_0?: number | null
  v_nom_1?: number | null
  // PyPSA vintage attributes — same shape as Generator / Link / Storage.
  // Default build_year=0 ("exists from the start"); null lifetime ⇒ infinite.
  build_year: number
  lifetime: number | null
}

export interface TransformerType {
  label: string
  v_nom_0: number
  v_nom_1: number
  s_nom: number
  x: number
}
export interface SnapshotInfo {
  count: number; snapshots: string[]; weightings: Record<string, number>[]
  // Present only when n.snapshots is a MultiIndex (multi-period planning).
  // Parallel to `snapshots`: periods[i] is the investment period that
  // snapshots[i]'s timestep belongs to.
  periods?: Array<number | string>
  // Min start / max end datetime across all flat uploaded time-series
  // (_user_ts). null when nothing flat has been uploaded. The Model Horizon
  // page uses this to default the snapshot range to the uploaded data.
  ts_start?: string | null
  ts_end?: string | null
  // True when a flat uploaded profile spans all 12 months of one year at
  // hourly resolution — gates the representative-week sampler.
  can_sample_weeks?: boolean
}
export interface NetworkMeta { name: string; snapshot_count: number; bus_count: number }
export interface SolverConfig {
  solver_name: string; mode: string; transmission_losses: boolean
  multi_investment_periods: boolean; solver_options: Record<string, unknown>
  extra_functionality_code: string
  // Modelling assumptions — applied transiently during solve. Backend
  // restores originals after. Safe defaults reproduce pre-feature behaviour.
  discount_rate: number              // 0..1 (7% = 0.07)
  // Expected inflation rate (decimal). Combined with discount_rate via the
  // Fisher relation real = (1+nom)/(1+infl) − 1 to derive the real discount
  // used in the cross-period PV factor when auto_discount_periods is on.
  // Default 0.0 preserves prior behaviour (real ≡ nominal).
  inflation_rate: number             // 0..1 (2% = 0.02)
  default_lifetime: number           // years; fallback when asset.lifetime is empty
  co2_price: number                  // €/tCO2 — added to fossil marginal_cost
  // Per-investment-period override of the scalar co2_price. Keyed by period
  // year (string). When non-empty AND the network is multi-period, each
  // snapshot uses the period's price; periods missing from the dict fall
  // back to the scalar `co2_price`. Ignored on flat (single-period) networks.
  co2_price_per_period?: Record<string, number>
  voll: number                       // €/MWh — when > 0, slack gens per bus
  investment_periods: number[]       // list of years; honoured iff multi_investment_periods
  // Per-investment-period load multiplier, keyed by period year (string).
  // 1.0 = unchanged; 1.05 = +5% load growth. Applied transiently at solve
  // time (multi-period only). Absent/missing key ⇒ treated as 1.0.
  load_scalers?: Record<string, number>
  // Per-carrier per-period load multiplier. Outer key = canonical carrier
  // bucket ('electrical' / 'hydrogen' / 'heat' / passthrough), inner key =
  // period year (str), value = multiplier. Overrides `load_scalers` (which
  // remains as a legacy all-carriers fallback) for the same period.
  load_scalers_by_carrier?: Record<string, Record<string, number>>
  // When true, the LP overwrites ipw.objective per period with
  // (1 + discount_rate)^-(P - ref_year) × years[P] at solve time. Stops
  // the LP from front-loading every CAPEX dollar into the first period.
  auto_discount_periods?: boolean
  // Per-investment-period upper bound on overnight CAPEX in EUR.
  // Constraint: Σ overnight_cost × Δp_nom ≤ budget[P] for assets with
  // build_year=P. Missing/zero entries are unconstrained.
  capex_budget_per_period?: Record<string, number>
  // N-1 SCLOPF. When sclopf=true and mode=='lopf', backend routes the solve
  // through PyPSA's optimize_security_constrained. The branch_outages list
  // is composed from include-all flags + voltage threshold + explicit picks.
  sclopf: boolean
  sclopf_include_all_lines: boolean
  sclopf_include_all_transformers: boolean
  sclopf_voltage_threshold_kv: number
  sclopf_extra_lines: string[]
  sclopf_extra_transformers: string[]
  // Contingency scope when SCLOPF runs under the myopic-foresight strategy.
  //  • "horizon"        — every snapshot in each myopic iteration (current
  //                       hourly + future-period representative when limited
  //                       foresight is on) is contingency-constrained.
  //  • "current_period" — only current-period hourly snapshots get the
  //                       contingency constraints. Future-period tail is
  //                       dropped from each iteration's LP. Cheaper, but
  //                       limited foresight becomes a no-op for SCLOPF iters.
  // Ignored when solve_strategy != "myopic" (the full-horizon SCLOPF path
  // has only one snapshot window anyway).
  sclopf_scope?: 'horizon' | 'current_period'
  // Stage 2 — post-solve AC Power Flow. When run_ac_pf_after_lopf is true,
  // the solver auto-chains an AC PF run with dispatch fixed from the LP
  // solution. Standalone trigger lives at POST /api/simulation/run_ac_pf.
  // ac_pf_slack_bus="" ⇒ backend auto-picks the bus with the largest
  // aggregate generation.
  run_ac_pf_after_lopf: boolean
  ac_pf_slack_bus: string
  ac_pf_x_tol: number
  // Solver presolve toggle. Default ON for all solvers — disable only when
  // debugging infeasibility (presolve typically masks the originating row).
  presolve_enabled: boolean
  // Numerical-conditioning multiplier on the LP objective.
  // The optimal solution is invariant to this scaling (linear-program
  // invariant: scaling the objective by a positive constant doesn't move
  // the argmin). The solver sees coefficients × scale; reported n.objective
  // and LP duals are divided back by scale post-solve so user-facing €
  // stays unchanged. Useful when cost coefficients span many orders of
  // magnitude (e.g. annualised CAPEX vs marginal_cost) and the solver
  // reports numerical-conditioning warnings. Default 1.0 = identity.
  user_objective_scale: number
  // When true, the solver auto-picks the objective scale from the model's
  // cost-coefficient spread, overriding user_objective_scale. Engages only for
  // ill-conditioned models; a no-op otherwise. Default false.
  auto_objective_scale: boolean
  // Solve strategy. 'full' = single LP over all snapshots (default).
  // 'rolling' = PyPSA optimize_with_rolling_horizon, splitting into
  // `rolling_horizon`-sized chunks with `rolling_overlap` SoC carry —
  // operational only, validation forbids combining with multi-period.
  // 'myopic' = solve each investment period sequentially. Pure myopic
  // (Phase 1) when lf_aggregate_future=false; limited-foresight (Phase 2)
  // when lf_aggregate_future=true — each iteration also sees aggregated
  // representative weeks for every future period.
  solve_strategy: 'full' | 'rolling' | 'myopic'
  // Limited-foresight (Phase 2). Off by default — pure myopic is the safe
  // baseline. When on, every iteration of the myopic loop also sees k
  // representative blocks (each lf_period_length_h hours long) per future
  // period, clustered via tsam.
  lf_aggregate_future: boolean
  lf_k_periods: number
  lf_period_length_h: number
  lf_cluster_method: string
  lf_include_extreme: boolean
  rolling_horizon: number
  rolling_overlap: number
  // MIP knobs. Only consumed when committable=True on at least one
  // generator (the LP becomes a MILP). mip_gap = 0.01 = 1 % rel. tolerance,
  // a sensible default for UC. mip_time_limit_s = 0 disables the cap.
  mip_gap: number
  mip_time_limit_s: number
}
/**
 * Actionable failure card for a finished solve. Produced by the backend's
 * failure taxonomy (services/failure_taxonomy.py); null on success/abort.
 * `category` is a stable machine token; `title`/`hint` are user-facing.
 */
export interface FailureInfo {
  category: string
  title: string
  hint: string
  detail: string
}
export interface SimulationStatus {
  running: boolean; status: string; condition: string | null
  objective: number | null; solve_time: number | null
  failure?: FailureInfo | null
  // Dispatch freshness: 'fresh' (results match topology), 'stale' (results
  // exist but columns diverge), 'none' (no/cleared results). A topology edit on
  // a solved network clears it → the UI flags "results invalidated, re-run".
  dispatch?: 'fresh' | 'stale' | 'none'
}
export interface ValidationIssue {
  severity: 'error' | 'warning'
  code: string
  component_class: string
  name: string
  message: string
}
export interface PreflightResult {
  ok: boolean
  errors: number
  warnings: number
  issues: ValidationIssue[]
  // Set by the backend when a solver worker is still alive after the user
  // clicked Abort. The LP transforms (vintage rows, slack generators,
  // dispatch fix) are still in place and validation would report bogus
  // issues, so the backend returns an empty issues[] and this flag instead.
  // The IssuesPanel renders `deferred_reason` as a banner so the user
  // knows to wait, rather than seeing "All clear" or a wall of fake errors.
  deferred?: boolean
  deferred_reason?: string
  // True when the deferred state is the "stuck abort" case: the user has
  // already clicked Abort but HiGHS/Gurobi is still iterating in native
  // code. Only a backend restart will recover; the UI surfaces this with
  // a stronger banner + restart-instructions instead of the regular
  // "wait for the lock to release" message.
  deferred_stuck?: boolean
}
export interface ImportSummary {
  buses: number; generators: number; lines: number; links: number
  storage_units: number; stores: number; loads: number; transformers: number; snapshots: number
}
export interface ProjectInfo {
  name: string; created_at: string; has_solver_config: boolean
  bus_count: number; snapshot_count: number; objective: number | null
  // True when an interrupted save left a `*.tmp` sibling next to the
  // main bundle file. The main file is still the previous good version
  // (atomic rename only commits at the end), but the partial write hasn't
  // been explicitly committed or discarded. Surfaced in the crash-recovery
  // banner on App startup.
  has_orphan_tmp?: boolean
  // Phase 8 scenario-tree fields. `parent_project` is non-null when this
  // project is a scenario branched off another; the scenario tree is the
  // graph reconstructed by walking these pointers across the list.
  parent_project?: string | null
  scenario_description?: string | null
}

// Compact summary returned by GET /api/projects/{name}/compare-state.
// Loaded into a transient pypsa.Network on the backend — never touches the
// active singleton — so the compare view can A/B inspect scenarios without
// disturbing whatever the user is editing.
export interface CompareState {
  name: string
  bus_count: number
  generator_count: number
  line_count: number
  load_count: number
  link_count: number
  storage_unit_count: number
  store_count: number
  snapshot_count: number
  snapshot_start: string | null
  snapshot_end: string | null
  installed_capacity_by_carrier: Record<string, number>
  optimised_capacity_by_carrier: Record<string, number> | null
  storage_capacity_by_carrier: Record<string, number>
  store_capacity_by_carrier: Record<string, number>
  peak_demand_mw: number
  total_energy_mwh: number
  objective: number | null
  solve_time: number | null
  dispatch_status: 'none' | 'fresh' | 'stale'
  parent_project: string | null
  scenario_description: string | null
  created_at: string | null
  last_saved: string | null
}

// ── Results-Summary types (Compare Scenarios v2) ─────────────────────────────
// One per-tab payload per project. Each metric carries both an aggregated
// `total` and an optional `by_period` breakdown (period year stringified).
// For multi-period networks the per-period values cover INVESTMENT periods
// (build_year for capacity, snapshot period for dispatch).

export interface CarrierPeriodValue {
  total: number
  by_period: Record<string, number>
}

export interface CapacityComparison {
  capacity_mw_by_carrier: Record<string, CarrierPeriodValue>
  // Total annuitised CAPEX (existing + new) per carrier — matches the
  // live /results/cost_breakdown numbers.
  capex_meur_by_carrier: Record<string, CarrierPeriodValue>
  // Increment-only CAPEX (max(0, p_nom_opt − p_nom) × annuitised cc).
  // Useful as a secondary "what's new in this scenario" metric.
  new_capex_meur_by_carrier: Record<string, CarrierPeriodValue>
  // Increment-only CAPACITY in MW per carrier — GENERATORS ONLY. Storage and
  // link builds have their own maps below; mixing them here produced
  // impossible rows (battery total=0 / built=427 MW) because the generator
  // table's total column is generators-only. Optional so older payloads
  // (pre-2026-05) don't crash.
  new_capacity_mw_by_carrier?: Record<string, CarrierPeriodValue>
  storage_mw_by_carrier: Record<string, CarrierPeriodValue>
  storage_mwh_by_carrier: Record<string, CarrierPeriodValue>
  // New (built) storage increments — MW and MWh (storage_units + stores).
  new_storage_mw_by_carrier?: Record<string, CarrierPeriodValue>
  new_storage_mwh_by_carrier?: Record<string, CarrierPeriodValue>
  // Link power capacity (MW) per carrier — total (brownfield + built) and
  // built-only. Heat-pumps / electrolyzers / datacenters / P2X converters.
  link_capacity_mw_by_carrier?: Record<string, CarrierPeriodValue>
  new_link_capacity_mw_by_carrier?: Record<string, CarrierPeriodValue>
}

export interface DispatchComparison {
  dispatch_gwh_by_carrier: Record<string, CarrierPeriodValue>
  opex_meur: CarrierPeriodValue
  total_load_gwh: CarrierPeriodValue
  storage_cycles_by_carrier: Record<string, CarrierPeriodValue>
}

export interface LineLoadingEntry {
  name: string
  s_nom_opt: number
  is_transformer: boolean
  // Multi-port Links use a separate flag + carrier so the Compare View can
  // render them in a dedicated section (Loading-of-Links) and tooltip the
  // carrier alongside loading %. Backend sets is_link=true and carrier
  // alongside s_nom_opt (which holds p_nom_opt for Links).
  is_link?: boolean
  carrier?: string | null
  peak_loading: CarrierPeriodValue       // fraction (0..1)
  mean_loading: CarrierPeriodValue
  binding_hours: CarrierPeriodValue
}

export interface LoadingComparison {
  lines: LineLoadingEntry[]              // sorted by peak loading desc
}

export interface CarrierPriceStats {
  bus_count: number
  mean_price: CarrierPeriodValue
  median_price: CarrierPeriodValue
  p90_price: CarrierPeriodValue
}

export interface PricesComparison {
  duration_curve: number[]               // 101 samples, index 0 = peak price
  mean_price: CarrierPeriodValue         // €/MWh
  median_price: CarrierPeriodValue
  p90_price: CarrierPeriodValue
  // Extreme tail markers. The duration_curve's first sample can be a single
  // bus-snapshot scarcity spike (LP dual at a binding constraint). Used by
  // the Compare View Prices tab to annotate the curve with the absolute
  // max/min separately so users know the spike vs the bulk distribution.
  max_price?: number                     // absolute max €/MWh (unfiltered)
  min_price?: number                     // absolute min €/MWh
  bus_count: number
  // Per-bus-carrier price stats (electricity / H2 / heat / ...). Sector-
  // coupled networks have very different price regimes by carrier, so
  // the Compare View surfaces these alongside the system-wide stats.
  by_carrier_stats?: Record<string, CarrierPriceStats>
}

export interface EmissionsComparison {
  total_kt: CarrierPeriodValue
  by_carrier_kt: Record<string, CarrierPeriodValue>
  intensity_kg_per_mwh: CarrierPeriodValue
}

export interface CarrierEconomics {
  revenue_meur: CarrierPeriodValue
  // Headline TOTAL OPEX — sum of the four split fields below. The split
  // lets the Compare View show users WHAT they paid for per carrier
  // (gas: mostly gen cost; battery: gen + charge cost; solar: curtailment
  // cost; etc.). Storage_charge_cost can be NEGATIVE when bus prices go
  // negative during charging hours (solar curtailment).
  opex_meur: CarrierPeriodValue
  gen_cost_meur?: CarrierPeriodValue
  storage_charge_cost_meur?: CarrierPeriodValue
  curtailment_cost_meur?: CarrierPeriodValue
  lost_load_cost_meur?: CarrierPeriodValue
  capex_meur: CarrierPeriodValue
  dispatch_gwh: CarrierPeriodValue
  lcoe_eur_per_mwh: CarrierPeriodValue
}

export interface AssetLCOHEntry {
  name: string
  carrier: string | null
  p_nom_opt: number
  capex_meur: CarrierPeriodValue
  opex_meur: CarrierPeriodValue
  input_cost_meur: CarrierPeriodValue
  output_gwh: CarrierPeriodValue
  lcoh_eur_per_mwh: CarrierPeriodValue
}

export interface EconomicsComparison {
  by_carrier: Record<string, CarrierEconomics>
  // Per-Link LCOH rows (electrolysers, heat pumps, P2X, ...). Empty when
  // no extendable Link in either project has non-trivial dispatch.
  per_asset_lcoh?: AssetLCOHEntry[]
}

export interface CurtailmentComparison {
  total_gwh: CarrierPeriodValue
  by_carrier_gwh: Record<string, CarrierPeriodValue>
  rate_pct_by_carrier: Record<string, CarrierPeriodValue>
  system_rate_pct: CarrierPeriodValue
}

export interface LostLoadBus {
  bus: string
  energy_mwh: CarrierPeriodValue
  cost_meur: CarrierPeriodValue
}

export interface LostLoadByCarrier {
  carrier: string
  bus_count: number
  energy_mwh: CarrierPeriodValue
  cost_meur: CarrierPeriodValue
}

export interface LostLoadComparison {
  available: boolean
  voll_eur_per_mwh: number
  total_mwh: CarrierPeriodValue
  total_cost_meur: CarrierPeriodValue
  by_bus: LostLoadBus[]
  // Per-bus-carrier shedding roll-up — answers "is the load-shedding
  // concentrated on the electrical side or on heat/H2?" without scanning
  // every bus. Empty when lost-load isn't available.
  by_carrier?: LostLoadByCarrier[]
}

export interface StorageUnitCycles {
  name: string
  carrier: string
  p_nom_mw: number
  energy_mwh: number
  throughput_mwh: CarrierPeriodValue
  cycles: CarrierPeriodValue
}

export interface StorageCyclingComparison {
  cycles_by_carrier: Record<string, CarrierPeriodValue>
  by_unit: StorageUnitCycles[]
}

export interface ResultsSummary {
  project: string
  is_multi_period: boolean
  periods: number[]
  has_solve: boolean
  capacity: CapacityComparison | null
  dispatch: DispatchComparison | null
  loading: LoadingComparison | null
  prices: PricesComparison | null
  emissions: EmissionsComparison | null
  economics: EconomicsComparison | null
  curtailment: CurtailmentComparison | null
  lost_load: LostLoadComparison | null
  storage_cycling: StorageCyclingComparison | null
}
export interface SaveResult {
  saved: string; path: string
  ts_columns_saved: number; bus_count: number; snapshot_count: number
}
export interface BundleImportResult {
  imported: string; summary: ImportSummary
}
export interface TimeseriesData {
  index: string[]; columns: string[]; data: number[][]
}
export interface TimeseriesInfo {
  component: string; attribute: string; column_count: number; columns: string[]
}
export type LoadSection = 'electricity' | 'hydrogen' | 'heat' | 'other'

// `sum` = Σ of the profile values — energy (MWh) for a load p_set profile,
// full-load-equivalent hours for a p_max_pu profile. The GUI labels it
// per attribute.
export type LoadProfileMeta =
  | { has_profile: false; section: LoadSection; bus: string }
  | { has_profile: true; section: LoadSection; bus: string
      rows: number; start: string | null; end: string | null
      mean: number; peak: number; sum: number }

export interface LoadAggregate {
  index: string[]
  values: (number | null)[]
  total_loads: number
  loads_with_profile: number
  peak: number
  mean: number
}

// Per-attribute metadata block. Used both for the top-level p_max_pu fields
// (kept flat for back-compat) and for the nested p_min_pu sub-record.
// `sum` = Σ of the profile values (full-load-equivalent hours for p_max_pu).
export type GeneratorProfileAttrMeta =
  | { has_profile: false }
  | { has_profile: true; rows: number; start: string | null; end: string | null
      mean: number; peak: number; sum: number }

export type GeneratorProfileMeta =
  & ({ has_profile: false } | { has_profile: true; rows: number; start: string | null; end: string | null; mean: number; peak: number; sum: number })
  & {
      carrier: string;
      category: string;
      p_min_pu?: GeneratorProfileAttrMeta;
      // Time-varying €/MWh dispatch cost for conventional plants (fuel-price
      // traces, market scenarios). Surfaced on the Conventional tab's third
      // sub-toggle when has_profile=true.
      marginal_cost?: GeneratorProfileAttrMeta;
    }

export type LinkProfileMeta =
  & ({ has_profile: false } | { has_profile: true; rows: number; start: string | null; end: string | null; mean: number; peak: number; sum: number })
  & {
      category: string
      // Per-attribute sub-blocks. Top-level fields above describe p_max_pu
      // for back-compat with the Links tab's default rendering.
      p_min_pu?: GeneratorProfileAttrMeta
      marginal_cost?: GeneratorProfileAttrMeta
    }
