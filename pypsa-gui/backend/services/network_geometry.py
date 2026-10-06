"""
Great-circle line lengths, bus coordinates, and the impedance rescale preview.

Extracted verbatim from `routers/network.py`; `routers.network` re-exports
every name so its fifty-plus call sites are untouched. Pure geometry — no
network service, no router state, no HTTP.
"""
from __future__ import annotations

import math
from typing import NamedTuple


_EARTH_KM = 6371.0


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def _bus_coord(n, bus_name: str) -> tuple[float, float] | None:
    if bus_name not in n.buses.index:
        return None
    try:
        x = float(n.buses.at[bus_name, "x"])
        y = float(n.buses.at[bus_name, "y"])
    except Exception:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    # PyPSA's Bus.x / Bus.y default to 0.0, so the exact pair means "never
    # set", not "the Gulf of Guinea". Without this, every line touching an
    # unplaced bus is rewritten to the great-circle distance to Null Island
    # and stored as fact — see tests/test_line_lengths.py and
    # docs/superpowers/specs/2026-07-30-unplaced-buses-map-design.md.
    #
    # BOTH exactly zero: a bus at (0, 51.478) is Greenwich and stays valid.
    if x == 0.0 and y == 0.0:
        return None
    # Mirrors the range check in frontend/src/utils/geo.ts's busLatLng. The
    # frontend hides a bus outside these bounds and counts it as "unplaced"
    # in UnplacedBusesPanel, but until this check _bus_coord had no range
    # check at all — a bus at y == 91 was hidden by the map and reported as
    # unplaced while recalculate_lengths still measured a haversine distance
    # to it and wrote that into n.lines.length. Reachable in practice:
    # PropertiesPanel's Longitude/Latitude fields are unbounded NumInputs and
    # BusCreate.x / BusCreate.y (models/schemas.py) are plain unbounded
    # floats. Do not remove the frontend's check when reading this — both
    # layers must reject out-of-range coordinates.
    if not (-90.0 <= y <= 90.0 and -180.0 <= x <= 180.0):
        return None
    return x, y


def _line_haversine_km(n, bus0: str, bus1: str) -> float | None:
    c0 = _bus_coord(n, bus0)
    c1 = _bus_coord(n, bus1)
    if c0 is None or c1 is None:
        return None
    return _haversine_km(c0[0], c0[1], c1[0], c1[1])


def route_length_km(points_lnglat) -> float:
    """
    Geodesic length of a polyline, haversine per segment. `points_lnglat` is
    GeoJSON order — `[[lng, lat], ...]`, the order `map_layout.json` stores
    (plan M2). Fewer than two points is a length of zero. The frontend twin is
    `utils/geo.ts::routeLengthKm` (in Leaflet's `[lat, lng]`); the two are
    tested against the same geodesics.
    """
    total = 0.0
    for (lng0, lat0), (lng1, lat1) in zip(points_lnglat, points_lnglat[1:]):
        total += _haversine_km(float(lng0), float(lat0), float(lng1), float(lat1))
    return total


def _branch_geometry_km(n, bus0: str, bus1: str, interior_lnglat=None) -> tuple[float, str] | None:
    """
    The branch's length from its geometry: `(km, "route")` through the
    interior waypoints when it has any, else `(km, "chord")` bus0→bus1. None
    when either bus is unplaced — the same guard `_line_haversine_km` has, for
    the same reason (never measure to Null Island).
    """
    c0 = _bus_coord(n, bus0)
    c1 = _bus_coord(n, bus1)
    if c0 is None or c1 is None:
        return None
    if interior_lnglat:
        return route_length_km([list(c0), *interior_lnglat, list(c1)]), "route"
    return _haversine_km(c0[0], c0[1], c1[0], c1[1]), "chord"


_IMPEDANCE_FIELDS = ("r", "x", "b")


