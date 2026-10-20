"""
Grid-to-process products as results read them (ticket 04): which buses carry a product, what a product is worth,
and what a Generator on a product bus stands for.

A Generator's role comes from its `process_role` column (`models/process_unit.py`). The `__buy_<p>` / `__lost_<p>`
names of the design (§2.1) are only a fallback, and a role read from a name carries the flag `role_from_name`, so
a hidden naming convention never decides a booking silently (gatekeeper, 2026-10-19). Ownership never reads the
fallback: the value flows and the investment case take a role from the column alone (`process_unit.product_roles`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from models.process_unit import PROCESS_GENERATOR_ROLES, product_role_generators
from services.carrier_catalog import CARRIER_CATALOG

BUY_IN, SHORTFALL = PROCESS_GENERATOR_ROLES
_NAME_PREFIX = {"__buy_": BUY_IN, "__lost_": SHORTFALL}


def _cell(df, row: str, col: str):
    return df.at[row, col] if col in df.columns and row in df.index else None


def _positive(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def bus_unit(n, bus: str) -> str:
    """The unit a product bus counts in: its own `unit` column, else its carrier's catalogue unit, else ""."""
    unit = str(_cell(n.buses, bus, "unit") or "").strip()
    if unit in ("t", "MWh"):
        return unit
    carrier = str(_cell(n.buses, bus, "carrier") or "")
    return CARRIER_CATALOG.get(carrier, {}).get("unit", "")


def is_product_bus(n, bus: str) -> bool:
    """A bus in tonnes, or a bus whose carrier is a catalogued product (NH3, N2, ore, concentrate)."""
    unit = str(_cell(n.buses, bus, "unit") or "").strip()
    carrier = str(_cell(n.buses, bus, "carrier") or "")
    return unit == "t" or "unit" in CARRIER_CATALOG.get(carrier, {})


def product_buses(n) -> list[str]:
    return [str(b) for b in n.buses.index if is_product_bus(n, b)]


def product_value(n, bus: str) -> float | None:
    """EUR per bus unit from the bus's `product_value`; None (Unavailable) when unset, never 0."""
    return _positive(_cell(n.buses, bus, "product_value"))


def generator_role(n, gen: str) -> tuple[str, str | None]:
    """(role, flag) for one Generator: the `process_role` column, else the design's name prefix flagged
    `role_from_name`, else ("", None)."""
    for role in PROCESS_GENERATOR_ROLES:
        if gen in product_role_generators(n, role):
            return role, None
    for prefix, named in _NAME_PREFIX.items():
        if str(gen).startswith(prefix):
            return named, "role_from_name"
    return "", None


def lost_production_value(n, gen: str) -> float | None:
    """A shortfall Generator's lost-production value, EUR per bus unit: its marginal_cost, the ledgered input the LP
    priced. Never VoLL. None (Unavailable) when it is not a finite positive number."""
    return _positive(_cell(n.generators, gen, "marginal_cost"))


@dataclass(frozen=True)
class Shortfall:
    """One source of a product's lost production, as every reader takes it (ticket 08, gatekeeper)."""

    bus: str
    name: str                   # the role Generator, or the solve-time slack's name (a capture)
    p: pd.Series | None         # dispatch per snapshot, bus units per hour; None when not readable
    cost: pd.Series | None      # what the LP priced it at per snapshot (EUR per bus unit)
    value: float | None         # the lost-production value (EUR per bus unit); None: Unavailable
    source: str                 # "generator" | "capture"
    flag: str | None            # role_from_name | lost_production_capture_stale | lost_production_capture_missing


def _capture_key(n, bus: str, value: float) -> str:
    from services.solver.lost_production import capture_key

    return capture_key(n, bus, value)


def shortfalls(n, lost_load=None, *, gen_p=None) -> list[Shortfall]:
    """Every product shortfall of a solved network: the one accessor (results, compare, energy flows, the ledger).

    A role Generator (its `process_role`, else the name fallback, flagged) is read from its dispatch (`gen_p`, else
    `n.generators_t.p`) at its dense marginal cost; it wins, so its bus is never also read from the capture. A bus
    with a `lost_production_value` and no such Generator is read from the solve's capture
    (`services.solver.lost_production`, the `lost_production` entry of the solve's `last_lost_load` record): stale
    when the network changed what that solve keyed (flagged, never
    current), and with no capture at all its dispatch is None (`lost_production_capture_missing`), never 0."""
    from services.solver.lost_production import bus_values
    from services.adequacy.slack import lost_slack_name

    gen_p = n.generators_t.p if gen_p is None else gen_p
    out: list[Shortfall] = []
    own: set[str] = set()
    mc = None
    for g in n.generators.index:
        role, flag = generator_role(n, g)
        if role != SHORTFALL:
            continue
        if mc is None:
            mc = n.get_switchable_as_dense("Generator", "marginal_cost")
        bus = str(n.generators.at[g, "bus"])
        own.add(bus)
        p = gen_p[g].reindex(n.snapshots) if g in getattr(gen_p, "columns", ()) else None
        out.append(Shortfall(bus, str(g), p, mc[g], lost_production_value(n, g), "generator", flag))
    capture = (lost_load or {}).get("lost_production") if isinstance(lost_load, dict) else None
    frame = (capture or {}).get("p_t")
    values = dict((capture or {}).get("value") or {})
    keys = dict((capture or {}).get("key") or {})
    current = bus_values(n)
    for bus in sorted((set(current) | set(values)) - own):
        value = values.get(bus, current.get(bus))
        if frame is None or bus not in frame.columns or value is None:
            out.append(Shortfall(bus, lost_slack_name(bus), None, None, _positive(current.get(bus)), "capture",
                                 "lost_production_capture_missing"))
            continue
        stale = bus not in current or keys.get(bus) != _capture_key(n, bus, current[bus])
        cost = pd.Series(float(value), index=n.snapshots)
        out.append(Shortfall(bus, lost_slack_name(bus), frame[bus].reindex(n.snapshots), cost, float(value),
                             "capture", "lost_production_capture_stale" if stale else None))
    return out
