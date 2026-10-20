"""Readiness feed for wind/solar generators (RP-02).

Spec: .scratch/renewable-profiles/issues/02-readiness-feed.md (R1).

One row per wind/solar generator on a network: where its coordinates come
from, what profile it carries, the generated profile's provenance, and
whether that provenance is stale. Read-only — the Time Series Manager, the
chat tools and the Guided offer read it; `apply.assign_profile` stays the
one writer.

- ``coord_source``: ``bus`` (the bus has usable coordinates, the rule
  `network_geometry._bus_coord` applies to the map) or ``missing``.
  PyPSA generators carry no coordinates of their own.
- ``profile``: ``generated`` (a user-TS entry whose meta is the engine's
  provenance), ``uploaded`` (a user-TS entry without it — saving a project
  moves its own ``_t`` profiles into the store, so they read as uploaded
  from then on, the safe reading), ``network_native`` (a ``p_max_pu``
  column the store has not seen) or ``none``.
- ``stale_reason``: ``bus_moved`` (the bus sits more than 0.5° from the
  point the profile was generated for — apply's warning threshold) or
  ``bus_missing_coordinates``; None otherwise.
"""
from __future__ import annotations

from typing import Any, Mapping

from services.network_geometry import _bus_coord
from services.profile_gen.apply import carrier_flags

# Provenance fields a reader needs; the full meta stays in user_ts_meta.
_PROVENANCE_KEYS = (
    "technology", "source", "preset", "weather_year", "resolution",
    "time_basis", "time_basis_note", "leap_day", "tiled", "calibrated",
    "parent_job", "job_id", "generated_at", "applied_at",
)
_MOVED_DEGREES = 0.5


def _generated_point(meta: Mapping[str, Any], component: str):
    coords = meta.get("point_coords") or {}
    if component in coords:
        return coords[component]
    if len(coords) == 1:
        return next(iter(coords.values()))
    return None


def readiness_rows(ctx) -> list[dict[str, Any]]:
    """The feed for ``ctx``'s network, one row per wind/solar generator in
    network order."""
    n = ctx.network
    native = getattr(n.generators_t, "p_max_pu", None)
    native_cols = set(native.columns) if native is not None else set()
    rows: list[dict[str, Any]] = []
    for name in n.generators.index:
        is_wind, is_solar = carrier_flags(n.generators.at[name, "carrier"])
        if not (is_wind or is_solar):
            continue
        bus = str(n.generators.at[name, "bus"] or "")
        coord = _bus_coord(n, bus)
        key = ("generators", "p_max_pu", name)
        meta = ctx.user_ts_meta.get(key)
        generated = isinstance(meta, Mapping) and "source" in meta
        if key in ctx.user_ts:
            profile = "generated" if generated else "uploaded"
        elif name in native_cols:
            profile = "network_native"
        else:
            profile = "none"
        stale = None
        provenance = None
        if profile == "generated":
            provenance = {k: meta[k] for k in _PROVENANCE_KEYS if k in meta}
            was = _generated_point(meta, name)
            if was is not None:
                if coord is None:
                    stale = "bus_missing_coordinates"
                elif (abs(coord[0] - was[0]) > _MOVED_DEGREES
                      or abs(coord[1] - was[1]) > _MOVED_DEGREES):
                    stale = "bus_moved"
        rows.append({
            "generator": str(name),
            "technology": "wind" if is_wind else "solar",
            "bus": bus,
            "lon": coord[0] if coord else None,
            "lat": coord[1] if coord else None,
            "coord_source": "bus" if coord else "missing",
            "profile": profile,
            "provenance": provenance,
            "stale_reason": stale,
        })
    return rows
