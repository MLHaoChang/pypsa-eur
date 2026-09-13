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
from typing import Any

import threading as _threading

from fastapi import APIRouter, HTTPException, Query, Response

from services.dispatch_status import dispatch_status as _dispatch_status
from services.pypsa_service import PyPSAService
from services.adequacy.coupling_loop_runner import (  # noqa: F401
    LOOP_WARNING_V1,
    UNREACHABLE_COPY_V1,
    MARGIN_LOOP_PANEL_LABEL,
    NEVER_BOUND_COPY_V1,
    NEVER_BOUND_WITH_MARGIN_COPY_V1,
    CouplingLoopRequest,
)
from services.adequacy.margin_loop_runner import (  # noqa: F401
    MARGIN_LOOP_WARNING_V1,
    MARGIN_MULTI_PERIOD_WARNING_V1,
    PROBE_MARGIN,
    MarginLoopRequest,
)
from services.adequacy.mc_loop_runner import (  # noqa: F401
    McElccAsset,
    McRequest,
)
from services.adequacy.frontier_loop_runner import (  # noqa: F401
    FrontierRequest,
)
from services.adequacy.fmea_sweep_runner import (  # noqa: F401
    FmeaSweepRequest,
)
from services.results.prices import _apply_merit_order_correction  # noqa: F401
from services.results.cost_breakdown import (  # noqa: F401
    _class_lifetime,
    _lifetime_total,
    _sum_lifetime,
)
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

from services import study_state as _study_state
from services.adequacy import slack as _slack
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
from services.results.losses import compute_losses_summary
from services.results.economics_by_carrier import compute_economics_by_carrier
from services.results.objective_decomposition import compute_objective_decomposition
from services.results.load_frames import (
    corrected_marginal_prices as _lf_corrected_marginal_prices,
    lp_scaled_load_frame as _lf_lp_scaled_load_frame,
)
from routers.simulation import _solver_in_flight, _state, _state_snapshot

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
    n = PyPSAService.get_network()
    # `get_cost_breakdown()` keeps its own dispatch gate and 204; the wrapping
    # try mirrors the one that used to surround this call inside the body, so a
    # raising cost breakdown still yields a partial payload rather than a 500.
    try:
        cb = get_cost_breakdown()
    except Exception:
        cb = None
    return compute_objective_decomposition(n, cb)


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
        # MERGE NOTE (2026-09-10): a bare `{}` was indistinguishable from
        # "solved, and this network genuinely rolls up to no carriers" — the
        # wider of this endpoint's two availability holes, and the one a user
        # hits first. Same shape as the success path so callers need one
        # branch, not two. The decomposition moved this gate into the router
        # and the `{}` came back with it; ADR-0001 forbids the conflation.
        return {"available": False, "by_carrier": {}}
    # Foreground project: the VOLL capture lives in the live solver state, not
    # on the network (solver_service strips the slacks). The solver config is
    # the same one cost_breakdown / asset_economics resolve their `cfg` from.
    return compute_economics_by_carrier(
        n, _state.get("solver_config"), _state.get("last_lost_load"),
        result_df=_result_df,
    )


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
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    payload = compute_losses_summary(
        n, source, _state.get("ac_pf_results") is not None, result_df=_result_df,
    )
    return _not_solved() if payload is None else payload


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


