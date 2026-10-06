"""
The `map_layout.json` sidecar — the map view's user geometry.

Routed branches and dragged asset bubbles are presentation state beside the
network model, never inside it: PyPSA has no column for a polyline, and a
route must survive a bus drag, a scenario fork and an export. It travels with
the bundle through `_BUNDLE_FILES` in `routers/projects.py`, exactly as
`layout.json` does (plan: docs/superpowers/plans/2026-10-06-visual-layers-2-
map-view.md, M1).

    {
      "version": 1,
      "routes":  {"<kind>:<name>": {"points": [[lng, lat], ...], "source": "user" | "import" | "osm"}},
      "bubbles": {"<bus>|<category>": {"dx": px, "dy": px}}
    }

`kind` is one of the map's edge-kind prefixes — `line`, `link`, `tr` — which
is why `route_key` maps the PyPSA class to it. `points` are the INTERIOR
waypoints of a branch in GeoJSON `[lng, lat]` order; the ends are the two
buses' coordinates and are not stored, so a bus drag keeps the interior
intact and only the chord's ends move. One bend is a valid route, hence the
minimum of one vertex. (The map's Leaflet tuples are `[lat, lng]`; the
frontend store converts at its boundary.) `dx`/`dy` are pixel offsets from
the bus, so a bubble follows its bus at every zoom.

This module is pure over paths and dicts: no FastAPI, no PyPSA, no context.
The routes translate its exceptions; the rename seam calls
`rename_component_on_disk` best-effort. Modelled on `services/site_service.py`.
"""
from __future__ import annotations

import json
import logging
import math
import pathlib

from services.atomic_io import atomic_write_text

log = logging.getLogger(__name__)

MAP_LAYOUT_FILE = "map_layout.json"
# The same cap as `layout.json`; the route passes the router's constant
# through so the two cannot drift (`test_the_cap_is_the_layout_cap`).
MAX_MAP_LAYOUT_BYTES = 4 * 1024 * 1024
SCHEMA_VERSION = 1

ROUTE_KINDS = frozenset({"line", "link", "tr"})
ROUTE_SOURCES = frozenset({"user", "import", "osm"})
# PyPSA class → the map's edge-kind prefix. Only branches have routes.
CLASS_TO_KIND = {"Line": "line", "Link": "link", "Transformer": "tr"}


class MapLayoutInvalid(ValueError):
    """The document violates the schema; the message names the offending field."""


class MapLayoutTooLarge(ValueError):
    """The serialised document exceeds the cap."""


def empty_document() -> dict:
    return {"version": SCHEMA_VERSION, "routes": {}, "bubbles": {}}


# ── read / write ────────────────────────────────────────────────────────────

def read_map_layout(project_dir: pathlib.Path) -> dict:
    """
    The project's map document, or the empty document when the file is
    missing or unusable.

    Corruption degrades — the map without routes is still a working map (the
    chords draw), and the next write replaces the bad file. A `PermissionError`
    is NOT corruption and propagates: a denial reported as "no routes" would
    let the next save overwrite a perfectly good document the process merely
    could not read (the `/layout` rule, `routers/projects.py::get_layout`).
    """
    path = project_dir / MAP_LAYOUT_FILE
    if not path.exists():
        return empty_document()
    try:
        raw = path.read_text(encoding="utf-8")
    except PermissionError:
        raise
    except (OSError, UnicodeDecodeError):
        # A directory named map_layout.json, an I/O error: unusable, not denied.
        return empty_document()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return empty_document()
    if (
        isinstance(data, dict)
        and isinstance(data.get("routes"), dict)
        and isinstance(data.get("bubbles"), dict)
    ):
        return data
    return empty_document()


def serialise(doc: dict, max_bytes: int = MAX_MAP_LAYOUT_BYTES) -> str:
    """Compact JSON — machine-written, never hand-read; raises `MapLayoutTooLarge`."""
    text = json.dumps(doc, separators=(",", ":"), allow_nan=False)
    if len(text.encode("utf-8")) > max_bytes:
        raise MapLayoutTooLarge(
            f"Map layout document exceeds the {max_bytes // (1024 * 1024)} MB cap."
        )
    return text


def write_map_layout(project_dir: pathlib.Path, doc: dict, max_bytes: int = MAX_MAP_LAYOUT_BYTES) -> None:
    """Atomic replace of `map_layout.json`. Validation is the caller's job (the route)."""
    atomic_write_text(project_dir / MAP_LAYOUT_FILE, serialise(doc, max_bytes))


