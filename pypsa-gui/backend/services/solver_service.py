import logging
import logging.handlers
import math
import pathlib
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
import pypsa

from services import period_utils as _period_utils
from services.adequacy.slack import (
    DSR_SLACK_CARRIER,
    DSR_SLACK_PREFIX,
    INVOLUNTARY_SLACK_CARRIER,
    INVOLUNTARY_SLACK_CARRIERS,
    VOLL_SLACK_PREFIX,
    is_slack_carrier,
    strip_slack_prefix,
)
from services.pypsa_service import PyPSAService
from services.validation_service import has_errors, validate_for_run
from services.vintage_service import apply_vintage_bounds

# ── Load carrier canonicalisation ────────────────────────────────────────────
# Mirrors `loadCarrierKey` in pypsa-gui/frontend/src/pages/results/Dispatch.tsx
# so the per-carrier load-scaler lookups on this backend match what the
# frontend's Multi-period planning UI writes. Collapses common spellings
# (empty / 'AC' / 'electricity') into a single 'electrical' bucket.

_LOAD_ELECTRICAL_ALIASES = frozenset({
    "", "ac", "electricity", "electric", "electrical", "el",
})
# Substring-based matching for heat and hydrogen — covers PyPSA-Eur naming
# conventions: "urban central heat", "rural heat", "low T heat", "district
# heat", "H2 for industry", "H2 for power", "hydrogen storage", etc.
# Electrical stays exact-match because its aliases ('', 'AC', 'el') are too
# short for safe substring fallback.
_LOAD_HEAT_TOKENS = ("heat", "thermal")
_LOAD_HYDROGEN_TOKENS = ("hydrogen", "h2")


def _safe_log(log_queue, msg: str) -> None:
    """
    Put `msg` on the solver log queue, swallowing a None queue or any put
    error. The None-safe try/except wrapper for the per-phase `_emit`/`_put`
    closures (BUDGET / CURT / NUMERICS / objective-scale), which keep their own
    tag prefixes and delegate the boilerplate here.
    """
    if log_queue is None:
        return
    try:
        log_queue.put(msg)
    except Exception:
        pass


def _canonical_load_carrier_key(raw) -> str:
    """
    Return the canonical bucket key for a PyPSA `loads.carrier` value.

    Matches the frontend's `loadCarrierKey` so a user typing 'AC' on a load
    and entering `load_scalers_by_carrier['electrical']['2027'] = 1.1` in
    the multi-period planning UI gets that load scaled, despite the names
    not matching verbatim.
    """
    if raw is None:
        return "electrical"
    try:
        c = str(raw).strip().lower()
    except Exception:
        return "unspecified"
    if c in _LOAD_ELECTRICAL_ALIASES:
        return "electrical"
    if not c:
        return "electrical"
    if any(tok in c for tok in _LOAD_HYDROGEN_TOKENS):
        return "hydrogen"
    if any(tok in c for tok in _LOAD_HEAT_TOKENS):
        return "heat"
    return c or "unspecified"


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
    # Reliability target (adequacy spec §5.1): unserved ELECTRICAL energy
    # cap in parts per ten thousand (‱) of the period's weighted electrical
    # demand. None = off. Enforced per investment period via
    # _wrap_with_ens_cap; requires voll > 0 (preflight warns otherwise).
    ens_cap_permyriad: float | None = None
    # Per-zone ceiling as a multiple of the system target, applied to each
    # zone's OWN demand (zone = bus `country`). None = no zone ceilings.
    ens_zone_cap_multiple: float | None = None
    # Demand-response tier (spec §4.4): voluntary, volume-capped, OPT-IN per
    # bus. price 0 = off. Never silently global — price set with an empty
    # bus list keeps the tier off and preflight warns (double-count hazard
    # against networks that already model DSR as a real asset).
    dsr_price_eur_per_mwh: float = 0.0
    dsr_share_of_load: float = 0.0      # DSR p_nom = share × bus peak load
    dsr_buses: list = field(default_factory=list)
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


def check_solver_availability() -> dict[str, bool]:
    try:
        from linopy.solvers import available_solvers
    except ImportError:
        available_solvers = []
    known = ["highs", "gurobi", "glpk", "cplex", "scip", "cbc"]
    return {s: s in available_solvers for s in known}


# ── Abort plumbing ──────────────────────────────────────────────────────────
# The /abort endpoint sets a stop_event that the worker observes two ways:
#
#   1. COOPERATIVE checkpoints — `_check_stop()` polls the event BEFORE optimize()
#      (validation, modelling assumptions, snapshot build), BETWEEN myopic
#      iterations, and AFTER optimize() (AC PF, diagnostics, storage). Cheap,
#      always-safe, raises `SolveAborted`.
#   2. NATIVE-SOLVE interrupt — while HiGHS is inside the blocking `n.optimize()`
#      C call, cooperative polling can't fire. linopy 0.6.5 runs `h.run()` on its
#      own sub-thread and polls `finished.wait(0.1)` catching `KeyboardInterrupt`
#      → `h.cancelSolve()` (HiGHS returns `kInterrupt` → a CLEAN user-interrupt
#      status, not an error). So we make the running LP abortable by INJECTING a
#      `KeyboardInterrupt` into the solver worker thread the moment the stop_event
#      fires during the optimize window — `_AbortWatcher` does this. The injected
#      KeyboardInterrupt lands at linopy's next poll tick (≤0.1s), HiGHS cancels
#      mid-iteration, and PyPSA returns. The worker's KeyboardInterrupt handler
#      re-raises it as `SolveAborted` so the existing restore chain runs.
#
# Hard constraint preserved: `restore_modelling()` MUST run regardless of abort
# — it reverts vintage rows, slack generators, dispatch fix, etc. Both the
# cooperative and injected paths converge on `SolveAborted` → the outer except
# runs restore before returning ("aborted", "user_aborted").

import ctypes as _ctypes


def _async_raise_in_thread(thread_id: int, exc_type: type) -> int:
    """
    Inject `exc_type` into the thread with id `thread_id` (CPython async-exc).

    Returns the count of threads affected (1 = delivered). The exception is
    raised at that thread's NEXT bytecode boundary — which, for a thread parked
    in linopy's `finished.wait(0.1)` poll loop around HiGHS's `h.run()`, is
    within ~0.1s. Used only to interrupt a native solve on user abort; every
    other phase uses the cooperative `_check_stop`.
    """
    res = _ctypes.pythonapi.PyThreadState_SetAsyncExc(
        _ctypes.c_long(thread_id), _ctypes.py_object(exc_type)
    )
    if res > 1:
        # Should never happen (one thread id) — undo to avoid corrupting state.
        _ctypes.pythonapi.PyThreadState_SetAsyncExc(_ctypes.c_long(thread_id), None)
    return res


class _AbortWatcher:
    """
    Background watcher that injects KeyboardInterrupt into the SOLVER WORKER
    thread when the stop_event fires WHILE ARMED.

    The armed window (via `arm()`/`disarm()`) spans the WHOLE solve `try:` body —
    the native `n.optimize()` call (the case that matters: it's the long blocking
    one) AND the post-solve work that follows it inside the same try (capacity
    capture, objective rescale, post-solve diagnostics, and — for myopic — every
    iteration plus its per-iteration restore). An interrupt landing ANYWHERE in
    that window is safe: it propagates to the solve `finally:`, whose FIRST act is
    `disarm()` (so injection stops before the restore walk), then the once-guarded
    `restore_modelling()` runs (idempotent, per-entry-guarded) and the outer
    `except KeyboardInterrupt` re-runs it defensively. So the restore discipline
    holds regardless of WHERE the interrupt fired — the network is never left
    carrying transient LP transforms, and an aborted solve is never autosaved.
    What's NOT in the window: validation + modelling-assumptions apply (pre-arm,
    covered by cooperative `_check_stop`) and `restore_modelling` itself + result
    writes + AC-PF (post-disarm).

    SINGLE-SHOT delivery (critical correctness invariant). The watcher injects
    KeyboardInterrupt EXACTLY ONCE — the instant `PyThreadState_SetAsyncExc`
    confirms delivery (return == 1) it NEVER injects again for this run. It does
    NOT need to: linopy 0.6.5 runs `highspy.Highs.run()` on a daemon sub-thread
    while the worker (this `worker_tid`) sits in a Python-level
    `while not finished.wait(0.1): pass` poll loop, so a single async-exc is
    reliably raised at the next 0.1s poll tick — there is no "deep in C code that
    misses the signal" window to retry around. Re-injecting would be actively
    HARMFUL: after linopy catches the first KeyboardInterrupt it calls
    `h.cancelSolve()` and enters a SECOND, un-guarded `finished.wait(0.1)` loop to
    wait for HiGHS to actually stop; a second injected interrupt landing there
    propagates out of linopy WHILE `h.run()` is still executing on the sub-thread
    (orphaning the native solve — CPU thrash that starves the next queued job),
    and a still-later injection can land inside `restore_modelling` (which catches
    only `Exception`, not `BaseException`) and strand the network half-restored.
    Single-shot lets linopy's own clean cancel→wait→re-raise path complete
    uninterrupted, so the network is fully restored and no native thread leaks.
    A genuine non-delivery (return == 0, e.g. the worker thread already gone) is
    the only case the loop retries — there is nothing to orphan in that case.
    """

    def __init__(self, stop_event: "threading.Event | None", worker_tid: int) -> None:
        self._stop_event = stop_event
        self._worker_tid = worker_tid
        self._armed = threading.Event()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._stop_event is None:
            return  # no abort channel (qa scripts) → nothing to watch
        self._thread = threading.Thread(
            target=self._loop, name="solve-abort-watcher", daemon=True
        )
        self._thread.start()

    def arm(self) -> None:
        self._armed.set()

    def disarm(self) -> None:
        self._armed.clear()

    def shutdown(self) -> None:
        self._shutdown.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2)

    def _loop(self) -> None:
        se = self._stop_event
        injected = False  # latch: inject ONCE on confirmed delivery, never again
        while not self._shutdown.is_set():
            # Wait for an abort request (cheap; wakes immediately on .set()).
            if se is None or not se.wait(timeout=0.2):
                continue
            # Abort requested. Inject a SINGLE KeyboardInterrupt while armed (the
            # native-solve window). The instant SetAsyncExc confirms delivery
            # (==1) we latch `injected` and stop — re-injection would break
            # linopy's post-cancelSolve finish-wait and orphan h.run() / strand
            # restore (see class docstring). Only a genuine non-delivery (==0,
            # worker gone) retries on the next tick.
            while se.is_set() and not self._shutdown.is_set():
                if self._armed.is_set() and not injected:
                    if _async_raise_in_thread(self._worker_tid, KeyboardInterrupt) == 1:
                        injected = True
                if self._shutdown.wait(timeout=0.2):
                    return


class _SolveHeartbeat:
    """
    Periodic liveness pings during the otherwise-silent native solve window.

    HiGHS runs the LP inside a C-extension call (`h.run()`); depending on the
    solver phase — presolve especially — it can go many seconds, minutes on a
    large model, WITHOUT writing a single line to its log file. The log tail
    then shows nothing and the GUI looks frozen: the user can't tell a long
    solve from a hung one. This daemon emits a `[PHASE]` heartbeat every
    `interval` seconds while a solve is active, carrying elapsed wall time and
    the reminder that Abort is available — so a long solve always reads as
    alive and interruptible. It writes to the SAME `log_queue` the SSE consumer
    drains, so the lines stream live and land in the replay buffer.

    Lifecycle mirrors `_AbortWatcher`: `start()` once at run entry, `begin()` /
    `end()` bracket the native-solve window (the same arm/disarm points), and
    `shutdown()` in the outer finally joins the thread. Inactive (parked, no
    pings) outside a begin/end window, so pre-LP setup and post-solve
    diagnostics stay quiet. The first ping fires one full `interval` AFTER
    begin() — a fast solve that finishes inside the interval emits nothing.
    """

    def __init__(self, log_queue, interval: float = 15.0) -> None:
        self._q = log_queue
        self._interval = interval
        self._active = threading.Event()
        self._shutdown = threading.Event()
        self._start_monotonic = 0.0
        self._label = "Optimising"
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="solve-heartbeat", daemon=True
        )
        self._thread.start()

    def begin(self, label: str = "Optimising") -> None:
        self._label = label
        self._start_monotonic = time.monotonic()
        self._active.set()

    def end(self) -> None:
        self._active.clear()

    def shutdown(self) -> None:
        self._shutdown.set()
        self._active.set()  # wake the loop so it observes _shutdown promptly
        t = self._thread
        if t is not None:
            t.join(timeout=2)

    def _loop(self) -> None:
        while not self._shutdown.is_set():
            # Park until a solve begins (cheap; wakes on begin()/shutdown()).
            if not self._active.wait(timeout=0.5):
                continue
            if self._shutdown.is_set():
                return
            # Active. Sleep the interval in short hops so end() silences pings
            # within ~0.5s, then emit ONE heartbeat if the solve is still going.
            waited = 0.0
            while waited < self._interval:
                if self._shutdown.is_set() or not self._active.is_set():
                    break
                time.sleep(0.5)
                waited += 0.5
            if self._active.is_set() and not self._shutdown.is_set():
                elapsed = int(time.monotonic() - self._start_monotonic)
                mm, ss = divmod(elapsed, 60)
                try:
                    self._q.put(
                        f"[PHASE] ⏳ {self._label}… {mm:d}:{ss:02d} "
                        f"elapsed — click Abort to cancel."
                    )
                except Exception:
                    pass  # never let a logging hiccup kill the heartbeat thread


class SolveAborted(Exception):
    """
    Raised when the user-set stop_event is observed at a checkpoint.
    Caller's existing try/except/finally chain unwinds modelling-assumption
    restores cleanly before propagating. Treat as a normal control-flow
    exception, not an error — log at INFO, not ERROR.
    """

    pass


def _check_stop(stop_event: threading.Event | None, phase, where: str) -> None:
    """
    Raise SolveAborted if the user requested abort.

    Call at natural checkpoints — never inside an LP solver call (we can't
    interrupt that). Emits a single phase line so the SSE log records WHERE
    the abort was honoured, which is useful for diagnosing "why didn't it
    abort sooner" on long pre-LP setup.
    """
    if stop_event is not None and stop_event.is_set():
        try:
            phase(f"Aborted by user (at: {where}). Reverting modelling assumptions…")
        except Exception:
            pass
        raise SolveAborted(where)


