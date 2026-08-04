from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

# Frontend Edit-card UX sends `null` for blanked optional bounds (so the
# spread-then-override idiom can actually un-set a previously-typed value).
# PyPSA's native sentinel for "no bound" on `_max` / `lifetime` is +inf,
# and -inf for `_min` floor fields. Coerce `None` → the right sentinel at
# the schema boundary so the rest of the backend sees a plain `float`.
# Without this, every "Apply" with one of those fields blanked 422s the
# whole PUT — surfaced by QA G1.
_NoneToPosInf = Annotated[float, BeforeValidator(
    lambda v: float("inf") if v is None else v
)]
_NoneToNegInf = Annotated[float, BeforeValidator(
    lambda v: float("-inf") if v is None else v
)]
# For multi-port Link efficiencies (efficiency2 / efficiency3). PyPSA's
# default for an UNSET secondary efficiency is 1.0 (the bus_i is treated as
# pass-through). The frontend's `{...cached}` PUT spread carries `null` for
# every secondary efficiency the user hasn't set — without coercion, the
# float validator rejects null and 422s the whole Apply.
_NoneToOne = Annotated[float, BeforeValidator(
    lambda v: 1.0 if v is None else v
)]


class BusCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    v_nom: float = 1.0
    carrier: str = "AC"
    x: float = 0.0
    y: float = 0.0
    country: str = ""
    unit: str = "MW"
    control: str = "PQ"
    sub_network: str = ""


class CarrierCreate(BaseModel):
    # `name` is required on POST (the URL has no name) but OPTIONAL on PUT
    # (the URL carries the name, body is the partial update). Pydantic
    # marks `name` Optional with a None default so PUT /carriers/{name}
    # bodies like `{"co2_emissions": 0.2}` validate cleanly. POST is gated
    # by the FastAPI route handler which requires name explicitly.
    name: str | None = None
    co2_emissions: float = 0.0
    color: str = ""
    nice_name: str = ""
    unit: str = ""


class LineCreate(BaseModel):
    name: str
    bus0: str
    bus1: str
    length: float = 1.0
    type: str = ""
    r: float = 0.0
    x: float = 0.01
    b: float = 0.0
    s_nom: float = 0.0
    s_nom_extendable: bool = False
    s_nom_min: float = 0.0
    s_nom_max: _NoneToPosInf = Field(default=float("inf"))
    capital_cost: float = 0.0
    fom_cost: float = 0.0   # fixed O&M, €/MVA/yr (PyPSA-native)
    # Lump-sum construction cost (€/MVA). NaN means "unset" — PyPSA's
    # native annuity (overnight × annuity(rate, life)) takes over when
    # this is set; otherwise capital_cost is used directly.
    overnight_cost: float | None = None
    # Per-asset discount rate; NaN ⇒ fall back to solver config's global
    # discount_rate at solve time. PyPSA's consistency check requires this
    # to be set per-asset whenever overnight_cost is.
    discount_rate: float | None = None
    terrain_factor: float = 1.0
    carrier: str = "AC"
    # PyPSA vintage attributes — same defaults as Generators / Storage /
    # Links / Stores so multi-period validators don't single lines out.
    # `lifetime=inf` keeps a line buildable across every investment
    # period until the user explicitly retires it.
    build_year: int = 0
    lifetime: _NoneToPosInf = Field(default=float("inf"))


class ImpedanceRescaleEntry(BaseModel):
    name: str
    r: float
    x: float
    b: float


class ImpedanceRescaleRequest(BaseModel):
    lines: list[ImpedanceRescaleEntry]


class LinkCreate(BaseModel):
    name: str
    bus0: str
    bus1: str
    bus2: str = ""
    bus3: str = ""
    bus4: str = ""
    carrier: str = ""
    efficiency: float = 1.0
    # Secondary efficiencies for multi-output Links (CHP plants etc).
    # Only enter the energy balance when the corresponding bus2/bus3/bus4 is
    # set. `_NoneToOne` coerces null → 1.0 so the frontend's full-object
    # cached-PUT spread doesn't 422 on `efficiency3: null` when only bus2 is
    # configured (heat-pump case).
    efficiency2: _NoneToOne = 1.0
    efficiency3: _NoneToOne = 1.0
    p_nom: float = 0.0
    p_nom_extendable: bool = False
    p_nom_min: float = 0.0
    p_nom_max: _NoneToPosInf = Field(default=float("inf"))
    marginal_cost: float = 0.0
    capital_cost: float = 0.0
    fom_cost: float = 0.0   # fixed O&M, €/MW/yr (PyPSA-native)
    overnight_cost: float | None = None  # €/MW; NaN = unset
    # Per-asset discount rate used by PyPSA's native annuity when
    # overnight_cost is set. NaN ⇒ fall back to solver config's global
    # discount_rate at solve time (transient fill).
    discount_rate: float | None = None
    p_min_pu: float = 0.0
    p_max_pu: float = 1.0
    build_year: int = 0
    lifetime: _NoneToPosInf = Field(default=float("inf"))


