"""
Lifecycle identity of a component instance (Site 3D decision D06).

A sidecar that keys data by `<Class>:<name>` (the 3D site's placements and
footprints) cannot tell a component from a later one that reuses its name. This
module gives a component a uid, stored in `n.meta["component_ids"]`, so it
travels with the network through netCDF, undo, saves, Save-As, scenario copies
and imports, and changes in the same step as the network edit, under the same
lock. No sidecar hook is needed for it to stay right.

- A uid is minted lazily, the first time something binds to the component
  (`uid_for`).
- A rename moves it (`rename`).
- Every removal drops it (`drop`, from
  `network_crud.purge_component_side_data`), so a same-name recreation gets a
  fresh uid.

A uid is never re-attached by name. A binding whose uid the network does not
hold (deleted, replaced, or minted after the last save and then discarded) is
lost, never aliased.

Callers hold the network's write lock (`PyPSAService.get_lock()`). This
module is the only writer. A uid is a uuid4, never derived from a name.
"""
from __future__ import annotations

import uuid

_KEY = "component_ids"


def _read(n) -> dict:
    meta = getattr(n, "meta", None)
    root = meta.get(_KEY) if isinstance(meta, dict) else None
    return root if isinstance(root, dict) else {}


def _root(n) -> dict:
    if not isinstance(getattr(n, "meta", None), dict):
        n.meta = {}
    if not isinstance(n.meta.get(_KEY), dict):
        n.meta[_KEY] = {}
    return n.meta[_KEY]


def _uids(n, component_class: str) -> dict:
    uids = _read(n).get("uids")
    cls = uids.get(component_class) if isinstance(uids, dict) else None
    return cls if isinstance(cls, dict) else {}


def _exists(n, component_class: str, name: str) -> bool:
    try:
        return name in n.components[component_class].static.index
    except (KeyError, AttributeError):
        return False                       # not a component class this network has


def get(n, component_class: str, name: str) -> str | None:
    """The uid the component holds, or None (never bound, or not in the network)."""
    uid = _uids(n, component_class).get(name)
    return uid if isinstance(uid, str) and _exists(n, component_class, name) else None


def uid_for(n, component_class: str, name: str) -> str | None:
    """The component's uid, minted on first use; None when there is no such component."""
    if not _exists(n, component_class, name):
        return None
    uid = get(n, component_class, name)
    if uid is None:
        uid = str(uuid.uuid4())
        _root(n).setdefault("uids", {}).setdefault(component_class, {})[name] = uid
    return uid


def name_of(n, component_class: str, uid: str) -> str | None:
    """The live component holding `uid`, or None (dropped, replaced, or never in this network)."""
    for name, held in _uids(n, component_class).items():
        if held == uid:
            return name if _exists(n, component_class, name) else None
    return None



def rename(n, component_class: str, old: str, new: str) -> None:
    cls = _uids(n, component_class)
    if old in cls:
        cls[new] = cls.pop(old)


def drop(n, component_class: str, name: str) -> None:
    _uids(n, component_class).pop(name, None)


def forget_all(n) -> None:
    """
    A network rebuilt from another (clustering) holds new components, some
    under old names: no uid may carry across, so every binding reads unbound.
    """
    meta = getattr(n, "meta", None)
    if isinstance(meta, dict):
        meta.pop(_KEY, None)
