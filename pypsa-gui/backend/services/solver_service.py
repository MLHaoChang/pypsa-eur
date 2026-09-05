import logging
import logging.handlers
import pathlib
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import pypsa

# ── Re-export façade: services/solver/ ───────────────────────────────────────
# These names are DEFINED in `services/solver/`, not here. They are imported
# back so that `services.solver_service` stays the single import surface for
# the whole solver layer — `routers/results.py`, `routers/compare.py`,
# `services/cost_totals.py`, `services/asset_results/compute.py` and nine test
# modules all import them from here, and none of them changed when the code
# moved. The imports are re-exports, not uses; `solver_service` itself calls
# only `fill_periodized_cost_defaults`.
#
# The carved modules never import back from here — dependencies run one way,
# and `tests/test_solver_facade_surface.py` enforces both halves of that.
from services.solver.diagnostics import (  # noqa: F401
    _diagnose_infeasibility,
    _emit_core_post_solve_diagnostics,
    _log_cost_decomposition_post_solve,
    _log_global_constraint_shadow_prices,
    _log_sclopf_post_solve,
)
from services.solver.myopic import (  # noqa: F401
    _capture_extendable_p_nom_opt_to_frozen_store,
    _defer_future_vintage_builds,
    _freeze_period_capacities,
    _outages_active_in_period,
    _patch_passive_branch_holes,
    _run_myopic_foresight,
)
from services.solver.objective import (  # noqa: F401
    _log_objective_conditioning,
    _objective_conditioning,
    _rescale_results_for_objective,
    _wrap_with_capex_budget,
    _wrap_with_curtailment_cost,
    _wrap_with_objective_scale,
)
from services.solver.runtime import (  # noqa: F401
    SolveAborted,
    _AbortWatcher,
    _RollingWindowFailureCatcher,
    _SolveHeartbeat,
    _ThreadScopedQueueHandler,
    _check_stop,
    _safe_log,
    check_solver_availability,
)
from services.solver.assumptions import (  # noqa: F401
    _DISPATCH_FIX_ACCESSORS,
    _apply_modelling_assumptions,
    _canonical_load_carrier_key,
    _clear_dispatch_fix,
    _compute_loss_atol,
    _normalise_dynamic_indexes,
    _resolve_mip_kwargs,
    _resolve_presolve_kwargs,
    _sanitise_transformer_types,
    resolve_branch_outages,
)
from services.solver.vintage_store import (  # noqa: F401
    _MYOPIC_VINTAGE_SOURCE,
    _frozen_vintage_store,
)
from services.solver.periodized_costs import (  # noqa: F401
    _annuity,
    _pv_factor_series,
    _reference_build_year,
    fill_periodized_cost_defaults,
    periodized_capital_costs,
    with_periodized_cost_defaults,
)
from services.validation_service import has_errors, validate_for_run





