"""
The solve-time lost-production slack (grid-to-process ticket 08, design §2.1 and §5).

A product bus with a ``lost_production_value`` (EUR per bus unit, ``models.process_unit``) and no hand-built shortfall
Generator gets ``__lost_<bus>`` beside the VoLL slacks. It is priced at that value (never VoLL), bounded by the bus's
delivery in each snapshot, and carries ``process_role`` ``shortfall``. Like the VoLL slacks it is a transient LP
transform: marked, added, and on restore its dispatch is captured into ``captured["lost_production"]`` before the row
(and any column or carrier added for it) is removed, so it never reaches the saved network or the sidecars.

The capture rides in the solve's ``last_lost_load`` record (saved, reloaded and handed to every reader with it), and
is what every reader of the shortfall reads when no role Generator exists (``process_products.shortfalls``):

    {"p_t": DataFrame (snapshots × bus, bus units per hour), "value": {bus: EUR per bus unit},
     "key": {bus: solve key}}

The solve key (``capture_key``) holds the snapshots and the value the solve used. A network edited since then
(other snapshots, another value, a hand-built shortfall added) reads the capture as stale, never as current.

A hand-built shortfall Generator wins: its bus gets no slack and no capture. A value at or below the bus's buy-in
(the highest of its dense marginal cost: a grey purchase carries its CO2 in a series) is refused.
"""
from __future__ import annotations

import hashlib
import math

import pandas as pd

from models.process_unit import product_role_generators
from services.adequacy.slack import lost_slack_name
from services.pypsa_service import PyPSAService

#: The carrier of the solve-time slack (added for the solve when the network has none).
CARRIER = "lost_production"


class LostProductionRefused(ValueError):
    """A bus's lost-production value at or below what its buy-in costs: the LP would buy by shedding."""


def bus_values(n) -> dict[str, float]:
    """{bus: lost_production_value} for every bus with a finite value above 0."""
    if "lost_production_value" not in n.buses.columns:
        return {}
    v = pd.to_numeric(n.buses["lost_production_value"], errors="coerce")
    return {str(b): float(x) for b, x in v.items() if math.isfinite(x) and x > 0}


def capture_key(n, bus: str, value: float) -> str:
    """What the capture was solved on: the snapshots and the value."""
    blob = f"{bus}|{value!r}|{len(n.snapshots)}|{n.snapshots[0]!r}|{n.snapshots[-1]!r}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _delivery(n, bus: str) -> pd.Series:
    loads = n.loads.index[n.loads.bus.astype(str) == bus]
    if len(loads) == 0:
        return pd.Series(0.0, index=n.snapshots)
    return n.get_switchable_as_dense("Load", "p_set")[loads].sum(axis=1).clip(lower=0.0)


def refusals(n) -> list[tuple[str, str]]:
    """(bus, why) for every bus value that cannot be solved: a value at or below the buy-in on its bus."""
    out = []
    buy = product_role_generators(n, "buy_in")
    mc = n.get_switchable_as_dense("Generator", "marginal_cost") if len(buy) else None
    for bus, value in bus_values(n).items():
        for g in (g for g in buy if str(n.generators.at[g, "bus"]) == bus):
            top = float(mc[g].max())
            if value <= top:
                out.append((bus, f"Bus {bus!r}: lost_production_value {value:g} is at or below the buy-in {g!r} "
                           f"(up to {top:g} per unit, its CO2 included): the LP would buy by shedding. The "
                           "lost-production value must be above the purchase price."))
    return out


def apply(n, phase, undo_actions: list, captured: dict) -> None:
    """Add the slacks (module docstring); the restore callback appended to `undo_actions` captures and removes."""
    values = bus_values(n)
    if not values:
        return
    refused = refusals(n)
    if refused:
        raise LostProductionRefused(" ".join(why for _bus, why in refused))
    hand_built = {str(n.generators.at[g, "bus"]) for g in product_role_generators(n, "shortfall")}
    names, buses, p_noms, max_pu, costs = [], [], [], {}, []
    for bus, value in sorted(values.items()):
        name = lost_slack_name(bus)
        if bus in hand_built or bus not in n.buses.index or name in n.generators.index:
            continue
        demand = _delivery(n, bus)
        p_nom = max(float(demand.max()), 1.0)
        names.append(name)
        buses.append(bus)
        p_noms.append(p_nom)
        costs.append(value)
        max_pu[name] = (demand / p_nom).clip(upper=1.0)
    if not names:
        return
    added_carrier = CARRIER not in n.carriers.index
    added_column = "process_role" not in n.generators.columns
    for name in names:
        PyPSAService.mark_transient("Generator", name)
    try:
        if added_carrier:
            n.add("Carrier", CARRIER)
        n.add("Generator", names, bus=buses, p_nom=p_noms, marginal_cost=costs, carrier=CARRIER,
              p_max_pu=pd.DataFrame(max_pu, index=n.snapshots))
        if added_column:
            n.generators["process_role"] = ""
        n.generators.loc[names, "process_role"] = "shortfall"
    except Exception:
        for name in names:
            PyPSAService.unmark_transient("Generator", name)
        raise
    phase(f"Added {len(names)} lost-production slack(s) at the buses' lost-production value "
          f"({', '.join(f'{b} {c:g}' for b, c in zip(buses, costs))}).")

    def _capture_and_remove(names=tuple(names), buses=tuple(buses), costs=tuple(costs)):
        try:
            p = n.generators_t.p
            live = [nm for nm in names if nm in p.columns]
            if live:
                frame = p[live].copy()
                frame.columns = [buses[names.index(nm)] for nm in live]
                captured["lost_production"] = {
                    "p_t": frame,
                    "value": {b: float(c) for b, c in zip(buses, costs)},
                    "key": {b: capture_key(n, b, float(c)) for b, c in zip(buses, costs)},
                }
        except Exception:
            pass
        for nm in names:
            if nm in n.generators.index:
                n.remove("Generator", nm)
            PyPSAService.unmark_transient("Generator", nm)
        if added_column and "process_role" in n.generators.columns:
            n.generators.drop(columns="process_role", inplace=True)
        if added_carrier and CARRIER in n.carriers.index:
            n.remove("Carrier", CARRIER)

    undo_actions.append(("call", _capture_and_remove))
