"""
PyPSA's cost columns, derived from an asset's investment parts.

For a composite asset (`schema.COMPOSITE`) this is the one place its annual `capital_cost` comes from:

    capital_cost = sum over parts of  overnight x scale x annuity(rate, part lifetime)
    fom_cost     = sum over parts of  overnight x scale x fom_share
    lifetime     = the longest part's lifetime (the part that retires the asset; shorter parts are replacements)
    overnight_cost = NaN

`overnight_cost` stays empty on purpose: when it is set, PyPSA 1.1.2 annuitises it over the single `lifetime`
and ignores `capital_cost` (`pypsa.costs.periodized_cost`), which would charge a battery's inverter over the
storage block's life. The annuity is `periodized_costs._annuity`, the GUI's one annuity.

`capital_cost` is ANNUAL here, like every `capital_cost` the GUI stores; the solve-time fill
(`periodized_costs.fill_periodized_cost_defaults`) re-derives it with the discount rate in force and then scales
it to the modelled horizon.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from services.asset_schema.schema import CLASS_ATTR, PART_FIELDS, composite_parts, part_columns
from services.solver.periodized_costs import _annuity


def number(v: Any) -> float | None:
    """A finite-or-inf float, or None for a missing or NaN value."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def has_parts(component_class: str, values: Mapping[str, Any]) -> bool:
    """True when `values` prices at least one part of a composite class."""
    return any(number(values.get(p.column("overnight"))) is not None for p in composite_parts(component_class))


def derive_composite(component_class: str, parts: Mapping[str, Any], *,
                     max_hours: float, discount_rate: float) -> dict[str, float]:
    """The derived PyPSA columns of a composite asset. Refuses a priced part without a lifetime."""
    specs = composite_parts(component_class)
    if not specs:
        raise ValueError(f"{component_class} has no composite investment parts")
    scales = {"max_hours": float(max_hours)}
    capital = fom = 0.0
    lifetimes: list[float] = []
    for spec in specs:
        overnight = number(parts.get(spec.column("overnight")))
        if overnight is None:
            continue
        life = number(parts.get(spec.column("lifetime")))
        if life is None or not math.isfinite(life) or life <= 0:
            raise ValueError(f"{component_class} {spec.name} part is priced but has no lifetime")
        share = number(parts.get(spec.column("fom_share"))) or 0.0
        per_unit = overnight * (scales[spec.scale_by] if spec.scale_by else 1.0)
        capital += per_unit * _annuity(float(discount_rate), life)
        fom += per_unit * share
        lifetimes.append(life)
    if not lifetimes:
        raise ValueError(f"{component_class} has no priced investment part")
    return {"capital_cost": capital, "fom_cost": fom, "lifetime": max(lifetimes),
            "overnight_cost": float("nan")}


def split_parts(component_class: str, kwargs: dict) -> tuple[dict, dict]:
    """Separate a composite class's part fields from the PyPSA kwargs (the catalog whitelist would drop them)."""
    cols = set(part_columns(component_class))
    parts = {k: v for k, v in kwargs.items() if k in cols}
    rest = {k: v for k, v in kwargs.items() if k not in cols}
    return rest, parts


def effective_discount_rate(asset_rate: Any, global_rate: float) -> float:
    """The asset's own rate when it has one, else the project's."""
    r = number(asset_rate)
    return r if r is not None else float(global_rate)


def apply_parts(n, component_class: str, name: str, parts: Mapping[str, Any], *, discount_rate: float) -> None:
    """
    Write a composite asset's parts and its derived columns onto an existing row. A no-op when `parts` prices
    nothing, so an asset typed the old way (a bare `capital_cost`) is written exactly as before.
    """
    df = getattr(n, CLASS_ATTR[component_class])
    current = {c: df.at[name, c] for c in part_columns(component_class) if c in df.columns}
    merged = {**current, **{k: v for k, v in parts.items()}}
    if not has_parts(component_class, merged):
        return
    max_hours = float(df.at[name, "max_hours"]) if "max_hours" in df.columns else 1.0
    derived = derive_composite(component_class, merged, max_hours=max_hours, discount_rate=discount_rate)
    for spec in composite_parts(component_class):
        for field in PART_FIELDS:
            col = spec.column(field)
            v = number(merged.get(col))
            if col not in df.columns:
                df[col] = float("nan")
            df.at[name, col] = float("nan") if v is None else v
    for col, v in derived.items():
        df.at[name, col] = v
