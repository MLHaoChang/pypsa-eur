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

``stamp`` writes the key once the solve's transforms are restored (``process_products.capture_key``: the inputs the
product's shortfall depends on). A network edited since then reads the capture as stale, never as current.

A hand-built shortfall Generator wins: its bus gets no slack, no capture and no refusal. A value at or below the bus's
buy-in (the highest of its dense marginal cost: a grey purchase carries its CO2 in a series) is refused.
"""
from __future__ import annotations

import pandas as pd

from models.process_unit import product_role_generators
from services.adequacy.slack import lost_slack_name
from services.process_products import bus_values, capture_key
from services.pypsa_service import PyPSAService

#: The carrier of the solve-time slack (added for the solve when the network has none).
CARRIER = "lost_production"


class LostProductionRefused(ValueError):
    """A bus's lost-production value at or below what its buy-in costs: the LP would buy by shedding."""


def _delivery_bound(n, bus: str) -> pd.Series:
    """The most the bus can fall short of in each snapshot: its loads' p_set, never negative."""
    loads = n.loads.index[n.loads.bus.astype(str) == bus]
    if len(loads) == 0:
        return pd.Series(0.0, index=n.snapshots)
    return n.get_switchable_as_dense("Load", "p_set")[loads].sum(axis=1).clip(lower=0.0)


def _slack_buses(n) -> dict[str, float]:
    """{bus: value} of the buses that get a slack: a value and no hand-built shortfall Generator."""
    hand_built = {str(n.generators.at[g, "bus"]) for g in product_role_generators(n, "shortfall")}
    return {b: v for b, v in bus_values(n).items() if b not in hand_built and b in n.buses.index}


def refusals(n) -> list[tuple[str, str]]:
    """(bus, why) for every slack that cannot be solved: a value at or below the buy-in on its bus."""
    out = []
    buy = product_role_generators(n, "buy_in")
    mc = n.get_switchable_as_dense("Generator", "marginal_cost") if len(buy) else None
    for bus, value in _slack_buses(n).items():
        for g in (g for g in buy if str(n.generators.at[g, "bus"]) == bus):
            top = float(mc[g].max())
            if value <= top:
                out.append((bus, f"Bus {bus!r}: lost_production_value {value:g} is at or below the buy-in {g!r} "
                           f"(up to {top:g} per unit, its CO2 included): the LP would buy by shedding. The "
                           "lost-production value must be above the purchase price."))
    return out


def apply(n, phase, undo_actions: list, captured: dict) -> None:
    """Add the slacks (module docstring); the restore callback appended to `undo_actions` captures and removes."""
    buses = _slack_buses(n)
    if not buses:
        return
    refused = refusals(n)
    if refused:
        raise LostProductionRefused(" ".join(why for _bus, why in refused))
    rows = []                                   # (name, bus, value, p_nom, p_max_pu)
    for bus, value in sorted(buses.items()):
        name = lost_slack_name(bus)
        if name in n.generators.index:
            continue
        demand = _delivery_bound(n, bus)
        p_nom = max(float(demand.max()), 1.0)
        rows.append((name, bus, value, p_nom, (demand / p_nom).clip(upper=1.0)))
    if not rows:
        return
    names = [r[0] for r in rows]
    added_carrier = CARRIER not in n.carriers.index
    added_column = "process_role" not in n.generators.columns
    for name in names:
        PyPSAService.mark_transient("Generator", name)
    try:
        if added_carrier:
            n.add("Carrier", CARRIER)
        n.add("Generator", names, bus=[r[1] for r in rows], p_nom=[r[3] for r in rows],
              marginal_cost=[r[2] for r in rows], carrier=CARRIER,
              p_max_pu=pd.DataFrame({r[0]: r[4] for r in rows}, index=n.snapshots))
        if added_column:
            n.generators["process_role"] = ""
        n.generators.loc[names, "process_role"] = "shortfall"
    except Exception:
        for name in names:
            PyPSAService.unmark_transient("Generator", name)
        raise
    phase(f"Added {len(rows)} lost-production slack(s) at the buses' lost-production value "
          f"({', '.join(f'{r[1]} {r[2]:g}' for r in rows)}).")

    def _capture_and_remove(rows=tuple(rows)):
        try:
            p = n.generators_t.p
            live = [r for r in rows if r[0] in p.columns]
            if live:
                frame = p[[r[0] for r in live]].copy()
                frame.columns = [r[1] for r in live]
                captured["lost_production"] = {"p_t": frame, "value": {r[1]: float(r[2]) for r in rows},
                                               "key": {}}
        except Exception:
            pass
        for r in rows:
            if r[0] in n.generators.index:
                n.remove("Generator", r[0])
            PyPSAService.unmark_transient("Generator", r[0])
        if added_column and "process_role" in n.generators.columns:
            n.generators.drop(columns="process_role", inplace=True)
        if added_carrier and CARRIER in n.carriers.index:
            n.remove("Carrier", CARRIER)

    undo_actions.append(("call", _capture_and_remove))


def stamp(n, captured: dict) -> None:
    """Key the capture to the network as the solve leaves it (every transform restored), so a later edit of what
    the shortfall depends on reads it as stale."""
    cap = captured.get("lost_production")
    if cap:
        cap["key"] = {bus: capture_key(n, bus, value) for bus, value in cap["value"].items()}
