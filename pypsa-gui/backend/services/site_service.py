"""
The `sites.json` sidecar — the 3D site view's own document.

A site groups one or more buses inside a user-drawn boundary and remembers
where each asset was placed. It is presentation state beside the network
model, never inside it, for the reasons the design records
(docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md, D1 and the
rejected `n.meta` alternative). It travels with the bundle through
`_BUNDLE_FILES` / `_BUNDLE_DIRS` in `routers/projects.py`, exactly as
`layout.json` does.

This module is pure over paths and dicts: no FastAPI, no PyPSA, no context.
The routes translate its exceptions; the rename seams call `rename_component`
best-effort.

Site ids are directory names. `<project>/sites/<id>/context.json` caches the
fetched site context, so an id is validated against `SITE_ID_RE` before any
path is joined and the join re-checks containment — the discipline
`_SNAPSHOT_ID_RE` and `_FILE_ID_RE` already follow elsewhere.
"""
from __future__ import annotations

import json
import logging
import math
import os
import pathlib
import re
import shutil
from collections.abc import Callable

from services.atomic_io import atomic_write_text

log = logging.getLogger(__name__)

SITES_FILE = "sites.json"
SITES_DIR = "sites"
MAX_SITES_BYTES = 4 * 1024 * 1024
SITE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SCHEMA_VERSION = 1

# Presentation envelopes only; no cable/line, building-load or bus footprint.
FOOTPRINT_CLASSES = frozenset({"Generator", "StorageUnit", "Store", "Transformer", "Link"})
MAX_FOOTPRINT_DIMENSION_M = 100_000

# The PyPSA classes a placement key may name. `Bus` is included: the site
# view draws a switchyard per bus and the user may move it.
PLACEABLE_CLASSES = frozenset(
    {"Bus", "Generator", "StorageUnit", "Store", "Load", "Transformer", "Line", "Link"}
)


class SitesInvalid(ValueError):
    """The document violates the schema; the message names the offending field."""


class SitesTooLarge(ValueError):
    """The serialised document exceeds `MAX_SITES_BYTES`."""


def empty_document() -> dict:
    return {"version": SCHEMA_VERSION, "sites": []}


# ── read / write ────────────────────────────────────────────────────────────

def read_sites(project_dir: pathlib.Path) -> dict:
    """
    The project's site document, or the empty document when the file is
    missing or unusable.

    Corruption degrades — the 3D view without sites is still a working view,
    and the next write replaces the bad file. A `PermissionError` is NOT
    corruption and propagates: a denial reported as "no sites" would let the
    next save overwrite a perfectly good document the process merely could
    not read (the `/layout` rule, `routers/projects.py::get_layout`).
    """
    path = project_dir / SITES_FILE
    if not path.exists():
        return empty_document()
    try:
        raw = path.read_text(encoding="utf-8")
    except PermissionError:
        raise
    except (OSError, UnicodeDecodeError):
        # A directory named sites.json, an I/O error: unusable, not denied.
        return empty_document()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return empty_document()
    return data if isinstance(data, dict) and isinstance(data.get("sites"), list) else empty_document()


def serialise(doc: dict) -> str:
    """Compact JSON — machine-written, never hand-read; raises `SitesTooLarge`."""
    text = json.dumps(doc, separators=(",", ":"), allow_nan=False)
    if len(text.encode("utf-8")) > MAX_SITES_BYTES:
        raise SitesTooLarge(f"sites document exceeds the {MAX_SITES_BYTES // (1024 * 1024)} MB cap")
    return text


def write_sites(project_dir: pathlib.Path, doc: dict) -> None:
    """Atomic replace of `sites.json`. Validation is the caller's job (the route)."""
    atomic_write_text(project_dir / SITES_FILE, serialise(doc))


# ── validation ──────────────────────────────────────────────────────────────