class GeneratorCreate(BaseModel):
    name: str
    bus: str
    carrier: str = ""
    # PyPSA control mode for power flow / unit commitment. PQ = fixed P & Q,
    # PV = fixed P with voltage controlled, Slack = absorbs imbalance. Default
    # PQ for safety — irrelevant for LOPF but consumed by Stage 2 (AC PF) and
    # the auto-slack picker in solver_service. Validation in Phase 7 will
    # ensure at least one Slack exists when run_ac_pf_after_lopf is on.
    control: str = "PQ"
    p_nom: float = 0.0
    p_nom_extendable: bool = False
    p_nom_min: float = 0.0
    p_nom_max: _NoneToPosInf = Field(default=float("inf"))
    p_min_pu: float = 0.0
    p_max_pu: float = 1.0
    marginal_cost: float = 0.0
    capital_cost: float = 0.0
    fom_cost: float = 0.0           # fixed O&M, €/MW/yr (PyPSA-native)
    overnight_cost: float | None = None  # €/MW; NaN = unset
    # Per-asset discount rate used by PyPSA's native annuity when
    # overnight_cost is set. NaN ⇒ fall back to solver config's global
    # discount_rate at solve time (transient fill).
    discount_rate: float | None = None
    # PyPSA has no built-in curtailment cost for renewables. We store this as
    # a custom column on n.generators; solver_service injects an
    # extra_functionality callback at solve time that adds the LP term
    #   Σ curtailment_cost × (p_max_pu × p_nom_opt − p)
    # to the objective. Only honoured for generators where the value is > 0.
    curtailment_cost: float = 0.0   # €/MWh of curtailed renewable energy
    efficiency: float = 1.0
    committable: bool = False
    ramp_limit_up: float | None = None
    ramp_limit_down: float | None = None
    start_up_cost: float = 0.0
    shut_down_cost: float = 0.0
    min_up_time: int = 0
    min_down_time: int = 0
    # Per-generator annual energy floor / ceiling (MWh). PyPSA enforces
    # `e_sum_min ≤ Σ_t (p_t × weight_t) ≤ e_sum_max` on the dispatch.
    # Default to PyPSA's own sentinels (-inf / +inf) meaning "no bound".
    # Use cases: hydro reservoir annual energy from inflow, fuel-budget caps,
    # take-or-pay PPAs, water-rights envelopes.
    e_sum_min: _NoneToNegInf = Field(default=float("-inf"))
    e_sum_max: _NoneToPosInf = Field(default=float("inf"))
    build_year: int = 0
    lifetime: _NoneToPosInf = Field(default=float("inf"))
    unit: str = "MW"


class StorageUnitCreate(BaseModel):
    name: str
    bus: str
    carrier: str = ""
    p_nom: float = 0.0
    p_nom_extendable: bool = False
    p_nom_min: float = 0.0
    p_nom_max: _NoneToPosInf = Field(default=float("inf"))
    max_hours: float = 6.0
    efficiency_store: float = 0.9
    efficiency_dispatch: float = 0.9
    standing_loss: float = 0.0
    cyclic_state_of_charge: bool = True
    state_of_charge_initial: float = 0.5
    # Exogenous energy input to the storage SoC equation (MW). Constant value
    # set here; time-varying inflows go through `storage_units_t.inflow` via
    # the generic /timeseries upload endpoint. Required to model hydro
    # reservoirs — without it a "reservoir" is just a battery with no fuel.
    inflow: float = 0.0
    capital_cost: float = 0.0
    marginal_cost: float = 0.0
    fom_cost: float = 0.0   # fixed O&M, €/MW/yr (PyPSA-native)
    overnight_cost: float | None = None  # €/MW; NaN = unset
    # Per-asset discount rate used by PyPSA's native annuity when
    # overnight_cost is set. NaN ⇒ fall back to solver config's global
    # discount_rate at solve time (transient fill).
    discount_rate: float | None = None
    build_year: int = 0
    lifetime: _NoneToPosInf = Field(default=float("inf"))


class StoreCreate(BaseModel):
    name: str
    bus: str
    carrier: str = ""
    e_nom: float = 0.0
    e_nom_extendable: bool = False
    e_nom_min: float = 0.0
    e_nom_max: _NoneToPosInf = Field(default=float("inf"))
    e_min_pu: float = 0.0
    e_max_pu: float = 1.0
    e_initial: float = 0.0
    e_cyclic: bool = False
    capital_cost: float = 0.0
    marginal_cost: float = 0.0
    fom_cost: float = 0.0   # fixed O&M, €/MWh/yr (PyPSA-native)
    overnight_cost: float | None = None  # €/MWh; NaN = unset
    # Per-asset discount rate; NaN ⇒ fall back to solver config's global
    # discount_rate at solve time (transient fill).
    discount_rate: float | None = None
    standing_loss: float = 0.0
    build_year: int = 0
    lifetime: _NoneToPosInf = Field(default=float("inf"))


class LoadCreate(BaseModel):
    name: str
    bus: str
    carrier: str = ""
    p_set: float = 0.0
    q_set: float = 0.0
    sign: float = -1.0


class TransformerCreate(BaseModel):
    name: str
    bus0: str
    bus1: str
    type: str = ""
    s_nom: float = 0.0
    r: float = 0.0
    x: float = 0.1
    tap_ratio: float = 1.0
    tap_side: int = 0
    phase_shift: float = 0.0
    s_nom_extendable: bool = False
    s_nom_min: float = 0.0
    s_nom_max: _NoneToPosInf = Field(default=float("inf"))
    capital_cost: float = 0.0
    fom_cost: float = 0.0   # fixed O&M, €/MVA/yr (PyPSA-native)
    overnight_cost: float | None = None  # €/MVA; NaN = unset
    # Per-asset discount rate; NaN ⇒ fall back to solver config's global
    # discount_rate at solve time (transient fill).
    discount_rate: float | None = None
    # User-declared voltage step. PyPSA itself derives transformer voltages
    # from the connected buses, but capturing the user's intent here lets the
    # backend reject mismatched bus pairings before the network state is
    # touched. `None` (or omitted) ⇒ skip the voltage check (treat as
    # "any bus voltages OK"). Switched from `float = 0.0` to
    # `Optional[float] = None` so a partial-PUT body that simply omits
    # these fields gets a clean "skip validation" instead of accidentally
    # asserting v_nom_0 == v_nom_1 == 0 (which short-circuited via the
    # `<= 0` branch but is semantically wrong — 0 kV is not a valid step).
    v_nom_0: float | None = None
    v_nom_1: float | None = None
    # PyPSA vintage attributes — same defaults as Generators / Storage /
    # Links / Stores so multi-period validators don't single transformers
    # out. `lifetime=inf` keeps a transformer buildable across every
    # investment period until the user explicitly retires it.
    build_year: int = 0
    lifetime: _NoneToPosInf = Field(default=float("inf"))


