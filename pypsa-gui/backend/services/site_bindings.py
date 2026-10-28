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
  clustered, or bound after the last save and then discarded. The entry is
  hidden and listed in `unbound_keys`, so a same-name component never
  inherits it. It stays on disk, and a PUT carries it forward, so an undo of
  the delete brings it back. A uid is never re-attached by name: a lost
  binding stays lost.
- **No binding** (a document from before D06, or a key a client just placed).
  A read shows it by name, as before D06. The next PUT binds it: to the live
  component of that name, or, when there is none, to a uid no component holds,
  so a component created later never takes the key by its name.

Reads (`project_view`, `stale_keys`) never write: no uid is minted and the
network is not marked dirty. Only `stamp` (PUT, under the network's write
lock) mints. Only the open project's network can answer, so both run for the
open project only. An enabled Site's placements and the footprints its scene
owns are the scene's to change (D03): they are never hidden or re-keyed here;
one that is stale, or whose instance now has another name, only shows in
`unbound_keys`.
"""
from __future__ import annotations

import copy
import os
import pathlib
import uuid

from services import component_identity, dirty_state


def _keys(doc: dict) -> set[str]:
    keys = set(doc.get("asset_footprints") or {})
    for site in doc.get("sites") or []:
        keys |= set(site.get("placements") or {})
    return keys


def _bindings(doc: dict) -> dict:
    value = doc.get("bindings")
    return dict(value) if isinstance(value, dict) else {}


def _resolved_key(n, key: str, bindings: dict) -> str | None:
    """The key the bound instance has now; None when no live component holds the uid. Never writes."""
    cls = key.partition(":")[0]
    live = component_identity.name_of(n, cls, bindings[key])
    return f"{cls}:{live}" if live is not None else None


def stale_keys(n, keys, bindings: dict) -> set[str]:
    """The bound keys whose instance is gone or now has another name: never this key's component."""
    return {key for key in keys if isinstance(bindings.get(key), str) and _resolved_key(n, key, bindings) != key}


def _move(entries: dict, moves: dict[str, str | None]) -> dict:
    out = {k: v for k, v in entries.items() if k not in moves}
    for key, target in moves.items():
        if key in entries and target is not None and target not in out:
            out[target] = entries[key]
    return out


def project_view(doc: dict, n, enabled: set[str], owned: set[str]) -> dict:
    """GET: the document as the open network sees it, plus `unbound_keys`. Read-only."""
    out = copy.deepcopy(doc)
    bindings = _bindings(out)
    moves = {key: _resolved_key(n, key, bindings) for key in _keys(out) if isinstance(bindings.get(key), str)}
    moves = {key: target for key, target in moves.items() if target != key}
    for site in out.get("sites") or []:
        if site.get("id") not in enabled and isinstance(site.get("placements"), dict):
            site["placements"] = _move(site["placements"], moves)
    if isinstance(out.get("asset_footprints"), dict):
        fps = out["asset_footprints"]
        mine = {k: v for k, v in fps.items() if k not in owned}
        out["asset_footprints"] = {**_move(mine, moves), **{k: v for k, v in fps.items() if k in owned}}
    shown, before = _keys(out), _keys(doc)
    # Unbound: an entry hidden because its instance is gone, or one the scene
    # still shows under a name its instance no longer has.
    unbound = sorted(key for key in moves if moves[key] is None or key in shown)
    # Every entry shown keeps its own binding, so a PUT that echoes it can
    # never re-bind it by name; an entry shown under its new name carries the
    # binding that moved it.
    view = {k: bindings[k] for k in shown if isinstance(bindings.get(k), str)}
    for key, target in moves.items():
        if target is not None and target in shown and target not in before:
            view[target] = bindings[key]
    # Absent rather than empty, so a document with nothing bound reads as before.
    for field, value in (("bindings", view), ("unbound_keys", unbound)):
        if value:
            out[field] = value
        else:
            out.pop(field, None)
    return out


def stamp(incoming: dict, on_disk: dict, n, enabled: set[str]) -> dict:
    """
    PUT, under the network's write lock: the document to write.

    - A key sent without a binding (a new placement, or a pre-D06 key) is
      bound now: to the live component of that name, or to a uid no component
      holds.
    - A stale entry the client never saw (it was hidden) is carried forward
      with its binding, so an undo can restore it. A key the client re-placed
      takes the client's entry.
    - One uid binds one key: a key left under an old name by a failed rename
      hook loses it to the key of the instance's current name, and gets a uid
      no component holds, so it stays unbound.
    """
    out = copy.deepcopy(incoming)
    out.pop("unbound_keys", None)
    bindings = _bindings(out)
    disk_bindings = _bindings(on_disk)
    sent = _keys(out)
    stale = {key for key in _keys(on_disk) - sent
             if isinstance(disk_bindings.get(key), str) and _resolved_key(n, key, disk_bindings) is None}
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
    keys = _keys(out)
    for key in sorted(keys):
        if isinstance(bindings.get(key), str):
            continue
        cls, _, name = key.partition(":")
        first = component_identity.get(n, cls, name) is None
        uid = component_identity.uid_for(n, cls, name)
        if uid is None:
            uid = str(uuid.uuid4())              # held by no component: unbound from the start
        elif first:
            dirty_state.mark_dirty()             # the new uid must reach the saved network
        bindings[key] = uid
    # The key under the instance's current name keeps a shared uid; the others are cut loose.
    holder: set[str] = set()
    for key in sorted(keys, key=lambda k: (_resolved_key(n, k, bindings) != k, k)):
        if bindings[key] in holder:
            bindings[key] = str(uuid.uuid4())
        holder.add(bindings[key])
    out["bindings"] = {k: bindings[k] for k in sorted(keys)}
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
