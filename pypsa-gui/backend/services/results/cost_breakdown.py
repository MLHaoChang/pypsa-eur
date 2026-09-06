"""
Lifted from `routers.results` (get_cost_breakdown).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

import logging

from services.period_utils import (
    is_period_only as _is_period_only,
    period_years_map,
    years_for_period,
)
from services.serialization import safe_float as _safe_float
from services.solver_service import (
    _pv_factor_series,
    _reference_build_year,
    with_periodized_cost_defaults,
)
from typing import Any

# The SAME logger the router uses, not a child of it: `logger.exception(...)`
# text inside the lifted bodies must produce byte-identical log records.
logger = logging.getLogger("pypsa_gui.results")



def compute_cost_breakdown(n, cfg):
    """
    CAPEX + OPEX broken out per component class, carrier and period.

    Lifted from `routers.results.get_cost_breakdown`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
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
        return None
    if stats is None or stats.empty:
        return None

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
            return None

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