class ShuntImpedanceCreate(BaseModel):
    name: str
    bus: str
    g: float = 0.0
    b: float = 0.0


class SnapshotConfig(BaseModel):
    start: str
    end: str
    freq: str = "h"
    weightings: float | None = None


class SampleWeeksConfig(BaseModel):
    # ISO calendar weeks to sample per calendar month (1–5). Each week is 168
    # hourly snapshots; snapshot_weightings scales the sample up to the full
    # month (days_in_month / (weeks_sampled × 7)).
    n_weeks: int = 1
    # RNG seed for reproducible sampling. None ⇒ non-deterministic.
    seed: int | None = None


class InvestmentPeriods(BaseModel):
    periods: list[int]
    objective_weightings: list[float] | None = None
    years_weightings: list[float] | None = None


# Network-wide constraints in `n.global_constraints`. PyPSA recognises the
# following `type` values; the rest of the fields apply selectively:
#
#   primary_energy                  — caps Σ(carrier_attribute) over horizon
#                                     (CO2 cap, fuel-use cap, etc.)
#   transmission_volume_expansion_limit — caps total ΣMW·km of new lines/links
#   transmission_expansion_cost_limit   — caps total annualised CAPEX of new tx
#   tech_capacity_expansion_limit   — per-carrier cap on new generation/storage
#   operational_limit               — per-carrier cap on dispatch over horizon
#   growth_limit                    — bounds growth between investment periods
#
# All but `primary_energy` use `carrier`; `primary_energy` uses `carrier_attribute`.
# `sense` is one of "<=" / ">=" / "==". `investment_period` only applies in
# multi-period setups.
class GlobalConstraintCreate(BaseModel):
    name: str
    type: str = "primary_energy"
    sense: str = "<="
    constant: float = 0.0
    carrier_attribute: str | None = None      # for primary_energy
    carrier: str | None = None                # for tech / operational / etc.
    investment_period: int | None = None      # multi-period only


class NetworkMeta(BaseModel):
    name: str = "Untitled Project"
    description: str = ""


class SolverConfigSchema(BaseModel):
    solver_name: str = "highs"
    mode: str = "lopf"
    transmission_losses: bool = False
    multi_investment_periods: bool = False
    solver_options: dict[str, Any] = {}
    extra_functionality_code: str = ""
    # Modelling assumptions — applied transiently at solve time. All five are
    # safe defaults that exactly reproduce the pre-feature behaviour (zero
    # surcharges, no slack, single period).
    discount_rate: float = 0.07
    # Expected inflation rate (decimal). Combined with `discount_rate` via the
    # Fisher relation `real = (1+nom)/(1+infl) − 1` to derive the real
    # discount used in the cross-period PV factor when `auto_discount_periods`
    # is enabled. Default 0.0 preserves prior behaviour (real ≡ nominal).
    inflation_rate: float = 0.0
    default_lifetime: float = 25.0
    co2_price: float = 0.0
    # Per-investment-period override of the scalar `co2_price`. Keyed by
    # period year (str). When non-empty AND the network is multi-period,
    # each snapshot's surcharge uses the period's entry; periods missing
    # from the dict fall back to the scalar `co2_price`. Applied via the
    # time-varying `generators_t.marginal_cost`. Ignored on single-period
    # networks (warning emitted to the solver log).
    co2_price_per_period: dict[str, float] = {}
    voll: float = 0.0
    investment_periods: list[int] = []
    # Per-investment-period load multiplier, keyed by period year (str). 1.0 =
    # unchanged. Applied transiently at solve time — see SolverConfig.load_scalers.
    # Legacy field: applied to ALL load carriers. Use load_scalers_by_carrier
    # for per-carrier growth profiles.
    load_scalers: dict[str, float] = {}
    # Per-carrier per-period load multiplier. Outer key = canonical carrier
    # ('electrical' / 'hydrogen' / 'heat' / passthrough), inner key = period
    # year (str), value = multiplier. The per-carrier entry wins over
    # `load_scalers` for the same period; carriers without an entry fall
    # back to `load_scalers[period]` or 1.0. See solver_service step 5.
    load_scalers_by_carrier: dict[str, dict[str, float]] = {}
    # Per-period auto-discount via ipw.objective. When True, the LP applies a
    # uniform PV factor (1+discount_rate)^-(P-ref_year) to every period's
    # cost (capex AND opex) — encourages deferring investment until needed
    # instead of front-loading. Reference year = first period.
    auto_discount_periods: bool = False
    # Per-investment-period upper bound on overnight CAPEX (€). Keyed by
    # period year (str). Empty / missing entries are unconstrained. Adds an
    # LP constraint Σ overnight_cost × Δp_nom ≤ budget[P] for all
    # extendable assets with build_year=P.
    capex_budget_per_period: dict[str, float] = {}
    # Security-Constrained LOPF (N-1). When `sclopf` is True and mode == "lopf",
    # solver_service routes the solve through PyPSA's
    # `n.optimize.optimize_security_constrained()` instead of plain `n.optimize`.
    # The branch_outages list passed to that call is built per-solve by union
    # of: (a) all lines if `sclopf_include_all_lines`, (b) all transformers if
    # `sclopf_include_all_transformers`, (c) every line/transformer whose
    # higher-voltage bus is ≥ `sclopf_voltage_threshold_kv` (when > 0), and
    # (d) any extra lines/transformers listed by name. Empty resolved set ⇒
    # validation_service blocks the run (it would just be a plain LOPF).
    sclopf: bool = False
    sclopf_include_all_lines: bool = False
    sclopf_include_all_transformers: bool = False
    sclopf_voltage_threshold_kv: float = 0.0
    sclopf_extra_lines: list[str] = []
    sclopf_extra_transformers: list[str] = []
    # SCLOPF contingency scope when running under myopic foresight.
    # • "horizon"        — N-1 enforced at every snapshot in each iteration
    # • "current_period" — N-1 only on current-period hourly snapshots;
    #                      future-period representatives solved as plain LOPF
    # Defaults to "horizon" (safer, matches TSO N-1-always convention).
    sclopf_scope: str = "horizon"
    # ── Stage 2 (post-solve AC Power Flow) ──────────────────────────────────
    # When True, run_simulation chains an AC PF run after the LOPF stage with
    # the dispatch fixed from the LP solution. Standalone trigger also exists:
    # POST /api/simulation/run_ac_pf. ac_pf_slack_bus="" ⇒ auto-pick.
    run_ac_pf_after_lopf: bool = False
    ac_pf_slack_bus: str = ""
    # PyPSA's n.pf() does NOT expose a max-iter cap; only x_tol gates convergence.
    ac_pf_x_tol: float = 1e-6
    # Solver presolve toggle. Default ON because presolve typically cuts solve
    # time 2-10× by eliminating redundant rows/cols. Disable only when
    # debugging an infeasibility — presolve often masks the original culprit
    # row by collapsing it before simplex even starts.
    presolve_enabled: bool = True
    # User-supplied numerical-conditioning multiplier on the LP objective.
    # The optimal solution is invariant to this scaling (LP-theory result);
    # it only shifts the solver's internal magnitude. Useful when costs
    # span many orders of magnitude. 1.0 = identity.
    user_objective_scale: float = 1.0
    # When True, the solver auto-picks the objective scale (a power of 10) from
    # the model's cost-coefficient spread, overriding user_objective_scale. Only
    # engages for ill-conditioned models; a no-op otherwise. Default OFF.
    auto_objective_scale: bool = False
    # Solve strategy. Three modes:
    #   "full"    — single-shot LP (default).
    #   "rolling" — PyPSA's optimize_with_rolling_horizon (operational
    #               rolling). Validation rejects this with multi-period
    #               capacity expansion since each window decouples
    #               investment decisions and later periods shed load.
    #   "myopic"  — Phase 1 of limited-foresight planning: solve each
    #               investment period sequentially at full hourly detail,
    #               freeze capacities, move forward. Requires
    #               multi_investment_periods=True.
    solve_strategy: str = "full"
    rolling_horizon: int = 168
    rolling_overlap: int = 24
    # Limited-foresight (Phase 2 of myopic). Off by default — pure myopic is
    # the safe baseline. When on, each iteration's LP also sees aggregated
    # representative periods for every future period, giving the solver
    # forward visibility without the full LP's cost. See the docstring on
    # services/time_aggregation_service for clustering details.
    lf_aggregate_future: bool = False
    lf_k_periods: int = 8
    lf_period_length_h: int = 168
    lf_cluster_method: str = "hierarchical"
    lf_include_extreme: bool = True
    # MIP knobs. Only consumed when at least one generator has committable=True
    # (the LP becomes a MILP). mip_gap is a relative tolerance; 0 = exact;
    # 0.01 = stop within 1 % of optimal. mip_time_limit_s = 0 disables the cap.
    mip_gap: float = 0.01
    mip_time_limit_s: float = 0


