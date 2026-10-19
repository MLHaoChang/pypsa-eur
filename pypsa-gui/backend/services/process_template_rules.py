"""
The template validator's process rules (grid-to-process ticket 03, design §2.1 and §2.3).

``refusals(n, solver_config)`` lists why a bundled template network must not ship; empty means it
passes. ``project_templates/_build.py`` refuses to build on any, and the template test runs it over
every builder. The rules are solver semantics, so they live here and not in the template tickets:

* a start-up or shut-down cost above 0 on a weighted network (PyPSA leaves it unweighted,
  ``services.solver.process_uc``): the restart belongs in ``min_down_time`` and the shortfall
  slack, any other cost of a start in ``stand_by_cost``;
* a process unit both committable and extendable (big-M, so u·p_nom is bilinear);
* a delivered product (a process unit's output bus that carries a Load, or a bus with a role
  Generator) without its own shortfall slack (the solve's VoLL slack would then value the
  product at VoLL, #312), or a lost-production value missing or equal to VoLL. An intermediate
  product (one unit's output feeding another, no Load) needs none;
* a lost-production value at or below the buy-in price, or the LP "buys" by shedding.

User networks are not refused by these: the solver warns on the start-up cost there
(``validation_service``, ``uc_start_cost_unweighted``).
"""
from __future__ import annotations

import math

import pandas as pd

from models.process_unit import product_role_generators
from services.solver.process_uc import (committable_mask, flag_column, process_mask,
                                       weighted_start_cost_units)


def _cost(n, g) -> pd.Series:
    """The generator's marginal cost per snapshot (the static value where no series is set)."""
    t = n.generators_t.marginal_cost
    if g in t.columns:
        return pd.to_numeric(t[g], errors="coerce")
    return pd.Series(pd.to_numeric(pd.Series([n.generators.at[g, "marginal_cost"]]),
                                   errors="coerce").iloc[0], index=n.snapshots)


def _voll(solver_config) -> float | None:
    """The template's VoLL, or None when it sets none (never read as 0)."""
    if solver_config is None:
        return None
    v = (solver_config.get("voll") if isinstance(solver_config, dict)
         else getattr(solver_config, "voll", None))
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def _start_costs(n) -> list[str]:
    return [f"{comp} {name!r}: {attr} = {value:g} on a weighted network (snapshot or period "
            "objective weight ≠ 1). PyPSA 1.1.2 does not weight it, so it would be undercounted. "
            "Set it to 0: model the restart as min_down_time and price its lost tonnes through the "
            "shortfall slack, and put any other cost of a start in stand_by_cost."
            for comp, name, attr, value in weighted_start_cost_units(n)]


def _committable_extendable(n) -> list[str]:
    links = n.links
    hit = links.index[process_mask(links) & committable_mask(links)
                      & flag_column(links, "p_nom_extendable")]
    return [f"Process unit {name!r} is committable and extendable: PyPSA models that with big-M, "
            "so u·p_nom is bilinear. A process unit is a fixed-size input; a later expansion is "
            "another fixed-size row with build_year." for name in hit]


def _delivered_products(n, shortfall, buy) -> set[str]:
    """Process output buses that carry a Load, and every bus with a role Generator."""
    links = n.links
    outputs = {str(links.at[u, "bus1"]) for u in links.index[process_mask(links)]}
    with_load = {str(b) for b in n.loads.bus}
    return (outputs & with_load) | {str(n.generators.at[g, "bus"]) for g in [*shortfall, *buy]}


def _lost_value(n, g, bus, buy, voll) -> list[str]:
    c = _cost(n, g)
    if c.isna().any() or not (c > 0).all():
        return [f"Shortfall slack {g!r} on {bus!r} has no lost-production value (marginal_cost "
                "missing or ≤ 0). It is a ledgered input, never a default."]
    out = []
    if voll is not None and math.isclose(float(c.min()), voll) and math.isclose(float(c.max()), voll):
        out.append(f"Shortfall slack {g!r} on {bus!r} is priced at VoLL ({voll:g}): the "
                   "lost-production value is a ledgered input per product, never defaulted from "
                   "VoLL (#312).")
    for b in (x for x in buy if n.generators.at[x, "bus"] == bus):
        cb = _cost(n, b).reindex(c.index)
        if (c <= cb).any():
            out.append(f"Shortfall slack {g!r} is priced at or below the buy-in {b!r} on {bus!r} "
                       f"(lost {float(c.min()):g} ≤ buy-in {float(cb.max()):g}): the LP would buy "
                       "by shedding. The lost-production value must be above the purchase price.")
    return out


def _product_slacks(n, solver_config) -> list[str]:
    shortfall = product_role_generators(n, "shortfall")
    buy = product_role_generators(n, "buy_in")
    voll = _voll(solver_config)
    out = []
    for bus in sorted(_delivered_products(n, shortfall, buy)):
        here = [g for g in shortfall if n.generators.at[g, "bus"] == bus]
        if not here:
            out.append(f"Product bus {bus!r} has no shortfall slack (a Generator with process_role "
                       "'shortfall'): the solve's VoLL slack would value the lost production at "
                       "VoLL (#312). Add one priced at the ledgered lost-production value.")
        for g in here:
            out += _lost_value(n, g, bus, buy, voll)
    return out


def refusals(n, solver_config=None) -> list[str]:
    return [*_start_costs(n), *_committable_extendable(n), *_product_slacks(n, solver_config)]
