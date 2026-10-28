"""
D01: the confirmed native site anchor.

Source today: no project-level coordinate exists. `/flow`'s
`site_location_missing` (services/flow_service.py) is derived from Bus x/y, and
each legacy Site carries its own `origin` in sites.json. So the anchor is new
state, and a Bus placement is never an anchor (no dummy Bus).

Frozen persistence: one project-level sidecar, `<project>/site_anchor.json`,
written atomically (`services.atomic_io.atomic_write_text`) under the project
lock with an `expected_revision` compare-and-swap. Not a key inside sites.json:
the legacy whole-document `PUT /sites` carries no revision and the client
spreads its cached `...doc` back, so a stale tab would silently revert an
anchor stored there. SC-01 adds the file to `_BUNDLE_FILES` (an anchor is not
private) and mounts GET/PUT `/api/projects/{name}/site-anchor`.

Multiple Sites: one anchor per project, the creation context. Each Site keeps
its own `origin`, fixed when it is created. Moving the anchor later never moves
an existing Site; `/flow` reports `anchor_moved` for a Site whose origin lies
outside `ANCHOR_MOVED_M` of the anchor.
"""
from __future__ import annotations

import json
import math
import pathlib
from typing import Any

from services.atomic_io import atomic_write_text
from tests.site_scene_proto.contract import SceneError

ANCHOR_FILE = "site_anchor.json"
SCHEMA_VERSION = 1
SOURCES = ("manual", "geocode")
ANCHOR_MOVED_M = 1000.0


def _finite(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_anchor(body: Any) -> dict[str, Any]:
    """
    Validate a PUT body `{lat, lng, source, confirmed}`; return the stored anchor.

    Refusals (all `invalid_input`, field named): missing/non-finite values,
    latitude outside ±90, longitude outside ±180, unknown source. A latitude
    out of range whose longitude would be a valid latitude adds
    `hint: "swapped"`: it is refused, never silently swapped. A pair that is in
    range both ways round must be stored with `confirmed: true` before it can
    admit Site creation; `confirmed: false` is kept as a draft and admits
    nothing. An explicit confirmed (0, 0) is valid.
    """
    if not isinstance(body, dict):
        raise SceneError("invalid_input", "anchor must be an object")
    lat, lng = body.get("lat"), body.get("lng")
    for field, v in (("lat", lat), ("lng", lng)):
        if not _finite(v):
            raise SceneError("invalid_input", f"{field} must be a finite number", {"field": field})
    if not -90 <= lat <= 90:
        detail: dict[str, Any] = {"field": "lat"}
        if -90 <= lng <= 90 and -180 <= lat <= 180:
            detail["hint"] = "swapped"
        raise SceneError("invalid_input", "latitude must be between -90 and 90", detail)
    if not -180 <= lng <= 180:
        raise SceneError("invalid_input", "longitude must be between -180 and 180", {"field": "lng"})
    source = body.get("source")
    if source not in SOURCES:
        raise SceneError("invalid_input", f"source must be one of {SOURCES}", {"field": "source"})
    confirmed = body.get("confirmed")
    if not isinstance(confirmed, bool):
        raise SceneError("invalid_input", "confirmed must be true or false", {"field": "confirmed"})
    return {"lat": float(lat), "lng": float(lng), "source": source, "confirmed": confirmed}


def read_anchor(project_dir: pathlib.Path) -> dict[str, Any]:
    """`{version, revision, anchor}`; revision 0 and anchor None when absent."""
    path = project_dir / ANCHOR_FILE
    if not path.exists():
        return {"version": SCHEMA_VERSION, "revision": 0, "anchor": None}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("version") != SCHEMA_VERSION or not isinstance(doc.get("revision"), int):
        raise SceneError("integrity_error", f"{ANCHOR_FILE} is not a version {SCHEMA_VERSION} anchor document")
    return doc


def write_anchor(project_dir: pathlib.Path, body: Any, expected_revision: Any) -> dict[str, Any]:
    """
    Compare-and-swap write. The caller holds the project lock and has checked
    editor access (native ACL); this function enforces the revision.
    """
    anchor = validate_anchor(body) if body is not None else None
    current = read_anchor(project_dir)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
        raise SceneError("invalid_input", "expected_revision is required", {"field": "expected_revision"})
    if expected_revision != current["revision"]:
        raise SceneError("revision_conflict", "the site anchor changed; reload it",
                         {"current_revision": current["revision"]})
    doc = {"version": SCHEMA_VERSION, "revision": current["revision"] + 1, "anchor": anchor}
    atomic_write_text(project_dir / ANCHOR_FILE, json.dumps(doc, sort_keys=True))
    return doc


def admits_creation(doc: dict[str, Any]) -> bool:
    a = doc.get("anchor")
    return bool(a and a.get("confirmed"))
