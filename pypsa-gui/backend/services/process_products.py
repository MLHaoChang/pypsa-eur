"""
Grid-to-process products as results read them (ticket 04): which buses carry a product, what a product is worth,
and what a Generator on a product bus stands for.

A Generator's role comes from its `process_role` column (`models/process_unit.py`). The `__buy_<p>` / `__lost_<p>`
names of the design (§2.1) are only a fallback, and a role read from a name carries the flag `role_from_name`, so
a hidden naming convention never decides a booking silently (gatekeeper, 2026-10-19). Ownership never reads the
fallback: the value flows and the investment case take a role from the column alone (`process_unit.product_roles`).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import pandas as pd

from models.process_unit import PROCESS_GENERATOR_ROLES, bus_values, product_role_generators
from services.adequacy.slack import LOST_SLACK_PREFIX, lost_slack_name
from services.carrier_catalog import CARRIER_CATALOG

BUY_IN, SHORTFALL = PROCESS_GENERATOR_ROLES
_NAME_PREFIX = {"__buy_": BUY_IN, LOST_SLACK_PREFIX: SHORTFALL}


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


def t_per_unit(n, bus: str) -> float | None:
    """Tonnes per bus unit: 1 on a bus in t, 1 / mwh_per_t on a bus in MWh, None when not known."""
    if bus_unit(n, bus) == "t":
        return 1.0
    f = _positive(_cell(n.buses, bus, "mwh_per_t"))
    return None if f is None else 1.0 / f


_KEYED = ("Bus", "Carrier", "Generator", "Load", "Link", "Store", "StorageUnit", "Line", "Transformer",
          "GlobalConstraint")


def capture_key(n, bus: str, value: float) -> str:
    """What a solve's lost-production capture was solved on: the bus, its value, the snapshots and their weights,
    and every INPUT of the network (each component's non-output columns, custom ones included, and its input time
    series). Any edit since the solve gives another key, so the capture reads stale, never current."""
    h = hashlib.sha256(f"{bus}|{value!r}".encode())

    def column(name, series, default):
        """One column, normalised so a save and reload (or a rebuild) hashes the same: missing values read as the
        attribute's default, numbers as floats, a column holding only its default is left out."""
        num = pd.to_numeric(series, errors="coerce")
        if num.notna().sum() == series.notna().sum():
            d = pd.to_numeric(pd.Series([default]), errors="coerce").iloc[0]
            vals = num.astype(float).fillna(d if pd.notna(d) else float("nan"))
            if pd.notna(d) and (vals == d).all():
                return
            body = vals.round(12).to_numpy().tobytes()
        else:
            vals = series.astype(object).where(series.notna(), "" if default is None else default).astype(str)
            if (vals == ("" if default is None else str(default))).all():
                return
            body = "\x1f".join(vals).encode()
        h.update(f"{name}\x1e".encode())
        h.update("\x1f".join(map(str, series.index)).encode())
        h.update(body)

    def frame(tag, df, defaults=None):
        if df is None or df.empty:
            return
        for c in sorted(df.columns, key=str):
            dflt = defaults.get(c) if isinstance(defaults, dict) else defaults
            column(f"{tag}.{c}", df[c], dflt)

    frame("weights", n.snapshot_weightings)
    for name in _KEYED:
        comp = n.components[name]
        attrs = comp.defaults
        outputs = set(attrs.index[attrs["status"] == "Output"])
        static = comp.static
        frame(name, static[[c for c in static.columns if c not in outputs]], attrs["default"].to_dict())
        dynamic = comp.dynamic
        for attr in sorted(a for a in attrs.index[attrs["varying"].astype(bool)] if a not in outputs):
            if attr in dynamic:
                frame(f"{name}_t.{attr}", dynamic[attr], None)
    return h.hexdigest()[:16]


def shortfalls(n, lost_load=None, *, gen_p=None) -> list[Shortfall]:
    """Every product shortfall of a solved network: the one accessor (results, compare, energy flows, the ledger).

    A role Generator (its `process_role`, else the name fallback, flagged) is read from its dispatch (`gen_p`, else
    `n.generators_t.p`) at its dense marginal cost; it wins, so its bus is never also read from the capture. A bus
    with a `lost_production_value` and no such Generator is read from the solve's capture
    (`services.solver.lost_production`, the `lost_production` entry of the solve's `last_lost_load` record): stale
    when the network changed what that solve keyed (flagged, never
    current), and with no capture at all its dispatch is None (`lost_production_capture_missing`), never 0."""
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
        stale = bus not in current or keys.get(bus) != capture_key(n, bus, current[bus])
        cost = pd.Series(float(value), index=n.snapshots)
        out.append(Shortfall(bus, lost_slack_name(bus), frame[bus].reindex(n.snapshots), cost, float(value),
                             "capture", "lost_production_capture_stale" if stale else None))
    return out
