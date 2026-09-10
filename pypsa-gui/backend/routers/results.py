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
import math
from typing import Any

import contextvars as _contextvars
import threading as _threading

from pydantic import BaseModel as _BaseModel

# Whole-branch review, finding S6: the study request models typed their
# floats as plain `float`, which accepts the JSON `Infinity`/`NaN` literals
# (12f's finding, on the asset schemas). A frontier target of `Infinity`
# passed, the study was PUBLISHED and RAN, the POST's own response then
# failed to encode and every later GET on the record answered 500 until a
# swap cleared it. `Finite` (12g's own type) on every study float; the 12f
# handler renders the refusal.
from models.schemas import Finite as _Finite

from fastapi import APIRouter, HTTPException, Query, Response

from services.dispatch_status import dispatch_status as _dispatch_status
from services.pypsa_service import PyPSAService
from services.adequacy.coupling import snapshot_hash as _snapshot_hash
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
        return {}
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
    per_mode: list = []
    copt = get_copt()
    if isinstance(copt, dict):
        per_mode.extend(copt["per_mode"])
    sweep = _state.get("fmea_sweep")
    # Phase 12e: an ABORTED sweep measured real contingencies before it was
    # stopped, and the worksheet is where those rows are read. Dropping them
    # here would make the abort silently lose work the user paid solves for.
    if sweep and sweep.get("status") in ("done", "aborted"):
        for r in sweep.get("rows", []):
            if r.get("failure_mode"):
                per_mode.append({**r["failure_mode"],
                                 "delta_eue_mwh": r.get("delta_eue_mwh")})
    if not per_mode:
        return Response(status_code=204)
    # Phase 12e (shipped-code review, finding 11): `(-criticality, mode_id)`,
    # which is what the spec claimed and the code did not do. Sorting on
    # criticality alone left exactly-tied rows in SOURCE order — class A from
    # the COPT engine, then the sweep's B and C — and the tie is not a corner
    # case here: with no VoLL set every criticality is €0/yr (see below), so
    # the whole ranking ties and the order the worksheet renders depended on
    # which classes happened to have been computed. `reverse=True` cannot be
    # used with a tuple key: it would reverse the mode_id order too.
    per_mode.sort(key=lambda r: (-float(r.get("criticality_eur_per_year", 0.0)),
                                 str(r.get("mode_id", ""))))
    # VOLL travels with the rows so the worksheet can say WHY every
    # criticality is zero. Criticality is ΔEUE × VoLL × occurrence, so with
    # no VoLL set the whole ranking collapses to €0/yr — modes whose ΔEUE
    # differs by 4× tie at zero, and the table reads "these failure modes
    # cost nothing" when the truth is "these failure modes cannot be priced".
    # The sweep already refuses outright (422) without a VoLL; this surface
    # still has LOLE/EUE worth serving, so it reports the condition instead.
    _cfg = _state.get("solver_config")
    try:
        _voll = float(getattr(_cfg, "voll", 0.0) or 0.0)
    except (TypeError, ValueError):
        _voll = 0.0
    return {"per_mode": per_mode,
            "voll_eur_per_mwh": _voll,
            "sweep_status": (sweep or {}).get("status"),
            # Phase 12e (shipped-code review, finding 14): the worksheet is
            # where the sweep's rows are read, so it is where a failed closing
            # re-solve has to be said. The record has carried these since the
            # review's finding 1; this surface used to drop them, leaving the
            # user reading contingency rows with no sign that the network is
            # still on the last contingency.
            "sweep_base_restored": (sweep or {}).get("base_restored"),
            "sweep_base_restore_status": (sweep or {}).get("base_restore_status"),
            "sweep_error": (sweep or {}).get("error")}


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


class FmeaSweepRequest(_BaseModel):
    # Class-C scenarios, passed by the client from the authorized registry
    # GET (/api/projects/{name}/stress_scenarios) — this route operates on
    # the FOREGROUND network and carries no project name, so the sidecar is
    # read where authorization lives and re-validated here before running.
    scenarios: list = []


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
    import time

    from services.adequacy.stress import (
        StressValidationError,
        run_class_c_sweep,
    )
    from services.adequacy.sweep import SweepBudgetError, run_class_b_sweep
    from routers.simulation import _state_update

    _refuse_if_mesh_busy("fmea_sweep")
    cfg = _state.get("solver_config")
    if cfg is None or float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise HTTPException(422, "the sweep requires a VOLL > 0 in solver settings")
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()

    scenarios = list(getattr(body, "scenarios", None) or [])

    stop_event = _threading.Event()
    record: dict = {"status": "running", "rows": [], "error": None,
                    "base_restored": None, "base_restore_status": None,
                    "started_at": time.time(), "thread": None,
                    "stop_event": stop_event}

    def worker():
        try:
            # Class B first with a private final sink; the LAST sweep's
            # closing base re-solve writes the REAL state sink, so
            # /results/lost_load etc. reflect base afterwards.
            rows, restore_b = run_class_b_sweep(
                n, lock, cfg, stop_event=stop_event,
                final_state_update=None if scenarios else _state_update,
            )
            restore = restore_b
            # Phase 12e: the worker runs TWO sweeps, so the flag is checked
            # BETWEEN them. Without this, breaking out of class B's
            # contingency loop returns here and class C runs in full — the
            # abort would stop one sweep, not the study. When class C is
            # skipped, class B ran with a private final sink, so the
            # foreground results are the pre-study ones; that is correct and
            # is what the user is looking at.
            if scenarios and not stop_event.is_set():
                rows_c, restore = run_class_c_sweep(
                    n, lock, cfg, scenarios, stop_event=stop_event,
                    final_state_update=_state_update,
                )
                rows = rows + rows_c
            record.update(
                status="aborted" if stop_event.is_set() else "done",
                rows=rows, finished_at=time.time(), error=None,
                # Phase 12e (shipped-code review, finding 1): whether the
                # closing base re-solve ran, and what the solver said. A
                # sweep whose restore FAILED leaves the network on the last
                # contingency while the foreground results describe another
                # plan — the user has to be told, and before this the guard
                # swallowed the exception and the record still read `done`.
                base_restored=restore.get("base_restored"),
                base_restore_status=restore.get("base_restore_status"))
        except (SweepBudgetError, StressValidationError) as exc:
            record.update(
                status="failed", rows=[], error=str(exc), finished_at=time.time())
        except Exception as exc:  # noqa: BLE001
            record.update(
                status="failed", rows=[], error=str(exc), finished_at=time.time())

    # The loops' pattern (see post_coupling_loop): the record is CLOSED OVER
    # so a context switch cannot redirect the worker's writes away from the
    # dict the poller reads, the request's context is carried so the closing
    # restore's `_state_update` lands in the right project, and the record is
    # published and the thread started under ONE lock hold.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="fmea-sweep")
    record["thread"] = t
    _publish_study("fmea_sweep", record, t)
    return {"status": "running"}


class FrontierRequest(_BaseModel):
    # Reliability targets (‱) to sweep. Omitted → the default spread across
    # the decade where the cost gradient is steep enough to show a knee.
    targets_permyriad: list[_Finite] | None = None


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
    import time

    from services.adequacy.frontier import (
        DEFAULT_TARGETS_PERMYRIAD,
        FrontierBudgetError,
        FrontierConfigError,
        knee_index,
        run_frontier_sweep,
    )
    from routers.simulation import _state_update

    _refuse_if_mesh_busy("frontier")
    cfg = _state.get("solver_config")
    if cfg is None or float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise HTTPException(422, "the frontier requires a VOLL > 0 in solver settings")

    targets = list(getattr(body, "targets_permyriad", None)
                   or DEFAULT_TARGETS_PERMYRIAD)
    # An ENS cap is a positive ceiling on unserved energy: a zero or negative
    # target is not a point on the frontier (0 is "no shedding at all", which
    # the LP cannot reach on any network that ever sheds, and a negative cap
    # is infeasible by construction). Refused here, before the record is
    # published, rather than discovered one infeasible solve later.
    bad = [t for t in targets if not (math.isfinite(float(t)) and float(t) > 0)]
    if bad:
        raise HTTPException(
            422, f"targets_permyriad must be positive finite numbers; got "
                 f"{bad[:5]}{' …' if len(bad) > 5 else ''}")
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()

    stop_event = _threading.Event()
    # IEEE 39-bus review, F3: the standing reserve margin travels with the
    # record. The frontier deliberately does NOT strip it (unlike the
    # contingency sweep) — a margin is a standing standard, not a swept one —
    # and the phase-8 plan says that is right "but must be stated on the
    # panel, or the curve reads as cost-vs-eps when it is
    # cost-vs-eps-at-margin-m". Measured on the IEEE 39-bus network: with the
    # margin already covering every swept target, all three points came back
    # with identical cost and zero ENS and nothing on the panel said why.
    record: dict = {"status": "running", "points": [], "error": None,
                    "warning": None, "knee": None,
                    "reserve_margin": (
                        float(getattr(cfg, "reserve_margin", None))
                        if getattr(cfg, "reserve_margin", None) is not None
                        else None),
                    "targets_permyriad": targets, "base_restored": None,
                    "base_restore_status": None,
                    "started_at": time.time(), "thread": None,
                    "stop_event": stop_event}

    def worker():
        try:
            res = run_frontier_sweep(n, lock, cfg, targets, stop_event=stop_event,
                                     final_state_update=_state_update)
            voll = float(getattr(cfg, "voll", 0.0) or 0.0)
            record.update(
                status="aborted" if res.get("aborted") else "done",
                points=res["points"], warning=res["warning"],
                knee=knee_index(res["points"], voll), voll_eur_per_mwh=voll,
                # Phase 12e: the engine has always computed this and the route
                # threw it away. It says whether the closing re-solve RAN —
                # not that the plan is back — and a study that could not
                # restore the user's plan must say so.
                base_restored=res.get("base_restored"),
                base_restore_status=res.get("base_restore_status"),
                finished_at=time.time(), error=None)
        except (FrontierBudgetError, FrontierConfigError) as exc:
            record.update(status="failed", points=[], error=str(exc),
                          finished_at=time.time())
        except Exception as exc:                              # noqa: BLE001
            # The engine attaches its partial record to the exception so the
            # completed points and the restore's outcome are not lost with it.
            partial = getattr(exc, "frontier_result", None) or {}
            record.update(status="failed", points=partial.get("points") or [],
                          base_restored=partial.get("base_restored"),
                          base_restore_status=partial.get("base_restore_status"),
                          error=str(exc), finished_at=time.time())

    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-frontier")
    record["thread"] = t
    _publish_study("frontier", record, t)
    return {"status": "running", "targets_permyriad": targets}


class McElccAsset(_BaseModel):
    # ``kind`` is a plain str rather than a Literal: the authoritative kind
    # list lives in services/adequacy/elcc.py, and duplicating it in a pydantic
    # Literal here would fork it — the day a fourth kind lands, the route would
    # reject it with a schema error that names no asset. An unknown kind still
    # ends up a 422, raised by the resolver that owns the list.
    kind: str
    name: str


