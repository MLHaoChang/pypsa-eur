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
- Every removal retires it (`drop`, from
  `network_crud.purge_component_side_data`), so a same-name recreation never
  takes it back.

A uid is `<lineage>-<token>`. The lineage is stamped on a network the first
time it is exported (`ensure_lineage`, from
`PyPSAService.export_network_to_netcdf`, which every save and undo snapshot
goes through). It lets `adopt` restore a uid that was minted after the last
save, and never one from another network's lineage.

This module is the only writer.
"""
from __future__ import annotations

import secrets

_KEY = "component_ids"
MAX_RETIRED = 4096


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


def lineage(n) -> str | None:
    value = _read(n).get("lineage")
    return value if isinstance(value, str) else None


def ensure_lineage(n) -> str:
    """The network's lineage, stamped on first use."""
    value = lineage(n)
    if value is None:
        value = _root(n)["lineage"] = secrets.token_hex(6)
    return value


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
        uid = f"{ensure_lineage(n)}-{secrets.token_hex(8)}"
        _root(n).setdefault("uids", {}).setdefault(component_class, {})[name] = uid
    return uid


def name_of(n, component_class: str, uid: str) -> str | None:
    """The live component holding `uid`, or None (retired, replaced, or never in this network)."""
    for name, held in _uids(n, component_class).items():
        if held == uid:
            return name if _exists(n, component_class, name) else None
    return None


def adopt(n, component_class: str, name: str, uid: str) -> bool:
    """
    Give `name` the uid a binding recorded for it, when the network lost it by
    not being saved after the mint. Only if the component holds no uid, no
    component holds this one, it was never retired, and it is of this
    network's lineage (or the network has none yet: it has never been saved
    since D06, so it is the project's own).
    """
    if not _exists(n, component_class, name) or get(n, component_class, name) is not None:
        return False
    if uid in (_read(n).get("retired") or []) or any(held == uid for held in _uids(n, component_class).values()):
        return False
    own = lineage(n)
    if own is not None and not uid.startswith(f"{own}-"):
        return False
    _root(n).setdefault("uids", {}).setdefault(component_class, {})[name] = uid
    return True


def rename(n, component_class: str, old: str, new: str) -> None:
    cls = _uids(n, component_class)
    if old in cls:
        cls[new] = cls.pop(old)


def drop(n, component_class: str, name: str) -> None:
    cls = _uids(n, component_class)
    uid = cls.pop(name, None)
    if isinstance(uid, str):
        retired = _root(n).setdefault("retired", [])
        retired.append(uid)
        del retired[:-MAX_RETIRED]