class ImportSummary(BaseModel):
    buses: int
    generators: int
    lines: int
    links: int
    storage_units: int
    stores: int
    loads: int
    transformers: int
    snapshots: int


class ImportFolderRequest(BaseModel):
    """
    A folder the desktop user points at, to import pre-desktop projects from.

    `apply` defaults to False: the destructive version of this is one click on
    a path somebody typed, and `legacy_import.import_all` already offers a
    faithful dry run. The UI previews first and asks.
    """

    path: str
    apply: bool = False


class ProjectInfo(BaseModel):
    name: str
    # Stable project identifier. Populated with the DB-registry UUID (as a
    # string) when multi-user auth is enabled; None in legacy single-user
    # mode, where projects are identified by their (unique) on-disk name.
    id: str | None = None
    created_at: str
    has_solver_config: bool
    bus_count: int
    snapshot_count: int
    objective: float | None = None
    # True when the project dir contains an orphaned `*.tmp` sibling — left
    # behind by `_atomic_write_with` if the process was killed mid-save.
    # The main `network.nc` is still the previous good version (atomic rename
    # only happens at the end), but the user should investigate the orphan to
    # decide whether to commit or discard the partial write. Surfaced in the
    # crash-recovery banner on the next app open.
    has_orphan_tmp: bool = False
    # The registry row exists but its files do not. Specific to the LOCAL app:
    # D13 puts projects in `~/Documents/PyPSA GUI/Projects/<name>/` so a human
    # can navigate them, which means deleting one in Finder is the designed
    # workflow meeting its obvious consequence — not misuse, and impossible in
    # the web deployment where nobody has the disk.
    #
    # Without this the stub branch reports `bus_count: 0`, which is exactly
    # what a real empty project reports, so the list shows an ordinary entry
    # that 404s when opened. Reported rather than hidden (the user still needs
    # a handle to delete the row) or purged (a project on an unmounted share
    # would be destroyed).
    missing: bool = False
    # Name of the parent project (None = root scenario, not a child).
    # A non-null value means this project is a scenario branched off another.
    # The frontend reconstructs the scenario tree by walking these pointers.
    # NB: a project rename would break the link — the rename path (when
    # introduced) must cascade-update children's `parent_project` to keep
    # the tree consistent.
    parent_project: str | None = None
    # Free-form one-line label describing the scenario's purpose. Only
    # populated for scenarios (set at creation); root projects leave it None.
    scenario_description: str | None = None
    # Scenario category: 'baseline' | 'scenario' | 'stress', or None when the
    # project has never been categorised. Was a `[type]` prefix on the
    # description until migration 0004; see `Project.scenario_type`.
    scenario_type: str | None = None

    @field_validator("parent_project", "scenario_description", "scenario_type", mode="before")
    @classmethod
    def _empty_str_is_none(cls, v):
        # Coerce empty / whitespace-only strings to None so downstream
        # `if proj.parent_project:` vs `if proj.parent_project is not None:`
        # converge — a scenario is identified by a non-None parent, never
        # by an empty string. Defends against future endpoints that POST
        # an empty body field instead of omitting it.
        if isinstance(v, str) and not v.strip():
            return None
        return v