@dataclass
class SolverConfig:
    solver_name: str = "highs"
    mode: Literal["lopf", "pf"] = "lopf"
    transmission_losses: bool = False
    multi_investment_periods: bool = False
    solver_options: dict = field(default_factory=dict)
    extra_functionality_code: str = ""
    # ── Modelling assumptions (transient at solve time) ────────────────────
    # These five are applied via _apply_modelling_assumptions() right before
    # n.optimize() and reverted in a try/finally so the network state on disk
    # never reflects the LP transforms.
    discount_rate: float = 0.07         # used in annuity calc on extendable assets
    # Expected inflation rate (decimal, e.g. 0.02 = 2 %). Treated together
    # with ``discount_rate`` to derive the REAL discount used in the per-period
    # PV factor when ``auto_discount_periods`` is on:
    #   real_r = (1 + discount_rate) / (1 + inflation_rate) − 1
    # Default 0.0 ⇒ identity (real_r = discount_rate), so existing solver
    # configs that don't set this field behave exactly as before. PyPSA's
    # annuity (capital_cost = overnight × annuity(r, L)) keeps using
    # ``discount_rate`` directly — users who entered a REAL rate there keep
    # that interpretation. The inflation knob only modulates the cross-period
    # PV factor, which is the only place where the gap between nominal and
    # real matters for LP behaviour.
    inflation_rate: float = 0.0
    default_lifetime: float = 25.0      # years; fallback when asset.lifetime is empty
    co2_price: float = 0.0              # €/tCO2 — added to fossil marginal_cost
    # Per-investment-period CO2 price override. Keyed by period year as str
    # (JSON keys are always strings), value €/tCO2. When non-empty AND the
    # network is multi-period, each snapshot's surcharge uses the price for
    # that snapshot's period: surcharge[g,t] = price[period(t)] × co2[carrier(g)] / efficiency[g].
    # Periods missing from the dict fall back to the scalar `co2_price`.
    # On flat (single-period) networks this dict is ignored — log a warning
    # at solve time so the user notices the silent no-op.
    co2_price_per_period: dict = field(default_factory=dict)
    voll: float = 0.0                   # €/MWh — when >0, slack gens get added per bus
    investment_periods: list = field(default_factory=list)  # list[int] of years
    # Per-investment-period load scaling. Keyed by period year as str (JSON
    # object keys are always strings), value is a multiplier (1.0 = 100 %,
    # unchanged). Applied transiently in _apply_modelling_assumptions: every
    # value in n.loads_t.p_set for period P is multiplied by
    # load_scalers[str(P)] before the LP and reverted after. Models load
    # growth across the horizon (e.g. {"2026": 1.0, "2027": 1.05,
    # "2030": 1.18}). Multi-period only; ignored for flat networks.
    #
    # LEGACY field: applied to ALL loads regardless of carrier. Use
    # `load_scalers_by_carrier` for per-carrier growth profiles.
    load_scalers: dict = field(default_factory=dict)
    # Per-carrier per-period load scaling. Outer key = carrier identifier
    # (canonical form matches loadCarrierKey in the frontend: 'electrical',
    # 'hydrogen', 'heat', or a passthrough carrier string), inner key =
    # period year (str), value = multiplier.
    #
    # Resolution order at LP time, per (load, period):
    #   1. `load_scalers_by_carrier[carrier_key][period_str]` (most specific)
    #   2. `load_scalers[period_str]` (legacy fallback, all carriers)
    #   3. 1.0 (identity — no scaling)
    #
    # Carrier matching uses `_canonical_load_carrier_key` (defined below)
    # which mirrors the frontend's `loadCarrierKey` so the same alias set
    # collapses '', 'AC', 'electricity', … into 'electrical'.
    load_scalers_by_carrier: dict = field(default_factory=dict)
    # ── Period discounting (PV via ipw.objective) ──────────────────────────
    # When True, _apply_modelling_assumptions overwrites
    # `n.investment_period_weightings.objective[P]` with the present-value
    # factor `(1 + discount_rate)^-(P - ref_year) × period_years[P]`. PyPSA's
    # LP multiplies every cost (capex AND opex) per period by this weight,
    # so this knob applies a uniform social-discount to all future periods
    # — making the LP prefer building close to when capacity is needed
    # instead of "front-load everything in the first period" (the
    # undiscounted optimum). Reference year = first period in the horizon.
    # Reverted in restore() so the on-disk network keeps user-set weights.
    # Multi-period only; ignored for flat networks.
    auto_discount_periods: bool = False
    # ── CAPEX budget per period ────────────────────────────────────────────
    # Per-period upper bound on total NEW investment (overnight CAPEX). Keyed
    # by period year as str (JSON object keys are always strings), value in
    # EUR. Applied as the LP constraint:
    #   Σ over extendable assets with build_year=P:
    #     overnight_cost_g × Δp_nom_g ≤ capex_budget_per_period[str(P)]
    # Empty / missing entries are unconstrained (no upper bound for that
    # period). Models real-world planning constraints — utilities can't
    # build unlimited capacity in any single year regardless of LP
    # economics. Applied via the extra_functionality wrapper alongside the
    # curtailment-cost penalty.
    capex_budget_per_period: dict = field(default_factory=dict)
    # ── N-1 Security-Constrained LOPF ──────────────────────────────────────
    # When `sclopf=True` AND mode=="lopf", we route through
    # `n.optimize.optimize_security_constrained()`. The list of branches
    # treated as possible outages is composed from the four flags below;
    # see _resolve_branch_outages() for the merge logic.
    sclopf: bool = False
    sclopf_include_all_lines: bool = False
    sclopf_include_all_transformers: bool = False
    sclopf_voltage_threshold_kv: float = 0.0
    sclopf_extra_lines: list = field(default_factory=list)
    sclopf_extra_transformers: list = field(default_factory=list)
    # Contingency scope when SCLOPF runs under the myopic foresight strategy.
    # Ignored on the full-horizon SCLOPF path (which has only one snapshot
    # window anyway).
    #
    #  • "horizon"          — every snapshot in each myopic iteration
    #                         (current-period hourly + future-period
    #                         representative) is contingency-constrained.
    #                         Matches N-1-must-always-hold TSO convention.
    #  • "current_period"   — only the current-period hourly snapshots get
    #                         contingency constraints; the future-period
    #                         representative tail (when limited foresight is
    #                         on) is dropped from THIS iteration's LP
    #                         entirely. Cheaper, focuses safety on near-term
    #                         dispatch. Trade-off: limited foresight becomes
    #                         a no-op for SCLOPF iterations — capacity is
    #                         sized without forward-looking demand growth.
    #
    # Future-dated outage candidates (build_year > current_period) are
    # silently filtered out of any given iteration's contingency set — they
    # haven't been built yet, so contingencies against them aren't
    # meaningful. They re-enter the contingency set in the iteration whose
    # current_period >= build_year (i.e. once the line/transformer exists).
    sclopf_scope: Literal["horizon", "current_period"] = "horizon"
    # ── Stage 2: post-solve AC Power Flow ───────────────────────────────────
    # When True, run_simulation chains a Newton–Raphson AC PF after the LOPF
    # / SCLOPF stage with the dispatch fixed to the LP solution. Produces real
    # flows, voltages, and per-snapshot convergence. See `run_ac_pf_stage`.
    # When False, the user can still trigger Stage 2 on a previously-solved
    # network via the dedicated `POST /api/simulation/run_ac_pf` endpoint.
    run_ac_pf_after_lopf: bool = False
    # Slack bus override. Empty string ⇒ auto-pick the bus with the largest
    # aggregate generation (sum of p_nom over generators at that bus).
    ac_pf_slack_bus: str = ""
    # Newton-Raphson tolerance passed through to PyPSA's n.pf(x_tol=...).
    # PyPSA does NOT expose a max-iter knob; iterations are capped by the
    # tolerance alone. Adding a max_iter field here would mislead the UI.
    ac_pf_x_tol: float = 1e-6
    # ── Solver-presolve toggle ─────────────────────────────────────────────
    # Default ON: presolve typically cuts solve time 2-10× by eliminating
    # redundant rows/cols. Off only useful when debugging infeasibility (presolve
    # often masks the original culprit row). The dispatcher in
    # _resolve_presolve_kwargs() maps this to the solver-specific knob.
    presolve_enabled: bool = True
    # ── LP objective numerical-conditioning scale ──────────────────────────
    # Multiplier applied to the linopy model's objective expression right
    # before `model.solve()`. Affects solver INTERNAL numerics only — the
    # optimal x* is identical regardless of this factor (a linear-program
    # invariant: scaling the objective by a positive constant doesn't move
    # the argmin). The reported `n.objective` is divided back by the same
    # factor after the solve so user-facing € values stay in the original
    # units.
    #
    # When to use a non-default value:
    #   • LP costs spanning many orders of magnitude (e.g. capital_cost in
    #     €M alongside marginal_cost in €/MWh) can hit HiGHS / Gurobi's
    #     "objective value too large" / numerical-conditioning warnings.
    #     Setting scale < 1 (e.g. 1e-6 to convert € → M€) reduces coefficient
    #     range.
    #   • Tiny-LP precision issues (rare) — scale > 1 can recover bits.
    #   • 1.0 = identity (default).
    #
    # Range enforced at solve time: must be > 0 and finite. Anything else
    # is treated as 1.0 (a warning is logged so the user notices).
    user_objective_scale: float = 1.0
    # ── Automatic objective conditioning ───────────────────────────────────
    # When True, the solver picks an objective scale ITSELF (a power of 10)
    # from the spread of the model's cost coefficients, overriding
    # `user_objective_scale`. It only engages for genuinely ill-conditioned
    # models (geometric-mean coefficient far outside a healthy band) and is a
    # no-op otherwise — so leaving it on is safe for well-behaved networks.
    # The reported `n.objective` / duals are divided back exactly as for the
    # manual scale, so user-facing € are unchanged. Default OFF — the
    # always-on `[NUMERICS]` diagnostic recommends a value first; this just
    # applies it without a manual step.
    auto_objective_scale: bool = False
    # ── MIP knobs ──────────────────────────────────────────────────────────
    # Only consumed by the LP backend when at least one generator has
    # committable=True (the LP becomes a MILP — branch-and-bound over the
    # status / start_up / shut_down binaries). Defaults are PyPSA-friendly:
    # 1 % gap (good enough for capacity-planning UC), unbounded time.
    # `_resolve_mip_kwargs` maps to solver-specific keys, same dispatcher
    # pattern as presolve.
    mip_gap: float = 0.01       # relative MIP gap, decimal
    mip_time_limit_s: float = 0  # seconds; 0 = no time limit
    # ── Solve strategy ──────────────────────────────────────────────────────
    # Three modes:
    #
    # • `"full"`     — single-shot LP over every snapshot. The default. Use
    #                  when the model fits comfortably in solver memory.
    # • `"rolling"`  — operational rolling via PyPSA's
    #                  `optimize_with_rolling_horizon(horizon=H, overlap=O)`.
    #                  Slices snapshots sequentially; carries storage SoC
    #                  via overlap. Designed for pure-dispatch problems —
    #                  rejected at validation when multi_investment_periods
    #                  is on (each window's LP solves an independent
    #                  capacity problem and later periods shed load
    #                  instead of expanding). Not compatible with SCLOPF
    #                  or auto-chained AC PF.
    # • `"myopic"`   — multi-period capacity expansion solved one period at
    #                  a time. For each period in order: solve an LP that
    #                  only sees that period's snapshots; freeze the
    #                  resulting capacities (extendable=False, p_nom =
    #                  p_nom_opt); move forward. Requires
    #                  multi_investment_periods=True. Phase 1 of the
    #                  "limited-foresight with aggregated future tail"
    #                  approach — Phase 2 will plug aggregated future-
    #                  period snapshots into `_build_iteration_snapshots`
    #                  so each iteration has forward visibility.
    solve_strategy: Literal["full", "rolling", "myopic"] = "full"
    rolling_horizon: int = 168
    rolling_overlap: int = 24
    # ── Limited-foresight (Phase 2 of myopic) ──────────────────────────────
    # When `solve_strategy == "myopic"` and `lf_aggregate_future == True`, each
    # rolling iteration includes the CURRENT period at full hourly detail PLUS
    # `lf_k_periods` representative blocks (each `lf_period_length_h` hours
    # long) for every future period — clustered via tsam, weighted so each
    # representative stands in for its cluster of original periods. Gives the
    # rolling solver forward visibility (so it doesn't under-build for known
    # demand growth) without the full LP's solve cost. Off by default — pure
    # myopic is the safe baseline.
    lf_aggregate_future: bool = False
    lf_k_periods: int = 8
    lf_period_length_h: int = 168           # 168 = weekly; 24 = daily
    lf_cluster_method: str = "hierarchical"  # tsam: hierarchical | k_means | k_medoids
    lf_include_extreme: bool = True         # append peak-load + renewable-drought period

    def __post_init__(self):
        # ── Sanitize solver_options ───────────────────────────────────────
        # solver_options is a free-form dict forwarded to the solver (HiGHS,
        # Gurobi, …) for solver-specific tuning. Users sometimes accidentally
        # nest top-level SolverConfig fields here (notably `user_objective_scale`),
        # producing the documented "duplicate field, ambiguous precedence" bug:
        # the LP reads the top-level value via `cfg.user_objective_scale`, while
        # the nested copy gets passed to HiGHS as an unknown option name. If the
        # nested value disagrees with the top-level value, the user's intent is
        # silently lost.
        #
        # Strategy: at construction time, if any top-level field name appears
        # inside solver_options, lift the nested value to the top level WHEN
        # the top-level field is at its default — otherwise drop the nested
        # copy and let the top-level value win. Either way, solver_options no
        # longer shadows the top level.
        if not isinstance(self.solver_options, dict):
            return
        # Build the field name → default value map from the dataclass spec.
        from dataclasses import fields as _dc_fields
        defaults = {}
        for f in _dc_fields(self):
            if f.name == "solver_options":
                continue
            if f.default is not field:  # type: ignore[comparison-overlap]
                defaults[f.name] = f.default
        for key in list(self.solver_options.keys()):
            if key not in defaults:
                continue  # legitimate solver-specific option
            nested_val = self.solver_options.pop(key)
            top_val = getattr(self, key, None)
            top_is_default = top_val == defaults[key]
            if top_is_default and nested_val != defaults[key]:
                # Migrate nested → top
                try:
                    setattr(self, key, type(defaults[key])(nested_val))
                except (TypeError, ValueError):
                    pass




