"""
Branch lengths from the map geometry (plan M2).

A Line's or Link's `length` can come from three places: a number the user
typed, the great-circle chord between its buses, or the geodesic length of
the route drawn for it on the map (`map_layout.json`). This module owns the
geometry-driven rewrite — `POST /api/network/lengths/from_geometry` and the
pieces a bus drag borrows — and records which of the three it was beside the
route, since PyPSA has no column for it.

Length is rewritten because it follows from geometry. Impedance is a
modelling choice and is only PREVIEWED, exactly as a bus drag does today
(`services/network_lines.py`, `_impedance_preview`). Links carry no r/x/b, so
they get a length and no preview.

The project's routes and its "Derive lengths from geometry" setting are read
from the ACTIVE context's storage directory — the same seam the rename hook
uses — so a scratch network (no project) has no geometry to read. Never
imports ``routers.*``.
"""
from __future__ import annotations

import logging
import pathlib

from fastapi import HTTPException

from services import change_log_service, map_layout_service, project_settings
from services.network_geometry import (
    _IMPEDANCE_FIELDS,
    _branch_geometry_km,
    _impedance_preview,
)
from services.pypsa_service import PyPSAService

log = logging.getLogger(__name__)

# The kinds that carry a `length` in PyPSA, and where they live. A transformer
# has a route on the map but no length in the model.
LENGTH_KINDS: dict[str, str] = {"line": "lines", "link": "links"}


def active_project_dir() -> pathlib.Path | None:
    """The active context's storage directory, or None for a scratch network."""
    try:
        storage_dir = getattr(PyPSAService.get_active_context(), "storage_dir", None)
    except Exception:  # noqa: BLE001 — no active context is "no project", not an error
        return None
    return pathlib.Path(storage_dir) if storage_dir else None


def interior_routes(doc: dict) -> dict[str, list]:
    """`<kind>:<name>` → interior `[lng, lat]` waypoints, for the routes that have any."""
    routes = doc.get("routes")
    if not isinstance(routes, dict):
        return {}
    out: dict[str, list] = {}
    for key in routes:
        points = map_layout_service.route_points(doc, key)
        if points:
            out[key] = points
    return out


def geometry_context() -> tuple[pathlib.Path | None, dict[str, list], bool]:
    """
    `(project_dir, routes, derive_enabled)` for the active project — the
    inputs a bus drag needs. Best-effort: a sidecar that cannot be read is
    "no routes", because a drag must never fail on a presentation file.
    """
    project_dir = active_project_dir()
    if project_dir is None:
        return None, {}, False
    try:
        routes = interior_routes(map_layout_service.read_map_layout(project_dir))
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        log.warning("map_layout.json unreadable for a bus drag; using chords: %s", exc)
        routes = {}
    return project_dir, routes, project_settings.derive_lengths_enabled(project_dir)


def record_length_sources(project_dir: pathlib.Path | None, sources: dict[str, str]) -> None:
    """
    Write `route` / `chord` provenance into the project's `map_layout.json`,
    best-effort: provenance is a note beside the model, and a note that could
    not be written must never undo a length that was.
    """
    if not project_dir or not sources:
        return
    try:
        doc = map_layout_service.read_map_layout(project_dir)
        for key, source in sources.items():
            map_layout_service.set_length_source(doc, key, source)
        map_layout_service.write_map_layout(project_dir, doc)
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        log.warning("map_layout.json length provenance not recorded: %s", exc)


def _targets(n, keys: list[str] | None) -> tuple[list[tuple[str, str]], list[dict]]:
    """Resolve `keys` (or every Line and Link) to `(kind, name)` pairs; the rest are skipped with a reason."""
    if keys is None:
        targets = [(kind, str(name)) for kind, attr in LENGTH_KINDS.items() for name in getattr(n, attr).index]
        return targets, []
    targets: list[tuple[str, str]] = []
    skipped: list[dict] = []
    for key in keys:
        kind, sep, name = key.partition(":")
        if not sep or kind not in LENGTH_KINDS:
            skipped.append({"key": key, "reason": "no-length"})
        elif name not in getattr(n, LENGTH_KINDS[kind]).index:
            skipped.append({"key": key, "reason": "unknown"})
        else:
            targets.append((kind, name))
    return targets, skipped


def apply_lengths_from_geometry(keys: list[str] | None) -> dict:
    """
    Rewrite the named branches' lengths (or all Lines and Links when `keys` is
    None) from the map geometry: the route when the branch has one, else the
    chord. Returns `{updated, skipped, rescale, sources}`.

    409 for a scratch network — the routes live in the project's sidecar, so
    there is nothing to measure against until the project is saved.
    """
    project_dir = active_project_dir()
    if project_dir is None:
        raise HTTPException(409, "Save the project first: map routes live in its map_layout.json.")
    try:
        doc = map_layout_service.read_map_layout(project_dir)
    except PermissionError as exc:
        raise HTTPException(503, f"Could not read {map_layout_service.MAP_LAYOUT_FILE}: {exc}") from exc
    routes = interior_routes(doc)

    n = PyPSAService.get_network()
    previews: list[dict] = []
    sources: dict[str, str] = {}
    with PyPSAService.get_lock():
        targets, skipped = _targets(n, keys)
        for kind, name in targets:
            df = getattr(n, LENGTH_KINDS[kind])
            key = f"{kind}:{name}"
            geo = _branch_geometry_km(n, str(df.at[name, "bus0"]), str(df.at[name, "bus1"]), routes.get(key))
            if geo is None:
                skipped.append({"key": key, "reason": "unplaced"})
                continue
            new_km, source = geo
            old_length = float(df.at[name, "length"])
            old = {k: float(df.at[name, k]) for k in _IMPEDANCE_FIELDS} if kind == "line" else None
            df.at[name, "length"] = float(new_km)
            sources[key] = source
            if old is not None:
                p = _impedance_preview(name, old_length, float(new_km), old)
                if p is not None:
                    previews.append(p)

    if sources:
        by_source = {s: sum(1 for v in sources.values() if v == s) for s in ("route", "chord")}
        change_log_service.log(
            "update", "Branches", "(geometry)",
            f"Rewrote {len(sources)} branch length(s) from map geometry: "
            f"{by_source['route']} route, {by_source['chord']} chord",
        )
        record_length_sources(project_dir, sources)
    return {"updated": len(sources), "skipped": skipped, "rescale": previews, "sources": sources}
