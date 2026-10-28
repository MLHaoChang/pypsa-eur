"""
Which component instance a 3D placement or footprint belongs to (Site 3D D06).

`sites.json` keys placements and footprints by `<Class>:<name>`, and a name can
be reused. Its top-level `bindings` map records, for each key, the uid
(`services.component_identity`) of the instance it was authored against. The
open network decides what that uid means now:

- **Bound.** The uid is held by a live component. If that component has been
  renamed since (an undo of a rename, or a rename whose sidecar hook failed),
  the entry is shown under its current name.
- **Stale.** No live component holds the uid: deleted, replaced by an import,
  or never saved. The entry is hidden and listed in `unbound_keys`, so a
  same-name component never inherits it. It stays on disk, and a PUT carries
  it forward, so an undo of the delete brings it back.
- **No binding** (a document from before D06, or a key a client just placed).
  It is bound once, the first time the open network sees it: to the live
  component of that name, or, when there is none, to a uid no component holds.
  A key is never re-bound by name after that.

Only the open project's network can answer, so `project` and `stamp` are
called for the open project only. An enabled Site's placements and the
footprints its scene owns are the scene's to change (D03): they are never
hidden or re-keyed here; one that is stale, or whose instance now has
another name, only shows in `unbound_keys`.
"""
from __future__ import annotations

import copy
import os
import pathlib
import secrets

from services import component_identity, dirty_state


def _keys(doc: dict) -> set[str]:
    keys = set(doc.get("asset_footprints") or {})
    for site in doc.get("sites") or []:
        keys |= set(site.get("placements") or {})
    return keys


def _bind_missing(n, keys, bindings: dict) -> bool:
    """
    Give each key without a binding one, the first time the open network sees
    it: the uid of the live component of that name, or a uid no component
    holds when there is none, so a component created later never takes the
    key by its name. Returns whether one was added.
    """
    added = False
    for key in sorted(keys):
        if isinstance(bindings.get(key), str):
            continue
        cls, _, name = key.partition(":")
        first = component_identity.get(n, cls, name) is None
        uid = component_identity.uid_for(n, cls, name)
        if uid is None:
            uid = secrets.token_hex(12)          # held by no component: unbound from the start
        elif first:
            dirty_state.mark_dirty()             # the new uid must reach the saved network
        bindings[key] = uid
        added = True
    return added


def _where(n, key: str, bindings: dict) -> str | None:
    """The key the entry belongs under now, or None when it is stale or names no component."""
    cls = key.partition(":")[0]
    uid = bindings.get(key)
    if not isinstance(uid, str):
        return None
    live = component_identity.name_of(n, cls, uid)
    return f"{cls}:{live}" if live is not None else None


def stale_keys(n, keys, bindings: dict) -> set[str]:
    """The keys whose bound instance is gone or now has another name: never this key's component."""
    return {key for key in keys if isinstance(bindings.get(key), str) and _where(n, key, bindings) != key}


def _move(entries: dict, moves: dict[str, str | None]) -> dict:
    out = {k: v for k, v in entries.items() if k not in moves}
    for key, target in moves.items():
        if key in entries and target is not None and target not in out:
            out[target] = entries[key]
    return out


def project(doc: dict, n, enabled: set[str], owned: set[str]) -> tuple[dict, dict | None]:
    """
    GET: the document as the open network sees it, plus `unbound_keys`. Also
    returns the bindings to persist when a key had none (else None). Mints a
    uid on a component bound for the first time.
    """
    out = copy.deepcopy(doc)
    bindings = out.get("bindings") if isinstance(out.get("bindings"), dict) else {}
    added = _bind_missing(n, _keys(out), bindings)
    where = {key: _where(n, key, bindings) for key in _keys(out)}
    moves = {key: target for key, target in where.items() if isinstance(bindings.get(key), str) and target != key}
    for site in out.get("sites") or []:
        if site.get("id") not in enabled and isinstance(site.get("placements"), dict):
            site["placements"] = _move(site["placements"], moves)
    if isinstance(out.get("asset_footprints"), dict):
        fps = out["asset_footprints"]
        mine = {k: v for k, v in fps.items() if k not in owned}
        out["asset_footprints"] = {**_move(mine, moves), **{k: v for k, v in fps.items() if k in owned}}
    # Every entry shown keeps its own binding (a stale one the scene still
    # shows too), so a PUT that echoes it can never re-bind it by name; an
    # entry shown under its new name carries the binding that moved it.
    shown, before = _keys(out), _keys(doc)
    # Unbound: an entry hidden because its instance is gone, or one the scene
    # still shows under a name its instance no longer has.
    unbound = sorted(key for key in moves if moves[key] is None or key in shown)
    out["bindings"] = {k: bindings[k] for k in shown if isinstance(bindings.get(k), str)}
    for key, target in where.items():
        if target is not None and target != key and target in shown and target not in before:
            out["bindings"][target] = bindings[key]
    # Absent rather than empty, so a document with nothing to bind reads as before.
    for field, value in (("bindings", out["bindings"]), ("unbound_keys", unbound)):
        if value:
            out[field] = value
        else:
            out.pop(field, None)
    return out, (dict(bindings) if added else None)


def stamp(incoming: dict, on_disk: dict, n, enabled: set[str]) -> dict:
    """
    PUT: the document to write. A key the client sent without a binding is
    bound to the live component of that name. A stale entry the client never
    saw (it was hidden) is carried forward with its binding, so an undo can
    restore it; a key the client re-placed takes the client's entry.
    """
    out = copy.deepcopy(incoming)
    out.pop("unbound_keys", None)
    bindings = out.get("bindings") if isinstance(out.get("bindings"), dict) else {}
    disk_bindings = on_disk.get("bindings") if isinstance(on_disk.get("bindings"), dict) else {}
    sent = _keys(out)
    stale = {key for key in _keys(on_disk) - sent
             if isinstance(disk_bindings.get(key), str) and _where(n, key, disk_bindings) is None}
    by_id = {s.get("id"): s for s in out.get("sites") or [] if isinstance(s, dict)}
    for site in on_disk.get("sites") or []:
        target = by_id.get(site.get("id"))
        if target is None or site.get("id") in enabled:
            continue
        kept = {k: v for k, v in (site.get("placements") or {}).items() if k in stale}
        if kept:
            target["placements"] = {**kept, **(target.get("placements") or {})}
    kept = {k: v for k, v in (on_disk.get("asset_footprints") or {}).items() if k in stale}
    if kept:
        out["asset_footprints"] = {**kept, **(out.get("asset_footprints") or {})}
    for key in stale:
        bindings.setdefault(key, disk_bindings[key])
    _bind_missing(n, _keys(out), bindings)
    keys = _keys(out)
    out["bindings"] = {k: v for k, v in bindings.items() if k in keys}
    if not out["bindings"]:
        del out["bindings"]
    return out


def open_network(project_dir: pathlib.Path):
    """The open network when `project_dir` is the open project, else None (no binding decision is made)."""
    from services import network_lengths
    from services.pypsa_service import PyPSAService

    active = network_lengths.active_project_dir()
    if active is None or os.path.realpath(active) != os.path.realpath(project_dir):
        return None
    return PyPSAService.get_network()


def owned_footprints(project_dir: pathlib.Path, enabled: set[str]) -> set[str]:
    """The footprint keys an enabled Site's scene owns (D03)."""
    from services.site_scene import store as scene_store

    owned: set[str] = set()
    for site_id in enabled:
        owned |= set(scene_store.read_scene(project_dir, site_id).get("asset_footprints") or {})
    return owned