def run_simulation(
    config: SolverConfig,
    network: pypsa.Network,
    lock: threading.Lock,
    stop_event: threading.Event,
    log_queue: queue.SimpleQueue,
    state_update: Callable[..., None] | None = None,
) -> tuple[str, str]:
    # `state_update` is the sink for this solve's side-results (lost-load,
    # AC-PF). It's injected (not imported) so a multi-project dispatcher can
    # point it at the SOLVING project's ProjectSolverState rather than the
    # foreground module-global `_state` — a background solve must not clobber
    # the project the user is currently viewing. The `/run` worker passes
    # `routers.simulation._state_update`; the standalone qa_*.py scripts pass
    # nothing (None) — they inspect the network directly and don't need the
    # state side-channel, so the writes below are simply skipped for them.
    def _emit_state(**kw) -> None:
        if state_update is not None:
            state_update(**kw)
    queue_handler = _ThreadScopedQueueHandler(log_queue)
    root_logger = logging.getLogger()
    root_logger.addHandler(queue_handler)

    # Phase markers — defined first so the gate-error branch can use them.
    def phase(msg: str) -> None:
        log_queue.put(f"[PHASE] {msg}")

    t_start = time.time()
    status, condition = "error", "unknown"
    tmp_log = pathlib.Path(tempfile.mktemp(suffix=".log"))
    tail_stop = threading.Event()
    tail_thread: threading.Thread | None = None
    # Abort watcher — injects KeyboardInterrupt into THIS (worker) thread when
    # the user aborts during the native `n.optimize()` window, so a long HiGHS
    # solve is cancelled mid-iteration instead of running to completion. Armed
    # only across the solve region (see `_abort_watcher.arm/disarm` below);
    # outside it the cooperative `_check_stop` handles aborts. No-op when
    # stop_event is None (qa scripts).
    _abort_watcher = _AbortWatcher(stop_event, threading.get_ident())
    _abort_watcher.start()
    # Heartbeat — emits an elapsed-time `[PHASE]` ping every 15s while the LP is
    # in HiGHS's native solve (which can be silent for minutes during presolve).
    # Bracketed by begin()/end() at the SAME points the abort watcher arms/
    # disarms, so it's quiet during setup + post-solve and only chatters while
    # the genuinely-long, log-silent solve is running.
    _heartbeat = _SolveHeartbeat(log_queue)
    _heartbeat.start()
    # Always-bound so the outer except handlers can revert the modelling
    # transforms even when an exception fires in the window between
    # `_apply_modelling_assumptions` and the solve try/finally (an abort at the
    # pre-LP checkpoint, or presolve/MIP option resolution). Reassigned to a
    # once-guarded wrapper after the apply; stays None on the PF path / pre-apply.
    restore_modelling = None
    try:
        # Compile user code + curtailment wrapper INSIDE the try so a
        # ValueError (e.g. user-code disabled gate) or SyntaxError gets
        # surfaced as a graceful `[PHASE] Failed` instead of crashing the
        # worker before any sentinel is emitted on log_queue (which would
        # hang the SSE consumer waiting for `None`).
        extra_fn = _compile_extra_functionality(config.extra_functionality_code)
        # Wrap whatever the user wrote (or nothing) with our auto-curtailment-cost
        # callback if any generator carries a non-zero `curtailment_cost`. This
        # mirrors the user's expectation that "set curtailment_cost on a renewable
        # → the optimiser pays for curtailing it" without forcing them to write
        # any extra_functionality code themselves. The wrapper chains both
        # callbacks so user-provided code still runs.
        extra_fn = _wrap_with_curtailment_cost(network, extra_fn, log_queue=log_queue)
        # Per-period CAPEX budget constraint — applied via extra_functionality
        # so PyPSA's LP picks up the linopy constraint. Only adds work when
        # the config dict is non-empty.
        extra_fn = _wrap_with_capex_budget(network, extra_fn, config, log_queue=log_queue)
        # User-supplied numerical-conditioning scale on the LP objective.
        # Multiplies model.objective by a positive constant right before
        # solve. Adds work only when scale ≠ 1.0.
        extra_fn = _wrap_with_objective_scale(network, extra_fn, config, log_queue=log_queue)

        tmp_log.touch()
        tail_thread = threading.Thread(
            target=_tail_log_file, args=(tmp_log, log_queue, tail_stop), daemon=True
        )
        tail_thread.start()
        with lock:
            phase(
                f"Loading network state — {len(network.buses)} buses, "
                f"{len(network.snapshots)} snapshots, mode={config.mode}, solver={config.solver_name}"
            )

            _check_stop(stop_event, phase, "before validation")
            phase("Running pre-flight validation...")
            issues = validate_for_run(network, config)
            if issues:
                for iss in issues:
                    tag = "ERROR" if iss.severity == "error" else "WARN"
                    where = f"{iss.component_class} '{iss.name}'" if iss.component_class else "(network)"
                    log_queue.put(f"[VALIDATION] {tag}: {where} — {iss.message} [{iss.code}]")
            err_count = sum(1 for i in issues if i.severity == "error")
            warn_count = sum(1 for i in issues if i.severity == "warning")
            if has_errors(issues):
                phase(f"Validation failed: {err_count} error(s). Aborting.")
                # Set the vars (not a literal return) so the terminal
                # failure-classification in the outer `finally` sees the right
                # condition and emits a "validation" failure card.
                status, condition = "error", "validation_failed"
                return status, condition
            phase(f"Validation passed ({warn_count} warning{'s' if warn_count != 1 else ''}).")
            _check_stop(stop_event, phase, "after validation, before modelling assumptions")

            t_solve = time.time()
            # Clean up stale `transformer.type` strings that don't match any
            # row in n.transformer_types. The GUI's presets are display
            # labels, not PyPSA types — leaving them in place crashes the
            # solve. Applies to both solve modes (lopf, pf) since PF also
            # resolves transformer types.
            _sanitise_transformer_types(network, phase)
            # Re-broadcast every user-uploaded time series from `_user_ts`
            # onto the live `_t` tables. Without this, a profile uploaded
            # while the network was flat (DatetimeIndex) ends up missing
            # from `n.generators_t.p_max_pu` when the user later promotes
            # to MultiIndex snapshots, and the LP silently falls back to
            # scalar `p_max_pu = 1.0` — renewables dispatch flat at their
            # rated capacity and the build pattern becomes meaningless.
            # The reapply helper handles all 4 cases (flat↔multi, with
            # broadcasting via level-1 timestep match) and is idempotent.
            # Imported here to avoid a circular top-level import.
            # GATED on "is this the FOREGROUND/active network?" — `_user_ts` is a
            # process-global store that belongs to the ACTIVE project (B6-writes
            # deferred → no per-ctx _user_ts yet). For a foreground solve (/run, or
            # the dispatcher solving the resident foreground in-place) `network IS`
            # the active network → reapply (byte-identical to pre-B4). For a
            # BACKGROUND ctx solve (B4.3 dispatcher on a non-active project) the
            # active `_user_ts` is a DIFFERENT project's store; reapplying it would
            # overlay the foreground's uploaded profiles onto this project's network
            # wherever an asset name collides (corrupting its LP + persisted
            # dispatch). Skip it — the background netcdf already carries baked,
            # solve-ready profiles (`_hydrate_context_from_disk`). Closes the C2
            # end-to-end QA `_user_ts` cross-talk finding.
            try:
                from services.pypsa_service import PyPSAService as _PS
                _is_foreground = network is _PS.get_network()
            except Exception:
                _is_foreground = True  # fail safe → preserve legacy reapply
            if _is_foreground:
                try:
                    from routers.network import _reapply_user_ts_to_network as _reapply_ts
                    _reapply_ts(network)
                    phase("Re-applied user-uploaded time series to network "
                          "(_t tables aligned to current snapshots).")
                except Exception as exc:
                    phase(f"WARN: could not re-apply user time series: {type(exc).__name__}: {exc}")
            else:
                phase("Skipped _user_ts reapply (background project solve — netcdf "
                      "carries baked profiles; the active _user_ts belongs to the "
                      "foreground project).")
            # Belt-and-suspenders index sync: makes sure every _t DataFrame's
            # row index matches n.snapshots before the LP. Catches stale
            # MultiIndex residue from a previous multi-period solve that
            # wasn't fully reset on demotion, which otherwise crashes
            # `assign_duals` with `KeyError: DatetimeIndex(...) not in index`
            # after a successful solve.
            _normalise_dynamic_indexes(network, phase)
            # Clear stale *_t.p_set persisted by a prior AC-PF dispatch fix.
            # PyPSA's create_model adds a `Generator-p_set` equality constraint
            # for every non-null row, locking dispatch. Plain LOPF still solves
            # (it re-produces the same answer) but SCLOPF cannot redispatch to
            # honour contingency LODF constraints — the LP goes infeasible at
            # presolve. Cleared in both modes so PF also sees a clean state
            # whenever the user wants to resolve from scratch.
            _clear_dispatch_fix(network, phase)

            if config.mode == "lopf":
                # SCLOPF dispatches to PyPSA's
                # `n.optimize.optimize_security_constrained()`, which adds
                # one constraint per (branch_outage, monitored_branch) via
                # the LODF matrix. The dispatch decision must be feasible
                # in the base case AND under every contingency. We resolve
                # the user's branch-selection knobs (all lines / all trafos
                # / voltage threshold / extra picks) into a list here.
                use_sclopf = bool(getattr(config, "sclopf", False))
                if use_sclopf:
                    outages = resolve_branch_outages(network, config)
                    if not outages:
                        phase("SCLOPF requested but no branches selected — "
                              "falling back to plain LOPF.")
                        use_sclopf = False
                # Build the dispatch-mode label so the log doesn't mislead
                # the user when myopic + SCLOPF are combined. The actual
                # dispatch goes through `_run_myopic_foresight` (which then
                # routes each iteration through `optimize_security_constrained`),
                # NOT the full-horizon SCLOPF branch.
                _is_myopic_dispatch = (
                    getattr(config, "solve_strategy", "full") == "myopic"
                    and config.multi_investment_periods
                )
                if use_sclopf and _is_myopic_dispatch:
                    phase(
                        f"Optimising via {config.solver_name} (myopic + SCLOPF, "
                        f"{len(outages)} contingency(ies), "
                        f"scope={getattr(config, 'sclopf_scope', 'horizon')})..."
                    )
                elif use_sclopf:
                    phase(f"Optimising via {config.solver_name} (SCLOPF, "
                          f"{len(outages)} contingency(ies))...")
                else:
                    phase(f"Optimising via {config.solver_name} (LOPF)...")
                # Surface the resolved contingency list so the user can
                # verify their selection at a glance. Capped at 8 names to
                # avoid wall-of-text in the log panel for huge sets — the
                # full list is always in the saved solver_config.json.
                if use_sclopf:
                    preview = ", ".join(f"{cls[0]}:{name}" for cls, name in
                                        [(c, n) for c, n in outages[:8]])
                    suffix = "" if len(outages) <= 8 else f" (+{len(outages) - 8} more)"
                    phase(f"  contingency set: {preview}{suffix}")
                # Apply modelling assumptions (discount rate, CO2 price, VOLL,
                # investment periods) only for the LOPF solve — PF/LPF are
                # pure power-flow sims and don't read these cost knobs. The
                # restore callback runs in finally so the on-disk state never
                # reflects the LP transforms — important so export_to_netcdf
                # writes originals and re-solves don't double-apply.
                # `captured` collects solve-only data (VOLL slack dispatch)
                # that the restore step would otherwise wipe.
                _real_restore, captured = _apply_modelling_assumptions(network, config, phase)
                # Once-guarded restore wrapper. The network now carries the LP
                # transforms; restore MUST run exactly once before the network
                # can be serialised. The solve try/finally below calls this on
                # the normal/solve-error path, and the outer except handlers
                # call it too — the guard makes a double-call a no-op and (more
                # importantly) guarantees restore even if the window between
                # here and that try raises (abort checkpoint, presolve/MIP
                # resolution) and skips the inner finally entirely.
                _restore_done = {"v": False}

                def _guarded_restore() -> None:
                    if _restore_done["v"]:
                        return
                    _restore_done["v"] = True
                    _real_restore()

                restore_modelling = _guarded_restore
                # Clear any stale loss DataFrames from a previous solve. PyPSA
                # only writes `lines_t.loss` / `transformers_t.loss` when the LP
                # is built with transmission_losses=True; toggling the kwarg
                # off in a subsequent run leaves the old values in place, which
                # makes the LoadFlow "losses" panel report data from a run that
                # didn't happen. Wipe before each solve so the panel reflects
                # exactly this run.
                try:
                    network.lines_t.loss = network.lines_t.loss.iloc[0:0]
                except Exception:
                    pass
                try:
                    network.transformers_t.loss = network.transformers_t.loss.iloc[0:0]
                except Exception:
                    pass
                # Merge presolve toggle into solver_options. User-supplied
                # values in solver_options win (so power users can override
                # with explicit `{"presolve": "choose"}` etc.).
                merged_solver_options = _resolve_presolve_kwargs(
                    config.solver_name, config.presolve_enabled, config.solver_options
                )
                if merged_solver_options != (config.solver_options or {}):
                    phase(f"Presolve: {'on' if config.presolve_enabled else 'off'} "
                          f"(solver={config.solver_name})")
                # MIP knobs — only relevant when at least one generator is
                # committable (the LP otherwise has no integer variables and
                # the solver ignores mip_gap / time_limit). Same dispatcher
                # pattern as presolve.
                has_committable = (
                    not network.generators.empty
                    and "committable" in network.generators.columns
                    and bool(network.generators["committable"].any())
                )
                if has_committable:
                    merged_solver_options = _resolve_mip_kwargs(
                        config.solver_name,
                        getattr(config, "mip_gap", 0.01),
                        getattr(config, "mip_time_limit_s", 0),
                        merged_solver_options,
                    )
                    phase(
                        f"Unit commitment active: {int(network.generators['committable'].sum())} "
                        f"committable generator(s), MIP gap {getattr(config, 'mip_gap', 0.01):.3g}"
                        + (f", time limit {int(getattr(config, 'mip_time_limit_s', 0))}s"
                           if getattr(config, 'mip_time_limit_s', 0) > 0 else "")
                    )
                # Solve-strategy dispatch. Three modes share the same lifecycle
                # (apply_modelling → optimise → restore) but branch differently
                # at the solve step:
                #   • "myopic"  — sequential per-period LP (this section's
                #                 driver, designed so Phase 2 mixed-resolution
                #                 future tail slots in via _build_iteration_snapshots)
                #   • "rolling" — PyPSA's operational rolling (forbidden with
                #                 multi-period; auto-falls back to full)
                #   • "full"    — default, single-shot LP
                # NOTE: `not use_sclopf` USED to be a guard here back when
                # SCLOPF wasn't supported under myopic. As of the Phase-1
                # myopic+sclopf feature, `_run_myopic_foresight` dispatches
                # internally to `optimize_security_constrained` per iteration,
                # so myopic now subsumes SCLOPF when both are enabled.
                use_myopic = (
                    getattr(config, "solve_strategy", "full") == "myopic"
                    and config.multi_investment_periods
                )
                use_rolling = (
                    getattr(config, "solve_strategy", "full") == "rolling"
                    and not use_sclopf
                )
                if use_rolling and config.multi_investment_periods:
                    phase(
                        "Rolling-horizon is incompatible with multi-investment "
                        "periods (PyPSA's optimize_with_rolling_horizon solves "
                        "each window independently without period weightings, "
                        "so capacity decisions don't coordinate across periods "
                        "and later periods shed load instead of expanding). "
                        "Falling back to full-horizon LP."
                    )
                    use_rolling = False
                if use_rolling:
                    H = max(1, int(getattr(config, "rolling_horizon", 168)))
                    O = max(0, int(getattr(config, "rolling_overlap", 24)))
                    if O >= H:
                        raise ValueError(
                            f"rolling_overlap ({O}) must be < rolling_horizon ({H})."
                        )
                    phase(
                        f"Rolling-horizon solve: {len(network.snapshots)} snapshots "
                        f"in windows of {H} (overlap {O})"
                    )
                if not use_myopic:
                    # A myopic run leaves `_myopic_period_objectives` on the
                    # network, and nothing else clears it. Re-solving the SAME
                    # network full-horizon then left the stale marker in place,
                    # and every "did this run go myopic?" test downstream —
                    # `_compute_run_objective`, the Summary line,
                    # `/results/objective_decomposition` — keys off exactly that
                    # marker. Clear it so the marker means "THIS run was myopic".
                    network._myopic_period_objectives = []
                if use_myopic:
                    # Periods live on n.investment_periods (Snapshots →
                    # Multi-period promotion). cfg.investment_periods is an
                    # optional override and is typically empty.
                    try:
                        _periods_for_log = list(network.investment_periods)
                    except Exception:
                        _periods_for_log = list(config.investment_periods or [])
                    phase(
                        f"Myopic foresight: {len(_periods_for_log)} "
                        f"period(s) solved sequentially at full hourly detail."
                    )
                # Per-iteration capacity-freeze undos for the myopic driver.
                # Stacked on top of `restore_modelling` in the finally block so
                # the network goes back to its pre-solve state regardless of
                # which branch ran (myopic, rolling, sclopf, full).
                # Capacity-freeze undos from completed myopic periods. Pass
                # this list BY REFERENCE into `_run_myopic_foresight` so the
                # partial state is preserved if the user aborts mid-loop —
                # _run_myopic_foresight raises SolveAborted with the list
                # already populated, and the outer `finally` below walks it
                # in reverse to revert the freezes. Without by-reference
                # passing, the inner function's local list disappears with
                # the exception and the network keeps stale extendable=False
                # / p_nom=p_nom_opt rows from completed periods.
                myopic_undo: list = []
                # Re-normalise dynamic indexes AFTER `_apply_modelling_assumptions`.
                # The up-front pass at run_simulation's entry runs BEFORE the
                # modelling step, but step 4 (`set_investment_periods` cfg-only
                # flat→MultiIndex promotion) and step 7 (transient vintage
                # expansion via `n.add()`) can leave `_t` frames with a stale
                # shape / a dropped `.name='snapshot'`. Without this second pass
                # the full-horizon, SCLOPF, and rolling `n.optimize()` branches
                # below crash with cryptic `dim_0` / `'snapshot' is not a valid
                # dimension` errors. The myopic driver re-normalises per
                # iteration (so it's already covered); the call is idempotent
                # and cheap (shape + index comparison), so running it here for
                # every branch is the structural fix.
                fixed_idx = _normalise_dynamic_indexes(network, phase)
                if fixed_idx:
                    phase(
                        f"Normalised {fixed_idx} stale dynamic index/indexes "
                        "after modelling assumptions, pre-LP."
                    )
                # Last cooperative checkpoint before kicking off the LP. ARM the
                # abort watcher across the whole solve try-body (the long native
                # n.optimize() is what matters; post-solve diagnostics are also in
                # the window but harmless — see _AbortWatcher). A user abort now
                # injects KeyboardInterrupt into this thread, which lands at
                # linopy's HiGHS-run poll tick (≤0.1s) → h.cancelSolve() → clean
                # interrupt. Disarmed in the solve `finally` BEFORE the restore
                # walk, so injection never overlaps restore_modelling.
                # Read-only numerical-conditioning report (and a WARN +
                # recommended scale when the objective is ill-conditioned).
                # After modelling assumptions so VOLL slacks / vintage rows are
                # included; before the solve so the user sees it up front.
                _log_objective_conditioning(network, log_queue)
                _check_stop(stop_event, phase, "before LP solve")
                _abort_watcher.arm()
                _heartbeat.begin("Optimising")
                try:
                    if use_myopic:
                        status, condition, _ = _run_myopic_foresight(
                            network, config, phase,
                            merged_solver_options=merged_solver_options,
                            extra_fn=extra_fn,
                            tmp_log=tmp_log,
                            stop_event=stop_event,
                            iteration_undo=myopic_undo,
                        )
                    elif use_sclopf:
                        # PyPSA's optimize_security_constrained has a bug
                        # in its branch_outages handling: anything that's a
                        # list-or-pd.Index gets wrapped with
                        # `pd.MultiIndex.from_product([("Line",), input])`,
                        # which destroys (component, name) tuples and
                        # incorrectly forces every entry to be a Line.
                        # Passing a plain Python tuple bypasses the elif
                        # entirely (tuple isn't list-or-Index), so the
                        # downstream `branches_i.intersection(...)` call
                        # gets the right (Line, name)/(Transformer, name)
                        # tuples and the LP includes both component
                        # classes in the contingency set.
                        status, condition = network.optimize.optimize_security_constrained(
                            branch_outages=tuple(outages),
                            multi_investment_periods=config.multi_investment_periods,
                            extra_functionality=extra_fn,
                            solver_name=config.solver_name,
                            log_fn=str(tmp_log),
                            **merged_solver_options,
                        )
                        _capture_extendable_p_nom_opt_to_frozen_store(network, phase)
                        _rescale_results_for_objective(network, log_queue=log_queue)
                        # PyPSA's SCLOPF leaves NaN in passive-branch _t
                        # outputs for snapshots outside the LP slice; patch
                        # them to 0 so /results/* and comparison views see
                        # consistent data. See `_patch_passive_branch_holes`
                        # docstring for the failure mode this works around.
                        _patch_passive_branch_holes(network, phase)
                    elif use_rolling:
                        # PyPSA's rolling-horizon returns the network itself
                        # (not a (status, condition) tuple) and, on a non-ok
                        # window, only LOGS a warning and CONTINUES — it does NOT
                        # raise. So a clean return does NOT mean every window
                        # solved. Previously this hardcoded status="ok"/"optimal"
                        # unconditionally, masking an infeasible window as a
                        # successful run. Attach a handler that captures each
                        # failed window's (status, condition) for the duration of
                        # the call, then surface a real failure if ANY window
                        # failed.
                        _roll_catcher = _RollingWindowFailureCatcher()
                        _roll_logger = logging.getLogger("pypsa.optimization.abstract")
                        _roll_logger.addHandler(_roll_catcher)
                        try:
                            network.optimize.optimize_with_rolling_horizon(
                                horizon=H,
                                overlap=O,
                                solver_name=config.solver_name,
                                extra_functionality=extra_fn,
                                **merged_solver_options,
                            )
                        finally:
                            _roll_logger.removeHandler(_roll_catcher)
                        _capture_extendable_p_nom_opt_to_frozen_store(network, phase)
                        _rescale_results_for_objective(network, log_queue=log_queue)
                        if _roll_catcher.failures:
                            _fst, _fcond = _roll_catcher.failures[0]
                            phase(
                                f"Rolling-horizon: {len(_roll_catcher.failures)} "
                                f"window(s) failed to solve (first: {_fst}/{_fcond}). "
                                "The dispatch is incomplete — treating the run as "
                                "failed."
                            )
                            status, condition = _fst, _fcond
                        else:
                            status, condition = "ok", "optimal"
                    else:
                        # Auto-scale the secant loss tolerance so small-r lines
                        # don't trip the spurious flow-cap bug (see
                        # _compute_loss_atol docstring). When transmission_losses
                        # is off we pass False as before.
                        tl_kwarg = _compute_loss_atol(network) if config.transmission_losses else False
                        if isinstance(tl_kwarg, dict):
                            phase(f"Transmission losses: secants, atol={tl_kwarg['atol']:.2e} MW (auto-scaled for smallest line)")
                        status, condition = network.optimize(
                            solver_name=config.solver_name,
                            transmission_losses=tl_kwarg,
                            multi_investment_periods=config.multi_investment_periods,
                            extra_functionality=extra_fn,
                            log_fn=str(tmp_log),
                            solver_options=merged_solver_options,
                            # Surface ALL constraint duals (not just the
                            # placeholder set). Without this, line / transformer
                            # capacity duals (mu_upper / mu_lower) are computed
                            # but discarded — the /results/line_duals endpoint
                            # then has nothing to report. ~negligible solver
                            # cost; just extra DataFrame writes after the solve.
                            assign_all_duals=True,
                        )
                        _capture_extendable_p_nom_opt_to_frozen_store(network, phase)
                        # Reverse user_objective_scale on n.objective + LP duals
                        # so the post-solve diagnostics below (and every
                        # downstream /results/* consumer) see user-facing
                        # € values regardless of the scaling factor used.
                        _rescale_results_for_objective(network, log_queue=log_queue)
                        # Same post-solve diagnostics the myopic path emits,
                        # so full-horizon LPs are equally inspectable. Passes
                        # `current_period="ALL"` since the LP solved every
                        # period jointly — there's no single iteration period.
                        if status in ("ok", "optimal"):
                            try:
                                _emit_core_post_solve_diagnostics(network, network.snapshots, "ALL", phase)
                                _log_cost_decomposition_post_solve(network, config, network.snapshots, "ALL", phase)
                                _log_global_constraint_shadow_prices(network, log_queue)
                            except Exception as exc:
                                phase(f"Post-solve diagnostic failed: {exc}")
                finally:
                    # DISARM the abort watcher FIRST — the solve window is over.
                    # From here on the restore walk + modelling-assumption unwind
                    # MUST complete; an injected KeyboardInterrupt mid-restore
                    # would leave vintage rows / slacks on the network. After
                    # disarm, a pending abort is honoured cooperatively (the
                    # KeyboardInterrupt that already landed propagates as normal;
                    # any further abort signal is a no-op here).
                    _abort_watcher.disarm()
                    _heartbeat.end()  # silence pings before the restore walk
                    # Reverse myopic capacity-freezes first (innermost layer),
                    # then unwind the modelling-assumption transforms. Each
                    # entry is ("col", attr, col, idx, original) matching the
                    # convention `_apply_modelling_assumptions` uses, so we
                    # can share the walk implementation. Errors here are
                    # swallowed per-entry so one bad row doesn't strand the
                    # rest of the network in a half-restored state.
                    for action in reversed(myopic_undo):
                        try:
                            if action[0] == "col":
                                _, attr_name, col, idx, original = action
                                df = getattr(network, attr_name, None)
                                if df is None:
                                    continue
                                valid = [i for i in idx if i in df.index]
                                if valid:
                                    df.loc[valid, col] = original.loc[valid]
                            elif action[0] == "call":
                                action[1]()
                        except Exception as exc:
                            phase(f"Myopic restore: skipped one entry ({exc})")
                    restore_modelling()
                # Save captured lost-load (if any) into the simulation state
                # so the /results/lost_load endpoint can serve it.
                if captured.get("lost_load_t") is not None:
                    try:
                        _emit_state(last_lost_load=captured)
                        phase(
                            f"Lost load captured: {captured.get('lost_load_total_mwh', 0):.1f} MWh "
                            f"(cost {captured.get('lost_load_cost_eur', 0):,.0f} EUR)"
                        )
                    except Exception:
                        pass
            elif config.mode == "pf":
                phase("Solving non-linear AC power flow (Newton–Raphson)...")
                network.pf()
                status, condition = "ok", "ok"
            solve_secs = time.time() - t_solve
            phase(
                f"Solve complete (status={status}, condition={condition}, {solve_secs:.2f} s)"
            )

            # Explainability: on a CONFIRMED-infeasible solve, point at the likely
            # structural cause (HiGHS exposes no exact IIS). Read-only, runs only
            # when infeasible — so the hints are safe pointers, never a false
            # block. Gurobi additionally gets its native infeasibility report.
            if status not in ("ok", "optimal") and "infeasible" in str(condition).lower():
                try:
                    _diagnose_infeasibility(network, config, log_queue)
                except Exception as _dexc:
                    phase(f"Infeasibility diagnosis skipped: {_dexc}")

            # Post-process — PyPSA already populates the result DataFrames in
            # place during n.optimize() / n.pf(). We don't need to
            # store anything, but emit a one-line summary so the user sees what
            # got produced and can deep-link into the Results panel.
            phase("Storing results...")
            try:
                summary_bits = []
                if hasattr(network, "generators_t") and not network.generators_t.p.empty:
                    summary_bits.append(f"generators_t.p: {network.generators_t.p.shape}")
                if hasattr(network, "storage_units_t") and not network.storage_units_t.state_of_charge.empty:
                    summary_bits.append(f"storage SoC: {network.storage_units_t.state_of_charge.shape}")
                if hasattr(network, "lines_t") and not network.lines_t.p0.empty:
                    summary_bits.append(f"lines_t.p0: {network.lines_t.p0.shape}")
                if hasattr(network, "buses_t") and not network.buses_t.marginal_price.empty:
                    summary_bits.append(f"prices: {network.buses_t.marginal_price.shape}")
                if summary_bits:
                    phase("Stored: " + ", ".join(summary_bits))
            except Exception as exc:
                # Don't fail the run on a logging hiccup
                log_queue.put(f"[PHASE] Result-summary logging failed: {exc}")

            # PyPSA convention: total system cost = n.objective +
            # n.objective_constant. n.objective is the solver's variable-only
            # value; the constant carries existing-capacity CAPEX (PyPSA's
            # contribution) AND our curtailment wrapper's non-extendable
            # capacity offset (so the LP merit-order subsidy is balanced in
            # the reported total). Showing just `n.objective` looks negative
            # when those constants are large.
            #
            # MYOPIC EXCEPTION: `network.objective` here is the LAST iteration's
            # LP only — every earlier period's contribution is gone from this
            # lens, so this line used to report ~-75% of the true horizon cost
            # while the status bar showed a different wrong number and the
            # Economics tab showed the right one. Report the same statistics-
            # based horizon total the status bar and Economics use, and label it
            # so the per-period LP value is still identifiable in the log.
            obj = float(network.objective) if getattr(network, "objective", None) is not None else None
            obj_const = float(getattr(network, "_objective_constant", 0.0) or 0.0)
            wall = time.time() - t_start
            _myopic_ran = bool(getattr(network, "_myopic_period_objectives", None))
            if _myopic_ran:
                from services.cost_totals import horizon_system_cost
                _horizon = horizon_system_cost(network, config)
                if _horizon is not None:
                    obj_str = (
                        f"€{_horizon:,.2f} (myopic horizon total; "
                        f"final-period LP={obj:,.2f})" if obj is not None
                        else f"€{_horizon:,.2f} (myopic horizon total)"
                    )
                else:
                    obj_str = "n/a"
            elif obj is not None:
                total = obj + obj_const
                obj_str = (
                    f"€{total:,.2f} (solver={obj:,.2f} + constant={obj_const:,.2f})"
                )
            else:
                obj_str = "n/a"
            phase(f"Summary — objective={obj_str}, wall_time={wall:.2f} s")

            # Honour abort BEFORE kicking off Stage 2 — the LP has finished
            # by now; if the user clicked Abort while Stage 1 was running,
            # don't auto-chain another expensive non-linear PF.
            _check_stop(stop_event, phase, "after LP, before AC PF auto-chain")
            # Stage 2: auto-chain AC PF if the user opted in. Runs only on LOPF
            # mode and only when the LP stage succeeded — otherwise there's no
            # dispatch to fix. Failures here don't roll back Stage 1 results;
            # the AC PF outcome lives in _state alongside the LP results, and
            # the frontend's result-source toggle decides which to render.
            if (config.run_ac_pf_after_lopf
                    and config.mode == "lopf"
                    and status in ("ok", "optimal")):
                try:
                    # Lazy import: ac_pf_service imports SolverConfig /
                    # _normalise_dynamic_indexes / _DISPATCH_FIX_ACCESSORS back
                    # from this module at module level, so importing it here (not
                    # at top level) is what keeps the dependency acyclic.
                    from services.ac_pf_service import run_ac_pf_stage
                    ac_pf_out = run_ac_pf_stage(network, config, log_queue)
                    try:
                        # Atomic update — the status-poll endpoint reads
                        # `ac_pf_results` + `ac_pf_convergence` together and
                        # a bare `.update(dict)` between two reads can show
                        # `available=True` with a half-populated convergence
                        # list. The injected `state_update` holds `_state_lock`
                        # for the full multi-key apply so the poll sees all-or-none.
                        _emit_state(**ac_pf_out)
                    except Exception:
                        pass
                    # Flip `condition` so /api/simulation/status reflects that
                    # AC PF also ran (otherwise it shows the LP's "optimal"
                    # and there's no way to tell from the status card alone
                    # that Stage 2 followed). Mirrors the standalone trigger
                    # in routers/simulation.py which uses the same string.
                    condition = "lopf+ac_pf_ok"
                except Exception as exc:
                    # Push the full traceback so the user can diagnose a
                    # PF failure (singular-matrix slack, bad bus voltage,
                    # stale `_t` MultiIndex producing `dim_0` / `cannot
                    # include dtype 'M' in a buffer`, etc.). Without
                    # this, the inner `except` swallowed the frame info
                    # and a cryptic xarray/linopy/pandas error surfaced
                    # with no path forward — same diagnostic black hole
                    # CLAUDE.md flags for the outer LP `except` branch.
                    import traceback as _tb
                    tb_str = _tb.format_exc()
                    phase(f"Stage 2: AC PF failed — {exc}")
                    log_queue.put(f"ERROR: Stage 2 AC PF: {exc}")
                    for line in tb_str.rstrip().split("\n"):
                        log_queue.put(f"TRACEBACK: {line}")
                    condition = f"{condition}; ac_pf_failed: {exc}"
    except KeyboardInterrupt:
        # The abort watcher injected this to cancel the native HiGHS solve mid-
        # iteration (linopy caught it → h.cancelSolve() → PyPSA returned, then
        # it re-propagated here). Translate to the SAME clean-abort path as a
        # cooperative `_check_stop`: revert modelling transforms, status=aborted.
        # The solve `finally` already disarmed the watcher and ran the inner
        # restore, but call the once-guarded restore_modelling again defensively
        # (idempotent) in case the interrupt landed before the inner finally.
        if restore_modelling is not None:
            try:
                restore_modelling()
            except Exception as _rexc:
                log_queue.put(f"[PHASE] WARN: modelling restore after abort failed: {_rexc}")
        log_queue.put("[PHASE] Aborted by user (solver interrupted mid-iteration). "
                      "Modelling assumptions reverted.")
        status, condition = "aborted", "user_aborted:native_solve"
    except SolveAborted as exc:
        # Clean user-requested abort. Revert the modelling transforms here too:
        # if the abort fired at the pre-LP checkpoint (before the solve
        # try/finally was entered) the inner finally never ran, so call the
        # once-guarded restore to guarantee the network is back to its pre-LP
        # state before any autosave can persist vintage rows / VOLL slacks /
        # rebased build-years. Idempotent — a no-op if the inner finally
        # already restored. Don't dump a traceback — abort is intentional.
        if restore_modelling is not None:
            try:
                restore_modelling()
            except Exception as _rexc:
                log_queue.put(f"[PHASE] WARN: modelling restore after abort failed: {_rexc}")
        log_queue.put(f"[PHASE] Aborted by user at: {exc}. Modelling assumptions reverted.")
        status, condition = "aborted", f"user_aborted:{exc}"
    except Exception as exc:
        # Revert modelling transforms FIRST — a failure in the window between
        # _apply_modelling_assumptions and the solve try/finally skips the
        # inner finally, and the network must not be autosaved carrying
        # transient LP transforms. Once-guarded → no-op if already restored.
        if restore_modelling is not None:
            try:
                restore_modelling()
            except Exception as _rexc:
                log_queue.put(f"[PHASE] WARN: modelling restore after error failed: {_rexc}")
        import traceback as _tb
        tb_str = _tb.format_exc()
        log_queue.put(f"[PHASE] Failed: {exc}")
        log_queue.put(f"ERROR: {exc}")
        # Push the full traceback so the UI / backend log shows where the LP
        # construction actually died. Without this, an internal linopy/xarray
        # error like "snapshot is not a valid dimension" surfaces with no
        # filename and impossible to debug from the user side.
        for line in tb_str.rstrip().split("\n"):
            log_queue.put(f"TRACEBACK: {line}")
        status, condition = "error", str(exc)
    finally:
        # Tear down the abort watcher FIRST so no stray KeyboardInterrupt can be
        # injected during cleanup (disarm already stopped injection at the solve
        # boundary; shutdown joins the watcher thread).
        _abort_watcher.shutdown()
        _heartbeat.shutdown()
        root_logger.removeHandler(queue_handler)
        tail_stop.set()
        # tail_thread may be None if compile/wrap raised before we spawned it.
        if tail_thread is not None:
            tail_thread.join(timeout=2)
        try:
            tmp_log.unlink()
        except OSError:
            pass
        # Classify the terminal outcome into an actionable failure card (or
        # None on success/abort) and stash it on the lifecycle state via the
        # same sink the rest of the state flows through, so /status and the SSE
        # `done` payload can surface "why it failed + what to try". Emitting
        # None on success clears any stale card from a prior run. Best-effort —
        # a taxonomy hiccup must never block the sentinel below.
        try:
            from services.failure_taxonomy import classify_failure
            _emit_state(last_failure=classify_failure(status, condition))
        except Exception:
            pass
        # ALWAYS emit the SSE sentinel — without this the consumer
        # (loop.run_in_executor in routers/simulation.py) blocks forever on
        # queue.get and the UI is stuck in "running" until manual abort.
        log_queue.put(None)

    return status, condition


