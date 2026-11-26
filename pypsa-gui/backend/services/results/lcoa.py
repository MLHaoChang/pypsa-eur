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

Unlike LCOH it never sums part of the numerator (GK B2 A4): a part it cannot
value (a needed bus price, a weighting, the solved capacity, a cost, the
output's unit) answers `not_established` naming that part, never a lower figure.
"""
from __future__ import annotations

from services.period_utils import is_multi_period, period_years_map, snapshot_weights, years_for_period
from services.results.load_frames import corrected_marginal_prices

#: PyPSA-Eur's ``MWh_NH3_per_tNH3`` (config.default.yaml), used for an MWh bus with no ``mwh_per_t``.
DEFAULT_MWH_PER_T = 5.166


class _NotValued(Exception):
    """A part of the cost or of the NH3 made that cannot be valued; its text names the part."""


def _num(value):
    """A finite float, else None."""
    import math

    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _carrier(frame, name) -> str:
    if "carrier" not in frame.columns or name not in frame.index:
        return ""
    return str(frame.at[name, "carrier"]).strip()


def _input_buses(links, name):
    """``(bus, -efficiency_k)`` for each further bus the Link draws from (efficiency_k < 0, k >= 2)."""
    out = []
    k = 2
    while f"bus{k}" in links.columns:
        bus = links.at[name, f"bus{k}"]
        eff = _num(links.at[name, f"efficiency{k}"]) if f"efficiency{k}" in links.columns else None
        if isinstance(bus, str) and bus and eff is not None and eff < 0:
            out.append((bus, -eff))
        k += 1
    return out


def ammonia_synthesis_links(n) -> list[str]:
    """Names of the ammonia-synthesis Links.

    The carrier names Haber(-Bosch) or ammonia synthesis; or the Link makes NH3
    (bus1 carries NH3) from an H2 input. An NH3 import or purchase Link, which
    draws no H2, is not synthesis (GK B2 A3).
    """
    links = getattr(n, "links", None)
    if links is None or links.empty:
        return []
    out = []
    for name in links.index:
        carrier = _carrier(links, name).lower()
        if "haber" in carrier or "ammonia synthesis" in carrier:
            out.append(name)
            continue
        bus1 = links.at[name, "bus1"] if "bus1" in links.columns else None
        if _carrier(n.buses, bus1).upper() != "NH3":
            continue
        if any(_carrier(n.buses, bus).upper() == "H2" for bus, _ in _input_buses(links, name)):
            out.append(name)
    return out


def _mwh_per_t(n, bus) -> tuple[float, str]:
    """The output bus's unit per t NH3, and how it was read (GK B2 A2: fail closed)."""
    unit = str(n.buses.at[bus, "unit"]).strip() if "unit" in n.buses.columns and bus in n.buses.index else ""
    if unit == "t":
        return 1.0, "NH3 bus in t"
    if unit == "MWh":
        if "mwh_per_t" in n.buses.columns:
            v = _num(n.buses.at[bus, "mwh_per_t"])
            if v is not None and v > 0:
                return v, f"{v:g} MWh/t from the NH3 bus"
        return DEFAULT_MWH_PER_T, f"{DEFAULT_MWH_PER_T:g} MWh/t, PyPSA-Eur's MWh_NH3_per_tNH3"
    raise _NotValued(f"the NH3 bus {bus!r} has unit {unit or 'none'!r}, neither t nor MWh")


def compute_lcoa(n, cfg, *, result_df, price_limit=...):
    """Fleet levelised cost of ammonia (EUR per t NH3) over the ammonia-synthesis Links.

    Returns ``{"rows", "total", "currency"}``; ``total`` is None when no Link
    made NH3. ``unavailable_reason`` is set for an integer (MIP) solve, and
    ``not_established`` (naming the part) when a part cannot be valued.
    ``price_limit`` is the caller's judgement of the solve that wrote the
    prices; without one, ``n`` under ``cfg``.
    """
    empty = {"rows": [], "total": None, "currency": "EUR"}
    names = ammonia_synthesis_links(n)
    if not names:
        return empty

    from harness.solve_validity import MIP_PRICES_TEXT, mip_price_limit
    if price_limit is ...:
        price_limit = mip_price_limit(n, cfg)
    if price_limit is not None:
        return {**empty, "unavailable_reason": price_limit, "message": MIP_PRICES_TEXT}
    try:
        return _lcoa(n, cfg, names, result_df)
    except _NotValued as exc:
        return {**empty, "not_established": str(exc)}


def _lcoa(n, cfg, names, result_df) -> dict:
    import numpy as _np
    import pandas as _pd
    from services.solver.periodized_costs import periodized_cost_view
    from services.solver_service import fom_per_horizon

    links = n.links
    p0 = getattr(n.links_t, "p0", None)
    if p0 is None or p0.empty:
        raise _NotValued("the solved dispatch is missing")

    sns = n.snapshots
    weights = snapshot_weights(n, "objective").reindex(sns)
    energy_weights = snapshot_weights(n, "generators").reindex(sns)
    if not (_np.isfinite(weights.to_numpy(float)).all() and _np.isfinite(energy_weights.to_numpy(float)).all()):
        raise _NotValued("a snapshot weighting is missing")
    # Horizon-total years for the annuitised fixed cost, as LCOH: the OPEX and
    # input sums already span every period's years through `weights`.
    years = 1.0
    if is_multi_period(n):
        try:
            ymap = period_years_map(n)
            years = float(sum(years_for_period(ymap, p) for p in sorted({int(p) for p in sns.get_level_values(0)})))
        except Exception as exc:
            raise _NotValued("the investment periods' years are missing") from exc

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

    # The solved duals as read, before the merit-order correction: the correction
    # answers zeros for a missing frame, so presence is judged on the raw read (A4).
    try:
        raw = result_df(n, "buses_t", "marginal_price", "lopf")
    except Exception:
        raw = getattr(n.buses_t, "marginal_price", None)
    try:
        prices = corrected_marginal_prices(n, result_df=result_df)
    except Exception:
        prices = raw

    def _price(bus, name):
        for frame in (raw, prices):
            if frame is None or bus not in getattr(frame, "columns", ()) \
                    or frame[bus].reindex(sns).astype(float).isna().any():
                raise _NotValued(f"the price of bus {bus!r} that {name!r} draws from is missing")
        return prices[bus].reindex(sns).astype(float)

    def _part(value, what, name):
        v = _num(value)
        if v is None:
            raise _NotValued(f"the {what} of {name!r} is missing")
        return v

    rows: list[dict] = []
    fleet_cost = 0.0
    fleet_t = 0.0
    for name in names:
        if name not in p0.columns:
            raise _NotValued(f"the dispatch of {name!r} is missing")
        disp = p0[name].reindex(sns).astype(float)
        if disp.isna().any():
            raise _NotValued(f"the dispatch of {name!r} is missing")
        if "p_nom_opt" not in links.columns:
            raise _NotValued(f"the solved capacity of {name!r} is missing")
        p_nom = _part(links.at[name, "p_nom_opt"], "solved capacity", name)
        eff = _part(links.at[name, "efficiency"], "efficiency", name)
        consume = disp.clip(lower=0)
        mwh_per_t, basis = _mwh_per_t(n, links.at[name, "bus1"])
        nh3_t = float((consume * energy_weights).sum()) * eff / mwh_per_t
        if nh3_t <= 0:
            rows.append({"name": name, "nh3_t": 0.0, "cost_eur": None, "lcoa_eur_per_t": None})
            continue
        # The typed costs as entered: the per-horizon helpers fill a gap with 0.
        _part(links.at[name, "capital_cost"], "capital cost", name)
        if "fom_cost" in links.columns:
            _part(links.at[name, "fom_cost"], "fixed O&M", name)
        cc = _part(cap_costs.get(name), "capital cost", name)
        fom = _part(fom_costs.get(name), "fixed O&M", name)
        mc = _part(links.at[name, "marginal_cost"], "marginal cost", name)
        fixed = (cc + fom) * p_nom * years
        vom = float((disp.abs() * mc * weights).sum())
        electricity = float((consume * _price(links.at[name, "bus0"], name) * weights).sum())
        # Further inputs (efficiency_k < 0): the Haber-Bosch loop's H2 draw, at its bus's price.
        inputs = sum(float((consume * share * _price(bus, name) * weights).sum())
                     for bus, share in _input_buses(links, name))
        cost = fixed + vom + electricity + inputs
        rows.append({"name": name, "nh3_t": nh3_t, "cost_eur": cost, "lcoa_eur_per_t": cost / nh3_t,
                     "fixed_eur": fixed, "vom_eur": vom, "electricity_eur": electricity,
                     "inputs_eur": inputs, "t_basis": basis})
        fleet_cost += cost
        fleet_t += nh3_t
    total = ({"lcoa_eur_per_t": fleet_cost / fleet_t, "nh3_t": fleet_t, "cost_eur": fleet_cost,
              "t_basis": sorted({r["t_basis"] for r in rows if r.get("t_basis")})}
             if fleet_t > 0 else None)
    return {"rows": rows, "total": total, "currency": "EUR"}