class McRequest(_BaseModel):
    # All optional: the bare POST is the useful default (a headline LOLE/EUE
    # with no ELCC study), and every field below has an engine-side default
    # that this route must not fork.
    draws: int | None = None
    seed: int | None = None
    cov_target: _Finite | None = None
    elcc_assets: list[McElccAsset] | None = None
    # Phase 12c: price the whole profile-bearing fleet as one portfolio, per
    # period, beside the reserve margin's own credit for the same group. A
    # boolean, not a pseudo-asset: the row must never land in `elcc` (a
    # consumer summing that list would double-count), and the population is
    # the engines' to derive, not the caller's to name.
    elcc_portfolio: bool | None = None


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
    import time

    from services.adequacy.elcc import (
        MAX_ELCC_ASSETS,
        elcc_for_asset,
    )
    # Private on purpose: it is the ONE place asset-kind resolution lives, and
    # re-implementing the name lookup here to keep the import public would fork
    # the very mapping (kind → removal semantics) the 404/422 split depends on.
    from services.adequacy.elcc import _resolve as _resolve_elcc_asset
    from services.adequacy.mc import (
        MAX_DRAWS,
        MC_WARNING_V1,
        mc_adequacy,
        snapshot_inputs,
        transition_probs,
    )

    _refuse_if_mesh_busy("mc")

    draws = getattr(body, "draws", None)
    draws = 500 if draws is None else int(draws)
    if draws < 1:
        raise HTTPException(422, "draws must be a positive number of samples")
    if draws > MAX_DRAWS:
        # A product cap, not a numerical one: the benchmark harness runs far
        # deeper budgets by calling the engine directly (spec §7).
        raise HTTPException(
            422,
            f"draws={draws} exceeds the engine cap of {MAX_DRAWS} draws per "
            "study — the adaptive batching stops at that budget anyway")
    seed = getattr(body, "seed", None)
    seed = 0 if seed is None else int(seed)
    cov_target = getattr(body, "cov_target", None)
    cov_target = 0.05 if cov_target is None else float(cov_target)

    assets = [(a.kind, a.name) for a in (getattr(body, "elcc_assets", None) or [])]
    want_portfolio = bool(getattr(body, "elcc_portfolio", None) or False)
    if len(assets) > MAX_ELCC_ASSETS:
        raise HTTPException(
            422,
            f"{len(assets)} ELCC assets requested; the cap is "
            f"{MAX_ELCC_ASSETS} (each asset costs a baseline plus ~10 full "
            "MC evaluations)")

    # The ONE snapshot, taken under the mutation lock (spec §1). Everything
    # after this line — validation and the worker alike — reads plain arrays,
    # so the network is free the moment the lock is released.
    from services.adequacy.activity import activity_summary as _activity_summary

    n = PyPSAService.get_network()
    population = None
    snapshot_fp = None
    margin_payload = None
    with PyPSAService.get_lock():
        vre_names = [nm for kind, nm in assets if kind == "vre"]
        if want_portfolio:
            # The portfolio's must-take half needs its profiles PRESERVED in
            # the snapshot (`snapshot_inputs` keeps only the names it is
            # asked for): every must-take whose column is informative.
            from services.adequacy.copt import (
                must_take_generators,
                series_is_informative,
            )
            pmp = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
            for nm in must_take_generators(n):
                if (nm not in vre_names and pmp is not None
                        and nm in getattr(pmp, "columns", [])
                        and series_is_informative(pmp[nm])):
                    vre_names.append(nm)
        try:
            inputs = snapshot_inputs(
                n, vre_assets=vre_names, cfg=_state.get("solver_config"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Phase 12d: computed HERE, from the network, under the lock — the
        # worker never touches `n` (plan A9), and the must-take half of the
        # disclosure is not in the fleet (shipped-code review, finding 1).
        activity_block = _activity_summary(n, inputs.periods)
        if want_portfolio:
            # Everything the worker needs from the NETWORK and from request-
            # scoped state is captured here (plan 12c v3.1 A9): the
            # population with the engines' capacity rule, the fingerprint the
            # margin payload is checked against, and that payload itself —
            # the worker never touches `_state` or `n`.
            import copy as _copy

            from services.adequacy.portfolio import (
                network_fingerprint,
                portfolio_population,
            )
            population = portfolio_population(n, inputs)
            snapshot_fp = network_fingerprint(n)
            margin_payload = _copy.deepcopy(_state.get("last_reserve_margin"))

    if not inputs.units:
        raise HTTPException(
            422,
            "nothing to sample: no electrical generator carries resolvable "
            "occurrence data (unavailability + MTTR), so the sampled fleet is "
            "empty — an empty fleet would report the entire horizon as loss of "
            "load, which is a statement about missing input data, not about "
            "the system")

    # §2.2's inconsistent-pair rejection, pulled forward: it is a property of
    # the (q, MTTR) pair alone, so there is no reason to discover it a batch
    # into a background run and report it as a failed study.
    for u in inputs.units:
        try:
            transition_probs(u.q, u.mttr_hours, name=u.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    for kind, name in assets:
        try:
            _resolve_elcc_asset(inputs, kind, name)
        except KeyError as exc:
            msg = str(exc.args[0]) if exc.args else str(exc)
            raise HTTPException(
                404, f"unknown ELCC asset {name!r} (kind {kind!r}): {msg}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    # The record is closed over by the worker rather than reached through
    # `_state` inside it: `_state` resolves the *request-scoped* project
    # context, and a worker thread has no request context — it would resolve a
    # different dict and write its result where no reader looks.
    stop_event = _threading.Event()
    record: dict = {"status": "running", "result": None, "error": None,
                    "started_at": time.time(), "thread": None,
                    "stop_event": stop_event}

    def worker():
        # Phase 12h: the rule that decides which profiled units had no
        # outages sampled. Imported from the COPT so `/mc`'s lists and
        # `split_fleet`'s buckets cannot disagree about the same fleet.
        from services.adequacy.copt import is_flag_deterministic as _is_flag_deterministic, rate_is_zero as _rate_is_zero
        try:
            # The ONLY call in the codebase that may carry the flag: this
            # is the study's own baseline, not a replay of one. Every ELCC
            # and loop call passes `stop_event=None` (see `mc_adequacy`).
            metrics = mc_adequacy(inputs, draws=draws, seed=seed,
                                  cov_target=cov_target, stop_event=stop_event)
            # Phase 12c: the headline metrics ARE the baseline every ELCC
            # row needs, argument for argument; injected with a content key
            # the callee recomputes (the N+1 baseline, closed with CRN kept).
            from services.adequacy.elcc import baseline_key as _baseline_key
            from services.adequacy.mc import MAX_DRAWS as _MAX_DRAWS
            key = _baseline_key(inputs, draws=draws, seed=seed,
                                cov_target=cov_target, max_draws=_MAX_DRAWS,
                                batch=250)
            rows = []
            for kind, name in assets:
                # Between assets: a stopped study keeps the rows it priced and
                # never starts another. The bisection inside each asset is
                # checked too, so the worst case is one probe, not one asset.
                if stop_event.is_set():
                    break
                try:
                    rows.append(elcc_for_asset(
                        inputs, kind, name, seed=seed, draws=draws,
                        cov_target=cov_target, baseline=metrics,
                        baseline_key=key, stop_event=stop_event))
                except KeyError as exc:
                    # Belt-and-braces for the 404 the POST already raised
                    # synchronously: the only way to reach this is a name that
                    # resolved at POST and stopped resolving mid-run. Caught
                    # HERE rather than around the whole worker so an internal
                    # KeyError from the sampler cannot be mislabelled as a
                    # missing asset — and so the message names WHICH asset.
                    msg = str(exc.args[0]) if exc.args else str(exc)
                    record.update(
                        status="failed", result=None, finished_at=time.time(),
                        error=f"unknown ELCC asset {name!r} "
                              f"(kind {kind!r}): {msg}")
                    return
            portfolio = None
            if want_portfolio:
                from services.adequacy.portfolio import portfolio_block
                portfolio = portfolio_block(
                    inputs, population, margin_payload=margin_payload,
                    snapshot_fingerprint=snapshot_fp, seed=seed, draws=draws,
                    cov_target=cov_target, baseline=metrics, baseline_key=key,
                    stop_event=stop_event)
            record.update(
                status="aborted" if stop_event.is_set() else "done",
                error=None, finished_at=time.time(),
                result={
                    # A SIBLING payload, deliberately not folded into
                    # AdequacyReport: the MC is an engine-local study (like the
                    # COPT), and merging it would grow the one report shape
                    # every other consumer parses (spec §4, recorded decision).
                    "engine": "mc",
                    "fidelity": "sequential_mc",
                    "metrics": metrics,
                    "elcc": rows,
                    # Phase 12c: a SIBLING of `elcc`, never a row in it.
                    "elcc_portfolio": portfolio,
                    "warning": MC_WARNING_V1,
                    # Phase 12c-pre: the units whose outages were sampled ON
                    # their availability series rather than at nameplate.
                    #
                    # Phase 12h: a rate-zero unit carries a profile but has
                    # NO outages sampled on it, so leaving it here would
                    # make this list's documented meaning false. The MC
                    # never calls `split_fleet`, so the two lists are built
                    # here and are DISJOINT by construction.
                    "profile_units": [
                        str(u.name) for u in inputs.units
                        if getattr(u, "profile", None) is not None
                        and not _rate_is_zero(u)],
                    # M5: EVERY unit the flag zeroed — profiled or folded —
                    # so the disclosure is symmetric across the two shapes.
                    "deterministic_units": [
                        str(u.name) for u in inputs.units
                        if _is_flag_deterministic(u)],
                    # F8: the typed-zero half of the same disclosure — see
                    # `/copt`. Without it a unit whose rate the user typed
                    # as 0 is in NO list here, and this payload has no row
                    # note to fall back on.
                    "rate_zero_units": [
                        str(u.name) for u in inputs.units
                        if _rate_is_zero(u)
                        and not _is_flag_deterministic(u)],
                    "folded_units": [
                        {"name": str(u.name),
                         "folded_constant": float(u.folded_constant),
                         "source": "static"}
                        for u in inputs.units
                        if getattr(u, "folded_constant", None) is not None],
                    # Phase 12d: the activity disclosure (see /copt),
                    # captured in the request.
                    "activity": activity_block,
                })
        except Exception as exc:                              # noqa: BLE001
            record.update(status="failed", result=None, error=str(exc),
                          finished_at=time.time())

    # The loops' pattern: the record is already closed over; Phase 12e adds
    # the request's context (a bare Thread does not inherit the ContextVar the
    # active project lives in) and publish-and-start under one lock hold.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-mc")
    record["thread"] = t
    _publish_study("mc", record, t)
    return {"status": "running", "draws": draws, "seed": seed,
            "cov_target": cov_target, "elcc_assets": len(assets),
            "elcc_portfolio": want_portfolio}


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

# The loop's own caveats, appended to the MC's standing warning. Not a
# restatement of it: these three are properties of the SEARCH, and each one is
# a way a reader could over-read the answer.
LOOP_WARNING_V1 = (
    "The map from the energy cap to MC-LOLE is a step function, not a curve: "
    "a range of caps produces the identical plan and therefore the identical "
    "LOLE, so ε* is the cheapest cap this search VERIFIED, not the largest cap "
    "that would still pass. Every iterate is a genuine optimum of its own "
    "constrained problem, but only iterates whose own MC evaluation met the "
    "target are answers — the bracket is a search heuristic, and tightening ε "
    "can raise MC-LOLE."
)

# [N5]. Stated where the number is read, because the mismatch is structural
# and no choice of ε can remove it.
MULTI_PERIOD_WARNING_V1 = (
    "This network has more than one period: the energy cap is enforced per "
    "period against each period's own demand, while the target is a horizon "
    "SUM of LOLE — a single scalar ε cannot say 'fix period 3 only', so an "
    "unreachable or budget-exhausted verdict is structurally likelier here. "
    "The per-iterate by_period rows are the diagnostic."
)

# [N6]. Three mechanisms, named, because the user's NEXT ACTION differs by
# which one is operating and a bare "unreachable" is unactionable.
# The margin-loop panel's OWN heading, verbatim.
#
# ★ A verdict that diagnoses a dead end and names the way out is only useful
# if the way out can be FOUND: "a planning reserve margin" is a lever, and the
# user still has to know the tool will search for one. This names the control
# they must click. `MarginLoopPanel.test.tsx` pins the panel to the same
# string, because a verdict naming a control that does not exist under that
# name is worse than no pointer at all.
MARGIN_LOOP_PANEL_LABEL = "Reliability-targeted reserve margin loop"

NEVER_BOUND_WITH_MARGIN_COPY_V1 = (
    "The cap never bound. On every iterate that solved, the LP's own shed "
    "energy stayed under the ceiling, so tightening the cap could not change "
    "the plan — and no cap can. What DID shape this plan is the firm-capacity "
    "standard: a reserve margin is already in force, and the loop is reporting "
    "the cap's failure, not the margin's. The loss of load the MC still sees "
    "comes from outages beyond what that margin buys. Raise the margin (or "
    "lower the target) rather than capping harder; the cap has no leverage "
    "here either way. HOW MUCH to raise it by is what the \""
    + MARGIN_LOOP_PANEL_LABEL + "\" on this tab searches for: the same "
    "target and the same sampler, on the lever that is actually shaping "
    "this plan."
)

NEVER_BOUND_COPY_V1 = (
    "The cap never bound. On every iterate that solved, the LP's own shed "
    "energy stayed under the ceiling (the binding column reads something "
    "other than 'system_cap' throughout), so tightening the cap could not "
    "change the plan — and no cap can. The loss of load the MC reports here "
    "comes from OUTAGES the LP does not model at all, not from energy the LP "
    "chose to shed: its deterministic view already covers demand, which is "
    "exactly why the cap has no leverage. What would move this number is firm "
    "capacity the LP sees no deterministic reason to build — a planning "
    "reserve margin, or the candidate unit itself. Capping harder will not. "
    "You do not have to size that margin by hand: the \""
    + MARGIN_LOOP_PANEL_LABEL + "\" on this tab runs this same search on "
    "that lever and certifies what it finds against this same MC-LOLE "
    "target."
)

UNREACHABLE_COPY_V1 = (
    "No cap this search could reach produced a plan that met the target on "
    "the MC's own LOLE. Three mechanisms produce this, and they call for "
    "different responses: (a) the LP has perfect FORESIGHT over storage while "
    "the MC dispatches greedily, so a plan that leans on storage looks "
    "adequate to the solver and is not; (b) demand response serves the LP's "
    "cap but is EXCLUDED as a resource in the MC, so tightening ε buys cost "
    "without buying MC-LOLE and the plan stops changing; (c) tightening ε can "
    "substitute storage for thermal capacity and RAISE MC-LOLE. Check the "
    "per-iterate binding column and by_period rows before raising the target."
)


class CouplingLoopRequest(_BaseModel):
    # `target_lole_h` is the only required field, and it is HORIZON-basis
    # hours (the panel does the h/yr conversion so the wire stays unit-safe).
    # Optional here rather than required-by-pydantic so a missing target is
    # refused with the route's own sentence instead of a schema dump.
    target_lole_h: _Finite | None = None
    draws: int | None = None
    seed: int | None = None
    eps0: _Finite | None = None
    max_solves: int | None = None
    restore: str | None = None


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
    import dataclasses
    import hashlib
    import queue as _queue
    import time

    from services.adequacy.coupling import MAX_LOOP_SOLVES, run_coupling_loop
    from services.adequacy.lever_text import format_lever_value
    from services.adequacy.mc import (
        MAX_DRAWS,
        MC_WARNING_V1,
        mc_adequacy,
        snapshot_inputs,
    )
    from services.adequacy.metrics import horizon_years, resolve_time_basis
    from services.adequacy.sweep import _solve_once
    from routers.simulation import _state_update

    # ── the 409 mesh ──────────────────────────────────────────────────────
    _refuse_if_mesh_busy("coupling_loop")

    # ── the synchronous 422 set ───────────────────────────────────────────
    target = getattr(body, "target_lole_h", None)
    try:
        target = float(target) if target is not None else None
    except (TypeError, ValueError):
        target = None
    if target is None or not (target > 0):
        raise HTTPException(
            422,
            "target_lole_h is required and must be > 0: the loop searches for "
            "the cheapest cap whose plan meets a RELIABILITY STANDARD, and a "
            "target of zero (or none) is not a standard — it is the demand "
            "that no draw ever sheds an hour, which no finite plan can buy")

    draws = getattr(body, "draws", None)
    draws = 500 if draws is None else int(draws)
    if draws < 1:
        raise HTTPException(422, "draws must be a positive number of samples")
    if draws > MAX_DRAWS:
        raise HTTPException(
            422,
            f"draws={draws} exceeds the engine cap of {MAX_DRAWS} draws per "
            "evaluation — and the loop pays that cost once per iterate")
    seed = getattr(body, "seed", None)
    seed = 0 if seed is None else int(seed)

    max_solves = getattr(body, "max_solves", None)
    max_solves = MAX_LOOP_SOLVES if max_solves is None else int(max_solves)
    if not (1 <= max_solves <= MAX_LOOP_SOLVES):
        raise HTTPException(
            422,
            f"max_solves must be between 1 and {MAX_LOOP_SOLVES} (got "
            f"{max_solves}) — each solve is a full capacity expansion, so the "
            "budget is the wall-clock promise this request makes")

    restore = getattr(body, "restore", None) or "base"
    if restore not in ("base", "final"):
        raise HTTPException(
            422,
            f"restore must be 'base' or 'final' (got {restore!r}): 'base' "
            "re-solves with your original config, 'final' leaves you holding "
            "the certified plan at ε*")

    cfg = _state.get("solver_config")
    if cfg is None or float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise HTTPException(
            422, "the coupling loop requires a VOLL > 0 in solver settings — "
                 "with no load-shedding slacks the cap constrains nothing and "
                 "every iterate collapses to the same unconstrained plan")

    strategy = str(getattr(cfg, "solve_strategy", "full") or "full")
    if strategy in ("rolling", "myopic"):
        raise HTTPException(
            422,
            f"the reliability target is not supported with the {strategy!r} "
            "solve strategy: each LP window would need its own demand "
            "denominator, so every capped solve fails validation. The loop "
            "would spend its whole budget on failed iterates and report "
            "'unreachable' — which is a statement about the strategy, not "
            "about the network. Use the full strategy, or unset the target.")

    # The ONE snapshot the validation reads, taken under the mutation lock.
    # `keep_zero_capacity=True` from the very first call (spec §1.2): the
    # sampled fleet's MEMBERSHIP must be invariant across iterates or the
    # positional CRN substreams shift under it, and a fleet that is empty here
    # would be empty for every evaluation too.
    n = PyPSAService.get_network()
    # Phase 12f: the same up-front refusal the margin loop makes, for the same
    # reason and against the same defect. A non-finite value in one of the five
    # finite-default LP bounds is a blocking preflight error, so EVERY iterate
    # would come back `validation_failed`, the loop would spend its whole
    # budget, and the verdict copy would advise "Raise max_solves, or start
    # from a tighter eps0" — advice that can never work here.
    #
    # Guarded at BOTH loops deliberately: this codebase already learned that a
    # guard repeated at seven call sites is the one the eighth route forgets.
    from services.validation_service import _check_nonfinite_bounds as _cnb
    for _iss in _cnb(n):
        raise HTTPException(422, _iss.message)
    with PyPSAService.get_lock():
        try:
            # Phase 12c-0: the LP's demand basis — the plan the loop
            # certifies was built on it (the fifteenth finding).
            inputs = snapshot_inputs(n, keep_zero_capacity=True, cfg=cfg)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        nyears = float(horizon_years(n))

    if not inputs.units:
        raise HTTPException(
            422,
            "nothing to sample: no electrical generator carries resolvable "
            "occurrence data (unavailability + MTTR), so the sampled fleet is "
            "empty — an empty fleet would report the entire horizon as loss of "
            "load, which is a statement about missing input data, not about "
            "the system")

    # [S11] the up-front resolution floor. One shortfall hour in one draw
    # contributes that hour's WEIGHT to the mean, so the smallest non-zero
    # LOLE these draws can resolve is `min positive weight / draws`. A target
    # under it is undecidable: every verdict the run could reach — met or
    # missed — would be an artefact of the sample size, and the study would
    # answer with a confident number that means nothing.
    _w = inputs.weights[inputs.weights > 0]
    floor_h = (float(_w.min()) / draws) if _w.size else None
    if floor_h is not None and target < floor_h:
        need = int(math.ceil(float(_w.min()) / target))
        raise HTTPException(
            422,
            f"target_lole_h={target:g} h is below this study's resolution "
            f"floor of {floor_h:g} h at {draws} draws: a single shortfall "
            "hour in a single draw already exceeds it, so no verdict here "
            "could distinguish a compliant plan from a lucky sample. About "
            f"{need} draws would resolve it.")

    eps0 = getattr(body, "eps0", None)
    if eps0 is None:
        eps0 = getattr(cfg, "ens_cap_permyriad", None)
    try:
        eps0 = float(eps0) if eps0 is not None else None
    except (TypeError, ValueError):
        eps0 = None
    # An unset cap reaches the loop as the ≤ 0 NO-TARGET sentinel, which the
    # controller would clamp to its hard backstop and start the search two
    # decades tighter than any real plan needs. 100‱ is the loose end of the
    # frontier's own default spread — the cheap side, where a solve is fast.
    if eps0 is None or not (eps0 > 0):
        eps0 = 100.0

    lock = PyPSAService.get_lock()
    base_cfg = cfg
    basis = resolve_time_basis(nyears)

    # ── the bindings (spec §3) ────────────────────────────────────────────

    def _snapshot():
        # `base_cfg` is captured in the request — the worker never reads
        # `_state` — and the scalers do not change across iterates.
        with lock:
            return snapshot_inputs(n, keep_zero_capacity=True, cfg=base_cfg)

    def _hash(mc_inputs) -> str:
        """sha256 over exactly what the MC reads — the sorted
        ``(name, capacity_mw)`` unit vector, the sorted
        ``(name, p_nom_mw, e_nom_mwh)`` storage vector, and the residual bytes.

        NOT the objective (plan [B3]): degenerate optima give equal cost for
        different plans, and with DSR configured the objective moves (variable
        cost) while the plan stands still. Equal hash ⇒ bit-identical MC under
        the same seed and draw count, so reuse is exact where cost equality
        was a guess.
        """
        # Phase 12d: one implementation for both loops, testable
        # (`tests/test_adequacy_activity.py` E8).
        return _snapshot_hash(mc_inputs)

    _margin_bound_flag = [False]

    def solve_at(eps: float) -> dict:
        """One capped capacity-expansion solve into a PRIVATE sink, read out
        exactly as ``run_frontier_sweep`` reads its points. Solve failures
        come back as a status — the controller is specified never to see an
        exception from here, and a raise would cost the whole study."""
        sink: dict = {}
        _solve_once(dataclasses.replace(base_cfg, ens_cap_permyriad=float(eps)),
                    n, lock, None, sink)
        status = sink.get("_status")
        out = {"status": status, "condition": sink.get("_condition"),
               "cost_eur": None, "ens_mwh": None, "cap_mwh": None,
               "binding": None, "report": None}
        if status not in ("ok", "optimal"):
            return out
        rep = sink.get("adequacy_report")
        if not rep:
            # A target WAS set and the solve succeeded, so the report is the
            # contract. Its absence is a defect, and reporting it as a failed
            # iterate keeps the loop from evaluating a plan it cannot describe.
            out["status"] = "no_report"
            out["condition"] = "the solve returned no adequacy report"
            return out
        # Did a reserve margin shape this iterate? The controller's row keeps
        # nine fixed keys and drops the report, and `coupling.py` is
        # deliberately not touched (it is the regression oracle for the margin
        # loop), so the answer is captured HERE, where the report is in hand.
        # `binding` itself can never say this: it is computed purely from the
        # ENS caps and reads "voll" even when a margin is what bound.
        _rm = (rep.get("reserve_margin") or {}).get("by_period") or []
        if any(bool(p.get("binding")) for p in _rm):
            _margin_bound_flag[0] = True
        sysblk = rep["target"]["system"]
        out.update(
            report=rep,
            cost_eur=float(rep["cost"]["total_system_cost_eur"]),
            ens_mwh=float(sysblk["achieved_ens_mwh"]),
            cap_mwh=float(sysblk["cap_mwh"]),
            binding=rep["target"]["binding"],
        )
        return out

    # The floor the payload reports, refreshed by every evaluation so the
    # value that ships is the FINAL evaluation's own (spec v1.2 §3) rather
    # than the up-front estimate. With `draws == max_draws` the sample count
    # is pinned, so the two agree — which is the point: a drifting n_samples
    # would mean the floor under the verdict was not the floor that was
    # validated.
    eval_state: dict = {"floor": floor_h}

    def evaluate():
        mc_inputs = _snapshot()
        # §3's normative call. `max_draws=N` is what PINS the sample count:
        # merely ignoring cov_target leaves the adaptive 2000-draw cap in play
        # and n_samples drifts between iterates, which breaks the common
        # random numbers the plateau reuse rests on.
        # `stop_event` is NEVER passed here: this is a REPLAY of one batch
        # sequence (see `mc_adequacy`'s note), and the loop's own abort is
        # checked between iterates, never inside an evaluation.
        metrics = mc_adequacy(mc_inputs, draws=draws, seed=seed,
                              max_draws=draws, stop_event=None)
        try:
            eval_state["floor"] = metrics.get("resolution_floor_h")
        except AttributeError:                                # noqa: BLE001
            pass
        return _hash(mc_inputs), metrics

    def _plan_hash():
        """The duck-typed probe of spec v1.2 §1. The hash comes from the
        snapshot alone, so the controller can skip an MC on a plateau instead
        of running one and throwing the result away."""
        return _hash(_snapshot())

    evaluate.plan_hash = _plan_hash

    # ── the record (closed over, never reached through `_state`) ──────────
    stop_event = _threading.Event()
    record: dict = {
        "study": "coupling_loop",
        "status": "running",
        "target_lole_h": target,
        "basis": basis,
        "horizon_years": nyears,
        "draws": draws,
        "seed": seed,
        "eps0": eps0,
        "max_solves": max_solves,
        "restore": restore,
        "base_restored": False,
        "confident": False,
        "eps_star": None,
        "resolution_floor_h": floor_h,
        "solves_used": 0,
        "iterations": [],
        "final": None,
        "verdict": None,
        "warning": MC_WARNING_V1 + " " + LOOP_WARNING_V1,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
    }

    def on_iteration(row: dict) -> None:
        """Grow the record by REBINDING, never by appending in place.

        ``get_coupling_loop`` serves a shallow copy, so an in-place append
        hands the serializer the very list this thread is mutating: a mid-run
        GET can then be written half-way through an append, and a client
        polling every second can watch its own earlier history change. A fresh
        list per iterate makes every snapshot immutable by construction — each
        GET's list is a prefix of the next, permanently.
        """
        with PyPSAService.get_solver_state_lock():
            record["iterations"] = record["iterations"] + [row]

    # The solver's own word on the closing re-solve, for the payload. A cell
    # rather than a return value because `_restore_closing` returns a bool
    # that four call sites already read (12e shipped-code review, S1).
    _restore_word: list = [None]

    def _restore_closing(met: bool, eps_star) -> bool:
        """The closing re-solve — the route's job, on EVERY path (spec §3,
        §1.3's pattern). The loop mutates the network once per iterate, so
        without this it is left on whichever ε happened to be last while the
        foreground results still describe the pre-study solve: the study
        silently rewrites the user's plan and says nothing.

        ``"final"`` is only meaningful on a met verdict — there is no
        certified cap otherwise — so it falls back to base rather than
        applying a cap nothing verified.
        """
        from services.adequacy.sweep import restore_is_clean
        from services.solver_service import run_simulation

        use_final = (restore == "final" and met and eps_star is not None)
        if use_final:
            final_cfg = dataclasses.replace(
                base_cfg, ens_cap_permyriad=float(eps_star))
            # Persisted through the normal config path (read-modify-write
            # under the solver-state lock, exactly as PUT /solver_config
            # does) BEFORE the solve: the user asked to hold ε*, and a
            # restore whose solve fails must still leave the setting they
            # will re-run with, with `base_restored` reporting the failure.
            with PyPSAService.get_solver_state_lock():
                _state["solver_config"] = dataclasses.replace(
                    _state["solver_config"],
                    ens_cap_permyriad=float(eps_star))
        else:
            final_cfg = base_cfg
        try:
            status, condition = run_simulation(
                final_cfg, n, lock, _threading.Event(), _queue.SimpleQueue(),
                state_update=_state_update)
        except Exception as exc:                              # noqa: BLE001
            logger.exception(
                "coupling loop: the closing re-solve FAILED — the network is "
                "left on the last iterate's cap and the foreground results do "
                "not describe the plan the verdict is about")
            _restore_word[0] = f"raised: {exc}"
            return False
        # The CONDITION, through the shared predicate — never `status`. This
        # read `status in ("ok", "optimal")`, and linopy's `SolverStatus.ok`
        # also covers `time_limit`, `iteration_limit`, `terminated_by_limit`,
        # `suboptimal` and `imprecise`: a closing re-solve that hit the MIP
        # time limit (`mip_time_limit_s` is a shipped setting) reported `ok`,
        # so the panel said "restored" while the foreground was a time-limited
        # dispatch. The frontier and the contingency sweep had the same bug and
        # it was found there first (12e shipped-code review, S1); this is the
        # same defect in the two loops, fixed the same way and through the same
        # one predicate rather than a fourth copy of the vocabulary.
        word = str(condition or status)
        _restore_word[0] = word
        if not restore_is_clean(word):
            logger.warning(
                "coupling loop: the closing re-solve returned %r — it ran but "
                "did not restore the plan the verdict is about", word)
        return restore_is_clean(word)

    def _verdict_copy(status: str, eps_star, rows=None) -> str:
        _margin_bound = _margin_bound_flag[0]
        if status == "unreachable":
            # WHICH unreachable? The three-mechanism copy assumes the cap was
            # doing something and the MC disagreed with it. The commonest case
            # in practice (QA round S17) is that the cap never bound at all —
            # the LP sheds nothing at any ceiling because its outage-free view
            # already covers demand — and telling that user to check storage
            # foresight and DSR sends them after mechanisms that are not
            # happening. It is diagnosable from the rows, so diagnose it.
            solved = [r for r in (rows or [])
                      if r.get("solve_status") in ("ok", "optimal")]
            if solved and not any(r.get("binding") == "system_cap"
                                  for r in solved):
                # WHICH never-bound? `report.binding` is computed purely from
                # the ENS caps, so it reads "voll" even when a reserve margin
                # is what actually shaped the plan. Prescribing a margin to a
                # user who already set one reads as the tool not knowing what
                # they configured — the diagnosis is right, only the
                # recommendation is stale. The margin's own block says whether
                # it was in force, so ask it.
                if _margin_bound:
                    return NEVER_BOUND_WITH_MARGIN_COPY_V1
                return NEVER_BOUND_COPY_V1
            return UNREACHABLE_COPY_V1
        if status == "met" and eps_star is not None:
            # `format_lever_value`, never `%g` — and never the badge's two
            # significant figures either. The panel's own restore explainer
            # prints this same number, and the two disagreed IN THE SAME
            # PANEL: the verdict said 0.0347281 where the explainer said
            # 0.035. An ENS cap is a CEILING on unserved energy, so a value
            # rounded UP is a strictly LOOSER standard that need not
            # reproduce the certified plan. One number, spelled once.
            cap_text = format_lever_value(eps_star)
            if restore == "final":
                return (
                    f"A plan meeting {target:g} h was verified at ε* = "
                    f"{cap_text}‱, and that cap has been APPLIED to your "
                    f"solver settings (ens_cap_permyriad = {cap_text}) and "
                    "re-solved — the network you are holding is the certified "
                    "plan.")
            return (
                f"A plan meeting {target:g} h was verified at ε* = "
                f"{cap_text}‱. Your original config has been re-solved, so "
                "the network you are holding is NOT that plan: to keep it, set "
                f"ens_cap_permyriad = {cap_text} and re-solve.")
        if status == "aborted":
            return ("The study was aborted between iterates. Any iterates "
                    "already evaluated are shown; the closing restore ran, so "
                    "the network is back on your own config.")
        if status == "budget_exhausted":
            return (
                f"The solve budget ({max_solves}) was spent without verifying "
                "a plan that meets the target. Nothing here says the target is "
                "unreachable — only that this search did not reach it. Raise "
                "max_solves, or start from a tighter eps0.")
        return ("The study did not complete. The iterates recorded below are "
                "what it managed before it stopped.")

    def worker():
        res: dict | None = None
        err: str | None = None
        try:
            try:
                res = run_coupling_loop(
                    solve_at, evaluate, target_lole_h=target, eps0=eps0,
                    max_solves=max_solves, stop_event=stop_event,
                    on_iteration=on_iteration)
            except BaseException as exc:                      # noqa: BLE001
                # The controller is total by construction; this is the belt
                # for a broken binding above, and it must never leave the
                # record stuck on "running" for the rest of the session.
                logger.exception("coupling loop: the controller raised")
                err = str(exc)
        finally:
            status = (res or {}).get("status") or "failed"
            eps_star = (res or {}).get("eps_star")
            try:
                base_restored = _restore_closing(status == "met", eps_star)
            except BaseException:                             # noqa: BLE001
                logger.exception("coupling loop: the restore itself raised")
                base_restored = False
            iterations = (res or {}).get("iterations")
            if iterations is None:
                iterations = record["iterations"]
            warning = MC_WARNING_V1 + " " + LOOP_WARNING_V1
            if any(len((r.get("mc") or {}).get("by_period") or {}) > 1
                   for r in iterations):
                warning += " " + MULTI_PERIOD_WARNING_V1
            # ONE atomic apply, under the same lock `on_iteration` rebinds
            # under: a GET landing between the status flip and the verdict
            # would otherwise serve a finished study with a running study's
            # empty final, which is the one shape the panel cannot render.
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status=status,
                    iterations=iterations,
                    final=(res or {}).get("final"),
                    confident=bool((res or {}).get("confident")),
                    eps_star=eps_star,
                    solves_used=int((res or {}).get("solves_used") or 0),
                    resolution_floor_h=eval_state["floor"],
                    base_restored=bool(base_restored),
                    # The solver's word, so a restore that RAN and still did
                    # not put the plan back can be named rather than merely
                    # denied (12e shipped-code review, S1).
                    base_restore_status=_restore_word[0],
                    verdict=_verdict_copy(
                        status, eps_star,
                        (res or {}).get("iterations")),
                    warning=warning,
                    error=err,
                    finished_at=time.time(),
                )

    # The worker carries the REQUEST's context (the /simulation/run pattern):
    # the active project lives in a ContextVar and a bare Thread does not
    # inherit it, so without this the closing restore's `_state_update` and
    # the `restore="final"` config write would land in the PROCESS foreground
    # — a different project's state from the one the caller is polling. The
    # study record itself is CLOSED OVER rather than reached through `_state`
    # (post_mc's pattern), so it cannot be redirected by a context switch at
    # all; post_frontier's in-thread `_state["frontier"].update(...)` is the
    # anti-pattern this deliberately does not copy.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-coupling-loop")
    record["thread"] = t
    # Publish and START under one lock hold. `_study_running` tests
    # `thread.is_alive()`, and a registered-but-not-yet-started thread reports
    # False — so a second POST arriving in that window would read the record as
    # stale state, claim the surface, and put two loops on the same network.
    _publish_study("coupling_loop", record, t)
    return {"status": "running", "target_lole_h": target, "draws": draws,
            "seed": seed, "eps0": eps0, "max_solves": max_solves,
            "restore": restore, "basis": basis,
            "resolution_floor_h": floor_h}


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

# The search's own caveats — properties of THIS lever's search, not of the MC.
MARGIN_LOOP_WARNING_V1 = (
    "The map from the reserve margin to MC-LOLE is a step function, not a "
    "curve: a range of margins forces the identical build and therefore the "
    "identical LOLE, so m* is the cheapest margin this search VERIFIED, not "
    "the smallest margin that would still pass. Every iterate is a genuine "
    "optimum of its own constrained problem, but only iterates whose own MC "
    "evaluation met the target are answers — the bracket is a search "
    "heuristic. The margin buys FIRM CAPACITY against a peak the LP already "
    "covers deterministically, which is why it can move a number no energy "
    "cap can; it does not buy energy, so a network short of energy rather "
    "than of capacity will not respond to it."
)

MARGIN_MULTI_PERIOD_WARNING_V1 = (
    "This network has more than one period: the margin is enforced per "
    "period against each period's own peak, while the target is a horizon "
    "SUM of LOLE — a single scalar margin cannot say 'fix period 3 only', so "
    "an unreachable or budget-exhausted verdict is structurally likelier "
    "here. The per-iterate by_period rows are the diagnostic. Note also that "
    "when the ACTIVE EXTENDABLE SET is identical in every period the LP has "
    "one horizon-wide nominal variable, so the standard degenerates to a "
    "single one at the largest peak (the report's `horizon_wide` flag)."
)

# The probe of §2.3 runs at the user's own margin — but at exactly 0 there is
# no standard at all (`_prm_margin` reads `<= 0` as "no margin"), the wrapper
# installs nothing and the report carries no reserve-margin block, so there
# would be nothing to read the incumbent's tight margin OFF. A margin this
# small is numerically "no margin" for every purpose except that it makes the
# standard — and therefore its report block — exist.
PROBE_MARGIN = 1e-4


class MarginLoopRequest(_BaseModel):
    # No `m0`: the starting margin is not a user parameter but a MEASUREMENT
    # (§2.3, the probing solve). A user-supplied start would be the one number
    # in this request that can silently make the study worthless — too small
    # and the search walks through a region where the plan does not change,
    # too large and it overshoots the bracket entirely.
    target_lole_h: _Finite | None = None
    draws: int | None = None
    seed: int | None = None
    max_solves: int | None = None
    restore: str | None = None


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
    import dataclasses
    import hashlib
    import queue as _queue
    import time

    from services.adequacy.coupling import MAX_LOOP_SOLVES, run_coupling_loop
    from services.adequacy.lever_text import format_lever_value
    from services.adequacy.margin_lever import (
        MAX_MARGIN,
        STEP_OVERSHOOT,
        to_margin,
        to_x,
    )
    from services.adequacy.mc import (
        MAX_DRAWS,
        MC_WARNING_V1,
        mc_adequacy,
        snapshot_inputs,
    )
    from services.adequacy.metrics import horizon_years, resolve_time_basis
    from services.adequacy.sweep import _solve_once
    from services.solver_service import _prm_margin, reserve_margin_facts
    from services.validation_service import (
        _check_nonfinite_bounds,
        _check_reserve_margin,
    )
    from routers.simulation import _state_update

    # ── the 409 mesh ──────────────────────────────────────────────────────
    _refuse_if_mesh_busy("margin_loop")

    # ── the synchronous 422 set (§2.4) ────────────────────────────────────
    target = getattr(body, "target_lole_h", None)
    try:
        target = float(target) if target is not None else None
    except (TypeError, ValueError):
        target = None
    if target is None or not (target > 0):
        raise HTTPException(
            422,
            "target_lole_h is required and must be > 0: the loop searches for "
            "the cheapest reserve margin whose plan meets a RELIABILITY "
            "STANDARD, and a target of zero (or none) is not a standard — it "
            "is the demand that no draw ever sheds an hour, which no finite "
            "plan can buy")

    draws = getattr(body, "draws", None)
    draws = 500 if draws is None else int(draws)
    if draws < 1:
        raise HTTPException(422, "draws must be a positive number of samples")
    if draws > MAX_DRAWS:
        raise HTTPException(
            422,
            f"draws={draws} exceeds the engine cap of {MAX_DRAWS} draws per "
            "evaluation — and the loop pays that cost once per iterate")
    seed = getattr(body, "seed", None)
    seed = 0 if seed is None else int(seed)

    max_solves = getattr(body, "max_solves", None)
    max_solves = MAX_LOOP_SOLVES if max_solves is None else int(max_solves)
    if not (1 <= max_solves <= MAX_LOOP_SOLVES):
        raise HTTPException(
            422,
            f"max_solves must be between 1 and {MAX_LOOP_SOLVES} (got "
            f"{max_solves}) — each solve is a full capacity expansion, so the "
            "budget is the wall-clock promise this request makes (the probing "
            "solve of the informed step is one more, outside it)")

    restore = getattr(body, "restore", None) or "base"
    if restore not in ("base", "final"):
        raise HTTPException(
            422,
            f"restore must be 'base' or 'final' (got {restore!r}): 'base' "
            "re-solves with your original config, 'final' leaves you holding "
            "the certified plan at m*")

    cfg = _state.get("solver_config")
    if cfg is None:
        raise HTTPException(
            422, "no solver configuration is set for this project — the loop "
                 "builds every iterate from it")

    # NO VoLL REQUIREMENT (§2.4). The cap loop needs one because without
    # load-shedding slacks its lever constrains nothing; the margin is a
    # CONSTRAINT on installed firm capacity and binds whether or not unserved
    # energy carries a price.

    strategy = str(getattr(cfg, "solve_strategy", "full") or "full")
    if strategy == "rolling":
        raise HTTPException(
            422,
            "the reserve margin is not supported with the 'rolling' solve "
            "strategy: PyPSA solves each window independently, so the "
            "constraint would be built against that WINDOW's peak demand "
            "rather than the period's — a weaker standard than the one you "
            "set, enforced under its name. Every iterate would fail the same "
            "blocking validation and the loop would report 'unreachable', "
            "which is a statement about the strategy, not about the network. "
            "Use the full strategy. ('myopic' IS supported: each iteration's "
            "snapshots are exactly one investment period, which is the peak "
            "the standard is defined against — only its report is partial.)")

    # The ONE snapshot the validation reads, taken under the mutation lock.
    # `keep_zero_capacity=True` from the very first call (coupling spec §1.2):
    # the sampled fleet's MEMBERSHIP must be invariant across iterates or the
    # positional CRN substreams shift under it — and it is what keeps the
    # UNBUILT peaker, the very asset a margin exists to force into being, in
    # the fleet at all.
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()
    with lock:
        try:
            # Phase 12c-0: the LP's demand basis — the plan the loop
            # certifies was built on it (the fifteenth finding).
            inputs = snapshot_inputs(n, keep_zero_capacity=True, cfg=cfg)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        nyears = float(horizon_years(n))

    if not inputs.units:
        raise HTTPException(
            422,
            "nothing to sample: no electrical generator carries resolvable "
            "occurrence data (unavailability + MTTR), so the sampled fleet is "
            "empty — an empty fleet would report the entire horizon as loss of "
            "load, which is a statement about missing input data, not about "
            "the system")

    # The up-front resolution floor. One shortfall hour in one draw
    # contributes that hour's WEIGHT to the mean, so the smallest non-zero
    # LOLE these draws can resolve is `min positive weight / draws`. A target
    # under it is undecidable.
    _w = inputs.weights[inputs.weights > 0]
    floor_h = (float(_w.min()) / draws) if _w.size else None
    if floor_h is not None and target < floor_h:
        need = int(math.ceil(float(_w.min()) / target))
        raise HTTPException(
            422,
            f"target_lole_h={target:g} h is below this study's resolution "
            f"floor of {floor_h:g} h at {draws} draws: a single shortfall "
            "hour in a single draw already exceeds it, so no verdict here "
            "could distinguish a compliant plan from a lucky sample. About "
            f"{need} draws would resolve it.")

    base_cfg = cfg
    basis = resolve_time_basis(nyears)

    # ── the facts the standard knows before an LP exists (§2.4) ───────────
    #
    # `reserve_margin_facts` returns None for a config with no margin set, so
    # the preflight reads it at a NOMINAL margin. Only `required_mw` scales
    # with that number; the peaks, the derates, the unpriceable list and
    # `max_achievable_mw` — everything read below — do not.
    m_user = _prm_margin(base_cfg) or 0.0
    probe_margin = max(m_user, PROBE_MARGIN)
    facts_cfg = dataclasses.replace(base_cfg, reserve_margin=probe_margin)
    with lock:
        try:
            facts = reserve_margin_facts(n, facts_cfg)
        except Exception as exc:                              # noqa: BLE001
            raise HTTPException(
                422,
                "the firm-capacity standard could not be measured on this "
                f"network, so the loop has no ceiling to search under: {exc}"
            ) from exc
        # Unpriceable assets — refused with the VALIDATOR'S OWN SENTENCE, not
        # a second one. The loop's own gate would pass (it needs one priceable
        # unit, not all of them) and then EVERY iterate would fail the same
        # blocking validation, ending `budget_exhausted` and advising "raise
        # max_solves", which can never work here.
        margin_issues = _check_reserve_margin(n, facts_cfg)
        # Phase 12f: the SAME up-front refusal, for the same reason. A
        # non-finite value in one of the five finite-default LP bounds is a
        # blocking preflight error, so every iterate would fail validation and
        # the loop would end `budget_exhausted` advising "raise max_solves" —
        # `_margin_out_of_reach` only relabels `validation_failed` when the
        # MARGIN is the cause, and it is not here. This check has to be CALLED,
        # not merely allowed through the filter below: `_check_reserve_margin`
        # is one sub-validator, not `validate_for_run`, so a code it never
        # produces can never appear in `margin_issues`.
        margin_issues = margin_issues + _check_nonfinite_bounds(n)
    for iss in margin_issues:
        # Phase 12g: every `nonfinite_*` code, by prefix. The first version
        # listed the two 12f codes literally, so each category 12g adds would
        # have slipped past this guard and the loop would have spent its
        # budget refusing — the K6 outcome this guard exists to prevent.
        if iss.code == "reserve_margin_unpriceable_assets" \
                or iss.code.startswith("nonfinite_"):
            raise HTTPException(422, iss.message)

    # The ceiling: a margin is achievable iff EVERY period can reach it, so
    # the binding period is the one that fails first and the aggregate is
    # `min`, not `max` (plan §2.2 — `max` would let the search run past a
    # margin one period already makes impossible). `max_achievable_mw` is
    # `inf` on the ordinary network (PyPSA's default `p_nom_max`), where the
    # ceiling is the schema's own bound instead.
    m_max = math.inf
    m_max_where: tuple[str, float, float] | None = None
    for P, per in ((facts or {}).get("stash", {}).get("periods") or {}).items():
        try:
            peak = float(per.get("peak_mw") or 0.0)
            reach = float(per.get("max_achievable_mw") or 0.0)
        except (TypeError, ValueError):
            continue
        if peak <= 0:
            continue
        here = reach / peak - 1.0
        if here < m_max:
            m_max, m_max_where = here, (str(P), peak, reach)
    if m_max <= 0:
        where = ""
        if m_max_where is not None and m_max_where[0] != "ALL":
            where = f" in period {m_max_where[0]}"
        peak_mw = m_max_where[1] if m_max_where else 0.0
        reach_mw = m_max_where[2] if m_max_where else 0.0
        raise HTTPException(
            422,
            f"no reserve margin is reachable on this network{where}: the "
            f"whole fleet — every extendable at its p_nom_max, derated — tops "
            f"out at {reach_mw:,.1f} MW against a {peak_mw:,.1f} MW peak, a "
            f"margin of {m_max:.1%}, so even a margin of 0 % (firm capacity "
            "equal to the peak) is out of reach. Every iterate would be "
            "refused by the same blocking preflight the solver runs, and the "
            "loop would spend its whole budget proving it. Raise a p_nom_max, "
            "add candidate capacity, or enter outage data for assets the "
            "standard currently cannot price.")
    # The search's own upper bound: the fleet ceiling, or the schema's `le=5`
    # when the fleet is unbounded.
    m_ceiling = min(m_max, MAX_MARGIN)

    # ── the bindings (§2.1) ───────────────────────────────────────────────

    def _snapshot():
        # `base_cfg` is captured in the request — the worker never reads
        # `_state` — and the scalers do not change across iterates.
        with lock:
            return snapshot_inputs(n, keep_zero_capacity=True, cfg=base_cfg)

    def _hash(mc_inputs) -> str:
        """sha256 over exactly what the MC reads — the sorted
        ``(name, capacity_mw)`` unit vector, the sorted
        ``(name, p_nom_mw, e_nom_mwh)`` storage vector, and the residual
        bytes. NOT the objective: degenerate optima give equal cost for
        different plans. Equal hash ⇒ bit-identical MC under the same seed and
        draw count, so the controller's plateau reuse is exact."""
        # Phase 12d: one implementation for both loops, testable
        # (`tests/test_adequacy_activity.py` E8).
        return _snapshot_hash(mc_inputs)

    def _margin_out_of_reach(m: float) -> str | None:
        """Is THIS margin impossible from the candidate set — the same
        constant arithmetic `_check_reserve_margin` blocks on, asked at a
        specific margin (§2.5)?

        A second implementation of the derating chain here would be a second
        standard, so the numbers come from `reserve_margin_facts` — the very
        function the validator and the LP wrapper share.
        """
        try:
            with lock:
                f = reserve_margin_facts(
                    n, dataclasses.replace(base_cfg, reserve_margin=float(m)))
        except Exception:                                     # noqa: BLE001
            return None
        if not f:
            return None
        for P, per in (f["stash"].get("periods") or {}).items():
            try:
                required = float(per.get("required_mw") or 0.0)
                reach = float(per.get("max_achievable_mw") or 0.0)
            except (TypeError, ValueError):
                continue
            if required <= 0 or not math.isfinite(required):
                continue
            if reach < required:
                where = "" if str(P) == "ALL" else f" in period {P}"
                return (
                    f"infeasible: no plan built from this candidate set "
                    f"reaches a {m:.1%} reserve margin{where} — it needs "
                    f"{required:,.1f} MW of derated firm capacity and the "
                    f"whole fleet tops out at {reach:,.1f} MW")
        return None

    _ceiling_missed = [False]
    _last_at_ceiling = [False]
    # IEEE 39-bus review, F1. The CLAMP below evaluates `m_ceiling` when the
    # controller's step overshoots the fleet ceiling — but the controller
    # records the coordinate it ASKED for (`coupling.py:_row`), and that
    # coordinate is what `_translate`, `lever_star`, the verdict and the
    # `restore="final"` config write all read. Measured on the stressed
    # IEEE 39 network (ceiling 19.9 %): rows 15.75 % -> 363 % -> 131.5 %,
    # verdict "verified at a reserve margin of 131.5%", `reserve_margin` left
    # at 1.315, and the closing re-solve refused by the study's own preflight
    # (`reserve_margin_unreachable`). The margin ACTUALLY solved is recorded
    # here, keyed by the controller's `x`, and consulted at the two places a
    # coordinate becomes a user-facing margin. The controller never solves one
    # `x` twice and the pre-controller probe never becomes a row, so the map is
    # unambiguous; an `x` not in it (a solve refused before it ran) still
    # translates exactly as before.
    _solved_margin: dict[float, float] = {}

    def solve_at(x: float) -> dict:
        _last_at_ceiling[0] = False
        """One capacity-expansion solve at the margin ``x`` stands for, read
        out exactly as ``run_frontier_sweep`` reads its points. Solve failures
        come back as a status — the controller is specified never to see an
        exception from here, and a raise would cost the whole study."""
        m = to_margin(x)
        out = {"status": None, "condition": None, "cost_eur": None,
               "ens_mwh": None, "cap_mwh": None, "binding": None,
               "report": None}

        if m > MAX_MARGIN * (1.0 + 1e-9):
            # The controller's blind step multiplies the margin by ~4 per
            # iterate, so it can walk past the schema's own bound in two
            # steps. Solving there would build a plan against a margin the
            # config schema refuses — and `restore="final"` would then persist
            # a value the next PUT rejects. Stopping is honest and free.
            out.update(
                status="error",
                condition=(
                    f"infeasible: a reserve margin of {m:.1%} is beyond the "
                    f"configured maximum of {MAX_MARGIN:.0%} — the search has "
                    "run out of lever, not out of budget"))
            return out

        # THE CLAMP, and it is the difference between a verdict and a guess.
        # The controller's blind step multiplies the margin ~4x per iterate, so
        # from a small start it can leap clean over `m_ceiling` — and an
        # over-ceiling solve fails validation, gets relabelled `infeasible`
        # below, and the nesting logic then (correctly, given what it was told)
        # concludes every stricter margin is infeasible too. The loop reports
        # `unreachable` having never evaluated the reachable region at all.
        # Found live in S19.3: ceiling 271%, last evaluated margin 18%, verdict
        # "unreachable" — with a plan that meets the target sitting between
        # them. Clamping makes the strictest REACHABLE margin the thing that
        # gets evaluated, so an `unreachable` verdict is one this study
        # actually verified.
        if m > m_ceiling:
            if _ceiling_missed[0]:
                # Already evaluated AT the ceiling and it missed. Nothing
                # stricter exists to try, so this is a real refusal rather
                # than another clamp to the same plan.
                out.update(
                    status="error",
                    condition=(
                        f"infeasible: the strictest reachable margin "
                        f"({m_ceiling:.1%}) was evaluated and still missed the "
                        "target — the candidate set, not the search, is the "
                        "limit"))
                return out
            m = m_ceiling
            _last_at_ceiling[0] = True
        _solved_margin[float(x)] = float(m)
        sink: dict = {}
        _solve_once(dataclasses.replace(base_cfg, reserve_margin=m),
                    n, lock, None, sink)
        status = sink.get("_status")
        condition = sink.get("_condition")
        out.update(status=status, condition=condition)

        if status not in ("ok", "optimal"):
            # §2.5. An out-of-reach margin is a BLOCKING PREFLIGHT ERROR, not
            # an infeasible LP (linopy raises TypeError on a constant
            # constraint and `Generator-p_nom` does not exist when nothing
            # extendable is active), so it arrives as `validation_failed` —
            # which `_is_infeasible` matches on neither the status nor the
            # condition. The controller would treat it as transient, keep
            # stepping, and end `budget_exhausted` advising "raise
            # max_solves", which can never work. Relabel it — but ONLY when
            # the facts confirm the margin is the cause: a validation failure
            # from anything else is not monotone in the margin and proves
            # nothing about tighter ones.
            if "validation_failed" in str(condition).lower():
                why = _margin_out_of_reach(m)
                if why is not None:
                    out["condition"] = why
            return out

        rep = sink.get("adequacy_report")
        if not rep:
            # A margin WAS set and the solve succeeded, so the report is the
            # contract. Its absence is a defect, and reporting it as a failed
            # iterate keeps the loop from evaluating a plan it cannot describe.
            out.update(status="no_report",
                       condition="the solve returned no adequacy report")
            return out

        # §2.1: `binding` comes from the MARGIN's own block. `target.binding`
        # is computed purely from the ENS caps and reads "voll" on every
        # margin run, which would make the controller's `reusable` pre-test
        # (`binding != "system_cap"`) permanently true and offer plateau reuse
        # on iterates where the margin demonstrably rebuilt the plan.
        rm_rows = ((rep.get("reserve_margin") or {}).get("by_period")) or []
        binding = ("system_cap" if any(bool(r.get("binding")) for r in rm_rows)
                   else "voll")

        metrics_blk = rep.get("metrics") or {}
        ens = metrics_blk.get("ens_mwh")
        if ens is None:
            ens = ((rep.get("target") or {}).get("system") or {}).get(
                "achieved_ens_mwh")
        try:
            ens = float(ens) if ens is not None else None
        except (TypeError, ValueError):
            ens = None

        out.update(
            report=rep,
            cost_eur=float(rep["cost"]["total_system_cost_eur"]),
            ens_mwh=ens,
            # §2.2, and it is LOAD-BEARING. The controller ends the search
            # with `unreachable` when `cap_mwh is not None and cap_mwh <
            # ENERGY_FLOOR_MWH`. On a margin-only report `cap_mwh` is `0.0` —
            # the ENS cap's per-period loop never runs, so `SystemTarget.
            # cap_mwh` is emitted as its initialised zero — so passing it
            # through fires that test on the FIRST miss and every run ends
            # `unreachable` after one solve, indistinguishable in the payload
            # from the real thing. None makes the test a genuine no-op, which
            # is the only correct reading for a lever with no energy cap.
            cap_mwh=None,
            binding=binding,
        )
        return out

    eval_state: dict = {"floor": floor_h}

    def evaluate():
        """IDENTICAL to the coupling loop's (§2.1): the same snapshot with
        `keep_zero_capacity=True`, the same pinned
        `mc_adequacy(inputs, draws=N, seed=S, max_draws=N)` call — merely
        ignoring `cov_target` would leave the adaptive cap in play and
        `n_samples` would drift between iterates, breaking the common random
        numbers the plateau reuse rests on — and the same plan hash."""
        mc_inputs = _snapshot()
        # `stop_event` is NEVER passed here: this is a REPLAY of one batch
        # sequence (see `mc_adequacy`'s note), and the loop's own abort is
        # checked between iterates, never inside an evaluation.
        metrics = mc_adequacy(mc_inputs, draws=draws, seed=seed,
                              max_draws=draws, stop_event=None)
        try:
            eval_state["floor"] = metrics.get("resolution_floor_h")
        except AttributeError:                                # noqa: BLE001
            pass
        return _hash(mc_inputs), metrics

    def _plan_hash():
        return _hash(_snapshot())

    evaluate.plan_hash = _plan_hash

    def _position_x0() -> tuple[float, float | None]:
        """§2.3 — the informed step, done by PRE-POSITIONING x0 rather than by
        changing the controller.

        With `cap_mwh=None` the controller's informed term is skipped and
        `_tighten` degrades to the blind `x/4`, which in margin terms is
        `m: 0 → 3` — a large but safe first jump that spends a solve learning
        nothing when the incumbent plan already carries a comfortable margin.
        So the route measures the incumbent first:

            m_tight = min over P of (firm_mw_P / peak_mw_P) − 1
            x0      = to_x(m_tight · (1 + STEP_OVERSHOOT))

        `m_tight` is the smallest margin at which the incumbent plan is TIGHT
        — at exactly that value the plan is feasible, unchanged, same hash,
        same LOLE, and flagged `binding` while nothing moved — so the step
        must STRICTLY exceed it, hence the overshoot. The aggregate is `min`
        because the first period to bind is the binding one; `max` would step
        past it and overshoot the bracket entirely.

        Returns ``(x0, m_tight)``.
        """
        res = solve_at(to_x(probe_margin))
        tights: list[float] = []
        if res.get("status") in ("ok", "optimal"):
            rows = ((res.get("report") or {}).get("reserve_margin") or {}).get(
                "by_period") or []
            for row in rows:
                try:
                    peak = float(row.get("peak_mw") or 0.0)
                    firm = float(row.get("firm_mw") or 0.0)
                except (TypeError, ValueError):
                    continue
                if peak > 0 and math.isfinite(firm):
                    tights.append(firm / peak - 1.0)
        if not tights:
            # No measurement (a failed probe, or a report with no usable
            # period): fall back to the user's own margin and let the
            # controller's blind step do the searching. A guess here would be
            # a worse start than no start.
            logger.info("margin loop: no tight margin measurable from the "
                        "probing solve; starting from the configured margin")
            m_start = min(max(probe_margin, STEP_OVERSHOOT), m_ceiling)
            return to_x(max(m_start, PROBE_MARGIN)), None
        m_tight = min(tights)
        base = max(m_tight, 0.0)
        m_start = base * (1.0 + STEP_OVERSHOOT)
        if not m_start > base:
            # `base == 0`: the incumbent is tight at (or below) a zero margin,
            # where a multiplicative overshoot is still zero — and a margin of
            # 0 installs no standard at all. The smallest step that changes
            # anything is the overshoot itself.
            m_start = STEP_OVERSHOOT
        m_start = max(min(m_start, m_ceiling), PROBE_MARGIN)
        return to_x(m_start), m_tight

    def _translate(row: dict) -> dict:
        """§2.6: the controller's `eps_permyriad` IS the substitution's `x`,
        an internal coordinate with no meaning to a user — 0.76 is not a
        margin, not a percentage and not a per-myriad ENS cap. Every row is
        translated on its way into the record, so `x` never reaches the wire
        at all.

        F1 (IEEE 39-bus review): the margin SOLVED, not the one the
        controller asked for — they differ on a clamped iterate."""
        return {
            "lever_value": _solved_margin.get(
                float(row["eps_permyriad"]), to_margin(row["eps_permyriad"])),
            "solve_status": row["solve_status"],
            "condition": row["condition"],
            "cost_eur": row["cost_eur"],
            "ens_mwh": row["ens_mwh"],
            "cap_mwh": row["cap_mwh"],
            "binding": row["binding"],
            "plateau": row["plateau"],
            "mc": row["mc"],
        }

    # ── the record (closed over, never reached through `_state`) ──────────
    stop_event = _threading.Event()
    record: dict = {
        "study": "margin_loop",
        # The frontend discriminator (spec §3): the column header, the badge
        # suffix and `restoreSentence`'s CONFIG FIELD NAME all come off these,
        # so a margin run can never tell the user to set the cap's field.
        "lever": "reserve_margin",
        "lever_label": "planning reserve margin",
        "lever_unit": "%",
        "status": "running",
        "target_lole_h": target,
        "basis": basis,
        "horizon_years": nyears,
        "draws": draws,
        "seed": seed,
        "margin0": None,
        "margin_tight": None,
        # M7: the FLEET ceiling, null when unbounded — `m_ceiling` also
        # carries the schema cap the search stops at, which is not a
        # ceiling any fleet has.
        "margin_ceiling": (None if not math.isfinite(m_max)
                           else float(m_max)),
        "max_solves": max_solves,
        "restore": restore,
        "base_restored": False,
        "confident": False,
        "lever_star": None,
        "resolution_floor_h": floor_h,
        "solves_used": 0,
        # The probing solve of §2.3 is OUTSIDE the controller's budget, so it
        # is reported separately rather than folded into `solves_used`: the
        # budget is a promise about the search, and a user timing the run
        # should be able to account for every solve it made.
        "probe_solves": 0,
        "iterations": [],
        "final": None,
        "verdict": None,
        "warning": MC_WARNING_V1 + " " + MARGIN_LOOP_WARNING_V1,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": stop_event,
    }

    def on_iteration(row: dict) -> None:
        """Grow the record by REBINDING, never by appending in place.

        `get_margin_loop` serves a shallow copy, so an in-place append hands
        the serializer the very list this thread is mutating: a mid-run GET
        can then be written half-way through an append, and a client polling
        every second can watch its own earlier history change. A fresh list
        per iterate makes every snapshot immutable by construction.
        """
        # Did the iterate that just finished sit AT the ceiling and miss? If
        # so no stricter margin exists to try, and `solve_at` refuses the next
        # request outright rather than clamping to the same plan forever.
        mc = row.get("mc") or {}
        lole = mc.get("lole_hours")
        if (_last_at_ceiling[0] and lole is not None
                and float(lole) > target):
            _ceiling_missed[0] = True
        with PyPSAService.get_solver_state_lock():
            record["iterations"] = record["iterations"] + [_translate(row)]

    # The solver's own word on the closing re-solve, for the payload. A cell
    # rather than a return value because `_restore_closing` returns a bool
    # that four call sites already read (12e shipped-code review, S1).
    _restore_word: list = [None]

    def _restore_closing(met: bool, m_star) -> bool:
        """The closing re-solve — the route's job, on EVERY path. The loop
        mutates the network once per iterate, so without this it is left on
        whichever margin happened to be last while the foreground results
        still describe the pre-study solve.

        `"final"` writes **`reserve_margin`** and nothing else: a user-set ENS
        cap is carried through untouched (every config here is built from
        `base_cfg`), because the study tuned one standard and the user asked
        for both.
        """
        from services.adequacy.sweep import restore_is_clean
        from services.solver_service import run_simulation

        use_final = (restore == "final" and met and m_star is not None)
        if use_final:
            final_cfg = dataclasses.replace(
                base_cfg, reserve_margin=float(m_star))
            # Persisted through the normal config path (read-modify-write
            # under the solver-state lock, exactly as PUT /solver_config does)
            # BEFORE the solve: the user asked to hold m*, and a restore whose
            # solve fails must still leave the setting they will re-run with,
            # with `base_restored` reporting the failure.
            with PyPSAService.get_solver_state_lock():
                _state["solver_config"] = dataclasses.replace(
                    _state["solver_config"], reserve_margin=float(m_star))
        else:
            final_cfg = base_cfg
        try:
            status, condition = run_simulation(
                final_cfg, n, lock, _threading.Event(), _queue.SimpleQueue(),
                state_update=_state_update)
        except Exception as exc:                              # noqa: BLE001
            logger.exception(
                "margin loop: the closing re-solve FAILED — the network is "
                "left on the last iterate's margin and the foreground results "
                "do not describe the plan the verdict is about")
            _restore_word[0] = f"raised: {exc}"
            return False
        # The CONDITION, through the shared predicate — see the coupling
        # loop's twin for why the status is the wrong half to read.
        word = str(condition or status)
        _restore_word[0] = word
        if not restore_is_clean(word):
            logger.warning(
                "margin loop: the closing re-solve returned %r — it ran but "
                "did not restore the plan the verdict is about", word)
        return restore_is_clean(word)

    def _both_standards_clause() -> str:
        try:
            cap = float(getattr(base_cfg, "ens_cap_permyriad", 0.0) or 0.0)
        except (TypeError, ValueError):
            cap = 0.0
        if cap <= 0:
            return ""
        return (
            f" Your energy cap (ens_cap_permyriad = {cap:g}‱) was left in "
            "force for every iterate and was never rewritten, so the "
            "certified plan meets BOTH standards.")

    def _verdict_copy(status: str, m_star, rows=None) -> str:
        if status == "met" and m_star is not None:
            if restore == "final":
                return (
                    f"A plan meeting {target:g} h was verified at a reserve "
                    f"margin of {m_star:.1%}, and that margin has been "
                    f"APPLIED to your solver settings (reserve_margin = "
                    f"{format_lever_value(m_star)}) and re-solved — the "
                    "network you are holding "
                    "is the certified plan." + _both_standards_clause())
            return (
                f"A plan meeting {target:g} h was verified at a reserve "
                f"margin of {m_star:.1%}. Your original config has been "
                "re-solved, so the network you are holding is NOT that plan: "
                # `format_lever_value`, never `%g`: the panel's own
                # restore explainer prints this same number two lines
                # above, and `%g`'s six significant figures made the
                # two disagree IN THE SAME PANEL — the verdict said
                # 0.6716 where the explainer said 0.671600430725. A
                # margin is a THRESHOLD on required firm capacity, so
                # the shorter value is a strictly LOOSER standard that
                # need not reproduce the certified plan.
                f"to keep it, set reserve_margin = "
                f"{format_lever_value(m_star)} and re-solve."
                + _both_standards_clause())
        if status == "unreachable":
            ceiling = (f"{m_ceiling:.1%}" if math.isfinite(m_ceiling)
                       else "unbounded")
            reason = (
                "the largest margin your candidate set can reach"
                if m_max <= MAX_MARGIN else
                "the largest margin the configuration schema allows")
            return (
                "No reserve margin this search could reach produced a plan "
                f"that met {target:g} h on the MC's own LOLE. The search is "
                f"bounded above by {ceiling} — {reason} — and beyond it no "
                "plan exists at all: the margin is refused by the same "
                "blocking preflight the solver runs, rather than by an "
                "infeasible LP. Under that ceiling, three mechanisms produce "
                "a miss and they call for different responses: (a) the added "
                "firm capacity is DERATED by the same outage data the MC "
                "samples, so a fleet of unreliable units buys less than its "
                "nameplate; (b) the standard is enforced at the PEAK, while "
                "loss of load in the MC can fall in hours the peak-"
                "coincidence window never measured; (c) energy-limited "
                "resources take a duration haircut, so a plan that meets the "
                "margin on storage can still run out of energy in a long "
                "outage. Check the per-iterate binding column and the "
                "by_period rows, then consider raising a p_nom_max, adding "
                "candidate capacity, or lowering the target.")
        if status == "aborted":
            return ("The study was aborted between iterates. Any iterates "
                    "already evaluated are shown; the closing restore ran, so "
                    "the network is back on your own config.")
        if status == "budget_exhausted":
            return (
                f"The solve budget ({max_solves}) was spent without verifying "
                "a plan that meets the target. Nothing here says the target "
                "is unreachable — only that this search did not reach it. "
                "Raise max_solves.")
        return ("The study did not complete. The iterates recorded below are "
                "what it managed before it stopped.")

    def worker():
        res: dict | None = None
        err: str | None = None
        try:
            try:
                x0, m_tight = _position_x0()
                with PyPSAService.get_solver_state_lock():
                    record["probe_solves"] = 1
                    record["margin0"] = to_margin(x0)
                    record["margin_tight"] = m_tight
                res = run_coupling_loop(
                    solve_at, evaluate, target_lole_h=target, eps0=x0,
                    max_solves=max_solves, stop_event=stop_event,
                    on_iteration=on_iteration)
            except BaseException as exc:                      # noqa: BLE001
                # The controller is total by construction; this is the belt
                # for a broken binding above, and it must never leave the
                # record stuck on "running" for the rest of the session.
                logger.exception("margin loop: the controller raised")
                err = str(exc)
        finally:
            status = (res or {}).get("status") or "failed"
            x_star = (res or {}).get("eps_star")
            m_star = None
            if x_star is not None:
                try:
                    # F1: the margin that iterate actually solved (they differ
                    # when the ceiling clamp fired), so the verdict names — and
                    # `restore="final"` persists — a margin the preflight
                    # accepts.
                    m_star = _solved_margin.get(float(x_star),
                                                to_margin(x_star))
                except ValueError:                            # noqa: BLE001
                    logger.exception("margin loop: unusable eps_star %r",
                                     x_star)
            try:
                base_restored = _restore_closing(status == "met", m_star)
            except BaseException:                             # noqa: BLE001
                logger.exception("margin loop: the restore itself raised")
                base_restored = False

            src_rows = (res or {}).get("iterations")
            if src_rows is None:
                rows_out = record["iterations"]
                final_out = None
            else:
                rows_out = [_translate(r) for r in src_rows]
                final_src = (res or {}).get("final")
                # Identity, not equality: two rows CAN carry the same numbers
                # (a plateau iterate differs only in its lever value, and a
                # broken binding could make even that equal), and picking the
                # wrong one would report a different iterate as the answer.
                final_out = next(
                    (out for src, out in zip(src_rows, rows_out)
                     if src is final_src), None)

            warning = MC_WARNING_V1 + " " + MARGIN_LOOP_WARNING_V1
            if any(len((r.get("mc") or {}).get("by_period") or {}) > 1
                   for r in rows_out):
                warning += " " + MARGIN_MULTI_PERIOD_WARNING_V1
            # ONE atomic apply, under the same lock `on_iteration` rebinds
            # under: a GET landing between the status flip and the verdict
            # would otherwise serve a finished study with a running study's
            # empty final, which is the one shape the panel cannot render.
            with PyPSAService.get_solver_state_lock():
                record.update(
                    status=status,
                    iterations=rows_out,
                    final=final_out,
                    confident=bool((res or {}).get("confident")),
                    lever_star=m_star,
                    solves_used=int((res or {}).get("solves_used") or 0),
                    resolution_floor_h=eval_state["floor"],
                    base_restored=bool(base_restored),
                    base_restore_status=_restore_word[0],
                    verdict=_verdict_copy(status, m_star, rows_out),
                    warning=warning,
                    error=err,
                    finished_at=time.time(),
                )

    # The worker carries the REQUEST's context (the /simulation/run pattern):
    # the active project lives in a ContextVar and a bare Thread does not
    # inherit it, so without this the closing restore's `_state_update` and
    # the `restore="final"` config write would land in the PROCESS foreground
    # — a different project's state from the one the caller is polling.
    _ctx = _contextvars.copy_context()
    t = _threading.Thread(target=lambda: _ctx.run(worker), daemon=True,
                          name="adequacy-margin-loop")
    record["thread"] = t
    # Publish and START under one lock hold. `_study_running` tests
    # `thread.is_alive()`, and a registered-but-not-yet-started thread reports
    # False — so a second POST arriving in that window would read the record as
    # stale state, claim the surface, and put two loops on the same network.
    _publish_study("margin_loop", record, t)
    return {"status": "running", "study": "margin_loop",
            "lever": "reserve_margin", "target_lole_h": target,
            "draws": draws, "seed": seed, "max_solves": max_solves,
            "restore": restore, "basis": basis,
            "margin_ceiling": (None if not math.isfinite(m_max)
                               else float(m_max)),
            "resolution_floor_h": floor_h}


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
    from services.adequacy.activity import activity_summary as _activity_summary
    from services.adequacy.activity import period_blocks as _period_blocks
    from services.adequacy.copt import (
        K_EXACT,
        fleet_and_residual,
        is_flag_deterministic as _is_flag_deterministic,
        must_take_generators,
        rate_is_zero as _rate_is_zero,
        screening_analysis,
    )

    n = PyPSAService.get_network()
    cfg = _state.get("solver_config")
    # Phase 12c-0: under the mutation lock, like /mc — a solve scales the
    # load frame IN PLACE for its duration, and a bare read mid-solve saw a
    # half-transformed network (v3 review, finding 8); and on the LP's
    # demand basis.
    with PyPSAService.get_lock():
        try:
            units, residual, w = fleet_and_residual(n, cfg=cfg)
        except ValueError as exc:
            # Whole-branch review S1: an outage rate outside [0, 1) is
            # refused by the walk, named, and answered 422 — the same
            # answer /mc and both loops already give a ValueError here.
            raise HTTPException(422, str(exc)) from exc
        # …and the membership read for `must_take`, under the same hold
        # (12c-0 shipped-code review, finding 4).
        n_must_take = len(must_take_generators(n))
        # Phase 12d: the activity disclosure reads the NETWORK (must-take
        # farms and rows dropped at zero capacity are not in the fleet —
        # shipped-code review, finding 1), so it is taken under the same
        # hold as the fleet it describes.
        activity = _activity_summary(n, _period_blocks(residual.index))
    if not units:
        return Response(status_code=204)
    voll = float(getattr(cfg, "voll", 0.0) or 0.0)
    # Phase 12c-pre: split, net the remainder, table, mixture, attribution —
    # one call so this route and the engine tests see the same arithmetic.
    analysis = screening_analysis(units, residual, weights=w, voll=voll,
                                  delta_mw=1.0)
    metrics = analysis["metrics"]
    rows = analysis["rows"]
    split = analysis["split"]
    # The must-take count comes from the SAME walk that decided membership.
    # The previous `electrical non-slack gens − len(units)` subtraction
    # miscounted zero-capacity generators, which the walk skips and the
    # subtraction did not (plan 12c-pre v2 review, finding 8).
    from services.adequacy.metrics import horizon_years, resolve_time_basis
    _copt_nyears = horizon_years(n)
    _copt_basis = resolve_time_basis(_copt_nyears)
    return {
        "engine": "copt",
        "fidelity": "analytic_convolution",
        "metrics": {
            "lole_hours": metrics["lole_hours"],
            "eue_mwh": metrics["eue_mwh"],
            "lolp_max": metrics["lolp_max"],
            "by_period": metrics["by_period"],
            # Derived, not asserted. The COPT sums over whatever horizon the
            # model spans, weighted; calling that "hours_per_year" on a
            # 168 h week reported 80.86 for a system whose annual LOLE is
            # ~4216 — and understating LOLE is the direction that gets a
            # number compared to a 3 h/yr standard it has no relation to.
            "time_basis": _copt_basis,
            "horizon_years": _copt_nyears,
        },
        "per_mode": [
            {**r["failure_mode"],
             "delta_eue_mwh": r["delta_eue_mwh"],
             **({"note": r["note"]} if "note" in r else {})}
            for r in rows
        ],
        "fleet": {
            "units": len(units),
            "must_take": n_must_take,
            "delta_mw": 1.0,
            # Phase 12c-pre disclosure: which units carry a profile INTO the
            # sampled fleet, which of those were netted beyond the exact
            # cap, and the sentence that says so. `fidelity` above stays the
            # engine enum the comparison table keys on.
            "profile_units": [u.name for u in split.mixed] + [u.name for u in split.netted],
            "netted_beyond_cap": [u.name for u in split.netted],
            "k_exact": K_EXACT,
            # Phase 12h. Two units the lists above cannot describe:
            #  * a unit whose STATIC p_max_pu was folded into its capacity
            #    has no profile at all, so it is in no existing list — the
            #    `source` field is here so a later phase can add another
            #    fold without changing the shape;
            #  * a unit whose outage rate is zero because its availability
            #    is declared to include outages carries a profile but is in
            #    neither `mixed` nor `netted` — it is netted exactly, at
            #    full availability, and no outages are sampled for it.
            "folded_units": [
                {"name": u.name, "folded_constant": float(u.folded_constant),
                 "source": "static"}
                for u in units
                if getattr(u, "folded_constant", None) is not None],
            "deterministic_units": [u.name for u in units
                                    if _is_flag_deterministic(u)],
            # IEEE 39-bus review, F8. The OTHER way a unit reaches q = 0:
            # the user typed the rate as 0. M4 stopped calling that "the
            # flag", which was the defect — but on `/mc`, which has no rows
            # to carry a note, it then left such a unit named nowhere at
            # all. Same fleet, same q, different reason: two lists, both
            # disjoint from `profile_units` and from each other.
            "rate_zero_units": [u.name for u in units
                                if _rate_is_zero(u)
                                and not _is_flag_deterministic(u)],
        },
        "fidelity_note": analysis["fidelity_note"],
        # Phase 12d: which units the engines masked in which period, by
        # build year / lifetime (and which are below nameplate, a later
        # vintage not yet built), with the sentence that says so.
        "activity": activity,
        "voll_eur_per_mwh": voll,
    }


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
    # Prefer the capture's explicit VoLL (present since the weighted-totals
    # change); older captures lack it — fall back to the cost/energy ratio.
    voll = float(cap.get("voll_eur_per_mwh") or 0.0) or (
        (total_cost / total_mwh) if total_mwh > 0 else 0.0
    )

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
    full_df = df   # bind BEFORE slicing — shed-hours is horizon-scope
    if _wants_slice(from_, to_):
        df, range_meta = _slice_ts(df, from_, to_)
    # Shed-hours (spec §5.1) — electrical buses only, weighted on the same
    # energy basis as dispatch. Computed on the FULL frame, not the sliced
    # range: it is a horizon reliability number, not a window statistic.
    from services.adequacy.metrics import electrical_columns, shed_hours
    from services.period_utils import snapshot_weights
    sh = shed_hours(
        full_df[electrical_columns(n, list(full_df.columns))],
        weights=snapshot_weights(n, "generators", sns=full_df.index),
    )
    return _ts_payload(df, extra={
        "total_mwh": total_mwh,
        "total_cost_eur": total_cost,
        "voll_eur_per_mwh": voll,
        "bus_carriers": bus_carriers,
        "shed_hours": {
            "total": sh["total"],
            "by_period": {str(k): v for k, v in sh["by_period"].items()},
        },
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