def _tail_log_file(
    path: pathlib.Path,
    q: queue.SimpleQueue,
    stop: threading.Event,
) -> None:
    with open(path) as f:
        while not stop.is_set():
            line = f.readline()
            if line:
                q.put(line.rstrip())
            else:
                time.sleep(0.05)


_USER_CODE_GATE_ENV = "PYPSA_GUI_ALLOW_USER_CODE"


def user_code_enabled() -> bool:
    """
    True iff the operator has explicitly opted in to executing user-supplied
    Python via the `extra_functionality_code` field. Off by default — this
    field is `exec()`-ed in-process with full FS / network privileges, and
    the GUI has no auth layer, so allowing it implicitly is a footgun. Set
    `PYPSA_GUI_ALLOW_USER_CODE=1` (or `true`/`yes`) to enable for trusted
    single-user / localhost deployments.
    """
    import os
    val = os.environ.get(_USER_CODE_GATE_ENV, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _compile_extra_functionality(code_str: str):
    if not code_str.strip():
        return None
    # Hard gate: refuse to compile user code unless the operator opted in.
    if not user_code_enabled():
        raise ValueError(
            f"extra_functionality_code is disabled in this deployment. "
            f"Set the environment variable {_USER_CODE_GATE_ENV}=1 before "
            f"starting the backend to enable. Only do this for trusted "
            f"single-user / localhost setups — the field runs arbitrary "
            f"Python in-process with no sandbox."
        )
    ns: dict = {}
    exec(compile(code_str, "<extra_functionality>", "exec"), ns)
    fn = ns.get("extra_functionality")
    if fn is None:
        raise ValueError(
            "extra_functionality code must define a function named 'extra_functionality(n, snapshots)'"
        )
    return fn










