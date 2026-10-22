"""Readiness feed (RP-02): one row per wind/solar generator, read-only.

Coordinates are the bus's (spec R1; `network_geometry._bus_coord` is the one
missing-coordinates rule). The profile state reads the same stores the
editors do: `ctx.user_ts` + `ctx.user_ts_meta` (an entry with no record, or a
measured one, is `uploaded`; any other is `generated`), then the network's own
`generators_t.p_max_pu` (`network_native`).

A generated profile is stale when its bus moved since generation (the
apply route's 0.5° rule) or when the model's snapshots are no longer the
hours it was written on. A bus that has lost its coordinates since is
reported as `coord_source: missing` and is not called stale.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from services.network_geometry import _bus_coord

# Same bound the apply route warns at (routers/profiles.py apply_profile).
BUS_MOVED_DEG = 0.5
KEY = ("generators", "p_max_pu")


def technology_of(carrier: Any) -> str | None:
    """wind / solar by the engine's carrier guard (`families._renewable_guard`)."""
    cl = str(carrier or "").lower()
    if "wind" in cl:
        return "wind"
    if "solar" in cl or "pv" in cl:
        return "solar"
    return None


def _stamps(series: pd.Series) -> pd.DatetimeIndex:
    idx = series.index
    return pd.DatetimeIndex(idx.get_level_values(-1) if isinstance(idx, pd.MultiIndex) else idx)


def _profile_state(ctx, name: str) -> tuple[str, dict | None, str | None]:
    n = ctx.network
    key = (*KEY, name)
    series = ctx.user_ts.get(key)
    if series is None:
        native = name in n.generators_t.p_max_pu.columns
        return ("network_native" if native else "none"), None, None
    meta = ctx.user_ts_meta.get(key) or {}
    if not meta or str(meta.get("provenance") or "").startswith("measured"):
        return "uploaded", None, None
    stale = None
    snaps = pd.DatetimeIndex(
        n.snapshots.get_level_values(-1) if isinstance(n.snapshots, pd.MultiIndex) else n.snapshots)
    if not _stamps(series).equals(snaps):
        stale = "horizon_changed"
    provenance = {"source": meta.get("source"), "weather_year": meta.get("weather_year"),
                  "provenance": meta.get("provenance")}
    return "generated", provenance, stale


def _bus_moved(meta: dict, name: str, now: tuple[float, float] | None) -> bool:
    """Whether the point the series was made for is now far from the bus.
    The point is the applied `point_id` the apply route records, else the
    generator's own name (the default point id), else a single-point job's
    only point; anything else stays unchecked (not stale)."""
    coords = meta.get("point_coords") or {}
    then = coords.get(meta.get("point_id")) or coords.get(name)
    if then is None and len(coords) == 1:
        then = next(iter(coords.values()))
    if then is None or now is None:
        return False
    return abs(now[0] - then[0]) > BUS_MOVED_DEG or abs(now[1] - then[1]) > BUS_MOVED_DEG


def unplaced_renewables(n) -> list[tuple[str, str]]:
    """`(generator, bus)` for each wind/solar generator whose bus has no
    location, by the same rule as the flow's `buses_placed`."""
    out: list[tuple[str, str]] = []
    for name, gen in n.generators.iterrows():
        if technology_of(gen["carrier"]) is None:
            continue
        bus = str(gen["bus"] or "")
        if _bus_coord(n, bus) is None:
            out.append((str(name), bus))
    return out


def readiness_rows(ctx) -> list[dict[str, Any]]:
    n = ctx.network
    rows: list[dict[str, Any]] = []
    for name, gen in n.generators.iterrows():
        tech = technology_of(gen["carrier"])
        if tech is None:
            continue
        name = str(name)
        bus = str(gen["bus"] or "")
        coord = _bus_coord(n, bus)
        profile, provenance, stale = _profile_state(ctx, name)
        row: dict[str, Any] = {
            "generator": name, "technology": tech, "bus": bus,
            "lon": coord[0] if coord else None,
            "lat": coord[1] if coord else None,
            "coord_source": "bus" if coord else "missing",
            "profile": profile,
        }
        if provenance is not None:
            row["provenance"] = provenance
            if _bus_moved(ctx.user_ts_meta.get((*KEY, name)) or {}, name, coord):
                stale = "bus_moved"
            if stale:
                row["stale_reason"] = stale
        rows.append(row)
    return rows