def _finite(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_vertex(v: object, where: str) -> None:
    if not (isinstance(v, list) and len(v) == 2 and all(_finite(c) for c in v)):
        raise SitesInvalid(f"{where}: boundary vertex must be [lng, lat]")
    lng, lat = v
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise SitesInvalid(f"{where}: boundary vertex out of range")


def _validate_asset_footprints(value: object) -> None:
    if not isinstance(value, dict):
        raise SitesInvalid("asset_footprints must be an object")
    for key, dims in value.items():
        if not isinstance(key, str):
            raise SitesInvalid("asset_footprints key must be a string")
        cls, sep, name = key.partition(":")
        if not sep or not name or cls not in FOOTPRINT_CLASSES:
            raise SitesInvalid("asset_footprints key must be <equipment Class>:<name>")
        if not isinstance(dims, dict):
            raise SitesInvalid(f"asset_footprints[{key!r}] must be dimensions")
        for field in ("width_m", "depth_m", *(["height_m"] if "height_m" in dims else [])):
            v = dims.get(field)
            if not _finite(v) or not 0 < v <= MAX_FOOTPRINT_DIMENSION_M:
                raise SitesInvalid(f"asset_footprints[{key!r}].{field} must be positive finite metres <= {MAX_FOOTPRINT_DIMENSION_M}")


def validate_sites(doc: object) -> None:
    """
    Raise `SitesInvalid` naming the field, else return. Unknown keys are
    allowed at every level (forward compatibility, same posture as the opaque
    layout document); only the keys the backend relies on are checked.
    """
    if not isinstance(doc, dict):
        raise SitesInvalid("document must be an object")
    if doc.get("version") != SCHEMA_VERSION:
        raise SitesInvalid(f"version must be {SCHEMA_VERSION}")
    sites = doc.get("sites")
    if not isinstance(sites, list):
        raise SitesInvalid("sites must be a list")
    if "asset_footprints" in doc:
        _validate_asset_footprints(doc["asset_footprints"])
    seen: set[str] = set()
    for i, site in enumerate(sites):
        where = f"sites[{i}]"
        if not isinstance(site, dict):
            raise SitesInvalid(f"{where}: must be an object")
        sid = site.get("id")
        if not isinstance(sid, str) or not SITE_ID_RE.match(sid):
            raise SitesInvalid(f"{where}.id: must match {SITE_ID_RE.pattern}")
        if sid in seen:
            raise SitesInvalid(f"{where}.id: duplicate id {sid!r}")
        seen.add(sid)
        where = f"site {sid}"
        if not isinstance(site.get("name"), str):
            raise SitesInvalid(f"{where}.name: must be a string")
        buses = site.get("buses")
        if not isinstance(buses, list) or not all(isinstance(b, str) for b in buses):
            raise SitesInvalid(f"{where}.buses: must be a list of bus names")
        boundary = site.get("boundary")
        if not isinstance(boundary, list) or len(boundary) < 3:
            raise SitesInvalid(f"{where}.boundary: needs at least three vertices")
        for v in boundary:
            _check_vertex(v, f"{where}.boundary")
        origin = site.get("origin")
        if not (isinstance(origin, dict) and _finite(origin.get("lng")) and _finite(origin.get("lat"))):
            raise SitesInvalid(f"{where}.origin: must be {{lng, lat}}")
        placements = site.get("placements", {})
        if not isinstance(placements, dict):
            raise SitesInvalid(f"{where}.placements: must be an object")
        for key, p in placements.items():
            cls, sep, name = key.partition(":")
            if not sep or not name or cls not in PLACEABLE_CLASSES:
                raise SitesInvalid(f"{where}.placements: key {key!r} must be <Class>:<name> with a placeable class")
            if not isinstance(p, dict) or not all(_finite(p.get(f)) for f in ("x", "y", "heading")):
                raise SitesInvalid(f"{where}.placements[{key!r}]: x, y, heading must be finite numbers")


# ── rename hook ─────────────────────────────────────────────────────────────

def rename_component(doc: dict, component_class: str, old: str, new: str) -> bool:
    """
    Re-key `<Class>:old` → `<Class>:new` in every site's placements, in
    place. Returns whether anything changed. Other classes with the same
    name are untouched; a missing key is a no-op.
    """
    old_key, new_key = f"{component_class}:{old}", f"{component_class}:{new}"
    changed = False
    footprints = doc.get("asset_footprints", {})
    if isinstance(footprints, dict) and old_key in footprints:
        footprints[new_key] = footprints.pop(old_key)
        changed = True
    for site in doc.get("sites", []):
        placements = site.get("placements")
        if isinstance(placements, dict) and old_key in placements:
            placements[new_key] = placements.pop(old_key)
            changed = True
    return changed


def remove_asset_footprint(doc: dict, component_class: str, name: str) -> bool:
    """A deleted component must not donate its envelope to same-name recreation."""
    footprints = doc.get("asset_footprints", {})
    key = f"{component_class}:{name}"
    if not isinstance(footprints, dict) or key not in footprints:
        return False
    del footprints[key]
    return True


def remove_asset_footprint_on_disk(project_dir: pathlib.Path | str | None, component_class: str, name: str) -> None:
    if not project_dir:
        return
    try:
        pdir = pathlib.Path(project_dir)
        doc = read_sites(pdir)
        if remove_asset_footprint(doc, component_class, name):
            write_sites(pdir, doc)
    except (OSError, ValueError) as exc:
        log.warning("sites.json footprint cleanup failed for %s %r: %s", component_class, name, type(exc).__name__)


def rename_component_on_disk(project_dir: pathlib.Path | str | None, component_class: str, old: str, new: str) -> None:
    """
    Best-effort variant for the CRUD seams: a sidecar problem must never fail
    a rename the model already performed. `None` (a scratch network with no
    storage) is a no-op.
    """
    if not project_dir or old == new:
        return
    try:
        pdir = pathlib.Path(project_dir)
        doc = read_sites(pdir)
        if rename_component(doc, component_class, old, new):
            write_sites(pdir, doc)
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        log.warning("sites.json rename hook failed for %s %r→%r: %s", component_class, old, new, exc)


# ── thumbnail (visual-layers plan 3, S5) ────────────────────────────────────
#
# `<project>/sites/<id>/thumb.png`: the site as the 3D view last captured it
# from the south (once per fresh solve, or on demand). Read by the Guided
# hub-design Site card and offered to the study report as a figure. It is
# a cache of a rendering, never the source of anything: a missing or stale
# file costs a picture, nothing else.

THUMB_FILE = "thumb.png"
MAX_THUMB_BYTES = 2 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ThumbnailInvalid(ValueError):
    """Not a PNG."""


class ThumbnailTooLarge(ValueError):
    """Over `MAX_THUMB_BYTES`."""


def validate_thumbnail(data: bytes) -> None:
    """Raise `ThumbnailTooLarge` or `ThumbnailInvalid`; the route maps them to 413 / 422."""
    if len(data) > MAX_THUMB_BYTES:
        raise ThumbnailTooLarge(f"thumbnail exceeds the {MAX_THUMB_BYTES // (1024 * 1024)} MB cap")
    if not data.startswith(_PNG_MAGIC):
        raise ThumbnailInvalid("thumbnail must be a PNG")


def write_thumbnail(target: pathlib.Path, data: bytes) -> pathlib.Path:
    """Atomic replace of `thumb.png` under a `site_dir` the caller resolved. Validation is the caller's job."""
    from services.atomic_io import atomic_write_bytes

    target.mkdir(parents=True, exist_ok=True)
    path = target / THUMB_FILE
    atomic_write_bytes(path, data)
    return path


def read_thumbnail(target: pathlib.Path) -> bytes | None:
    """The PNG bytes, or None when there is none (or it is unreadable)."""
    path = target / THUMB_FILE
    try:
        return path.read_bytes()
    except OSError:
        return None


def first_thumbnail(project_dir: pathlib.Path) -> bytes | None:
    """
    The thumbnail of the first site in document order that has one — what
    the report takes as its figure. None when no site has a thumbnail or
    the document is unreadable (a sidecar problem never fails a report).
    """
    try:
        doc = read_sites(project_dir)
    except (OSError, PermissionError):
        return None
    for site in doc.get("sites", []):
        sid = site.get("id") if isinstance(site, dict) else None
        try:
            target = site_dir(project_dir, sid)
        except SitesInvalid:
            continue
        data = read_thumbnail(target)
        if data:
            return data
    return None


# ── site directories ────────────────────────────────────────────────────────

def site_dir(project_dir: pathlib.Path, site_id: str) -> pathlib.Path:
    """
    `<project>/sites/<id>`, refusing any id that could leave the project.

    The id is checked against `SITE_ID_RE` first, and that rule alone keeps a
    separator or a dot-segment out. The containment below is the barrier
    anyway, spelled the way `campus_grid_code_service._inside` spells it:
    realpath of the joined path, required to sit under realpath of the sites
    root plus a separator (so a sibling folder sharing the prefix does not
    pass, and a symlinked id pointing out of the tree does not either).
    `Path.resolve().is_relative_to()` says the same thing, but CodeQL does not
    read it as a sanitiser (py/path-injection on #93), and every caller of
    the returned path inherited the alert. The string form is what it reads.
    """
    if not isinstance(site_id, str) or not SITE_ID_RE.match(site_id):
        raise SitesInvalid(f"site id must match {SITE_ID_RE.pattern}")
    root_real = os.path.realpath(project_dir / SITES_DIR)
    full = os.path.realpath(os.path.join(root_real, site_id))
    if not full.startswith(root_real + os.sep):
        raise SitesInvalid("site id resolves outside the project")
    return pathlib.Path(full)


def prune_site_dirs(
    project_dir: pathlib.Path,
    doc: dict,
    rmtree: Callable[[pathlib.Path], None] = shutil.rmtree,
) -> None:
    """
    Remove `sites/<id>/` for every id no longer in the document. Only names
    that pass the id rule are candidates — anything else under `sites/` is
    not ours and is left alone, logged.
    """
    root = project_dir / SITES_DIR
    if not root.is_dir():
        return
    keep = {s.get("id") for s in doc.get("sites", []) if isinstance(s, dict)}
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not SITE_ID_RE.match(child.name):
            log.warning("ignoring unexpected entry under %s: %r", root, child.name)
            continue
        if child.name in keep:
            continue
        try:
            rmtree(child)
        except OSError as exc:
            log.warning("could not remove orphan site dir %s: %s", child, exc)


# ── boundary geometry ───────────────────────────────────────────────────────
# The backend twin of `frontend/src/site3d/boundary.ts` (`centroid`,
# `pointInBoundary`, `busesInside`): a GeoJSON import turns a Polygon feature
# into a site (plan 2, M3), so the server must decide membership and the
# origin the same way the canvas does. Everything metric is computed on the
# tangent plane at the polygon's vertex mean (metres), exactly as the
# frontend does, so the two agree to well under a metre at any site-sized
# polygon. Pure stdlib — this module stays free of shapely on purpose.

_EARTH_RADIUS_M = 6378137.0
_M_PER_DEG_LAT = _EARTH_RADIUS_M * math.pi / 180.0


def _to_local(origin: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
    """(lng, lat) → metres east / north of `origin`, equirectangular at the origin's latitude."""
    per_lng = _M_PER_DEG_LAT * math.cos(math.radians(origin[1]))
    return (p[0] - origin[0]) * per_lng, (p[1] - origin[1]) * _M_PER_DEG_LAT


def _from_local(origin: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
    per_lng = _M_PER_DEG_LAT * math.cos(math.radians(origin[1]))
    return origin[0] + p[0] / per_lng, origin[1] + p[1] / _M_PER_DEG_LAT


def centroid(boundary: list[list[float]]) -> tuple[float, float]:
    """
    Area-weighted centroid of a lon/lat ring, as `(lng, lat)`. A degenerate
    (zero-area) ring falls back to the vertex mean so a site never gets a
    NaN origin — `boundary.ts::centroid`, line for line.
    """
    n = len(boundary)
    mean = (sum(v[0] for v in boundary) / n, sum(v[1] for v in boundary) / n)
    if n < 3:
        return mean
    pts = [_to_local(mean, (v[0], v[1])) for v in boundary]
    twice = cx = cy = 0.0
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        cross = ax * by - bx * ay
        twice += cross
        cx += (ax + bx) * cross
        cy += (ay + by) * cross
    if abs(twice) < 1e-9:
        return mean
    area = twice / 2.0
    return _from_local(mean, (cx / (6.0 * area), cy / (6.0 * area)))


def _on_segment(p, a, b, eps: float = 1e-6) -> bool:
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > eps * max(1.0, math.hypot(b[0] - a[0], b[1] - a[1])):
        return False
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
            and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def _point_in_polygon_local(p, poly) -> bool:
    """Ray casting in local metres; a point on an edge or a vertex is inside."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        a, b = poly[i], poly[j]
        if _on_segment(p, a, b):
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x:
                inside = not inside
        j = i
    return inside


def point_in_boundary(lng: float, lat: float, boundary: list[list[float]]) -> bool:
    """Is the lon/lat point inside (or on) the ring? False for a ring of fewer than three vertices."""
    if len(boundary) < 3:
        return False
    origin = centroid(boundary)
    poly = [_to_local(origin, (v[0], v[1])) for v in boundary]
    return _point_in_polygon_local(_to_local(origin, (lng, lat)), poly)


def buses_inside(buses: dict[str, tuple[float, float]], boundary: list[list[float]]) -> list[str]:
    """
    Names of the PLACED buses (`name → (lng, lat)`; the caller leaves
    unplaced ones out) inside the ring, in the order given.
    """
    if len(boundary) < 3:
        return []
    origin = centroid(boundary)
    poly = [_to_local(origin, (v[0], v[1])) for v in boundary]
    return [name for name, (lng, lat) in buses.items() if _point_in_polygon_local(_to_local(origin, (lng, lat)), poly)]
