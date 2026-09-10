"""
Lifted from `routers.results` (get_carrier_kpis).

The handler keeps the network lookup, the `_dispatch_ready` gate and every
`_state` read; this module gets the arithmetic and returns the payload, or
`None` where the endpoint answers 204. Result frames arrive through the
injected `result_df` callable where one is needed, so this runs on any
network with no router state — see `tests/test_results_seam.py`.

pandas / numpy / math are imported locally inside each function, the pattern
the router already used, so they are intentionally absent from this header.
"""
from __future__ import annotations

from services.period_utils import (
    period_years_map,
    years_for_period,
)
from services.results.load_frames import corrected_marginal_prices



def compute_carrier_kpis(n, *, result_df):
    """
    Per-carrier KPI rows: energy, capacity factor, market value, revenue.

    Lifted from `routers.results.get_carrier_kpis`, which keeps the network
    lookup, the `_dispatch_ready` gate and the `_state` reads. Returns the
    payload dict, or `None` where the handler returns 204.
    """
    import math as _math

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
        bus_prices = corrected_marginal_prices(n, from_state=True, result_df=result_df)
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