class CreateScenarioRequest(BaseModel):
    """
    Body for POST /api/projects/{base}/scenarios.

    `name` is the new scenario's project name (a sibling on disk to the
    base; not a sub-folder). `description` is optional, capped at 500 chars
    to keep metadata.json small.

    `scenario_type` is the category. It used to arrive INSIDE `description`
    as a `[type]` prefix; that encoding is retired, and a description still
    carrying one is rejected rather than silently stored (see
    `_reject_legacy_tag`) — accepting both would let the two channels
    disagree about the same project.
    """

    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(None, max_length=500)
    scenario_type: str | None = Field(None, max_length=32)


class UpdateScenarioRequest(BaseModel):
    """
    Body for PATCH /api/projects/{name}/scenario.

    Both fields are optional and only APPLIED when present, so a caller
    changing the category cannot blank the description as a side effect — the
    partial-PUT trap this codebase has hit repeatedly. `null` is a real value
    here (it clears the field); omitting the key leaves it alone. That
    distinction needs `model_fields_set`, which is why the route reads
    `model_dump(exclude_unset=True)` rather than the attributes.
    """

    description: str | None = Field(None, max_length=500)
    scenario_type: str | None = Field(None, max_length=32)


class RenameProjectRequest(BaseModel):
    """
    Body for POST /api/projects/{name}/rename. ``new_name`` is validated
    against the same character / length rules as project creation — the
    server's ``_safe_project_dir`` rejects path-traversal characters and
    enforces the [letters, digits, _, -, ., space] charset.
    """

    new_name: str = Field(..., min_length=1, max_length=64)


class CompareState(BaseModel):
    """
    Compact snapshot of a non-active project's state, suitable for
    side-by-side scenario comparison. Built by GET /api/projects/{name}/compare-state
    by loading the project's network.nc into a transient pypsa.Network,
    computing aggregates, then discarding it. The active singleton is NOT
    touched — the user can compare scenarios without losing in-memory state.
    """

    name: str
    bus_count: int
    generator_count: int
    line_count: int
    load_count: int
    link_count: int
    storage_unit_count: int
    store_count: int
    snapshot_count: int
    # ISO timestamps of the first and last snapshot, if any. MultiIndex
    # (multi-investment-period) networks expose the level-1 (timestep)
    # datetime here; investment periods themselves are out of scope for this
    # quick-compare view.
    snapshot_start: str | None = None
    snapshot_end: str | None = None
    # Generator capacity (MW) summed per carrier — pre-optimisation `p_nom`,
    # the installed value the user typed in. ALWAYS populated.
    installed_capacity_by_carrier: dict[str, float] = Field(default_factory=dict)
    # Optimised `p_nom_opt` per carrier (MW). Only populated when the project
    # has a fresh solve on disk; None for unsolved / stale networks. Frontend
    # should render this when present and fall back to `installed_*` otherwise.
    optimised_capacity_by_carrier: dict[str, float] | None = None
    # Storage-unit capacity (MW), keyed by carrier.
    storage_capacity_by_carrier: dict[str, float] = Field(default_factory=dict)
    # Store ENERGY capacity (MWh), keyed by carrier. Different units from
    # storage (which is power) — naming is intentional.
    store_capacity_by_carrier: dict[str, float] = Field(default_factory=dict)
    # Peak instantaneous demand across all loads, MW. Computed from
    # `loads_t.p_set` when present (typical post-import) and falls back to
    # static `loads.p_set` otherwise.
    peak_demand_mw: float = 0.0
    # Annual delivered energy across all loads, MWh. Time-weighted by
    # `snapshot_weightings.objective` so multi-year or representative-week
    # networks return a meaningful total. Fallback for static-only loads:
    # `Σ|p_set| × snapshot_count`.
    total_energy_mwh: float = 0.0
    # Solver lifecycle. Objective + solve_time come from metadata (last save);
    # dispatch_status is computed fresh against the loaded network using the
    # same classifier the active /results gate uses, so a "fresh"/"stale"/"none"
    # reading is consistent across both views.
    objective: float | None = None
    solve_time: float | None = None
    dispatch_status: str = "none"
    # Scenario-tree fields piped through unmodified for the compare view.
    parent_project: str | None = None
    scenario_description: str | None = None
    scenario_type: str | None = None
    created_at: str | None = None
    last_saved: str | None = None


# ── Results-Summary types (Compare Scenarios v2) ─────────────────────────────
# Per-tab payload built by GET /api/projects/{name}/results-summary. The same
# transient-network pattern as /compare-state — netcdf is loaded, aggregates
# computed, network discarded. The active singleton is never touched.

class CarrierPeriodValue(BaseModel):
    """
    A scalar metric with both an aggregated value and a per-period
    breakdown. For flat (single-period) networks, ``by_period`` is empty and
    only ``total`` carries the metric. For multi-period networks ``total`` is
    the weighted sum across periods and ``by_period`` keys are period years as
    strings (e.g. ``{"2026": ..., "2027": ...}``).
    """

    total: float = 0.0
    by_period: dict[str, float] = Field(default_factory=dict)


