"""
Levelised cost of ammonia: the fleet cost per tonne of NH3 the design's
ammonia-synthesis Links made (template-e2e issue 28a).

It mirrors `services.results.lcoh.compute_lcoh` term for term, on the same
helpers and weighting bases: the Link's fixed cost (annuitised investment +
FOM, per modelled horizon, times the horizon's years), its variable O&M, its
electricity at the corrected bus0 price, and every other input it draws (the
Haber-Bosch loop's H2 on bus2) at that bus's corrected price. Divided by the
NH3 it made, in tonnes. No by-product credit and no tank cost, as LCOH has
none. An integer (MIP) solve's prices are not prices, so it answers
`unavailable_reason` there, as LCOH does.
"""
from __future__ import annotations

from services.period_utils import is_multi_period, period_years_map, snapshot_weights, years_for_period
from services.results.load_frames import corrected_marginal_prices

#: PyPSA-Eur's ``MWh_NH3_per_tNH3`` (config.default.yaml), used when bus1 carries no ``mwh_per_t``.
DEFAULT_MWH_PER_T = 5.166


def is_ammonia_synthesis(links, name) -> bool:
    """A Haber-Bosch-like Link: its carrier names Haber(-Bosch), or its output bus carries NH3."""
    carrier = str(links.at[name, "carrier"] if "carrier" in links.columns else "").strip().lower()
    return "haber" in carrier or "ammonia synthesis" in carrier


def ammonia_synthesis_links(n) -> list[str]:
    """Names of the ammonia-synthesis Links: by carrier, or by bus1's ``NH3`` carrier."""
    links = getattr(n, "links", None)
    if links is None or links.empty:
        return []
    bus_carrier = n.buses["carrier"].astype(str).str.strip().str.upper() if "carrier" in n.buses.columns else None
    out = []
    for name in links.index:
        bus1 = links.at[name, "bus1"] if "bus1" in links.columns else None
        to_nh3 = bus_carrier is not None and bus1 in bus_carrier.index and bus_carrier.at[bus1] == "NH3"
        if is_ammonia_synthesis(links, name) or to_nh3:
            out.append(name)
    return out


def _num(value, default: float) -> float:
    import math

    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def compute_lcoa(n, cfg, *, result_df, price_limit=...):
    """Fleet levelised cost of ammonia (EUR per t NH3) over the ammonia-synthesis Links.

    Returns ``{"rows", "total", "currency"}``; ``total`` is None when no Link
    made NH3. ``unavailable_reason`` is set for an integer (MIP) solve.
    """
    import pandas as _pd
    from services.solver.periodized_costs import periodized_cost_view
    from services.solver_service import fom_per_horizon

    empty = {"rows": [], "total": None, "currency": "EUR"}
    names = ammonia_synthesis_links(n)
    if not names:
        return empty
    links = n.links

    from harness.solve_validity import MIP_PRICES_TEXT, mip_price_limit
    if price_limit is ...:
        price_limit = mip_price_limit(n, cfg)
    if price_limit is not None:
        return {**empty, "unavailable_reason": price_limit, "message": MIP_PRICES_TEXT}

    p0 = getattr(n.links_t, "p0", None)
    if p0 is None or p0.empty:
        return empty

    sns = n.snapshots
    weights = snapshot_weights(n, "objective")
    energy_weights = snapshot_weights(n, "generators")
    # Horizon-total years for the annuitised fixed cost, as LCOH: the OPEX and
    # input sums already span every period's years through `weights`.
    years = 1.0
    if is_multi_period(n):
        try:
            ymap = period_years_map(n)
            years = float(sum(years_for_period(ymap, int(p)) for p in sorted({int(p) for p in sns.get_level_values(0)})))
        except Exception:
            years = 1.0

    try:
        with periodized_cost_view(n, cfg) as view:
            cap_costs = view.c["Link"].capital_cost
            if not isinstance(cap_costs, _pd.Series):
                cap_costs = _pd.Series(cap_costs, index=links.index)
            cap_costs = cap_costs.copy()
    except Exception:
        cap_costs = fom_per_horizon(n, links.get("capital_cost", _pd.Series(0.0, index=links.index)))
    fom_costs = (fom_per_horizon(n, links["fom_cost"]) if "fom_cost" in links.columns
                 else _pd.Series(0.0, index=links.index))

    try:
        prices = corrected_marginal_prices(n, result_df=result_df)
    except Exception:
        prices = getattr(n.buses_t, "marginal_price", None)

    def _input_cost(bus, flow) -> float:
        if prices is None or not bus or bus not in prices.columns:
            return 0.0
        bp = prices[bus].reindex(sns).fillna(0.0).astype(float)
        return float((flow * bp * weights).sum())

    rows: list[dict] = []
    fleet_cost = 0.0
    fleet_t = 0.0
    for name in names:
        if name not in p0.columns:
            continue
        p_nom = _num(links.at[name, "p_nom_opt"] if "p_nom_opt" in links.columns else None,
                     _num(links.at[name, "p_nom"] if "p_nom" in links.columns else 0.0, 0.0))
        eff = _num(links.at[name, "efficiency"] if "efficiency" in links.columns else 1.0, 1.0)
        if p_nom <= 0 or eff <= 0:
            continue
        disp = p0[name].reindex(sns).fillna(0.0).astype(float)
        consume = disp.clip(lower=0)
        nh3_mwh = float((consume * energy_weights).sum()) * eff
        bus1 = links.at[name, "bus1"]
        mwh_per_t = DEFAULT_MWH_PER_T
        if "mwh_per_t" in n.buses.columns and bus1 in n.buses.index:
            mwh_per_t = _num(n.buses.at[bus1, "mwh_per_t"], DEFAULT_MWH_PER_T)
            if mwh_per_t <= 0:
                mwh_per_t = DEFAULT_MWH_PER_T
        nh3_t = nh3_mwh / mwh_per_t
        if nh3_t <= 0:
            rows.append({"name": name, "nh3_t": 0.0, "cost_eur": None, "lcoa_eur_per_t": None})
            continue
        cc = max(_num(cap_costs.get(name, 0.0), 0.0), 0.0)
        fom = max(_num(fom_costs.get(name, 0.0), 0.0), 0.0)
        fixed = (cc + fom) * p_nom * years
        mc = _num(links.at[name, "marginal_cost"] if "marginal_cost" in links.columns else 0.0, 0.0)
        vom = float((disp.abs() * mc * weights).sum()) if mc > 0 else 0.0
        inputs = _input_cost(links.at[name, "bus0"], consume)
        # Further inputs (efficiency_k < 0): the Haber-Bosch loop's H2 draw, at its bus's price.
        k = 2
        while f"bus{k}" in links.columns:
            bus_k = links.at[name, f"bus{k}"]
            eff_k = _num(links.at[name, f"efficiency{k}"] if f"efficiency{k}" in links.columns else 0.0, 0.0)
            if bus_k and eff_k < 0:
                inputs += _input_cost(bus_k, consume * -eff_k)
            k += 1
        cost = fixed + vom + inputs
        rows.append({"name": name, "nh3_t": nh3_t, "cost_eur": cost, "lcoa_eur_per_t": cost / nh3_t,
                     "mwh_per_t": mwh_per_t})
        fleet_cost += cost
        fleet_t += nh3_t
    total = ({"lcoa_eur_per_t": fleet_cost / fleet_t, "nh3_t": fleet_t, "cost_eur": fleet_cost}
             if fleet_t > 0 else None)
    return {"rows": rows, "total": total, "currency": "EUR"}