# ── validation ──────────────────────────────────────────────────────────────

def _finite(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_point(v: object, where: str) -> None:
    if not (isinstance(v, list) and len(v) == 2 and all(_finite(c) for c in v)):
        raise MapLayoutInvalid(f"{where}: each point must be [lng, lat] with finite numbers")
    lng, lat = v
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise MapLayoutInvalid(f"{where}: point out of range (lng ±180, lat ±90)")


def validate_map_layout(doc: object) -> None:
    """
    Raise `MapLayoutInvalid` naming the field, else return. Unknown keys are
    allowed at every level (forward compatibility: M2 adds `length_source`
    beside `points`); only the keys the backend relies on are checked.
    """
    if not isinstance(doc, dict):
        raise MapLayoutInvalid("document must be an object")
    if doc.get("version") != SCHEMA_VERSION:
        raise MapLayoutInvalid(f"version must be {SCHEMA_VERSION}")
    routes = doc.get("routes")
    if not isinstance(routes, dict):
        raise MapLayoutInvalid("routes must be an object")
    for key, route in routes.items():
        kind, sep, name = key.partition(":")
        if not sep or not name or kind not in ROUTE_KINDS:
            raise MapLayoutInvalid(
                f"routes: key {key!r} must be <kind>:<name> with kind in {sorted(ROUTE_KINDS)}"
            )
        where = f"routes[{key!r}]"
        if not isinstance(route, dict):
            raise MapLayoutInvalid(f"{where}: must be an object")
        points = route.get("points")
        if not isinstance(points, list) or len(points) < 1:
            raise MapLayoutInvalid(f"{where}.points: needs at least one [lng, lat] waypoint")
        for p in points:
            _check_point(p, f"{where}.points")
        if route.get("source") not in ROUTE_SOURCES:
            raise MapLayoutInvalid(f"{where}.source: must be one of {sorted(ROUTE_SOURCES)}")
    bubbles = doc.get("bubbles")
    if not isinstance(bubbles, dict):
        raise MapLayoutInvalid("bubbles must be an object")
    for key, bubble in bubbles.items():
        # `rpartition`: a bus name may itself contain `|`; the category is
        # whatever follows the LAST separator.
        bus, sep, category = key.rpartition("|")
        if not sep or not bus or not category:
            raise MapLayoutInvalid(f"bubbles: key {key!r} must be <bus>|<category>")
        where = f"bubbles[{key!r}]"
        if not isinstance(bubble, dict) or not all(_finite(bubble.get(f)) for f in ("dx", "dy")):
            raise MapLayoutInvalid(f"{where}: dx, dy must be finite numbers")


# ── rename hook ─────────────────────────────────────────────────────────────

def route_key(component_class: str, name: str) -> str | None:
    """`Line`→`line:<name>`, `Link`→`link:<name>`, `Transformer`→`tr:<name>`; None otherwise."""
    kind = CLASS_TO_KIND.get(component_class)
    return f"{kind}:{name}" if kind else None


def rename_component(doc: dict, component_class: str, old: str, new: str) -> bool:
    """
    Re-key the branch's route in place. Returns whether anything changed. A
    class without routes, or a missing key, is a no-op; other kinds with the
    same name are untouched.
    """
    old_key, new_key = route_key(component_class, old), route_key(component_class, new)
    if old_key is None or new_key is None:
        return False
    routes = doc.get("routes")
    if not isinstance(routes, dict) or old_key not in routes:
        return False
    routes[new_key] = routes.pop(old_key)
    return True


def rename_component_on_disk(project_dir: pathlib.Path | str | None, component_class: str, old: str, new: str) -> None:
    """
    Best-effort variant for the CRUD seam: a sidecar problem must never fail
    a rename the model already performed. `None` (a scratch network with no
    storage) is a no-op, as is a class that has no route.
    """
    if not project_dir or old == new or component_class not in CLASS_TO_KIND:
        return
    try:
        pdir = pathlib.Path(project_dir)
        doc = read_map_layout(pdir)
        if rename_component(doc, component_class, old, new):
            write_map_layout(pdir, doc)
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        log.warning("map_layout.json rename hook failed for %s %r→%r: %s", component_class, old, new, exc)
