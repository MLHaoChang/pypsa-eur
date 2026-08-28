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

from pydantic import BaseModel as _BaseModel

from fastapi import APIRouter, HTTPException, Query, Response

from services.dispatch_status import dispatch_status as _dispatch_status
from services.economics import co2_intensity_map
from services.pypsa_service import PyPSAService
from services.serialization import (
    df_to_json,
    safe_float as _safe_float,
    safe_values as _safe_values,
    slice_ts as _slice_ts,
    ts_payload as _ts_payload,
)
from services.solver_service import (
    SolverConfig,
    _pv_factor_series,
    _reference_build_year,
    periodized_capital_costs,
    with_periodized_cost_defaults,
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
from services.period_utils import (
    is_multi_period,
    is_period_only as _is_period_only,
    period_years_map,
    snapshot_weights,
    years_for_period,
)

from routers.simulation import _state, _state_snapshot
from services.adequacy import slack as _slack

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


# `_safe_values` / `_ts_payload` (NaN-safe time-series payload builders) now
# live in `services/serialization.py`, imported above as aliases so the call
# sites in this module are unchanged.


def _wants_slice(from_: int | None, to_: int | None) -> bool:
    """
    True when the caller actually asked for a `from`/`to` window.

    Deliberately `isinstance(x, int)`, not `x is None` / `x is not None`:
    when a route function decorated with a `Query(None, ...)` default is
    called directly as plain Python — bypassing FastAPI's request handling,
    which is the only place that actually resolves the query string into an
    int-or-None — the unset parameter's value is the `fastapi.params.Query`
    sentinel object itself, not `None`. `x is not None` is then True for a
    bound the caller never supplied, and `_slice_ts` chokes on a non-int
    bound. `tests/test_compare_cross_surface.py` calls
    `get_prices`/`get_curtailment` exactly this way; nothing calls one of
    the `_serve_ts`-backed endpoints directly today, but the guard is kept
    identical across every call site on purpose — a reader who "fixes" one
    back to `is not None` because it looks simpler must not have a second,
    correct form to copy from.

    When both bounds are absent, the caller should leave `range_meta` as
    `None` so the payload stays byte-identical to the pre-range response —
    that is what keeps every consumer that has not been converted to ask
    for a slice working unchanged.
    """
    return isinstance(from_, int) or isinstance(to_, int)


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
    cfg = _state["solver_config"]
    # `*_lifetime_by_class` is built inside the same `with` block as the
    # statistics call so the lifetime fill is still in place when we read
    # per-asset `lifetime`. Used to back the "Total over lifetime" toggle
    # on the CapacityExpansion tab — sum of (periodized capex × asset
    # lifetime) per component class. We keep the annualised numbers
    # PyPSA already returns and ADD a lifetime variant; the toggle picks
    # one or the other in the UI.
    import math as _math
    capex_lifetime_by_class: dict[str, float] = {}
    capex_expansion_lifetime_by_class: dict[str, float] = {}
    NOM_PAIRS = [
        ("generators",    "Generator",    "p_nom"),
        ("storage_units", "StorageUnit",  "p_nom"),
        ("stores",        "Store",        "e_nom"),
        ("links",         "Link",         "p_nom"),
        ("lines",         "Line",         "s_nom"),
        ("transformers",  "Transformer",  "s_nom"),
    ]
    try:
        with with_periodized_cost_defaults(n, cfg):
            stats = n.statistics()
            exp_series = None
            try:
                exp_series = n.statistics.expanded_capex()
            except Exception:  # noqa: BLE001 — older PyPSA versions
                exp_series = None
            # Per-asset lifetime-weighted CAPEX. Build inside the same block
            # so that lifetime fills (from solver config) are in place.
            reference_year = _reference_build_year(n)
            for comp_attr, comp_class, nom in NOM_PAIRS:
                df = getattr(n, comp_attr, None)
                if df is None or df.empty:
                    continue
                # Upfront (overnight) cost per unit of capacity. PyPSA's
                # `comp.overnight_cost` returns the user-typed
                # `overnight_cost` directly, and back-calculates from
                # `capital_cost / annuity / nyears` for assets that left
                # it blank. Then we apply a per-asset PV factor so future-
                # year investments are discounted back to year-0 (= min
                # build_year). For single-instant runs the factor is 1.
                try:
                    upfront_series = n.c[comp_class].overnight_cost
                except Exception:
                    continue
                pv_series = _pv_factor_series(df, cfg, reference_year)
                upfront_pv = upfront_series * pv_series
                nom_col = df[nom] if nom in df.columns else None
                opt_col = df[f"{nom}_opt"] if f"{nom}_opt" in df.columns else nom_col
                if opt_col is None:
                    continue
                # Installed: PV-upfront × p_nom_opt across all assets.
                capex_lifetime_sum = float((upfront_pv * opt_col).fillna(0).sum())
                # Expansion only: PV-upfront × positive delta.
                if nom_col is not None:
                    delta = (opt_col - nom_col).where(lambda s: s > 0, 0)
                    exp_lifetime_sum = float((upfront_pv * delta).fillna(0).sum())
                else:
                    exp_lifetime_sum = 0.0
                if _math.isnan(capex_lifetime_sum) or _math.isinf(capex_lifetime_sum):
                    capex_lifetime_sum = 0.0
                if _math.isnan(exp_lifetime_sum) or _math.isinf(exp_lifetime_sum):
                    exp_lifetime_sum = 0.0
                capex_lifetime_by_class[comp_class] = capex_lifetime_sum
                capex_expansion_lifetime_by_class[comp_class] = exp_lifetime_sum
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()
    if stats is None or stats.empty:
        return _not_solved()

    # Multi-period networks return `n.statistics()` with MultiIndex columns
    # — typically `(metric, period)` tuples like `('Capital Expenditure', 2025)`.
    # Some PyPSA versions instead put the period in the row index as the
    # outermost level. We support both. Whichever shape we see, we iterate
    # cells WITHOUT flattening (the old "groupby(level=metric).sum().T" path
    # lost per-period info and didn't apply years weighting → users saw
    # numbers that didn't add up across the Aggregated/Per-period views).
    import pandas as _pd
    cols_are_multi = isinstance(stats.columns, _pd.MultiIndex)
    idx_is_multi   = isinstance(stats.index,   _pd.MultiIndex)

    # Detect which level (column or row) carries the metric name vs period.
    def _detect_metric_period_levels(midx: _pd.MultiIndex) -> tuple[int, int]:
        """
        Return (metric_level, period_level). The metric level holds strings
        like 'Capital Expenditure'; the period level holds ints/years.
        """
        metric_l = None
        period_l = None
        for i in range(midx.nlevels):
            vals = midx.get_level_values(i)
            if len(vals) == 0: continue
            sample = vals[0]
            if isinstance(sample, str):
                if metric_l is None: metric_l = i
            else:
                if period_l is None: period_l = i
        if metric_l is None: metric_l = 0
        if period_l is None: period_l = 1 if metric_l == 0 else 0
        return metric_l, period_l

    col_metric_level: int | None = None
    col_period_level: int | None = None
    capex_col: Any = None
    opex_col: Any  = None
    if cols_are_multi:
        col_metric_level, col_period_level = _detect_metric_period_levels(stats.columns)
    else:
        # Flat columns — PyPSA versions slightly differ on column names.
        capex_col = next((c for c in stats.columns if isinstance(c, str) and "capital" in c.lower()), None)
        opex_col  = next((c for c in stats.columns if isinstance(c, str) and "operational" in c.lower()), None)
        if not capex_col or not opex_col:
            return _not_solved()

    # `n.statistics()` for multi-period returns per-period values weighted by
    # `snapshot_weightings.objective` but NOT by `investment_period_weightings.years`.
    # To produce a horizon total (the user-facing "total OPEX" / "total CAPEX"
    # they see in the Aggregated view), multiply each cell's value by that
    # period's `years` BEFORE summing — otherwise a multi-period horizon with
    # years=4+5 each shows numbers ~4× too small.
    period_years = period_years_map(n)

    def _years_for_period(p) -> float:
        return years_for_period(period_years, p)

    def _normalize_period_key(p) -> Any:
        """
        Coerce a period value to int when possible so the response dict
        sorts numerically and matches the frontend's selectedPeriod (int).
        """
        try:
            return int(p)
        except (TypeError, ValueError):
            return p

    # `_is_period_only` (skip the bare-year period-total rows PyPSA emits) is
    # imported from services.period_utils.
    by_class: dict[str, dict[str, float]] = {}
    by_carrier: list[dict[str, Any]] = []
    # Per-period totals — keyed by the period level (year int).
    # Each entry: {capex: number, opex: number, by_component: {Class: {capex, opex}}}
    # Already multiplied by investment_period_weightings.years so each entry is
    # the period's full LP-objective contribution. Per-period view consumers
    # (the Dispatch tab when a specific period is selected) read directly from
    # this — avoids the bug where the horizon total `cost.opex` was being
    # shown verbatim in per-period view and double-counted across periods.
    by_period: dict[Any, dict[str, Any]] = {}
    by_carrier_dict: dict[tuple[str, str], dict[str, float]] = {}
    capex_total = 0.0
    opex_total = 0.0

    def _accumulate(comp: str, carrier: str, period: Any, capex_v: float, opex_v: float) -> None:
        """
        Add one (component, carrier, period) row to all accumulators. period
        may be None for single-period or when stats has no period dimension.
        """
        nonlocal capex_total, opex_total
        capex_total += capex_v
        opex_total  += opex_v
        bucket = by_class.setdefault(comp, {"capex": 0.0, "opex": 0.0, "capex_expansion": 0.0})
        bucket["capex"] += capex_v
        bucket["opex"]  += opex_v
        cb = by_carrier_dict.setdefault((comp, carrier), {"capex": 0.0, "opex": 0.0})
        cb["capex"] += capex_v
        cb["opex"]  += opex_v
        if period is None:
            return
        pkey = _normalize_period_key(period)
        # by_period now carries BOTH a per-component and a per-carrier
        # breakdown for the period. Per-carrier is the cross-product of
        # `by_period` and `by_carrier` so the frontend can show
        # "OPEX by carrier for period 2026" without re-aggregating client-side.
        p_entry = by_period.setdefault(
            pkey, {"capex": 0.0, "opex": 0.0, "by_component": {}, "by_carrier": {}},
        )
        p_entry["capex"] += capex_v
        p_entry["opex"]  += opex_v
        p_bucket = p_entry["by_component"].setdefault(comp, {"capex": 0.0, "opex": 0.0})
        p_bucket["capex"] += capex_v
        p_bucket["opex"]  += opex_v
        # Group by carrier alone within the period — collapses Generator/gas
        # + Link/gas (rare but possible) into a single "gas" row. The
        # frontend already does the same flattening on `cost.by_carrier` for
        # the horizon-wide view.
        c_bucket = p_entry["by_carrier"].setdefault(carrier or "", {"capex": 0.0, "opex": 0.0})
        c_bucket["capex"] += capex_v
        c_bucket["opex"]  += opex_v


    for idx, row in stats.iterrows():
        # Row identity. Index may be (period, comp, carrier), (comp, carrier),
        # or just a string. We pick off the period only when it's the FIRST
        # level of a MultiIndex row AND the value is a year-shaped int.
        row_period: Any = None
        if idx_is_multi and isinstance(idx, tuple) and len(idx) >= 1:
            first = idx[0]
            try:
                _p = int(first)
                if 1900 <= _p <= 2200:
                    row_period = _p
            except (TypeError, ValueError):
                pass
        if isinstance(idx, tuple):
            levels = idx[1:] if row_period is not None else idx
            comp = str(levels[0]) if len(levels) >= 1 else ""
            carrier = str(levels[1]) if len(levels) >= 2 else ""
        else:
            comp, carrier = str(idx), ""
        # Normalise carrier to lowercase for cross-endpoint matching with
        # emissions / carrier_kpis. PyPSA's statistics index sometimes
        # uses title-cased values from nice_name; emissions uses the raw
        # key. Lowercasing both surfaces a single canonical id the frontend
        # can join on across tabs.
        carrier = carrier.lower() if carrier else ""
        if not comp or _is_period_only(comp):
            continue

        if cols_are_multi:
            # Iterate (metric, period) cells. row.items() yields (col_tuple, val).
            for col, val in row.items():
                if not isinstance(col, tuple) or len(col) < 2: continue
                metric = col[col_metric_level]
                period = col[col_period_level] if row_period is None else row_period
                if not isinstance(metric, str): continue
                ml = metric.lower()
                if "capital" not in ml and "operational" not in ml: continue
                v = _safe_float(val) * _years_for_period(period)
                if "capital" in ml:
                    _accumulate(comp, carrier, period, v, 0.0)
                else:
                    _accumulate(comp, carrier, period, 0.0, v)
        else:
            cx = _safe_float(row[capex_col])
            ox = _safe_float(row[opex_col])
            # Flat columns means stats has already been aggregated across
            # periods by PyPSA. If the row index carries a period, scale by
            # years; otherwise the row is horizon-total already.
            years = _years_for_period(row_period) if row_period is not None else 1.0
            cx *= years; ox *= years
            _accumulate(comp, carrier, row_period, cx, ox)

    # Materialise by_carrier as a flat list now that all rows have been folded.
    for (comp, carrier), v in by_carrier_dict.items():
        by_carrier.append({
            "component": comp, "carrier": carrier,
            "capex": v["capex"], "opex": v["opex"],
            "total": v["capex"] + v["opex"],
        })

    # ── Expansion CAPEX (new investments only) ──────────────────────────────
    # `n.statistics.expanded_capex()` returns a Series of capital_cost × Δp_nom
    # (or s_nom/e_nom equivalents) summed per (component, carrier), i.e. the
    # capex of capacity NEWLY built this run. Distinct from the "Capital
    # Expenditure" column above, which is annualised cost of ALL installed
    # capacity — the source of the user-confusing €3 B on networks with non-
    # extendable lines that carry a capital_cost.
    capex_expansion_total = 0.0
    if exp_series is not None:
        exp_idx_is_multi = isinstance(exp_series.index, _pd.MultiIndex)
        for idx, val in exp_series.items():
            # Same period-stripping + years-scaling logic as the stats loop:
            # expanded_capex returns ANNUALISED per-period values, so multiply
            # by investment_period_weightings.years to get a horizon total.
            row_period: Any = None
            if exp_idx_is_multi and isinstance(idx, tuple) and len(idx) >= 1:
                first = idx[0]
                try:
                    _p = int(first)
                    if 1900 <= _p <= 2200:
                        row_period = _p
                except (TypeError, ValueError):
                    pass
            if isinstance(idx, tuple):
                levels = idx[1:] if row_period is not None else idx
                comp = levels[0] if len(levels) >= 1 else str(idx)
            else:
                comp = str(idx)
            comp = str(comp)
            if _is_period_only(comp):
                continue
            years_mul = _years_for_period(row_period) if row_period is not None else 1.0
            v = _safe_float(val) * years_mul
            capex_expansion_total += v
            bucket = by_class.setdefault(comp, {"capex": 0.0, "opex": 0.0, "capex_expansion": 0.0})
            bucket["capex_expansion"] += v

    # Fallback when `expanded_capex` is unavailable OR returns 0 despite
    # observable expansion on the parent rows. The latter happens on
    # vintage-expanded networks: vintage_service flips the parent's
    # *_extendable=False during solve, so PyPSA's helper sees no expansion
    # at the parent level, while the parent's p_nom_opt already includes
    # all vintage builds via post-solve aggregation. Compute manually
    # as Σ (p_nom_opt - p_nom) × capital_cost when the helper underreports.
    if capex_expansion_total < 1.0:  # < 1 € total → almost certainly wrong
        manual_total = 0.0
        with with_periodized_cost_defaults(n, cfg):
            for comp_attr, comp_class, nom in NOM_PAIRS:
                df = getattr(n, comp_attr, None)
                if df is None or df.empty or f"{nom}_opt" not in df.columns:
                    continue
                try:
                    cc_series = n.c[comp_class].capital_cost
                except Exception:
                    continue
                nom_col = df[nom].reindex(df.index).fillna(0.0)
                opt_col = df[f"{nom}_opt"].reindex(df.index).fillna(nom_col)
                delta = (opt_col - nom_col).clip(lower=0)
                comp_sum = float((cc_series.reindex(df.index) * delta).fillna(0).sum())
                if not _math.isfinite(comp_sum) or comp_sum < 0:
                    comp_sum = 0.0
                if comp_sum > 0:
                    manual_total += comp_sum
                    bucket = by_class.setdefault(comp_class, {"capex": 0.0, "opex": 0.0, "capex_expansion": 0.0})
                    bucket["capex_expansion"] = max(bucket.get("capex_expansion", 0.0), comp_sum)
        if manual_total > capex_expansion_total:
            capex_expansion_total = manual_total

    capex_lifetime_total = sum(capex_lifetime_by_class.values())
    capex_expansion_lifetime_total = sum(capex_expansion_lifetime_by_class.values())

    # Curtailment penalty: Σ curtailment_t × curtailment_cost over renewables
    # that opted in (curtailment_cost > 0). PyPSA's n.statistics() doesn't
    # include this charge — it's a custom extra_functionality term — so we
    # compute it here and surface it alongside OPEX. Weighting basis matches
    # the LP objective (snapshot_weightings.objective × period years).
    curtailment_cost_total = 0.0
    try:
        gens_df = getattr(n, "generators", None)
        gens_t_p = getattr(n.generators_t, "p", None) if hasattr(n, "generators_t") else None
        if (
            gens_df is not None and not gens_df.empty
            and "curtailment_cost" in gens_df.columns
            and gens_t_p is not None and not gens_t_p.empty
        ):
            cc_series = gens_df["curtailment_cost"].fillna(0)
            charged = cc_series[cc_series > 0]
            if not charged.empty:
                p_max_pu_t = getattr(n.generators_t, "p_max_pu", None)
                p_max_static = gens_df["p_max_pu"] if "p_max_pu" in gens_df.columns else None
                p_nom_opt = gens_df.get("p_nom_opt", gens_df.get("p_nom"))
                # Snapshot weight per row × period years.
                sw = getattr(n, "snapshot_weightings", None)
                if sw is not None and "objective" in sw.columns:
                    obj_w = sw["objective"].astype(float)
                else:
                    obj_w = None
                period_years = period_years_map(n)  # NaN years → 1.0 (was unguarded)
                is_mp_local = isinstance(n.snapshots, _pd.MultiIndex)
                # Iterate per generator → cheaper than full matrix when only a
                # handful of generators carry curtailment_cost.
                for gname in charged.index:
                    if gname not in gens_t_p.columns:
                        continue
                    p_series = gens_t_p[gname]
                    # Profile shape: time-varying if column present in
                    # p_max_pu_t, else scalar from static table.
                    if p_max_pu_t is not None and gname in p_max_pu_t.columns:
                        prof = p_max_pu_t[gname]
                    elif p_max_static is not None:
                        prof = _pd.Series(p_max_static.get(gname, 1.0), index=p_series.index)
                    else:
                        prof = _pd.Series(1.0, index=p_series.index)
                    p_nom_val = float(p_nom_opt.get(gname, 0) or 0)
                    avail = prof * p_nom_val
                    # Multi-period effective capacity via vintage_results.
                    # vintage_service aggregates vintages into the parent
                    # post-solve, so p_nom_opt is the horizon-end total. For
                    # earlier snapshots, the effective capacity is smaller
                    # because some vintages weren't yet built. Walk
                    # n.meta["vintage_results"] to rebuild the time-varying
                    # effective capacity per snapshot. See /curtailment
                    # endpoint for the same fix.
                    if is_mp_local:
                        try:
                            vr = (n.meta or {}).get("vintage_results", {}) if hasattr(n, "meta") else {}
                            gen_vr = vr.get("Generator", {}) if isinstance(vr, dict) else {}
                            meta = gen_vr.get(gname)
                            if meta:
                                initial = float(meta.get("initial_capacity", 0.0) or 0.0)
                                periods_meta = meta.get("periods", []) or []
                                periods_arr = p_series.index.get_level_values(0).astype(int)
                                eff = _pd.Series(initial, index=p_series.index, dtype=float)
                                for entry in periods_meta:
                                    try:
                                        by = int(entry.get("build_year"))
                                        pn = float(entry.get("p_nom_opt", 0.0) or 0.0)
                                    except (TypeError, ValueError):
                                        continue
                                    if pn <= 0:
                                        continue
                                    eff.values[periods_arr >= by] += pn
                                avail = prof * eff
                        except Exception:
                            pass
                    curt = (avail - p_series).clip(lower=0)
                    if obj_w is not None:
                        weights = obj_w.reindex(p_series.index).fillna(1.0)
                    else:
                        weights = _pd.Series(1.0, index=p_series.index)
                    if is_mp_local and period_years:
                        try:
                            periods_lvl = p_series.index.get_level_values(0)
                            year_mul = _pd.Series(
                                [period_years.get(int(p), 1.0) for p in periods_lvl],
                                index=p_series.index,
                            )
                            weights = weights * year_mul
                        except Exception:
                            pass
                    contrib = float((curt * weights).sum()) * float(charged.at[gname])
                    if _math.isfinite(contrib):
                        curtailment_cost_total += contrib
    except Exception:
        curtailment_cost_total = 0.0

    # Storage-only CAPEX-expansion: sum of by_component['capex_expansion']
    # for StorageUnit + Store, plus the lifetime variant. Exposed so the UI
    # can show "how much money goes into storage vs everything else" at a
    # glance without re-summing the per-component list client-side.
    storage_capex_expansion = float(
        by_class.get("StorageUnit", {}).get("capex_expansion", 0.0)
        + by_class.get("Store", {}).get("capex_expansion", 0.0)
    )
    storage_capex_expansion_lifetime = float(
        capex_expansion_lifetime_by_class.get("StorageUnit", 0.0)
        + capex_expansion_lifetime_by_class.get("Store", 0.0)
    )
    # Sorted list of per-period entries — same fields as the top-level totals
    # but scoped to one period. Each entry's capex/opex are already multiplied
    # by `investment_period_weightings.years[period]` so that
    # `sum(p.opex for p in by_period) == opex_total` and likewise for capex.
    def _sort_key_period(k: Any) -> tuple:
        if isinstance(k, int):
            return (0, k)
        try:
            return (0, int(k))
        except (TypeError, ValueError):
            return (1, str(k))
    by_period_list = []
    for p in sorted(by_period.keys(), key=_sort_key_period):
        entry = by_period[p]
        by_period_list.append({
            "period": p,
            "capex": entry["capex"],
            "opex": entry["opex"],
            "total": entry["capex"] + entry["opex"],
            "by_component": [
                {"component": c, "capex": v["capex"], "opex": v["opex"]}
                for c, v in sorted(entry["by_component"].items())
            ],
            # Per-carrier breakdown WITHIN the period — sorted by total
            # (capex+opex) desc so the dominant carriers surface first.
            # Frontend uses this when the user has picked a specific period
            # for the OPEX-by-carrier view; falls back to the horizon-wide
            # `by_carrier` otherwise.
            "by_carrier": sorted(
                [
                    {"carrier": c, "capex": v["capex"], "opex": v["opex"]}
                    for c, v in entry.get("by_carrier", {}).items()
                ],
                key=lambda r: -(r["capex"] + r["opex"]),
            ),
        })
    return {
        "capex": capex_total,
        "capex_lifetime": capex_lifetime_total,
        "capex_expansion": capex_expansion_total,
        "capex_expansion_lifetime": capex_expansion_lifetime_total,
        "opex": opex_total,
        "total": capex_total + opex_total,
        # Renewable-curtailment penalty (Σ curtailment_t × curtailment_cost).
        # Zero unless the user set curtailment_cost > 0 on at least one
        # renewable generator. Already weighted by snapshot × period years.
        "curtailment_cost": curtailment_cost_total,
        # CAPEX going into storage (StorageUnit + Store), expansion only.
        # Useful as a quick "how much of the investment is storage?" KPI.
        "storage_capex_expansion": storage_capex_expansion,
        "storage_capex_expansion_lifetime": storage_capex_expansion_lifetime,
        "by_component": [
            {
                "component": c,
                "capex": v["capex"],
                "capex_lifetime": capex_lifetime_by_class.get(c, 0.0),
                "capex_expansion": v.get("capex_expansion", 0.0),
                "capex_expansion_lifetime": capex_expansion_lifetime_by_class.get(c, 0.0),
                "opex": v["opex"],
                "total": v["capex"] + v["opex"],
            }
            for c, v in sorted(by_class.items())
        ],
        "by_carrier": sorted(by_carrier, key=lambda r: r["total"], reverse=True),
        # Empty list on single-period networks. Frontend uses this only when
        # `selectedPeriod != null` to render period-scoped CAPEX/OPEX.
        "by_period": by_period_list,
    }


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
        result = _compute_economics_summary(n, periods, is_multi, True)
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
    cfg = _state["solver_config"]
    try:
        with with_periodized_cost_defaults(n, cfg):
            stats = n.statistics()
            # `df_to_json` runs `reset_index` internally + applies `_clean`
            # to coerce NaN/Inf → None. The previous code called
            # `stats.reset_index()` BEFORE handing off, producing a DOUBLE
            # reset_index that left a stray `level_0` / `index` column on
            # the records — AND the fallback `else` branch skipped `_clean`
            # entirely, so a single NaN in `n.statistics()` (common for
            # missing metrics) would crash Starlette's `JSONResponse.render`
            # at `json.dumps(allow_nan=False)` with a 500 plain-text error.
            # Same class of bug as `/results/storage` 500s `_safe_values`
            # was added to fix. Single code path; pass `stats` straight in.
            return df_to_json(stats)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()


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
    import math

    import pandas as _pd
    from services.solver_service import with_periodized_cost_defaults

    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()

    links_df = n.links
    if links_df.empty:
        return {"rows": [], "total": None, "currency": "EUR"}

    # Filter to electrolyser-like links by carrier substring. Matches the
    # frontend's `isElectrolyzerCarrier` token set so the two views agree.
    tokens = [
        "electrol", "p2g", "p2h2", "power-to-h2", "power-to-gas",
        "power2gas", "hydrogen", "h2",
    ]
    def _is_electrolyzer(c) -> bool:
        s = str(c or "").strip().lower()
        return any(t in s for t in tokens)

    if "carrier" not in links_df.columns:
        return {"rows": [], "total": None, "currency": "EUR"}
    candidate_names = [name for name in links_df.index if _is_electrolyzer(links_df.at[name, "carrier"])]
    if not candidate_names:
        return {"rows": [], "total": None, "currency": "EUR"}

    # Snapshot weight × period years — same weighting basis as cost_breakdown.
    sns = n.snapshots
    is_multi = is_multi_period(n)
    weights = snapshot_weights(n, "objective")

    # ENERGY weighting for the H₂-produced denominator follows the `generators`
    # column (matching PyPSA n.statistics() and the Dispatch tab); the cost
    # terms (VOM, electricity input) keep the `objective` weighting above.
    # Identical when the two columns coincide (the common case); they diverge
    # only under representative-week weighting. Lazy import avoids the
    # projects<->simulation import cycle; the helper applies the same
    # generators→objective→1.0 fallback + investment-period years scaling.
    from routers.compare import _build_snapshot_weights as _bsw
    energy_weights = _bsw(n, "generators")

    # Effective capital_cost via the same fill PyPSA uses for n.statistics().
    cfg = _state.get("solver_config") or SolverConfig()
    try:
        with with_periodized_cost_defaults(n, cfg):
            cap_costs = n.c["Link"].capital_cost
            if not isinstance(cap_costs, _pd.Series):
                cap_costs = _pd.Series(cap_costs, index=links_df.index)
    except Exception:
        cap_costs = links_df.get("capital_cost", _pd.Series(0.0, index=links_df.index))

    # bus0 marginal prices for the electricity-cost term. Use the merit-order
    # SUBSIDY-REMOVED duals (the same correction asset_economics and the Compare
    # per-carrier economics apply via the shared helper) — NOT raw
    # n.buses_t.marginal_price. Under a curtailment_cost subsidy the raw bus
    # dual goes negative wherever a subsidised renewable sets the price, which
    # makes the electrolyser's electricity input cost negative (unphysical — no
    # money flows; it's an LP-accounting artefact) and understates LCOH.
    # corrected_marginal_prices restores the real price at those buses/snapshots.
    try:
        bus_prices = corrected_marginal_prices(n)
    except Exception:
        try:
            bus_prices = n.buses_t.marginal_price
        except Exception:
            bus_prices = None

    p0 = getattr(n.links_t, "p0", None)
    if p0 is None or p0.empty:
        return {"rows": [], "total": None, "currency": "EUR"}

    rows: list[dict] = []
    # Fleet accumulators for the aggregated LCOH at the bottom.
    # `fleet_capex_per_year` is the sum of annualised €/yr CAPEX across
    # links (the value surfaced in the response for backwards display
    # compatibility). `fleet_capex_total` is the horizon-total CAPEX used
    # in the LCOH numerator — see total_years_factor comment below.
    fleet_capex_per_year = 0.0
    fleet_capex_total = 0.0
    fleet_vom = 0.0
    fleet_elec = 0.0
    fleet_h2 = 0.0
    # Fleet per-period accumulators. Only populated when the network is
    # multi-period; lets the frontend Economics tab switch between the
    # horizon-aggregate LCOH (when "Aggregated" is selected) and the
    # period-scoped LCOH for any specific year.
    fleet_by_period: dict[int, dict[str, float]] = {}

    # Helpers for per-period slicing — Multi-period MultiIndex → list of
    # period years; flat → empty (no per-period view).
    if is_multi:
        try:
            unique_periods = sorted({int(p) for p in sns.get_level_values(0)})
            period_level = sns.get_level_values(0)
            _years_map = period_years_map(n)
            period_year_factor = {
                int(p): years_for_period(_years_map, p) for p in unique_periods
            }
        except Exception:
            unique_periods = []
            period_level = None
            period_year_factor = {}
    else:
        unique_periods = []
        period_level = None
        period_year_factor = {}

    # Horizon-total years multiplier. Per-row OPEX, electricity, and H₂
    # totals are already weighted by `weights = sw * years_s` (line ~1567),
    # so they accumulate ACROSS investment periods (e.g. multi-period run
    # over [2030(years=5), 2040(years=10)] yields totals that span 15
    # operational years). CAPEX is computed as `capital_cost × p_nom_opt`
    # which is PyPSA's ANNUALISED value (€/yr). To keep the LCOH numerator
    # consistent — apples-to-apples integration across the same horizon —
    # scale CAPEX by the same total-years factor before mixing it with
    # OPEX/elec. Without this, a 15-year-horizon LCOH would have ~1× CAPEX
    # divided by ~15× H₂ and silently understate by an order of magnitude.
    # Flat networks (no investment periods) get factor=1.0, preserving
    # the legacy per-row formula `1-yr CAPEX + 1-yr OPEX`.
    total_years_factor = float(sum(period_year_factor.values())) if is_multi and period_year_factor else 1.0

    for name in candidate_names:
        try:
            p_nom_opt = float(links_df.at[name, "p_nom_opt"]) if "p_nom_opt" in links_df.columns else float(links_df.at[name, "p_nom"])
        except (TypeError, ValueError, KeyError):
            continue
        if not math.isfinite(p_nom_opt) or p_nom_opt <= 0:
            continue
        try:
            cc = float(cap_costs.at[name]) if name in cap_costs.index else 0.0
        except Exception:
            cc = 0.0
        if not math.isfinite(cc) or cc < 0:
            cc = 0.0
        capex_eur_per_year = cc * p_nom_opt

        try:
            eff = float(links_df.at[name, "efficiency"])
        except (TypeError, ValueError, KeyError):
            eff = 1.0
        if not math.isfinite(eff) or eff <= 0:
            eff = 1.0

        try:
            mc = float(links_df.at[name, "marginal_cost"])
        except (TypeError, ValueError, KeyError):
            mc = 0.0

        if name not in p0.columns:
            continue
        try:
            disp = p0[name].reindex(sns).fillna(0.0).astype(float)
        except Exception:
            continue
        # Only count the CONSUMING direction (positive p0). A reverse-flow
        # snapshot represents the link running in fuel-cell mode and isn't
        # part of H2 production cost.
        consume = disp.clip(lower=0)
        weighted_consume = consume * energy_weights
        consume_mwh = float(weighted_consume.sum())
        if consume_mwh <= 0:
            # Link wasn't dispatched as a consumer during this run.
            rows.append({
                "name": name,
                "carrier": str(links_df.at[name, "carrier"] or ""),
                "p_nom_opt_mw": p_nom_opt,
                "efficiency": eff,
                "capex_eur_per_year": capex_eur_per_year,
                "vom_cost_eur": 0.0,
                "electricity_cost_eur": 0.0,
                "h2_produced_mwh": 0.0,
                "lcoh_eur_per_mwh_h2": None,
                "lcoh_eur_per_kg_h2": None,
            })
            continue

        # Variable OPEX (€): Σ |p0| × marginal_cost × weight. Use |p0| so
        # reverse-flow snapshots still attract their marginal cost (PyPSA's
        # convention).
        vom_cost = float((disp.abs() * mc * weights).sum()) if mc > 0 else 0.0

        # Electricity input cost: bus0 marginal price × consume_t × weight.
        bus0 = links_df.at[name, "bus0"] if "bus0" in links_df.columns else None
        elec_cost = 0.0
        if bus_prices is not None and bus0 is not None and bus0 in bus_prices.columns:
            try:
                bp = bus_prices[bus0].reindex(sns).fillna(0.0).astype(float)
                elec_cost = float((consume * bp * weights).sum())
            except Exception:
                elec_cost = 0.0

        # H2 produced (MWh_H2): consume × efficiency × weight.
        h2_produced_mwh = consume_mwh * eff

        # Horizon-total CAPEX for the LCOH numerator — annualised €/yr × total
        # operational years across all investment periods. Keeps the
        # ratio's units consistent (€ / MWh_H2) regardless of horizon length.
        capex_total_eur = capex_eur_per_year * total_years_factor

        total_cost = capex_total_eur + vom_cost + elec_cost
        lcoh_eur_per_mwh = total_cost / h2_produced_mwh if h2_produced_mwh > 0 else None
        # Convert €/MWh_H2 → €/kg using LHV: 33.33 MWh / kg
        # (1 kg H2 = 33.33 kWh = 0.03333 MWh, so €/MWh × 0.03333 → €/kg)
        lcoh_eur_per_kg = lcoh_eur_per_mwh * 0.03333 if lcoh_eur_per_mwh is not None else None

        # Per-period breakdown for the Economics tab's period selector. For
        # each investment period:
        #   - h2[P]  = Σ consume × weight over snapshots in P
        #   - elec[P] = Σ consume × bus0_price × weight over snapshots in P
        #   - vom[P] = Σ |p0| × mc × weight over snapshots in P
        #   - capex[P] = annuitised cc × p_nom_opt × ipw.years[P]
        #     (the asset's annual cost allocated to this period's year-span)
        # All cost contributions in € for the period; LCOH[P] = sum / h2[P].
        per_period_rows: list[dict] = []
        if is_multi and unique_periods and period_level is not None:
            for p in unique_periods:
                mask = period_level == p
                try:
                    consume_p = consume[mask]
                    weights_p = weights[mask]
                    energy_weights_p = energy_weights[mask]
                except Exception:
                    continue
                # H₂ (energy) on the generators basis; VOM/elec (cost) on objective.
                weighted_consume_p = consume_p * energy_weights_p
                h2_p_mwh = float(weighted_consume_p.sum()) * eff
                vom_p = float((disp[mask].abs() * mc * weights_p).sum()) if mc > 0 else 0.0
                elec_p = 0.0
                if bus_prices is not None and bus0 is not None and bus0 in bus_prices.columns:
                    try:
                        bp_p = bus_prices[bus0][mask].reindex(consume_p.index).fillna(0.0).astype(float)
                        elec_p = float((consume_p * bp_p * weights_p).sum())
                    except Exception:
                        elec_p = 0.0
                capex_p = capex_eur_per_year * period_year_factor.get(int(p), 1.0)
                tot_p = capex_p + vom_p + elec_p
                lcoh_p = tot_p / h2_p_mwh if h2_p_mwh > 0 else None
                lcoh_p_kg = lcoh_p * 0.03333 if lcoh_p is not None else None
                per_period_rows.append({
                    "period": int(p),
                    "h2_produced_mwh": h2_p_mwh,
                    "capex_eur": capex_p,
                    "vom_cost_eur": vom_p,
                    "electricity_cost_eur": elec_p,
                    "lcoh_eur_per_mwh_h2": lcoh_p,
                    "lcoh_eur_per_kg_h2": lcoh_p_kg,
                })
                # Roll into fleet per-period totals.
                fb = fleet_by_period.setdefault(int(p), {
                    "h2_produced_mwh": 0.0, "capex_eur": 0.0,
                    "vom_cost_eur": 0.0, "electricity_cost_eur": 0.0,
                })
                fb["h2_produced_mwh"] += h2_p_mwh
                fb["capex_eur"] += capex_p
                fb["vom_cost_eur"] += vom_p
                fb["electricity_cost_eur"] += elec_p

        rows.append({
            "name": name,
            "carrier": str(links_df.at[name, "carrier"] or ""),
            "p_nom_opt_mw": p_nom_opt,
            "efficiency": eff,
            "capex_eur_per_year": capex_eur_per_year,
            "vom_cost_eur": vom_cost,
            "electricity_cost_eur": elec_cost,
            "h2_produced_mwh": h2_produced_mwh,
            "lcoh_eur_per_mwh_h2": lcoh_eur_per_mwh,
            "lcoh_eur_per_kg_h2": lcoh_eur_per_kg,
            "by_period": per_period_rows,
        })
        # Two CAPEX accumulators: annualised €/yr for display, horizon-total
        # for the LCOH numerator (apples-to-apples with OPEX/elec/H₂ totals).
        fleet_capex_per_year += capex_eur_per_year
        fleet_capex_total += capex_total_eur
        fleet_vom += vom_cost
        fleet_elec += elec_cost
        fleet_h2 += h2_produced_mwh

    rows.sort(key=lambda r: -(r["p_nom_opt_mw"] or 0))

    total = None
    if fleet_h2 > 0:
        fleet_total_cost = fleet_capex_total + fleet_vom + fleet_elec
        fleet_lcoh = fleet_total_cost / fleet_h2
        # Per-period fleet LCOH — same shape as the per-row by_period array.
        fleet_by_period_list = []
        for p in sorted(fleet_by_period.keys()):
            fb = fleet_by_period[p]
            tot_p = fb["capex_eur"] + fb["vom_cost_eur"] + fb["electricity_cost_eur"]
            lcoh_p = tot_p / fb["h2_produced_mwh"] if fb["h2_produced_mwh"] > 0 else None
            fleet_by_period_list.append({
                "period": p,
                "h2_produced_mwh": fb["h2_produced_mwh"],
                "capex_eur": fb["capex_eur"],
                "vom_cost_eur": fb["vom_cost_eur"],
                "electricity_cost_eur": fb["electricity_cost_eur"],
                "lcoh_eur_per_mwh_h2": lcoh_p,
                "lcoh_eur_per_kg_h2": lcoh_p * 0.03333 if lcoh_p is not None else None,
            })
        total = {
            "h2_produced_mwh": fleet_h2,
            "capex_eur_per_year": fleet_capex_per_year,
            "vom_cost_eur": fleet_vom,
            "electricity_cost_eur": fleet_elec,
            "lcoh_eur_per_mwh_h2": fleet_lcoh,
            "lcoh_eur_per_kg_h2": fleet_lcoh * 0.03333,
            "by_period": fleet_by_period_list,
        }
    return {"rows": rows, "total": total, "currency": "EUR"}


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
    import math as _math
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()

    import pandas as _pd

    # Per-period weighting for intensive metrics summed across the horizon.
    # n.statistics() on a multi-period network puts the period in COLUMNS
    # (MultiIndex), not rows. Without flattening, `.items()` yields
    # (col_key, Series) pairs that fall the isinstance(v, (int,float)) check
    # → empty result → the entire panel silently disappears. Apply the
    # same column-multiindex handling as get_cost_breakdown.
    period_years_lookup = period_years_map(n)

    def _years_for_period(p) -> float:
        return years_for_period(period_years_lookup, p)

    # Intensive metrics (capacity_factor, market_value) shouldn't get scaled
    # by period years — averaging a CF across periods needs a different
    # treatment. For now, weighted-average them by the period's contribution
    # weight when collapsing multi-period columns.
    #
    # Capacity metrics are CAPACITY-STOCK, not energy-flow: PyPSA returns the
    # same MW figure once per investment period, so multiplying by period
    # years (extensive treatment) inflates them by N× across N periods. They
    # need their own collapse mode: take the MAX across periods (the final,
    # cumulative capacity that survives to the last period). Otherwise a
    # 588 MW battery solved across 3 periods reports as 1764 MW.
    INTENSIVE = {"capacity_factor", "market_value"}
    CAPACITY_STOCK = {"optimal_capacity", "installed_capacity"}

    def _kpi_series(name: str):
        """
        Run `n.statistics.<name>(groupby='carrier')` and return (comp,
        carrier) → float. Handles both flat (Series) and multi-period
        DataFrame outputs.
        """
        try:
            fn = getattr(n.statistics, name)
            s = fn(groupby="carrier")
        except Exception:
            return {}
        if s is None:
            return {}
        out: dict = {}
        try:
            # Multi-period: returned as DataFrame with columns =
            # (metric, period) MultiIndex OR a plain DataFrame with per-period
            # columns. Flatten by summing across periods (with years scaling
            # for extensive metrics) or averaging (for intensive ones).
            if hasattr(s, "columns") and not isinstance(s, _pd.Series):
                df_kpi = s
                is_intensive = name in INTENSIVE
                is_capacity_stock = name in CAPACITY_STOCK
                for idx, row in df_kpi.iterrows():
                    if not isinstance(idx, tuple) or len(idx) < 2:
                        continue
                    comp_name = str(idx[0])
                    carrier_name = str(idx[1])
                    total = 0.0
                    weight_sum = 0.0
                    max_val = float("-inf")
                    for col_key, val in row.items():
                        if not isinstance(val, (int, float)) or not _math.isfinite(val):
                            continue
                        # Column key shape: either a period integer/string
                        # or a tuple (metric, period). Extract period.
                        if isinstance(col_key, tuple) and len(col_key) >= 2:
                            period = col_key[-1]
                        else:
                            period = col_key
                        years_w = _years_for_period(period)
                        v = float(val)
                        if is_capacity_stock:
                            # Capacity-stock: take the MAX across periods (the
                            # cumulative final value that survives to last).
                            if v > max_val:
                                max_val = v
                        elif is_intensive:
                            # Weighted average by years
                            total += v * years_w
                            weight_sum += years_w
                        else:
                            # Extensive — multiply by years
                            total += v * years_w
                    if is_capacity_stock:
                        final_v = max_val if max_val > float("-inf") else 0.0
                    elif is_intensive and weight_sum > 0:
                        final_v = total / weight_sum
                    else:
                        final_v = total
                    if _math.isfinite(final_v):
                        out[(comp_name, carrier_name)] = float(final_v)
                return out
            # Flat / Series path — the original behaviour.
            for idx, v in s.items():
                if not isinstance(v, (int, float)) or not _math.isfinite(v):
                    continue
                if isinstance(idx, tuple) and len(idx) >= 2:
                    out[(str(idx[0]), str(idx[1]))] = float(v)
        except Exception:
            return out
        return out

    cf      = _kpi_series("capacity_factor")
    curt    = _kpi_series("curtailment")
    mv      = _kpi_series("market_value")
    rev     = _kpi_series("revenue")
    energy  = _kpi_series("supply")  # MWh dispatched (positive)
    cap_opt = _kpi_series("optimal_capacity")
    cap_ins = _kpi_series("installed_capacity")

    # Union of (comp, carrier) keys we have data for, filtered to comps the
    # user cares about for per-carrier KPI comparison.
    KEEP_COMPONENTS = ("Generator", "StorageUnit", "Store", "Link")
    keys = (set(cf) | set(curt) | set(mv) | set(rev)
            | set(energy) | set(cap_opt) | set(cap_ins))
    keys = {(c, k) for (c, k) in keys if c in KEEP_COMPONENTS}

    # Components for which curtailment is a meaningful concept (= a primary
    # energy resource exists that the LP could have dispatched but didn't).
    # Storage and Store don't have a primary-energy resource — their "max
    # available" = p_nom × hours is just nameplate runtime, NOT curtailable
    # energy. PyPSA's n.statistics.curtailment() still computes the gap, but
    # surfacing it as "92% curtailed" is nonsensical for a battery. Suppress
    # it for those components in the UI.
    #
    # Additionally restrict Generator curtailment to renewable carriers —
    # PyPSA also reports "curtailment" for thermal plant as unused headroom
    # (p_nom − p), which the Curtailment / Dispatch tabs never show. Keeping
    # it here made Load Flow's carrier table disagree with those tabs (e.g.
    # gas "1 017 GWh curtailed" while Curtailment only listed PV spill).
    CURTAILMENT_SOURCES = {"Generator", "Link"}
    _RENEWABLE_KW = (
        "wind", "solar", "ror", "hydro", "geothermal", "wave", "tidal",
        "pv", "biomass", "biogas", "run-of-river",
    )

    def _is_renewable_carrier(name: str) -> bool:
        c = (name or "").lower()
        return any(k in c for k in _RENEWABLE_KW)

    rows: list[dict] = []
    for comp, carrier in sorted(keys):
        # Prefer optimal_capacity (post-solve) over installed_capacity (input)
        # so capacity-expansion runs see the expanded fleet.
        cap_mw = cap_opt.get((comp, carrier), cap_ins.get((comp, carrier), 0.0))
        energy_mwh = energy.get((comp, carrier), 0.0)
        if (
            comp in CURTAILMENT_SOURCES
            and (comp != "Generator" or _is_renewable_carrier(carrier))
        ):
            curt_mwh = curt.get((comp, carrier), 0.0)
            # Curtailment ratio: dispatched + curtailed = the max-available
            # envelope, so % = curtailed / envelope.
            if energy_mwh + curt_mwh > 0:
                curt_pct = 100.0 * curt_mwh / (energy_mwh + curt_mwh)
            else:
                curt_pct = 0.0
        else:
            # StorageUnit / Store / thermal generators: no renewable spill KPI.
            curt_mwh = 0.0
            curt_pct = 0.0
        rows.append({
            "component": comp,
            "carrier": carrier,
            "capacity_mw": cap_mw,
            "energy_mwh": energy_mwh,
            "capacity_factor_pct": 100.0 * cf.get((comp, carrier), 0.0),
            "curtailment_mwh": curt_mwh,
            "curtailment_pct": curt_pct,
            "market_value_eur_per_mwh": mv.get((comp, carrier), 0.0),
            "revenue_eur": rev.get((comp, carrier), 0.0),
        })

    # Storage revenue from n.statistics is NET (discharge − charge at bus
    # price). Economics / economics_by_carrier report GROSS discharge revenue
    # and book charge cost separately — override so Load Flow's carrier table
    # matches those tabs (and market_value = capture price on discharge).
    try:
        from services.period_utils import snapshot_weights as _sw
        bus_prices = corrected_marginal_prices(n, from_state=True)
        w_obj = _sw(n, "objective")
        sns = n.snapshots

        def _overlay_storage_revenue(df, t_p_df, comp_label: str) -> None:
            if df is None or df.empty or t_p_df is None or t_p_df.empty:
                return
            if bus_prices is None or bus_prices.empty:
                return
            # Key by lower-case carrier — n.statistics() often returns the
            # carrier nice_name ("Battery") while the component table stores
            # the raw carrier id ("battery").
            by_carrier_rev: dict[str, float] = {}
            for asset_name in df.index:
                if asset_name not in t_p_df.columns:
                    continue
                bus = df.at[asset_name, "bus"] if "bus" in df.columns else None
                if bus is None or bus not in bus_prices.columns:
                    continue
                carrier = str(
                    df.at[asset_name, "carrier"] if "carrier" in df.columns else "unknown"
                ).lower()
                series = t_p_df[asset_name].reindex(sns).fillna(0.0).astype(float)
                discharge = series.clip(lower=0)
                bp = bus_prices[bus].reindex(sns).fillna(0.0).astype(float)
                rev_total = float((discharge * bp * w_obj).sum())
                if not _math.isfinite(rev_total):
                    continue
                by_carrier_rev[carrier] = by_carrier_rev.get(carrier, 0.0) + rev_total
            if not by_carrier_rev:
                return
            for row in rows:
                if row["component"] != comp_label:
                    continue
                key = str(row["carrier"] or "").lower()
                if key not in by_carrier_rev:
                    continue
                row["revenue_eur"] = by_carrier_rev[key]
                e = row["energy_mwh"] or 0.0
                row["market_value_eur_per_mwh"] = (
                    row["revenue_eur"] / e if e > 1e-9 else 0.0
                )

        _overlay_storage_revenue(
            n.storage_units,
            getattr(n.storage_units_t, "p", None) if hasattr(n, "storage_units_t") else None,
            "StorageUnit",
        )
        # Stores: positive p = discharge from store to bus.
        _overlay_storage_revenue(
            n.stores,
            getattr(n.stores_t, "p", None) if hasattr(n, "stores_t") else None,
            "Store",
        )
    except Exception:
        pass

    # Sort by revenue desc (biggest earners first); ties broken by energy.
    rows.sort(key=lambda r: (-(r["revenue_eur"] or 0), -(r["energy_mwh"] or 0)))
    return {"rows": rows}


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
    import math as _math

    import pandas as _pd
    src = source if source in ("lopf", "ac_pf") else "lopf"
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    p = _result_df(n, "generators_t", "p", src)
    if p is None or p.empty:
        return _not_solved()

    # Snapshot weighting for ENERGY: emissions = Σ dispatch × weight × factor,
    # so use the `generators` column — PyPSA's energy basis, matching
    # n.statistics() and the primary-energy CO2 constraint. Falls back
    # generators → objective → None on older netcdf (identical when the two
    # columns coincide).
    try:
        weights = n.snapshot_weightings.generators
    except Exception:
        try:
            weights = n.snapshot_weightings.objective
        except Exception:
            weights = None

    # ── Per-period weighting setup ───────────────────────────────────────
    # PyPSA multi-period scaling = snapshot_weight × investment_period_years.
    # The previous implementation skipped years scaling, under-reporting on
    # any horizon with non-unit period weights. Apply both consistently here.
    is_multi = isinstance(n.snapshots, _pd.MultiIndex)
    period_years = period_years_map(n)

    def _years_for(p_val) -> float:
        return years_for_period(period_years, p_val)

    def _weight_series_for(snapshots) -> _pd.Series:
        """Per-row effective weight = snapshot weight (generators) × period.years."""
        w = _pd.Series(1.0, index=snapshots, dtype=float)
        if weights is not None:
            try:
                w = w.multiply(weights.reindex(snapshots).fillna(1.0), axis=0)
            except Exception:
                pass
        if is_multi and period_years:
            try:
                period_lvl = snapshots.get_level_values(0)
                years_series = _pd.Series(
                    [_years_for(pv) for pv in period_lvl],
                    index=snapshots, dtype=float,
                )
                w = w.multiply(years_series, axis=0)
            except Exception:
                pass
        return w

    w_series = _weight_series_for(p.index)

    # Helper: collapse a (snapshot × asset) dispatch DataFrame to per-period
    # weighted energy. Returns a dict {period → Series[asset → MWh]} on
    # multi-period; on flat networks returns {None → Series} (single bucket
    # so downstream code can iterate uniformly).
    def _energy_by_period(p_df: _pd.DataFrame) -> dict:
        weighted = p_df.multiply(w_series.reindex(p_df.index).fillna(0.0), axis=0)
        if not is_multi:
            return {None: weighted.sum(axis=0)}
        period_lvl = p_df.index.get_level_values(0)
        out: dict = {}
        for p_key, sub in weighted.groupby(period_lvl):
            try:
                p_norm = int(p_key)
            except (TypeError, ValueError):
                p_norm = p_key
            out[p_norm] = sub.sum(axis=0)
        return out

    gen_energy_per_period = _energy_by_period(p)
    # Flat-horizon convenience: also produce a horizon-total Series so the
    # per-generator row can pin its energy_mwh column without per-period
    # bookkeeping.
    gen_energy_horizon = sum(gen_energy_per_period.values()) if not is_multi else (
        p.multiply(w_series, axis=0).sum(axis=0)
    )

    # Carrier intensity lookup. PyPSA's primary-energy constraint reads
    # carriers.co2_emissions × dispatched_energy / efficiency. Lower-case
    # keys to match the lowercase comp.carrier values we look up with.
    co2_by_carrier: dict[str, float] = co2_intensity_map(n)

    gens = n.generators

    # Per-period accumulators. Keyed by period (or None for flat).
    period_totals: dict = {}                          # period → total tCO2
    period_carrier_totals: dict = {}                  # period → {carrier → tCO2}
    period_gen_rows: dict = {}                        # period → [row dicts]

    def _accumulate(period_key, tCO2: float, carrier: str, row: dict) -> None:
        period_totals[period_key] = period_totals.get(period_key, 0.0) + tCO2
        bucket = period_carrier_totals.setdefault(period_key, {})
        bucket[carrier] = bucket.get(carrier, 0.0) + tCO2
        period_gen_rows.setdefault(period_key, []).append(row)

    # Horizon-level mirrors so the headline `total_tCO2` / `by_carrier` /
    # `by_generator` stay populated. These sum across the per-period entries.
    rows_by_gen: list[dict] = []
    total_t = 0.0
    carrier_totals: dict[str, float] = {}

    # ── Generator loop ──────────────────────────────────────────────────
    for name in gens.index:
        g_carrier = (str(gens.at[name, "carrier"]) if "carrier" in gens.columns else "").lower()
        intensity = co2_by_carrier.get(g_carrier, 0.0)
        eff = float(gens.at[name, "efficiency"]) if "efficiency" in gens.columns else 1.0
        if not _math.isfinite(eff) or eff <= 0:
            eff = 1.0
        out_intensity = intensity / eff if intensity != 0 else 0.0
        mwh_horizon = float(gen_energy_horizon.get(name, 0.0))
        if not _math.isfinite(mwh_horizon):
            mwh_horizon = 0.0
        tCO2_horizon = mwh_horizon * out_intensity
        total_t += tCO2_horizon
        if intensity != 0:
            carrier_totals[g_carrier] = carrier_totals.get(g_carrier, 0.0) + tCO2_horizon
        rows_by_gen.append({
            "name": str(name),
            "carrier": g_carrier,
            "energy_mwh": mwh_horizon,
            "tCO2": tCO2_horizon,
            "intensity_tCO2_per_MWh_out": out_intensity,
        })
        # Per-period split. On flat networks period_key=None; on multi-period
        # each (period, generator) gets one row.
        for period_key, energy_p in gen_energy_per_period.items():
            mwh = float(energy_p.get(name, 0.0))
            if not _math.isfinite(mwh):
                mwh = 0.0
            tCO2 = mwh * out_intensity
            _accumulate(period_key, tCO2, g_carrier, {
                "name": str(name),
                "carrier": g_carrier,
                "energy_mwh": mwh,
                "tCO2": tCO2,
                "intensity_tCO2_per_MWh_out": out_intensity,
            })

    # ── Storage emissions ────────────────────────────────────────────────
    # StorageUnit + Store: emissions when the unit's carrier has
    # co2_emissions > 0. Only discharge (positive p) emits.
    for comp_attr, comp_class, t_attr in (
        ("storage_units", "StorageUnit", "p"),
        ("stores", "Store", "p"),
    ):
        comp_df = getattr(n, comp_attr, None)
        if comp_df is None or comp_df.empty:
            continue
        comp_t = getattr(n, f"{comp_attr}_t", None)
        if comp_t is None:
            continue
        p_t = getattr(comp_t, t_attr, None)
        if p_t is None or p_t.empty:
            continue
        p_discharge = p_t.clip(lower=0)
        energy_per_period = _energy_by_period(p_discharge)
        energy_horizon = (
            p_discharge.multiply(w_series.reindex(p_discharge.index).fillna(0.0), axis=0).sum(axis=0)
        )
        for name in comp_df.index:
            s_carrier = (str(comp_df.at[name, "carrier"]) if "carrier" in comp_df.columns else "").lower()
            intensity = co2_by_carrier.get(s_carrier, 0.0)
            if intensity == 0:
                continue
            eff = 1.0
            if comp_attr == "storage_units" and "efficiency_dispatch" in comp_df.columns:
                try:
                    eff = float(comp_df.at[name, "efficiency_dispatch"])
                except (TypeError, ValueError):
                    eff = 1.0
            if not _math.isfinite(eff) or eff <= 0:
                eff = 1.0
            out_intensity = intensity / eff
            mwh_horizon = float(energy_horizon.get(name, 0.0))
            if not _math.isfinite(mwh_horizon) or mwh_horizon <= 0:
                continue
            tCO2_horizon = mwh_horizon * out_intensity
            total_t += tCO2_horizon
            carrier_totals[s_carrier] = carrier_totals.get(s_carrier, 0.0) + tCO2_horizon
            rows_by_gen.append({
                "name": str(name),
                "carrier": s_carrier,
                "energy_mwh": mwh_horizon,
                "tCO2": tCO2_horizon,
                "intensity_tCO2_per_MWh_out": out_intensity,
                "component": comp_class,
            })
            for period_key, energy_p in energy_per_period.items():
                mwh = float(energy_p.get(name, 0.0))
                if not _math.isfinite(mwh) or mwh <= 0:
                    continue
                tCO2 = mwh * out_intensity
                _accumulate(period_key, tCO2, s_carrier, {
                    "name": str(name),
                    "carrier": s_carrier,
                    "energy_mwh": mwh,
                    "tCO2": tCO2,
                    "intensity_tCO2_per_MWh_out": out_intensity,
                    "component": comp_class,
                })

    by_carrier = sorted(
        [
            {
                "carrier": c,
                "tCO2": t,
                "share_pct": (100.0 * t / total_t) if total_t > 0 else 0.0,
            }
            for c, t in carrier_totals.items()
        ],
        key=lambda r: r["tCO2"], reverse=True,
    )
    rows_by_gen.sort(key=lambda r: r["tCO2"], reverse=True)

    # ── Per-period breakdown (multi-period only) ─────────────────────────
    # Build by_period[] = [{period, total_tCO2, by_carrier, by_generator}]
    # sorted by period. Flat networks emit an empty list.
    by_period_payload: list[dict] = []
    if is_multi:
        sorted_periods = sorted(
            [p_key for p_key in period_totals.keys() if p_key is not None],
            key=lambda x: (0, int(x)) if hasattr(x, "__int__") else (1, str(x)),
        )
        for p_key in sorted_periods:
            total_p = float(period_totals.get(p_key, 0.0))
            carrier_p = period_carrier_totals.get(p_key, {})
            bc = sorted(
                [
                    {
                        "carrier": c,
                        "tCO2": t,
                        "share_pct": (100.0 * t / total_p) if total_p > 0 else 0.0,
                    }
                    for c, t in carrier_p.items()
                ],
                key=lambda r: r["tCO2"], reverse=True,
            )
            gen_rows_p = sorted(
                period_gen_rows.get(p_key, []),
                key=lambda r: r["tCO2"], reverse=True,
            )
            by_period_payload.append({
                "period": p_key,
                "total_tCO2": total_p,
                "by_carrier": bc,
                "by_generator": gen_rows_p,
            })

    # ── CO₂ caps ─────────────────────────────────────────────────────────
    # Detect every primary_energy + co2_emissions constraint. Some may be
    # horizon-wide (no investment_period set), others per-period (the
    # `investment_period` column carries an int year). Surface them in
    # `caps[]`; keep the legacy `cap` field as the first active one for
    # backward compatibility.
    caps: list[dict] = []
    cap_info: dict = {"active": False}
    try:
        if not n.global_constraints.empty:
            gc = n.global_constraints
            mask = (
                (gc["type"].astype(str) == "primary_energy")
                & (gc.get("carrier_attribute", "").astype(str) == "co2_emissions")
            )
            for cap_name in gc.index[mask]:
                cap_value = float(gc.at[cap_name, "constant"])
                mu = float(gc.at[cap_name, "mu"]) if "mu" in gc.columns else 0.0
                # Which period does this cap apply to (if any)?
                ip_raw = gc.at[cap_name, "investment_period"] if "investment_period" in gc.columns else None
                ip_norm: Any = None
                try:
                    if ip_raw is not None and ip_raw == ip_raw:  # not NaN
                        ip_int = int(ip_raw)
                        # PyPSA stores no-period sentinel as -1 or 0 on some
                        # versions; treat anything outside a reasonable year
                        # range as horizon-wide.
                        if 1900 <= ip_int <= 2200:
                            ip_norm = ip_int
                except (TypeError, ValueError):
                    ip_norm = None
                # The slack depends on which scope the cap covers:
                #   • horizon-wide → cap − total_t
                #   • per-period → cap − period's total
                if ip_norm is None:
                    used = total_t
                else:
                    used = float(period_totals.get(ip_norm, 0.0))
                slack = (cap_value - used) if _math.isfinite(cap_value) else None
                cap_entry = {
                    "active": True,
                    "name": str(cap_name),
                    "investment_period": ip_norm,
                    "scope": "period" if ip_norm is not None else "horizon",
                    "cap_tCO2": cap_value if _math.isfinite(cap_value) else None,
                    "used_tCO2": used,
                    "shadow_price_eur_per_tCO2": mu if _math.isfinite(mu) else 0.0,
                    "slack_tCO2": slack,
                    "binding": (slack is not None and abs(slack) < max(1.0, abs(cap_value) * 1e-6)),
                }
                caps.append(cap_entry)
                if not cap_info.get("active"):
                    # Legacy `cap` field — first active cap, preserves the
                    # shape older Emissions.tsx code reads.
                    cap_info = {
                        "active": True,
                        "name": str(cap_name),
                        "cap_tCO2": cap_value if _math.isfinite(cap_value) else None,
                        "shadow_price_eur_per_tCO2": mu if _math.isfinite(mu) else 0.0,
                        "slack_tCO2": slack,
                    }
    except Exception:
        pass

    return {
        "total_tCO2": total_t,
        "by_carrier": by_carrier,
        "by_generator": rows_by_gen,
        "cap": cap_info,
        "caps": caps,
        "is_multi_period": is_multi,
        "by_period": by_period_payload,
    }


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
    import math as _math
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    status = _result_df(n, "generators_t", "status", "lopf")
    if status is None or status.empty:
        return {"generators": [], "status_grid": None, "n_committable": 0,
                "note": "No unit-commitment results. Set committable=True on at "
                "least one generator and re-solve."}

    start_up = _result_df(n, "generators_t", "start_up", "lopf")
    shut_down = _result_df(n, "generators_t", "shut_down", "lopf")
    p = _result_df(n, "generators_t", "p", "lopf")

    # ENERGY basis (energy_mwh + weighted on-hours): use the `generators`
    # column — PyPSA's energy weighting (matches n.statistics() and the Dispatch
    # tab), falling back generators → objective → None on older netcdf. The UC
    # start/shut COSTS below are count-based (not snapshot-weighted), so this
    # only affects the energy figures. Identical when the columns coincide.
    try:
        weights = n.snapshot_weightings.generators
    except Exception:
        try:
            weights = n.snapshot_weightings.objective
        except Exception:
            weights = None

    # Restrict to actually-committable generators. PyPSA writes the status grid
    # only for those — non-committable units have NaN here and we skip them.
    gens = n.generators
    committable_names = []
    if not gens.empty and "committable" in gens.columns:
        committable_names = [str(name) for name in gens.index[gens["committable"]]]

    rows: list[dict] = []
    for name in committable_names:
        if name not in status.columns:
            continue
        s = status[name].fillna(0)
        on_hours = float(s.sum())  # binary so sum = on-count
        # Apply weights if present for the on-hours metric so representative-day
        # workflows scale correctly.
        if weights is not None:
            on_hours_weighted = float((s * weights).sum())
        else:
            on_hours_weighted = on_hours
        n_starts = int(start_up[name].fillna(0).sum()) if (start_up is not None and name in start_up.columns) else 0
        n_shuts = int(shut_down[name].fillna(0).sum()) if (shut_down is not None and name in shut_down.columns) else 0
        # Energy (MWh) only over hours when on.
        if p is not None and name in p.columns:
            p_on = p[name].fillna(0)
            if weights is not None:
                energy = float((p_on * weights).sum())
            else:
                energy = float(p_on.sum())
        else:
            energy = 0.0
        p_nom = float(gens.at[name, "p_nom"]) if "p_nom" in gens.columns else 0.0
        if not _math.isfinite(p_nom):
            p_nom = 0.0
        # CF when on: energy / (p_nom × on_hours). When the unit was always
        # off, return 0 instead of NaN.
        cf_when_on = (100.0 * energy / (p_nom * on_hours_weighted)) if (p_nom > 0 and on_hours_weighted > 0) else 0.0
        su_cost = float(gens.at[name, "start_up_cost"]) if "start_up_cost" in gens.columns else 0.0
        sd_cost = float(gens.at[name, "shut_down_cost"]) if "shut_down_cost" in gens.columns else 0.0
        total_uc_cost = su_cost * n_starts + sd_cost * n_shuts
        rows.append({
            "name": name,
            "carrier": str(gens.at[name, "carrier"]) if "carrier" in gens.columns else "",
            "p_nom_MW": p_nom,
            "n_starts": n_starts,
            "n_shuts": n_shuts,
            "hours_on": on_hours,
            "energy_mwh": energy,
            "capacity_factor_when_on_pct": cf_when_on,
            "total_uc_cost_eur": total_uc_cost,
        })
    rows.sort(key=lambda r: -r["energy_mwh"])

    # The binary status grid for the heatmap. Restrict to committable
    # generators to keep the payload small. Replace NaN with 0 (off) so the
    # frontend doesn't have to handle three-state cells.
    cols = [c for c in status.columns if c in committable_names]
    if cols:
        grid = status[cols].fillna(0).astype(int)
        range_meta = None
        if _wants_slice(from_, to_):
            grid, range_meta = _slice_ts(grid, from_, to_)
        status_payload = _ts_payload(grid, range_meta=range_meta)
    else:
        status_payload = None

    return {
        "generators": rows,
        "status_grid": status_payload,
        "n_committable": len(committable_names),
    }


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
    import math as _math
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    mu_up = _result_df(n, "lines_t", "mu_upper", "lopf")
    mu_lo = _result_df(n, "lines_t", "mu_lower", "lopf")
    p0    = _result_df(n, "lines_t", "p0",       "lopf")
    if (mu_up is None or mu_up.empty) and (mu_lo is None or mu_lo.empty):
        return {"rows": [], "note": "No LP duals captured. Re-run the solve "
                "(assign_all_duals is enabled by default in this build)."}

    try:
        weights = n.snapshot_weightings.objective
    except Exception:
        weights = None

    # Per-period years scaling — mirror get_cost_breakdown so congestion rent
    # is comparable to other € totals shown in the UI. Without this, networks
    # with non-unit `investment_period_weightings.years` understate rent by
    # the years factor.
    import pandas as _pd_ld
    is_multi_period_ld = isinstance(n.snapshots, _pd_ld.MultiIndex)
    period_weight_series = None
    if is_multi_period_ld:
        try:
            years_lookup = period_years_map(n)
            if years_lookup:
                period_lvl = n.snapshots.get_level_values(0)
                period_weight_series = _pd_ld.Series(
                    [years_for_period(years_lookup, p) for p in period_lvl],
                    index=n.snapshots, dtype=float,
                )
        except Exception:
            period_weight_series = None

    # Take the absolute value once — mu_lower is reported as ≤ 0 by PyPSA's
    # sign convention; in plain English a binding lower bound has positive
    # rent magnitude. Pre-fill NaN with 0 so missing-dual snapshots don't
    # propagate through the aggregations.
    mu_up_abs = mu_up.abs().fillna(0.0) if mu_up is not None else None
    mu_lo_abs = mu_lo.abs().fillna(0.0) if mu_lo is not None else None

    line_names = list(n.lines.index)
    rows: list[dict] = []
    for name in line_names:
        u = mu_up_abs[name] if (mu_up_abs is not None and name in mu_up_abs.columns) else None
        l = mu_lo_abs[name] if (mu_lo_abs is not None and name in mu_lo_abs.columns) else None
        # Skip lines that have no dual data at all (e.g. infeasible solves).
        if u is None and l is None:
            continue
        # Use max(|mu_upper|, |mu_lower|) per snapshot — only one bound binds
        # at a time, never both.
        combined = u if l is None else (l if u is None else u.where(u >= l, l))
        # Use a small tolerance for "non-zero" — LP duals can carry numerical
        # noise from interior-point solvers at the 1e-12 level.
        binding = combined > 1e-6
        n_binding = int(binding.sum())
        max_mu = float(combined.max()) if not combined.empty else 0.0
        if n_binding > 0:
            mean_mu = float(combined[binding].mean())
        else:
            mean_mu = 0.0
        # Congestion rent: ∫ |mu| × |p0| dt. The LP dual is shadow price per
        # unit capacity; multiplied by actual flow it approximates the
        # annual welfare value of redispatching out of this congestion.
        # Multi-period: also scale by investment_period_weightings.years
        # so the total reads as horizon-cumulative (matches cost_breakdown).
        if p0 is not None and name in p0.columns:
            p_abs = p0[name].abs().fillna(0.0)
            row_w = combined * p_abs
            if weights is not None:
                row_w = row_w * weights
            if period_weight_series is not None:
                row_w = row_w * period_weight_series
            rent = float(row_w.sum())
        else:
            rent = 0.0
        if not _math.isfinite(max_mu): max_mu = 0.0
        if not _math.isfinite(mean_mu): mean_mu = 0.0
        if not _math.isfinite(rent): rent = 0.0
        # Look up s_nom for context — "this line binds 50 % of hours at 100 MW
        # is more interesting than at 10 GW".
        try:
            s_nom = float(n.lines.at[name, "s_nom"])
        except Exception:
            s_nom = 0.0
        # Detect VOLL-bound binding hours: dual ≥ 10,000 €/MWh is almost
        # never physical congestion — it's a load-shedding signal where
        # the LP is choosing to shed rather than relax the line. Surface
        # this count so the UI can warn users that "congestion rent" on
        # this line largely reflects the cost of unmet demand, not the
        # value of transmission expansion.
        VOLL_THRESHOLD = 10_000.0
        voll_bound = int((combined > VOLL_THRESHOLD).sum())
        rows.append({
            "name": str(name),
            "s_nom_MW": s_nom if _math.isfinite(s_nom) else 0.0,
            "binding_hours": n_binding,
            "voll_bound_hours": voll_bound,
            "max_mu_eur_per_MWh": max_mu,
            "mean_mu_when_binding_eur_per_MWh": mean_mu,
            "congestion_rent_eur": rent,
        })

    rows.sort(key=lambda r: r["congestion_rent_eur"], reverse=True)
    total_rent = sum(r["congestion_rent_eur"] for r in rows)
    return {
        "rows": rows,
        "total_congestion_rent_eur": float(total_rent),
        "n_snapshots": len(n.snapshots),
    }


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
    import numpy as np
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    try:
        df = _result_df(n, "buses_t", "marginal_price", source)
        if df is None or df.empty:
            return _not_solved()
        # Replace NaN with 0 explicitly — JSON encoders convert NaN to null
        # which the frontend then has to handle. Zero is the right semantic
        # default for non-binding constraints, and we use a separate `source`
        # field to flag when prices are unreliable.
        df = df.fillna(0.0)

        # Diagnostic: are the duals actually informative? "All zero" usually
        # means the LP didn't surface dual variables (solver config) OR a
        # very cheap (or negative-cost) generator with abundant headroom
        # serves every snapshot, making the dual mathematically 0. Provide a
        # generator-cost-based fallback the UI can show alongside.
        all_zero = bool(np.allclose(df.values, 0.0))
        fallback_per_snapshot: list[float] = []
        if all_zero:
            try:
                gens_p = n.generators_t.p
                if not gens_p.empty:
                    mc = n.generators["marginal_cost"].reindex(gens_p.columns).fillna(0.0)
                    # Per snapshot: highest marginal_cost among generators with p > epsilon.
                    eps = 1e-3
                    for i in range(len(gens_p.index)):
                        row = gens_p.iloc[i]
                        active = row.index[row.abs() > eps]
                        if len(active) == 0:
                            fallback_per_snapshot.append(0.0)
                        else:
                            fallback_per_snapshot.append(float(mc.loc[active].max()))
            except Exception:
                fallback_per_snapshot = []

        # Merit-order ("subsidy-removed") view: the curtailment_cost extra-
        # functionality term in solver_service adds `-cost × p` to the LP
        # objective for any renewable with curtailment_cost > 0. That makes
        # the renewable's effective marginal cost `marginal_cost - cost`,
        # and by LP duality the bus price equals that effective cost when
        # the renewable is the marginal unit — i.e. it dispatches strictly
        # between 0 and p_max_pu × p_nom_opt. The result reads as
        # "negative price" even though physically nothing is being paid.
        #
        # To give users a "merit order" view that ignores the dispatch
        # subsidy, add the curtailment_cost of the marginal renewable back
        # to the LP dual for every (bus, snapshot) where one is active.
        # When no renewable is marginal at that cell the LP dual already
        # reflects the true merit order and we leave it alone.
        data_adjusted: list[list[float]] = []
        negative_hours = 0
        try:
            gens = n.generators
            if (not gens.empty
                    and "curtailment_cost" in gens.columns
                    and not n.generators_t.p.empty):
                subsidised = gens.index[gens["curtailment_cost"].fillna(0) > 0]
                if len(subsidised) > 0:
                    p = n.generators_t.p
                    p_max_pu = n.get_switchable_as_dense("Generator", "p_max_pu")
                    p_nom_opt = (gens["p_nom_opt"]
                                 if "p_nom_opt" in gens.columns
                                 else gens["p_nom"])
                    eps = 1e-6
                    dual_tol = 1.0  # €/MWh — LP duals are exact to numerical eps
                    # bus → list of (gen_name, curtailment_cost, real_marginal_cost)
                    by_bus: dict[str, list[tuple[str, float, float]]] = {}
                    for g in subsidised:
                        if g not in p.columns:
                            continue
                        bus = str(gens.at[g, "bus"])
                        cost = float(gens.at[g, "curtailment_cost"])
                        real_mc = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
                        by_bus.setdefault(bus, []).append((g, cost, real_mc))
                    adj = df.copy()
                    # Targeted merit-order adjustment — same logic as
                    # /asset_economics. Fire only when the LP dual at this
                    # (bus, snapshot) actually equals the renewable's
                    # effective LP MC (real_mc - curtailment_cost), within
                    # 1 €/MWh tolerance. That's the unambiguous diagnostic
                    # that THIS renewable is setting the dual via the
                    # subsidy term. The previous loose rule ("fires whenever
                    # renewable is strictly between 0 and ceiling") was
                    # too narrow — it skipped the common case of renewable
                    # AT its ceiling with dual still pinned at effective MC
                    # (QA: 2756 negative cells raw, only 27 lifted by old
                    # rule). The new rule covers AT-the-ceiling cases too
                    # by checking the dual diagnostic instead of the
                    # operational position.
                    for bus, members in by_bus.items():
                        if bus not in adj.columns:
                            continue
                        for i in range(len(p.index)):
                            t = p.index[i]
                            raw_dual = float(adj.at[t, bus])
                            for g, cost, real_mc in members:
                                pv = float(p.at[t, g])
                                float(p_max_pu.at[t, g]) * float(p_nom_opt.loc[g])
                                # Renewable must be dispatching (pv > 0). It
                                # can be either strictly in the middle OR at
                                # the ceiling — both can be setting the dual
                                # at effective_lp_mc under LP duality.
                                if pv <= eps:
                                    continue
                                effective_lp_mc = real_mc - cost
                                if abs(raw_dual - effective_lp_mc) <= dual_tol:
                                    adj.at[t, bus] = real_mc
                                    break  # don't double-adjust
                    adj = adj.fillna(0.0)
                    data_adjusted = _safe_values(adj)
            if not data_adjusted:
                data_adjusted = _safe_values(df)
        except Exception:
            data_adjusted = _safe_values(df)

        # Count snapshots that the user is likely to see as "negative" so
        # the frontend can show a hint without re-scanning the whole grid.
        # A whole-horizon aggregate (not a per-snapshot array) — it does NOT
        # narrow with a `from`/`to` window below; `range.complete` is what
        # tells the frontend whether it reflects the full series or a slice.
        try:
            negative_hours = int((df.values < -1e-6).any(axis=1).sum())
        except Exception:
            negative_hours = 0

        # Use _ts_payload for the index+columns+data+periods shape, then merge
        # in the prices-specific extras. Keeps multi-period periods array
        # consistent with every other /results/* endpoint.
        range_meta = None
        if _wants_slice(from_, to_):
            df, range_meta = _slice_ts(df, from_, to_)
            # data_adjusted / fallback_per_snapshot are per-snapshot arrays
            # computed above against the FULL (unsliced) frame — same
            # positionally-aligned-to-the-rows shape as `data`. Slice them to
            # the bounds slice_ts ACTUALLY served (range_meta, not the raw
            # from_/to_ — slice_ts clamps and may cap) or a ranged response
            # carries N sliced `data` rows next to the full-length arrays,
            # and a UI indexing data_adjusted[i] against data[i] silently
            # renders another snapshot's price as if it were this one's.
            lo, hi = range_meta["from"], range_meta["to"]
            data_adjusted = data_adjusted[lo : hi + 1]
            fallback_per_snapshot = fallback_per_snapshot[lo : hi + 1]
        return _ts_payload(df, extra={
            # Merit-order ("subsidy-removed") version. Same shape as `data`.
            # When the LP-dual ALREADY reflects the merit order at a given
            # cell (no marginal subsidised renewable there), the value
            # equals the LP dual — so flipping the toggle has no effect on
            # those hours, which is the right semantics.
            "data_adjusted": data_adjusted,
            "negative_hours": negative_hours,
            # New diagnostic fields. UI can use these to render a banner like
            # "LP duals were all zero — showing analytical prices instead".
            "source": "fallback" if all_zero and fallback_per_snapshot else "lp",
            "fallback_per_snapshot": fallback_per_snapshot if all_zero else [],
            "note": (
                "LP duals are zero — most likely either (a) solver skipped "
                "dual extraction, or (b) demand is served by ample low-cost "
                "capacity so an extra MW would cost 0. The fallback shows "
                "the highest-marginal-cost dispatching generator per snapshot."
                if all_zero and fallback_per_snapshot else
                ""
            ),
        }, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()


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
    import math
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    try:
        prices = n.buses_t.marginal_price
        if prices.empty:
            return _not_solved()
        gens_p = n.generators_t.p if hasattr(n.generators_t, "p") else None
        if gens_p is None or gens_p.empty:
            return _not_solved()
        gens = n.generators
        # Bus → list of generator names that connect to it. Pre-built so
        # we don't re-scan n.generators per cell.
        gens_by_bus: dict[str, list[str]] = {}
        for name in gens.index:
            bus = str(gens.at[name, "bus"]) if "bus" in gens.columns else ""
            gens_by_bus.setdefault(bus, []).append(str(name))
        thr = float(threshold)
        # Collect rows above threshold first, then sort + truncate.
        rows: list[dict] = []
        for col in prices.columns:
            series = prices[col]
            for t, v in series.items():
                try:
                    pv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(pv) or abs(pv) <= thr:
                    continue
                # Find marginal generator: dispatching > 1e-3 at this t,
                # connected to this bus, with marginal_cost closest to |pv|.
                bus = str(col)
                candidates = gens_by_bus.get(bus, [])
                best_name: str | None = None
                best_diff = float("inf")
                best_mc = 0.0
                best_carrier = ""
                best_dispatch = 0.0
                voll_slack_active = False
                voll_dispatch = 0.0
                for g in candidates:
                    if g not in gens_p.columns:
                        continue
                    try:
                        disp = float(gens_p.at[t, g])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if abs(disp) <= 1e-3:
                        continue
                    mc = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
                    carrier = str(gens.at[g, "carrier"]) if "carrier" in gens.columns else ""
                    # Membership tests via the shared convention owner
                    # (services/adequacy/slack.py) — also catches the legacy
                    # voll_slack spellings the AC-PF strip defends against.
                    if _slack.is_slack_name(g) or _slack.is_slack_carrier(carrier):
                        voll_slack_active = True
                        voll_dispatch = disp
                        # VOLL slack wins unconditionally for diagnosis — any
                        # dispatch from it means the LP is shedding load.
                        best_name = g
                        best_mc = mc
                        best_carrier = carrier
                        best_dispatch = disp
                        break
                    diff = abs(mc - abs(pv))
                    if diff < best_diff:
                        best_diff = diff
                        best_name = g
                        best_mc = mc
                        best_carrier = carrier
                        best_dispatch = disp
                # Diagnosis tag — one of:
                #   load_shedding    — VOLL slack dispatching, OR observed
                #                       price grossly exceeds any dispatching
                #                       gen's MC (price ≥ 10× max(mc)).
                #                       Catches cases where VOLL slack wasn't
                #                       named __voll_* but was added inline.
                #   thermal_peaker   — marginal gen's mc is within 5% of |price|
                #                       AND > 100 €/MWh
                #   transmission     — price >> any dispatching gen's mc but
                #                       within a reasonable scarcity multiple;
                #                       contingency / line bind is what's driving
                #   unattributed     — couldn't find any dispatching gen on the bus
                MAX_MC_MULTIPLIER = 10.0  # price ≥ N × max(MC) → load_shedding
                if voll_slack_active:
                    diag = "load_shedding"
                elif best_name is None:
                    diag = "unattributed"
                elif best_mc > 0 and abs(pv) >= MAX_MC_MULTIPLIER * best_mc and abs(pv) >= 1000.0:
                    # Price is orders of magnitude above the gen's MC. Even if
                    # the slack generator wasn't found, the LP is effectively
                    # shedding (or near-shedding) load at this bus.
                    diag = "load_shedding"
                elif best_mc > 100 and abs(best_mc - abs(pv)) / max(abs(pv), 1.0) < 0.05:
                    diag = "thermal_peaker"
                else:
                    diag = "transmission"
                # Multi-period: `t` is a (period, timestep) tuple — has no
                # `.isoformat`, so the fallback `str(t)` would produce the
                # tuple-string repr ("(2026, Timestamp('...'))") consumers
                # can't parse. Split into period + ISO timestep instead.
                if isinstance(t, tuple) and len(t) == 2:
                    period_val = t[0]
                    ts_val = t[1]
                    try:
                        period_out = int(period_val)
                    except (TypeError, ValueError):
                        period_out = str(period_val)
                    snap_iso = ts_val.isoformat() if hasattr(ts_val, "isoformat") else str(ts_val)
                else:
                    period_out = None
                    snap_iso = t.isoformat() if hasattr(t, "isoformat") else str(t)
                row = {
                    "snapshot": snap_iso,
                    "bus": bus,
                    "price": pv,
                    "marginal_gen": best_name,
                    "marginal_cost": best_mc,
                    "carrier": best_carrier,
                    "dispatch": best_dispatch,
                    "voll_slack_active": voll_slack_active,
                    "voll_dispatch": voll_dispatch,
                    "diagnosis": diag,
                }
                if period_out is not None:
                    row["period"] = period_out
                rows.append(row)
        rows.sort(key=lambda r: abs(r["price"]), reverse=True)
        truncated = len(rows) > limit
        return {
            "threshold": thr,
            "total_above_threshold": len(rows),
            "truncated": truncated,
            "rows": rows[:limit],
        }
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()


@results_router.get("/curtailment")
def get_curtailment(
    from_: int | None = Query(None, alias="from", description="Inclusive start index into the snapshot axis."),
    to_: int | None = Query(None, alias="to", description="Inclusive end index into the snapshot axis."),
):
    import pandas as _pd
    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()
    try:
        p = n.generators_t.p
        if p.empty:
            return _not_solved()
        # Align p_nom to p.columns up front. Without this, generators in p
        # that aren't in n.generators (rare but possible after carrier edits)
        # would give NaN columns and crash JSON encoding.
        cap_col = "p_nom_opt" if "p_nom_opt" in n.generators.columns else "p_nom"
        p_nom = n.generators[cap_col].reindex(p.columns).fillna(0.0)

        # PyPSA stores time-varying p_max_pu only for generators that actually
        # have a profile — others fall back to the static n.generators.p_max_pu
        # (default 1.0). Build a full-column DataFrame so the subtraction below
        # produces a clean DataFrame with no NaN columns.
        p_max_pu = n.generators_t.p_max_pu
        if p_max_pu.empty:
            # Constant p_max_pu = 1.0 for every generator → p_max ≡ p_nom.
            p_max = (p * 0.0).add(p_nom, axis=1)
        else:
            p_max_pu_full = p_max_pu.reindex(columns=p.columns, fill_value=1.0)
            p_max = p_max_pu_full.multiply(p_nom, axis=1)

        # ── Time-varying effective capacity (multi-period vintages) ─────
        # vintage_service aggregates vintage p_nom_opt into the parent
        # post-solve (parent's column = parent + Σ vintages). But each
        # vintage is only physically active from its build_year onwards
        # — `p` is forced to 0 in earlier snapshots. Using the AGGREGATED
        # p_nom_opt × p_max_pu gives a phantom p_max that exceeds what
        # any of the contributing vintages could actually deliver in
        # that snapshot. The endpoint then reports massive curtailment
        # that doesn't exist (e.g. 920 GWh of fake curtailment in 2026
        # from a 293-MW vintage built in 2028 whose capacity was rolled
        # into Solar2's p_nom_opt).
        #
        # Fix: rebuild a per-(snapshot, generator) effective capacity by
        # walking `n.meta["vintage_results"]` and summing only vintages
        # with build_year ≤ snapshot's period. For generators without
        # vintage_results, use the aggregated p_nom_opt (unchanged).
        if isinstance(p.index, _pd.MultiIndex):
            try:
                period_lvl = p.index.get_level_values(0).astype(int)
                vintage_results = (n.meta or {}).get("vintage_results", {}) if hasattr(n, "meta") else {}
                gen_vr = vintage_results.get("Generator", {}) if isinstance(vintage_results, dict) else {}
                if gen_vr:
                    # Build per-column time-varying effective capacity. Start
                    # from the existing p_max (= p_nom_opt × p_max_pu) and
                    # OVERRIDE columns that have vintage_results data.
                    for gname in p.columns:
                        meta = gen_vr.get(gname)
                        if not meta:
                            continue
                        initial = float(meta.get("initial_capacity", 0.0) or 0.0)
                        periods_meta = meta.get("periods", []) or []
                        # Vector of effective_p_nom per snapshot for this gen.
                        eff = _pd.Series(initial, index=p.index, dtype=float)
                        for entry in periods_meta:
                            try:
                                by = int(entry.get("build_year"))
                                pn = float(entry.get("p_nom_opt", 0.0) or 0.0)
                            except (TypeError, ValueError):
                                continue
                            if pn <= 0:
                                continue
                            # Active in snapshots whose period >= build_year.
                            mask = period_lvl >= by
                            eff.values[mask] += pn
                        # Re-compute p_max for this column using time-varying
                        # effective capacity. p_max_pu_full has it as the
                        # snapshot-indexed profile we built above.
                        if p_max_pu.empty or gname not in p_max_pu_full.columns:
                            p_max[gname] = eff
                        else:
                            p_max[gname] = p_max_pu_full[gname] * eff
            except Exception:
                pass  # defensive — fall back to unmasked behaviour

        # fillna(0) is defensive — covers any residual NaN in `p` or column
        # alignment edge cases. Required because JSON encoders reject NaN.
        curtailment = (p_max - p).clip(lower=0).fillna(0.0)
        # Filter to generators where (p_max - p) is genuinely "curtailment":
        # renewables (free energy that's wasted) OR generators with explicit
        # curtailment_cost > 0 (the LP penalty signals user intent).
        # Without this filter, thermal "headroom" (unused dispatch capacity
        # of a 200 MW thermal running at 50 MW) gets reported as curtailment
        # — confusing in raw CSV exports and downstream consumers.
        RENEW_KEYWORDS = ("wind", "solar", "pv", "ror", "geothermal",
                          "offwind", "onwind", "hydro", "biomass",
                          "wave", "tidal", "rooftop")
        keep_cols: list[str] = []
        gens_df = n.generators
        for col in curtailment.columns:
            if col not in gens_df.index:
                continue
            carrier = str(gens_df.at[col, "carrier"]).lower() if "carrier" in gens_df.columns else ""
            is_renew = any(k in carrier for k in RENEW_KEYWORDS)
            has_subsidy = False
            if "curtailment_cost" in gens_df.columns:
                cc_val = gens_df.at[col, "curtailment_cost"]
                try:
                    has_subsidy = float(cc_val) > 0
                except (TypeError, ValueError):
                    has_subsidy = False
            if is_renew or has_subsidy:
                keep_cols.append(col)
        if keep_cols:
            curtailment = curtailment[keep_cols]
        else:
            # No curtailable generators in the network — return an empty-shaped
            # payload (preserves index, drops all columns) rather than 204.
            curtailment = curtailment.iloc[:, 0:0]
        range_meta = None
        if _wants_slice(from_, to_):
            curtailment, range_meta = _slice_ts(curtailment, from_, to_)
        return _ts_payload(curtailment, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()


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
    if sweep and sweep.get("status") == "done":
        for r in sweep.get("rows", []):
            if r.get("failure_mode"):
                per_mode.append({**r["failure_mode"],
                                 "delta_eue_mwh": r.get("delta_eue_mwh")})
    if not per_mode:
        return Response(status_code=204)
    per_mode.sort(key=lambda r: float(r.get("criticality_eur_per_year", 0.0)),
                  reverse=True)
    return {"per_mode": per_mode,
            "sweep_status": (sweep or {}).get("status"),
            "sweep_error": (sweep or {}).get("error")}


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
    return {k: v for k, v in st.items() if k != "thread"}


class FmeaSweepRequest(_BaseModel):
    # Class-C scenarios, passed by the client from the authorized registry
    # GET (/api/projects/{name}/stress_scenarios) — this route operates on
    # the FOREGROUND network and carries no project name, so the sidecar is
    # read where authorization lives and re-validated here before running.
    scenarios: list = []


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

    st = _state.get("fmea_sweep")
    if st and st.get("status") == "running" and st.get("thread") is not None             and st["thread"].is_alive():
        raise HTTPException(409, "an FMEA sweep is already running")
    if _state.get("status") == "running":
        raise HTTPException(409, "a solve is running — wait for it to finish")
    cfg = _state.get("solver_config")
    if cfg is None or float(getattr(cfg, "voll", 0.0) or 0.0) <= 0:
        raise HTTPException(422, "the sweep requires a VOLL > 0 in solver settings")
    n = PyPSAService.get_network()
    lock = PyPSAService.get_lock()

    scenarios = list(getattr(body, "scenarios", None) or [])

    def worker():
        try:
            # Class B first with a private final sink; the LAST sweep's
            # closing base re-solve writes the REAL state sink, so
            # /results/lost_load etc. reflect base afterwards.
            rows = run_class_b_sweep(
                n, lock, cfg,
                final_state_update=None if scenarios else _state_update,
            )
            if scenarios:
                rows = rows + run_class_c_sweep(
                    n, lock, cfg, scenarios,
                    final_state_update=_state_update,
                )
            _state["fmea_sweep"].update(
                status="done", rows=rows, finished_at=time.time(), error=None)
        except (SweepBudgetError, StressValidationError) as exc:
            _state["fmea_sweep"].update(
                status="failed", rows=[], error=str(exc), finished_at=time.time())
        except Exception as exc:  # noqa: BLE001
            _state["fmea_sweep"].update(
                status="failed", rows=[], error=str(exc), finished_at=time.time())

    t = _threading.Thread(target=worker, daemon=True, name="fmea-sweep")
    _state["fmea_sweep"] = {"status": "running", "rows": [], "error": None,
                            "started_at": time.time(), "thread": t}
    t.start()
    return {"status": "running"}


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
    from services.adequacy.copt import (
        attribute_criticality,
        build_copt,
        fleet_and_residual,
        hourly_adequacy,
    )

    n = PyPSAService.get_network()
    units, residual, w = fleet_and_residual(n)
    if not units:
        return Response(status_code=204)
    dist = build_copt(units, delta_mw=1.0)
    metrics = hourly_adequacy(dist, residual, weights=w)
    cfg = _state.get("solver_config")
    voll = float(getattr(cfg, "voll", 0.0) or 0.0)
    rows = attribute_criticality(units, dist, residual, weights=w, voll=voll)
    # Count must-take by re-deriving membership is wasteful; infer from the
    # generator walk instead: electrical non-slack gens minus COPT units.
    from services.adequacy.metrics import electrical_columns
    from services.adequacy.slack import slack_generator_mask
    gens = n.generators
    elec = set(electrical_columns(n, list(n.buses.index)))
    slack = slack_generator_mask(gens)
    n_elec_gens = sum(
        1 for g in gens.index
        if not bool(slack.get(g, False)) and str(gens.at[g, "bus"]) in elec
    )
    return {
        "engine": "copt",
        "fidelity": "analytic_convolution",
        "metrics": {
            "lole_hours": metrics["lole_hours"],
            "eue_mwh": metrics["eue_mwh"],
            "lolp_max": metrics["lolp_max"],
            "by_period": metrics["by_period"],
            "time_basis": "hours_per_year",
        },
        "per_mode": [
            {**r["failure_mode"],
             "delta_eue_mwh": r["delta_eue_mwh"]}
            for r in rows
        ],
        "fleet": {
            "units": len(units),
            "must_take": n_elec_gens - len(units),
            "delta_mw": 1.0,
        },
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

    ``from_state``: when True (default, live network) the LP-stage `_state`
    result snapshot takes priority via ``_result_df``. When False (e.g. a
    freshly-loaded Compare bundle ``temp_n``) read ``n.loads_t.p`` DIRECTLY —
    ``_result_df`` would otherwise return the LIVE network's cached
    `_state['lopf_results']` and cross-contaminate the comparison.
    """
    import pandas as _pd
    if from_state:
        try:
            df = _result_df(n, "loads_t", "p", source)
        except Exception:
            df = None
    else:
        df = getattr(getattr(n, "loads_t", None), "p", None)
    already_scaled = df is not None and not df.empty
    if not already_scaled:
        df = getattr(getattr(n, "loads_t", None), "p_set", None)
    if df is None or df.empty:
        return None
    load_scalers = getattr(cfg, "load_scalers", {}) if cfg is not None else {}
    by_carrier = getattr(cfg, "load_scalers_by_carrier", {}) if cfg is not None else {}
    multi_periods = isinstance(df.index, _pd.MultiIndex)
    has_any_scaling = bool(load_scalers) or bool(by_carrier)
    if not already_scaled and multi_periods and has_any_scaling:
        from services.solver_service import _canonical_load_carrier_key
        df = df.copy(deep=True)
        carrier_by_col: dict = {}
        try:
            loads_df = n.loads
            if "carrier" in loads_df.columns:
                for col in df.columns:
                    carrier_by_col[col] = (
                        _canonical_load_carrier_key(loads_df.at[col, "carrier"])
                        if col in loads_df.index else "electrical"
                    )
            else:
                for col in df.columns:
                    carrier_by_col[col] = "electrical"
        except Exception:
            carrier_by_col = {col: "electrical" for col in df.columns}
        period_level = df.index.get_level_values(0)
        for period in sorted(set(period_level)):
            mask = period_level == period
            p_str = str(period)
            for col in df.columns:
                carrier_key = carrier_by_col.get(col, "electrical")
                factor = None
                car_block = by_carrier.get(carrier_key) if isinstance(by_carrier, dict) else None
                if isinstance(car_block, dict):
                    raw = car_block.get(p_str)
                    if raw is not None:
                        try:
                            f = float(raw)
                            if f == f:
                                factor = f
                        except (TypeError, ValueError):
                            pass
                if factor is None and load_scalers:
                    raw = load_scalers.get(p_str)
                    if raw is not None:
                        try:
                            f = float(raw)
                            if f == f:
                                factor = f
                        except (TypeError, ValueError):
                            pass
                if factor is None or factor == 1.0:
                    continue
                df.loc[mask, col] = df.loc[mask, col] * factor
    return df


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
    try:
        df = lp_scaled_load_frame(n, _state.get("solver_config"), source)
        if df is None or df.empty:
            return _not_solved()
        range_meta = None
        if _wants_slice(from_, to_):
            df, range_meta = _slice_ts(df, from_, to_)
        return _ts_payload(df, range_meta=range_meta)
    except Exception:
        logger.exception("results endpoint failed; returning 204 (see traceback)")
        return _not_solved()


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
    import pandas as _pd
    if from_state:
        try:
            prices = _result_df(n, "buses_t", "marginal_price", "lopf")
        except Exception:
            prices = None
    else:
        prices = getattr(getattr(n, "buses_t", None), "marginal_price", None)
    if prices is None or prices.empty:
        return _pd.DataFrame(0.0, index=n.snapshots, columns=n.buses.index)
    prices = prices.fillna(0.0)
    try:
        gens = n.generators
        if (not gens.empty
                and "curtailment_cost" in gens.columns
                and not n.generators_t.p.empty):
            subsidised = gens.index[gens["curtailment_cost"].fillna(0) > 0]
            if len(subsidised) > 0:
                p_gens = n.generators_t.p
                p_max_pu_full = n.get_switchable_as_dense("Generator", "p_max_pu")
                p_nom_opt = (gens["p_nom_opt"]
                             if "p_nom_opt" in gens.columns
                             else gens["p_nom"])
                eps = 1e-6
                dual_tol = 1.0  # EUR/MWh — LP duals are exact to numerical eps
                by_bus: dict[str, list[tuple[str, float, float]]] = {}
                for g in subsidised:
                    if g not in p_gens.columns:
                        continue
                    bus = str(gens.at[g, "bus"])
                    cost = float(gens.at[g, "curtailment_cost"])
                    real_mc = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
                    by_bus.setdefault(bus, []).append((g, cost, real_mc))
                if by_bus:
                    prices = prices.copy()
                    for bus, members in by_bus.items():
                        if bus not in prices.columns:
                            continue
                        for i in range(len(p_gens.index)):
                            t = p_gens.index[i]
                            raw_dual = float(prices.at[t, bus])
                            for g, cost, real_mc in members:
                                pv = float(p_gens.at[t, g])
                                if pv <= eps:
                                    continue
                                effective_lp_mc = real_mc - cost
                                if abs(raw_dual - effective_lp_mc) <= dual_tol:
                                    prices.at[t, bus] = real_mc
                                    break
                                if raw_dual < effective_lp_mc - dual_tol:
                                    try:
                                        pmp = float(p_max_pu_full.at[t, g])
                                        nom = float(p_nom_opt.get(g, 0.0))
                                        ceiling = pmp * nom
                                    except Exception:
                                        ceiling = None
                                    if ceiling is not None and ceiling > eps and abs(pv - ceiling) <= 1e-3 * max(ceiling, 1.0):
                                        prices.at[t, bus] = real_mc
                                        break
                    prices = prices.fillna(0.0)
    except Exception:
        pass  # defensive — keep raw LP duals if adjustment fails
    return prices


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
    import math

    import numpy as _np
    import pandas as _pd

    n = PyPSAService.get_network()
    if not _dispatch_ready(n):
        return _not_solved()

    cfg = _state["solver_config"]

    try:
        # ── Pre-compute the effective annualised capital_cost for every asset.
        # Mirrors the same context the cost_breakdown endpoint uses — so the
        # numbers reconcile (cost_breakdown.capex = Σ fixed_cost across assets).
        asset_costs = periodized_capital_costs(n, cfg)
    except Exception:
        asset_costs = {}

    # ── Snapshot + period weighting helpers ──────────────────────────────
    # Multi-period: `snapshot_weightings.objective` carries the per-row weight
    # (representative-week scaling lives here), and `investment_period_weightings.years`
    # carries the per-period multiplier. Flat snapshots: only the snapshot
    # weight applies.
    try:
        sw_obj = n.snapshot_weightings["objective"]
    except (KeyError, AttributeError):
        sw_obj = None
    # Energy basis: the `generators` column — PyPSA's n.statistics() energy
    # weight, the same basis the Dispatch tab uses. Cost terms (revenue, VOM,
    # charge cost) keep `objective`; ENERGY denominators (energy_mwh,
    # discharge/charge_mwh, capacity-factor hours) use this. Falls back to the
    # objective column when absent (older netcdf) — identical to prior behaviour.
    try:
        sw_gen = n.snapshot_weightings["generators"]
    except (KeyError, AttributeError):
        sw_gen = sw_obj
    period_years_lookup = period_years_map(n)
    # Horizon scaling factor for time-basis alignment between annual
    # CAPEX and horizon-summed OPEX/dispatch. Without this, LCOS for
    # storage and LCOE for generators are computed as (annual CAPEX +
    # horizon OPEX + horizon charge_cost) / horizon discharge — mixing
    # units. Documented in CLAUDE.md "LCOH/LCOE fleet aggregation mixing
    # single-year CAPEX with horizon-total OPEX". Surfaced by user:
    # Battery 1 LCOS = 4 €/MWh in this view vs 19.6 €/MWh in Compare
    # View, a ~5× ratio that exactly tracks the n_periods × annual mismatch.
    total_years_factor = (
        float(sum(period_years_lookup.values()))
        if period_years_lookup else 1.0
    )

    is_multi = isinstance(n.snapshots, _pd.MultiIndex)
    if is_multi:
        try:
            period_lvl = n.snapshots.get_level_values(0)
        except Exception:
            period_lvl = None
    else:
        period_lvl = None

    def _years_for_period(p) -> float:
        return years_for_period(period_years_lookup, p)

    def _weight_series_for(snapshots, sw) -> _pd.Series:
        """Per-row effective weight = sw × period.years (sw column: objective=cost, generators=energy)."""
        w = _pd.Series(1.0, index=snapshots, dtype=float)
        if sw is not None:
            try:
                w = w.multiply(sw.reindex(snapshots).fillna(1.0), axis=0)
            except Exception:
                pass
        if period_lvl is not None and period_years_lookup:
            try:
                years = _pd.Series(
                    [_years_for_period(p) for p in period_lvl],
                    index=snapshots, dtype=float,
                )
                w = w.multiply(years, axis=0)
            except Exception:
                pass
        return w

    # ── Per-row weights for the network's current snapshots ──────────────
    # Two bases: w_vals (objective) for COST quantities (revenue, VOM, charge
    # cost); w_vals_energy (generators) for ENERGY quantities (energy_mwh,
    # discharge/charge_mwh, capacity-factor hours). Equal when the columns
    # coincide; LCOE/LCOS = cost[objective] / energy[generators].
    snapshots = n.snapshots
    w_series = _weight_series_for(snapshots, sw_obj)
    w_vals = w_series.values
    w_series_energy = _weight_series_for(snapshots, sw_gen)
    w_vals_energy = w_series_energy.values
    # Period vector (same length as snapshots) for grouping later.
    if is_multi and period_lvl is not None:
        period_keys = [
            (int(p) if hasattr(p, "__int__") else p) for p in period_lvl
        ]
    else:
        period_keys = [None] * len(snapshots)

    def _safe_finite(x: float) -> float:
        return 0.0 if x is None or not math.isfinite(x) else float(x)

    def _accumulate_per_period(
        series: _pd.Series,
        weights: _np.ndarray,
    ) -> tuple[float, dict]:
        """
        Sum (value × weight) across rows; also bucket by period.

        Returns (total, by_period_dict). For flat snapshots `by_period_dict`
        is empty (single-period collapses to the total).
        """
        vals = series.values
        # Vectorised weighted total. NaN/Inf → 0 so JSON serialises.
        finite_mask = _np.isfinite(vals) & _np.isfinite(weights)
        weighted = _np.where(finite_mask, vals * weights, 0.0)
        total = float(weighted.sum())
        if not is_multi:
            return total, {}
        by_p: dict = {}
        for i, p in enumerate(period_keys):
            if p is None:
                continue
            by_p[p] = by_p.get(p, 0.0) + float(weighted[i])
        return total, by_p

    # ── Marginal prices per bus (one bus per row in n.buses) ─────────────
    try:
        prices = _result_df(n, "buses_t", "marginal_price", "lopf")
    except Exception:
        prices = None
    if prices is None or prices.empty:
        prices = _pd.DataFrame(0.0, index=snapshots, columns=n.buses.index)
    # Replace NaN with 0 so missing duals don't poison weighted sums.
    prices = prices.fillna(0.0)

    # ── Merit-order ("subsidy-removed") price adjustment ─────────────────
    # The curtailment_cost extra-functionality term in solver_service adds
    # `-cost × p` to the LP objective for any renewable with
    # curtailment_cost > 0. That distorts the LP dual at the renewable's
    # bus: when the renewable is the marginal unit (dispatching strictly
    # between 0 and p_max_pu × p_nom_opt), the dual equals its effective
    # LP MC, i.e. (marginal_cost − curtailment_cost). With marginal_cost=0
    # and a typical curtailment_cost of 100–5000 €/MWh, the dual is large
    # and negative — and any asset trading against that bus sees a
    # "negative charge cost" or "negative revenue" that's not physical
    # (no money flows; the negative number is purely the LP's internal
    # accounting).
    #
    # The fix here is TARGETED — fire ONLY when the LP dual actually
    # equals the renewable's effective LP MC (within a small tolerance),
    # which is the diagnostic signal that the renewable IS the unit
    # setting the dual. A naive "renewable strictly between 0 and
    # ceiling → adjust" rule over-corrects on real networks where
    # other binding constraints (line limits, ramping, storage SoC)
    # determine the dual while the subsidised renewable just happens
    # to be operating in the middle of its range — that produces prices
    # 30×–50× too high (observed in QA: avg charge price jumping from
    # +50 €/MWh to +1700 €/MWh).
    try:
        gens = n.generators
        if (not gens.empty
                and "curtailment_cost" in gens.columns
                and not n.generators_t.p.empty):
            subsidised = gens.index[gens["curtailment_cost"].fillna(0) > 0]
            if len(subsidised) > 0:
                p_gens = n.generators_t.p
                p_max_pu_full = n.get_switchable_as_dense("Generator", "p_max_pu")
                p_nom_opt = (gens["p_nom_opt"]
                             if "p_nom_opt" in gens.columns
                             else gens["p_nom"])
                eps = 1e-6
                dual_tol = 1.0  # €/MWh — LP duals are exact to numerical eps
                by_bus: dict[str, list[tuple[str, float, float]]] = {}
                for g in subsidised:
                    if g not in p_gens.columns:
                        continue
                    bus = str(gens.at[g, "bus"])
                    cost = float(gens.at[g, "curtailment_cost"])
                    real_mc = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
                    by_bus.setdefault(bus, []).append((g, cost, real_mc))
                if by_bus:
                    prices = prices.copy()
                    for bus, members in by_bus.items():
                        if bus not in prices.columns:
                            continue
                        for i in range(len(p_gens.index)):
                            t = p_gens.index[i]
                            raw_dual = float(prices.at[t, bus])
                            # Walk subsidised renewables at this bus. Adjust
                            # only if one is dispatching (pv > 0) AND the
                            # observed LP dual matches its effective LP MC
                            # within tolerance — the unambiguous diagnostic
                            # that THIS renewable is setting the dual via
                            # the subsidy term. The renewable can be either
                            # mid-range OR at its ceiling; the dual-match
                            # check captures both LP-degenerate situations.
                            for g, cost, real_mc in members:
                                pv = float(p_gens.at[t, g])
                                if pv <= eps:
                                    continue
                                effective_lp_mc = real_mc - cost
                                # Two diagnostics trigger the adjustment:
                                #  (a) dual exactly at the subsidised LP MC
                                #      → renewable IS the marginal unit.
                                #  (b) dual BELOW the subsidised LP MC AND
                                #      the renewable is dispatching at its
                                #      ceiling (p == p_max_pu × p_nom_opt).
                                #      Here PyPSA stacks the upper-bound
                                #      shadow on top of the subsidy, dragging
                                #      the dual further negative. Without
                                #      this branch, hours where Solar is
                                #      saturated (the common case at noon)
                                #      keep an artificially negative dual
                                #      that flows through to storage's
                                #      "charge_cost" as a phantom subsidy.
                                if abs(raw_dual - effective_lp_mc) <= dual_tol:
                                    prices.at[t, bus] = real_mc
                                    break
                                if raw_dual < effective_lp_mc - dual_tol:
                                    # Ceiling check: only adjust when this
                                    # renewable is actually at its upper
                                    # bound (within numerical tolerance).
                                    try:
                                        pmp = float(p_max_pu_full.at[t, g])
                                        nom = float(p_nom_opt.get(g, 0.0))
                                        ceiling = pmp * nom
                                    except Exception:
                                        ceiling = None
                                    if ceiling is not None and ceiling > eps and abs(pv - ceiling) <= 1e-3 * max(ceiling, 1.0):
                                        prices.at[t, bus] = real_mc
                                        break
                    prices = prices.fillna(0.0)
    except Exception:
        pass  # defensive — keep raw LP duals if adjustment fails

    # ── Generator block ──────────────────────────────────────────────────
    gen_rows: list[dict] = []
    try:
        gens_p = _result_df(n, "generators_t", "p", "lopf")
    except Exception:
        gens_p = None
    if gens_p is not None and not gens_p.empty and not n.generators.empty:
        gens_df = n.generators
        # Use post-solve p_nom_opt when available (capacity expansion);
        # else fall back to the input p_nom.
        if "p_nom_opt" in gens_df.columns:
            p_nom = gens_df["p_nom_opt"].fillna(gens_df.get("p_nom", 0.0))
        else:
            p_nom = gens_df["p_nom"]
        mc_static = gens_df["marginal_cost"].fillna(0.0) if "marginal_cost" in gens_df.columns else _pd.Series(0.0, index=gens_df.index)
        fom_static = gens_df["fom_cost"].fillna(0.0) if "fom_cost" in gens_df.columns else _pd.Series(0.0, index=gens_df.index)
        # PyPSA also allows a time-varying marginal_cost — capture it when present.
        try:
            mc_t_df = n.get_switchable_as_dense("Generator", "marginal_cost")
        except Exception:
            mc_t_df = None

        for g in gens_p.columns:
            if g not in gens_df.index:
                continue
            bus = str(gens_df.at[g, "bus"])
            if bus not in prices.columns:
                continue
            try:
                p_series = gens_p[g].fillna(0.0)
            except Exception:
                continue
            price_series = prices[bus].reindex(p_series.index).fillna(0.0)
            if mc_t_df is not None and g in mc_t_df.columns:
                mc_series = mc_t_df[g].reindex(p_series.index).fillna(float(mc_static.get(g, 0.0)))
            else:
                mc_series = _pd.Series(float(mc_static.get(g, 0.0)), index=p_series.index)

            # revenue = Σ p × price × weight (€)
            revenue_total, revenue_per_p = _accumulate_per_period(p_series * price_series, w_vals)
            # vom = Σ p × marginal_cost × weight (€). For generators p is
            # non-negative, but use |p| anyway so a backed-out p_min_pu<0
            # asset (e.g. a Link-like generator) doesn't book negative VOM.
            vom_series = p_series.abs() * mc_series
            vom_total, vom_per_p = _accumulate_per_period(vom_series, w_vals)
            # ENERGY (LCOE denominator) on the generators basis; revenue/VOM above on objective.
            energy_total, energy_per_p = _accumulate_per_period(p_series, w_vals_energy)

            # Fixed cost: capital_cost (annualised) × p_nom_opt, scaled to
            # horizon by total_years_factor so the LCOE denominator (horizon-
            # summed energy via per-period weights × ipw.years) and the
            # numerator are on the same time basis. Without this, multi-period
            # generators show LCOE = (annual_capex + horizon_vom) / horizon_energy,
            # under-reporting CAPEX by `n_periods` (same bug as the storage
            # block fixed above).
            try:
                cc_eff = float(asset_costs.get("generators", {}).get(g, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            p_nom_g = float(p_nom.get(g, 0.0) or 0.0)
            fixed_cost_annual = cc_eff * p_nom_g
            fixed_cost = fixed_cost_annual * total_years_factor
            fom_per_mw = float(fom_static.get(g, 0.0) or 0.0)
            fom_cost = fom_per_mw * p_nom_g

            # LCOE — divide total horizon-scaled cost by total energy
            # dispatched. When energy is ~0 (e.g. a built-but-curtailed
            # renewable) the ratio is undefined; report None.
            denom = energy_total
            if denom > 1e-6:
                lcoe = (fixed_cost + vom_total) / denom
                avg_price = revenue_total / denom
            else:
                lcoe = None
                avg_price = None

            # Capacity factor: energy / (8760 × p_nom_opt × Σ years). Useful
            # for thermal vs renewable comparisons. Skip if p_nom_opt = 0.
            if p_nom_g > 1e-6:
                # Hours on the same (generators) basis as energy_total so the
                # capacity factor = energy / (p_nom × represented_hours) is consistent.
                total_hours_modelled = float(w_vals_energy.sum())
                cap_factor = energy_total / (p_nom_g * total_hours_modelled) if total_hours_modelled > 0 else None
            else:
                cap_factor = None

            # Per-period roll-up. Each period gets its own LCOE / avg-price.
            by_period_rows: list[dict] = []
            if is_multi and energy_per_p:
                # Per-period fixed cost is fixed_cost × years[p] / Σ years —
                # i.e. distribute the annualised CAPEX across periods in
                # proportion to their years weight. This matches what PyPSA's
                # statistics does: cost_per_period = capital_cost × p_nom × years.
                total_years = sum(period_years_lookup.values()) or 1.0
                for p_key in sorted(set(list(energy_per_p.keys()) + list(revenue_per_p.keys()))):
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    vom_p = vom_per_p.get(p_key, 0.0)
                    rev_p = revenue_per_p.get(p_key, 0.0)
                    e_p = energy_per_p.get(p_key, 0.0)
                    lcoe_p = ((fixed_p + vom_p) / e_p) if e_p > 1e-6 else None
                    avg_price_p = (rev_p / e_p) if e_p > 1e-6 else None
                    net_p = rev_p - fixed_p - vom_p
                    by_period_rows.append({
                        "period": p_key,
                        "energy_mwh": _safe_finite(e_p),
                        "revenue_eur": _safe_finite(rev_p),
                        "fixed_cost_eur": _safe_finite(fixed_p),
                        "fom_cost_eur": _safe_finite(fom_p),
                        "vom_cost_eur": _safe_finite(vom_p),
                        "net_profit_eur": _safe_finite(net_p),
                        "lcoe_eur_per_mwh": _safe_finite(lcoe_p) if lcoe_p is not None else None,
                        "avg_price_eur_per_mwh": _safe_finite(avg_price_p) if avg_price_p is not None else None,
                    })

            gen_rows.append({
                "name": str(g),
                "bus": bus,
                "carrier": str(gens_df.at[g, "carrier"]) if "carrier" in gens_df.columns else "",
                "p_nom_opt_mw": _safe_finite(p_nom_g),
                "energy_mwh": _safe_finite(energy_total),
                "capacity_factor": _safe_finite(cap_factor) if cap_factor is not None else None,
                "revenue_eur": _safe_finite(revenue_total),
                "vom_cost_eur": _safe_finite(vom_total),
                "fixed_cost_eur": _safe_finite(fixed_cost),
                "fom_cost_eur": _safe_finite(fom_cost),
                "net_profit_eur": _safe_finite(revenue_total - fixed_cost - vom_total),
                "lcoe_eur_per_mwh": _safe_finite(lcoe) if lcoe is not None else None,
                "avg_price_eur_per_mwh": _safe_finite(avg_price) if avg_price is not None else None,
                "by_period": by_period_rows,
            })

    # ── StorageUnit block (arbitrage) ────────────────────────────────────
    # Sign convention: positive p = discharge (acts like generation), negative
    # p = charge (acts like load). Revenue comes from discharging into the
    # market; cost comes from charging from it.
    su_rows: list[dict] = []
    try:
        su_p = _result_df(n, "storage_units_t", "p", "lopf")
    except Exception:
        su_p = None
    if su_p is not None and not su_p.empty and not n.storage_units.empty:
        su_df = n.storage_units
        if "p_nom_opt" in su_df.columns:
            p_nom_su = su_df["p_nom_opt"].fillna(su_df.get("p_nom", 0.0))
        else:
            p_nom_su = su_df["p_nom"]
        mc_static_su = su_df["marginal_cost"].fillna(0.0) if "marginal_cost" in su_df.columns else _pd.Series(0.0, index=su_df.index)
        fom_static_su = su_df["fom_cost"].fillna(0.0) if "fom_cost" in su_df.columns else _pd.Series(0.0, index=su_df.index)
        max_hours_su = su_df["max_hours"].fillna(0.0) if "max_hours" in su_df.columns else _pd.Series(0.0, index=su_df.index)

        for s in su_p.columns:
            if s not in su_df.index:
                continue
            bus = str(su_df.at[s, "bus"])
            if bus not in prices.columns:
                continue
            try:
                p_series = su_p[s].fillna(0.0)
            except Exception:
                continue
            price_series = prices[bus].reindex(p_series.index).fillna(0.0)

            discharge_series = p_series.clip(lower=0.0)
            charge_series = (-p_series).clip(lower=0.0)
            discharge_revenue_total, discharge_revenue_pp = _accumulate_per_period(
                discharge_series * price_series, w_vals,
            )
            charge_cost_total, charge_cost_pp = _accumulate_per_period(
                charge_series * price_series, w_vals,
            )
            discharge_mwh, discharge_mwh_pp = _accumulate_per_period(discharge_series, w_vals_energy)
            charge_mwh, charge_mwh_pp = _accumulate_per_period(charge_series, w_vals_energy)
            # PyPSA convention: marginal_cost applies to discharge dispatch.
            # Charge has no explicit cost in standard formulation. Keep VOM
            # restricted to discharge to match the LP objective contribution.
            vom_total_su, vom_pp_su = _accumulate_per_period(
                discharge_series * float(mc_static_su.get(s, 0.0)), w_vals,
            )

            try:
                cc_eff = float(asset_costs.get("storage_units", {}).get(s, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            p_nom_s = float(p_nom_su.get(s, 0.0) or 0.0)
            # `cc_eff` is annual annuitised €/MW/yr. To put it on the same
            # time basis as `vom_total_su` / `charge_cost_total` / `discharge_mwh`
            # (which are all horizon-summed via the per-period weights below),
            # multiply by `total_years_factor` = Σ ipw.years across periods.
            # On flat networks total_years_factor=1 and this is a no-op.
            # Documented bug: previous `fixed_cost = cc_eff × p_nom_s` mixed
            # annual capex with horizon opex → LCOS was understated by ~3×
            # for a 3-year multi-period horizon (Battery 1: reported 4 €/MWh,
            # true 16.4 €/MWh).
            fixed_cost_annual = cc_eff * p_nom_s
            fixed_cost = fixed_cost_annual * total_years_factor
            fom_per_mw = float(fom_static_su.get(s, 0.0) or 0.0)
            fom_cost = fom_per_mw * p_nom_s

            net_profit = discharge_revenue_total - charge_cost_total - vom_total_su - fixed_cost

            # LCOS — total cost to deliver one MWh of discharge energy.
            # Numerator includes the cost to charge (electricity bought
            # at market price), the variable cost of discharging, and
            # the horizon-scaled fixed cost. Denominator = discharge MWh
            # (what the storage actually delivered, horizon-summed).
            if discharge_mwh > 1e-6:
                lcos = (fixed_cost + vom_total_su + charge_cost_total) / discharge_mwh
            else:
                lcos = None
            # Spread = average discharge price − average charge price.
            avg_discharge_price = (discharge_revenue_total / discharge_mwh) if discharge_mwh > 1e-6 else None
            avg_charge_price = (charge_cost_total / charge_mwh) if charge_mwh > 1e-6 else None
            spread = None
            if avg_discharge_price is not None and avg_charge_price is not None:
                spread = avg_discharge_price - avg_charge_price
            # Round-trip efficiency for display — PyPSA stores eta_charge
            # and eta_dispatch separately.
            eta_c = float(su_df.at[s, "efficiency_store"]) if "efficiency_store" in su_df.columns else 1.0
            eta_d = float(su_df.at[s, "efficiency_dispatch"]) if "efficiency_dispatch" in su_df.columns else 1.0
            try:
                rte = eta_c * eta_d
            except Exception:
                rte = None

            by_period_rows: list[dict] = []
            if is_multi and discharge_mwh_pp:
                total_years = sum(period_years_lookup.values()) or 1.0
                periods_set = sorted(set(
                    list(discharge_mwh_pp.keys())
                    + list(charge_mwh_pp.keys())
                    + list(discharge_revenue_pp.keys())
                    + list(charge_cost_pp.keys())
                ))
                for p_key in periods_set:
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    dm = discharge_mwh_pp.get(p_key, 0.0)
                    cm = charge_mwh_pp.get(p_key, 0.0)
                    dr = discharge_revenue_pp.get(p_key, 0.0)
                    cc = charge_cost_pp.get(p_key, 0.0)
                    vp = vom_pp_su.get(p_key, 0.0)
                    np_period = dr - cc - vp - fixed_p
                    lcos_p = ((fixed_p + vp + cc) / dm) if dm > 1e-6 else None
                    spread_p = None
                    ap_d = (dr / dm) if dm > 1e-6 else None
                    ap_c = (cc / cm) if cm > 1e-6 else None
                    if ap_d is not None and ap_c is not None:
                        spread_p = ap_d - ap_c
                    by_period_rows.append({
                        "period": p_key,
                        "discharge_mwh": _safe_finite(dm),
                        "charge_mwh": _safe_finite(cm),
                        "discharge_revenue_eur": _safe_finite(dr),
                        "charge_cost_eur": _safe_finite(cc),
                        "fixed_cost_eur": _safe_finite(fixed_p),
                        "fom_cost_eur": _safe_finite(fom_p),
                        "vom_cost_eur": _safe_finite(vp),
                        "net_profit_eur": _safe_finite(np_period),
                        "lcos_eur_per_mwh": _safe_finite(lcos_p) if lcos_p is not None else None,
                        "spread_eur_per_mwh": _safe_finite(spread_p) if spread_p is not None else None,
                    })

            su_rows.append({
                "name": str(s),
                "bus": bus,
                "carrier": str(su_df.at[s, "carrier"]) if "carrier" in su_df.columns else "",
                "p_nom_opt_mw": _safe_finite(p_nom_s),
                "max_hours": _safe_finite(float(max_hours_su.get(s, 0.0) or 0.0)),
                "energy_capacity_mwh": _safe_finite(p_nom_s * float(max_hours_su.get(s, 0.0) or 0.0)),
                "round_trip_efficiency": _safe_finite(rte) if rte is not None else None,
                "discharge_mwh": _safe_finite(discharge_mwh),
                "charge_mwh": _safe_finite(charge_mwh),
                "discharge_revenue_eur": _safe_finite(discharge_revenue_total),
                "charge_cost_eur": _safe_finite(charge_cost_total),
                "vom_cost_eur": _safe_finite(vom_total_su),
                "fixed_cost_eur": _safe_finite(fixed_cost),
                "fom_cost_eur": _safe_finite(fom_cost),
                "net_profit_eur": _safe_finite(net_profit),
                "lcos_eur_per_mwh": _safe_finite(lcos) if lcos is not None else None,
                "spread_eur_per_mwh": _safe_finite(spread) if spread is not None else None,
                "avg_discharge_price_eur_per_mwh": _safe_finite(avg_discharge_price) if avg_discharge_price is not None else None,
                "avg_charge_price_eur_per_mwh": _safe_finite(avg_charge_price) if avg_charge_price is not None else None,
                "by_period": by_period_rows,
            })

    # ── Store block (energy-as-state arbitrage) ──────────────────────────
    # Same arbitrage logic as StorageUnit, but the capacity unit is MWh
    # (e_nom) instead of MW (p_nom). Hydrogen / heat storage commonly uses
    # this. Sign convention identical to StorageUnit.
    store_rows: list[dict] = []
    try:
        store_p = _result_df(n, "stores_t", "p", "lopf")
    except Exception:
        store_p = None
    if store_p is not None and not store_p.empty and not n.stores.empty:
        stores_df = n.stores
        if "e_nom_opt" in stores_df.columns:
            e_nom = stores_df["e_nom_opt"].fillna(stores_df.get("e_nom", 0.0))
        else:
            e_nom = stores_df["e_nom"]
        mc_static_st = stores_df["marginal_cost"].fillna(0.0) if "marginal_cost" in stores_df.columns else _pd.Series(0.0, index=stores_df.index)
        fom_static_st = stores_df["fom_cost"].fillna(0.0) if "fom_cost" in stores_df.columns else _pd.Series(0.0, index=stores_df.index)

        for s in store_p.columns:
            if s not in stores_df.index:
                continue
            bus = str(stores_df.at[s, "bus"])
            if bus not in prices.columns:
                continue
            try:
                p_series = store_p[s].fillna(0.0)
            except Exception:
                continue
            price_series = prices[bus].reindex(p_series.index).fillna(0.0)

            discharge_series = p_series.clip(lower=0.0)
            charge_series = (-p_series).clip(lower=0.0)
            discharge_revenue_total, discharge_revenue_pp = _accumulate_per_period(
                discharge_series * price_series, w_vals,
            )
            charge_cost_total, charge_cost_pp = _accumulate_per_period(
                charge_series * price_series, w_vals,
            )
            discharge_mwh, discharge_mwh_pp = _accumulate_per_period(discharge_series, w_vals_energy)
            charge_mwh, charge_mwh_pp = _accumulate_per_period(charge_series, w_vals_energy)
            vom_total_st, vom_pp_st = _accumulate_per_period(
                discharge_series * float(mc_static_st.get(s, 0.0)), w_vals,
            )

            try:
                cc_eff = float(asset_costs.get("stores", {}).get(s, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            e_nom_s = float(e_nom.get(s, 0.0) or 0.0)
            # Horizon-scale annual capex (see storage_units block above for
            # the same fix). cc_eff is €/MWh/yr × e_nom_opt gives annual €;
            # multiplying by total_years_factor matches the horizon-summed
            # opex / discharge / charge_cost magnitudes used below.
            fixed_cost_annual = cc_eff * e_nom_s
            fixed_cost = fixed_cost_annual * total_years_factor
            fom_per_unit = float(fom_static_st.get(s, 0.0) or 0.0)
            fom_cost = fom_per_unit * e_nom_s

            net_profit = discharge_revenue_total - charge_cost_total - vom_total_st - fixed_cost
            if discharge_mwh > 1e-6:
                lcos = (fixed_cost + vom_total_st + charge_cost_total) / discharge_mwh
            else:
                lcos = None
            avg_discharge_price = (discharge_revenue_total / discharge_mwh) if discharge_mwh > 1e-6 else None
            avg_charge_price = (charge_cost_total / charge_mwh) if charge_mwh > 1e-6 else None
            spread = None
            if avg_discharge_price is not None and avg_charge_price is not None:
                spread = avg_discharge_price - avg_charge_price

            by_period_rows: list[dict] = []
            if is_multi and discharge_mwh_pp:
                total_years = sum(period_years_lookup.values()) or 1.0
                periods_set = sorted(set(
                    list(discharge_mwh_pp.keys())
                    + list(charge_mwh_pp.keys())
                    + list(discharge_revenue_pp.keys())
                    + list(charge_cost_pp.keys())
                ))
                for p_key in periods_set:
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    dm = discharge_mwh_pp.get(p_key, 0.0)
                    cm = charge_mwh_pp.get(p_key, 0.0)
                    dr = discharge_revenue_pp.get(p_key, 0.0)
                    cc = charge_cost_pp.get(p_key, 0.0)
                    vp = vom_pp_st.get(p_key, 0.0)
                    np_period = dr - cc - vp - fixed_p
                    lcos_p = ((fixed_p + vp + cc) / dm) if dm > 1e-6 else None
                    spread_p = None
                    ap_d = (dr / dm) if dm > 1e-6 else None
                    ap_c = (cc / cm) if cm > 1e-6 else None
                    if ap_d is not None and ap_c is not None:
                        spread_p = ap_d - ap_c
                    by_period_rows.append({
                        "period": p_key,
                        "discharge_mwh": _safe_finite(dm),
                        "charge_mwh": _safe_finite(cm),
                        "discharge_revenue_eur": _safe_finite(dr),
                        "charge_cost_eur": _safe_finite(cc),
                        "fixed_cost_eur": _safe_finite(fixed_p),
                        "fom_cost_eur": _safe_finite(fom_p),
                        "vom_cost_eur": _safe_finite(vp),
                        "net_profit_eur": _safe_finite(np_period),
                        "lcos_eur_per_mwh": _safe_finite(lcos_p) if lcos_p is not None else None,
                        "spread_eur_per_mwh": _safe_finite(spread_p) if spread_p is not None else None,
                    })

            store_rows.append({
                "name": str(s),
                "bus": bus,
                "carrier": str(stores_df.at[s, "carrier"]) if "carrier" in stores_df.columns else "",
                "e_nom_opt_mwh": _safe_finite(e_nom_s),
                "discharge_mwh": _safe_finite(discharge_mwh),
                "charge_mwh": _safe_finite(charge_mwh),
                "discharge_revenue_eur": _safe_finite(discharge_revenue_total),
                "charge_cost_eur": _safe_finite(charge_cost_total),
                "vom_cost_eur": _safe_finite(vom_total_st),
                "fixed_cost_eur": _safe_finite(fixed_cost),
                "fom_cost_eur": _safe_finite(fom_cost),
                "net_profit_eur": _safe_finite(net_profit),
                "lcos_eur_per_mwh": _safe_finite(lcos) if lcos is not None else None,
                "spread_eur_per_mwh": _safe_finite(spread) if spread is not None else None,
                "avg_discharge_price_eur_per_mwh": _safe_finite(avg_discharge_price) if avg_discharge_price is not None else None,
                "avg_charge_price_eur_per_mwh": _safe_finite(avg_charge_price) if avg_charge_price is not None else None,
                "by_period": by_period_rows,
            })

    # ── Link block (converters: electrolysers, heat pumps, P2X) ──────────
    # Missing entirely until 2026-07-31. The user asked why their electrolyser
    # showed no economics; the endpoint returned generators / storage_units /
    # stores and no `links` key at all, so a Link could not appear in the
    # Economics table however its costs were configured.
    #
    # A Link is two-sided in a way the other three are not: it BUYS at bus0 and
    # SELLS at bus1. `revenue_eur` is therefore the NET of the two — value
    # delivered at bus1 minus energy bought at bus0 — so `net_profit_eur`
    # (revenue − fixed − vom) means the same thing it does for a generator and
    # the columns stay comparable down the table. The gross halves ride along
    # as their own fields so the netting is auditable rather than implied, and
    # so the Compare view can reconstruct either convention.
    link_rows: list[dict] = []
    try:
        links_p0 = _result_df(n, "links_t", "p0", "lopf")
        links_p1 = _result_df(n, "links_t", "p1", "lopf")
    except Exception:
        links_p0 = links_p1 = None
    if links_p0 is not None and not links_p0.empty and not n.links.empty:
        links_df = n.links
        if "p_nom_opt" in links_df.columns:
            l_p_nom = links_df["p_nom_opt"].fillna(links_df.get("p_nom", 0.0))
        else:
            l_p_nom = links_df["p_nom"]
        l_mc_static = (
            links_df["marginal_cost"].fillna(0.0) if "marginal_cost" in links_df.columns
            else _pd.Series(0.0, index=links_df.index)
        )
        l_fom_static = (
            links_df["fom_cost"].fillna(0.0) if "fom_cost" in links_df.columns
            else _pd.Series(0.0, index=links_df.index)
        )
        try:
            l_mc_t_df = n.get_switchable_as_dense("Link", "marginal_cost")
        except Exception:
            l_mc_t_df = None

        for ln in links_p0.columns:
            if ln not in links_df.index:
                continue
            bus0 = str(links_df.at[ln, "bus0"]) if "bus0" in links_df.columns else ""
            bus1 = str(links_df.at[ln, "bus1"]) if "bus1" in links_df.columns else ""
            try:
                p0_series = links_p0[ln].fillna(0.0)
            except Exception:
                continue

            # `p1` is NEGATIVE when the Link delivers into bus1, so flip it to
            # get a positive quantity of energy sold. Fall back to
            # p0 × efficiency only when the dispatch table lacks p1.
            if links_p1 is not None and ln in links_p1.columns:
                out_series = -links_p1[ln].reindex(p0_series.index).fillna(0.0)
            else:
                try:
                    eff = float(links_df.at[ln, "efficiency"])
                except (KeyError, TypeError, ValueError):
                    eff = 1.0
                out_series = p0_series * eff
            gross_revenue_series = out_series * (
                prices[bus1].reindex(p0_series.index).fillna(0.0)
                if bus1 in prices.columns else 0.0
            )

            # Multi-output Links (CHP: bus0 gas → bus1 electricity + bus2 heat;
            # heat pumps with a second sink) deliver at bus2/bus3/bus4 as well.
            # Counting only bus1 would silently drop half a CHP's product and
            # inflate its unit cost accordingly. Each extra port is valued at
            # ITS OWN bus price, which is unambiguous; the energy total is the
            # combined output across ports, so for a multi-output Link the
            # unit cost is per MWh of everything it delivers.
            for port in ("2", "3", "4"):
                bus_col = f"bus{port}"
                if bus_col not in links_df.columns:
                    continue
                bus_n = str(links_df.at[ln, bus_col] or "").strip()
                if not bus_n:
                    continue
                try:
                    p_n_df = _result_df(n, "links_t", f"p{port}", "lopf")
                except Exception:
                    p_n_df = None
                if p_n_df is None or ln not in getattr(p_n_df, "columns", []):
                    continue
                out_n = -p_n_df[ln].reindex(p0_series.index).fillna(0.0)
                out_series = out_series + out_n
                if bus_n in prices.columns:
                    gross_revenue_series = gross_revenue_series + (
                        out_n * prices[bus_n].reindex(p0_series.index).fillna(0.0)
                    )

            # Unlike the generator block, a missing bus price is NOT a reason
            # to drop the row. An H₂ or heat bus often carries no meaningful
            # dual, and skipping would reproduce the very bug this block
            # fixes — the asset silently vanishing from the table. Treat an
            # absent price as zero and still report capacity, energy and cost.
            if bus0 in prices.columns:
                price0 = prices[bus0].reindex(p0_series.index).fillna(0.0)
            else:
                price0 = _pd.Series(0.0, index=p0_series.index)
            if l_mc_t_df is not None and ln in l_mc_t_df.columns:
                l_mc_series = l_mc_t_df[ln].reindex(p0_series.index).fillna(float(l_mc_static.get(ln, 0.0)))
            else:
                l_mc_series = _pd.Series(float(l_mc_static.get(ln, 0.0)), index=p0_series.index)

            gross_revenue_total, gross_rev_per_p = _accumulate_per_period(gross_revenue_series, w_vals)
            input_cost_total, input_cost_per_p = _accumulate_per_period(p0_series * price0, w_vals)
            # PyPSA charges a Link's marginal_cost against p0 (the input), not
            # the output — matching how the LP builds the objective.
            vom_total, vom_per_p = _accumulate_per_period(p0_series.abs() * l_mc_series, w_vals)
            # ENERGY = what leaves bus1. Using p0 here would overstate a
            # 70%-efficient electrolyser's product by 1/0.7 and understate its
            # unit cost by the same factor.
            energy_total, energy_per_p = _accumulate_per_period(out_series, w_vals_energy)
            input_energy_total, _ = _accumulate_per_period(p0_series, w_vals_energy)

            try:
                cc_eff = float(asset_costs.get("links", {}).get(ln, {}).get("capital_cost", 0.0))
            except (TypeError, ValueError):
                cc_eff = 0.0
            p_nom_l = float(l_p_nom.get(ln, 0.0) or 0.0)
            fixed_cost = cc_eff * p_nom_l * total_years_factor
            fom_cost = float(l_fom_static.get(ln, 0.0) or 0.0) * p_nom_l

            revenue_total = gross_revenue_total - input_cost_total

            # All-in levelised cost of the Link's OUTPUT: capital + VOM + the
            # energy it had to buy. The bought energy belongs in the numerator
            # — for a 70%-efficient electrolyser it is the dominant term, and
            # omitting it produced €43.74/MWh against the LCOH panel's €246.02
            # for the identical asset. Two views of one converter disagreeing
            # by 5.6x is worse than either number alone, so this matches
            # `/results/lcoh` exactly, term for term.
            denom = energy_total
            if denom > 1e-6:
                lcoe = (fixed_cost + vom_total + input_cost_total) / denom
                avg_price = gross_revenue_total / denom
            else:
                lcoe = None
                avg_price = None

            # p_nom bounds the INPUT (p0), so utilisation is measured there.
            if p_nom_l > 1e-6:
                total_hours_modelled = float(w_vals_energy.sum())
                cap_factor = (
                    input_energy_total / (p_nom_l * total_hours_modelled)
                    if total_hours_modelled > 0 else None
                )
            else:
                cap_factor = None

            by_period_rows = []
            if is_multi and energy_per_p:
                total_years = sum(period_years_lookup.values()) or 1.0
                keys = set(energy_per_p) | set(gross_rev_per_p) | set(input_cost_per_p)
                for p_key in sorted(keys):
                    y = _years_for_period(p_key)
                    fixed_p = fixed_cost * (y / total_years) if total_years > 0 else 0.0
                    fom_p = fom_cost * (y / total_years) if total_years > 0 else 0.0
                    vom_p = vom_per_p.get(p_key, 0.0)
                    gross_p = gross_rev_per_p.get(p_key, 0.0)
                    in_p = input_cost_per_p.get(p_key, 0.0)
                    rev_p = gross_p - in_p
                    e_p = energy_per_p.get(p_key, 0.0)
                    # Same all-in basis as the horizon figure above.
                    lcoe_p = ((fixed_p + vom_p + in_p) / e_p) if e_p > 1e-6 else None
                    by_period_rows.append({
                        "period": p_key,
                        "energy_mwh": _safe_finite(e_p),
                        "revenue_eur": _safe_finite(rev_p),
                        "gross_revenue_eur": _safe_finite(gross_p),
                        "input_cost_eur": _safe_finite(in_p),
                        "fixed_cost_eur": _safe_finite(fixed_p),
                        "fom_cost_eur": _safe_finite(fom_p),
                        "vom_cost_eur": _safe_finite(vom_p),
                        "net_profit_eur": _safe_finite(rev_p - fixed_p - vom_p),
                        "lcoe_eur_per_mwh": _safe_finite(lcoe_p) if lcoe_p is not None else None,
                        "avg_price_eur_per_mwh": _safe_finite((gross_p / e_p) if e_p > 1e-6 else 0.0) if e_p > 1e-6 else None,
                    })

            link_rows.append({
                "name": str(ln),
                "bus": bus0,
                "bus1": bus1,
                "carrier": str(links_df.at[ln, "carrier"]) if "carrier" in links_df.columns else "",
                "efficiency": _safe_finite(float(links_df.at[ln, "efficiency"])) if "efficiency" in links_df.columns else None,
                "p_nom_opt_mw": _safe_finite(p_nom_l),
                "energy_mwh": _safe_finite(energy_total),
                "input_energy_mwh": _safe_finite(input_energy_total),
                "capacity_factor": _safe_finite(cap_factor) if cap_factor is not None else None,
                "revenue_eur": _safe_finite(revenue_total),
                "gross_revenue_eur": _safe_finite(gross_revenue_total),
                "input_cost_eur": _safe_finite(input_cost_total),
                "vom_cost_eur": _safe_finite(vom_total),
                "fixed_cost_eur": _safe_finite(fixed_cost),
                "fom_cost_eur": _safe_finite(fom_cost),
                "net_profit_eur": _safe_finite(revenue_total - fixed_cost - vom_total),
                "lcoe_eur_per_mwh": _safe_finite(lcoe) if lcoe is not None else None,
                "avg_price_eur_per_mwh": _safe_finite(avg_price) if avg_price is not None else None,
                "by_period": by_period_rows,
            })

    # Periods list (sorted) for the frontend's period selector.
    periods_list: list = []
    if is_multi:
        seen = set()
        for p_key in period_keys:
            if p_key is not None and p_key not in seen:
                periods_list.append(p_key)
                seen.add(p_key)
        try:
            periods_list = sorted(periods_list, key=lambda x: (0, int(x)) if hasattr(x, "__int__") else (1, str(x)))
        except Exception:
            pass

    return {
        "currency": "EUR",
        "is_multi_period": is_multi,
        "periods": periods_list,
        "generators": gen_rows,
        "storage_units": su_rows,
        "stores": store_rows,
        "links": link_rows,
    }