def _impedance_preview(
    line_name: str, old_length: float, new_length: float, old: dict[str, float]
) -> dict | None:
    """
    What a per-km-preserving rescale WOULD do. Never mutates.

    Returns None when there is no choice to offer — an all-zero impedance
    scales to zero whatever the length does.

    The relative change is identical for r, x and b (each is multiplied by the
    same length ratio), so one number describes all three.

    `rel_change` is a MAGNITUDE (`abs(ratio - 1.0)`), not a signed delta — a
    shrinking line reports the same positive number as a growing one at the
    same ratio. This is deliberate, not an oversight: downstream, previews
    get partitioned by `rel_change <= <threshold>` to decide what to apply
    WITHOUT asking the user. If this were signed, a line whose length HALVED
    (ratio 0.5, signed change -0.5) would read as -0.5, which is <= any
    positive threshold, and its impedance would be silently halved with no
    prompt — the exact silent rewrite this feature exists to prevent. Keep
    the `abs()`; a shrink must clear the same bar a growth does.
    """
    if all(float(old.get(k, 0.0) or 0.0) == 0.0 for k in _IMPEDANCE_FIELDS):
        return None

    reason: str | None = None
    if not (old_length > 0):
        reason = "old_length<=0"      # per-km undefined — nothing to preserve
    elif not (new_length > 0):
        reason = "new_length<=0"      # would zero the impedance

    if reason is not None:
        new = dict(old)
        rel = 0.0
    else:
        ratio = new_length / old_length
        new = {k: float(old.get(k, 0.0) or 0.0) * ratio for k in _IMPEDANCE_FIELDS}
        # Magnitude, on purpose — see the docstring above. Do NOT drop the
        # abs(): a shrinking line (ratio < 1) must report the same positive
        # rel_change a growing line at the same ratio would, or a threshold
        # comparison downstream lets shrinks slip through unprompted.
        rel = abs(ratio - 1.0)

    return {
        "name": line_name,
        "old_length": float(old_length),
        "new_length": float(new_length),
        "old": {k: float(old.get(k, 0.0) or 0.0) for k in _IMPEDANCE_FIELDS},
        "new": new,
        "rel_change": rel,
        "skipped_reason": reason,
    }


class _RecomputeResult(NamedTuple):
    """
    `_recompute_lengths_for_bus` counts two different things and they are NOT
    interchangeable: `updated` is how many lines actually had `length`
    rewritten (every line that resolved a haversine distance); `previews` is
    the (possibly shorter) list of impedance-rescale offers, which
    `_impedance_preview` omits for an all-zero-impedance line even though its
    length WAS rewritten. A changelog that reports `len(previews)` undercounts
    whenever a zero-impedance line is among the ones touched.

    `sources` is the length PROVENANCE per rewritten branch, keyed by the
    map's `<kind>:<name>` — `"route"` when the length followed a routed
    polyline, `"chord"` when it is the bus0→bus1 great circle — for the
    caller to record in `map_layout.json` (plan M2).
    """

    updated: int
    previews: list[dict]
    sources: dict[str, str]


def _recompute_lengths_for_bus(
    n, bus_name: str, *, routes: dict[str, list] | None = None, include_links: bool = False,
) -> _RecomputeResult:
    """
    Rewrite `length` for every branch touching `bus_name`, and return the
    rewrite count, one preview per LINE whose impedance a per-km-preserving
    rescale would change, and the provenance of each length.

    By default this is the pre-M2 behaviour: Lines only, by chord. With the
    project setting "Derive lengths from geometry" on, the caller passes the
    document's interior `routes` (`<kind>:<name>` → `[[lng, lat], ...]`), so
    a routed branch gets its route length, and `include_links=True`, so Links
    (which carry `length` in PyPSA but no impedance) follow too.

    Length is rewritten here because it follows from geometry. Impedance is a
    modelling choice and is only PREVIEWED — see _impedance_preview and
    POST /lines/rescale_impedances. The caller must hold PyPSAService.get_lock().
    """
    routes = routes or {}
    kinds = (("line", "lines"), ("link", "links")) if include_links else (("line", "lines"),)
    updated = 0
    previews: list[dict] = []
    sources: dict[str, str] = {}
    for kind, attr in kinds:
        df = getattr(n, attr, None)
        if df is None or df.empty:
            continue
        mask = (df["bus0"] == bus_name) | (df["bus1"] == bus_name)
        for name in df.index[mask]:
            key = f"{kind}:{name}"
            geo = _branch_geometry_km(n, str(df.at[name, "bus0"]), str(df.at[name, "bus1"]), routes.get(key))
            if geo is None:
                continue
            d, source = geo
            old_length = float(df.at[name, "length"])
            old = {k: float(df.at[name, k]) for k in _IMPEDANCE_FIELDS} if kind == "line" else None
            df.at[name, "length"] = float(d)
            updated += 1
            sources[key] = source
            if old is not None:
                p = _impedance_preview(str(name), old_length, float(d), old)
                if p is not None:
                    previews.append(p)
    return _RecomputeResult(updated, previews, sources)
