"""
A generator's fuel curve (homer-parity 02a, HOMER-GAPS-1).

HOMER models a genset's fuel use as a no-load intercept plus a slope:

    fuel [MWh fuel/h] = no_load × p_nom × on + slope × p

``fuel_curve_no_load`` is MWh of fuel per hour per MW of rated capacity while the unit is on (HOMER's intercept
coefficient), ``fuel_curve_slope`` is MWh of fuel per MWh generated (the unit of the DC backup heat rate,
``models/flex_archetypes.py``), ``fuel_price`` is EUR per MWh of fuel. The curve reaches the solve through PyPSA's
own columns, so the objective and ``n.statistics`` opex carry it with no extra term:

* ``marginal_cost = slope × fuel_price`` (EUR/MWh);
* ``stand_by_cost = no_load × p_nom × fuel_price`` (EUR/h while committed). PyPSA weights it by the snapshot
  weighting in both the objective and the statistics; it needs ``committable`` and a fixed ``p_nom`` (a cost per
  hour cannot follow ``p_nom_opt``).

A generator with none of the three set keeps its own costs: no curve, no change.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

SLOPE = "fuel_curve_slope"
NO_LOAD = "fuel_curve_no_load"
PRICE = "fuel_price"
COLUMNS = (SLOPE, NO_LOAD, PRICE)


def _num(v: Any) -> float | None:
    """A finite float, or None for missing, blank, NaN or non-numeric."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def curve_of(row: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """``(slope, no_load, price)`` with unset terms as 0, or None when the row sets no curve term."""
    vals = [_num(row.get(k)) for k in COLUMNS]
    if all(v is None for v in vals):
        return None
    slope, no_load, price = (v or 0.0 for v in vals)
    return slope, no_load, price


def check(row: Mapping[str, Any]) -> str | None:
    """Why the row's curve cannot be priced, or None when it can (or sets none)."""
    for k in COLUMNS:
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)) and _num(v) is None:
            return f"{k} must be a finite number (got {v!r})"
    curve = curve_of(row)
    if curve is None:
        return None
    slope, no_load, price = curve
    for k, v in zip(COLUMNS, curve):
        if v < 0:
            return f"{k} must be zero or more (got {v:g})"
    if (slope > 0 or no_load > 0) and _num(row.get(PRICE)) is None:
        return "a fuel curve needs a fuel price (EUR per MWh of fuel)"
    if no_load > 0:
        if not bool(row.get("committable")):
            return ("no-load fuel is burnt only while the unit is on: turn on Unit commitment "
                    "(committable) to use it")
        if bool(row.get("p_nom_extendable")):
            return ("no-load fuel is a cost per hour on the rated capacity, which cannot follow an "
                    "extendable p_nom: fix the capacity or set no-load fuel to 0")
    return None


def derived_costs(row: Mapping[str, Any]) -> dict[str, float]:
    """The PyPSA cost columns the curve sets, or {} when the row sets no curve."""
    curve = curve_of(row)
    if curve is None:
        return {}
    slope, no_load, price = curve
    p_nom = _num(row.get("p_nom")) or 0.0
    return {"marginal_cost": slope * price, "stand_by_cost": no_load * p_nom * price}


def apply(n, names=None) -> list[str]:
    """Write ``derived_costs`` onto ``n.generators`` for each curve generator in ``names`` (all when None).

    Idempotent: the derived columns follow the inputs and ``p_nom`` only. Returns the generators written.
    """
    gens = n.generators
    if gens.empty or not any(c in gens.columns for c in COLUMNS):
        return []
    idx = gens.index if names is None else [g for g in names if g in gens.index]
    written = []
    for name in idx:
        costs = derived_costs(gens.loc[name])
        if not costs:
            continue
        if "stand_by_cost" not in gens.columns:
            gens["stand_by_cost"] = 0.0
        for col, val in costs.items():
            gens.at[name, col] = val
        written.append(name)
    return written


def fuel_use(n, period_years: Mapping[Any, float] | None = None) -> list[dict[str, Any]]:
    """Each curve generator's fuel burn and cost over the solved horizon.

    Weighted like the LP objective: ``snapshot_weightings.objective`` × the period's ``years`` (``period_years``,
    from ``services.period_utils.period_years_map``; empty or None for a single period).
    """
    gens = n.generators
    p = getattr(n.generators_t, "p", None)
    if gens.empty or p is None or p.empty or not any(c in gens.columns for c in COLUMNS):
        return []
    w = n.snapshot_weightings.objective.reindex(p.index).fillna(0.0)
    if isinstance(p.index, pd.MultiIndex) and period_years:
        from services.period_utils import years_for_period

        w = w * pd.Series([years_for_period(period_years, k[0]) for k in p.index], index=p.index)
    status = getattr(n.generators_t, "status", None)
    rows = []
    for name in gens.index:
        curve = curve_of(gens.loc[name])
        if curve is None or name not in p.columns:
            continue
        slope, no_load, price = curve
        on = (status[name] if status is not None and name in status.columns
              else pd.Series(1.0, index=p.index)).reindex(p.index).fillna(0.0)
        p_nom = _num(gens.at[name, "p_nom"]) or 0.0
        slope_mwh = float((slope * p[name] * w).sum())
        no_load_mwh = float((no_load * p_nom * on * w).sum()) if bool(gens.at[name, "committable"]) else 0.0
        fuel = slope_mwh + no_load_mwh
        rows.append({"generator": name, "carrier": str(gens.at[name, "carrier"]),
                     "fuel_mwh": fuel, "no_load_fuel_mwh": no_load_mwh,
                     "fuel_cost_eur": fuel * price, "no_load_cost_eur": no_load_mwh * price})
    return rows
