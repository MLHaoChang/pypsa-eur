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
    for site in doc.get("sites", []):
        placements = site.get("placements")
        if isinstance(placements, dict) and old_key in placements:
            placements[new_key] = placements.pop(old_key)
            changed = True
    return changed


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
