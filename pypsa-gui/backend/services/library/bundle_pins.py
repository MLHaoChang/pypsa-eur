"""
A project pins the Library versions it references (Edge Investment Case P1 WP1.1c).

Every `PriceSeriesRef` found anywhere in a project's solver config (the
commercial block from WP1.3 on; flex archetype refs later) is written as
`(id, version, hash)` to the `library_refs.json` sidecar at save. The sidecar
is in `routers.projects._BUNDLE_FILES`, so it travels with a bundle export, a
snapshot and a scenario fork.

`check_pins` re-verifies the pins against the org's Library when a project is
opened, imported or activated. A pin that no longer resolves to exactly
the pinned bytes is reported as a `library_ref_stale` issue. Nothing here ever
moves a ref to another version: the config keeps naming what it named. The
solve itself reads the price already materialised on the network at config
time (`links_t["ic_export_price"]`), i.e. the pinned bytes; re-applying the
config re-resolves through `series_store.resolve`, which refuses a mismatch.

Issue reasons:

  * `missing`            no such (id, version) in this org's Library.
  * `changed`            the Library row's hash differs from the pin.
  * `payload_unreadable` the row matches but its file is gone or altered.
  * `unpinned`           the config names a ref the sidecar does not pin.
  * `sidecar_unreadable` the sidecar itself is not valid JSON of this shape.

Pure service: imports neither routers nor `solver_service` (plan gate rubric).
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session as DBSession

from models.commercial import PriceSeriesRef
from services.atomic_io import atomic_write_text
from services.library import series_store as S

SIDECAR_NAME = "library_refs.json"
SCHEMA = 1
ISSUE_CODE = "library_ref_stale"

_REF_KEYS = frozenset({"id", "version", "hash", "source"})


def collect_refs(obj: Any) -> list[PriceSeriesRef]:
    """Every valid `PriceSeriesRef`-shaped dict nested in `obj`, deduped on
    (id, version, hash), sorted by (id, version)."""
    found: dict[tuple, PriceSeriesRef] = {}

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if _REF_KEYS <= x.keys():
                try:
                    ref = PriceSeriesRef.model_validate(x)
                except ValidationError:
                    ref = None
                if ref is not None:
                    found.setdefault((ref.id, ref.version, ref.hash), ref)
                    return
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(obj)
    return sorted(found.values(), key=lambda r: (r.id, r.version, r.hash))


def _pin(ref: PriceSeriesRef) -> dict:
    return {"id": ref.id, "version": ref.version, "hash": ref.hash}


def write_pins(project_dir: Path, refs: Iterable[PriceSeriesRef]) -> None:
    """Write the sidecar atomically; remove it when there is nothing to pin, so
    a project that dropped its last ref does not keep reporting a stale one."""
    pins = sorted({(r.id, r.version, r.hash) for r in refs})
    target = Path(project_dir) / SIDECAR_NAME
    if not pins:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return
    body = json.dumps({"schema": SCHEMA,
                       "refs": [{"id": i, "version": v, "hash": h} for i, v, h in pins]},
                      indent=2)
    # The house writer (`<name>.tmp` + replace): an interrupted write leaves an
    # orphan `storage_reconcile.sweep_tmp` knows how to clean up.
    atomic_write_text(target, body)


def _issue(reason: str, pin: dict | None, message: str) -> dict:
    pin = pin or {}
    return {"code": ISSUE_CODE, "reason": reason, "id": pin.get("id"),
            "version": pin.get("version"), "hash": pin.get("hash"), "message": message}


def _strict_pin(r: Any) -> dict:
    """A pin exactly as written, or ValueError: a hand-edited `2.7` must not
    quietly become version 2 and check a different item."""
    if not isinstance(r, dict):
        raise ValueError("pin is not an object")
    i, v, h = r.get("id"), r.get("version"), r.get("hash")
    if not isinstance(i, str) or not i:
        raise ValueError("pin id must be a non-empty string")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError("pin version must be an integer >= 1")
    if not isinstance(h, str) or len(h) < 8:
        raise ValueError("pin hash must be a string")
    return {"id": i, "version": v, "hash": h}


def read_pins(project_dir: Path) -> tuple[list[dict], list[dict]]:
    """(pins, issues). An absent sidecar is no pins and no issue."""
    path = Path(project_dir) / SIDECAR_NAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], []
    try:
        data = json.loads(raw)
        refs = data["refs"]
        if data.get("schema") != SCHEMA or not isinstance(refs, list):
            raise ValueError("unexpected shape")
        pins = [_strict_pin(r) for r in refs]
    except (ValueError, KeyError, TypeError) as exc:
        return [], [_issue("sidecar_unreadable", None,
                           f"{SIDECAR_NAME} is unreadable ({type(exc).__name__}); "
                           "the project's Library pins cannot be checked")]
    return pins, []


def check_pins(db: DBSession, org_id: UUID, project_dir: Path | None, *, config: Any,
               root: Path | None = None) -> list[dict]:
    """Issues for every pin (and every config ref) that does not resolve to
    exactly its pinned bytes in `org_id`'s Library. Empty list = all good.

    `project_dir=None` checks the config's refs alone (a resident context,
    whose in-memory config may be ahead of the sidecar on disk)."""
    pins, issues = read_pins(project_dir) if project_dir is not None else ([], [])
    if project_dir is None:
        pins = [_pin(r) for r in collect_refs(config)]
    pinned = {(p["id"], p["version"], p["hash"]) for p in pins}
    to_check = list(pins)
    for ref in collect_refs(config):
        key = (ref.id, ref.version, ref.hash)
        if key in pinned:
            continue
        pinned.add(key)
        to_check.append(_pin(ref))
        if not any(i["reason"] == "sidecar_unreadable" for i in issues):
            issues.append(_issue("unpinned", _pin(ref),
                                 f"the config references {ref.id!r} v{ref.version}, "
                                 f"which {SIDECAR_NAME} does not pin"))
    for pin in to_check:
        label = f"{pin['id']!r} v{pin['version']}"
        current = S.ref_for(db, org_id, pin["id"], pin["version"])
        if current is None:
            issues.append(_issue("missing", pin,
                                 f"Library series {label} is not in this organization's Library"))
            continue
        if current.hash != pin["hash"]:
            issues.append(_issue("changed", pin,
                                 f"Library series {label} no longer has the pinned content"))
            continue
        try:
            S.resolve(db, org_id, PriceSeriesRef(**pin, source=current.source), root=root)
        except (S.LibraryRefStale, S.LibraryRefNotFound) as exc:
            issues.append(_issue("payload_unreadable", pin, str(exc)))
        except Exception as exc:  # noqa: BLE001 — this check must never break an open
            issues.append(_issue("payload_unreadable", pin,
                                 f"Library series {label} could not be read "
                                 f"({type(exc).__name__})"))
    return issues
