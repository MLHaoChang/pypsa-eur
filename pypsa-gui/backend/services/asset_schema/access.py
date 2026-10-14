"""
The upfront (overnight) cost of an asset, for every reader: reports, the capex budget, the finance engine.

`upfront_parts` returns, per asset:

- a composite asset: its parts, each per unit of the asset's sizing variable (an energy part is scaled by
  `max_hours`, so a battery's parts are both per MW of power);
- a single-part asset with a typed `overnight_cost`: that one part;
- an asset priced only through an annualised `capital_cost`: one part back-calculated as
  `capital_cost / annuity(rate, lifetime)` and flagged `derived_from_capital_cost` (ADR-0001: flagged, never
  passed off as typed);
- nothing priced: `None`.

`capital_cost` is read as an ANNUAL figure, the GUI's convention; call this outside a solve-time fill, or inside
one only where the horizon is a year.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from services.asset_schema.derive import has_parts, number
from services.asset_schema.schema import CLASS_ATTR, composite_parts
from services.solver.periodized_costs import _annuity


@dataclass(frozen=True)
class UpfrontPart:
    name: str
    upfront_per_unit: float      # EUR per unit of the asset's sizing variable (MW for a battery)
    lifetime: float | None
    fom_share: float | None
    derived_from_capital_cost: bool = False


def upfront_parts(n, component_class: str, name: str, *,
                  discount_rate: float | None = None) -> list[UpfrontPart] | None:
    df = getattr(n, CLASS_ATTR[component_class])
    row = df.loc[name]
    if has_parts(component_class, row):
        out = []
        for spec in composite_parts(component_class):
            overnight = number(row.get(spec.column("overnight")))
            if overnight is None:
                continue
            scale = float(row.get(spec.scale_by, 1.0)) if spec.scale_by else 1.0
            life = row.get(spec.column("lifetime")) if spec.own_lifetime else row.get("lifetime")
            out.append(UpfrontPart(spec.name, overnight * scale, number(life),
                                   number(row.get(spec.column("fom_share")))))
        return out
    lifetime = number(row.get("lifetime"))
    typed = number(row.get("overnight_cost"))
    if typed is not None and typed != 0:
        return [UpfrontPart("investment", typed, lifetime, None)]
    capital = number(row.get("capital_cost"))
    if capital is None or capital == 0:
        return None
    rate = number(row.get("discount_rate"))
    rate = rate if rate is not None else discount_rate
    if rate is None:
        return None
    finite_life = lifetime is not None and math.isfinite(lifetime)
    ann = _annuity(rate, lifetime) if finite_life else rate       # annuity(r, inf) = r
    if ann <= 0:
        return None
    return [UpfrontPart("investment", capital / ann, lifetime, None, derived_from_capital_cost=True)]


def upfront_per_unit(n, component_class: str, *, discount_rate: float | None = None) -> pd.Series:
    """Total upfront cost per unit of sizing variable, per asset; NaN where it is unknown."""
    df = getattr(n, CLASS_ATTR[component_class])
    values = {}
    for name in df.index:
        parts = upfront_parts(n, component_class, name, discount_rate=discount_rate)
        values[name] = float("nan") if parts is None else sum(p.upfront_per_unit for p in parts)
    return pd.Series(values, dtype=float)


#: What one unit of an asset's sizing variable is, per class: the basis of `upfront_per_unit`.
SIZING = {
    "Generator": ("p_nom", "MW"),
    "Link": ("p_nom", "MW"),
    "StorageUnit": ("p_nom", "MW"),
    "Store": ("e_nom", "MWh"),
    "Line": ("s_nom", "MVA"),
    "Transformer": ("s_nom", "MVA"),
}


def _finite(v: float | None) -> float | None:
    """JSON has no inf: an infinite value (a lifetime that never ends) is sent as None."""
    return v if v is not None and math.isfinite(v) else None


def asset_cost_rows(n, *, discount_rate: float | None = None, keep=None) -> list[dict]:
    """Every asset of the investable classes with its upfront parts (Economics in Expert, plan
    2026-10-14-economics-in-expert.md): one row per asset, `parts` empty when it is not priced. Money is in
    EUR per unit of the sizing variable (`basis`); `capital_cost` and `fom_cost` are the asset's annual columns
    as stored (derived where the asset is priced by parts). `keep(component_class, names)` filters the names
    (the routes drop solver scaffolding)."""
    out = []
    for cls, (size_col, basis) in SIZING.items():
        df = getattr(n, CLASS_ATTR[cls])
        names = list(df.index)
        if keep is not None:
            names = keep(cls, names)
        for name in names:
            row = df.loc[name]
            parts = upfront_parts(n, cls, name, discount_rate=discount_rate) or []
            per_unit = sum(p.upfront_per_unit for p in parts)
            size = number(row.get(size_col))
            out.append({
                "component": cls, "name": str(name), "carrier": str(row.get("carrier", "") or ""),
                "basis": f"EUR/{basis}", "size_column": size_col, "size": _finite(size),
                "extendable": bool(row.get(f"{size_col}_extendable", False)),
                "parts": [{"name": p.name, "upfront_per_unit": _finite(p.upfront_per_unit),
                           "lifetime": _finite(p.lifetime), "fom_share": _finite(p.fom_share),
                           "derived_from_capital_cost": p.derived_from_capital_cost} for p in parts],
                "upfront_per_unit": _finite(per_unit) if parts else None,
                "upfront_total": _finite(per_unit * size) if parts and size is not None else None,
                "capital_cost": _finite(number(row.get("capital_cost"))),
                "fom_cost": _finite(number(row.get("fom_cost"))),
            })
    return out
