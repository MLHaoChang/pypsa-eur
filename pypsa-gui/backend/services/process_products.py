"""
Grid-to-process products as results read them (ticket 04): which buses carry a product, what a product is worth,
and what a Generator on a product bus stands for.

A Generator's role comes from its `process_role` column (`models/process_unit.py`). The `__buy_<p>` / `__lost_<p>`
names of the design (§2.1) are only a fallback, and a role read from a name carries the flag `role_from_name`, so
a hidden naming convention never decides a booking silently (gatekeeper, 2026-10-19).
"""
from __future__ import annotations

import math

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