@results_router.get("/fmea_modes")
def get_fmea_modes():
    """
    Every COMPUTED failure-mode row on one list (adequacy Phase 4 Task 4):
    class A from the COPT engine (regenerated on every call — zero solves)
    plus the last contingency sweep's class B/C rows, criticality-sorted.
    The worksheet merges this with the per-project sidecar's expert rows
    client-side. 204 only when every source is empty.
    """
    from services.adequacy.copt_endpoint import build_fmea_modes_payload
    try:
        out = build_fmea_modes_payload(
            PyPSAService.get_network(),
            _state.get("solver_config"),
            sweep_record=_state.get("fmea_sweep"),
            get_lock=PyPSAService.get_lock,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if out is None:
        return Response(status_code=204)
    return out




# One predicate for the whole mutual-exclusion mesh (sweep / frontier / mc /
# coupling loop), MOVED to services/study_state.py so the foreground solve
# entrypoints in routers/simulation.py can enforce the same mesh without a
# circular import (this module already imports `_state` from that one). The
# alias is kept because every guard below reads better with it, and because a
# second definition here is exactly how the two sides of a mesh drift apart.
_study_running = _study_state.study_running


def _study_mesh_blocker(self_key: str) -> str | None:
    """The 409 detail that refuses a NEW `self_key` study, or None when the
    surface is free. ONE predicate for all five studies.

    Whole-branch review, findings S2/S4: each study POST carried its own six
    `if` gates, run with NO lock, and they tested the foreground solve by its
    status STRING. Two things were wrong with that. `/simulation/abort` flips
    the status to `"aborted"` while the worker keeps running (the restore
    phase, or HiGHS refusing to yield) — `_solver_in_flight()` exists for
    exactly this and is what preflight, save and activate already use, so a
    study could start on a network whose LP transforms were still being
    reverted. And a gate that runs outside the lock that publishes is a
    check-then-act window: two POSTs close together both passed and the
    second overwrote the first's record, orphaning a worker nothing could
    abort or see. This predicate is therefore called TWICE per POST: once
    early (a cheap refusal before any synchronous work) and once INSIDE the
    publish hold, in `_publish_study`, which is the claim.
    """
    label = _study_state.STUDY_LABELS
    if _study_running(self_key):
        return f"{label.get(self_key, self_key)} is already running"
    for key in _study_state.STUDY_KEYS:
        if key != self_key and _study_running(key):
            return f"{label.get(key, key)} is running — wait for it to finish"
    if _solver_in_flight():
        if _state.get("status") == "aborted":
            return ("a solve is still winding down after its abort — its "
                    "worker is restoring the network; wait for it to exit")
        return "a solve is running — wait for it to finish"
    return None


def _refuse_if_mesh_busy(self_key: str) -> None:
    blocked = _study_mesh_blocker(self_key)
    if blocked:
        raise HTTPException(409, blocked)


def _publish_study(key: str, record: dict, thread: "_threading.Thread") -> None:
    """Claim the surface, publish the record and START the worker under ONE
    `solver_state_lock` hold — the same shape as `/simulation/run`'s claim.

    The mesh is re-checked inside the hold: that is what makes it a claim
    rather than a check. `_study_running` tests `thread.is_alive()` (or
    `ident is None` for a published-but-unstarted thread), so a competing
    POST that takes the lock next reads this record as live. If `start()`
    raises, the record is rolled back rather than left as a never-started
    thread that `record_is_running` would count as running for the rest of
    the process (review finding M1).
    """
    # The MUTATION lock outside the state lock (the order every solver
    # write already uses). Fix review, F2: the save gate (`_save_context`)
    # re-checks the study INSIDE `ctx.mutation_lock` and holds that lock for
    # its whole export, so a study can only publish before a save has begun
    # exporting or after it has finished — never between the save's gate
    # and its export, which is where a sweep's first, lock-free contingency
    # mutation was landing on disk as the user's project.
    lock = PyPSAService.get_lock()
    # Bounded: a foreground solve that claimed between this POST's early
    # gate and here holds the mutation lock for its whole run, and the POST
    # must answer 409 in seconds rather than wait it out (fix review, note).
    if not lock.acquire(timeout=5.0):
        raise HTTPException(409, "a solve is running — wait for it to finish")
    try:
        with PyPSAService.get_solver_state_lock():
            _refuse_if_mesh_busy(key)
            _state[key] = record
            try:
                thread.start()
            except BaseException:
                _state[key] = None
                raise
    finally:
        lock.release()


@results_router.get("/fmea_sweep")
def get_fmea_sweep():
    """
    Status + rows of the last class-B/C contingency sweep (adequacy plan
    Phase 4). 204 = never run. The stored state carries a worker-thread
    handle that must not leak into the payload.
    """
    st = _state.get("fmea_sweep")
    if not st:
        return Response(status_code=204)
    return {k: v for k, v in st.items() if k not in ("thread", "stop_event")}


@results_router.post("/fmea_sweep/abort")
def post_fmea_sweep_abort():
    """
    Ask a running FMEA sweep to stop (Phase 12e).

    The shipped loop routes' contract verbatim: 200 sets the record's stop
    event and the engine stops at its next boundary — so an abort costs at
    most the work already in flight, plus the closing restore, which still
    runs. IDEMPOTENT and 200 even when the run is already finishing or
    finished: "stop" on something that has stopped is satisfied, and a 409
    there would make the button flicker into an error at exactly the moment it
    worked. 404 only when no run has ever been recorded — a client bug, not a
    race.

    Deliberately NOT folded into ``/simulation/abort``: that route's stop
    event belongs to the foreground solver thread and nothing in it reaches a
    study worker, so a user pressing it would be told the abort succeeded
    while the study kept running.
    """
    with PyPSAService.get_solver_state_lock():
        st = _state.get("fmea_sweep")
        if not st:
            raise HTTPException(
                404, "no FMEA sweep has been run in this session")
        ev = st.get("stop_event")
        status = st.get("status")
    if ev is not None:
        ev.set()
    return {"status": status, "aborting": status == "running"}


@results_router.post("/fmea_sweep")
def post_fmea_sweep(body: FmeaSweepRequest | None = None):
    """
    Start the contingency sweep — class B (link outages) plus any class-C
    scenarios in the body — in a worker thread: a sweep is several LP
    solves and must never block a request. 409 while a sweep or a
    foreground solve is running. The closing base re-solve leaves the
    network AND the foreground results in base state (it writes through
    the real state sink).
    """
    from routers.simulation import _state_update
    from services.adequacy.fmea_sweep_runner import start_fmea_sweep
    _refuse_if_mesh_busy("fmea_sweep")
    return start_fmea_sweep(
        body,
        solver_state=_state,
        state_update=_state_update,
        publish_study=_publish_study,
    )


@results_router.get("/frontier")
def get_frontier():
    """
    Status + points of the last cost-vs-availability study (spec §5.6).
    204 when none has been run in this session.
    """
    st = _state.get("frontier")
    if not st:
        return Response(status_code=204)
    return {k: v for k, v in st.items() if k not in ("thread", "stop_event")}


@results_router.post("/frontier/abort")
def post_frontier_abort():
    """
    Ask a running frontier study to stop (Phase 12e).

    The shipped loop routes' contract verbatim: 200 sets the record's stop
    event and the engine stops at its next boundary — so an abort costs at
    most the work already in flight, plus the closing restore, which still
    runs. IDEMPOTENT and 200 even when the run is already finishing or
    finished: "stop" on something that has stopped is satisfied, and a 409
    there would make the button flicker into an error at exactly the moment it
    worked. 404 only when no run has ever been recorded — a client bug, not a
    race.

    Deliberately NOT folded into ``/simulation/abort``: that route's stop
    event belongs to the foreground solver thread and nothing in it reaches a
    study worker, so a user pressing it would be told the abort succeeded
    while the study kept running.
    """
    with PyPSAService.get_solver_state_lock():
        st = _state.get("frontier")
        if not st:
            raise HTTPException(
                404, "no frontier study has been run in this session")
        ev = st.get("stop_event")
        status = st.get("status")
    if ev is not None:
        ev.set()
    return {"status": status, "aborting": status == "running"}


@results_router.post("/frontier")
def post_frontier(body: FrontierRequest | None = None):
    """
    Start the ε-constraint frontier study in a worker thread: one full
    capacity-expansion solve per target, so it must never block a request.
    409 while a study, a sweep or a foreground solve is running.

    Unlike the class-B/C sweep this does NOT freeze capacities — the study
    asks what plan you would BUILD for each standard, so expansion has to
    re-optimise at every point.
    """
    from routers.simulation import _state_update
    from services.adequacy.frontier_loop_runner import start_frontier
    _refuse_if_mesh_busy("frontier")
    return start_frontier(
        body,
        solver_state=_state,
        state_update=_state_update,
        publish_study=_publish_study,
    )


@results_router.get("/mc")
def get_mc():
    """
    Status + payload of the last sequential-MC study (spec §4).

    204 = never run in this session. While the worker runs this serves
    ``{"status": "running", "result": None, ...}`` — same shape as the
    frontier surface, so the panel polls one contract. The stored record
    carries the worker-thread handle, which must never reach the wire.
    """
    st = _state.get("mc")
    if not st:
        return Response(status_code=204)
    # `stop_event` is a threading.Event: unserialisable, and the abort route's
    # only handle on a live run. Same filter the loops' GETs use.
    return {k: v for k, v in st.items() if k not in ("thread", "stop_event")}


@results_router.post("/mc/abort")
def post_mc_abort():
    """
    Ask a running sequential-MC study to stop (Phase 12e).

    The shipped loop routes' contract verbatim: 200 sets the record's stop
    event and the engine stops at its next boundary — so an abort costs at
    most the work already in flight, plus the closing restore, which still
    runs. IDEMPOTENT and 200 even when the run is already finishing or
    finished: "stop" on something that has stopped is satisfied, and a 409
    there would make the button flicker into an error at exactly the moment it
    worked. 404 only when no run has ever been recorded — a client bug, not a
    race.

    Deliberately NOT folded into ``/simulation/abort``: that route's stop
    event belongs to the foreground solver thread and nothing in it reaches a
    study worker, so a user pressing it would be told the abort succeeded
    while the study kept running.
    """
    with PyPSAService.get_solver_state_lock():
        st = _state.get("mc")
        if not st:
            raise HTTPException(
                404, "no sequential-MC study has been run in this session")
        ev = st.get("stop_event")
        status = st.get("status")
    if ev is not None:
        ev.set()
    return {"status": status, "aborting": status == "running"}


@results_router.post("/mc")
def post_mc(body: McRequest | None = None):
    """
    Start a sequential-MC adequacy study — optionally with an ELCC table —
    in a worker thread (spec §4).

    ASYNCHRONOUS BY CONSTRUCTION, not as an optimisation: a ten-asset ELCC
    run is a baseline plus ~10 bisected MC evaluations per asset, i.e. minutes
    of arithmetic. Running it inline would hold a request open long past every
    proxy and browser timeout, and would block the event loop for the whole
    process while doing it.

    Unlike the frontier and the class-B/C sweep this engine SOLVES NOTHING and
    never mutates the network, so it does NOT require a VoLL (spec §4): its
    metrics are hours and MWh, not euros. It is still in the mutual-exclusion
    mesh — the snapshot it takes must not be a half-mutated network, and the
    sweep/frontier re-solve the one it is reading.

    Validation is deliberately SYNCHRONOUS wherever it is cheap: an empty
    fleet, an inconsistent (q, MTTR) pair and an unknown ELCC asset are all
    knowable from the snapshot alone, and a user who typed a wrong asset name
    must learn that now rather than after seven minutes of spinner. The
    in-thread KeyError/ValueError mapping stays as belt-and-braces for the
    cases only the run can discover.
    """
    from services.adequacy.mc_loop_runner import start_mc
    _refuse_if_mesh_busy("mc")
    return start_mc(
        body,
        solver_state=_state,
        publish_study=_publish_study,
    )


@results_router.get("/mc/elcc_candidates")
def get_mc_elcc_candidates():
    """
    The assets an ELCC study may be asked for, for the panel's picker.

    ``{"assets": [{kind, name, nameplate_mw}, …], "max_assets": N}``, sorted by
    nameplate descending, ties by name. `elcc_assets` was API-only until this
    endpoint existed: the panel could render a credit table it had no way to
    request, and a user would have had to type asset names and kinds copied out
    of the network editor — including the kind distinction (an occurrence-
    bearing "generator" vs a must-take "vre") which is a property of the
    OCCURRENCE DATA, not of anything the editor shows.

    Membership AGREES BY CONSTRUCTION with what ``post_mc`` accepts — see
    ``elcc.elcc_candidates``, which reads it off the same snapshot the run
    resolves against. That is the whole point: a candidate this endpoint offers
    and the run then 404s on is the failure mode it exists to prevent.

    Synchronous and read-only: one snapshot, no sampling, no solve. Hence NO
    409 guard — unlike ``post_mc`` this starts nothing and mutates nothing, and
    refusing to list assets while some other study runs would disable the
    picker for minutes at a time for no gain. The lock is still taken for the
    snapshot itself (same discipline as ``post_mc``): the frames must not be
    read half-mutated.

    200 with an EMPTY list — never 204 — when nothing qualifies. "This network
    has no asset whose capacity credit could be measured" is an answer, and the
    panel renders an explanatory line from it; a 204 would collapse it into the
    client's "never fetched" case and leave an empty box on screen.
    """
    from services.adequacy.elcc import MAX_ELCC_ASSETS, elcc_candidates

    n = PyPSAService.get_network()
    with PyPSAService.get_lock():
        try:
            assets = elcc_candidates(n, cfg=_state.get("solver_config"))
        except ValueError as exc:
            # The same walk `/copt` and `/mc` refuse through (S1's
            # `OutageRateError`); the fix review found this route letting it
            # out as a 500.
            raise HTTPException(422, str(exc)) from exc
    return {"assets": assets, "max_assets": MAX_ELCC_ASSETS}


# ── the coupling loop (Phase 7) ───────────────────────────────────────────
#
# The route BINDS the pure controller in services/adequacy/coupling.py to this
# process's network, config and solver: `solve_at` is one capped
# capacity-expansion solve, `evaluate` is one sequential-MC run over the plan
# that solve produced, and everything about storage, locking, aborting and
# restoring lives here rather than in the controller (spec §§2–3).


@results_router.get("/coupling_loop")
def get_coupling_loop():
    """
    Status + payload of the last coupling-loop study (spec §3).

    204 = never run in this session. While the worker runs, this serves the
    SAME record with ``status: "running"`` and an ``iterations`` list that
    grows between polls — that is the whole point of the surface, since a run
    is minutes long and the panel renders each iterate as it lands.

    The record carries a worker-thread handle AND the abort stop-event, and
    neither may reach the wire: both are unserialisable, and the stop event in
    particular is the abort route's only handle on a live run.
    """
    with PyPSAService.get_solver_state_lock():
        st = _state.get("coupling_loop")
        if not st:
            return Response(status_code=204)
        # Shallow copy under the lock; `iterations` is REBOUND by the worker,
        # never mutated, so the list this copy captures is frozen for ever.
        return {k: v for k, v in st.items()
                if k not in ("thread", "stop_event")}


@results_router.post("/coupling_loop/abort")
def post_coupling_loop_abort():
    """
    Ask a running coupling loop to stop (spec §3, plan [S8]).

    200 sets the record's stop event; the controller checks it before each
    solve, so an abort costs at most the iterate already in flight and the
    closing restore still runs. IDEMPOTENT and 200 even when the run is
    already finishing or finished: "stop" on something that has stopped is
    satisfied, and a 409 there would make the button flicker into an error at
    exactly the moment it worked. 404 only when no run has ever been recorded
    — that is a client bug, not a race.

    Deliberately NOT folded into ``/simulation/abort``: that route's stop
    event belongs to the foreground solver thread and nothing in it reaches a
    study worker, so a user pressing it would be told the abort succeeded
    while the loop kept solving.
    """
    with PyPSAService.get_solver_state_lock():
        st = _state.get("coupling_loop")
        if not st:
            raise HTTPException(
                404, "no coupling-loop study has been run in this session")
        ev = st.get("stop_event")
        status = st.get("status")
    if ev is not None:
        ev.set()
    return {"status": status, "aborting": status == "running"}


@results_router.post("/coupling_loop")
def post_coupling_loop(body: CouplingLoopRequest | None = None):
    """
    Start the adequacy-coupled planning loop in a worker thread (spec §3).

    Solve the LP under an energy cap, run the sequential MC on the PLAN it
    produced, retune the cap, re-solve — until the plan meets the user's
    target on the MC's own LOLE rather than on the LP proxy's shed energy.
    The two are not the same standard: the LP has perfect foresight over
    storage and no outages at all, so a plan that sheds exactly its cap in the
    LP can lose load for tens of hours in the MC.

    ASYNCHRONOUS BY CONSTRUCTION: up to ``max_solves`` full capacity-expansion
    solves plus an MC evaluation each, plus the closing restore — minutes to
    tens of minutes.

    VALIDATION IS SYNCHRONOUS WHEREVER IT IS CHEAP, and here that is the whole
    set. Every refusal below is knowable from the config and one snapshot, and
    the alternative is not "a slower error" but a WRONG ANSWER: under a
    rolling or myopic strategy every capped solve fails validation, so the
    loop would burn its budget and report ``unreachable`` — "no plan meets
    this standard" — when the truth is "this strategy cannot enforce a cap".
    """
    from routers.simulation import _state_update
    from services.adequacy.coupling_loop_runner import start_coupling_loop
    _refuse_if_mesh_busy("coupling_loop")
    return start_coupling_loop(
        body,
        solver_state=_state,
        state_update=_state_update,
        publish_study=_publish_study,
    )


# ── the margin loop (Phase 9) ─────────────────────────────────────────────
#
# The SAME controller as the coupling loop, on a different lever. Nothing in
# `services/adequacy/coupling.py` is touched (margin-loop spec §0): the margin
# reaches it through the reciprocal substitution of
# `services/adequacy/margin_lever.py`, under which every comparison the
# controller makes — the multiplicative shrink, the strictly-positive assert,
# the geometric midpoint, the `miss > met` test and the `(cost, -x)` tie-break
# — is already correct for a lever that gets stricter as it GROWS. What lives
# here is what the controller cannot know: that this lever has no energy cap
# (§2.2), that its `binding` comes from the margin's own block (§2.1), where
# the search should START (§2.3), which refusals are knowable before the first
# solve (§2.4), that an out-of-reach margin arrives as `validation_failed`
# rather than as an infeasible LP (§2.5), and that the controller's `x` must
# never reach the wire (§2.6).

@results_router.get("/margin_loop")
def get_margin_loop():
    """
    Status + payload of the last margin-loop study (spec §2.6).

    204 = never run in this session. While the worker runs, this serves the
    SAME record with ``status: "running"`` and an ``iterations`` list that
    grows between polls. The thread handle and the abort stop-event never
    reach the wire: both are unserialisable, and the stop event is the abort
    route's only handle on a live run.
    """
    with PyPSAService.get_solver_state_lock():
        st = _state.get("margin_loop")
        if not st:
            return Response(status_code=204)
        # Shallow copy under the lock; `iterations` is REBOUND by the worker,
        # never mutated, so the list this copy captures is frozen for ever.
        return {k: v for k, v in st.items()
                if k not in ("thread", "stop_event")}


@results_router.post("/margin_loop/abort")
def post_margin_loop_abort():
    """
    Ask a running margin loop to stop (the coupling loop's contract, §2).

    200 sets the record's stop event; the controller checks it before each
    solve, so an abort costs at most the iterate already in flight and the
    closing restore still runs. IDEMPOTENT and 200 even when the run is
    already finishing: "stop" on something that has stopped is satisfied.
    404 only when no run has ever been recorded.
    """
    with PyPSAService.get_solver_state_lock():
        st = _state.get("margin_loop")
        if not st:
            raise HTTPException(
                404, "no margin-loop study has been run in this session")
        ev = st.get("stop_event")
        status = st.get("status")
    if ev is not None:
        ev.set()
    return {"status": status, "aborting": status == "running"}


@results_router.post("/margin_loop")
def post_margin_loop(body: MarginLoopRequest | None = None):
    """
    Drive the PLANNING RESERVE MARGIN until the plan meets the user's target
    on the sequential MC's own LOLE (margin-loop spec §2).

    The sibling of ``POST /results/coupling_loop`` and the answer to the case
    that one cannot serve: on a network whose firm capacity already covers
    demand deterministically, the LP sheds nothing at any energy cap, so no ε
    changes the plan and the cap loop can only report ``unreachable``. The
    loss of load the MC sees there comes from OUTAGES the LP does not model at
    all, and the lever that buys firm capacity the LP sees no deterministic
    reason to build is the margin.

    ASYNCHRONOUS BY CONSTRUCTION: one probing solve plus up to ``max_solves``
    full capacity expansions with an MC evaluation each, plus the closing
    restore.

    VALIDATION IS SYNCHRONOUS WHEREVER IT IS CHEAP, and for this lever that is
    the whole set — ``reserve_margin_facts`` is explicitly preflight-callable
    ("nothing in this function touches ``n.model``"), so the ceiling and the
    unpriceable-asset refusal both cost zero solves. Two of the cap loop's
    refusals are deliberately NOT copied: a VoLL is not required (the margin
    is a constraint, not a price), and ``myopic`` is allowed (each myopic
    iteration's snapshots are exactly one investment period, which is the peak
    the standard is defined against — the margin's own validator downgrades it
    to a warning, and refusing it here would deny a supported configuration).
    """
    from routers.simulation import _state_update
    from services.adequacy.margin_loop_runner import start_margin_loop
    _refuse_if_mesh_busy("margin_loop")
    return start_margin_loop(
        body,
        solver_state=_state,
        state_update=_state_update,
        publish_study=_publish_study,
    )


@results_router.get("/copt")
def get_copt():
    """
    Screening adequacy + the class-A FMECA ranking from the COPT engine
    (adequacy plan Phase 2), computed ON DEMAND from the current network —
    no solve required, zero LP solves involved. fidelity =
    "analytic_convolution": thermal-only, storage-excluded, network-free;
    NOT comparable to a statutory standard, and its divergence from the
    LP proxy is the diagnostic (spec §5.3).

    204 = nothing to convolve: no electrical generator carries resolvable
    occurrence data (see services/adequacy/occurrence.py).
    """
    from services.adequacy.copt_endpoint import build_copt_payload
    try:
        out = build_copt_payload(
            PyPSAService.get_network(),
            _state.get("solver_config"),
            get_lock=PyPSAService.get_lock,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if out is None:
        return Response(status_code=204)
    return out




@results_router.get("/adequacy")
def get_adequacy():
    """
    The minimal AdequacyReport from the last target-constrained solve
    (adequacy plan Phase 1 Task 3): which standard actually bound
    (system cap / zone ceiling / VoLL), achieved ENS + shed-hours vs the
    target, cost excluding shed by construction, all provenance-tagged
    (engine="lp_proxy" — a deterministic proxy, not comparable to a
    statutory standard, and the UI must say so at the point of display).

    204 = no report: the solve ran without a target, or nothing has been
    solved. Same convention as /results/lost_load.
    """
    report = _state.get("adequacy_report")
    if not report:
        return Response(status_code=204)
    return report


@results_router.get("/reserve_margin")
def get_reserve_margin():
    """
    The firm-capacity (planning reserve margin) standard the last solve
    enforced, and what met it (Phase 8 §4): one row per investment period —
    peak, requirement, achieved firm MW, `met`, `binding` — plus the derating
    table (name, kind, built capacity, derate, basis, source, energy_limited)
    and the `derating_bases` roll-up.

    Serves the PERSISTED solve-time stash, emitted into solver state like
    `last_lost_load`, and NEVER a recomputation: the wrapper measured its
    peaks with the load-scaling transforms applied, and the post-solve restore
    has since reverted them — recomputing here would report a standard the LP
    never enforced.

    A met margin is NOT a met reliability target. It is a proxy standard
    justified by convention and by the derating factors, not by a sampler, and
    the panel says so at the point of display.

    204 = no margin result: nothing solved yet, the last solve set no margin,
    or it did not produce a dispatch to judge one against. Same convention as
    /results/lost_load and /results/adequacy.
    """
    from services.adequacy.report import sanitize_reserve_margin_payload

    payload = _state.get("last_reserve_margin")
    if not payload:
        return Response(status_code=204)
    # `max_achievable_mw` is `inf` whenever an active extendable has an
    # unbounded `p_nom_max` — the honest value, and not JSON: Starlette dumps
    # with `allow_nan=False`, so serving it untouched raises inside the
    # response and the panel gets a 500 instead of a report.
    return sanitize_reserve_margin_payload(payload)


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
    from services.results.lost_load import compute_lost_load
    payload = compute_lost_load(
        PyPSAService.get_network(),
        _state.get("last_lost_load"),
        from_,
        to_,
    )
    return Response(status_code=204) if payload is None else payload



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

    The returned frame MAY BE THE LIVE ``loads_t.p_set`` when nothing is
    scaled (Phase 12c-0) — read-only for every consumer; never mutate it.

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