class _RollingWindowFailureCatcher(logging.Handler):
    """
    Captures PyPSA's per-window "Optimization failed" warnings.

    `optimize_with_rolling_horizon` solves each window and, on a non-ok window,
    LOGS ``logger.warning("Optimization failed with status %s and condition
    %s", status, condition)`` and CONTINUES — it returns the network (not a
    status tuple) and never raises. So a clean return does NOT mean every window
    solved. This handler, attached to ``pypsa.optimization.abstract`` for the
    duration of the call, records each failed window's ``(status, condition)``
    (read from ``record.args``) so the caller can surface a real failure instead
    of reporting a masked "optimal".

    Thread-scoped: the logger is process-global, so a CONCURRENT solve (foreground
    vs queue background) attaching its own catcher would otherwise see THIS
    solve's window warnings and cross-attribute them. PyPSA emits the warning
    synchronously inside the solving thread, so we capture this solve's thread
    ident at construction and ignore records emitted from any other thread.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.failures: list[tuple[str, str]] = []
        self._tid = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        # Only count failures emitted from the thread that owns this catcher —
        # a concurrent solve on another thread shares this global logger.
        if getattr(record, "thread", None) != self._tid:
            return
        try:
            msg = record.getMessage()
        except Exception:
            return
        if "Optimization failed with status" in msg:
            args = record.args if isinstance(record.args, tuple) else ()
            st = str(args[0]) if len(args) >= 1 else "warning"
            cond = str(args[1]) if len(args) >= 2 else "unknown"
            self.failures.append((st, cond))


class _ThreadScopedQueueHandler(logging.handlers.QueueHandler):
    """
    Root-logger → SSE bridge that only forwards THIS solve's records.

    The plain ``QueueHandler`` this replaced was attached to the **root**
    logger, so on a shared process every log record produced by every other
    tenant's request — 500 tracebacks included — was streamed into whichever
    tenant happened to be solving. That is a cross-tenant disclosure, not a
    cosmetic one.

    The fix is NOT to attach to a narrower logger. Attaching to, say, the
    solver's own logger would EMPTY the stream: the root attachment is
    deliberate, because the log the user reads *is* ``pypsa.*`` / ``linopy.*`` /
    HiGHS output emitted under those third-party logger names. Instead, filter
    on the emitting thread, which is exactly the pattern
    ``_RollingWindowFailureCatcher`` above already uses for the same reason —
    PyPSA emits synchronously inside the solving thread, so the ident captured
    at construction identifies this solve's records and no one else's.

    STEP 2 CAVEAT: this holds only while one job runs per thread. If the worker
    ever runs jobs asyncio-concurrently on a single event loop, ``thread``
    degenerates to a constant and cross-job contamination returns *silently*.
    Switch to a ``contextvars.ContextVar`` at that point — it survives both
    threads and tasks — and keep the S0.6 assertion that pypsa/linopy lines are
    still present, so the fix cannot regress into the bug it replaced.
    """

    def __init__(self, log_queue) -> None:
        super().__init__(log_queue)
        self._owner_tid = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "thread", None) != self._owner_tid:
            return
        super().emit(record)


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
        # Reliability target: per-period ENS cap (+ per-zone ceilings) on the
        # involuntary slack dispatch. Adds work only when a target is set.
        extra_fn = _wrap_with_ens_cap(network, extra_fn, config, log_queue=log_queue)
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
                # Adequacy report — emitted whenever a target was enforced,
                # INCLUDING the nothing-shed case (achieved 0, binding=voll).
                _ens_targets = getattr(network, "_ens_cap_targets", None)
                if _ens_targets:
                    try:
                        from services.adequacy.report import (
                            build_adequacy_report,
                        )
                        _emit_state(adequacy_report=build_adequacy_report(
                            network, config, _ens_targets, captured))
                        phase("Adequacy report built (target evaluation).")
                    except Exception as exc:
                        phase(f"Adequacy report skipped: {exc}")
                    finally:
                        try:
                            delattr(network, "_ens_cap_targets")
                        except AttributeError:
                            pass
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


def _diagnose_infeasibility(network, config, log_queue) -> None:
    """
    Heuristic "why is it infeasible?" hints, emitted as ``[INFEASIBLE]`` lines.

    HiGHS (the default solver) exposes no exact irreducible-inconsistent-subsystem
    (IIS), so on a CONFIRMED-infeasible LP we point at the common structural
    causes a modeller can act on: a bus carrying load with no way to serve it; a
    peak demand that exceeds all available + buildable generation with no VOLL;
    and any active global constraint (CO2 cap, …) that may be infeasibly tight.
    Read-only, and it runs ONLY after the solver already returned infeasible — so
    every hint is a safe pointer, never a false block. When the solver is Gurobi,
    also surface its native infeasibility report.
    """
    def emit(msg: str) -> None:
        try:
            log_queue.put(msg)
        except Exception:
            pass

    n = network
    emit("[INFEASIBLE] Diagnosing likely causes (heuristic — the LP solver found "
         "no feasible point):")
    hints = 0

    # 1) Islanded load: a bus with load but no local supply AND no branch.
    try:
        connected: set = set()
        for comp in ("lines", "transformers"):
            df = getattr(n, comp, None)
            if df is not None and not df.empty:
                connected |= set(df["bus0"]) | set(df["bus1"])
        links = getattr(n, "links", None)
        if links is not None and not links.empty:
            for col in ("bus0", "bus1", "bus2", "bus3", "bus4"):
                if col in links.columns:
                    connected |= {b for b in links[col].astype(str) if b and b != "nan"}
        supply_buses: set = set()
        for comp in ("generators", "storage_units", "stores"):
            df = getattr(n, comp, None)
            if df is not None and not df.empty and "bus" in df.columns:
                supply_buses |= set(df["bus"])
        loads = getattr(n, "loads", None)
        if loads is not None and not loads.empty:
            ldt = getattr(n.loads_t, "p_set", None)
            for name, ld in loads.iterrows():
                bus = ld["bus"]
                if ldt is not None and name in ldt.columns:
                    peak = float(ldt[name].abs().max())
                else:
                    peak = float(abs(ld.get("p_set", 0.0)))
                if peak > 1e-6 and bus not in supply_buses and bus not in connected:
                    emit(f"[INFEASIBLE]   • Bus '{bus}' carries ~{peak:,.0f} MW of load but "
                         "has no generator/storage and no line/link — it can never be served.")
                    hints += 1
    except Exception:
        pass

    # 2) Whole-network peak demand vs maximum available + buildable supply.
    try:
        import numpy as _np
        ldt = getattr(n.loads_t, "p_set", None)
        if ldt is not None and not ldt.empty:
            peak_load = float(ldt.sum(axis=1).abs().max())
        else:
            peak_load = float(n.loads["p_set"].abs().sum()) if not n.loads.empty else 0.0
        avail = 0.0
        gens = getattr(n, "generators", None)
        if gens is not None and not gens.empty:
            gmax = getattr(n.generators_t, "p_max_pu", None)
            for name, g in gens.iterrows():
                if bool(g.get("p_nom_extendable", False)):
                    pmax = float(g.get("p_nom_max", _np.inf))
                    avail += pmax if _np.isfinite(pmax) else _np.inf
                else:
                    pu = (float(gmax[name].max()) if gmax is not None and name in gmax.columns
                          else float(g.get("p_max_pu", 1.0)))
                    avail += float(g.get("p_nom", 0.0)) * pu
        # storage/store discharge headroom (an upper bound on instantaneous supply)
        for comp, col in (("storage_units", "p_nom"), ("stores", "e_nom")):
            df = getattr(n, comp, None)
            if df is not None and not df.empty and col in df.columns:
                avail += float(df[col].clip(lower=0).sum())
        voll = float(getattr(config, "voll", 0.0) or 0.0)
        if _np.isfinite(avail) and peak_load > avail * (1.0 + 1e-6) and voll <= 0:
            emit(f"[INFEASIBLE]   • Peak demand ~{peak_load:,.0f} MW exceeds the maximum "
                 f"available + buildable generation ~{avail:,.0f} MW, and no Value of Lost "
                 "Load is set. Add capacity, raise an extendable p_nom_max, or set a VOLL "
                 "to price unserved demand.")
            hints += 1
    except Exception:
        pass

    # 3) Active global constraints (CO2 caps etc.) that may be binding too tight.
    try:
        gc = getattr(n, "global_constraints", None)
        if gc is not None and not gc.empty:
            for name, row in gc.iterrows():
                emit(f"[INFEASIBLE]   • Global constraint '{name}' "
                     f"({row.get('type', '?')} {row.get('sense', '')} "
                     f"{row.get('constant', '?')}) is active — if it's a CO2 / primary-"
                     "energy cap it may be infeasibly tight; relax it or check must-run "
                     "emitters.")
                hints += 1
    except Exception:
        pass

    # 4) Gurobi-only: native infeasibility report (IIS).
    try:
        if str(getattr(config, "solver_name", "")).lower() == "gurobi":
            model = getattr(n, "model", None)
            printer = getattr(model, "print_infeasibilities", None)
            if callable(printer):
                emit("[INFEASIBLE]   • Gurobi infeasibility report (constraints in the "
                     "irreducible conflict set) follows in the solver log.")
                printer()
    except Exception:
        pass

    if hints == 0:
        emit("[INFEASIBLE]   • No obvious structural cause found. Check binding capacity "
             "bounds (p_nom_min/max), reserve / storage-SoC targets, ramp limits, or a "
             "too-tight global constraint. Turning OFF presolve can make the solver report "
             "whether the model is infeasible vs unbounded.")


def _log_global_constraint_shadow_prices(network, log_queue) -> None:
    """
    On a successful solve, report each BINDING global constraint's shadow price
    (``global_constraints.mu``) as a raw ``[GLOBAL-CONSTRAINT]`` line — e.g. a CO2
    cap's mu is the implied carbon price (the marginal abatement cost at which the
    cap binds). Skips non-binding (mu ≈ 0) constraints. Read-only; mu is already
    in user-facing units.
    """
    try:
        gc = getattr(network, "global_constraints", None)
        if gc is None or gc.empty or "mu" not in gc.columns:
            return
        for name, row in gc.iterrows():
            try:
                mu = float(row.get("mu", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(mu) < 1e-6:
                continue
            ctype = str(row.get("type", ""))
            is_carbon = "co2" in str(name).lower() or "primary_energy" in ctype.lower()
            unit = "€/t" if is_carbon else "€/unit"
            try:
                log_queue.put(
                    f"[GLOBAL-CONSTRAINT] '{name}' ({ctype} {row.get('sense', '')} "
                    f"{row.get('constant', '?')}) BINDS — shadow price {mu:,.2f} {unit} "
                    "(marginal cost of tightening it by one unit)."
                )
            except Exception:
                pass
    except Exception:
        pass


def _log_curtailment_post_solve(network, sns, current_period, phase) -> None:
    """
    After each myopic iteration, log per-vintage build + curtailment so
    the user can verify the wrapper's effect end-to-end. Pushes lines via
    `phase` (= log_queue.put) — same channel as everything else the user
    sees in the Log tab.

    For every generator with curtailment_cost > 0:
      • p_nom_opt        — capacity the LP picked (MW)
      • dispatched_MWh   — Σ p × snapshot_weight × period_weight over `sns`
      • potential_MWh    — Σ p_max_pu × p_nom_opt × snapshot_weight × period_weight
      • curtailed_MWh    — potential − dispatched
      • curt_pct         — curtailed / potential × 100
      • penalty_eur      — curtailed × cc

    The penalty is what the wrapper added to the LP objective for THIS
    generator's curtailment. If you see penalty >> capex_eff × p_nom_opt,
    the LP is paying a fortune in curtailment cost but still building —
    meaning some other benefit (VOLL avoidance, OPEX savings) made it
    worth it. That's the smoking gun for VOLL > cc.
    """
    gens = getattr(network, "generators", None)
    if gens is None or "curtailment_cost" not in gens.columns:
        return
    cc_series = gens["curtailment_cost"].fillna(0.0)
    cc_gens = cc_series.index[cc_series > 0.0]
    if cc_gens.empty:
        return

    # Need solved p (per snapshot, per gen). After PyPSA's optimize call,
    # `n.generators_t.p` holds the dispatch result. If it's empty (rare —
    # e.g. solver returned ok but with no data), bail rather than emit
    # confusing zero-rows.
    p_t = getattr(network.generators_t, "p", None)
    if p_t is None or p_t.empty:
        phase("Post-solve curtailment log: dispatch table empty — skipping.")
        return

    # Weight basis must match what the wrapper used so penalty_eur lines
    # up with the LP's actual contribution: snapshot_w × period_w over
    # this iteration's `sns` only.
    try:
        sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    except Exception:
        sw = pd.Series(1.0, index=sns, dtype=float)
    is_mp = isinstance(sns, pd.MultiIndex)
    if is_mp:
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    # p_max_pu (dense, both scalar and time-varying paths handled)
    p_max_pu = network.get_switchable_as_dense("Generator", "p_max_pu")
    try:
        p_max_pu = p_max_pu.loc[sns]
    except KeyError:
        pass

    p_nom_col = "p_nom_opt" if "p_nom_opt" in gens.columns else "p_nom"
    phase(f"[CURT-POST] Period {current_period} — built capacity + curtailment:")
    for g in cc_gens:
        try:
            p_nom_opt = float(gens.at[g, p_nom_col] or 0.0)
            cc = float(cc_series.at[g])
            if g in p_t.columns:
                p_series = p_t[g].reindex(sns).fillna(0.0)
                dispatched = float((p_series * weights).sum())
            else:
                dispatched = 0.0
            if g in p_max_pu.columns:
                pmp = p_max_pu[g].reindex(sns).fillna(0.0)
                potential = float((pmp * weights).sum() * p_nom_opt)
            else:
                potential = 0.0
            curtailed = max(0.0, potential - dispatched)
            curt_pct = (curtailed / potential * 100.0) if potential > 0 else 0.0
            penalty = curtailed * cc
            phase(
                f"[CURT-POST] {g}: p_nom_opt={p_nom_opt:.1f} MW | "
                f"dispatched={dispatched/1000:.1f} GWh | "
                f"potential={potential/1000:.1f} GWh | "
                f"curtailed={curtailed/1000:.1f} GWh ({curt_pct:.1f}%) | "
                f"penalty_eur={penalty:,.0f}"
            )
        except Exception as exc:
            phase(f"[CURT-POST] {g}: diagnostic failed ({exc})")


def _log_storage_post_solve(network, sns, current_period, phase) -> None:
    """
    Per-storage diagnostic after each myopic iteration.

    The user expectation: with the curtailment-cost merit-order fix, the LP
    should build storage to absorb renewable excess. This logger surfaces:
      • p_nom_opt (MW)    — power capacity the LP picked
      • max_hours         — fixed energy/power ratio (energy_cap = p_nom × max_hours)
      • energy_MWh        — implied storage energy capacity
      • throughput_MWh    — Σ |p| × snapshot_weight × period_weight (gross
                            charge + discharge — measure of cycling)
      • equivalent_cycles — throughput / (2 × energy_MWh), so a full
                            charge-then-discharge counts as 1.0 cycle
      • mean_SoC_pct      — average state-of-charge over the period

    If storage stays at p_nom_opt=0, the LP found it uneconomic. Likely
    causes: capital_cost too high, max_hours too low (storage shifts too
    little energy), or curtailment_cost too low to justify the build.
    """
    sus = getattr(network, "storage_units", None)
    if sus is None or sus.empty:
        return
    # Only log storage that's either extendable in THIS iteration or has
    # non-zero p_nom (so frozen vintages from earlier periods also show up).
    p_nom_col = "p_nom_opt" if "p_nom_opt" in sus.columns else "p_nom"
    rows = sus[sus[p_nom_col].fillna(0) > 0.01]
    if rows.empty:
        # Surface the negative result explicitly — silence here is confusing.
        phase(
            f"[STORAGE-POST] Period {current_period}: no storage built "
            f"(all p_nom_opt < 0.01 MW). LP found storage uneconomic."
        )
        return

    sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    is_mp = isinstance(sns, pd.MultiIndex)
    if is_mp:
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    p_t = getattr(network.storage_units_t, "p", None)
    soc_t = getattr(network.storage_units_t, "state_of_charge", None)

    phase(f"[STORAGE-POST] Period {current_period} — storage build + utilization:")
    for name in rows.index:
        try:
            p_nom_opt = float(rows.at[name, p_nom_col] or 0.0)
            max_hours = float(rows.at[name, "max_hours"] or 0.0) if "max_hours" in rows.columns else 0.0
            energy_cap = p_nom_opt * max_hours
            throughput = 0.0
            mean_soc_pct = 0.0
            if p_t is not None and not p_t.empty and name in p_t.columns:
                p_series = p_t[name].reindex(sns).fillna(0.0).abs()
                throughput = float((p_series * weights).sum())
            if soc_t is not None and not soc_t.empty and name in soc_t.columns and energy_cap > 0:
                soc_series = soc_t[name].reindex(sns).fillna(0.0)
                # SoC is in MWh; convert to % of energy capacity.
                mean_soc_pct = float(soc_series.mean() / energy_cap * 100.0)
            cycles = throughput / (2 * energy_cap) if energy_cap > 0 else 0.0
            phase(
                f"[STORAGE-POST] {name}: p_nom_opt={p_nom_opt:.1f} MW | "
                f"max_hours={max_hours:.1f} h | energy_cap={energy_cap/1000:.2f} GWh | "
                f"throughput={throughput/1000:.1f} GWh | "
                f"equiv_cycles={cycles:.1f} | mean_SoC={mean_soc_pct:.1f}%"
            )
        except Exception as exc:
            phase(f"[STORAGE-POST] {name}: diagnostic failed ({exc})")


def _log_sclopf_post_solve(
    network,
    sns,
    current_period: int,
    iter_outages: list[tuple[str, str]],
    phase,
) -> None:
    """
    Post-solve N-1 contingency analysis for each SCLOPF myopic iteration.

    Reports per-outage:
      • the outaged branch
      • the WORST-CASE loading (max |post-contingency flow| / s_nom)
        across all monitored branches and snapshots in this iteration
      • the affected branch where the worst case occurs
      • whether the contingency is BINDING (worst case ≥ 99 % s_nom →
        the LP is hitting the N-1 limit there; capacity sizing was
        driven by this contingency, not by the base case)

    The LP duals on the contingency constraints themselves aren't exposed
    by PyPSA on the SCLOPF path (its `optimize_security_constrained` adds
    constraints by name but doesn't tag them for our `assign_all_duals`
    extraction). Instead we reconstruct the worst-case post-outage flow
    analytically using the same BODF (Branch Outage Distribution Factor)
    matrix PyPSA uses internally:

        post_outage_flow[affected, t] = base_flow[affected, t]
                                       + BODF[affected, outage]
                                       × base_flow[outage, t]

    Comparing |post_flow| against s_nom tells you whether the LP needed
    to back off the base case to keep the system feasible under N-1.
    """
    if not iter_outages:
        return
    import math as _math

    # `lines_t.p0` carries the base-case flow per line per snapshot. PyPSA
    # writes it after every successful solve.
    try:
        lines_p0 = getattr(network.lines_t, "p0", None)
        trafos_p0 = getattr(network.transformers_t, "p0", None)
    except Exception:
        return
    # Combine both passive-branch classes into one DataFrame indexed by
    # (component, name) so we can look up post-outage flows uniformly.
    flow_frames: list[pd.DataFrame] = []
    s_nom_map: dict[tuple[str, str], float] = {}
    for comp, attr, df_t in (
        ("Line", "lines", lines_p0),
        ("Transformer", "transformers", trafos_p0),
    ):
        static = getattr(network, attr, None)
        if df_t is None or df_t.empty or static is None or static.empty:
            continue
        # Restrict to the iteration's snapshots so the analysis lines up
        # with the LP that just solved.
        try:
            df_sns = df_t.loc[sns]
        except KeyError:
            df_sns = df_t.reindex(sns).fillna(0.0)
        s_nom_col = "s_nom_opt" if "s_nom_opt" in static.columns else "s_nom"
        for name in df_sns.columns:
            if name not in static.index:
                continue
            try:
                s_nom = float(static.at[name, s_nom_col] or 0.0)
            except (TypeError, ValueError):
                s_nom = 0.0
            if s_nom <= 0:
                continue
            s_nom_map[(comp, name)] = s_nom
        flow_frames.append(df_sns)
    if not s_nom_map:
        return
    # Compute the BODF matrix from PyPSA's sub-network helper. PyPSA's
    # `optimize_security_constrained` calls this internally — we replay it
    # here for the post-solve diagnostic. BODF[a, o] gives the fraction
    # of `o`'s pre-outage flow that ends up on `a` after `o` trips.
    try:
        bodf_by_subnet = {}
        for sn in network.sub_networks.obj:
            try:
                sn.calculate_BODF()
                # BODF is a numpy 2D array; wrap as DataFrame indexed by
                # the sub-network's branch (component, name) tuples.
                br_i = sn.branches_i()
                bodf = pd.DataFrame(sn.BODF, index=br_i, columns=br_i)
                bodf_by_subnet[sn.name] = (br_i, bodf)
            except Exception:
                continue
    except Exception:
        return
    if not bodf_by_subnet:
        return
    # For each outage candidate, find the worst affected branch loading.
    phase(f"[SCLOPF-POST] Period {current_period} — N-1 contingency analysis ({len(iter_outages)} outage(s)):")
    binding_threshold = 0.99
    for outage_comp, outage_name in iter_outages:
        # Find which sub-network owns this branch.
        owner = None
        for sn_name, (br_i, bodf) in bodf_by_subnet.items():
            if (outage_comp, outage_name) in br_i:
                owner = (sn_name, br_i, bodf)
                break
        if owner is None:
            continue
        sn_name, br_i, bodf = owner
        try:
            outage_flow = None
            for fr in flow_frames:
                if outage_name in fr.columns:
                    outage_flow = fr[outage_name].values
                    break
            if outage_flow is None:
                continue
            worst_loading = 0.0
            worst_affected = None
            for affected_key in br_i:
                ac, an = affected_key
                if (ac, an) == (outage_comp, outage_name):
                    continue  # outaged branch itself; carries 0 post-outage
                s_nom = s_nom_map.get((ac, an), 0.0)
                if s_nom <= 0:
                    continue
                base_flow = None
                for fr in flow_frames:
                    if an in fr.columns:
                        base_flow = fr[an].values
                        break
                if base_flow is None:
                    continue
                try:
                    factor = float(bodf.at[(ac, an), (outage_comp, outage_name)])
                except (KeyError, ValueError):
                    continue
                post = base_flow + factor * outage_flow
                loading = float(max(abs(p) for p in post)) / s_nom
                if loading > worst_loading:
                    worst_loading = loading
                    worst_affected = an
            binding = "BINDING" if worst_loading >= binding_threshold else "slack"
            if worst_affected is not None and _math.isfinite(worst_loading):
                phase(
                    f"[SCLOPF-POST] outage '{outage_comp}:{outage_name}' → worst affected "
                    f"'{worst_affected}' loaded {worst_loading * 100:.1f}% of s_nom ({binding})"
                )
            else:
                phase(
                    f"[SCLOPF-POST] outage '{outage_comp}:{outage_name}' → no affected branch flow available"
                )
        except Exception as exc:
            phase(f"[SCLOPF-POST] outage '{outage_comp}:{outage_name}' → analysis failed ({type(exc).__name__}: {exc})")


def _log_capacity_summary_post_solve(network, current_period, phase) -> None:
    """
    Aggregate per-carrier capacity AFTER each myopic iteration.

    Shows the total active capacity (parent + vintages, summed across
    same-carrier assets) so the user can see expansion at a glance without
    having to add up per-vintage numbers manually. Includes Generator,
    StorageUnit, and Store carriers.

    Aggregation key is `carrier` — vintages share the parent's carrier,
    so `Solar2 + Solar2@2026 + Solar2@2027 + Solar2@2028` collapse into
    one row labeled by the carrier name.
    """
    phase(f"[CAP-SUM] Period {current_period} — total active capacity by carrier:")
    for comp, attr, unit in (
        ("Generator",   "generators",    "MW"),
        ("StorageUnit", "storage_units", "MW (×max_hours = MWh energy)"),
        ("Store",       "stores",        "MWh"),
    ):
        df = getattr(network, attr, None)
        if df is None or df.empty:
            continue
        cap_col = (
            "p_nom_opt" if "p_nom_opt" in df.columns
            else "e_nom_opt" if "e_nom_opt" in df.columns
            else "p_nom" if "p_nom" in df.columns
            else "e_nom"
        )
        if cap_col not in df.columns:
            continue
        if "carrier" not in df.columns:
            continue
        # Sum capacity per carrier. Skip near-zero rows so the summary
        # doesn't list every empty asset.
        agg = (df.groupby("carrier")[cap_col].sum().sort_values(ascending=False))
        agg = agg[agg > 0.01]
        if agg.empty:
            continue
        items = ", ".join(f"{c}: {v:.1f}" for c, v in agg.items())
        phase(f"[CAP-SUM] {comp} ({unit}) — {items}")


def _log_line_post_solve(network, sns, current_period, phase) -> None:
    """
    Per-line / per-transformer diagnostic after each myopic iteration.

    Captures over-build signals on transmission infrastructure:
      • s_nom_opt           — capacity decided (MW)
      • peak_loading_pct    — max |flow| / s_nom_opt × 100 over `sns`
      • mean_loading_pct    — Σ |flow| × w / (s_nom_opt × Σ w) × 100
      • binding_hours       — count of snapshots where the capacity
                              upper-bound dual (mu_upper) is non-zero;
                              these are the only hours where adding 1 MW
                              of line capacity would have saved system
                              cost. binding_hours ≈ 0 with peak < 70% is
                              a strong over-build signal.
      • congestion_rent_eur — Σ mu × s_nom_opt × snapshot_weight × period_weight
                              over the iteration (LP value of the line's
                              capacity — what extra capacity is worth).

    Surfaces ALL line/transformer rows, including vintages, so the user
    can see per-vintage decisions (parent + Line@2026 + Line@2027 + …).
    """
    sw_full = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    if isinstance(sns, pd.MultiIndex):
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw_full * year_mul
        except Exception:
            weights = sw_full
    else:
        weights = sw_full
    total_w = float(weights.sum()) if weights.sum() > 0 else 1.0

    for comp_class, attr, t_attr in (("Line", "lines", "p0"), ("Transformer", "transformers", "p0")):
        df = getattr(network, attr, None)
        if df is None or df.empty:
            continue
        cap_col = "s_nom_opt" if "s_nom_opt" in df.columns else "s_nom"
        # Include any line with non-trivial capacity OR an extendable flag.
        # Showing zero-capacity rows is noise; if vintage_service created a
        # vintage and the LP picked p_nom_opt=0, that's the "not built" case
        # — surface ONE summary line per asset class instead.
        rows = df[df[cap_col].fillna(0) > 0.01]
        if rows.empty:
            phase(
                f"[LINE-POST] Period {current_period}: no {comp_class} with "
                f"capacity > 0.01 MW."
            )
            continue
        # PyPSA's effective capital_cost (annuitised via periodized_cost). A
        # near-zero value is the smoking gun for "LP builds lines for free".
        # PyPSA exposes this as a property on the component; falls back to
        # the raw column on older PyPSA versions.
        try:
            eff_cap_cost = getattr(getattr(network.c, attr), "capital_cost", None)
        except Exception:
            eff_cap_cost = None
        flow_df = getattr(getattr(network, f"{attr}_t"), t_attr, None)
        # Capacity-upper dual — LP shadow price on s_nom_opt for each hour.
        # Non-zero = the LP would have benefited from more line capacity.
        try:
            mu_up_df = getattr(getattr(network, f"{attr}_t"), "mu_upper", None)
            mu_lo_df = getattr(getattr(network, f"{attr}_t"), "mu_lower", None)
        except Exception:
            mu_up_df = mu_lo_df = None
        phase(f"[LINE-POST] Period {current_period} — {comp_class} build & loading:")
        for name in rows.index:
            try:
                s_nom_opt = float(rows.at[name, cap_col] or 0.0)
                s_nom_max = float(rows.at[name, "s_nom_max"] or 0.0) if "s_nom_max" in rows.columns else float("inf")
                x_val = float(rows.at[name, "x"] or 0.0) if "x" in rows.columns else 0.0
                capex_eff = (
                    float(eff_cap_cost.get(name, float("nan")))
                    if eff_cap_cost is not None else float("nan")
                )
                peak_pct = 0.0
                mean_pct = 0.0
                binding_h = 0
                congestion_eur = 0.0
                if flow_df is not None and not flow_df.empty and name in flow_df.columns:
                    flow_series = flow_df[name].reindex(sns).fillna(0.0).abs()
                    if s_nom_opt > 0:
                        peak_pct = float(flow_series.max() / s_nom_opt * 100.0)
                        mean_pct = float((flow_series * weights).sum() / (s_nom_opt * total_w) * 100.0)
                # mu_upper + mu_lower combined (binding in EITHER direction).
                for mu_df in (mu_up_df, mu_lo_df):
                    if mu_df is not None and not mu_df.empty and name in mu_df.columns:
                        mu_series = mu_df[name].reindex(sns).fillna(0.0).abs()
                        # Use a tiny tolerance — solver leaves micro-duals
                        # around zero for inactive constraints.
                        binding_h += int((mu_series > 1e-6).sum())
                        congestion_eur += float((mu_series * weights).sum() * s_nom_opt)
                (
                    f"[{s_nom_opt:.1f}/{s_nom_max:.0f}]" if s_nom_max != float("inf")
                    else f"[{s_nom_opt:.1f}]"
                )
                phase(
                    f"[LINE-POST] {name}: s_nom_opt={s_nom_opt:.1f} MW "
                    f"(max={s_nom_max:.0f}, x={x_val:.4f}, "
                    f"capex_eff={capex_eff:.0f} €/MW) | "
                    f"peak={peak_pct:.1f}% | mean={mean_pct:.1f}% | "
                    f"binding_hours={binding_h} | "
                    f"congestion_rent_eur={congestion_eur:,.0f}"
                )
            except Exception as exc:
                phase(f"[LINE-POST] {name}: diagnostic failed ({exc})")


def _log_corridor_summary_post_solve(network, sns, current_period, phase) -> None:
    """
    Aggregate per-corridor line capacity vs. peak flow.

    Groups all parallel branches between the same (bus0, bus1) pair —
    parent + every vintage — and reports:
      • total_s_nom (MW) — sum of capacities on the corridor
      • peak_flow (MW)   — max(|flow_t|) where flow is summed across branches
      • corridor_util_%  — peak_flow / total_s_nom × 100
      • total_rent (€)   — sum of congestion_rent across branches

    Surfaces "the corridor is over-built" with a single number rather than
    making the user mentally add per-vintage capacities. Low corridor_util_%
    + total_rent>0 = LP needed the capacity for some hours but average
    utilisation is poor.

    Pairs of (bus0,bus1) and (bus1,bus0) are treated as the same corridor.
    """
    sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    if isinstance(sns, pd.MultiIndex):
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    phase(f"[CORRIDOR] Period {current_period} — corridor-level capacity vs. flow:")
    for comp_class, attr, t_attr in (("Line", "lines", "p0"), ("Transformer", "transformers", "p0")):
        df = getattr(network, attr, None)
        if df is None or df.empty:
            continue
        cap_col = "s_nom_opt" if "s_nom_opt" in df.columns else "s_nom"
        flow_df = getattr(getattr(network, f"{attr}_t"), t_attr, None)
        try:
            mu_up_df = getattr(getattr(network, f"{attr}_t"), "mu_upper", None)
            mu_lo_df = getattr(getattr(network, f"{attr}_t"), "mu_lower", None)
        except Exception:
            mu_up_df = mu_lo_df = None
        # Group by unordered (bus0, bus1) — frozenset handles either direction.
        corridors: dict = {}
        for name in df.index:
            try:
                bus0 = str(df.at[name, "bus0"])
                bus1 = str(df.at[name, "bus1"])
                key = frozenset((bus0, bus1))
                corridors.setdefault(key, []).append(name)
            except Exception:
                continue
        for key, names in corridors.items():
            try:
                bus_pair = "↔".join(sorted(key))
                total_sn = sum(float(df.at[n, cap_col] or 0.0) for n in names)
                # Per-snapshot total flow = Σ |p0| across branches in this corridor.
                # Branches can carry flow in different directions; the corridor's
                # net delivered power is sum(signed flow). For "is the corridor
                # over-built?" the relevant metric is total ENERGY moved, so we
                # use abs(p0) summed across branches.
                total_flow_series = None
                if flow_df is not None and not flow_df.empty:
                    cols = [n for n in names if n in flow_df.columns]
                    if cols:
                        # Sum across branches (signed sum better captures net
                        # corridor flow than abs-then-sum — counter-flow on
                        # one branch cancels on another).
                        total_flow_series = flow_df[cols].sum(axis=1).reindex(sns).fillna(0.0).abs()
                peak_flow = float(total_flow_series.max()) if total_flow_series is not None else 0.0
                mean_flow = float((total_flow_series * weights).sum() / weights.sum()) if total_flow_series is not None and weights.sum() > 0 else 0.0
                util_pct = (peak_flow / total_sn * 100.0) if total_sn > 0 else 0.0
                # Sum congestion rent across all branches in this corridor.
                total_rent = 0.0
                for mu_df in (mu_up_df, mu_lo_df):
                    if mu_df is None or mu_df.empty:
                        continue
                    for n in names:
                        if n not in mu_df.columns:
                            continue
                        s_nom_n = float(df.at[n, cap_col] or 0.0)
                        mu_s = mu_df[n].reindex(sns).fillna(0.0).abs()
                        total_rent += float((mu_s * weights).sum() * s_nom_n)
                phase(
                    f"[CORRIDOR] {comp_class} {bus_pair} [{len(names)} branches]: "
                    f"total_s_nom={total_sn:.1f} MW | "
                    f"peak_flow={peak_flow:.1f} MW ({util_pct:.1f}%) | "
                    f"mean_flow={mean_flow:.1f} MW | "
                    f"total_rent_eur={total_rent:,.0f}"
                )
            except Exception as exc:
                phase(f"[CORRIDOR] {comp_class} corridor failed: {exc}")


def _log_bus_balance_post_solve(network, sns, current_period, phase) -> None:
    """
    Per-bus power balance over the iteration. Useful for diagnosing
    transmission bottlenecks: if bus A has surplus renewable and bus B
    has unmet load, but the line A→B is binding, the LP can't deliver.

    For each bus:
      • load_MWh    — Σ load × snapshot_weight × period_weight
      • gen_MWh     — Σ p_at_bus × snapshot_weight × period_weight
      • net_export  — gen − load (MWh through lines)

    Negative net_export = bus imports. Large absolute values relative to
    load suggest a heavily-transit'd bus.
    """
    buses = getattr(network, "buses", None)
    if buses is None or buses.empty:
        return

    sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    if isinstance(sns, pd.MultiIndex):
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    # Per-bus load
    load_per_bus: dict[str, float] = {bus: 0.0 for bus in buses.index}
    loads = network.loads
    loads_t_p = getattr(network.loads_t, "p", None)
    loads_t_pset = getattr(network.loads_t, "p_set", None)
    # Prefer solved p (matches the load actually served), fall back to p_set.
    load_frame = loads_t_p if (loads_t_p is not None and not loads_t_p.empty) else loads_t_pset
    if load_frame is not None and not load_frame.empty and not loads.empty:
        for load_name in loads.index:
            if load_name not in load_frame.columns:
                continue
            bus = str(loads.at[load_name, "bus"])
            if bus not in load_per_bus:
                continue
            series = load_frame[load_name].reindex(sns).fillna(0.0)
            load_per_bus[bus] += float((series * weights).sum())

    # Per-bus generation (renewables + thermal + storage discharge)
    gen_per_bus: dict[str, float] = {bus: 0.0 for bus in buses.index}
    for comp_attr, t_attr, sign in (
        ("generators", "p", +1),  # gen output
        ("storage_units", "p", +1),  # storage net (>0 discharge, <0 charge)
        ("stores", "p", +1),
    ):
        df = getattr(network, comp_attr, None)
        if df is None or df.empty:
            continue
        t_df = getattr(getattr(network, f"{comp_attr}_t"), t_attr, None)
        if t_df is None or t_df.empty:
            continue
        for asset in df.index:
            if asset not in t_df.columns:
                continue
            bus = str(df.at[asset, "bus"])
            if bus not in gen_per_bus:
                continue
            series = t_df[asset].reindex(sns).fillna(0.0)
            gen_per_bus[bus] += sign * float((series * weights).sum())

    phase(f"[BUS-BAL] Period {current_period} — per-bus energy balance (GWh):")
    for bus in buses.index:
        load_mwh = load_per_bus.get(bus, 0.0)
        gen_mwh = gen_per_bus.get(bus, 0.0)
        net = gen_mwh - load_mwh  # > 0 export, < 0 import
        phase(
            f"[BUS-BAL] {bus}: load={load_mwh/1000:.1f} | "
            f"gen={gen_mwh/1000:.1f} | net_export={net/1000:+.1f}"
        )


def _log_cost_decomposition_post_solve(network, cfg, sns, current_period, phase) -> None:
    """
    Decompose the LP objective by period × cost category.

    Answers the question "why did the LP build capacity in period X instead
    of Y" by surfacing:

      • Per-period total OPEX-variable (Σ generator dispatch × marginal_cost)
      • Per-period CO2 surcharge (Σ emitter dispatch × CO2_intensity / eff
        × per-period CO2 price), separated from the base OPEX so the user
        can see how much the LP is "paying" for CO2 in each period.
      • Per-period new CAPEX added (assets with build_year=P, capital_cost
        × Δp_nom_opt). Reveals the LP's chronological investment plan.
      • Per-period dispatch by carrier — the carrier-level GWh feeding into
        the OPEX-CO2 split above.

    PLUS a one-shot "marginal capacity value" table at the end:
      • For each extendable asset, the LP dual on its p_nom_max constraint
        (= the EUR/MW the LP would have paid for one more MW of headroom).
      • Helps diagnose "the LP wanted more in 2027 but ran into p_nom_max"
        vs. "the LP didn't want more — the cap wasn't binding".

    Called once per myopic iteration (passing the iteration's sns and the
    current period). On the full-horizon path, called once with all periods.
    """
    import math as _math


    gens = getattr(network, "generators", None)
    if gens is None or gens.empty:
        return

    is_multi = isinstance(sns, pd.MultiIndex)

    # Per-snapshot effective weight = snapshot.objective × period.years.
    # This is the multiplier the LP sees for any cost term aggregated over
    # snapshots; matches how cost_breakdown / asset_economics report €.
    # `sns` is the LP's ACTUAL snapshot span, which for myopic foresight and
    # SCLOPF is a subset of n.snapshots — pass it explicitly so the weights
    # cover the same rows the objective did.
    weights = _period_utils.snapshot_weights(network, "objective", sns=sns)

    # Per-period bucket structure — periods are level-0 of the MultiIndex
    # or the single sentinel "ALL" on flat horizons.
    if is_multi:
        periods_seen = list(dict.fromkeys(int(p) for p in sns.get_level_values(0)))
    else:
        periods_seen = ["ALL"]
    period_data: dict = {
        p: {"opex_var_mc0": 0.0, "co2_surcharge": 0.0, "curt_penalty": 0.0,
            "voll_shed_cost": 0.0, "voll_shed_mwh": 0.0,
            "dsr_cost": 0.0, "dsr_mwh": 0.0,
            "new_capex": 0.0,
            "co2_emitted_t": 0.0,
            "by_carrier_mwh": {}}
        for p in periods_seen
    }

    # ── Per-period dispatch + OPEX split ────────────────────────────────
    # User-typed marginal_cost (NOT the LP-modified one). The CO2 surcharge
    # is the gap between the LP-applied marginal_cost (potentially
    # time-varying after our co2_price_per_period wrapper) and the user's
    # typed scalar. Compute both:
    p_t = getattr(network.generators_t, "p", None)
    getattr(network.generators_t, "marginal_cost", None)
    co2_by_carrier = {}
    try:
        if "co2_emissions" in network.carriers.columns:
            co2_by_carrier = {
                str(k).lower(): float(v)
                for k, v in network.carriers["co2_emissions"].items()
                if isinstance(v, (int, float)) and _math.isfinite(v)
            }
    except Exception:
        pass

    def _per_period_split(series, weights):
        """Group a per-snapshot series by period, returning {period → sum}."""
        weighted = (series.reindex(sns).fillna(0.0) * weights)
        if is_multi:
            return {int(p): float(v) for p, v in weighted.groupby(sns.get_level_values(0)).sum().items()}
        return {"ALL": float(weighted.sum())}

    if p_t is not None and not p_t.empty:
        for g in p_t.columns:
            if g not in gens.index:
                continue
            try:
                p_series = p_t[g].reindex(sns).fillna(0.0)
            except Exception:
                continue
            # User-typed marginal_cost (scalar). The wrapper restores this
            # after solve so what's on n.generators is the user's value.
            mc_scalar = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
            # LP-effective marginal_cost (time-varying if our wrapper wrote
            # to generators_t.marginal_cost during solve, but those get
            # cleared in restore — so this typically equals the scalar).
            # For the surcharge, we compute what CO2 cost WOULD HAVE been
            # applied analytically from cfg.co2_price_per_period.
            mwh_per_period = _per_period_split(p_series, weights)
            opex0_per_period = _per_period_split(p_series * mc_scalar, weights)
            carrier = str(gens.at[g, "carrier"]).lower() if "carrier" in gens.columns else ""
            eff = float(gens.at[g, "efficiency"]) if "efficiency" in gens.columns else 1.0
            if not _math.isfinite(eff) or eff <= 0:
                eff = 1.0
            intensity = co2_by_carrier.get(carrier, 0.0)
            for p, m in mwh_per_period.items():
                period_data.setdefault(p, {
                    "opex_var_mc0": 0.0, "co2_surcharge": 0.0,
                    "curt_penalty": 0.0, "voll_shed_cost": 0.0,
                    "voll_shed_mwh": 0.0, "dsr_cost": 0.0, "dsr_mwh": 0.0,
                    "new_capex": 0.0,
                    "co2_emitted_t": 0.0, "by_carrier_mwh": {}})
                period_data[p]["by_carrier_mwh"][carrier] = (
                    period_data[p]["by_carrier_mwh"].get(carrier, 0.0) + m
                )
            for p, e in opex0_per_period.items():
                period_data[p]["opex_var_mc0"] += e
            # CO2 emitted + per-period surcharge from cfg.co2_price_per_period.
            if intensity > 0:
                out_intensity = intensity / eff
                tco2_per_period = _per_period_split(p_series * out_intensity, weights)
                for p, t_co2 in tco2_per_period.items():
                    period_data[p]["co2_emitted_t"] += t_co2
                # CO2 surcharge = tCO2_per_period × co2_price_per_period[p]
                pp = getattr(cfg, "co2_price_per_period", None) or {}
                co2_scalar = getattr(cfg, "co2_price", 0.0) or 0.0
                for p, t_co2 in tco2_per_period.items():
                    try:
                        price = float(pp.get(str(int(p)), co2_scalar)) if p != "ALL" else float(co2_scalar)
                    except (TypeError, ValueError):
                        price = float(co2_scalar)
                    period_data[p]["co2_surcharge"] += t_co2 * price
            # Slack carriers are split out of the normal opex buckets — and
            # split from EACH OTHER (spec §4.4): demand response is a
            # resource priced at its compensation, never lumped into the
            # VOLL-shed bucket a reader treats as unserved energy.
            if is_slack_carrier(carrier):
                mwh_p = mwh_per_period
                cost_p = _per_period_split(p_series * mc_scalar, weights)
                if carrier == DSR_SLACK_CARRIER:
                    for p in mwh_p:
                        period_data[p]["dsr_mwh"] += mwh_p[p]
                        period_data[p]["dsr_cost"] += cost_p[p]
                else:
                    for p in mwh_p:
                        period_data[p]["voll_shed_mwh"] += mwh_p[p]
                        period_data[p]["voll_shed_cost"] += cost_p[p]

    # ── New CAPEX by build_year ─────────────────────────────────────────
    # Capital expenditure for assets newly built (Δp_nom_opt > 0) and built
    # in the iteration's current period (or any period for "ALL"). Mirrors
    # what cost_breakdown computes but split by build_year so the user sees
    # the LP's chronological allocation.
    for comp_attr, nom in (
        ("generators",    "p_nom"),
        ("storage_units", "p_nom"),
        ("stores",        "e_nom"),
        ("links",         "p_nom"),
        ("lines",         "s_nom"),
        ("transformers",  "s_nom"),
    ):
        df = getattr(network, comp_attr, None)
        if df is None or df.empty:
            continue
        if "build_year" not in df.columns:
            continue
        if f"{nom}_opt" not in df.columns:
            continue
        for name in df.index:
            try:
                by_raw = float(df.at[name, "build_year"])
                if not _math.isfinite(by_raw):
                    continue
                by_int = int(by_raw)
                if not (1900 <= by_int <= 2200):
                    continue
            except (TypeError, ValueError):
                continue
            if by_int not in period_data and "ALL" not in period_data:
                continue
            target_key = by_int if by_int in period_data else "ALL"
            try:
                opt = float(df.at[name, f"{nom}_opt"])
                ini = float(df.at[name, nom]) if nom in df.columns else 0.0
                cc = float(df.at[name, "capital_cost"]) if "capital_cost" in df.columns else 0.0
            except (TypeError, ValueError):
                continue
            # When capital_cost is 0 / NaN, fall back to annuitising
            # overnight_cost — mirrors PyPSA's periodized_cost(overnight_cost,
            # discount_rate, lifetime) so the diagnostic captures the LP's
            # actual investment signal for assets configured via overnight_cost
            # (the typical GUI setup, where capital_cost is left at 0 and the
            # solver derives the annuity at run time).
            if (not _math.isfinite(cc) or cc <= 0) and "overnight_cost" in df.columns:
                try:
                    oc = float(df.at[name, "overnight_cost"])
                except (TypeError, ValueError):
                    oc = 0.0
                if _math.isfinite(oc) and oc > 0:
                    dr = float(getattr(cfg, "discount_rate", 0.0) or 0.0)
                    if "discount_rate" in df.columns:
                        try:
                            dr_asset = float(df.at[name, "discount_rate"])
                            if _math.isfinite(dr_asset):
                                dr = dr_asset
                        except (TypeError, ValueError):
                            pass
                    lt = float(getattr(cfg, "default_lifetime", 25.0) or 25.0)
                    if "lifetime" in df.columns:
                        try:
                            lt_asset = float(df.at[name, "lifetime"])
                            if _math.isfinite(lt_asset) and lt_asset > 0:
                                lt = lt_asset
                        except (TypeError, ValueError):
                            pass
                    cc = oc * _annuity(dr, lt) if lt > 0 else 0.0
            delta = max(0.0, opt - ini)
            if delta <= 1e-6 or cc <= 0:
                continue
            period_data[target_key]["new_capex"] += cc * delta

    # ── Marginal capacity value (LP duals on p_nom_max) ─────────────────
    # Read from n.model.dual["{Class}-ext-p_nom-upper"] when assign_all_duals
    # was set (default in our run_simulation). The dual is € per relaxing
    # the upper bound by 1 MW — i.e., the LP's "willingness to pay" for one
    # more MW of capacity. Positive ⇒ the LP wanted more but the cap held;
    # 0 ⇒ the cap wasn't binding.
    marginal_caps: list[tuple[str, str, float, float]] = []  # (comp_class, name, p_nom_opt, mu)
    model = getattr(network, "model", None)
    if model is not None:
        for comp_class, df_attr in (
            ("Generator",    "generators"),
            ("StorageUnit",  "storage_units"),
            ("Store",        "stores"),
            ("Link",         "links"),
            ("Line",         "lines"),
            ("Transformer",  "transformers"),
        ):
            df = getattr(network, df_attr, None)
            if df is None or df.empty:
                continue
            nom_col = "e_nom" if comp_class == "Store" else (
                "s_nom" if comp_class in ("Line", "Transformer") else "p_nom"
            )
            if f"{nom_col}_opt" not in df.columns:
                continue
            try:
                dual_key = f"{comp_class}-ext-{nom_col}-upper"
                dual = model.dual.get(dual_key, None)
            except Exception:
                dual = None
            if dual is None:
                continue
            try:
                dual_dict = {str(k): float(v) for k, v in dual.to_pandas().items()}
            except Exception:
                try:
                    dual_dict = {str(k): float(v) for k, v in dual.items()}
                except Exception:
                    continue
            for name, mu in dual_dict.items():
                if not _math.isfinite(mu) or abs(mu) < 1e-3:
                    continue
                try:
                    p_opt = float(df.at[name, f"{nom_col}_opt"])
                except (KeyError, TypeError, ValueError):
                    p_opt = 0.0
                marginal_caps.append((comp_class, name, p_opt, mu))

    # ── Emit log ────────────────────────────────────────────────────────
    phase(f"[COST-DECOMP] Period {current_period} — investment-vs-OPEX breakdown:")
    for p in sorted([k for k in period_data.keys() if k != "ALL"],
                    key=lambda x: int(x) if isinstance(x, int) else 99999) + (
            ["ALL"] if "ALL" in period_data else []):
        d = period_data[p]
        total = (d["new_capex"] + d["opex_var_mc0"] + d["co2_surcharge"]
                 + d["voll_shed_cost"] + d["dsr_cost"])
        # Highlight the dominant cost component so user can scan visually.
        components = [
            ("CAPEX(new)", d["new_capex"]),
            ("OPEX(mc)",   d["opex_var_mc0"]),
            ("CO2 surcharge", d["co2_surcharge"]),
            ("VOLL shed",  d["voll_shed_cost"]),
            ("DSR",        d["dsr_cost"]),
        ]
        dominant = max(components, key=lambda x: abs(x[1]))[0]
        carrier_str = ", ".join(
            f"{c}={m/1000:.1f} GWh" for c, m in
            sorted(d["by_carrier_mwh"].items(), key=lambda x: -abs(x[1]))[:6]
        )
        phase(
            f"[COST-DECOMP] {p}: new_CAPEX={d['new_capex']/1e6:.2f}M€ | "
            f"OPEX(mc)={d['opex_var_mc0']/1e6:.2f}M€ | "
            f"CO2_surcharge={d['co2_surcharge']/1e6:.2f}M€ "
            f"({d['co2_emitted_t']/1e3:.1f} kt) | "
            f"VOLL_shed={d['voll_shed_cost']/1e6:.2f}M€ ({d['voll_shed_mwh']:.1f} MWh) | "
            f"DSR={d['dsr_cost']/1e6:.2f}M€ ({d['dsr_mwh']:.1f} MWh) | "
            f"dispatch: {carrier_str} | "
            f"dominant={dominant}, total={total/1e6:.2f}M€"
        )

    # Marginal capacity value table (compact — only assets with non-zero dual).
    if marginal_caps:
        phase(f"[COST-DECOMP] Period {current_period} — marginal capacity value (LP dual on p_nom_max):")
        # Top 10 by magnitude — these are the assets where the cap was binding.
        for cls, name, p_opt, mu in sorted(marginal_caps, key=lambda x: -abs(x[3]))[:10]:
            phase(
                f"[COST-DECOMP]   {cls} '{name}' @ p_nom_opt={p_opt:.1f} MW: "
                f"μ(p_nom_max)={mu:.2f} €/MW — "
                f"{'CAP BINDING (would build more)' if mu > 1e-3 else 'cap inactive'}"
            )
    else:
        phase(
            f"[COST-DECOMP] Period {current_period} — no extendable assets had a "
            "non-zero p_nom_max dual (every cap had headroom; LP didn't want more)."
        )


def _emit_core_post_solve_diagnostics(network, sns, current_period, phase) -> None:
    """
    The SIX per-solve diagnostic loggers shared by the full-horizon and myopic
    paths: curtailment / storage / line / corridor / capacity / bus-balance.

    Deliberately BARE (no try/except) so each caller keeps its OWN error policy
    — the divergence is intentional, not accidental:
      • full-horizon (`run_simulation`) wraps this in a swallow-all try/except:
        diagnostics are best-effort inspectability on a completed joint LP and
        must not fail the solve.
      • myopic (`_run_myopic_foresight`) calls it BARE: a diagnostic failure in a
        rolling iteration should SURFACE (propagate → abort that iteration),
        because a broken diagnostic there usually means the iteration's network
        state is itself malformed and the next iteration would compound it.

    Path-specific extras stay at the call site: full-horizon also emits
    `_log_cost_decomposition_post_solve` + `_log_global_constraint_shadow_prices`
    (the joint-LP global-constraint duals); myopic also emits the conditional
    `_log_sclopf_post_solve` (per-iteration contingencies) + cost-decomposition.
    """
    _log_curtailment_post_solve(network, sns, current_period, phase)
    _log_storage_post_solve(network, sns, current_period, phase)
    _log_line_post_solve(network, sns, current_period, phase)
    _log_corridor_summary_post_solve(network, sns, current_period, phase)
    _log_capacity_summary_post_solve(network, current_period, phase)
    _log_bus_balance_post_solve(network, sns, current_period, phase)


def _wrap_with_capex_budget(network: "pypsa.Network", user_fn, cfg, log_queue=None):
    """
    Compose the extra_functionality callback with a per-period CAPEX
    budget constraint.

    For each period P with `cfg.capex_budget_per_period[str(P)]` set:
      Σ over extendable assets with build_year == P:
        overnight_cost_g × Δp_nom_g     ≤     budget[P]

    where Δp_nom_g = p_nom_var_g − p_nom_g (the LP's NEW build above any
    pre-existing capacity). Falls back to capital_cost when overnight_cost
    is NaN/zero — preserves the constraint shape even on legacy datasets.

    Surfaces a `[BUDGET]` log line per period showing the configured cap and
    the assets contributing to that period's CAPEX. Returns ``user_fn``
    unchanged when no budget is set, so the LP stays untouched for runs
    that don't need this.
    """
    budgets = getattr(cfg, "capex_budget_per_period", None) or {}
    if not budgets:
        return user_fn

    # Normalize keys to int periods, drop entries with non-finite or
    # non-positive budgets (those are no-ops). Survive both
    # `{"2026": 5e8}` (JSON) and `{2026: 5e8}` (Python).
    normalized: dict[int, float] = {}
    for k, v in budgets.items():
        try:
            ik = int(k)
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not (fv > 0 and math.isfinite(fv)):
            continue
        normalized[ik] = fv
    if not normalized:
        return user_fn

    # Component → (static-attr, p_nom-field) for every cost-bearing
    # capacity decision. Same tuple shape vintage_service uses.
    capacity_attrs = (
        ("Generator",   "generators",    "p_nom"),
        ("StorageUnit", "storage_units", "p_nom"),
        ("Store",       "stores",        "e_nom"),
        ("Link",        "links",         "p_nom"),
        ("Line",        "lines",         "s_nom"),
        ("Transformer", "transformers",  "s_nom"),
    )

    def _emit(msg: str) -> None:
        _safe_log(log_queue, f"[BUDGET] {msg}")

    def capex_budget_fn(n, snapshots):
        for period, budget in normalized.items():
            # Find every extendable asset whose build_year matches this
            # period AND whose capacity variable lives in the LP.
            terms: list = []  # list of (name, coef, p_nom_var, p_nom_existing)
            for comp_class, attr, pnom in capacity_attrs:
                df = getattr(n, attr, None)
                if df is None or df.empty:
                    continue
                ext_col = f"{pnom}_extendable"
                if ext_col not in df.columns or "build_year" not in df.columns:
                    continue
                by = df["build_year"]
                # Some inputs leave build_year NaN — treat NaN as "not in
                # any period" (skip rather than match arbitrarily).
                mask = df[ext_col].astype(bool) & (by.fillna(-1).astype("Int64") == period)
                if not mask.any():
                    continue
                # The LP variable for p_nom is `{ComponentClass}-{p_nom}` in
                # linopy; same naming convention the curtailment wrapper
                # uses for Generator-p_nom.
                var_name = f"{comp_class}-{pnom}"
                if var_name not in n.model.variables:
                    continue
                p_nom_var = n.model.variables[var_name]
                # Effective per-MW cost: overnight_cost first, fall back to
                # capital_cost when overnight_cost is missing. Both are
                # in EUR per MW (overnight_cost is upfront, capital_cost is
                # annualised — for a budget cap we want upfront, but this is
                # a defensive fallback for incomplete data).
                for name in df.index[mask]:
                    oc = df.at[name, "overnight_cost"] if "overnight_cost" in df.columns else float("nan")
                    cc = df.at[name, "capital_cost"] if "capital_cost" in df.columns else float("nan")
                    try:
                        coef = float(oc) if (oc is not None and math.isfinite(float(oc)) and float(oc) > 0) else float(cc or 0.0)
                    except (TypeError, ValueError):
                        coef = 0.0
                    if coef <= 0 or not math.isfinite(coef):
                        continue
                    p_nom_existing = float(df.at[name, pnom] or 0.0)
                    terms.append((name, coef, p_nom_var.sel(name=name), p_nom_existing))
            if not terms:
                _emit(
                    f"Period {period}: budget €{budget:,.0f} set but no "
                    f"extendable assets with build_year={period} were found. "
                    f"Constraint skipped."
                )
                continue
            # Build the linopy expression: Σ coef × (p_nom_var − p_nom_existing) ≤ budget
            # Equivalent: Σ coef × p_nom_var ≤ budget + Σ coef × p_nom_existing
            existing_capex = sum(coef * p_nom_existing for _, coef, _, p_nom_existing in terms)
            rhs = budget + existing_capex
            # Linopy `merge` builds the linear combination from individual var terms.
            expr = sum(coef * var for _, coef, var, _ in terms)
            cname = f"capex_budget_{period}"
            try:
                n.model.add_constraints(expr <= rhs, name=cname)
                _emit(
                    f"Period {period}: cap = €{budget:,.0f} on "
                    f"{len(terms)} extendable asset(s); LP enforces "
                    f"Σ overnight_cost × Δp_nom ≤ €{budget:,.0f}."
                )
            except Exception as exc:
                _emit(
                    f"Period {period}: failed to add LP constraint: {exc}. "
                    f"Skipping this period's budget."
                )

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        capex_budget_fn(n, snapshots)

    return wrapper


def _wrap_with_ens_cap(network: "pypsa.Network", user_fn, cfg, log_queue=None):
    """
    Compose the extra_functionality callback with the reliability target:
    a per-investment-period cap on unserved ELECTRICAL energy (adequacy
    spec §5.1), plus optional per-zone ceilings (zone = bus `country`).

    For each period P (and, with `ens_zone_cap_multiple` set, each zone z):

        Σ_{t∈P} w_gen[t] · Σ_{slack b ∈ scope} p_shed[b,t]
            ≤ (ens_cap_permyriad / 1e4) [· multiple] × D_{P[,z]}

    where D is the period's (zone's) weighted electrical demand, computed
    INSIDE the callback from n.loads / n.loads_t.p_set at optimize time —
    after the modelling-assumption transforms, so load scalers are already
    applied and the cap is a fraction of the demand the LP actually serves.
    Slack membership via services/adequacy/slack (never name literals);
    "electrical" via the same _canonical_load_carrier_key the load views
    use, so the target and the Results tab can never disagree on scope.

    The cap sums the INVOLUNTARY tier only (spec §4.4): demand response is a
    resource, not unserved energy — the DSR tests assert it stays
    unconstrained by the cap.

    Period restriction is done by ZERO-MASKING the weight vector rather than
    selecting snapshot subsets — sidesteps the MultiIndex .sel pitfalls the
    curtailment wrapper documents. Returns user_fn unchanged when no target
    is set. Rolling/myopic strategies are blocked at preflight (each LP
    window would need its own demand denominator).
    """
    try:
        permyriad = cfg.ens_cap_permyriad
        permyriad = float(permyriad) if permyriad is not None else None
    except (TypeError, ValueError):
        permyriad = None
    if permyriad is None or not math.isfinite(permyriad) or permyriad <= 0:
        return user_fn

    zone_multiple = None
    try:
        zm = cfg.ens_zone_cap_multiple
        if zm is not None and math.isfinite(float(zm)) and float(zm) > 0:
            zone_multiple = float(zm)
    except (TypeError, ValueError):
        zone_multiple = None

    def _emit(msg: str) -> None:
        _safe_log(log_queue, f"[ENS] {msg}")

    def ens_cap_fn(n, snapshots):
        import xarray as xr

        from services.adequacy.slack import involuntary_slack_mask

        gens = getattr(n, "generators", None)
        buses = getattr(n, "buses", None)
        loads = getattr(n, "loads", None)
        if gens is None or gens.empty or buses is None or loads is None:
            return

        # Electrical bus set — canonical classifier, blank counts electrical.
        elec_buses: set[str] = set()
        if "carrier" in buses.columns:
            for b in buses.index:
                if _canonical_load_carrier_key(buses.at[b, "carrier"]) == "electrical":
                    elec_buses.add(str(b))
        else:
            elec_buses = set(map(str, buses.index))

        mask = involuntary_slack_mask(gens)
        slack_names = [
            str(g) for g in gens.index[mask]
            if str(gens.at[g, "bus"]) in elec_buses
        ]
        if not slack_names:
            _emit(
                f"Target {permyriad:g}‱ set but no electrical slack "
                "generators exist (voll off?) — cap skipped."
            )
            return
        if "Generator-p" not in n.model.variables:
            _emit("Generator-p variable absent — cap skipped.")
            return
        p_var = n.model.variables["Generator-p"]

        # Energy weights over the LP's snapshots.
        w = _period_utils.snapshot_weights(n, "generators", sns=snapshots)

        # Electrical demand per snapshot: static p_set overridden per-column
        # by loads_t.p_set where present, restricted to electrical buses.
        elec_loads = [
            str(l) for l in loads.index
            if str(loads.at[l, "bus"]) in elec_buses
        ]
        demand = pd.Series(0.0, index=snapshots)
        p_set_t = getattr(getattr(n, "loads_t", None), "p_set", None)
        for l in elec_loads:
            if p_set_t is not None and l in getattr(p_set_t, "columns", []):
                demand = demand.add(
                    p_set_t[l].reindex(snapshots).fillna(0.0), fill_value=0.0)
            else:
                try:
                    demand = demand + float(loads.at[l, "p_set"] or 0.0)
                except (TypeError, ValueError):
                    continue

        # Period buckets: MultiIndex level 0, or one "ALL" bucket.
        if isinstance(snapshots, pd.MultiIndex):
            period_of = pd.Series(snapshots.get_level_values(0), index=snapshots)
            periods = sorted(set(period_of))
        else:
            period_of = pd.Series("ALL", index=snapshots)
            periods = ["ALL"]

        # Zone of each slack: its bus's country (Task 2). None ⇒ system-only.
        zone_of_slack: dict[str, str] = {}
        if zone_multiple is not None and "country" in buses.columns:
            for g in slack_names:
                b = str(gens.at[g, "bus"])
                zone_of_slack[g] = str(buses.at[b, "country"] or "") if b in buses.index else ""
            zones = sorted(set(zone_of_slack.values()))
            if zones == [""]:
                _emit(
                    "Per-zone ceiling requested but every electrical bus has "
                    "a blank `country` — the ceiling collapses into a second "
                    "system cap (one unnamed zone). Populate bus.country to "
                    "make zones real."
                )
        else:
            zones = []

        snap_coord = p_var.coords["snapshot"]

        # Solve-time truth for the post-solve report (services/adequacy/
        # report.py): restore reverts the load-scaling transforms, so a
        # post-solve recomputation of these denominators would drift.
        # Cleaned up by run_simulation after the report is built.
        stash = {
            "permyriad": permyriad,
            "zone_multiple": zone_multiple,
            "zone_of_bus": {
                str(gens.at[g, "bus"]): z for g, z in zone_of_slack.items()
            },
            "periods": {},
        }
        n._ens_cap_targets = stash

        def _add_cap(name: str, names: list[str], w_masked: pd.Series,
                     cap_mwh: float, label: str) -> None:
            w_xr = xr.DataArray(
                w_masked.values, dims=["snapshot"],
                coords={"snapshot": snap_coord},
            )
            expr = sum((p_var.sel(name=g) * w_xr).sum() for g in names)
            n.model.add_constraints(expr <= cap_mwh, name=name)
            _emit(label)

        for P in periods:
            in_p = (period_of == P)
            w_p = w.where(in_p, 0.0)
            demand_p = float((demand * w_p).sum())
            cap_p = permyriad / 1e4 * demand_p
            stash["periods"][P] = {
                "cap_mwh": cap_p, "demand_mwh": demand_p, "zones": {},
            }
            _add_cap(
                f"ens_cap_{P}", slack_names, w_p, cap_p,
                f"Period {P}: ENS cap {cap_p:,.1f} MWh "
                f"({permyriad:g}‱ of {demand_p:,.0f} MWh electrical demand) "
                f"on {len(slack_names)} slack(s).",
            )
            for z in zones:
                z_names = [g for g in slack_names if zone_of_slack.get(g, "") == z]
                if not z_names:
                    continue
                z_buses = {str(gens.at[g, "bus"]) for g in z_names}
                z_loads = [l for l in elec_loads if str(loads.at[l, "bus"]) in z_buses]
                z_demand = pd.Series(0.0, index=snapshots)
                for l in z_loads:
                    if p_set_t is not None and l in getattr(p_set_t, "columns", []):
                        z_demand = z_demand.add(
                            p_set_t[l].reindex(snapshots).fillna(0.0), fill_value=0.0)
                    else:
                        try:
                            z_demand = z_demand + float(loads.at[l, "p_set"] or 0.0)
                        except (TypeError, ValueError):
                            continue
                z_demand_p = float((z_demand * w_p).sum())
                z_cap = zone_multiple * permyriad / 1e4 * z_demand_p
                stash["periods"][P]["zones"][z] = z_cap
                zlabel = z or "<blank>"
                _add_cap(
                    f"ens_zone_{zlabel}_{P}", z_names, w_p, z_cap,
                    f"Period {P}, zone {zlabel}: ceiling {z_cap:,.1f} MWh "
                    f"({zone_multiple:g}× the target on its own "
                    f"{z_demand_p:,.0f} MWh) on {len(z_names)} slack(s).",
                )

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        ens_cap_fn(n, snapshots)

    return wrapper


def _wrap_with_curtailment_cost(network: "pypsa.Network", user_fn, log_queue=None):
    """
    Build the final extra_functionality callable.

    If any generator carries a non-zero `curtailment_cost` (a custom column we
    surface in the GUI), return a wrapper that adds the LP term
        Σ curtailment_cost × (p_max_pu × nom − p)
    to the objective. When the user also supplied an extra_functionality, both
    callbacks run (user's first, ours second — order doesn't matter for
    objective terms).

    `log_queue` (optional): the SSE-bound queue used by ``run_simulation``.
    When provided, the wrapper pushes per-generator diagnostic lines onto it
    directly — bypassing Python's `logging` module, which by default filters
    INFO from the root logger and silently drops our diagnostics.

    Returns ``user_fn`` unchanged when no generator opts in, so we don't pay
    the import cost or burden the LP graph for runs that don't need it.
    """
    # The column may not exist on networks created before this feature shipped;
    # fall back gracefully if so.
    gens = getattr(network, "generators", None)
    if gens is None or "curtailment_cost" not in gens.columns:
        return user_fn
    # Capture the pre-wrapper `_objective_constant` baseline ONCE so re-solves
    # don't accumulate the curtailment constant on top of itself. PyPSA
    # persists `_objective_constant` through netcdf round-trips; without
    # this baseline, each re-solve would add the recomputed constant ON TOP
    # of the previous solve's constant, drifting the reported objective
    # upward by N×constant after N solves on the same network. The closure
    # below reads `n._baseline_objective_constant` and writes
    # `_objective_constant = baseline + new_constant`, idempotent across
    # re-solves.
    try:
        network._baseline_objective_constant = float(
            getattr(network, "_objective_constant", 0.0) or 0.0
        )
    except Exception:
        network._baseline_objective_constant = 0.0
    active = gens.index[gens["curtailment_cost"].fillna(0.0) > 0.0]
    if len(active) == 0:
        return user_fn

    def curtailment_cost_fn(n, snapshots):
        # Recompute the active set at solve time from n.generators directly.
        # The outer-scope `active` snapshot only captures parents — by the
        # time this callback fires, `vintage_service.apply_vintage_bounds`
        # has already added per-period extendable vintage rows that inherit
        # `curtailment_cost` from their parent. Those vintages are the only
        # sizing variables in multi-period mode (the parent's _extendable
        # was flipped to False), so missing them was silently disabling
        # the entire capacity-penalty term and letting the LP overbuild
        # renewables free of curtailment penalty.
        gens_now = getattr(n, "generators", None)
        if gens_now is None or "curtailment_cost" not in gens_now.columns:
            return
        live = [g for g in gens_now.index
                if float(gens_now.at[g, "curtailment_cost"] or 0.0) > 0.0]
        if not live:
            return
        n_ext = sum(1 for g in live if bool(gens_now.at[g, "p_nom_extendable"]))

        # Push diagnostics to the SSE log_queue directly so they show up
        # in the user-facing Log tab. Python's `logging` module filters
        # INFO at the root logger by default (PHASE markers are pushed
        # directly to the queue — see solver_service.run_simulation), so
        # logger.info(...) calls would be silently dropped.
        def _emit(msg: str) -> None:
            _safe_log(log_queue, f"[CURT] {msg}")

        _emit(
            f"Curtailment-cost wrapper active: {len(live)} generator(s) "
            f"({n_ext} extendable, including vintages) — penalty "
            f"Σ cc × (p_max_pu × p_nom − p) added to objective for extendables; "
            f"skipped for non-extendables (would cause one-sided dispatch subsidy)."
        )
        # nyears comes straight from PyPSA (Σ snapshot_w / 8760 per period
        # in multi-period mode) — this is what scales overnight_cost into
        # the LP via periodized_cost. nyears << 1 is the smoking gun for
        # the silent snapshot_weightings reset.
        try:
            ny = n.nyears
            if hasattr(ny, "items"):
                ny_str = ", ".join(f"{p}:{float(v):.3f}" for p, v in ny.items())
            else:
                ny_str = f"{float(ny):.3f}"
            _emit(f"n.nyears = {ny_str}")
        except Exception:
            pass

        import xarray as xr

        p_max_pu = n.get_switchable_as_dense("Generator", "p_max_pu")
        # Restrict p_max_pu to the LP's iteration snapshots. Without this,
        # myopic mode (which passes `snapshots=<this period's slice>`) sees
        # a capacity-penalty term summing p_max_pu over the FULL
        # n.snapshots index (e.g. 26280 snapshots for a 3-period model),
        # while the dispatch-subsidy term only sums p over the iteration's
        # subset (8760). The 3× imbalance over-penalises p_nom, the LP
        # under-builds renewables, and the result diverges sharply from a
        # full-horizon LP.
        try:
            p_max_pu = p_max_pu.loc[snapshots]
        except KeyError:
            pass  # defensive — keep the full frame if the slice mismatches

        # Pull the LP's actual per-snapshot weights so the extra term lines
        # up with PyPSA's own objective. PyPSA scales every operational
        # cost term by `snapshot_weightings.objective × period_years`; if
        # we don't match, the curtailment-cost term is in different units
        # from marginal_cost × p and the trade-off the LP makes is wrong.
        # Critical for Phase-2 limited-foresight runs where representative
        # future snapshots carry weights up to ~50 (one rep stands in for
        # one cluster of weeks).
        sw = getattr(n, "snapshot_weightings", None)
        if sw is not None and "objective" in sw.columns:
            try:
                w_obj = sw.loc[snapshots, "objective"].astype(float)
            except Exception:
                w_obj = pd.Series(1.0, index=snapshots, dtype=float)
        else:
            w_obj = pd.Series(1.0, index=snapshots, dtype=float)

        # Period-years weighting — PyPSA multiplies operational costs by
        # `investment_period_weightings.years[period]` in multi-period
        # mode. Mirror that so a snapshot in a 4-year-weighted period
        # contributes 4× to both the dispatch subsidy and the capacity
        # penalty, the same way it does to marginal_cost × p.
        ipw = getattr(n, "investment_period_weightings", None)
        years_map: dict[int, float] = {}
        if ipw is not None and "years" in ipw.columns:
            for pp, yy in ipw["years"].items():
                try:
                    years_map[int(pp)] = float(yy)
                except (TypeError, ValueError):
                    continue
        is_mp = isinstance(snapshots, pd.MultiIndex)
        if is_mp and years_map:
            period_level = snapshots.get_level_values(0)
            year_mul = pd.Series(
                [years_map.get(int(pp), 1.0) for pp in period_level],
                index=snapshots, dtype=float,
            )
            weights = w_obj * year_mul
        else:
            weights = w_obj

        # PyPSA 1.x linopy variables use `name` as the per-component dimension
        # (not the component class name). So Generator-p has dims (snapshot,
        # name) and Generator-p_nom has dim (name,). Also: linopy's Variables
        # collection has no `.get()`; use `in` for membership then `[]` for
        # retrieval.
        p = n.model.variables["Generator-p"]      # (snapshot, name) MW
        has_p_nom_var = "Generator-p_nom" in n.model.variables
        p_nom_var = n.model.variables["Generator-p_nom"] if has_p_nom_var else None

        # xarray DataArray for the dispatch-term linopy multiplication.
        # Reuse `p`'s OWN snapshot coord directly — guaranteed to align for
        # broadcast against `p.sel(name=g)`. Building from list(MultiIndex)
        # via `coords={"snapshot": list(snapshots)}` doesn't work because
        # xarray sees a list of (period, timestep) tuples as 2-D data and
        # rejects it for a 1-D coord. linopy's internal representation of
        # the snapshot dim already encodes the multi-period MultiIndex in a
        # broadcast-compatible way; defer to it.
        try:
            snap_coord = p.coords["snapshot"]
            w_xr = xr.DataArray(
                weights.values, dims=["snapshot"],
                coords={"snapshot": snap_coord},
            )
        except Exception:
            # Fallback: positional alignment only (no coord). Works for
            # flat snapshot indexes and avoids crashing the LP build if
            # linopy doesn't expose the coord under the name we expect.
            w_xr = xr.DataArray(weights.values, dims=["snapshot"])

        # Per-generator loop — simpler than wrestling xarray broadcasting for
        # mixed fixed/extendable cases, and the active set is typically small
        # (one entry per renewable carrier, not per snapshot).
        #
        # Math we're adding to the objective (weighted form):
        #   cost × Σ_t w_t × (p_max_pu_t × nom − p_t)
        # Linopy rejects pure-constant terms in the objective, so we split it
        # into ONLY the variable parts:
        #   • dispatch-incentive term: −cost × Σ_t w_t × p_t   (always variable)
        #   • capacity-penalty term:   +cost × (Σ_t w_t × p_max_pu_t) × p_nom_var
        #     (only when extendable; otherwise it's a constant we drop)
        # The dropped constant doesn't affect the optimum — the user's
        # frontend reconstructs the true curtailment cost from the result
        # series anyway (Dispatch.tsx's `curtailmentCost` memo).
        # Build a per-asset "effective capital cost" lookup so the diagnostic
        # log can show the LP-visible €/MW alongside the curtailment-penalty
        # coefficient. PyPSA's c.capital_cost = periodized_cost(...) — same
        # function the LP itself calls. We compute lazily; failures (e.g.
        # network without overnight_cost column) fall back to NaN.
        try:
            eff_cap_cost = n.c.generators.capital_cost  # pd.Series
        except Exception:
            eff_cap_cost = None

        # Track the IMPLIED capacity penalty for non-extendable gens —
        # we can't add the constant `cost × Σw × pmp × p_nom_fixed` to the
        # linopy objective (rejected), but we still need the reported
        # objective to include it so the user sees an accurate total cost.
        # PyPSA's `n._objective_constant` is exactly the offset added to
        # `solver_obj` to get the user-visible `n.objective`. We add to it
        # after the wrapper loop so PyPSA's own constant calculation is
        # preserved.
        nonext_capacity_constant = 0.0

        for g in live:
            cost = float(n.generators.at[g, "curtailment_cost"])
            extendable = bool(n.generators.at[g, "p_nom_extendable"])

            # ── Dispatch subsidy — applied UNIFORMLY to every renewable
            #    that opts into curtailment_cost, regardless of whether
            #    its capacity is extendable or frozen. This is the key
            #    merit-order fix: without it, a new extendable vintage
            #    gets effective mc=−cost while frozen vintages of the
            #    same asset get mc=0, so the LP curtails the OLDER
            #    vintages preferentially. Applying the subsidy uniformly
            #    ties them at the same effective marginal cost — the LP
            #    distributes curtailment proportionally across all
            #    same-bus same-profile renewables.
            n.model.objective += -cost * (w_xr * p.sel(name=g)).sum()

            # ── Capacity penalty — extendable: linear LP term; non-
            #    extendable: constant accumulated for the reported
            #    objective. Together they balance the dispatch subsidy
            #    (net contribution is `cost × curtailment` regardless of
            #    whether p_nom was decided by the LP or was a fixed input).
            weighted_pmp_sum = 0.0
            if g in p_max_pu.columns:
                weighted_pmp_sum = float((weights * p_max_pu[g]).sum())
            if extendable and p_nom_var is not None and weighted_pmp_sum > 0:
                n.model.objective += cost * weighted_pmp_sum * p_nom_var.sel(name=g)
            elif not extendable and weighted_pmp_sum > 0:
                # Constant: `cost × Σw × pmp × p_nom_fixed`. Doesn't affect
                # LP decisions (LP optimum is invariant to additive
                # constants in the objective) but DOES affect the reported
                # objective value. Added to _objective_constant below.
                p_nom_fixed = float(n.generators.at[g, "p_nom"] or 0.0)
                nonext_capacity_constant += cost * weighted_pmp_sum * p_nom_fixed

            # Per-generator diagnostic. Surfaces:
            #   • capex_eff: LP-visible €/MW (annuitised, scaled by nyears).
            #   • cap_penalty_coef: cost × weighted_pmp_sum, LP coefficient
            #     on extendable's p_nom_var (0 for non-extendable — that
            #     part is tracked in nonext_capacity_constant instead).
            #   • dispatch_subsidy_max: cost × Σ w × p_max_pu × (p_nom_max or
            #     p_nom_fixed) — the maximum the LP could deduct via the
            #     dispatch term. Same effective marginal cost for ALL
            #     renewables, so merit order stays neutral between
            #     vintages.
            try:
                capex_eff = (
                    float(eff_cap_cost.get(g, float("nan")))
                    if eff_cap_cost is not None else float("nan")
                )
                cap_penalty_coef = cost * weighted_pmp_sum if extendable else 0.0
                _emit(
                    f"{g} | ext={extendable} | wrapper_applied=True | "
                    f"cc={cost:.0f} €/MWh | capex_eff={capex_eff:.0f} €/MW | "
                    f"cap_penalty_coef={cap_penalty_coef:.0f} €/MW"
                )
            except Exception:
                pass

        # Inject the non-extendable capacity constant into PyPSA's
        # `_objective_constant`. PyPSA's `n.objective` (post-solve) is
        # `solver_obj + _objective_constant` — adding here keeps the
        # reported total accurate (LP's variable-only objective sees the
        # subsidy reducing it, the constant restores the offsetting
        # capacity term so net effect is `cost × actual_curtailment` per
        # non-extendable, matching the math).
        #
        # The constant is recomputed FROM SCRATCH per solve, but PyPSA
        # persists `_objective_constant` through netcdf round-trips —
        # without resetting to the baseline first, each re-solve would
        # ADD on top of the previous solve's constant, drifting the
        # reported objective upward by N×constant after N solves.
        # `_baseline_objective_constant` was captured BEFORE the LP run
        # (see baseline-capture comment further up the file) so the
        # subtract-then-add here is idempotent across re-solves.
        if nonext_capacity_constant != 0.0:
            try:
                # Reset to the pre-wrapper baseline FIRST so re-solves
                # don't accumulate. The baseline lives on `n` so it
                # survives between the wrapper-factory call and the
                # closure invocation (PyPSA may call the closure once
                # per `n.optimize` cycle).
                baseline = float(getattr(n, "_baseline_objective_constant", 0.0) or 0.0)
                n._objective_constant = baseline + nonext_capacity_constant
                _emit(
                    f"Implied capacity constant for non-extendable renewables: "
                    f"+€{nonext_capacity_constant:,.0f} added to reported objective "
                    f"(baseline €{baseline:,.0f} preserved)."
                )
            except Exception as exc:
                _emit(f"Couldn't inject implied capacity constant: {exc}")

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        curtailment_cost_fn(n, snapshots)

    return wrapper


# Healthy band for the geometric mean of objective cost coefficients. Inside
# this band the model is well-conditioned and auto-scale is a no-op; outside,
# the recommended scale pulls the geomean back toward _COND_TARGET.
_COND_GEOMEAN_LOW = 1e-1
_COND_GEOMEAN_HIGH = 1e4
_COND_TARGET = 1e2
# Warn when the coefficient spread exceeds this many orders of magnitude — a
# uniform objective scale CANNOT fix spread (only HiGHS's internal per-element
# scaling / normalising the outlier costs can), so the diagnostic calls it out.
_COND_SPREAD_WARN_LOG10 = 9.0


def _objective_conditioning(network: "pypsa.Network") -> dict | None:
    """
    Summarise the magnitude spread of the LP's objective cost coefficients.

    The objective is roughly ``Σ weight·marginal_cost·dispatch +
    Σ capital_cost·capacity``, so the per-variable coefficient magnitudes are
    ``|marginal_cost|·max_weight`` (operating) and ``|capital_cost|`` on
    extendable assets (investment). A very wide spread, or a geometric mean far
    from O(1–1e3), is what trips solver "numerical trouble" / "objective too
    large" warnings.

    Returns ``None`` when there are fewer than two nonzero coefficients (nothing
    to condition), else a dict with ``count / min / max / geomean /
    spread_log10 / recommended_scale``. ``recommended_scale`` is a power of 10
    (1.0 when already healthy) that, applied to the whole objective, pulls the
    geometric mean toward ``_COND_TARGET``. Pure + read-only.
    """
    import numpy as _np

    coeffs: list[float] = []
    try:
        sw = network.snapshot_weightings
        max_w = float(sw["objective"].abs().max()) if "objective" in sw else 1.0
    except Exception:
        max_w = 1.0
    if not _np.isfinite(max_w) or max_w <= 0:
        max_w = 1.0

    def _nonzero_abs(df, col):
        if df is None or getattr(df, "empty", True) or col not in df.columns:
            return None
        v = df[col].abs()
        return v[(v > 0) & _np.isfinite(v)]

    # Operating coefficients: |marginal_cost| × representative weight.
    for comp in ("generators", "links", "storage_units", "stores"):
        v = _nonzero_abs(getattr(network, comp, None), "marginal_cost")
        if v is not None and not v.empty:
            coeffs.extend((v * max_w).tolist())
    # Investment coefficients: |capital_cost| on EXTENDABLE assets only.
    for comp, ext_col in (("generators", "p_nom_extendable"),
                          ("links", "p_nom_extendable"),
                          ("lines", "s_nom_extendable"),
                          ("storage_units", "p_nom_extendable"),
                          ("stores", "e_nom_extendable")):
        df = getattr(network, comp, None)
        if df is None or getattr(df, "empty", True) or "capital_cost" not in df.columns:
            continue
        sub = df[df[ext_col]] if ext_col in df.columns else df
        v = _nonzero_abs(sub, "capital_cost")
        if v is not None and not v.empty:
            coeffs.extend(v.tolist())

    coeffs = [c for c in coeffs if c > 0 and _np.isfinite(c)]
    if len(coeffs) < 2:
        return None

    arr = _np.asarray(coeffs, dtype=float)
    cmin, cmax = float(arr.min()), float(arr.max())
    geomean = float(_np.exp(_np.log(arr).mean()))
    spread_log10 = float(_np.log10(cmax / cmin)) if cmin > 0 else float("inf")

    if geomean > 0 and _np.isfinite(geomean) and (
        geomean < _COND_GEOMEAN_LOW or geomean > _COND_GEOMEAN_HIGH
    ):
        rec = 10.0 ** round(_np.log10(_COND_TARGET / geomean))
        rec = float(min(max(rec, 1e-6), 1e6))
    else:
        rec = 1.0

    return {
        "count": len(coeffs),
        "min": cmin,
        "max": cmax,
        "geomean": geomean,
        "spread_log10": spread_log10,
        "recommended_scale": rec,
    }


def _log_objective_conditioning(network: "pypsa.Network", log_queue) -> None:
    """
    Emit a one-line ``[NUMERICS]`` pre-solve report on the objective's
    conditioning, and a WARN with an actionable remedy when it's poor.

    Read-only — never mutates the network or the LP. Cheap (vectorised over the
    component DataFrames). Runs AFTER modelling assumptions so VOLL slacks /
    vintage rows are included in the magnitude analysis. Writes raw ``[NUMERICS]``
    lines (the tag convention shared with ``[VALIDATION]`` / ``[OBJ-SCALE]``) so
    the frontend can colour the WARN amber.
    """
    if log_queue is None:
        return
    try:
        cond = _objective_conditioning(network)
    except Exception:
        return  # diagnostics must never break a solve
    if cond is None:
        return

    def _put(msg: str) -> None:
        _safe_log(log_queue, msg)

    _put(
        f"[NUMERICS] Objective cost coefficients: {cond['count']} terms, "
        f"range {cond['min']:.3g}…{cond['max']:.3g} "
        f"(geom. mean {cond['geomean']:.3g}, spread {cond['spread_log10']:.1f} "
        "orders of magnitude)."
    )
    poor_geomean = cond["recommended_scale"] != 1.0
    wide_spread = cond["spread_log10"] > _COND_SPREAD_WARN_LOG10
    if poor_geomean or wide_spread:
        bits = []
        if wide_spread:
            bits.append(
                "coefficients span a very wide magnitude range — normalising "
                "outlier cost/capacity values is the most robust fix"
            )
        if poor_geomean:
            bits.append(
                f"set user_objective_scale ≈ {cond['recommended_scale']:g} "
                "(or enable Auto-scale objective) to recentre the magnitudes"
            )
        _put("[NUMERICS] WARN: the objective may be ill-conditioned — "
             + "; ".join(bits) + ".")


def _wrap_with_objective_scale(network: "pypsa.Network", user_fn, cfg, log_queue=None):
    """
    Apply `cfg.user_objective_scale` to the linopy model's objective.

    Runs AFTER `n.optimize` builds the model but BEFORE `model.solve()` —
    PyPSA's ``extra_functionality`` callback is the canonical hook for that
    boundary. Multiplies ``model.objective`` by ``scale`` in place, which is
    invariant to the LP's argmin (only the solver's INTERNAL numerics shift).

    Post-solve responsibilities (rescaling reported `n.objective` and LP
    duals back to user-facing units) live in `_rescale_results_for_objective`,
    invoked from the outer solve driver after `n.optimize(...)` returns.

    Returns ``user_fn`` unchanged when ``scale ~= 1.0`` so the LP graph
    isn't touched for runs that don't need this.
    """
    import math as _math

    raw = getattr(cfg, "user_objective_scale", 1.0)
    # Auto-scale: when enabled, the solver picks the scale itself from the
    # model's cost-coefficient spread, overriding the manual value. A no-op
    # (recommended_scale == 1.0) for well-conditioned models, so it's safe to
    # leave on. Computed on the pre-modelling-assumptions network — the base
    # cost structure already fixes the power-of-10 decision; the later VOLL/
    # vintage rows don't shift it. Falls back to the manual value if the
    # analyser can't run.
    if getattr(cfg, "auto_objective_scale", False):
        try:
            cond = _objective_conditioning(network)
        except Exception:
            cond = None
        if cond is not None:
            raw = cond["recommended_scale"]
            if log_queue is not None and raw != 1.0:
                try:
                    log_queue.put(
                        f"[NUMERICS] Auto-scale engaged: objective × {raw:g} "
                        f"(geom. mean coeff {cond['geomean']:.3g} → ~{_COND_TARGET:g}); "
                        "reported € and duals are divided back post-solve."
                    )
                except Exception:
                    pass
    try:
        scale = float(raw)
    except (TypeError, ValueError):
        scale = 1.0
    if not _math.isfinite(scale) or scale <= 0.0:
        if log_queue is not None:
            try:
                log_queue.put(
                    f"[OBJ-SCALE] Ignoring invalid user_objective_scale={raw!r}: "
                    "must be a positive finite number. Using 1.0."
                )
            except Exception:
                pass
        scale = 1.0
    # Stash the scale on the network so the post-solve rescaler can find
    # it. Survives the `try/finally` restore because we ALSO clear it
    # explicitly in `_rescale_results_for_objective`. Use a sentinel
    # attribute name that won't collide with anything PyPSA owns.
    network._pypsa_gui_objective_scale = scale  # type: ignore[attr-defined]

    if abs(scale - 1.0) < 1e-12:
        return user_fn

    def _emit(msg: str) -> None:
        _safe_log(log_queue, msg)

    def objective_scale_fn(n, snapshots):
        try:
            model = getattr(n, "model", None)
            if model is None or not hasattr(model, "objective"):
                _emit("[OBJ-SCALE] Model.objective unavailable — scaling skipped.")
                return
            try:
                model.objective.expression = model.objective.expression * scale
            except AttributeError:
                # linopy versions where `.objective` is itself the expression.
                try:
                    model.objective = model.objective * scale
                except Exception as exc:
                    _emit(f"[OBJ-SCALE] Could not scale objective: {exc}")
                    return
            _emit(
                f"[OBJ-SCALE] LP objective × {scale:g} for numerical conditioning "
                "(optimal x* is invariant; n.objective and duals are divided back "
                "post-solve to keep user-facing € unchanged)."
            )
        except Exception as exc:
            _emit(f"[OBJ-SCALE] Scaling failed silently: {exc}")

    def wrapper(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        objective_scale_fn(n, snapshots)

    return wrapper


def _rescale_results_for_objective(network: "pypsa.Network", log_queue=None) -> None:
    """
    Reverse the LP-objective scaling on reported result fields.

    Called once per solve, right after `n.optimize(...)` returns (success
    OR failure — duals from a failed solve are usually empty, but the
    call is no-op safe). Multiplies every value the user reads back in
    EUR / €/MWh by ``1/scale`` so the GUI labels stay correct.

    Touches:
      • n.objective       — scalar
      • buses_t.marginal_price
      • lines_t.mu_upper, mu_lower
      • transformers_t.mu_upper, mu_lower
      • global_constraints.mu (per-constraint shadow price)
    """
    import math as _math

    scale = getattr(network, "_pypsa_gui_objective_scale", 1.0)
    try:
        scale_f = float(scale)
    except (TypeError, ValueError):
        scale_f = 1.0
    # Clear the sentinel regardless so the next solve starts fresh.
    try:
        del network._pypsa_gui_objective_scale
    except AttributeError:
        pass
    if not _math.isfinite(scale_f) or scale_f <= 0.0 or abs(scale_f - 1.0) < 1e-12:
        return

    inv = 1.0 / scale_f

    def _emit(msg: str) -> None:
        _safe_log(log_queue, msg)

    # n.objective is a read-only property; the underlying value lives in
    # `_objective`. Write to that field directly so the property reflects
    # the rescaled total.
    try:
        cur = getattr(network, "_objective", None)
        if cur is None:
            cur = getattr(network, "objective", None)
        if cur is not None:
            network._objective = float(cur) * inv
    except Exception as exc:
        _emit(f"[OBJ-SCALE] post-solve n.objective rescale failed: {exc}")
    # `_objective_constant` carries the curtailment-wrapper's
    # non-extendable capacity term — added by the wrapper at LP-build
    # time, BEFORE the objective scale multiplication runs. PyPSA's
    # property `n.objective` returns `_objective + _objective_constant`,
    # so if we only rescale `_objective` here and leave the constant
    # unscaled, the displayed total mixes scaled (variable part) with
    # unscaled (constant part) units. Rescale both so the user-facing
    # total stays in € regardless of `user_objective_scale`.
    try:
        cur_const = getattr(network, "_objective_constant", None)
        if cur_const is not None:
            network._objective_constant = float(cur_const) * inv
    except Exception as exc:
        _emit(f"[OBJ-SCALE] post-solve _objective_constant rescale failed: {exc}")
    # Dual-bearing _t accessors:
    for comp_attr, attrs in (
        ("buses_t",       ["marginal_price"]),
        ("lines_t",       ["mu_upper", "mu_lower"]),
        ("transformers_t", ["mu_upper", "mu_lower"]),
        ("links_t",       ["mu_upper", "mu_lower"]),
    ):
        acc = getattr(network, comp_attr, None)
        if acc is None:
            continue
        for a in attrs:
            df = getattr(acc, a, None)
            if df is None or getattr(df, "empty", True):
                continue
            try:
                setattr(acc, a, df * inv)
            except Exception as exc:
                _emit(f"[OBJ-SCALE] could not rescale {comp_attr}.{a}: {exc}")
    # Global constraint duals (`mu`) — €/(constraint-unit), e.g. €/tCO2 on
    # primary_energy CO2 caps.
    try:
        gc = getattr(network, "global_constraints", None)
        if gc is not None and not gc.empty and "mu" in gc.columns:
            network.global_constraints["mu"] = gc["mu"] * inv
    except Exception as exc:
        _emit(f"[OBJ-SCALE] could not rescale global_constraints.mu: {exc}")
    _emit(
        f"[OBJ-SCALE] Rescaled n.objective + LP duals by 1/{scale_f:g} "
        f"= {inv:g} (back to user-facing units)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Modelling assumptions (applied transiently at solve time)
# ─────────────────────────────────────────────────────────────────────────────

def _annuity(rate: float, lifetime: float) -> float:
    """
    Standard CRF / annuity factor.

    r × (1+r)^L / ((1+r)^L − 1), with the degenerate rate→0 case falling back
    to 1/L (straight-line). lifetime ≤ 0 returns 0 (no annualisation).
    """
    if lifetime <= 0:
        return 0.0
    if rate == 0.0:
        return 1.0 / lifetime
    factor = (1.0 + rate) ** lifetime
    return rate * factor / (factor - 1.0)


def fill_periodized_cost_defaults(n, cfg: "SolverConfig") -> Callable[[], None]:
    """
    Fill per-asset `discount_rate` / `lifetime` from the global config for
    any asset that has `overnight_cost` set but a blank discount_rate or a
    non-finite lifetime, and return a `revert` callable that undoes the fill.

    Used in two places:
      • solver_service.run_simulation — before n.optimize() so the LP sees
        a valid annuitization. PyPSA's consistency check otherwise raises
        "overnight_cost set but missing discount_rate".
      • routers/simulation.py — wrapped around `n.statistics()` calls.
        PyPSA computes the periodized capital_cost via
        `periodized_cost(capital_cost, overnight_cost, discount_rate, ...)`;
        with discount_rate=NaN that returns NaN for assets carrying
        overnight_cost, which collapses to 0 in our cost_breakdown and
        makes the "Investment (new only)" KPI under-report.

    Both sites need the same fill, so it lives here once. Caller MUST invoke
    the returned revert() in a finally block — otherwise the on-disk network
    state keeps the fill and a later global-rate change won't propagate.
    """
    import numpy as np

    undo: list[tuple[str, str, pd.Index, pd.Series]] = []
    for comp_attr in ("generators", "storage_units", "stores", "links", "lines", "transformers"):
        df = getattr(n, comp_attr, None)
        if df is None or df.empty or "overnight_cost" not in df.columns:
            continue
        has_overnight = df["overnight_cost"].notna() & (df["overnight_cost"] != 0)
        if not has_overnight.any():
            continue
        if "discount_rate" in df.columns:
            missing_dr = has_overnight & df["discount_rate"].isna()
            if missing_dr.any():
                idx = df.index[missing_dr]
                original = df.loc[idx, "discount_rate"].copy()
                df.loc[idx, "discount_rate"] = cfg.discount_rate
                undo.append((comp_attr, "discount_rate", idx, original))
        if "lifetime" in df.columns:
            lt = df["lifetime"]
            # Treat both NaN and inf as "user didn't pick a number" — PyPSA's
            # default lifetime is +inf, so we can't distinguish "explicit
            # perpetuity" from "unset" anyway.
            missing_lt = has_overnight & (lt.isna() | ~np.isfinite(lt))
            if missing_lt.any():
                idx = df.index[missing_lt]
                original = df.loc[idx, "lifetime"].copy()
                df.loc[idx, "lifetime"] = cfg.default_lifetime
                undo.append((comp_attr, "lifetime", idx, original))

    def revert() -> None:
        for comp_attr, col, idx, original in reversed(undo):
            df = getattr(n, comp_attr, None)
            if df is None:
                continue
            valid = [i for i in idx if i in df.index]
            if valid:
                df.loc[valid, col] = original.loc[valid]

    return revert


@contextmanager
def with_periodized_cost_defaults(n, cfg: "SolverConfig"):
    """
    Context-manager wrapper around fill_periodized_cost_defaults — for
    callers that prefer `with` over an explicit try/finally pair.
    """
    revert = fill_periodized_cost_defaults(n, cfg)
    try:
        yield
    finally:
        revert()


def _reference_build_year(n) -> float:
    """
    Reference year for discounting future-year investment to present value.

    Multi-period: the FIRST investment period. PV factors then discount each
    asset from its build_year back to the start of the planning horizon — and
    a build_year at or before the first period (including the GUI's 0 default
    for pre-existing assets) collapses to a PV factor of 1.0, as it should.

    Using ``min(build_year)`` here instead silently breaks multi-period runs:
    one pre-existing asset with build_year=0 drops the reference to year 0,
    and every asset built in a real year (2026, 2027, …) then gets discounted
    by (1 + r)^-2026 ≈ 0 — its PV investment rounds to zero (PV factor ~3e-60
    in practice). That zeroed every new build's CAPEX / "Investment (new, PV)"
    KPI and the expansion-by-class chart.

    Single-period: the earliest finite build_year across all cost-bearing
    components (0.0 when none carry one) — every PV factor then collapses to
    1.0 for the common overnight run, the original behaviour.
    """
    import numpy as _np
    import pandas as _pd
    # Multi-period → anchor PV discounting on the first investment period.
    try:
        if isinstance(n.snapshots, _pd.MultiIndex) and len(n.investment_periods) > 0:
            return float(min(int(p) for p in n.investment_periods))
    except (TypeError, ValueError, AttributeError):
        pass
    ref: float | None = None
    for comp_attr in ("generators", "storage_units", "stores", "links",
                      "lines", "transformers"):
        df = getattr(n, comp_attr, None)
        if df is None or df.empty or "build_year" not in df.columns:
            continue
        bys = df["build_year"]
        finite = bys[_np.isfinite(bys)]
        if len(finite) > 0:
            mn = float(finite.min())
            ref = mn if ref is None else min(ref, mn)
    return ref if ref is not None else 0.0


def _pv_factor_series(df, cfg: "SolverConfig", reference_year: float):
    """
    Per-asset present-value factor ``(1+r)^-(build_year - reference)``.

    ``r`` is the per-asset ``discount_rate`` column (which the LP-time fill
    populated from the global config for any blank entries). ``build_year``
    falls back to the reference year so an asset without a build_year just
    contributes a PV factor of 1.0. Negative deltas (asset built before the
    reference) are clipped to 0 too — pre-reference investment is already
    sunk; no compounding-up to present value here.
    """
    import numpy as _np
    import pandas as _pd
    if "build_year" in df.columns:
        bys = df["build_year"].where(_np.isfinite(df["build_year"]), reference_year)
    else:
        bys = _pd.Series(reference_year, index=df.index, dtype=float)
    years_future = (bys - reference_year).clip(lower=0)
    if "discount_rate" in df.columns:
        drs = df["discount_rate"].where(_np.isfinite(df["discount_rate"]), cfg.discount_rate)
    else:
        drs = _pd.Series(cfg.discount_rate, index=df.index, dtype=float)
    return (1.0 + drs) ** (-years_future)


def periodized_capital_costs(n, cfg: "SolverConfig") -> dict[str, dict[str, dict[str, float]]]:
    """
    Return per-asset cost facts for every cost-bearing component, keyed as
    ``{component_attr: {name: {"capital_cost": float, "overnight_cost": float, "lifetime": float}}}``.

    Two different cost numbers per asset:

      * ``capital_cost`` — PyPSA's annualised cost (`comp.capital_cost`), i.e.
        ``overnight × annuity × nyears`` for assets parameterised via
        overnight_cost, or the raw `capital_cost` column otherwise. This is
        what the LP objective sees and what the "Annualised" toggle on the
        frontend displays.

      * ``overnight_cost`` — the upfront lump-sum investment per unit of
        capacity (`comp.overnight_cost`). Returned as-typed when the user
        set `overnight_cost`; back-calculated from `capital_cost ÷ annuity`
        otherwise. This is what "Total over lifetime" should multiply by
        Δcapacity to get the user's expected upfront build cost (e.g. a
        battery with 1000 €/MW × 71.3 MW Δ ⇒ €71.3 k, not the annuity-
        times-lifetime figure which mixes scaling assumptions and confused
        users in earlier iterations).

      * ``lifetime`` — years, kept for tooltips and CSV exports.

    Returns NaN-free numbers — non-finite intermediates fall back to
    sensible defaults (raw column / global config / 0).
    """
    import math

    out: dict[str, dict[str, dict[str, float]]] = {}
    with with_periodized_cost_defaults(n, cfg):
        reference_year = _reference_build_year(n)
        for comp_attr, comp_class in (
            ("generators", "Generator"),
            ("storage_units", "StorageUnit"),
            ("stores", "Store"),
            ("links", "Link"),
            ("lines", "Line"),
            ("transformers", "Transformer"),
        ):
            df = getattr(n, comp_attr, None)
            if df is None or df.empty:
                continue
            try:
                ann_series = n.c[comp_class].capital_cost
            except Exception:
                continue
            try:
                # comp.overnight_cost back-calculates upfront cost from
                # capital_cost / annuity / nyears for assets that didn't
                # type one in. Raises ValueError if discount_rate/lifetime
                # are still NaN despite the fill — fall back to per-asset
                # capital_cost in that case so we don't 500.
                upfront_series = n.c[comp_class].overnight_cost
            except Exception:
                upfront_series = None
            # PV factor per asset based on (build_year − reference_year).
            pv_series = _pv_factor_series(df, cfg, reference_year)
            mapping: dict[str, dict[str, float]] = {}
            raw_cc = df["capital_cost"] if "capital_cost" in df.columns else None
            raw_lt = df["lifetime"] if "lifetime" in df.columns else None
            raw_by = df["build_year"] if "build_year" in df.columns else None
            for name in df.index:
                # Annualised
                try:
                    v_ann = float(ann_series.loc[name])
                except (KeyError, TypeError, ValueError):
                    v_ann = float("nan")
                if math.isnan(v_ann) or math.isinf(v_ann):
                    v_ann = float(raw_cc.loc[name]) if raw_cc is not None and name in raw_cc.index else 0.0
                # Upfront (overnight). NaN if PyPSA couldn't back-calculate
                # AND the user didn't set overnight_cost — fall back to the
                # annualised number so the lifetime toggle still shows a
                # finite (if conservative) value.
                v_upf = float("nan")
                if upfront_series is not None and name in upfront_series.index:
                    try:
                        v_upf = float(upfront_series.loc[name])
                    except (TypeError, ValueError):
                        v_upf = float("nan")
                if math.isnan(v_upf) or math.isinf(v_upf):
                    v_upf = v_ann
                # Present value of the upfront cost. For year-0 builds the
                # factor is 1; for future-year builds it shrinks the nominal
                # spend by (1+r)^-(years out).
                try:
                    pv = float(pv_series.loc[name])
                except (KeyError, TypeError, ValueError):
                    pv = 1.0
                if math.isnan(pv) or math.isinf(pv) or pv <= 0:
                    pv = 1.0
                v_upf_pv = v_upf * pv
                # Lifetime — used for display only (tooltip/CSV), so still
                # report after the global-default fill.
                lt = (float(raw_lt.loc[name])
                      if raw_lt is not None and name in raw_lt.index else float("nan"))
                if math.isnan(lt) or math.isinf(lt) or lt <= 0:
                    lt = float(cfg.default_lifetime)
                # Build year — float so the JSON serialises cleanly even
                # if pandas hands us numpy.int64 / NaN. Drop NaN to None.
                if raw_by is not None and name in raw_by.index:
                    try:
                        by_val = float(raw_by.loc[name])
                        if math.isnan(by_val) or math.isinf(by_val):
                            by_val = None  # type: ignore[assignment]
                    except (TypeError, ValueError):
                        by_val = None  # type: ignore[assignment]
                else:
                    by_val = None  # type: ignore[assignment]
                entry: dict[str, float] = {
                    "capital_cost": v_ann,
                    "overnight_cost": v_upf,
                    "overnight_cost_pv": v_upf_pv,
                    "lifetime": lt,
                }
                if by_val is not None:
                    entry["build_year"] = by_val
                mapping[str(name)] = entry
            out[comp_attr] = mapping
    return out


def resolve_branch_outages(n, cfg: "SolverConfig") -> "list[tuple[str, str]]":
    """
    Build the (component, name) list to pass to PyPSA's
    `optimize_security_constrained(branch_outages=…)`.

    Rules — union of:
      • all lines if `sclopf_include_all_lines`
      • all transformers if `sclopf_include_all_transformers`
      • every line whose max(v_nom of its two buses) ≥
        `sclopf_voltage_threshold_kv` (skipped when threshold == 0)
      • every transformer where max(v_nom_0, v_nom_1) ≥ threshold
        (transformers expose v_nom via their connected buses, so we
        resolve it the same way as lines)
      • any names listed in `sclopf_extra_lines` / `sclopf_extra_transformers`

    Returned as a list of (component_class, asset_name) tuples — PyPSA's
    `branch_outages` accepts a list of strings (lines only) OR a
    `pd.MultiIndex` with (component, name) levels for mixed-class outages.
    The caller converts to MultiIndex when there are transformers in play.
    """
    seen: set[tuple[str, str]] = set()

    def _branch_v_nom_max(bus0: str, bus1: str) -> float:
        try:
            v0 = float(n.buses.at[bus0, "v_nom"]) if bus0 in n.buses.index else 0.0
            v1 = float(n.buses.at[bus1, "v_nom"]) if bus1 in n.buses.index else 0.0
            return max(v0, v1)
        except Exception:
            return 0.0

    thr = float(cfg.sclopf_voltage_threshold_kv or 0.0)

    # Lines
    if not n.lines.empty:
        names = set()
        if cfg.sclopf_include_all_lines:
            names.update(n.lines.index.astype(str))
        if thr > 0:
            for name in n.lines.index:
                if _branch_v_nom_max(str(n.lines.at[name, "bus0"]),
                                     str(n.lines.at[name, "bus1"])) >= thr:
                    names.add(str(name))
        for nm in cfg.sclopf_extra_lines or []:
            if str(nm) in n.lines.index:
                names.add(str(nm))
        for nm in names:
            seen.add(("Line", nm))

    # Transformers
    if not n.transformers.empty:
        names = set()
        if cfg.sclopf_include_all_transformers:
            names.update(n.transformers.index.astype(str))
        if thr > 0:
            for name in n.transformers.index:
                if _branch_v_nom_max(str(n.transformers.at[name, "bus0"]),
                                     str(n.transformers.at[name, "bus1"])) >= thr:
                    names.add(str(name))
        for nm in cfg.sclopf_extra_transformers or []:
            if str(nm) in n.transformers.index:
                names.add(str(nm))
        for nm in names:
            seen.add(("Transformer", nm))

    return sorted(seen)


def _compute_loss_atol(n) -> dict | bool:
    """
    Build the `transmission_losses` kwarg for n.optimize() with an `atol`
    auto-scaled to the network's smallest per-line full-flow loss.

    Why: PyPSA's secant loss approximation places its first breakpoint at
    `p_1 = 2·sqrt(atol / r_pu_eff)`. When atol (default 1 MW) is much larger
    than the line's full-flow loss `r_pu_eff · s_nom²`, p_1 overshoots s_nom
    by orders of magnitude, the loop exits with a single secant, and the
    resulting lower-bound slope (∝ sqrt(atol · r_pu_eff)) collides with the
    upper bound `r_pu_eff · s_nom²`. The LP then accepts only |flow| up to
    ~`r_pu_eff · s_nom² / slope = sqrt(r_pu_eff · s_nom⁴ / atol)` — visibly
    a constant tiny flow, regardless of dispatch. Surface: "I enabled losses
    and now bus1-bus2 carries a flat 3.75 MW even though Gas 1 is idle".

    Fix: pick atol = 1% of the smallest meaningful full-flow loss across all
    passive branches, capped at PyPSA's default (1 MW) so well-conditioned
    networks aren't slowed down. A floor of 1e-9 prevents pathological
    sub-microwatt values that just inflate constraint counts.

    Returns the dict PyPSA expects, or True if we couldn't measure a loss
    (no lossy branches, missing v_nom) — letting PyPSA fall back to its
    default behaviour.
    """
    losses = []
    # Lines: r_pu_eff = r / v_nom² with v_nom from bus0
    if not n.lines.empty:
        bus_vnom = n.buses["v_nom"] if "v_nom" in n.buses.columns else None
        for name in n.lines.index:
            r = n.lines.at[name, "r"]
            s_nom = n.lines.at[name, "s_nom"]
            bus0 = str(n.lines.at[name, "bus0"])
            if not (math.isfinite(r) and r > 0):
                continue
            if not (math.isfinite(s_nom) and s_nom > 0):
                continue
            v_nom = bus_vnom.get(bus0) if bus_vnom is not None else None
            if v_nom is None or not math.isfinite(v_nom) or v_nom <= 0:
                continue
            losses.append((r / v_nom**2) * s_nom**2)
    # Transformers: r_pu_eff = (r / s_nom) × tap_ratio
    if not n.transformers.empty:
        for name in n.transformers.index:
            r = n.transformers.at[name, "r"]
            s_nom = n.transformers.at[name, "s_nom"]
            tap = n.transformers.at[name, "tap_ratio"] \
                if "tap_ratio" in n.transformers.columns else 1.0
            if not (math.isfinite(r) and r > 0):
                continue
            if not (math.isfinite(s_nom) and s_nom > 0):
                continue
            if not math.isfinite(tap) or tap == 0:
                tap = 1.0
            losses.append((r * tap / s_nom) * s_nom**2)
    if not losses:
        return True
    min_loss = min(losses)
    atol = max(1e-9, min(1.0, min_loss * 0.01))
    return {"mode": "secants", "atol": atol}


_PRESOLVE_KEYS_BY_SOLVER = {
    # Each entry: (option_key, on_value, off_value). User-supplied values in
    # solver_options always win over the toggle so power users can pin specific
    # behaviour (e.g. "choose" for HiGHS auto-detection).
    "highs":   ("presolve", "on", "off"),
    "gurobi":  ("Presolve", 2, 0),
    "cplex":   ("preprocessing_presolve", "y", "n"),
    "copt":    ("Presolve", 1, 0),
    "scip":    ("presolving/maxrounds", -1, 0),
    "glpk":    ("presol", "on", "off"),
    "xpress":  ("PRESOLVE", 1, 0),
    "mosek":   ("MSK_IPAR_PRESOLVE_USE", "MSK_PRESOLVE_MODE_ON", "MSK_PRESOLVE_MODE_OFF"),
}


_MIP_KEYS_BY_SOLVER = {
    # (gap_key, time_limit_key). User values via the dispatcher are decimal
    # gap (0.01) and seconds (e.g. 3600). The dispatcher converts where the
    # solver expects different units.
    "highs":   ("mip_rel_gap",                "time_limit"),
    "gurobi":  ("MIPGap",                     "TimeLimit"),
    "cplex":   ("mip_tolerances_mipgap",      "timelimit"),
    "copt":    ("RelGap",                     "TimeLimit"),
    "scip":    ("limits/gap",                 "limits/time"),
    "glpk":    ("mipgap",                     "tmlim"),
    "xpress":  ("MIPRELSTOP",                 "MAXTIME"),
    "mosek":   ("MSK_DPAR_MIO_TOL_REL_GAP",   "MSK_DPAR_MIO_MAX_TIME"),
}


def _resolve_mip_kwargs(
    solver_name: str,
    mip_gap: float,
    mip_time_limit_s: float,
    user_options: dict | None,
) -> dict:
    """
    Map mip_gap / mip_time_limit_s to solver-specific option keys.

    Only injects keys that aren't already user-set, so power-user overrides
    in `solver_options` still win. `mip_time_limit_s <= 0` is treated as
    "no limit" — the key is not injected at all (don't set 0 directly, some
    solvers interpret that as instant timeout).
    """
    base = dict(user_options or {})
    entry = _MIP_KEYS_BY_SOLVER.get(solver_name.lower())
    if entry is None:
        return base
    gap_key, time_key = entry
    if gap_key not in base:
        base[gap_key] = float(mip_gap)
    if time_key not in base and mip_time_limit_s and mip_time_limit_s > 0:
        base[time_key] = float(mip_time_limit_s)
    return base


def _resolve_presolve_kwargs(solver_name: str, enabled: bool, user_options: dict | None) -> dict:
    """
    Map the user's presolve toggle to a solver-specific option key.

    Returns a new dict (does not mutate `user_options`). When the user already
    specified the key explicitly, that takes precedence — the toggle is just a
    convenience for the common case.
    """
    base = dict(user_options or {})
    entry = _PRESOLVE_KEYS_BY_SOLVER.get(solver_name.lower())
    if entry is None:
        return base
    key, on_val, off_val = entry
    if key not in base:
        base[key] = on_val if enabled else off_val
    return base


_DISPATCH_FIX_ACCESSORS = ("generators_t", "storage_units_t", "stores_t")


def _normalise_dynamic_indexes(n, phase=None) -> int:
    """
    Ensure every `_t` DataFrame's row index matches `n.snapshots`.

    Background: PyPSA's ``assign_duals`` (run after ``n.optimize``) writes
    flat-shaped dual DataFrames onto ``c.dynamic[attr]`` via
    ``.loc[df.index, df.columns] = df``. If a stale MultiIndex remains on
    any target frame (e.g. an empty ``mu_upper`` left over from a previous
    multi-period solve that wasn't fully reset when the user demoted back
    to flat snapshots), the assignment raises
    ``KeyError: DatetimeIndex(...) not in index`` and the whole solve is
    reported as failed even though HiGHS converged.

    This helper sweeps every component's ``_t`` store and any frame whose
    index doesn't match ``n.snapshots`` is reset:
      • empty frame → drop in a fresh empty DataFrame indexed by snapshots
      • stale MultiIndex on a flat network → trim to first-period slice
      • everything else → ``reindex(n.snapshots)`` (PyPSA handles
        DatetimeIndex → MultiIndex via level alignment for non-empty data)

    Belt-and-suspenders: it covers the case where the demotion path in
    ``set_investment_periods`` misses an attribute that was created later
    by PyPSA itself.
    """
    # Ensure n.snapshots has .name = "snapshot". When this is None, xarray
    # converts _t DataArrays with the unnamed dim labelled "dim_0", and
    # PyPSA's optimizer fails on .sel(snapshot=sns) with
    # `'snapshot' is not a valid dimension or coordinate ... ('dim_0': N, ...)`.
    # set_snapshots(MultiIndex) only sets .name when the new index has none
    # AND the previous one did — multi→multi rebuilds (POST /snapshots/multi_period)
    # therefore drop the name. Force it here so every solve starts clean.
    try:
        if getattr(n.snapshots, "name", None) != "snapshot":
            n.snapshots.name = "snapshot"
    except Exception:
        pass
    snap = n.snapshots
    fixed = 0
    try:
        all_components = list(n.all_components)
    except Exception:
        return 0
    for comp in all_components:
        try:
            dyn = n.c[comp].dynamic
        except Exception:
            continue
        for attr in list(dyn.keys()):
            df = dyn.get(attr)
            if df is None or not hasattr(df, "index"):
                continue
            # Also propagate the snapshot name onto every _t frame's row index —
            # set_snapshots only updates frames that were non-empty at the time
            # of the reshape; later-created empties otherwise carry .name=None
            # and xarray would still see dim_0 for them.
            try:
                if getattr(df.index, "name", None) != "snapshot" and not isinstance(df.index, pd.MultiIndex):
                    df.index.name = "snapshot"
                elif isinstance(df.index, pd.MultiIndex) and df.index.name != "snapshot":
                    df.index.name = "snapshot"
            except Exception:
                pass
            if df.index.equals(snap):
                continue
            # Mismatch — three cases.
            if df.empty:
                # Drop in a fresh empty frame on the right index.
                dyn[attr] = pd.DataFrame(index=snap, columns=df.columns)
                fixed += 1
                continue
            if isinstance(df.index, pd.MultiIndex) and not isinstance(snap, pd.MultiIndex):
                # Stale MultiIndex on a flat network — keep period-0's slice.
                first_p = df.index.get_level_values(0).unique()[0]
                mask = df.index.get_level_values(0) == first_p
                sub = df[mask].copy()
                sub.index = pd.DatetimeIndex(df.index[mask].get_level_values(1))
                if len(sub) == len(snap):
                    sub.index = snap
                    dyn[attr] = sub
                else:
                    try:
                        dyn[attr] = sub.reindex(snap)
                    except Exception:
                        dyn[attr] = pd.DataFrame(index=snap, columns=df.columns)
                fixed += 1
                continue
            # Otherwise rely on pandas reindex (handles flat→MultiIndex and
            # different-length flat→flat). Swallow any failure into a
            # fresh empty frame so the LP can still proceed.
            try:
                dyn[attr] = df.reindex(snap)
            except Exception:
                dyn[attr] = pd.DataFrame(index=snap, columns=df.columns)
            fixed += 1
    if fixed and phase is not None:
        phase(f"Normalised {fixed} stale dynamic index(es) before solve.")
    return fixed


def _clear_dispatch_fix(n, phase=None) -> None:
    """
    Drop any *_t.p_set columns persisted by a prior `_fix_dispatch_for_ac_pf`.

    Called at the top of every LOPF/SCLOPF/PF solve so a saved-then-reloaded
    network doesn't carry equality constraints that pin generator dispatch.
    Storage units have two side attributes (`p_dispatch_set` / `p_store_set`)
    that PyPSA also reads, so wipe them too.

    Implementation: replace the DataFrame with a 0-column DataFrame indexed by
    the current snapshots. Dropping rows alone (`iloc[0:0]`) leaves the columns
    in place and PyPSA still emits Generator-p_set constraints — confirmed
    against PyPSA 1.1.x where misaligned `p_set` rows pin dispatch even when
    the row index doesn't match `n.snapshots`.
    """
    cleared = []
    for accessor_name in _DISPATCH_FIX_ACCESSORS:
        accessor = getattr(n, accessor_name, None)
        if accessor is None:
            continue
        for attr in ("p_set", "p_dispatch_set", "p_store_set"):
            df = getattr(accessor, attr, None)
            if df is None or df.empty or len(df.columns) == 0:
                continue
            try:
                setattr(accessor, attr, pd.DataFrame(index=n.snapshots))
                cleared.append(f"{accessor_name}.{attr}")
            except Exception:
                pass
    if cleared and phase is not None:
        phase(f"Cleared stale dispatch-fix on {len(cleared)} accessor(s): {', '.join(cleared)}")


def _sanitise_transformer_types(n, phase) -> None:
    """
    Strip any `transformer.type` value that isn't a row in
    `n.transformer_types`. PyPSA crashes at solve time otherwise with
    `The type(s) X do(es) not exist in n.transformer_types`.

    The GUI's transformer presets ("380/220 kV" etc.) are UI labels — they
    don't register as real PyPSA transformer types. When the user picks one,
    the GUI also fills in explicit s_nom/x; PyPSA can use those directly when
    `type=""`. So clearing the type at solve time is safe — the explicit
    parameters take over.

    Sanitises in place. No undo: the bad value was a UI artefact and the
    sanitised state is what the user wanted PyPSA to see anyway.
    """
    if n.transformers.empty or "type" not in n.transformers.columns:
        return
    try:
        valid = set(n.transformer_types.index)
    except Exception:
        valid = set()
    types = n.transformers["type"].fillna("")
    bad_mask = (types != "") & ~types.isin(valid)
    if not bad_mask.any():
        return
    bad = sorted(set(types[bad_mask].tolist()))
    n.transformers.loc[bad_mask, "type"] = ""
    phase(
        f"Stripped unrecognised transformer type(s) {bad!r} from "
        f"{int(bad_mask.sum())} transformer(s). Falling back to explicit s_nom/x."
    )


def _apply_modelling_assumptions(n, cfg: "SolverConfig", phase):
    """
    Apply the five transient modelling knobs and return a `(restore, captured)`
    pair. Caller MUST invoke restore() in a finally block, regardless of
    solve outcome — otherwise the network keeps the LP transforms.

    `captured` is a dict the restore callbacks populate before reverting
    transforms — used to lift solve-time-only data (like VOLL slack
    dispatch) out of the soon-to-be-removed slack generators and into the
    caller's results store. Empty when no VOLL is configured or no slack
    actually got dispatched.

    Logs a one-line summary per knob via the `phase` callable so the user can
    see what was modified in the log stream.

    Implementation note: undo actions store the component-attribute *name*
    (e.g. "generators"), not the DataFrame reference. PyPSA's n.add() can
    rebuild the underlying DataFrame, invalidating any stored reference; we
    must re-resolve via getattr(n, attr) at restore time.
    """
    import numpy as np

    # Each undo entry is ("col", attr_name, col_name, idx, original_series)
    # or ("call", callable_to_run). Listed in apply-order; restore walks the
    # list in reverse.
    undo_actions: list = []
    # Populated by VOLL slack-removal callback (and any future capture step)
    # before reverting LP transforms, so the caller can salvage solve-only
    # data that wouldn't survive restoration.
    captured: dict = {}

    # Wipe the cross-iteration frozen-capacity side-store. Populated by
    # `_freeze_period_capacities` during the myopic loop and read by
    # `vintage_service._capture_and_drop_vintages` at restore. Stale
    # entries from a previous solve on THIS thread would otherwise
    # mis-attribute capacity to this solve's vintages. (Thread-local on
    # solver_service — see `_frozen_vintage_store` for the per-thread
    # isolation rationale.)
    _frozen_vintage_store().clear()

    # 1) Discount-rate / lifetime fill — delegated to the shared helper so
    #    /results/cost_breakdown can re-apply the same fill at report time
    #    (PyPSA's n.statistics() computes capital_cost via periodized_cost
    #    too, and the revert below would otherwise leave NaN behind).
    revert_periodized = fill_periodized_cost_defaults(n, cfg)
    undo_actions.append(("call", revert_periodized))

    # 2) CO2 price. Adds emissions × price / efficiency to fossil generators.
    #    We index by carrier so any generator on a non-zero-CO2 carrier gets
    #    the bump; efficiency=0 is a model error elsewhere — skip silently.
    #
    #    Two flavours:
    #      • Uniform `co2_price` (single float) — bump every emitter's scalar
    #        marginal_cost by emissions × price / efficiency.
    #      • Per-period `co2_price_per_period` (dict period→€/tCO2) — applies
    #        ONLY on multi-period networks. Sets generators_t.marginal_cost
    #        (time-varying) so each snapshot uses the period's price.
    #        Periods missing from the dict fall back to the scalar co2_price.
    has_per_period_price = (
        bool(getattr(cfg, "co2_price_per_period", None))
        and isinstance(n.snapshots, pd.MultiIndex)
    )
    if (cfg.co2_price > 0 or has_per_period_price) and not n.generators.empty and not n.carriers.empty:
        co2 = n.carriers["co2_emissions"] if "co2_emissions" in n.carriers.columns else pd.Series(dtype=float)
        co2 = co2[co2 > 0]
        if not co2.empty:
            gens = n.generators
            mask = gens["carrier"].isin(co2.index) & (gens["efficiency"] > 0)
            if mask.any():
                idx = gens.index[mask]
                if has_per_period_price:
                    # Build per-snapshot price series keyed by period.
                    raw_dict = cfg.co2_price_per_period or {}
                    period_price: dict[int, float] = {}
                    for k, v in raw_dict.items():
                        try:
                            period_price[int(k)] = float(v)
                        except (TypeError, ValueError):
                            continue
                    period_lvl = n.snapshots.get_level_values(0)
                    price_series = pd.Series(
                        [period_price.get(int(p), cfg.co2_price) for p in period_lvl],
                        index=n.snapshots, dtype=float,
                    )
                    # Build the time-varying surcharge per emitting generator:
                    # surcharge[g, t] = price_series[t] × co2[carrier(g)] / efficiency[g]
                    intensity_per_gen = gens.loc[idx, "carrier"].map(co2)
                    eff_per_gen = gens.loc[idx, "efficiency"]
                    base_mc = gens.loc[idx, "marginal_cost"].copy()
                    # outer = snapshot × generator; broadcast price (T,) × intensity (G,)
                    surcharge = pd.DataFrame(
                        {g: price_series * float(intensity_per_gen.loc[g]) / float(eff_per_gen.loc[g])
                         for g in idx},
                        index=n.snapshots,
                    )
                    # Time-varying marginal_cost = scalar base + per-snapshot surcharge.
                    # PyPSA picks up generators_t.marginal_cost when set.
                    mc_t = n.generators_t.marginal_cost
                    # Capture the pre-existing time-varying columns we're
                    # about to overwrite so restore() can put them back.
                    existing_cols = [g for g in idx if g in mc_t.columns]
                    saved_t_mc = mc_t[existing_cols].copy() if existing_cols else None
                    new_cols = [g for g in idx if g not in mc_t.columns]
                    for g in idx:
                        n.generators_t.marginal_cost[g] = float(base_mc.loc[g]) + surcharge[g]
                    undo_actions.append(("t_marginal_cost", saved_t_mc, list(new_cols)))
                    by_period_msg = ", ".join(
                        f"{p}: {period_price.get(p, cfg.co2_price):.1f}"
                        for p in sorted(set(int(x) for x in period_lvl))
                    )
                    phase(
                        f"Applied per-period CO2 price ({by_period_msg} EUR/tCO2) to "
                        f"{len(idx)} fossil generator(s) via time-varying marginal_cost."
                    )
                else:
                    # Single-period network OR no per-period dict: uniform price path.
                    add_per_mwh = gens.loc[idx, "carrier"].map(co2) * cfg.co2_price / gens.loc[idx, "efficiency"]
                    original_mc = gens.loc[idx, "marginal_cost"].copy()
                    gens.loc[idx, "marginal_cost"] = original_mc + add_per_mwh
                    undo_actions.append(("col", "generators", "marginal_cost", idx, original_mc))
                    phase(
                        f"Applied CO2 price {cfg.co2_price:.1f} EUR/tCO2 to {len(idx)} "
                        f"fossil generator(s) (sum surcharge {add_per_mwh.sum():.2f} EUR/MWh)."
                    )
    elif getattr(cfg, "co2_price_per_period", None) and not isinstance(n.snapshots, pd.MultiIndex):
        # User set per-period prices but the network is flat — log so the
        # silent no-op doesn't go unnoticed.
        phase(
            "co2_price_per_period is set but the network is single-period; "
            "ignoring the per-period dict. Enable multi-period planning to apply per-year prices."
        )

    # 3) VOLL — RELOCATED to after step 5 (load scaling) so the slack
    #    p_nom sizing sees the SCALED p_set max, not the pre-scaling
    #    value. Previously sized here, a load-growth scaler of 2× could
    #    leave slack p_nom undersized for the high-growth period, and the
    #    LP would silently shed less load than VOLL was supposed to allow.
    #    See block "5b) VOLL (post-scaling sizing)" below.

    # 4) Investment periods. Only meaningful when multi_investment_periods is
    #    also true — otherwise the flag is ignored by PyPSA. Snapshot the
    #    original periods (could be empty) and restore on exit.
    if cfg.multi_investment_periods and cfg.investment_periods:
        try:
            original_periods = list(n.investment_periods)
            # Capture the pre-promotion snapshot shape so the restore can
            # actually demote multi→flat when the user started with flat
            # snapshots + cfg-only periods. Without this, every cfg-only
            # run silently leaves the network MultiIndexed on disk.
            was_flat = not isinstance(n.snapshots, pd.MultiIndex)
            periods = sorted(int(p) for p in cfg.investment_periods)
            n.set_investment_periods(periods=periods)
            phase(f"Configured {len(periods)} investment period(s): {periods}.")
            def _restore_periods(orig=original_periods, started_flat=was_flat):
                if orig:
                    n.set_investment_periods(periods=orig)
                elif started_flat:
                    # User had flat snapshots; cfg-only set the periods.
                    # Demote back to flat so the on-disk save doesn't
                    # carry transient cfg-only periods the user never
                    # set on the network surface. Lazy import avoids
                    # a circular dependency between solver_service and
                    # routers.network.
                    try:
                        from routers.network import _flatten_snapshot_state
                        _flatten_snapshot_state(n)
                        phase(
                            "Reverted snapshots to flat — cfg.investment_periods "
                            "promoted them transiently for the solve only."
                        )
                    except Exception as exc:
                        phase(
                            f"Couldn't auto-revert snapshots to flat ({exc}). "
                            "Reload the project to fully reset, or accept the "
                            "MultiIndex shape on disk."
                        )
            undo_actions.append(("call", _restore_periods))
        except Exception as exc:
            phase(f"Investment periods setup failed: {exc}. Continuing with single-period.")

    # 4b) Auto period discount via investment_period_weightings.objective.
    #     Computes PV factors (1+r)^-(P-ref_year) per period and writes them
    #     into ipw.objective so PyPSA's LP discounts BOTH capex and opex by
    #     a uniform social rate. Without this, a 3-period model treats every
    #     period as equally weighted — so the LP front-loads all CAPEX into
    #     the first period (cheaper amortisation, identical OPEX savings).
    #     Reference year = first period in the active list. Multi-period only.
    if (
        cfg.auto_discount_periods
        and cfg.multi_investment_periods
        and isinstance(n.snapshots, pd.MultiIndex)
        and len(n.investment_periods) > 0
    ):
        try:
            periods_active = sorted(int(p) for p in n.investment_periods)
            ref_year = periods_active[0]
            r_nom = float(cfg.discount_rate or 0.0)
            infl = float(getattr(cfg, "inflation_rate", 0.0) or 0.0)
            # Real discount rate used in the cross-period PV factor. When the
            # user enters a NOMINAL discount (e.g. WACC = 7 %) and a separate
            # inflation rate (e.g. 2 %), the real discount that matters for
            # discounting REAL-€ LP costs is roughly nominal − inflation.
            # Use the exact Fisher relation so the formula is right even at
            # higher inflations: real_r = (1 + nominal) / (1 + inflation) − 1.
            # Guard against pathological inflation > nominal (real_r would
            # go negative — mathematically valid but rarely intended): clamp
            # at -0.999 so (1 + r) stays positive in the PV exponentiation.
            if 1.0 + infl > 0:
                r = (1.0 + r_nom) / (1.0 + infl) - 1.0
            else:
                r = r_nom
            if r <= -0.999:
                r = -0.999
            ipw = n.investment_period_weightings
            original_obj = ipw["objective"].copy()
            new_factors: dict[int, float] = {}
            for p in periods_active:
                # Per-period span (years_in_period). PyPSA defaults to gap-to-next
                # so multi-year periods get correctly weighted. PV applied at
                # period start; the within-period sum is approximated by
                # PV(start) × years (sufficient for r ≪ 1 and short periods —
                # exact would compute the geometric series).
                yrs = float(ipw.at[p, "years"]) if "years" in ipw.columns else 1.0
                pv = (1.0 + r) ** -(p - ref_year)
                new_factors[p] = pv * yrs
                ipw.at[p, "objective"] = new_factors[p]
            infl_note = f", inflation_rate={infl:.3f}, real={r:.3f}" if infl != 0.0 else ""
            phase(
                f"Auto-discount: ipw.objective set to PV × years for {len(periods_active)} "
                f"period(s) at discount_rate={r_nom:.3f}{infl_note}: " +
                ", ".join(f"{p}:{new_factors[p]:.3f}" for p in periods_active)
            )
            def _restore_ipw_objective(orig=original_obj):
                for p in orig.index:
                    n.investment_period_weightings.at[p, "objective"] = float(orig.at[p])
            undo_actions.append(("call", _restore_ipw_objective))
        except Exception as exc:
            phase(f"Auto-discount setup failed: {exc}. Periods stay at original weights.")

    # 5) Per-period × per-carrier load scaling. For each load column, look up
    #    its (canonical carrier, period) and multiply by the resolved factor.
    #    Resolution priority:
    #      1. cfg.load_scalers_by_carrier[carrier][period_str] — per-carrier
    #         (e.g. electrical 2027 × 1.10, hydrogen 2027 × 1.50)
    #      2. cfg.load_scalers[period_str] — legacy global fallback
    #      3. 1.0 — identity
    #    Multi-period only; ignored for flat networks. The whole p_set frame
    #    is snapshotted and restored wholesale.
    by_carrier_cfg = getattr(cfg, "load_scalers_by_carrier", {}) or {}
    if (
        cfg.multi_investment_periods
        and (cfg.load_scalers or by_carrier_cfg)
        and isinstance(n.snapshots, pd.MultiIndex)
        and not n.loads_t.p_set.empty
    ):
        p_set = n.loads_t.p_set
        period_level = p_set.index.get_level_values(0)
        # Build a per-column carrier-key map up front. We canonicalise to
        # the same alias set the frontend uses (see loadCarrierKey in
        # Dispatch.tsx) so 'AC' / 'electricity' / '' all map to 'electrical'.
        carrier_by_col: dict[str, str] = {}
        if "carrier" in n.loads.columns:
            for col in p_set.columns:
                if col in n.loads.index:
                    carrier_by_col[col] = _canonical_load_carrier_key(n.loads.at[col, "carrier"])
                else:
                    carrier_by_col[col] = "unspecified"
        else:
            for col in p_set.columns:
                carrier_by_col[col] = "unspecified"

        applied: list[str] = []
        original_p_set = None
        for period in sorted(set(period_level)):
            mask = period_level == period
            for col in p_set.columns:
                carrier_key = carrier_by_col.get(col, "unspecified")
                factor: float | None = None
                # 1) per-carrier per-period
                car_block = by_carrier_cfg.get(carrier_key)
                if isinstance(car_block, dict):
                    raw = car_block.get(str(period))
                    if raw is not None:
                        try:
                            f = float(raw)
                            if math.isfinite(f):
                                factor = f
                        except (TypeError, ValueError):
                            pass
                # 2) legacy global (applied to ALL carriers if no per-carrier override)
                if factor is None and cfg.load_scalers:
                    raw = cfg.load_scalers.get(str(period))
                    if raw is not None:
                        try:
                            f = float(raw)
                            if math.isfinite(f):
                                factor = f
                        except (TypeError, ValueError):
                            pass
                # 3) identity
                if factor is None or factor == 1.0:
                    continue
                if original_p_set is None:
                    original_p_set = p_set.copy(deep=True)
                p_set.loc[mask, col] = p_set.loc[mask, col] * factor
                applied.append(f"{period}/{carrier_key}/{col}×{factor:g}")
        if original_p_set is not None:
            def _restore_p_set(orig=original_p_set):
                n.loads_t["p_set"] = orig
            undo_actions.append(("call", _restore_p_set))
            # Summarise — listing every (period, carrier, col) explosion in the
            # log would be noisy on networks with many loads; collapse to
            # unique (period, carrier) factor combos.
            unique_combos = sorted(set(
                f"{a.split('/')[0]}/{a.split('/')[1]}×{a.split('×')[-1]}"
                for a in applied
            ))
            phase(f"Applied per-carrier load scaling: {', '.join(unique_combos)}.")

    # 5b) VOLL (post-scaling sizing). Relocated from step 3 so the slack
    #    `p_nom` sees the loads AFTER step 5's per-period × per-carrier
    #    scaling has been applied. Previously sized at step 3 against
    #    the pre-scaling p_set, a 2× load-growth scaler in period N could
    #    leave the slack undersized and the LP would silently shed less
    #    load than the configured VOLL was supposed to permit (slack hits
    #    its p_nom cap before the demand is matched, producing a
    #    primal-infeasible window that PyPSA simply reports as zero VOLL
    #    dispatch). Sized at 10× the observed max so the slack always has
    #    headroom — bumping further would slow the solver without benefit.
    if cfg.voll > 0 and not n.buses.empty:
        added = []
        # Choose a slack p_nom that comfortably exceeds the worst-case load.
        try:
            max_load = float(n.loads_t.p_set.max().max()) if not n.loads_t.p_set.empty else 0.0
        except Exception:
            max_load = 0.0
        if max_load <= 0 and not n.loads.empty:
            max_load = float(n.loads["p_set"].max()) if "p_set" in n.loads.columns else 0.0
        slack_pnom = max(max_load, 1.0) * 10.0
        # ONLY add VOLL slack at buses that actually carry a load. Transit /
        # source buses (waste-heat collection, gas trunk, hydrogen network
        # backbone) have no demand to "fail to meet" — adding a slack there
        # lets the LP create energy from nothing at VOLL cost, which on a
        # sector-coupled network the optimiser exploits: e.g. a heat-pump
        # bus2 drawing from a low-T heat collector with insufficient waste
        # heat is filled by the slack instead of curtailing high-T demand,
        # producing physically impossible balances ("creating" 35 MW of
        # waste heat at €100k/MWh to enable €3M of avoided high-T VOLL).
        # If the user genuinely wants slack on a transit bus they can add
        # a Generator manually.
        load_bus_set = set(n.loads["bus"].astype(str)) if "bus" in n.loads.columns else set()
        skipped_transit = 0
        for bus in n.buses.index:
            if str(bus) not in load_bus_set:
                skipped_transit += 1
                continue
            name = f"{VOLL_SLACK_PREFIX}{bus}"
            if name in n.generators.index:
                continue  # don't double-add if a previous run leaked
            # Mark BEFORE n.add so a GET landing during the add window
            # still hides the row (filtering an absent name is a no-op).
            # On n.add failure we unmark to keep the registry consistent.
            PyPSAService.mark_transient("Generator", name)
            try:
                n.add(
                    "Generator", name,
                    bus=bus,
                    p_nom=slack_pnom,
                    marginal_cost=cfg.voll,
                    # The convention's owner is services/adequacy/slack.py.
                    carrier=INVOLUNTARY_SLACK_CARRIER,
                )
            except Exception:
                PyPSAService.unmark_transient("Generator", name)
                raise
            added.append(name)
        if added:
            phase(
                f"Added {len(added)} VOLL slack generator(s) at {cfg.voll:.0f} EUR/MWh "
                f"(sized to {slack_pnom:.0f} MW = 10× scaled-max load)."
                + (f" Skipped {skipped_transit} transit bus(es) without loads." if skipped_transit else "")
            )
            # Restore = remove the slacks.
            def _capture_and_remove_slacks(names=added, voll=cfg.voll):
                # Capture lost-load dispatch BEFORE removing the slack
                # generators — once they're removed, n.generators_t.p
                # forgets them and the user can never see their dispatch.
                try:
                    df = n.generators_t.p
                    if df is not None and not df.empty:
                        live = [nm for nm in names if nm in df.columns]
                        if live:
                            sub = df[live].copy()
                            # Strip the slack prefix so the bus name stands
                            # alone in the result payload — friendlier to plot.
                            sub.columns = [strip_slack_prefix(c) for c in sub.columns]
                            # Aggregate stats for the KPI tiles. SNAPSHOT-
                            # WEIGHTED (canonical, spec §6.3): the frame stays
                            # unweighted MW, but the totals integrate over the
                            # snapshot weights — "generators" for energy,
                            # "objective" for cost, matching the solve-log
                            # decomposition and the LP objective. The previous
                            # unweighted sum ("assumes hourly snapshots")
                            # under-reported by the weight factor on
                            # representative-snapshot (tsam) runs.
                            from services.adequacy.metrics import (
                                lost_load_totals,
                            )
                            w_energy = _period_utils.snapshot_weights(
                                n, "generators", sns=sub.index)
                            totals = lost_load_totals(
                                sub,
                                energy_weights=w_energy,
                                cost_weights=_period_utils.snapshot_weights(
                                    n, "objective", sns=sub.index),
                                voll=float(voll),
                            )
                            captured["lost_load_t"] = sub
                            captured["lost_load_total_mwh"] = totals["total_mwh"]
                            captured["lost_load_cost_eur"] = totals["cost_eur"]
                            # Solve-time achieved values for the adequacy
                            # report: per-bus-per-period weighted MWh, and
                            # the electrical shed-hours (spec §5.1).
                            from services.adequacy.metrics import (
                                electrical_columns,
                                shed_hours,
                            )
                            bus_e = sub.clip(lower=0).mul(
                                w_energy.reindex(sub.index).fillna(0.0), axis=0)
                            if isinstance(sub.index, pd.MultiIndex):
                                bp = bus_e.groupby(
                                    sub.index.get_level_values(0)).sum()
                            else:
                                bp = pd.DataFrame(
                                    [bus_e.sum()], index=["ALL"])
                            captured["lost_load_bus_period_mwh"] = bp
                            captured["shed_hours_electrical"] = shed_hours(
                                sub[electrical_columns(n, list(sub.columns))],
                                weights=w_energy,
                            )
                            # Explicit — consumers must not re-derive VoLL
                            # from cost/energy, which skews whenever the two
                            # weight columns differ.
                            captured["voll_eur_per_mwh"] = float(voll)
                except Exception:
                    pass
                # Now remove the slack generators so they don't pollute
                # the post-solve network state. Pair every remove with an
                # unmark_transient so the registry doesn't keep filtering
                # the name once the row is gone — important if the user
                # later creates a real generator that happens to reuse
                # the bus name.
                for nm in names:
                    if nm in n.generators.index:
                        n.remove("Generator", nm)
                    PyPSAService.unmark_transient("Generator", nm)
            undo_actions.append(("call", _capture_and_remove_slacks))

    # 5c) Demand-response tier (spec §4.4): voluntary, volume-capped, OPT-IN
    #    per bus. Independent of VOLL (a resource, not a failure valve).
    #    Never silently global — an empty opt-in list keeps the tier off
    #    (preflight warns). Same transient lifecycle as the VOLL slacks:
    #    mark → add → capture (into its OWN keys) → remove.
    dsr_price = float(getattr(cfg, "dsr_price_eur_per_mwh", 0.0) or 0.0)
    dsr_share = float(getattr(cfg, "dsr_share_of_load", 0.0) or 0.0)
    dsr_buses = [str(b) for b in (getattr(cfg, "dsr_buses", None) or [])]
    if dsr_price > 0 and dsr_share > 0 and dsr_buses and not n.loads.empty:
        dsr_added = []
        loads_by_bus = n.loads.groupby("bus") if "bus" in n.loads.columns else None
        p_set_t = getattr(n.loads_t, "p_set", None)
        for bus in dsr_buses:
            if bus not in n.buses.index or loads_by_bus is None:
                continue
            try:
                bus_loads = list(loads_by_bus.get_group(bus).index)
            except KeyError:
                continue  # opt-in bus without a load — nothing to respond with
            # Peak load at the bus: time-varying columns override statics.
            peak = 0.0
            per_snap = None
            for l in bus_loads:
                if p_set_t is not None and l in getattr(p_set_t, "columns", []):
                    series = p_set_t[l]
                else:
                    try:
                        series = float(n.loads.at[l, "p_set"] or 0.0)
                    except (TypeError, ValueError):
                        series = 0.0
                per_snap = series if per_snap is None else per_snap + series
            try:
                peak = float(per_snap.max()) if hasattr(per_snap, "max") else float(per_snap or 0.0)
            except (TypeError, ValueError):
                peak = 0.0
            if peak <= 0:
                continue
            name = f"{DSR_SLACK_PREFIX}{bus}"
            if name in n.generators.index:
                continue
            PyPSAService.mark_transient("Generator", name)
            try:
                n.add(
                    "Generator", name,
                    bus=bus,
                    p_nom=dsr_share * peak,
                    marginal_cost=dsr_price,
                    carrier=DSR_SLACK_CARRIER,
                )
            except Exception:
                PyPSAService.unmark_transient("Generator", name)
                raise
            dsr_added.append(name)
        if dsr_added:
            phase(
                f"Added {len(dsr_added)} demand-response slack(s) at "
                f"{dsr_price:.0f} EUR/MWh, volume {dsr_share:.0%} of each "
                f"bus's peak load (opt-in tier — NOT counted as unserved "
                f"energy)."
            )

            def _capture_and_remove_dsr(names=dsr_added):
                try:
                    df = n.generators_t.p
                    if df is not None and not df.empty:
                        live = [nm for nm in names if nm in df.columns]
                        if live:
                            sub = df[live].copy()
                            sub.columns = [
                                c.removeprefix(DSR_SLACK_PREFIX) for c in sub.columns
                            ]
                            w_energy = _period_utils.snapshot_weights(
                                n, "generators", sns=sub.index)
                            captured["dsr_t"] = sub
                            captured["dsr_total_mwh"] = float(
                                sub.clip(lower=0)
                                .mul(w_energy.reindex(sub.index).fillna(0.0), axis=0)
                                .to_numpy().sum()
                            )
                except Exception:
                    pass
                for nm in names:
                    if nm in n.generators.index:
                        n.remove("Generator", nm)
                    PyPSAService.unmark_transient("Generator", nm)

            undo_actions.append(("call", _capture_and_remove_dsr))
    elif dsr_price > 0 and not dsr_buses:
        phase(
            "Demand-response price is set but no buses are opted in — the "
            "tier stays OFF (it is never applied globally; see preflight)."
        )

    # 6) Multi-period activity guard. In a multi-period run PyPSA only lets an
    #    asset dispatch in period p when build_year <= p < build_year + lifetime.
    #    The GUI leaves build_year at its 0 default, and step 1 above may have
    #    filled lifetime to cfg.default_lifetime (e.g. 25) for overnight_cost
    #    assets — so an asset's active window can be [0, 25), which excludes
    #    investment periods like 2026 / 2027 entirely. PyPSA then can't dispatch
    #    it: the LP sheds load and "curtails" renewables that simply can't run.
    #    Rebase build_year to the first investment period for any asset that
    #    would otherwise be inactive in EVERY period, and stretch a too-short
    #    lifetime to cover the horizon. Transient — reverted in restore().
    if cfg.multi_investment_periods and isinstance(n.snapshots, pd.MultiIndex):
        try:
            mp_periods = sorted(int(p) for p in n.investment_periods)
        except (TypeError, ValueError):
            mp_periods = []
        if mp_periods:
            first_p, last_p = mp_periods[0], mp_periods[-1]
            rebased: list[str] = []
            for comp_attr in ("generators", "storage_units", "stores",
                              "links", "lines", "transformers"):
                df = getattr(n, comp_attr, None)
                if df is None or df.empty or "build_year" not in df.columns:
                    continue
                lifetimes = df["lifetime"] if "lifetime" in df.columns else None
                # COLLECT-THEN-APPLY: walk df.index once into a local snapshot
                # list, then mutate the frame in a second pass. Pandas tolerates
                # `df.at[name, col] = …` during iteration on `df.index`, but a
                # collected snapshot is more defensive (e.g. against any future
                # refactor that ends up calling n.add() / n.remove() inside the
                # loop, which would invalidate the index iterator).
                #
                # `_safe_isfinite` defends against object-dtype columns that
                # hold non-numeric tokens. Bare `np.isfinite(None)` raises
                # TypeError; with mixed-dtype columns (rare but possible after
                # CSV import of e.g. "auto") `np.isfinite("auto")` also raises.
                # Wrapping in try/except + coercing to float first reduces the
                # surface to the failure modes we care about (None / strings →
                # treated as PyPSA defaults).
                def _safe_isfinite(v) -> tuple[bool, float]:
                    """
                    Return (is_finite, float_value). Falls back to (False, 0)
                    for None / non-numeric / NaN / Inf.
                    """
                    if v is None:
                        return False, 0.0
                    try:
                        f = float(v)
                    except (TypeError, ValueError):
                        return False, 0.0
                    if not np.isfinite(f):
                        return False, 0.0
                    return True, f

                pending: list[tuple[str, float, object, float | None, object]] = []
                # tuple shape: (name, by, raw_by_for_undo, lt_for_resize_or_None, raw_lt_for_undo)
                for name in df.index:
                    raw_by = df.at[name, "build_year"]
                    finite_by, by = _safe_isfinite(raw_by)
                    if not finite_by:
                        by = 0.0
                    if lifetimes is not None:
                        raw_lt = lifetimes.at[name]
                        finite_lt, lt = _safe_isfinite(raw_lt)
                        if not finite_lt:
                            lt = float("inf")
                    else:
                        raw_lt = None
                        lt = float("inf")
                    if any(by <= p < by + lt for p in mp_periods):
                        continue  # already active in at least one period
                    # Stretch lifetime only when finite AND too short to cover
                    # the horizon from the new build_year.
                    resize_lt: float | None = None
                    if lifetimes is not None and np.isfinite(lt) and first_p + lt <= last_p:
                        resize_lt = float(last_p - first_p + 1)
                    pending.append((name, by, raw_by, resize_lt, raw_lt))

                # APPLY pass — now safe even if the loop body needed to read
                # un-rebased neighbours (none do today, but future-proof).
                for name, _by, raw_by, resize_lt, raw_lt in pending:
                    df.at[name, "build_year"] = first_p
                    undo_actions.append((
                        "col", comp_attr, "build_year",
                        pd.Index([name]), pd.Series({name: raw_by}),
                    ))
                    if resize_lt is not None:
                        df.at[name, "lifetime"] = resize_lt
                        undo_actions.append((
                            "col", comp_attr, "lifetime",
                            pd.Index([name]), pd.Series({name: raw_lt}),
                        ))
                    rebased.append(f"{comp_attr[:-1]} '{name}'")
            if rebased:
                preview = ", ".join(rebased[:4]) + ("…" if len(rebased) > 4 else "")
                phase(
                    f"Multi-period activity guard: rebased build_year -> {first_p} "
                    f"for {len(rebased)} asset(s) inactive in every investment "
                    f"period ({preview}). Set a build_year on these assets to "
                    f"control which period they're built in."
                )

    # 7) Per-period capacity bounds (vintage expansion). For each asset that
    #    the user assigned `p_nom_min` / `p_nom_max` per investment period via
    #    `n.meta["vintage_bounds"]`, replace the single extendable parent row
    #    with one vintage row per period — each carrying its own build_year
    #    and the bounds for that period. The parent's `*_extendable` flag is
    #    flipped off so the optimiser sizes ONLY the vintages. All transforms
    #    revert in restore(). No-op when not multi-period.
    if cfg.multi_investment_periods and isinstance(n.snapshots, pd.MultiIndex):
        try:
            apply_vintage_bounds(n, undo_actions, phase)
        except Exception as exc:
            phase(f"Vintage expansion failed: {exc}. Continuing without per-period bounds.")

    def restore():
        # Walk in reverse order. "col" actions write back original column
        # values; "call" actions run arbitrary cleanup (slack-gen removal,
        # period restoration, vintage drop). DataFrame is re-resolved at
        # restore time because PyPSA's n.add() may have rebuilt it during
        # apply.
        #
        # Each action is wrapped in try/except so one bad row doesn't strand
        # subsequent ones — without this, a failure in (say) the vintage
        # capture-and-drop callback would leave the parent's extendable
        # flip un-reverted and orphan rows in the network. We log to phase
        # but swallow per-entry so the rest of the restore proceeds.
        for action in reversed(undo_actions):
            kind = action[0]
            try:
                if kind == "col":
                    _, attr_name, col, idx, original = action
                    df = getattr(n, attr_name, None)
                    if df is None:
                        continue
                    # Filter index to rows that still exist — a previous undo
                    # action (e.g. slack removal) might have shrunk the frame.
                    valid = [i for i in idx if i in df.index]
                    if valid:
                        df.loc[valid, col] = original.loc[valid]
                elif kind == "call":
                    action[1]()
                elif kind == "t_marginal_cost":
                    # Restore generators_t.marginal_cost — drop columns we
                    # newly added, restore originals for columns we overwrote.
                    _, saved_t_mc, new_cols = action
                    mc_t = n.generators_t.marginal_cost
                    if new_cols:
                        keep_cols = [c for c in mc_t.columns if c not in new_cols]
                        n.generators_t.marginal_cost = mc_t[keep_cols]
                        mc_t = n.generators_t.marginal_cost
                    if saved_t_mc is not None and not saved_t_mc.empty:
                        for g in saved_t_mc.columns:
                            n.generators_t.marginal_cost[g] = saved_t_mc[g]
            except Exception as exc:
                # Surface via the phase callback so the log captures it, then
                # keep restoring. Don't re-raise.
                try:
                    phase(f"Restore: skipped one entry ({type(exc).__name__}: {exc})")
                except Exception:
                    pass
        # Safety net: drop any remaining transient-row marks. The per-row
        # unmark calls inside _capture_and_remove_slacks and
        # _capture_and_drop_vintages cover the happy path, but if a
        # restore step errored out before reaching them the registry
        # would keep filtering rows that no longer exist (or worse, real
        # rows that reuse a name later). Clearing here guarantees the
        # GET filter is a no-op on the post-restore network.
        try:
            PyPSAService.clear_transient()
        except Exception:
            pass
        # Drop the freeze-time vintage-capacity side-store after all
        # capture closures have read from it. Thread-local, so the explicit
        # clear() at end-of-restore prevents any chance of leakage into the
        # next solve cycle on this same worker thread.
        try:
            _frozen_vintage_store().clear()
        except Exception:
            pass

    return restore, captured


# ── Myopic foresight driver ───────────────────────────────────────────────────
# Each investment period is solved sequentially; capacities decided in one
# period are frozen (extendable=False, p_nom = p_nom_opt) before the next
# iteration. Two flavours, both routed through _run_myopic_foresight:
#   • Phase 1: pure myopic — each iteration sees ONLY its period's snapshots.
#     Zero forward visibility (cfg.lf_aggregate_future == False).
#   • Phase 2: limited foresight — each iteration sees its period at full
#     hourly detail PLUS representative-week snapshots for every future
#     period, clustered via tsam. The future-period snapshots carry weight
#     overrides on n.snapshot_weightings so the LP costs scale to "this
#     representative stands in for N actual weeks". Opt-in via
#     cfg.lf_aggregate_future == True.
# Both flavours share rolling-loop, freeze-after-solve, vintage-results
# capture, and undo bookkeeping.

# (component_class, attr_name, capacity_field) triples for the six PyPSA
# component classes that carry a nominal capacity decision. Stored once at
# module level so freeze + downstream helpers stay consistent.
_NOM_TRIPLES = [
    ("Generator",    "generators",    "p_nom"),
    ("StorageUnit",  "storage_units", "p_nom"),
    ("Store",        "stores",        "e_nom"),
    ("Link",         "links",         "p_nom"),
    ("Line",         "lines",         "s_nom"),
    ("Transformer",  "transformers",  "s_nom"),
]


def _build_iteration_snapshots(n, current_period, all_periods, cfg):
    """
    Return ``(snapshots, weight_overrides)`` for one myopic iteration.

    ``snapshots`` is a slice of ``n.snapshots`` PyPSA's ``n.optimize()``
    accepts directly via the ``snapshots=`` kwarg.

    ``weight_overrides`` is a `pd.Series` keyed by snapshot of multipliers
    to write into ``n.snapshot_weightings`` for the duration of this LP. The
    rolling driver records originals into ``undo_actions`` and reverts after
    the solve. ``None`` when no overrides are needed (Phase 1 path).

    Phase 1 — pure myopic, ``cfg.lf_aggregate_future == False``:
        Only ``current_period``'s snapshots; no weight overrides.

    Phase 2 — limited foresight, ``cfg.lf_aggregate_future == True``:
        ``current_period`` at full detail **+** representative-week /
        typical-day snapshots for every period > current_period (clustered
        via ``services/time_aggregation_service``). Each representative
        snapshot's weight is scaled by its cluster size so the LP's
        objective / energy-balance terms see the future-period costs at the
        right magnitude.
    """
    if not isinstance(n.snapshots, pd.MultiIndex):
        # Single-period (flat) snapshots — myopic degenerates to a single
        # solve over the whole index. The validation layer should have
        # caught this and refused the strategy, but be defensive.
        return n.snapshots, None
    period_level = n.snapshots.get_level_values(0)
    current_mask = period_level == current_period
    # `n.snapshots[mask]` preserves the MultiIndex with both levels intact —
    # critical because PyPSA's multi-period LP keys investment_period
    # weightings by the level-0 value of each snapshot.
    current_slice = n.snapshots[current_mask]

    if not bool(getattr(cfg, "lf_aggregate_future", False)):
        return current_slice, None

    # Phase 2: append representative snapshots from each future period,
    # accumulating the weight overrides as we go. tsam clustering is cached
    # per-(period, cfg-fingerprint) so subsequent rolling iterations don't
    # re-run the same clustering work.
    from services.time_aggregation_service import aggregate_period_snapshots
    future_periods = [p for p in all_periods if p > current_period]
    if not future_periods:
        return current_slice, None

    combined = current_slice
    overrides = pd.Series(dtype=float)
    for fp in future_periods:
        agg = aggregate_period_snapshots(n, fp, cfg)
        if len(agg.snapshots) == 0:
            continue
        combined = combined.append(agg.snapshots)
        overrides = pd.concat([overrides, agg.weights])
    # Drop any accidental duplicates (shouldn't happen — periods are
    # disjoint by construction — but be defensive against odd MultiIndex
    # quirks in pandas).
    if combined.has_duplicates:
        combined = pd.MultiIndex.from_tuples(
            list(dict.fromkeys(combined.to_list())),
            names=combined.names,
        )
    return combined, (overrides if not overrides.empty else None)


# Per-thread side-store for freeze-time vintage capacities. Populated by
# `_freeze_period_capacities` after each myopic period's LP solves and read
# by `vintage_service._capture_and_drop_vintages` at restore. Lives off
# n.meta because PyPSA's n.optimize() apparently resets n.meta between
# myopic iterations, wiping our writes — observed empirically on multi-port
# heat-pump Links where the live `p_nom_opt` always reads -0.0 post-solve
# regardless of LP outcome.
#
# Thread-local rather than a plain module dict: a process-wide dict WAS safe
# when the solver was single-threaded under one global lock, but B4's
# per-context solver locks let two `run_simulation` calls run concurrently
# (foreground /run + background queue) on different threads. A shared dict
# raced — cross-`.clear()` mid-restore and key collisions on the
# deterministic `{parent}@{year}` vintage names — producing silent wrong
# vintage/myopic capacities. One solve == one worker thread (the myopic loop
# runs its iterations sequentially on that thread), so a thread-local store
# isolates concurrent solves while preserving cross-iteration myopic
# behaviour. The reader runs inside `restore_modelling`, synchronously on
# the same solve thread that wrote the store — see `_frozen_vintage_store`.
_frozen_vintage_local = threading.local()


def _frozen_vintage_store() -> dict:
    """
    Per-thread vintage/myopic freeze store. Module-global WAS process-wide,
    but B4's per-context solver locks allow two concurrent run_simulation calls
    (foreground /run + background queue) on different threads; a shared dict
    raced → silent wrong vintage capacities. One solve == one thread (myopic
    iterations are sequential on that thread), so a thread-local store isolates
    concurrent solves while preserving cross-iteration myopic behaviour.
    """
    s = getattr(_frozen_vintage_local, "store", None)
    if s is None:
        s = {}
        _frozen_vintage_local.store = s
    return s


# Marks a `vintage_results` entry this module wrote, so a later myopic run can
# clear its OWN stale entries without touching the ones `vintage_service`
# writes for real per-period vintages.
_MYOPIC_VINTAGE_SOURCE = "myopic_freeze"


def _clear_myopic_build_periods(n) -> None:
    """
    Drop `vintage_results` entries left by a PREVIOUS myopic run.

    `apply_vintage_bounds` resets the whole dict at solve start, but it returns
    early when the user has no per-period bounds — which is exactly the case
    this feature exists for. Without this, a myopic run's build periods would
    accumulate across solves and the Capacity Expansion chart would show
    capacity that is no longer there.
    """
    meta = getattr(n, "meta", None)
    if not isinstance(meta, dict):
        return
    root = meta.get("vintage_results")
    if not isinstance(root, dict):
        return
    for comp_class in list(root.keys()):
        by_asset = root.get(comp_class)
        if not isinstance(by_asset, dict):
            continue
        for name in [
            k for k, v in by_asset.items()
            if isinstance(v, dict) and v.get("source") == _MYOPIC_VINTAGE_SOURCE
        ]:
            by_asset.pop(name, None)
        if not by_asset:
            root.pop(comp_class, None)


def _record_myopic_build_period(
    n, comp_class: str, pnom_field: str, names, initial, decided, period,
) -> None:
    """
    Record WHICH PERIOD decided each asset's capacity, through the existing
    ``vintage_results`` channel the Capacity Expansion view already reads.

    Why this is needed: a myopic run freezes assets that carry the default
    ``build_year = 0``, and `_freeze_period_capacities` deliberately does not
    touch ``build_year`` — that column drives PyPSA's activity mask, so writing
    the freeze period into it would change which periods the asset is active in
    and when it retires. Changing the model to populate a chart is the wrong
    trade.

    But the chart groups strictly by ``build_year > 0``, so a myopic run without
    per-period vintage bounds produced an EMPTY "Capacity expansion by period"
    section — precisely the run where the user most needs to see that everything
    was decided in the first period and nothing could be added later.

    ``vintage_results`` already carries ``{class: {asset: {capacity_field,
    initial_capacity, periods: [{build_year, p_nom_opt, ...}]}}}`` and the
    frontend already emits one row per entry, so filling it in needs no
    frontend change. ``p_nom_opt`` here is the vintage's OWN contribution (the
    frontend accumulates it onto ``initial_capacity``), i.e. the delta this
    period added — not the cumulative total.

    Transient vintage rows are skipped: `vintage_service` writes the real
    per-vintage breakdown for their PARENT at restore, and a competing entry
    under the vintage's own name would double-count it in the chart.
    """
    meta = getattr(n, "meta", None)
    if not isinstance(meta, dict):
        return
    try:
        transient = PyPSAService.get_transient_rows(comp_class)
    except Exception:  # noqa: BLE001 — never let bookkeeping break a solve
        transient = set()
    try:
        root = meta.setdefault("vintage_results", {})
        by_asset = root.setdefault(comp_class, {})
        for nm in names:
            name = str(nm)
            if name in transient:
                continue
            existing = by_asset.get(name)
            # Never overwrite vintage_service's richer breakdown.
            if isinstance(existing, dict) and existing.get("source") != _MYOPIC_VINTAGE_SOURCE:
                continue
            ini = float(initial.loc[nm])
            opt = float(decided.loc[nm]) if nm in decided.index else float(initial.loc[nm])
            delta = opt - ini
            if not math.isfinite(delta) or delta <= 1e-6:
                continue  # nothing was added in this period — no row to draw
            by_asset[name] = {
                "capacity_field": pnom_field,
                "initial_capacity": ini,
                "source": _MYOPIC_VINTAGE_SOURCE,
                "periods": [{
                    "build_year": int(period),
                    pnom_field + "_opt": delta,
                    "p_nom_opt": delta,
                }],
            }
        if not by_asset:
            root.pop(comp_class, None)
    except Exception:  # noqa: BLE001 — a chart hint must never abort a solve
        return


def _freeze_period_capacities(n, period, undo_actions, phase) -> int:
    """
    For every extendable asset *active in `period`* (build_year ≤ period
    < build_year + lifetime), set ``p_nom = p_nom_opt`` and flip
    ``p_nom_extendable = False``. Subsequent myopic iterations then see the
    asset as fixed existing capacity rather than a fresh decision variable.

    Returns the number of assets frozen. Original values are recorded in
    ``undo_actions`` so the post-rolling restore puts the network back to
    its pre-solve state — exactly like the vintage-bounds expansion path
    does.
    """
    frozen = 0
    for comp_class, attr, pf in _NOM_TRIPLES:
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        ext_col = f"{pf}_extendable"
        opt_col = f"{pf}_opt"
        if ext_col not in df.columns or opt_col not in df.columns:
            continue
        if "build_year" not in df.columns or "lifetime" not in df.columns:
            continue
        by = df["build_year"]
        lt = df["lifetime"]
        is_active = (by <= period) & (period < by + lt)
        is_extendable = df[ext_col].astype(bool)
        targets = is_active & is_extendable
        if not targets.any():
            continue
        names = df.index[targets]
        # Snapshot originals BEFORE mutation so the undo entry is faithful.
        orig_ext = df.loc[names, ext_col].copy()
        orig_pnom = df.loc[names, pf].copy()
        opt_vals = df.loc[names, opt_col].fillna(0).astype(float)
        # Multi-port Link p_nom_opt recovery. PyPSA 1.1.2's
        # `_from_xarray` (pypsa/components/array.py:31-52) calls
        # `expand_dims(name=c.names)` on the linopy variable's solution;
        # rows whose names weren't in the variable's coords get filled
        # with NaN which converts to -0.0 when written to the static
        # column. Observed on multi-port Link vintages (heat pumps with
        # bus2 set). The real solved values are still in
        # `n.model.solution[f"{comp_class}-{pf}"]` — recover them before
        # they propagate into the freeze store + downstream
        # vintage_results reporting.
        recovered = 0
        if hasattr(n, "model") and n.model is not None:
            try:
                sol = n.model.solution.get(f"{comp_class}-{pf}")
            except (AttributeError, KeyError):
                sol = None
            if sol is not None:
                try:
                    sol_dim = sol.dims[0]
                except (AttributeError, IndexError):
                    sol_dim = None
                if sol_dim:
                    for i, nm in enumerate(names):
                        cur = float(opt_vals.iat[i])
                        # -0.0 has signbit set; ordinary 0.0 does not.
                        # Treat -0.0 as the "missing-coord" sentinel
                        # that array.py's expand_dims produces; ordinary
                        # 0.0 (the LP genuinely picked zero) stays as-is.
                        if cur == 0.0 and math.copysign(1.0, cur) == -1.0:
                            try:
                                real = float(sol.sel({sol_dim: str(nm)}).item())
                            except (KeyError, ValueError, TypeError):
                                real = None
                            if real is not None and math.isfinite(real) and real >= 0:
                                opt_vals.iat[i] = real
                                recovered += 1
        if recovered:
            phase(
                f"Period {period}: recovered {recovered} {comp_class} p_nom_opt value(s) "
                f"from linopy solution (multi-port Link safeguard)."
            )
        df.loc[names, pf] = opt_vals
        df.loc[names, ext_col] = False
        undo_actions.append(("col", attr, pf, names, orig_pnom))
        undo_actions.append(("col", attr, ext_col, names, orig_ext))
        # Persist this period's authoritative p_nom_opt before subsequent
        # iterations can overwrite it. PyPSA's `assign_solution` on later
        # periods does not reliably preserve p_nom_opt on non-extendable
        # rows — observed on multi-port Links (e.g. heat pumps with bus2
        # set) where the value gets reset to -0.0 after the next period's
        # LP, leaving `_capture_and_drop_vintages` to read 0 at restore
        # and report "no expansion" in vintage_results despite the LP
        # having sized the vintage and dispatched it.
        #
        # First attempt used `n.meta["__vintage_frozen_capacities"]`, but
        # PyPSA's `n.optimize()` appears to reset `n.meta` somewhere in
        # its setup, wiping our writes between myopic iterations. Move
        # the stash to a thread-local store that PyPSA can't touch. This
        # myopic loop runs its iterations sequentially on the solve's
        # worker thread, so the per-thread store persists across them; two
        # concurrent solves on different threads get separate stores.
        store = _frozen_vintage_store()
        for nm, val in zip(names, opt_vals):
            try:
                store[(comp_class, str(nm))] = float(val)
            except (TypeError, ValueError):
                pass
        _record_myopic_build_period(
            n, comp_class, pf, names, orig_pnom, opt_vals, period,
        )
        frozen += len(names)
    if frozen:
        phase(f"Period {period}: froze {frozen} extendable asset(s) at p_nom_opt.")
    return frozen


def _capture_extendable_p_nom_opt_to_frozen_store(n, phase) -> int:
    """
    Capture-only counterpart to ``_freeze_period_capacities`` for the
    non-myopic solve paths (full-horizon single-shot, SCLOPF, rolling).

    Walks every extendable asset across ``_NOM_TRIPLES`` and writes its
    current ``p_nom_opt`` into the per-thread freeze store
    (``_frozen_vintage_store()``). **No mutation** — does not touch
    ``p_nom`` or ``p_nom_extendable``. Call immediately after a successful
    ``n.optimize()`` and before any rescaling / post-solve pass.

    Why this exists: PyPSA's ``assign_solution`` writes
    ``p_nom_opt = -0.0`` to multi-port Links (e.g. heat pumps with
    ``bus2`` set) post-solve on non-myopic full-horizon runs too — not
    only between myopic iterations as previously assumed. Without this
    capture, ``_capture_and_drop_vintages`` falls back to the live
    ``p_nom_opt`` at restore time, observes ``-0.0`` (the NaN-only
    filter at vintage_service.py:594 passes it through), and reports
    "no expansion" in ``vintage_results`` despite the LP having sized
    the vintage and dispatched it.

    Single-port assets read correctly from the live ``p_nom_opt`` and
    are unaffected by this helper, but capturing them is harmless and
    keeps the freeze store consistent across solve modes.
    """
    captured = 0
    recovered = 0
    store = _frozen_vintage_store()
    has_model = hasattr(n, "model") and n.model is not None
    for comp_class, attr, pf in _NOM_TRIPLES:
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        ext_col = f"{pf}_extendable"
        opt_col = f"{pf}_opt"
        if ext_col not in df.columns or opt_col not in df.columns:
            continue
        ext_mask = df[ext_col].astype(bool)
        if not ext_mask.any():
            continue
        # Recovery channel for the -0.0 PyPSA writeback bug — see the
        # matching block in `_freeze_period_capacities` for full context.
        sol = None
        sol_dim = None
        if has_model:
            try:
                sol = n.model.solution.get(f"{comp_class}-{pf}")
            except (AttributeError, KeyError):
                sol = None
            if sol is not None:
                try:
                    sol_dim = sol.dims[0]
                except (AttributeError, IndexError):
                    sol_dim = None
        for nm, val in df.loc[ext_mask, opt_col].items():
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if sol is not None and sol_dim and fval == 0.0 and math.copysign(1.0, fval) == -1.0:
                try:
                    real = float(sol.sel({sol_dim: str(nm)}).item())
                except (KeyError, ValueError, TypeError):
                    real = None
                if real is not None and math.isfinite(real) and real >= 0:
                    fval = real
                    recovered += 1
            store[(comp_class, str(nm))] = fval
            captured += 1
    if captured:
        msg = f"Captured p_nom_opt for {captured} extendable asset(s) into freeze store"
        if recovered:
            msg += f" ({recovered} recovered from linopy solution — multi-port Link safeguard)"
        else:
            msg += " (multi-port Link safeguard)"
        phase(msg + ".")
    return captured


def _defer_future_vintage_builds(n, current_period, undo_actions, phase) -> int:
    """
    For every extendable asset with ``build_year > current_period``, set
    ``p_nom_extendable = False`` AND ``p_nom = 0`` so the current iteration's
    LP cannot decide that vintage's capacity. The decision is left to the
    iteration whose ``current_period == vintage.build_year`` — i.e. each
    vintage gets exactly one shot at being sized, by the iteration that
    sees the full hourly view of its own period.

    Why this is required for myopic-foresight correctness:

      Without deferral, iter P sees future-period vintages (``build_year > P``)
      as extendable AND active in their period's snapshots. With limited-
      foresight aggregation those future periods are represented by tiny
      representative slices weighted up to their full annual hours, so the
      LP makes a build decision on partial information.

      Worse, the decision is then THROWN AWAY: when iter (build_year) runs
      and re-sees that vintage as extendable, ``_freeze_period_capacities``
      hasn't frozen it yet (it only freezes assets with
      ``build_year ≤ current_period``), so the LP re-decides from scratch.
      The result is an unstable build pattern that doesn't respond
      coherently to per-period cost signals (e.g. CO2 prices).

      Auto-discount on objective weights compounds the problem: future-
      period CAPEX gets PV-discounted (e.g. 0.873 for +2 years at 7 %), so
      the LP prefers to "build later" until the iteration where that becomes
      its only option. The build then anchors on the representative slice
      rather than the period's true peak.

    Effect across a 3-period horizon [2026, 2027, 2028]:
      * iter 2026 → defer Battery@2027, Battery@2028: only Battery@2026
        is decided.
      * iter 2027 → defer Battery@2028; Battery@2026 already frozen by
        prior iteration; Battery@2027 is decided here.
      * iter 2028 → nothing to defer; Battery@2028 is decided.

    Originals are recorded in ``undo_actions`` so the caller's per-iteration
    finally block reverts the deferral before the NEXT iteration runs —
    keeping the on-disk network identical to its pre-solve state.

    Returns the number of vintages deferred (logging / smoke-test only).
    """
    deferred = 0
    for comp_class, attr, pf in _NOM_TRIPLES:
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        ext_col = f"{pf}_extendable"
        if ext_col not in df.columns or "build_year" not in df.columns:
            continue
        future_mask = df["build_year"] > current_period
        ext_mask = df[ext_col].astype(bool)
        targets = future_mask & ext_mask
        if not targets.any():
            continue
        names = df.index[targets]
        orig_ext = df.loc[names, ext_col].copy()
        orig_pnom = df.loc[names, pf].copy()
        df.loc[names, ext_col] = False
        df.loc[names, pf] = 0.0
        undo_actions.append(("col", attr, ext_col, names, orig_ext))
        undo_actions.append(("col", attr, pf, names, orig_pnom))
        deferred += len(names)
    if deferred:
        phase(
            f"Period {current_period}: deferred {deferred} future-vintage "
            f"decision(s) to their build-year iteration (myopic foresight)."
        )
    return deferred


def _outages_active_in_period(
    network,
    current_period: int,
    outages: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """
    Filter the SCLOPF outage list down to branches that exist in
    ``current_period``. A line/transformer with ``build_year > current_period``
    isn't built yet, so contingencies against it aren't meaningful — it
    silently re-enters the contingency set in the iteration whose
    ``current_period >= build_year`` (and stays in until
    ``current_period >= build_year + lifetime``).

    Returns the filtered list, preserving the input ordering so downstream
    `tuple(...)` calls remain stable across iterations.
    """
    if not outages:
        return []
    active: list[tuple[str, str]] = []
    for comp, name in outages:
        attr = "lines" if comp == "Line" else "transformers" if comp == "Transformer" else None
        if attr is None:
            continue
        df = getattr(network, attr, None)
        if df is None or df.empty or name not in df.index:
            continue
        # build_year / lifetime are optional columns; default to "always active".
        try:
            by = float(df.at[name, "build_year"]) if "build_year" in df.columns else 0.0
        except (TypeError, ValueError):
            by = 0.0
        try:
            lt = float(df.at[name, "lifetime"]) if "lifetime" in df.columns else float("inf")
        except (TypeError, ValueError):
            lt = float("inf")
        # Active iff build_year ≤ period < build_year + lifetime. Matches the
        # convention `_freeze_period_capacities` uses everywhere else in this file.
        if by <= current_period < by + lt:
            active.append((comp, name))
    return active


def _run_myopic_foresight(
    network,
    cfg: "SolverConfig",
    phase,
    merged_solver_options: dict,
    extra_fn,
    tmp_log,
    stop_event: threading.Event | None = None,
    iteration_undo: list | None = None,
) -> tuple[str, str, list]:
    """
    Sequential per-period multi-investment-period LP. Returns
    ``(status, condition, undo_actions)`` — the caller stacks the returned
    undo on top of ``_apply_modelling_assumptions``' restore.

    Algorithm:
      1. For each investment period in ascending order:
         a. Build the iteration's snapshot index (current period only in
            Phase 1; current + aggregated future in Phase 2).
         b. Solve ``n.optimize(snapshots=<window>,
                               multi_investment_periods=True, …)``.
         c. Freeze every extendable asset active in this period at its
            p_nom_opt (see ``_freeze_period_capacities``).
      2. If any iteration returns non-ok, abort and propagate the status.
         The caller's ``finally`` block runs all undos.

    Vintage interaction: future-period vintages (``build_year > current``)
    are explicitly deferred via ``_defer_future_vintage_builds`` before each
    LP call — pinned to ``p_nom=0, extendable=False`` so the current
    iteration cannot decide their capacity. The deferral reverts in the
    per-iteration ``finally`` block so the NEXT iteration can decide its
    own vintage. The natural-decider iteration (``build_year ==
    current_period``) then sees the vintage as extendable and sizes it
    against the full hourly view of its own period — finally locking via
    ``_freeze_period_capacities`` once the LP solves. The vintage_results
    capture callback runs once at the very end during restore and
    aggregates the frozen p_nom_opt across vintages.
    """

    if not isinstance(network.snapshots, pd.MultiIndex):
        raise RuntimeError(
            "Myopic foresight requires multi-investment-period snapshots; "
            "got a flat DatetimeIndex. Validation should reject this earlier."
        )
    periods = sorted(int(p) for p in network.investment_periods)
    if not periods:
        raise RuntimeError(
            "Myopic foresight needs at least one investment period configured."
        )

    # SCLOPF setup — resolve the static contingency set once. Network topology
    # doesn't change between iterations (only p_nom / extendable flag), so the
    # outage candidate list is stable. The per-iteration filter then drops
    # candidates whose build_year > current_period (not yet built) and
    # candidates whose lifetime has expired (no longer in service). If the
    # resolver returns an empty set, fall back to plain myopic LOPF with a
    # diagnostic — matches the existing full-horizon SCLOPF behaviour.
    use_sclopf = bool(getattr(cfg, "sclopf", False))
    all_outages: list[tuple[str, str]] = []
    sclopf_scope = getattr(cfg, "sclopf_scope", "horizon") or "horizon"
    if use_sclopf:
        all_outages = resolve_branch_outages(network, cfg)
        if not all_outages:
            phase(
                "SCLOPF requested but no branches matched the selection — "
                "falling back to plain myopic LOPF for every iteration."
            )
            use_sclopf = False
        else:
            phase(
                f"SCLOPF + myopic active: {len(all_outages)} contingency "
                f"candidate(s) (scope={sclopf_scope}). Per-iteration filter "
                f"will drop branches with build_year > current_period."
            )

    # Accept the caller's list by reference so a SolveAborted mid-loop
    # leaves the partial undo entries in the caller's scope. Without this
    # the inner function's local list disappears when SolveAborted
    # propagates up, and `_freeze_period_capacities` rows from completed
    # periods (extendable=False, p_nom=p_nom_opt) leak onto the
    # post-solve network state.
    if iteration_undo is None:
        iteration_undo = []
    # Clear the per-period objective accumulator so re-solves don't compound
    # across runs. Populated inside the loop below, consumed by the outer
    # status worker to compute the horizon-total objective.
    network._myopic_period_objectives = []
    # Drop the previous myopic run's build-period records before this run
    # writes its own — see `_clear_myopic_build_periods` for why
    # `apply_vintage_bounds`' reset does not cover this case.
    _clear_myopic_build_periods(network)
    for i, current_period in enumerate(periods, start=1):
        # Honour abort between period iterations. Single-period LPs are
        # still uninterruptible (the C-level solver doesn't yield), but on
        # a multi-period myopic run the user gets up to N abort points,
        # each at the start of the next period's LP build.
        _check_stop(stop_event, phase, f"before myopic period {current_period} ({i}/{len(periods)})")
        # Normalise dynamic indexes BEFORE each iteration's LP build. The
        # previous iteration's `n.optimize()` ran `assign_duals` which
        # populated `mu_*` dual `_t` frames over THIS iteration's `sns`
        # subset; if iteration N+1 has a different (e.g. wider) `sns`,
        # those frames carry stale rows that break the next LP's internal
        # `.sel(snapshot=…)` calls with cryptic `dim_0` / `cannot include
        # dtype 'M' in a buffer` errors. The single up-front normalise
        # at run_simulation's entry only protects the FIRST iteration —
        # subsequent iterations need their own pass.
        fixed_idx = _normalise_dynamic_indexes(network, phase)
        if fixed_idx:
            phase(
                f"Myopic [{i}/{len(periods)}] period {current_period}: "
                f"normalised {fixed_idx} stale dynamic index/indexes pre-LP."
            )
        sns, weight_overrides = _build_iteration_snapshots(
            network, current_period, periods, cfg,
        )
        if len(sns) == 0:
            phase(f"Period {current_period}: no snapshots in slice — skipping.")
            continue
        # Skip the LP when no extendable assets are active in `current_period`.
        # This happens after earlier iterations froze every relevant capacity
        # (typical for users without vintage_bounds whose extendable assets all
        # have build_year ≤ current_period). PyPSA's `create_model` walks
        # `c.periodized_cost` even on empty ext_i and trips internal nyears
        # checks in that path, so it's both faster AND more robust to skip.
        # Capacity for this period is already fully determined by frozen rows
        # from earlier iterations; vintage_results capture is unaffected
        # because the parent's p_nom_opt is recomputed at restore() from the
        # frozen vintages, not from this LP.
        has_active_extendable = False
        for _cls, _attr, _pf in _NOM_TRIPLES:
            df = getattr(network, _attr, None)
            if df is None or df.empty:
                continue
            ext_col = f"{_pf}_extendable"
            if ext_col not in df.columns or "build_year" not in df.columns or "lifetime" not in df.columns:
                continue
            by = df["build_year"]
            lt = df["lifetime"]
            is_active = (by <= current_period) & (current_period < by + lt)
            is_ext = df[ext_col].astype(bool)
            if (is_active & is_ext).any():
                has_active_extendable = True
                break
        if not has_active_extendable:
            # No EXPANSION decisions remain for this period (capacity is frozen
            # from earlier iterations). BUT we must still verify the frozen
            # fleet can OPERATIONALLY serve this period's load — and produce its
            # dispatch. The previous behaviour `continue`d here, skipping the
            # solve entirely, which (a) masked operational infeasibility as a
            # clean "optimal" run (the frozen capacity literally cannot serve a
            # higher-demand later period → infeasible, reported as success), and
            # (b) left the period with no dispatch results. Solve dispatch-only
            # over this period's snapshots instead.
            phase(
                f"Myopic [{i}/{len(periods)}] period {current_period}: no new "
                f"extendable assets — running operational dispatch to verify "
                f"feasibility (capacity already frozen)."
            )
            try:
                op_status, op_condition = network.optimize(
                    snapshots=sns,
                    solver_name=cfg.solver_name,
                    multi_investment_periods=True,
                    extra_functionality=extra_fn,
                    log_fn=str(tmp_log),
                    solver_options=merged_solver_options,
                    assign_all_duals=True,
                )
            except Exception as exc:
                # PyPSA can trip its `periodized_cost` / `nyears` check when the
                # extendable index is empty under multi_investment_periods
                # (notably with differing period durations). Retry as a plain
                # single-period operational solve over the same snapshots — same
                # feasibility verdict + dispatch, avoiding that code path.
                phase(
                    f"Myopic period {current_period}: multi-period dispatch solve "
                    f"tripped ({type(exc).__name__}: {exc}); retrying operational."
                )
                op_status, op_condition = network.optimize(
                    snapshots=sns,
                    solver_name=cfg.solver_name,
                    extra_functionality=extra_fn,
                    log_fn=str(tmp_log),
                    solver_options=merged_solver_options,
                    assign_all_duals=True,
                )
            _rescale_results_for_objective(network)
            if op_status != "ok":
                phase(
                    f"Myopic period {current_period} is operationally INFEASIBLE "
                    f"({op_status}/{op_condition}) — the capacity frozen by earlier "
                    f"periods cannot serve this period's load. Aborting (later "
                    f"periods would only drift further)."
                )
                return op_status, op_condition, iteration_undo
            # Accumulate the period's operating cost into the horizon total, the
            # same way the expansion path does (so the reported objective covers
            # every period, not just the ones that expanded).
            try:
                _per_obj = float(network.objective) if getattr(network, "objective", None) is not None else 0.0
            except Exception:
                _per_obj = 0.0
            try:
                _per_const = float(getattr(network, "objective_constant", 0.0) or 0.0)
            except Exception:
                _per_const = 0.0
            if not hasattr(network, "_myopic_period_objectives"):
                network._myopic_period_objectives = []
            network._myopic_period_objectives.append(
                (int(current_period), _per_obj, _per_const)
            )
            continue
        # Phase 2: rewrite snapshot_weightings so the LP's view of each
        # future period sums to that period's TRUE total hours.
        #
        # PyPSA computes `n.nyears` (used by `periodized_cost`) by summing
        # snapshot_weightings.objective per period across the FULL index —
        # not just the LP's `sns` slice. If we only set weights for the
        # representative snapshots and leave the remaining future-period
        # snapshots at their default 1.0, `nyears[future_period]` ends up
        # double-counted (1.0 × non_repr + cluster_size × repr) and the
        # nyears-equality check trips with "overnight_cost cannot be used
        # when investment periods have different durations".
        #
        # Fix: for every future period covered by the aggregation, zero out
        # ALL of its snapshot_weightings first, then overlay the
        # representative weights. Result: nyears for each future period
        # equals (sum of representative weights) which by construction
        # matches the original period's total hours / 8760.
        per_iter_undo: list = []
        # Defer all future-period vintage decisions to their own iteration.
        # See `_defer_future_vintage_builds` docstring for why this is required
        # for myopic-foresight correctness. The defer is reverted in this
        # iteration's finally block so the NEXT iteration sees future
        # vintages as extendable again — each vintage is decided exactly once,
        # by the iteration whose `current_period == vintage.build_year`.
        _defer_future_vintage_builds(network, current_period, per_iter_undo, phase)
        if weight_overrides is not None and len(weight_overrides) > 0:
            sw = getattr(network, "snapshot_weightings", None)
            if sw is not None and not sw.empty:
                weight_cols = [c for c in ("objective", "generators", "stores") if c in sw.columns]
                # Future-period snapshot mask: every snapshot in sw whose
                # period level is strictly greater than current_period. The
                # `sns` slice excludes most of these but `sw` still carries
                # them, so we zero them on the network-wide weights table.
                future_periods_in_iter = sorted({s[0] for s in weight_overrides.index})
                sw_period_level = sw.index.get_level_values(0)
                future_mask = sw_period_level.isin(future_periods_in_iter)
                future_idx = sw.index[future_mask]
                target_idx = weight_overrides.index.intersection(sw.index)
                if len(target_idx) > 0 and weight_cols:
                    for col in weight_cols:
                        orig = sw.loc[future_idx, col].copy()
                        sw.loc[future_idx, col] = 0.0  # zero EVERY future-period snapshot
                        sw.loc[target_idx, col] = weight_overrides.loc[target_idx].astype(float)
                        # Single undo entry per column covers both the zero
                        # and the overlay — restoring the original future
                        # weights wipes both transforms in one step.
                        per_iter_undo.append(("col", "snapshot_weightings", col, future_idx, orig))
                    phase(
                        f"Limited foresight: rescaled {len(future_idx)} future-period "
                        f"snapshot(s) — {len(target_idx)} representatives weighted "
                        f"avg ×{weight_overrides.mean():.2f}, the rest zeroed."
                    )
        full_n = len(sns)
        future_n = full_n - sum(1 for s in sns if s[0] == current_period)
        phase(
            f"Myopic [{i}/{len(periods)}] period {current_period}: "
            f"solving {full_n} snapshot(s) "
            f"({full_n - future_n} hourly + {future_n} aggregated future)."
        )
        tl_kwarg = _compute_loss_atol(network) if cfg.transmission_losses else False
        # SCLOPF dispatch per iteration. Filter the static outage list down
        # to branches active in this period; if every candidate is in the
        # future, fall back to plain LOPF for this iteration. The outage
        # set re-grows in later iterations as those branches come online.
        iter_outages: list[tuple[str, str]] = []
        do_sclopf_this_iter = False
        if use_sclopf:
            iter_outages = _outages_active_in_period(network, current_period, all_outages)
            if not iter_outages:
                phase(
                    f"Myopic [{i}/{len(periods)}] period {current_period}: "
                    f"no active contingency candidates (all build_year > {current_period} "
                    f"or expired) — plain LOPF this iteration."
                )
            else:
                do_sclopf_this_iter = True
                phase(
                    f"Myopic [{i}/{len(periods)}] period {current_period}: "
                    f"SCLOPF with {len(iter_outages)} contingency(ies)."
                )
        try:
            if do_sclopf_this_iter:
                # `optimize_security_constrained` doesn't take a
                # `transmission_losses` kwarg (PyPSA's secant loss
                # formulation isn't wired into the BODF contingency LP).
                # Validation blocks the combination upstream — assert here
                # so a config-bypassed user gets a clean error instead of a
                # cryptic kwargs failure inside PyPSA.
                if tl_kwarg:
                    raise RuntimeError(
                        "SCLOPF + transmission_losses is not supported by "
                        "PyPSA. Disable one of the two."
                    )
                # Decide what snapshot subset the SCLOPF LP actually sees,
                # based on the iteration scope:
                #   • "horizon"        — every snapshot in `sns` (current
                #                        hourly + future-period representatives
                #                        when limited foresight is on)
                #   • "current_period" — only current-period hourly snapshots.
                #                        Future-period lookahead is dropped
                #                        from THIS iteration entirely; future
                #                        decisions land in their own myopic
                #                        iterations via the defer mechanism.
                # The non-SCLOPF (plain LOPF) path always uses the full `sns`
                # — switching scope semantics for the SCLOPF iteration only.
                if sclopf_scope == "current_period":
                    try:
                        period_level = sns.get_level_values(0)
                        sclopf_sns = sns[period_level == current_period]
                    except (AttributeError, KeyError, TypeError):
                        # Flat DatetimeIndex (single-period) or malformed sns:
                        # period filtering doesn't apply. Use the full sns.
                        sclopf_sns = sns
                    if len(sclopf_sns) == 0:
                        # Defensive: shouldn't fire on a normally-built
                        # iteration snapshot index, but if it does, fall back
                        # to the full iteration sns so the LP can't be empty.
                        sclopf_sns = sns
                    if len(sclopf_sns) < len(sns):
                        phase(
                            f"Myopic [{i}/{len(periods)}] period {current_period}: "
                            f"sclopf_scope='current_period' — dropping "
                            f"{len(sns) - len(sclopf_sns)} future-period snapshot(s) "
                            f"from this iteration's contingency LP."
                        )
                else:
                    sclopf_sns = sns
                # PyPSA's `solve_model` (which `optimize_security_constrained`
                # ends up calling internally) hardcodes
                # `extra_functionality(n, n.snapshots)` — passing the FULL
                # network snapshot index, NOT the iteration's `sns` subset.
                # The plain `n.optimize(...)` path passes `sns` instead. Our
                # wrappers (_wrap_with_curtailment_cost, _wrap_with_capex_budget,
                # _wrap_with_objective_scale) assume the iteration subset and
                # slice `p_max_pu` / coefficient arrays by it; if they get
                # `n.snapshots` they'll misalign against linopy variables that
                # only exist over `sns`. Wrap `extra_fn` to force the right
                # snapshot view on the SCLOPF path so both branches behave
                # identically.
                _iter_sns = sclopf_sns  # close over the LP's actual subset
                def _sclopf_extra_fn(net, _ignored_snapshots, _orig=extra_fn, _sns=_iter_sns):
                    if _orig is None:
                        return
                    _orig(net, _sns)
                # `optimize_security_constrained` doesn't accept
                # `assign_all_duals` directly — it forwards **kwargs to
                # linopy.Model.solve. assign_duals lives on n.model.solve
                # not on the model_kwargs path. We rely on PyPSA's default
                # dual assignment for SCLOPF results; the diagnostics below
                # that read marginal-price duals still work.
                status, condition = network.optimize.optimize_security_constrained(
                    branch_outages=tuple(iter_outages),
                    snapshots=sclopf_sns,
                    multi_investment_periods=True,
                    extra_functionality=_sclopf_extra_fn,
                    solver_name=cfg.solver_name,
                    log_fn=str(tmp_log),
                    **merged_solver_options,
                )
            else:
                status, condition = network.optimize(
                    snapshots=sns,
                    solver_name=cfg.solver_name,
                    transmission_losses=tl_kwarg,
                    multi_investment_periods=True,
                    extra_functionality=extra_fn,
                    log_fn=str(tmp_log),
                    solver_options=merged_solver_options,
                    assign_all_duals=True,
                )
            # Rescale n.objective + LP duals back to user-facing € BEFORE
            # the next myopic iteration starts (each iteration sets the
            # scale fresh via the extra_functionality wrapper).
            _rescale_results_for_objective(network)
            # Accumulate the per-period LP objective so the OUTER worker can
            # surface the FULL HORIZON total. Without this, `n.objective` at
            # the end of the myopic loop reflects only the FINAL period's
            # LP-variable, and `n._objective_constant` only the final
            # iteration's constant — leaving a ~15% gap vs the statistics-
            # based cost_breakdown.total. Verified on heat-with-time-series
            # 2026-05-26 solve: LP-side total €3.27B vs cost_breakdown €3.87B
            # (€600M = the two prior periods' contributions). Each entry is
            # (period, variable_objective, objective_constant_at_iter_end).
            # Wrap the entire accumulator in try/except — a bookkeeping
            # failure here MUST NOT abort the solve. Previous version
            # referenced `period` (undefined; loop variable is
            # `current_period`) and raised NameError mid-loop, killing the
            # whole myopic run with "name 'period' is not defined".
            try:
                try:
                    _per_obj = float(network.objective) if getattr(network, "objective", None) is not None else 0.0
                except Exception:
                    _per_obj = 0.0
                try:
                    _per_const = float(getattr(network, "objective_constant", 0.0) or 0.0)
                except Exception:
                    _per_const = 0.0
                if not hasattr(network, "_myopic_period_objectives"):
                    network._myopic_period_objectives = []
                network._myopic_period_objectives.append(
                    (int(current_period), _per_obj, _per_const)
                )
            except Exception as _acc_exc:
                phase(
                    f"Per-period objective accumulator skipped ({_acc_exc}) — "
                    "horizon total will fall back to single-LP n.objective + constant."
                )
        finally:
            # Revert THIS iteration's weight overrides immediately so the
            # next iteration starts from clean weights. Reversed order,
            # same "col" tuple shape as _apply_modelling_assumptions.
            # Each entry wrapped in its own try/except — a dtype mismatch
            # or stale-index failure on one undo MUST NOT skip the rest,
            # or vintage-defer transforms leak into the next iteration
            # (cascading miscapacity across periods). Mirrors the outer
            # myopic_undo walk in run_simulation. QA-flagged regression
            # vector that the previous bare loop didn't protect against.
            for action in reversed(per_iter_undo):
                try:
                    _, attr_name, col, idx, orig = action
                    target = getattr(network, attr_name, None)
                    if target is None:
                        continue
                    valid = [i for i in idx if i in target.index]
                    if valid:
                        target.loc[valid, col] = orig.loc[valid]
                except Exception as _undo_exc:
                    phase(
                        f"per_iter_undo entry failed ({_undo_exc}) — continuing "
                        "with the remaining entries so the iteration cleanup "
                        "completes."
                    )
        if status != "ok":
            phase(
                f"Myopic iteration for period {current_period} failed: "
                f"{status}/{condition}. Aborting before later periods get "
                f"a chance to drift further from the optimum."
            )
            return status, condition, iteration_undo

        # Post-iteration diagnostics. Together they make the LP's behaviour
        # fully visible:
        #   [CURT-POST]    — per-vintage build + curtailment (smoking gun for
        #                    merit-order distortion and VOLL over-build)
        #   [STORAGE-POST] — per-storage build + cycling (does the LP actually
        #                    build storage to absorb renewable excess?)
        #   [CAP-SUM]      — total active capacity per carrier (parent + all
        #                    vintages summed — easier to see overall expansion)
        #   [BUS-BAL]      — per-bus energy balance (detect transmission
        #                    bottlenecks limiting renewable absorption)
        # BARE (no try/except) on purpose — a diagnostic failure here propagates
        # to abort this myopic iteration (see _emit_core_post_solve_diagnostics).
        _emit_core_post_solve_diagnostics(network, sns, current_period, phase)
        # SCLOPF-specific post-solve check: for each active contingency,
        # report the worst-case post-outage line loading and whether the
        # constraint actually binds. Only fires when this iteration ran
        # through the SCLOPF path (do_sclopf_this_iter). The function is
        # defensive — wrap in try/except so a quirk in PyPSA's BODF
        # calculation can't crash the rest of the post-solve work.
        if do_sclopf_this_iter:
            try:
                _log_sclopf_post_solve(network, sns, current_period, iter_outages, phase)
            except Exception as exc:
                phase(f"[SCLOPF-POST] diagnostic skipped: {type(exc).__name__}: {exc}")
        # Cost-decomposition by period × category + LP duals on capacity
        # bounds. Helps the user reason about WHY the LP built X in period P
        # rather than period Q — surfaces both the cost components and the
        # marginal capacity value (μ on p_nom_max). Wrapped in try/except
        # since dual readout depends on assign_all_duals; we don't want a
        # diagnostic to crash the rest of the post-solve work.
        try:
            _log_cost_decomposition_post_solve(network, cfg, sns, current_period, phase)
        except Exception as exc:
            phase(f"[COST-DECOMP] diagnostic skipped: {type(exc).__name__}: {exc}")

        # Freeze this period's decisions before iterating forward.
        _freeze_period_capacities(network, current_period, iteration_undo, phase)
    # Defensive sweep: under SCLOPF + multi-period + limited-foresight,
    # PyPSA's `_set_dynamic_data` is observed to leave passive-branch _t
    # outputs (transformers_t.p0/p1 especially) with NaN entries at
    # snapshots not in the current iteration's LP slice — even though that
    # function's last step is supposed to be a `fillna(0.0)`. The result is
    # that result endpoints + scenario comparison report 0/NaN for
    # branches that DID carry flow in the LP, simply because the iteration
    # that produced the values left holes at snapshots from earlier iters.
    # We don't have a clean PyPSA-side fix, so do a final pass that walks
    # every passive-branch output and patches the holes — and LOG when
    # patching actually happens so the bug stays visible.
    _patch_passive_branch_holes(network, phase)
    phase(f"Myopic foresight complete: {len(periods)} period(s) solved.")
    return "ok", "optimal", iteration_undo


def _patch_passive_branch_holes(n, phase) -> None:
    """
    Final-pass cleanup for passive-branch result tables. Patches any
    NaN remaining in ``lines_t.p0/p1`` / ``transformers_t.p0/p1`` after the
    myopic loop ends.

    Why this exists: in SCLOPF + multi-period + limited-foresight, the
    last myopic iteration's call to PyPSA's ``assign_solution`` →
    ``_set_dynamic_data`` doesn't reliably fillna(0.0) the snapshot rows
    that weren't part of that iteration's LP slice. Earlier iterations'
    values survive (because ``_set_dynamic_data`` does
    ``loc[df.index, df.columns] = df`` rather than replacing the whole
    frame), but any snapshot that was never in any iter's slice — or any
    snapshot in the FIRST iter's slice that got reindexed away by a later
    iter — ends up NaN.

    Symptom on the user side: transformer loading shows zero / "no data"
    for periods that should have full data, even though the LP found
    feasible flows for those snapshots. The downstream view treats NaN
    correctly (skips it), but the right behaviour is for those snapshots
    to read 0 — the LP COULDN'T put flow there because earlier iters
    froze the topology; 0 is the correct LP output, not "missing".
    """
    if n is None or not isinstance(n.snapshots, pd.MultiIndex):
        return  # flat network: PyPSA's bug doesn't fire here
    sns = n.snapshots
    total_patched = 0
    for comp_attr, df_keys in (
        ("lines_t",        ("p0", "p1")),
        ("transformers_t", ("p0", "p1")),
    ):
        accessor = getattr(n, comp_attr, None)
        if accessor is None:
            continue
        for key in df_keys:
            df = getattr(accessor, key, None)
            if df is None or df.empty:
                continue
            # Align row index to n.snapshots first (defensive — handles
            # the rare case where the dataframe carries a stale subset
            # index from a previous solve).
            if not df.index.equals(sns):
                try:
                    df = df.reindex(sns)
                except Exception:
                    continue
            n_na = int(df.isna().sum().sum())
            if n_na == 0:
                continue
            # PyPSA leaves NaN at snapshots the LP didn't touch; 0 is the
            # correct LP-stage value (no flow). fillna(0.0) is what
            # ``_set_dynamic_data`` was supposed to do at solve time.
            df_filled = df.fillna(0.0)
            setattr(accessor, key, df_filled)
            total_patched += n_na
            phase(
                f"[PATCH] {comp_attr}.{key}: filled {n_na} NaN snapshot-rows "
                f"with 0 (PyPSA SCLOPF+multi-period leaves holes; "
                f"recovering for downstream views)."
            )
    if total_patched > 0:
        phase(f"[PATCH] Total passive-branch NaN cells repaired: {total_patched}")


