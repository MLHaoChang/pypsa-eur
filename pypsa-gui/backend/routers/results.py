"""
Read-only `/results/*` serializer endpoints (the `results_router`).

Carved out of `routers/simulation.py` so the threading- and `_state`-critical
run / abort / SSE lifecycle (which stays in simulation.py) is isolated from
these ~33 pure-compute result serializers. The two halves share only the live
solver state: this module imports the `_state` proxy + `_state_snapshot` from
simulation.py (read-only) for the `source=lopf|ac_pf` result lookup. No import
cycle — simulation.py never imports from here; main.py mounts both routers, and
projects.py lazily imports `lp_scaled_load_frame` / `corrected_marginal_prices`
from here.

pandas / numpy / math are imported LOCALLY inside each function (the pattern
this file already used), so they are intentionally absent from the module
header.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Response

from services.dispatch_status import dispatch_status as _dispatch_status
from services.pypsa_service import PyPSAService
from services.serialization import (
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
    wants_slice as _wants_slice,
)
from services.solver_service import (
    SolverConfig,
)
# Multi-period years-weighting helpers (the unified `_years_for_period` /
# `period_years` map + the bare-year-row filter). `is_period_only` is aliased to
# the legacy underscore name so call sites are unchanged.
#
# `is_multi_period` used to be excluded here because get_emissions and
# get_asset_economics each bound the same name to a LOCAL bool, which would
# shadow the callable inside those two functions — anyone writing
# `is_multi_period(n)` there would have hit "bool object is not callable".
# Those locals are now named `is_multi`, matching the convention used
# everywhere else in this module, so the import is safe. The two response
# payloads still emit the "is_multi_period" JSON key; only the variable moved.

# The arithmetic behind the endpoints below lives in `services/results/`
# (see the Phase 2 addendum of the decomposition spec). Each handler here
# keeps the network lookup, the `_dispatch_ready` gate and the `_state`
# reads, calls its `compute_*`, and maps `None` back to 204.
from services.results.cost_breakdown import compute_cost_breakdown
from services.results.asset_economics import compute_asset_economics
from services.results.emissions import compute_emissions
from services.results.lcoh import compute_lcoh
from services.results.carrier_kpis import compute_carrier_kpis
from services.results.prices import compute_prices
from services.results.prices import compute_price_drivers
from services.results.line_duals import compute_line_duals
from services.results.curtailment import compute_curtailment
from services.results.unit_commitment import compute_unit_commitment
from services.results.statistics import compute_statistics
from services.results.loads import compute_load_results
from services.results.load_frames import (
    corrected_marginal_prices as _lf_corrected_marginal_prices,
    lp_scaled_load_frame as _lf_lp_scaled_load_frame,
)
from routers.simulation import _state, _state_snapshot

logger = logging.getLogger("pypsa_gui.results")

results_router = APIRouter()


# ── Results endpoints ─────────────────────────────────────────────────────────

def _not_solved():
    return Response(status_code=204)


def _dispatch_ready(n) -> bool:
    """
    Tighter solve-gate than `is_solved` + non-empty `_t` tables.

    Returns True only when the in-memory network has dispatch that is
    INTERNALLY CONSISTENT with its current topology. This catches the
    "user added a bus after solving and didn't re-run" foot-gun: PyPSA
    doesn't auto-clear `_t` tables on topology mutations, so the legacy
    `not n.generators_t.p.empty` check passes while the dispatch column-
    set is for a different generator population.

    Older gating sites still use the inline `has_dispatch = (...)` pattern;
    over time those should migrate to this helper for single-source-of-
    truth. New call sites should prefer this over rolling their own.
    """
    if not getattr(n, "is_solved", False):
        return False
    return _dispatch_status(n) == "fresh"


def _result_df(n, accessor_name: str, attr: str, source: str = "lopf"):
    """
    Return the DataFrame for `<accessor>.<attr>` (e.g. 'lines_t', 'p0')
    from the source the user asked for.

    Lookup order:
      1. `source='ac_pf'` AND `_state['ac_pf_results']` has the key
         → return the AC PF snapshot.
      2. `source='lopf'` AND `_state['lopf_results']` has the key
         → return the LOPF snapshot.
      3. Fallback → read live from the network (`getattr(n.<accessor>, attr)`).

    The fallback is what makes the source param backward-compatible: when
    Stage 2 hasn't run, `_state['lopf_results']` is None, and every
    endpoint reads the live network exactly as before.

    Returns None if the attribute doesn't exist on the network.
    """
    key = f"{accessor_name}.{attr}"
    src = source if source in ("lopf", "ac_pf") else "lopf"
    snap = _state.get(f"{src}_results")
    if isinstance(snap, dict) and key in snap:
        return snap[key]
    try:
        accessor = getattr(n, accessor_name, None)
        if accessor is None:
            return None
        return getattr(accessor, attr, None)
    except Exception:
        return None


# `_safe_values` / `_ts_payload` (NaN-safe time-series payload builders) and
# `_wants_slice` (the Query-sentinel-aware range check, companion of
# `slice_ts`) now live in `services/serialization.py`, imported above as
# aliases so the call sites in this module are unchanged.


def _serve_ts(
    accessor: str,
    attr: str,
    source: str,
    *,
    from_: int | None = None,
    to_: int | None = None,
    echo_source: bool = False,
):
    """
    Shared body for the trivial `<accessor>.<attr>` time-series serializers.

    Gate on dispatch freshness, pull the frame via `_result_df` (honouring
    `source=lopf|ac_pf`), and return a NaN-safe `_ts_payload` — or 204
    (`_not_solved`) when the network is unsolved/stale, the frame is empty, or
    anything raises (logged with traceback). `echo_source=True` appends
    `{"source": source}` to the payload (the AC-PF voltage/reactive endpoints,
    so the frontend can tell which stage produced the numbers).

    `from_`/`to_` are optional inclusive, positional bounds into the snapshot
    axis (see `services.serialization.slice_ts`). When both are absent the
    payload is byte-identical to the pre-range response — no `range` key —
    which is what keeps every consumer that hasn't been converted to ask for
    a slice working unchanged.

    The ~11 single-table endpoints below are thin wrappers over this so the
    gate + lookup + error-handling lives in ONE place; they stay as explicit
    `@results_router.get` defs (greppable routes, per-endpoint docstrings +
    operationIds preserved). Endpoints with non-trivial bodies keep their own.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    try:
        df = _result_df(n, accessor, attr, source)
        if df is None or df.empty:
            return _not_solved()
        # See `_wants_slice` for why this isn't `from_ is not None or to_ is
        # not None`. No bounds supplied → `range_meta` stays None and the
        # payload is byte-identical to the pre-range response.
        range_meta = None
        if _wants_slice(from_, to_):
            df, range_meta = _slice_ts(df, from_, to_)
        extra = {"source": source} if echo_source else None
        return _ts_payload(df, extra=extra, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()


@results_router.get("/cost_breakdown")
def get_cost_breakdown():
    """
    CAPEX + OPEX broken out per component class.

    PyPSA's `n.statistics()` returns a DataFrame indexed by (component, carrier)
    with columns including 'Capital Expenditure' and 'Operational Expenditure'.
    We pivot that into:
      - per-component-class totals,
      - per-carrier breakdown,
      - and a grand total.

    The grand total here is the right thing to call "Total system cost"; the
    LOPF objective value alone is inferior because it can include additional
    penalty terms or omit certain costs depending on solver config.
    """
    n = PyPSAService.get_network()
    # Tighter gate than n.is_solved alone: also reject stale dispatch (column-
    # set mismatch with current topology — typical when user added/removed a
    # bus after solving without re-running). Without this, capital_cost ×
    # p_nom on phantom-empty dispatch tables surfaces as misleading numbers.
    if not _dispatch_ready(n):
        return _not_solved()
    # PyPSA's n.statistics() reads capital_cost via comp.capital_cost, which
    # is periodized_cost(capital_cost, overnight_cost, discount_rate, lifetime).
    # If the user set overnight_cost but left discount_rate blank, the LP
    # solved fine (we filled the fields transiently in solver_service) but
    # the network state has discount_rate=NaN again after solve — so the
    # annuity factor becomes NaN and statistics returns 0 / drops the row.
    # Re-apply the same fill here, just for the duration of the calculation.
    payload = compute_cost_breakdown(n, _state['solver_config'])
    return _not_solved() if payload is None else payload


@results_router.get("/objective_decomposition")
def get_objective_decomposition():
    """
    Audit endpoint: decompose `n.objective + n.objective_constant` into its
    LP-side components so the user can reconcile `status.objective` against
    `cost_breakdown.total`. Surfaces:

      • `n.objective`            — LP variable-only optimum (can be negative when
                                   the curtailment wrapper subtracts dispatch subsidies).
      • `n.objective_constant`   — PyPSA's fixed-cost offset (existing-capacity CAPEX
                                   + the curtailment wrapper's "all-curtailed" baseline).
      • `_baseline_objective_constant` — our wrapper's captured pre-LP baseline,
                                   used for idempotency across re-solves.
      • `pypsa_gui_objective_scale` — last applied LP-objective scale (clear=1.0 means
                                   either no scaling or rescale already reverted).
      • `cost_breakdown_total`   — computed live via the same path the GUI shows.
      • `gap_eur` and `gap_pct`  — difference between LP total and statistics total.

    Intended use: one-shot diagnosis, not a routine endpoint. Safe on any state.
    """
    import math as _math
    n = PyPSAService.get_network()
    out: dict = {
        "n_objective": None,
        "n_objective_constant": None,
        "lp_total": None,
        "baseline_objective_constant": None,
        "pypsa_gui_objective_scale": None,
        "cost_breakdown_total": None,
        "gap_eur": None,
        "gap_pct": None,
        # Multi-period myopic mode only: per-period (variable, constant) captured
        # by _run_myopic_foresight. Sum gives the full horizon LP total.
        "myopic_period_objectives": None,
        "myopic_horizon_total": None,
    }
    # Per-period myopic objectives, if present.
    me = getattr(n, "_myopic_period_objectives", None)
    if isinstance(me, list) and me:
        try:
            out["myopic_period_objectives"] = [
                {"period": int(p), "variable": float(v), "constant": float(c), "total": float(v + c)}
                for (p, v, c) in me
            ]
            out["myopic_horizon_total"] = sum(v + c for (_, v, c) in me)
        except Exception:
            pass
    try:
        out["n_objective"] = float(n.objective) if getattr(n, "objective", None) is not None else None
    except Exception:
        pass
    try:
        out["n_objective_constant"] = float(getattr(n, "objective_constant", 0.0) or 0.0)
    except Exception:
        out["n_objective_constant"] = float(getattr(n, "_objective_constant", 0.0) or 0.0)
    try:
        out["baseline_objective_constant"] = float(getattr(n, "_baseline_objective_constant", 0.0) or 0.0)
    except Exception:
        pass
    try:
        out["pypsa_gui_objective_scale"] = float(getattr(n, "_pypsa_gui_objective_scale", 1.0) or 1.0)
    except Exception:
        pass
    if out["n_objective"] is not None and out["n_objective_constant"] is not None:
        out["lp_total"] = out["n_objective"] + out["n_objective_constant"]
    # Try cost_breakdown.total — call the function directly to avoid an HTTP round-trip.
    try:
        cb = get_cost_breakdown()
        if isinstance(cb, dict) and "total" in cb:
            out["cost_breakdown_total"] = float(cb["total"])
            if out["lp_total"] is not None:
                gap = out["lp_total"] - out["cost_breakdown_total"]
                out["gap_eur"] = gap
                if abs(out["cost_breakdown_total"]) > 1e-9:
                    out["gap_pct"] = gap / out["cost_breakdown_total"] * 100.0
    except Exception:
        pass
    # Sanity: replace NaN/Inf with None for JSON safety.
    for k, v in list(out.items()):
        if isinstance(v, float) and not _math.isfinite(v):
            out[k] = None
    return out


@results_router.get("/economics_by_carrier")
def get_economics_by_carrier():
    """
    Per-carrier economic roll-up for the LIVE in-memory network.

    Mirrors the Compare View's `_compute_economics_summary` but operates on
    `PyPSAService.get_network()` instead of a disk-loaded project, so the
    single-project Results tab gets identical numbers without depending on
    autosave timing.

    Response shape: ``{carrier: CarrierEconomics, ...}`` — each value has
    revenue_meur / opex_meur / gen_cost_meur / storage_charge_cost_meur /
    curtailment_cost_meur / lost_load_cost_meur / capex_meur / dispatch_gwh /
    lcoe_eur_per_mwh, all with `total` and `by_period`.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return {}
    try:
        import pandas as _pd

        from routers.compare import _compute_economics_summary
        # _compute_economics_summary needs (n, periods, is_multi, has_solve).
        is_multi = isinstance(n.snapshots, _pd.MultiIndex)
        try:
            periods = sorted(int(p) for p in n.investment_periods) if is_multi else []
        except Exception:
            periods = []
        # Foreground project: the VOLL capture lives in the live solver
        # state, not on the network (solver_service strips the slacks).
        result = _compute_economics_summary(
            n, periods, is_multi, True,
            lost_load_cap=_state.get("last_lost_load"),
        )
        # Return just the by_carrier dict — that's what the Results tab needs.
        # Drop per_asset_lcoh (lives in /api/results/lcoh) to keep the payload small.
        return {
            "by_carrier": {k: v.model_dump() for k, v in result.by_carrier.items()},
        }
    except Exception as exc:
        import traceback
        return {"error": str(exc), "trace": traceback.format_exc().splitlines()[-5:]}


@results_router.get("/statistics")
def get_statistics():
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    # Same periodized_cost trap as /cost_breakdown — see comment there.
    payload = compute_statistics(n, _state['solver_config'])
    return _not_solved() if payload is None else payload


@results_router.get("/generators")
def get_generator_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    return _serve_ts("generators_t", "p", source, from_=from_, to_=to_)


@results_router.get("/storage_dispatch")
def get_storage_dispatch_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-snapshot StorageUnit power flow (signed MW).

    Sign convention follows PyPSA: positive = discharge (acts like generation),
    negative = charge (acts like load). The frontend splits this into
    'production' (max(p, 0)) and 'consumption' (-min(p, 0)) for display.
    Separate from /results/storage which returns state-of-charge in MWh.
    """
    return _serve_ts("storage_units_t", "p", source, from_=from_, to_=to_)


@results_router.get("/store_dispatch")
def get_store_dispatch_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-snapshot Store power flow (signed MW). Mirrors /storage_dispatch
    for the Store component. Sign convention identical: positive = discharge,
    negative = charge.
    """
    return _serve_ts("stores_t", "p", source, from_=from_, to_=to_)


@results_router.get("/store_energy")
def get_store_energy_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-snapshot Store state of energy (MWh). Mirrors n.storage_units_t.state_of_charge
    semantics for the Store component.
    """
    return _serve_ts("stores_t", "e", source, from_=from_, to_=to_)


@results_router.get("/storage")
def get_storage_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    return _serve_ts("storage_units_t", "state_of_charge", source, from_=from_, to_=to_)


@results_router.get("/lines")
def get_line_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    return _serve_ts("lines_t", "p0", source, from_=from_, to_=to_)


@results_router.get("/links")
def get_link_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-link power flow at ``bus0`` (MW). Signed by PyPSA convention:
    positive = power flowing from bus0 → bus1 (the "forward" direction the
    Link object declares). Used by the Dispatch tab to render HVDC, P2X
    converter, electrolyser, fuel-cell, and pipeline flows alongside
    generators / storage so the user sees the full energy balance, not
    just generation.
    """
    return _serve_ts("links_t", "p0", source, from_=from_, to_=to_)


@results_router.get("/lcoh")
def get_lcoh():
    """
    Per-electrolyzer Levelised Cost of Hydrogen.

    LCOH for an electrolyser link is the all-in unit cost of the H₂ output,
    in €/MWh_H2:
        LCOH = (annuitised CAPEX + variable OPEX + electricity input cost)
               / H2 produced

    Where:
      * annuitised CAPEX = capital_cost × p_nom_opt (annual €). PyPSA's
        `capital_cost` is already annualised — if the user supplied only
        `overnight_cost`, ``with_periodized_cost_defaults`` derives the
        annualised value from ``overnight × annuity(dr, lt)`` so we read
        `n.c['Link'].capital_cost` (the same accessor `n.statistics()` uses).
      * variable OPEX = ``Σ |p0| × marginal_cost × weights``. Skipped for
        links that don't carry a positive marginal_cost.
      * electricity input cost = ``Σ p0_positive × bus0_marginal_price ×
        weights``. p0 is positive when the link CONSUMES electricity at
        bus0 (the canonical electrolyser direction). Negative half (reverse
        flow / fuel cell) is excluded — it'd be a revenue, not a cost.
      * H2 produced (MWh_H2) = ``Σ p0_positive × efficiency × weights``.
        PyPSA's Link energy balance: power out at bus1 = p0 × efficiency.

    Returns one row per electrolyser-like link AND a fleet-aggregated
    summary (all links combined). Empty list when no qualifying links
    exist or the LP hasn't been solved.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    # Effective capital_cost via the same fill PyPSA uses for n.statistics().
    payload = compute_lcoh(n, _state.get('solver_config') or SolverConfig(), result_df=_result_df)
    return _not_solved() if payload is None else payload


@results_router.get("/ac_pf/status")
def get_ac_pf_status():
    """
    Stage 2 (AC PF) result availability + per-snapshot convergence.

    `available=False` ⇒ Stage 2 hasn't run since the last `/run` or
    `/run_ac_pf` invocation, so the frontend hides the result-source toggle.
    `available=True` ⇒ both `_state['lopf_results']` and `_state['ac_pf_results']`
    are populated; the toggle is enabled and the canvas can switch between
    them.

    Returns the convergence map as `{snapshot_iso: bool}` so the frontend
    can colour-code the snapshot picker without an extra fetch.
    """
    # Snapshot all 7 keys under `_state_lock` so the response is internally
    # consistent. Solver-worker writes go through `_state_update(**ac_pf_out)`
    # which holds the lock for the whole multi-key apply; without taking the
    # lock on the read side too, a poll arriving mid-`_state.update(dict)`
    # could observe `ac_pf_results` populated while `converged_list` /
    # `converged_count` / `total_snapshots` were still stale from a prior
    # run. RLock allows the same thread to re-enter if a future code path
    # nests another `_state_snapshot()` call.
    s = _state_snapshot()
    available = (s.get("ac_pf_results") is not None
                 and s.get("ac_pf_convergence") is not None)
    if not available:
        return {"available": False}
    return {
        "available": True,
        "slack_bus_used": s.get("ac_pf_slack_bus_used"),
        "stripped_voll_slacks": s.get("ac_pf_stripped_voll_slacks") or [],
        # `converged_per_snapshot` is the legacy `{iso: bool}` dict; on
        # multi-period the same ISO can map to multiple periods so the map
        # is ambiguous. New consumers should use `converged_list` —
        # `[{snapshot, period?, ok}, ...]` — which is parallel to n.snapshots
        # and disambiguates each entry. Both fields are emitted for
        # backward compatibility.
        "converged_per_snapshot": s.get("ac_pf_convergence") or {},
        "converged_list": s.get("ac_pf_convergence_list") or [],
        "converged_count": s.get("ac_pf_converged_count") or 0,
        "total_snapshots": s.get("ac_pf_total_snapshots") or 0,
    }


@results_router.get("/losses")
def get_losses_summary(source: str = "lopf"):
    """
    Summarize transmission losses across the solved network.

    PyPSA stores per-branch loss MW values in `n.lines_t.loss` and
    `n.transformers_t.loss` ONLY when the solve was run with
    `transmission_losses=True`. When the kwarg was off (or the run pre-dates
    the feature), those attributes are empty DataFrames — in which case we
    return zeroed totals so the UI can render "0 MWh" rather than a
    not-solved placeholder. `enabled` distinguishes the two cases.

    For `source='ac_pf'`, real losses are computed post-hoc from p0 + p1
    instead of read from the LP's loss variables — meaningful even when
    transmission_losses was off during Stage 1.

    Per-snapshot loss for a line/transformer is in MW; weighted by
    ``snapshot_weightings.generators × investment_period_weightings.years`` to
    get horizon MWh — PyPSA's ENERGY weighting basis (what n.statistics() uses).
    The years factor matters on MULTI-PERIOD runs: without it, loss energy was
    under-reported by ~1/Σyears (a [2030(years=5), 2040(years=10)] horizon
    reported ~1/15 of the true loss MWh).
    """
    import math
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    # Per-snapshot ENERGY weight = generators column × investment-period years.
    # The shared helper applies the generators→objective→1.0 fallback AND the
    # multi-period years scaling (the raw-column read used before omitted years).
    # Returns a Series indexed by n.snapshots, aligned with the _t loss tables
    # below. Lazy import avoids the projects<->simulation import cycle.
    from routers.compare import _build_snapshot_weights as _bsw
    weights = _bsw(n, "generators")

    def _branch_loss(df_t, df_static, comp_name: str):
        """Returns (per_branch_rows, snapshot_total_mw, total_mwh, peak_mw)."""
        rows = []
        total_mwh = 0.0
        peak_mw = 0.0
        snap_total = None
        if df_t is None or df_t.empty:
            return rows, snap_total, total_mwh, peak_mw
        # Replace NaN / inf with 0 so JSON serialises cleanly. PyPSA emits NaN
        # for snapshots when the loss var was masked (e.g. inactive lines).
        clean = df_t.fillna(0.0)
        # Per-line MWh = sum_t (loss_t × weight_t)
        if weights is not None:
            mwh = clean.multiply(weights, axis=0).sum(axis=0)
        else:
            mwh = clean.sum(axis=0)
        peak = clean.abs().max(axis=0)
        snap_total = clean.sum(axis=1)  # per-snapshot total across this comp
        for name in clean.columns:
            v_mwh = float(mwh.get(name, 0.0))
            v_peak = float(peak.get(name, 0.0))
            if not math.isfinite(v_mwh): v_mwh = 0.0
            if not math.isfinite(v_peak): v_peak = 0.0
            rows.append({
                "component": comp_name,
                "name": str(name),
                "loss_mwh": v_mwh,
                "peak_mw": v_peak,
            })
            total_mwh += v_mwh
            if v_peak > peak_mw:
                peak_mw = v_peak
        return rows, snap_total, total_mwh, peak_mw

    has_ac_pf_snapshot = _state.get("ac_pf_results") is not None
    if source == "ac_pf" and has_ac_pf_snapshot:
        # Real losses from AC PF: loss(t, branch) = p0 + p1. PyPSA's p0/p1
        # are signed; their sum is the resistive loss (both are positive
        # injections away from the buses). For lines that didn't converge
        # the snapshot will contain NaN, which `_branch_loss` masks to 0.
        line_p0  = _result_df(n, "lines_t",        "p0", "ac_pf") if not n.lines.empty        else None
        line_p1  = _result_df(n, "lines_t",        "p1", "ac_pf") if not n.lines.empty        else None
        trafo_p0 = _result_df(n, "transformers_t", "p0", "ac_pf") if not n.transformers.empty else None
        trafo_p1 = _result_df(n, "transformers_t", "p1", "ac_pf") if not n.transformers.empty else None
        line_t  = (line_p0  + line_p1)  if line_p0  is not None and line_p1  is not None else None
        trafo_t = (trafo_p0 + trafo_p1) if trafo_p0 is not None and trafo_p1 is not None else None
    else:
        # source='lopf' OR source='ac_pf' before Stage 2 has ever run — read
        # the LP loss variables. Returns empty when transmission_losses was
        # off on the last solve.
        line_t  = _result_df(n, "lines_t",        "loss", "lopf") if not n.lines.empty        else None
        trafo_t = _result_df(n, "transformers_t", "loss", "lopf") if not n.transformers.empty else None
    line_rows,  line_snap,  line_mwh,  line_peak  = _branch_loss(line_t,  n.lines,        "Line")
    trafo_rows, trafo_snap, trafo_mwh, trafo_peak = _branch_loss(trafo_t, n.transformers, "Transformer")

    # `enabled` reflects whether we actually have meaningful loss data:
    # for source='ac_pf' it means a Stage 2 snapshot exists; for source='lopf'
    # it means the LP solve modelled transmission_losses. Avoids the
    # misleading "enabled:true, all zeros" surface when source=ac_pf is
    # requested before Stage 2 has run (loss = p0 + p1 = 0 in DC OPF).
    if source == "ac_pf":
        enabled = has_ac_pf_snapshot and (
            (line_t is not None and not line_t.empty) or
            (trafo_t is not None and not trafo_t.empty)
        )
    else:
        enabled = (line_t is not None and not line_t.empty) or \
                  (trafo_t is not None and not trafo_t.empty)

    total_mwh = line_mwh + trafo_mwh
    peak_mw   = max(line_peak, trafo_peak)

    # Per-branch share of total (for sorting / "where do losses come from").
    rows = line_rows + trafo_rows
    if total_mwh > 0:
        for r in rows:
            r["share_pct"] = 100.0 * r["loss_mwh"] / total_mwh
    else:
        for r in rows:
            r["share_pct"] = 0.0
    rows.sort(key=lambda r: r["loss_mwh"], reverse=True)

    # Total served demand for the "% of demand" KPI. NaN-safe — on multi-period
    # networks an unsolved snapshot fraction leaves `loads_t.p` with NaN cells;
    # without `.fillna(0.0)` the sum produces NaN, JSONResponse.render then
    # 500s with allow_nan=False (same trap CLAUDE.md flags for /results/storage).
    # Belt-and-suspenders: also coerce the final scalar through
    # `_safe_isfinite` so any residual non-finite value collapses to 0.
    import math as _math
    total_demand_mwh = 0.0
    try:
        if hasattr(n.loads_t, "p") and not n.loads_t.p.empty:
            p = n.loads_t.p.fillna(0.0)
            if weights is not None:
                raw_total = float(p.multiply(weights, axis=0).sum().sum())
            else:
                raw_total = float(p.sum().sum())
            total_demand_mwh = raw_total if _math.isfinite(raw_total) else 0.0
    except Exception:
        total_demand_mwh = 0.0

    loss_pct_raw = (100.0 * total_mwh / total_demand_mwh) if total_demand_mwh > 0 else 0.0
    loss_pct = loss_pct_raw if _math.isfinite(loss_pct_raw) else 0.0
    total_mwh_safe = total_mwh if _math.isfinite(total_mwh) else 0.0
    peak_mw_safe = peak_mw if _math.isfinite(peak_mw) else 0.0

    return {
        "enabled": bool(enabled),
        "total_mwh": float(total_mwh_safe),
        "peak_mw": float(peak_mw_safe),
        "total_demand_mwh": float(total_demand_mwh),
        "loss_pct_of_demand": float(loss_pct),
        "by_branch": rows,
    }


@results_router.get("/carrier_kpis")
def get_carrier_kpis():
    """
    Per-carrier KPIs (capacity factor, curtailment, market value, revenue,
    energy, capacity) for Generator and StorageUnit components.

    Wraps PyPSA's `n.statistics.*(groupby='carrier')` helpers, each of which
    returns a (component, carrier)-indexed Series. We filter to Generator
    and StorageUnit since they're the carriers users actually compare against
    each other; Line/Transformer/Load contributions aren't meaningful as
    capacity-vs-energy ratios.

    Caveats baked into PyPSA's conventions:
      • capacity_factor is a decimal (0.30 = 30 %); UI multiplies by 100.
      • curtailment is absolute MWh (energy that COULD have been dispatched
        but wasn't), not a percentage. The percent form is computed here
        relative to the maximum-available energy if both values are present.
      • market_value = revenue / energy; can be NaN when energy = 0.
      • revenue can be negative (e.g. Load is a negative "producer"); for
        Generator/StorageUnit rows this is the LP's revenue from
        marginal-price-weighted dispatch.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_carrier_kpis(n, result_df=_result_df)
    return _not_solved() if payload is None else payload


@results_router.get("/emissions")
def get_emissions(source: str = "lopf"):
    """
    Per-carrier and per-generator CO₂ emissions over the solved horizon.

    Calculation:
      tCO2[g] = Σ_t (generators_t.p[g, t] × weight_t) × co2_emissions[carrier(g)] / efficiency[g]

    `co2_emissions` is the per-carrier intensity (tCO2 / MWh of *primary* energy
    consumed); dividing by generator efficiency converts to tCO2 per MWh of
    *output* energy, which matches what PyPSA's primary-energy global
    constraint enforces. Generators on carriers with co2_emissions=0 (or
    missing carrier definition) contribute zero — the canonical way to mark
    a clean technology.

    Also reports any active `primary_energy` global constraint on
    `co2_emissions`: value (tCO2 cap), shadow price (€/tCO2, the LP dual
    `mu` — the marginal cost to society of one extra tCO2 emitted at the
    optimum), and slack (cap − total emissions).

    `source='lopf'` (default) returns LP-stage dispatch; `'ac_pf'` falls back
    to the Stage 2 dispatch when AC PF has run. The audit flagged that the
    endpoint previously hardcoded `'lopf'` while OTHER `/results/*` accepted
    the parameter — making the result-source toggle inconsistent for the
    Economics/Emissions tab.

    Returns 204-equivalent when no dispatch is available.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_emissions(n, source, result_df=_result_df)
    return _not_solved() if payload is None else payload


@results_router.get("/transformers")
def get_transformer_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-snapshot transformer flows (MW on bus0 side). Mirrors /results/lines
    so the LoadFlow tab can compute loading % the same way.
    """
    return _serve_ts("transformers_t", "p0", source, from_=from_, to_=to_)


@results_router.get("/unit_commitment")
def get_unit_commitment(
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-generator unit-commitment results for committable=True units.

    Returns:
      • `generators` — list of {name, carrier, p_nom, n_starts, n_shuts,
         hours_on, capacity_factor_when_on_pct, total_uc_cost_eur}
      • `status_grid` — TSPayload (index, columns, data) of the binary on/off
         matrix. Frontend renders as a heatmap timeline.
      • `n_committable` — total count, so the UI can decide whether to render
         the section at all.

    The `total_uc_cost_eur` = start_up_cost × n_starts + shut_down_cost × n_shuts.
    The `capacity_factor_when_on_pct` = dispatched_energy / (p_nom × hours_on)
    — the operational CF *given the unit was committed*, distinct from the
    grid-wide CF that includes off-hours.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_unit_commitment(n, from_, to_, result_df=_result_df)
    return _not_solved() if payload is None else payload


@results_router.get("/line_duals")
def get_line_duals():
    """
    Per-line congestion shadow prices from the LP duals.

    A line constraint binds when its flow hits ±s_nom. PyPSA writes:
      • `lines_t.mu_upper[t, l]` ≥ 0 — €/MWh marginal benefit of relaxing
        the +flow capacity by 1 MW at snapshot t on line l.
      • `lines_t.mu_lower[t, l]` ≤ 0 (by convention) — same for the
        -flow direction. Reported here as |mu_lower| so users see a
        positive "binding rent".

    Per line, we aggregate:
      • `binding_hours` — count of snapshots where either dual is non-zero
      • `max_mu` — peak |mu| across all snapshots (worst-case scarcity)
      • `mean_mu_when_binding` — mean over binding hours only (the
        "typical" congestion cost when the line bites)
      • `congestion_rent_eur` — Σ_t (|mu_upper - mu_lower| × |p0| × weight)
        — the LP's annuity-of-physical-redispatch value for this line.

    Requires `assign_all_duals=True` at solve time (we set this in
    solver_service.run_simulation for the standard LOPF path). When duals
    weren't captured (transient solver issues, infeasible LPs) we return an
    empty `rows` list rather than 204 so the UI can render the section
    placeholder instead of disappearing.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_line_duals(n, result_df=_result_df)
    return _not_solved() if payload is None else payload


@results_router.get("/voltages")
def get_voltages(
    source: str = "ac_pf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-snapshot bus voltage magnitude (p.u.) from AC PF.

    Only meaningful when source='ac_pf' AND a Stage 2 snapshot exists — PyPSA's
    LP stage doesn't compute v_mag_pu, so the LOPF source returns 1.0 for
    every bus×snapshot (PyPSA's default v_mag_pu_set). The frontend treats
    "all 1.0" as "no AC PF result" and hides the voltage panel.
    """
    return _serve_ts("buses_t", "v_mag_pu", source, from_=from_, to_=to_, echo_source=True)


@results_router.get("/line_reactive")
def get_line_reactive(
    source: str = "ac_pf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-snapshot reactive power (MVAr) on line bus0 side. AC-PF only.

    LP stage doesn't compute Q — passive branches in DC OPF carry zero
    reactive power by construction. Returns `null` (HTTP 204 equivalent
    handled by `_not_solved`) when no AC PF snapshot exists.
    """
    return _serve_ts("lines_t", "q0", source, from_=from_, to_=to_, echo_source=True)


@results_router.get("/transformer_reactive")
def get_transformer_reactive(
    source: str = "ac_pf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """Per-snapshot reactive power (MVAr) on transformer bus0 side. AC-PF only."""
    return _serve_ts("transformers_t", "q0", source, from_=from_, to_=to_, echo_source=True)


@results_router.get("/prices")
def get_prices(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-bus marginal prices from the LOPF dual variables.

    Reads `n.buses_t.marginal_price` directly. When PyPSA didn't write duals
    (some solver configurations skip them) or all values are zero, the
    response includes an analytical fallback: the marginal cost of the
    most expensive dispatching generator at each snapshot — a system-wide
    proxy that ignores congestion but is better than reporting zeros.
    The `source` field tells the frontend which value-set it's looking at.

    Note: marginal prices are LP-duals — meaningful only for `source='lopf'`.
    For `source='ac_pf'` the snapshot is read from the AC PF state, but the
    values are typically zero (PyPSA's pf() does not produce duals). The
    frontend handles this by falling back to LOPF prices when displaying AC.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_prices(n, source, from_, to_, result_df=_result_df)
    return _not_solved() if payload is None else payload


@results_router.get("/price_drivers")
def get_price_drivers(threshold: float = 2000.0, limit: int = 200):
    """
    For every (bus, snapshot) cell whose |LP dual price| exceeds
    `threshold` €/MWh, return the most-likely marginal generator + a brief
    diagnosis. Helps the user answer "why is the price 3000 at 19:00?"
    without manually cross-referencing dispatch and marginal-cost tables.

    Marginality heuristic: among generators connected to the bus that are
    dispatching at the snapshot (p > 1e-3), pick the one whose
    `marginal_cost` is closest to |price|. The `__voll_*` slack generators
    we add when VOLL > 0 are surfaced specially — their carrier is
    `load_shedding` and a non-zero dispatch always means the LP was
    shedding load at that bus.

    Capped at `limit` rows (sorted by |price| desc) — on a 8760-snapshot
    1000-bus run there could be tens of thousands of cells above threshold
    and shipping them all would jam the frontend.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_price_drivers(n, threshold, limit)
    return _not_solved() if payload is None else payload


@results_router.get("/curtailment")
def get_curtailment(
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_curtailment(n, from_, to_)
    return _not_solved() if payload is None else payload


@results_router.get("/lost_load")
def get_lost_load(
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-bus lost-load dispatch — i.e. the MW that VOLL slack generators
    absorbed at each snapshot. Captured by solver_service right before the
    slack generators are removed in the post-solve restore step; populated
    only when the solver ran with `voll > 0` AND at least one slack actually
    got dispatched.

    Returns a TSPayload (index/columns/data) plus aggregate totals in MWh
    and EUR. 204 No Content when no lost-load data is available (either the
    run had voll=0, hasn't happened yet, or the LP didn't shed any load).
    """
    cap = _state.get("last_lost_load")
    if not cap or cap.get("lost_load_t") is None:
        return Response(status_code=204)
    df = cap["lost_load_t"]
    if df is None or df.empty:
        return Response(status_code=204)
    # total_mwh / total_cost are whole-horizon aggregates captured by the
    # solver, not per-snapshot arrays — a `from`/`to` window below narrows
    # `data` but does NOT recompute these; `range.complete` tells the
    # frontend whether the window covers the whole series.
    total_mwh = float(cap.get("lost_load_total_mwh", 0))
    total_cost = float(cap.get("lost_load_cost_eur", 0))
    # Surface VOLL directly so the frontend doesn't infer it via division
    # (which crashes on zero-MWh edge cases). Cost / MWh recovers the
    # per-MWh VOLL price the solver used.
    voll = (total_cost / total_mwh) if total_mwh > 0 else 0.0

    # Per-column bus carrier. solver_service adds a VOLL slack on EVERY bus
    # (not just electricity), so `lost_load_t.columns` carries bus names
    # across all energy carriers — H2, heat, gas, etc. Surface the bus
    # carrier so the frontend can split lost-load by carrier (the user's
    # ask is to see H2 / heat lost load separately from electrical).
    n = PyPSAService.get_network()
    bus_carriers: dict[str, str] = {}
    if hasattr(n, "buses") and not n.buses.empty and "carrier" in n.buses.columns:
        for col in df.columns:
            try:
                bus_carriers[str(col)] = str(n.buses.at[col, "carrier"] or "")
            except KeyError:
                bus_carriers[str(col)] = ""
    range_meta = None
    if _wants_slice(from_, to_):
        df, range_meta = _slice_ts(df, from_, to_)
    return _ts_payload(df, extra={
        "total_mwh": total_mwh,
        "total_cost_eur": total_cost,
        "voll_eur_per_mwh": voll,
        "bus_carriers": bus_carriers,
    }, range_meta=range_meta)


def lp_scaled_load_frame(n, cfg=None, source: str = "lopf", from_state: bool = True):
    """
    Load power as the LP saw it — the single source of truth for "scaled
    demand", used by both ``/results/loads`` (Results tab) and the Compare
    tab's demand totals so the two never diverge.

    Prefers ``loads_t.p`` (the solver OUTPUT, which already carries the LP-time
    ``load_scalers`` growth) when present; otherwise falls back to
    ``loads_t.p_set`` (the BASE input profile) and re-applies the per-carrier /
    per-period scalers from ``cfg``. Returns a DataFrame (snapshots × loads) or
    ``None``. Never mutates the source frame.

    ``from_state``: when True (default, live network) the LP-stage `_state`
    result snapshot takes priority via ``_result_df``. When False (e.g. a
    freshly-loaded Compare bundle ``temp_n``) read ``n.loads_t.p`` DIRECTLY —
    ``_result_df`` would otherwise return the LIVE network's cached
    `_state['lopf_results']` and cross-contaminate the comparison.
    """
    return _lf_lp_scaled_load_frame(n, cfg, source, from_state, result_df=_result_df)


@results_router.get("/loads")
def get_load_results(
    source: str = "lopf",
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    """
    Per-snapshot load power **as seen by the LP**, after applying
    ``cfg.load_scalers`` (per-period growth factors like 2026=1.0,
    2027=1.1, 2028=1.2).

    Why apply the scaling here rather than reading ``loads_t.p`` directly?
    ``solver_service._apply_modelling_assumptions`` multiplies
    ``loads_t.p_set`` in-place during the LP, then reverts the frame post-
    solve. PyPSA's ``loads_t.p`` MAY contain the scaled values (it copies
    p_set at solve time on most versions) but the persistence story is
    fragile — netcdf round-trips, partial restores, and the fact that
    loads have no decision variable all mean we can't reliably depend on
    p being scaled and p_set being unscaled. So we deterministically
    rebuild "what the LP solved against" from ``p_set + load_scalers``
    each time the chart loads.

    Source priority: LP-stage snapshot first (preserves the LP-time state
    when AC PF later overwrites it), then live ``loads_t.p``, then live
    ``p_set``. Scaling is applied to every branch.
    """
    n = PyPSAService.get_network()
    # Same dispatch-freshness gate as cost_breakdown — refuse to return p_set
    # masquerading as a result on an unsolved or stale-dispatch network.
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_load_results(n, _state.get("solver_config"), source, from_, to_, result_df=_result_df)
    return _not_solved() if payload is None else payload


def corrected_marginal_prices(n, from_state: bool = True):
    """
    Bus marginal prices with the curtailment-cost subsidy distortion removed.

    The curtailment_cost extra-functionality term adds ``-cost x p`` to the LP
    objective for subsidised renewables, dragging the bus dual negative when
    such a renewable sets the price. That's an LP-accounting artefact, not a
    real price — anything trading against the bus (storage charging, revenue)
    would otherwise see phantom negative prices. This restores the real price
    (``marginal_cost``) at exactly the buses/snapshots where a subsidised
    renewable is the dual-setting unit.

    Single source of truth for the merit-order correction: used by
    ``get_asset_economics`` (per-asset) AND by ``projects._compute_economics_summary``
    / ``_compute_prices_summary`` (per-carrier Compare tab) so all price the
    same corrected dual. Returns a DataFrame indexed by snapshots, columns by
    bus; falls back to raw (or zero) duals if anything goes wrong.

    ``from_state``: True (default, live network) reads the LP-stage `_state`
    snapshot via ``_result_df``. False (a loaded Compare bundle ``temp_n``)
    reads ``n.buses_t.marginal_price`` DIRECTLY — ``_result_df`` would otherwise
    return the LIVE network's cached `_state['lopf_results']` and contaminate
    the comparison.
    """
    return _lf_corrected_marginal_prices(n, from_state, result_df=_result_df)


@results_router.get("/asset_economics")
def get_asset_economics():
    """
    Per-asset economics for Generator / StorageUnit / Store.

    For each asset, computes:
      • revenue       = Σ_t p_t × price_t × weight_t      (€)
      • vom_cost      = Σ_t |p_t| × marginal_cost × weight_t  (€)
      • fixed_cost    = capital_cost × p_nom_opt          (€/yr; already
                        annualised by PyPSA's annuity machinery)
      • fom_cost      = fom_cost × p_nom_opt              (informational
                        breakdown of fixed_cost when the user typed FOM)
      • net_profit    = revenue − (fixed_cost + vom_cost)
      • LCOE / LCOS   = (fixed_cost + vom_cost [+ charge_cost]) / energy

    Storage adds:
      • discharge_mwh / charge_mwh — positive and negative halves of p_t
      • discharge_revenue / charge_cost — same split but multiplied by price
      • spread_eur_per_mwh = (discharge_revenue / discharge_mwh) −
                             (charge_cost / charge_mwh)

    Weightings: snapshot_weightings.objective × investment_period_weightings.years —
    same convention used everywhere else (cost_breakdown, carrier_kpis).

    Multi-period response also emits `by_period[period] = {...}` per asset so
    the frontend can show both the horizon-wide total AND a per-period view
    without re-running the same arithmetic on the client.
    """
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_asset_economics(n, _state['solver_config'], result_df=_result_df)
    return _not_solved() if payload is None else payload
