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
            out.append(UpfrontPart(spec.name, overnight * scale, number(row.get(spec.column("lifetime"))),
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