class CapacityComparison(BaseModel):
    """
    Capacity-expansion view: installed + built capacity per carrier, plus
    the annuitised CAPEX of new builds, both broken down per investment period
    so the user can see the chronological investment plan side-by-side.
    """

    # Optimised p_nom_opt by carrier (MW). `by_period[P]` is the capacity IN
    # SERVICE during period P, not a per-period increment: pre-existing
    # (brownfield) capacity is replicated into EVERY period's bucket by
    # `_bucket_replicate_per_period` (routers/compare.py) — without that
    # replication, switching the Compare View's period selector from "All"
    # to a single period would make in-service brownfield capacity vanish
    # from the bar — plus, for that period only, any new capacity/vintage
    # whose build_year equals P (each build-year increment lands in its own
    # period's bucket, not carried forward into later periods). Because
    # `total` counts brownfield once but every period's bucket counts it
    # again, `sum(by_period.values())` is NOT equal to `total` and must
    # never be treated as a per-period breakdown that sums to it — see
    # `compare_support.py`'s KIND registry, which classifies this field STOCK
    # for exactly that reason.
    capacity_mw_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # TOTAL annuitised CAPEX in M€ (existing fleet + new build), per carrier.
    # Computed as ``Σ p_nom_opt × annuitised_capital_cost × ipw.years[P]`` per
    # period — matches the live ``/results/cost_breakdown`` per-carrier numbers
    # so users can reconcile the compare view with what they see in the
    # Results panel. When an asset has only ``overnight_cost`` set, the
    # annuitised value is recovered via
    # ``overnight_cost × annuity(discount_rate, lifetime)`` — same fallback
    # path the LP uses internally.
    capex_meur_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # New (incremental-only) CAPEX in M€ — only the ``max(0, p_nom_opt − p_nom)``
    # delta. Useful for "what additional investment did this scenario commit?"
    # but can be misleadingly small when an expensive existing fleet wasn't
    # extended. The TOTAL field above is the headline; this one is the
    # secondary view.
    new_capex_meur_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # New (incremental-only) CAPACITY in MW — ``max(0, p_nom_opt − p_nom)`` per
    # asset, summed per carrier. Mirrors `new_capex_meur_by_carrier` but for
    # capacity rather than cost. Without this, the Compare View's
    # `capacity_mw_by_carrier` row for a fixed-capacity asset (heat-dump 500 MW
    # in both scenarios) reads as "both scenarios BUILT 500 MW of heat-dump"
    # when it's actually brownfield never expanded. Surfaces the "built" delta
    # separately so users can distinguish inherited fleet from new investment.
    # GENERATORS ONLY — storage / link builds live in their own maps below.
    # Mixing them here was a bug: the Compare View pairs this map (built) with
    # `optimised_capacity_by_carrier` (generators-only total), so a storage or
    # link carrier showed total=0 but built>0 (e.g. battery 0 MW total / 427 MW
    # built). Keeping each component class in its own map makes every table
    # internally consistent (total ≥ built per carrier).
    new_capacity_mw_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # Storage power capacity (MW) per carrier — separate accounting from
    # generators because the unit semantics differ (storage_units has p_nom
    # AND max_hours, generators only p_nom).
    storage_mw_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # Storage energy capacity (MWh): p_nom_opt × max_hours summed per carrier.
    storage_mwh_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # New (built) storage capacity — MW and MWh increments, storage_units +
    # stores. Lets the Storage table show "built" columns consistent with the
    # generator table, instead of folding storage builds into the generator
    # map.
    new_storage_mw_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    new_storage_mwh_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # Link power capacity (MW) per carrier — heat-pumps, electrolyzers,
    # datacenters, P2X converters. Total (brownfield + built) and built-only,
    # so the Compare View can render a dedicated Links table without polluting
    # the generator capacity table.
    link_capacity_mw_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    new_link_capacity_mw_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)


class DispatchComparison(BaseModel):
    """
    Dispatch view: energy by carrier (GWh) and operational expenditure
    (M€). Operational expenditure includes the variable OPEX (marginal_cost ×
    dispatch) — matches what /results/cost_breakdown reports as OPEX(mc).
    """

    # GWh dispatched per carrier (Σ p × snapshot_weight × period_weight, /1000).
    dispatch_gwh_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # OPEX in M€ — Σ marginal_cost × p × snapshot_weight × period_weight, /1e6.
    opex_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Total system load (GWh) — useful as a denominator for energy-mix shares
    # and as a sanity check that two scenarios serve the same demand.
    total_load_gwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Equivalent full-cycles per storage carrier (fleet-level): a "cycle"
    # is one full charge + discharge of total energy capacity. Computed as
    # ``Σ |p| × weight / (2 × Σ p_nom_opt × max_hours)`` across all units
    # in the carrier — the standard metric for "how hard is the battery
    # fleet working?" Per-period values divide by years × ipw to give
    # cycles-per-year so two scenarios with different horizon lengths
    # are still directly comparable.
    storage_cycles_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)


class LineLoadingEntry(BaseModel):
    """
    Per-line / per-transformer / per-link loading row for the Comparison view.

    For passive branches (lines, transformers) loading = ``|p0| / s_nom_opt``;
    for Links it's ``|p0| / p_nom_opt`` — both expressed as a fraction
    (0.0 = idle, 1.0 = at capacity). The three time-aggregated metrics
    each carry a per-period breakdown — same shape as everywhere else in
    the comparison schema.

    Sector-coupling networks (heat pumps, electrolysers, H2 pipelines, P2X)
    rely on Links to move energy across carriers — without them this tab
    showed only the electrical-corridor view. ``is_link=True`` + ``carrier``
    lets the frontend distinguish them visually and group by carrier.
    """

    name: str
    s_nom_opt: float = 0.0                                  # MW, post-solve
    is_transformer: bool = False
    is_link: bool = False
    carrier: str | None = None                              # populated for Links; None for passive branches
    peak_loading: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)  # max over snapshots
    mean_loading: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)  # snapshot-weighted mean
    # Hours where loading ≥ 0.99 — proxy for "binding N-0 thermal limit".
    # Already snapshot-weight × period-year multiplied so a representative-
    # week network reports the equivalent annual hour count.
    binding_hours: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class LoadingComparison(BaseModel):
    """
    Line and transformer loading view. Both branch types are bundled into
    one list because the user typically wants to see all corridor elements
    ranked together — `is_transformer` lets the frontend differentiate
    visually (e.g. a small icon next to the name).
    """

    # Ordered by peak loading (descending) on the SCENARIO-WIDE aggregate so
    # the worst-loaded branches surface first. Frontend renders top-N.
    lines: list[LineLoadingEntry] = Field(default_factory=list)


