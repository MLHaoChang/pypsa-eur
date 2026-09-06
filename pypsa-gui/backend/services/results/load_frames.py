"""
The two load-frame helpers shared by `/results/loads`, `/results/lcoh`,
`/results/carrier_kpis`, the Compare tab and Asset Detail.

Lifted verbatim from `routers/results.py`, with one change: `result_df` is a
keyword-only parameter instead of a module-level lookup into router state.
`routers.results.lp_scaled_load_frame` / `.corrected_marginal_prices` still
exist with their original signatures — they pass the router's `_result_df`
through — so `routers/compare.py` and `services/asset_results/compute.py`,
which import them by name, are untouched.
"""
from __future__ import annotations


def lp_scaled_load_frame(n, cfg=None, source: str = "lopf", from_state: bool = True, *, result_df=None):
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
            df = result_df(n, "loads_t", "p", source)
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


def corrected_marginal_prices(n, from_state: bool = True, *, result_df=None):
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
            prices = result_df(n, "buses_t", "marginal_price", "lopf")
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