class CarrierPriceStats(BaseModel):
    """
    Per-bus-carrier weighted price statistics. Sector-coupled networks
    have very different price regimes by carrier (electrical, H2, heat,
    gas); this lets the Compare View surface "are H2 buses cheaper in
    scenario A vs B" alongside the system-wide duration curve.
    """

    bus_count: int = 0
    mean_price: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    median_price: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    p90_price: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class PricesComparison(BaseModel):
    """
    Marginal-price view: a duration curve (sorted-desc prices) for plot
    overlay, plus per-period mean / median / p90 statistics for the table.

    Duration-curve sampling: we extract `n.buses_t.marginal_price`, flatten
    across (bus, snapshot), sort descending, and sample at 101 percentile
    points (0, 1, …, 100 %). That gives a smooth comparable curve at
    constant payload size regardless of network/snapshot count.

    For multi-period horizons the curve is weighted by snapshot_weightings ×
    investment_period_weightings.years so periods with longer durations
    correctly contribute more density to the curve.
    """

    # Sampled duration curve. Index 0 = highest price (0th percentile), index
    # 100 = lowest. Empty for unsolved networks.
    duration_curve: list[float] = Field(default_factory=list)
    # Per-period aggregate statistics (€/MWh).
    mean_price: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    median_price: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    p90_price: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Extreme tail markers — useful when the duration curve's first point is
    # a single-bus-snapshot scarcity spike (LP dual at a binding constraint
    # like a small fully-utilised line). Without these the user sees the
    # spike on the chart with no context; the Compare View can now show
    # "max: €X / min: €Y" tooltips alongside the curve and clip the y-axis
    # to avoid log-like distortion when one outlier dominates the scale.
    max_price: float = 0.0
    min_price: float = 0.0
    # Convenience scalar for the chart axis label / sanity check.
    bus_count: int = 0
    # Per-bus-carrier weighted stats — buses grouped by their `carrier`
    # attribute. Lets the Compare View show that electrical buses have
    # high marginal prices while a downstream H2 bus is cheaper, etc.
    by_carrier_stats: dict[str, CarrierPriceStats] = Field(default_factory=dict)


class EmissionsComparison(BaseModel):
    """
    CO2 emissions view: total kilotons + per-emitter-carrier breakdown +
    intensity (kg/MWh of dispatched energy) per period.

    ``intensity_kg_per_mwh`` divides total CO2 emissions by total system
    dispatch (all carriers, including zero-CO2 renewables) so it reflects
    the carbon-content-per-delivered-MWh of the whole portfolio — a single
    headline number to compare scenarios beyond raw kt.
    """

    total_kt: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    by_carrier_kt: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    intensity_kg_per_mwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class CarrierEconomics(BaseModel):
    """
    Per-carrier economic roll-up. Revenue + total OPEX + per-cost-type
    breakdown + annuitised CAPEX + LCOE, each broken down per period.

    LCOE is recomputed from the underlying components rather than carried
    as raw input, so the frontend doesn't have to do its own algebra and
    the per-period value uses that period's costs and dispatch.

    `opex_meur` is the headline TOTAL — sum of gen_cost + charge_cost +
    curtailment_cost + lost_load_cost. The individual breakdown fields
    let the user see WHAT they paid for, per carrier (e.g. heat carrier:
    €X gen_cost + €Y curtailment_cost + €Z lost_load_cost). All values
    are in M€.
    """

    revenue_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Headline total OPEX = gen + charge + curtailment + lost_load.
    opex_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Marginal-cost OPEX from generators on this carrier (Σ p × mc × w).
    # NOT storage charge cost (that's split out into storage_charge_cost_meur).
    gen_cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Net cost the storage units paid for charging — Σ |charge| × bus_price × w.
    # Can be NEGATIVE when bus prices are negative during charging hours
    # (e.g. solar-curtailment-driven negative AC prices).
    storage_charge_cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Renewable curtailment penalty Σ over carrier's generators of
    # (max_p_max_pu × p_nom_opt − dispatch) × curtailment_cost × weights.
    curtailment_cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Lost-load cost = Σ over carrier's load-bearing buses of VOLL slack
    # dispatch × VOLL × weights.
    lost_load_cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    capex_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    dispatch_gwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    lcoe_eur_per_mwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class AssetLCOHEntry(BaseModel):
    """
    Per-Link levelised-cost view for the Economics comparison tab.

    For each extendable Link with non-zero dispatch (electrolysers,
    heat pumps, P2X, H2 pipelines), computes the levelised cost of its
    primary output. Output = ``|p0| × efficiency`` in MWh of the
    bus1-side carrier (H2 for electrolysers, heat for heat pumps, etc.).
    Cost = annuitised CAPEX (horizon) + OPEX + input-electricity cost
    at bus0's marginal price. LCOH unit = €/MWh of the Link's output
    regardless of carrier label.

    Per-period breakdowns scale by ``investment_period_weightings.years``
    so multi-period horizons stay year-consistent — matches the rest of
    the Compare View.
    """

    name: str
    carrier: str | None = None
    p_nom_opt: float = 0.0
    capex_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    opex_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    input_cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    output_gwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    lcoh_eur_per_mwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class EconomicsComparison(BaseModel):
    """Per-asset-group economic summary used by the Economics comparison tab."""

    by_carrier: dict[str, CarrierEconomics] = Field(default_factory=dict)
    # Per-Link levelised-cost rows (one per extendable Link with non-trivial
    # dispatch). Lets users compare H2 plant economics across scenarios.
    # Carrier aggregation in `by_carrier` answers "what does the H2 fleet
    # cost on average?"; this list answers "which individual electrolyser
    # is most/least competitive in scenario A vs B?".
    per_asset_lcoh: list[AssetLCOHEntry] = Field(default_factory=list)


class CurtailmentComparison(BaseModel):
    """
    Per-carrier renewable curtailment view.

    Curtailment is the energy that COULD have been dispatched (``p_max_pu ×
    p_nom_opt``) but wasn't (clipped to ≥ 0 to ignore negative-headroom artefacts
    from solver tolerances). Reported in GWh and as a percentage of the
    max-available envelope so the user can see both absolute waste AND relative
    inefficiency. Only generators with a time-varying ``p_max_pu`` profile
    contribute — thermal units with a flat ``p_max_pu=1`` would otherwise
    inflate the result with "unused dispatchable capacity," which is a
    different concept.
    """

    total_gwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    by_carrier_gwh: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # Curtailment rate per carrier (%). Equals ``curtailed / (curtailed +
    # dispatched)`` averaged over the period. Comparable across scenarios
    # regardless of fleet size — useful headline for "how badly is this
    # renewable squeezed in scenario A vs B?"
    rate_pct_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    # System-wide curtailment rate (%) = ``Σ curtailed / Σ (curtailed +
    # dispatched)`` across all renewable carriers. One scalar to compare
    # scenarios at a glance.
    system_rate_pct: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class LostLoadBus(BaseModel):
    """
    Per-bus lost-load aggregate: energy unserved and the associated
    VOLL cost. ``by_period`` keys mirror the project's investment periods.
    """

    bus: str
    energy_mwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class LostLoadByCarrier(BaseModel):
    """
    Per-bus-carrier lost-load roll-up. Sums per-bus shedding across
    buses sharing a carrier. Useful for "is the load-shedding concentrated
    on the electrical side or on heat/H2?" — the per-bus list answers the
    where, this answers the which-energy-type.
    """

    carrier: str
    bus_count: int = 0
    energy_mwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class LostLoadComparison(BaseModel):
    """
    Voluntary-load-loss / VOLL slack dispatch view.

    Populated only when the project's ``results_state.pkl`` carries a
    lost-load capture — i.e. the solver ran with ``voll > 0`` AND the LP
    shed at least one MW. ``available=False`` covers three states the
    frontend distinguishes: ``voll`` was zero, the project hasn't been
    solved, or no shedding occurred (happy path). When ``available=True``,
    ``total_mwh.total`` is guaranteed > 0.
    """

    available: bool = False
    # VOLL price the solver used (€/MWh). Recovered from
    # ``lost_load_cost_eur / lost_load_total_mwh`` so the frontend doesn't
    # divide-by-zero on the edge case.
    voll_eur_per_mwh: float = 0.0
    total_mwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    total_cost_meur: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    # Per-bus breakdown, sorted by horizon-wide energy (descending). Capped
    # to keep the payload bounded on networks with thousands of buses; the
    # frontend renders the top entries in a table.
    by_bus: list[LostLoadBus] = Field(default_factory=list)
    # Per-bus-carrier roll-up — sums shedding across buses sharing a carrier
    # (electrical / H2 / heat / gas etc.). Lets the Compare View answer
    # "is shedding concentrated on one carrier?" without scanning every bus.
    by_carrier: list[LostLoadByCarrier] = Field(default_factory=list)


class StorageUnitCycles(BaseModel):
    """
    Per-storage-unit cycling detail. ``cycles`` is throughput / (2 ×
    energy_capacity), the standard equivalent-full-cycle metric — the same
    convention used in DispatchComparison's fleet-level rollup.
    """

    name: str
    carrier: str
    p_nom_mw: float = 0.0          # post-solve p_nom_opt
    energy_mwh: float = 0.0        # p_nom_opt × max_hours
    throughput_mwh: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)
    cycles: CarrierPeriodValue = Field(default_factory=CarrierPeriodValue)


class StorageCyclingComparison(BaseModel):
    """
    Detailed storage-cycling view: per-asset breakdown plus the
    carrier-level rollup that previously lived in DispatchComparison.
    A standalone tab makes it easier to compare A/B for the storage
    fleet specifically, with one row per unit.
    """

    cycles_by_carrier: dict[str, CarrierPeriodValue] = Field(default_factory=dict)
    by_unit: list[StorageUnitCycles] = Field(default_factory=list)


class ResultsSummary(BaseModel):
    """
    Full per-tab comparison payload for a non-active project. Each tab's
    data lives in its own optional field — frontend slices per tab. Built by
    GET /api/projects/{name}/results-summary. Future phases (curtailment,
    lost-load, storage-cycling) will fill in additional optional fields
    without breaking existing consumers.
    """

    project: str
    is_multi_period: bool = False
    # Sorted unique investment-period years. Empty for flat networks. Used by
    # the frontend to populate the period selector and to derive labels for
    # by_period keys (which are stringified ints).
    periods: list[int] = Field(default_factory=list)
    # True iff the loaded network has a fresh solve (dispatch_status == fresh).
    # The capacity/dispatch fields are populated regardless, but if has_solve
    # is False the capacity panel will fall back to installed p_nom (no
    # p_nom_opt) and dispatch numbers will be all-zero — the frontend uses
    # this flag to render a "scenario not solved" badge instead of charts.
    has_solve: bool = False
    # Phase 1.
    capacity: CapacityComparison | None = None
    dispatch: DispatchComparison | None = None
    # Phase 2.
    loading: LoadingComparison | None = None
    prices: PricesComparison | None = None
    # Phase 3.
    emissions: EmissionsComparison | None = None
    economics: EconomicsComparison | None = None
    # Phase 4.
    curtailment: CurtailmentComparison | None = None
    lost_load: LostLoadComparison | None = None
    storage_cycling: StorageCyclingComparison | None = None
