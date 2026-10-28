"""
D04: native proposals, the previews a user reviews before Apply.

A proposal is computed in the request (native generation is bounded, see
`native`) and kept in this process's memory until it is applied, cancelled or
expires. It is never persisted: the desktop app runs one worker, and a
proposal lost to a restart (or served by another worker) answers the honest
410 `proposal_expired`, so nothing new sits under the project directory or in
its copy closure.

The map is keyed by (project key, proposal id) and owned by the user who
created it: another user or another project gets `proposal_expired`, the same
answer as an unknown id, so a probe learns nothing. It is bounded by count,
by bytes and by age; the oldest entries are dropped first.

Apply never trusts the cached preview alone: under the project's scene lock it
recomputes the input hash from the stored inputs, rebuilds the buildings from
those inputs against the CURRENT Site envelope, and commits only through the
store's compare-and-swap on the scene revision.
"""
from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

from services.site_scene import native, store
from services.site_scene.contract import SceneError
from services.site_scene.verdicts import REASON_NO_BOUNDARY

ACTIONS = ("generate_native", "edit_buildings")
MAX_PROPOSALS = 64
MAX_TOTAL_BYTES = 64 * 1024 * 1024
TTL_SECONDS = 30 * 60

_lock = threading.Lock()
_entries: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()


def clear() -> None:
    """Drop every proposal (a restart, as far as callers can tell)."""
    with _lock:
        _entries.clear()


def _evict(now: float) -> None:
    for key in [k for k, e in _entries.items() if now - e["created"] > TTL_SECONDS]:
        del _entries[key]
    while len(_entries) > MAX_PROPOSALS or sum(e["bytes"] for e in _entries.values()) > MAX_TOTAL_BYTES:
        _entries.popitem(last=False)


def input_hash(site_id: str, action: str, base_revision: int, inputs: dict[str, Any]) -> str:
    return store.sha256({"site_id": site_id, "action": action, "base_revision": base_revision, "inputs": inputs})


def _buildings(action: str, current: list[dict[str, Any]], site: dict[str, Any],
               inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The full building list the proposal would commit, and the preview stats."""
    preview = native.generate(site, inputs)
    if action == "generate_native":
        return preview["buildings"], preview["stats"]
    # edit_buildings: the listed buildings replace those with the same id; the rest stay.
    edited = {b["id"]: b for b in preview["buildings"]}
    merged = [edited.pop(b["id"], b) for b in current] + list(edited.values())
    return merged, preview["stats"]


def _parse(body: Any) -> tuple[str, int, dict[str, Any]]:
    if not isinstance(body, dict):
        raise SceneError("invalid_input", "Proposal: send an object with action, base_revision and inputs")
    action, base, inputs = body.get("action"), body.get("base_revision"), body.get("inputs")
    if action not in ACTIONS:
        raise SceneError("invalid_input", "Proposal: action must be generate_native or edit_buildings",
                         {"field": "action"})
    if not isinstance(base, int) or isinstance(base, bool):
        raise SceneError("invalid_input", "Proposal: base_revision is required", {"field": "base_revision"})
    if not isinstance(inputs, dict) or set(inputs) - {"greenfield", "sources"}:
        raise SceneError("invalid_input", "Proposal: inputs may hold greenfield and sources lists",
                         {"field": "inputs"})
    return action, base, inputs


def create(project_dir, project_key: str, owner: str | None, site_id: str, body: Any) -> dict[str, Any]:
    """POST .../scene/proposals: validate, compute the preview, keep it. Writes nothing."""
    action, base, inputs = _parse(body)
    scene = store.read_scene(project_dir, site_id)
    if scene["state"] != "enabled":
        raise SceneError("not_found", "This site has no 3D scene yet: create it first")
    if base != scene["revision"]:
        raise SceneError("revision_conflict", "This site changed since you opened it: reload it and try again",
                         {"current_revision": scene["revision"]})
    if len(scene["site"].get("boundary") or []) < 3:
        raise SceneError("invalid_input", REASON_NO_BOUNDARY, {"field": "boundary"})
    buildings, stats = _buildings(action, scene["buildings"], scene["site"], inputs)
    pid = f"p-{uuid.uuid4().hex}"
    entry = {"id": pid, "site_id": site_id, "owner": owner, "action": action, "base_revision": base,
             "inputs": copy.deepcopy(inputs), "input_hash": input_hash(site_id, action, base, inputs),
             "status": "ready", "created": time.monotonic(), "bytes": stats["bytes"],
             "preview": {"buildings": buildings, "stats": stats}}
    with _lock:
        _entries[(project_key, pid)] = entry
        _evict(entry["created"])
    return view(entry)


def view(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in ("id", "site_id", "action", "base_revision", "input_hash", "status", "preview")}


def _committed(project_dir, site_id: str, pid: str) -> dict[str, Any] | None:
    """The receipt this site's chain holds for `pid`, if it was committed before the map lost it."""
    with store.project_lock(project_dir):
        pointer = store._read_pointer(project_dir, site_id)
        return None if pointer is None else store._chain_receipt(project_dir, site_id, pointer, pid)


def get(project_key: str, owner: str | None, site_id: str, pid: str) -> dict[str, Any]:
    """The caller's own live proposal, else `proposal_expired`."""
    with _lock:
        _evict(time.monotonic())
        entry = _entries.get((project_key, pid))
    if entry is None or entry["owner"] != owner or entry["site_id"] != site_id:
        raise SceneError("proposal_expired", "This proposal expired: generate the site again")
    return entry


def cancel(project_key: str, owner: str | None, site_id: str, pid: str) -> dict[str, Any]:
    """Idempotent. An applied proposal stays applied; it is never relabelled cancelled."""
    entry = get(project_key, owner, site_id, pid)
    with _lock:
        # An apply in flight ("applying") has claimed the proposal: it is not relabelled.
        if entry["status"] == "ready":
            entry["status"] = "cancelled"
    return view(entry)


def apply(project_dir, project_key: str, owner: str | None, site_id: str, pid: str, body: Any) -> dict[str, Any]:
    """POST .../apply `{expected_revision, input_hash?}` -> the committed receipt."""
    if not isinstance(body, dict):
        raise SceneError("invalid_input", "Apply: send an object with expected_revision")
    expected = body.get("expected_revision")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise SceneError("invalid_input", "Apply: expected_revision is required", {"field": "expected_revision"})
    try:
        entry = get(project_key, owner, site_id, pid)
    except SceneError:
        # A restart or eviction loses the map, not the commit: an apply that
        # already landed reads back its receipt instead of answering 410.
        receipt = _committed(project_dir, site_id, pid)
        if receipt is None:
            raise
        return receipt
    with _lock:
        # Check and claim under one lock, so a concurrent cancel cannot land
        # between the check and the commit.
        if entry["status"] == "cancelled":
            raise SceneError("proposal_cancelled", "This proposal was cancelled: generate the site again")
        claimed = entry["status"] == "ready"
        if claimed:
            entry["status"] = "applying"
    try:
        receipt = _apply_entry(project_dir, site_id, pid, entry, body, expected)
    except BaseException:
        if claimed:
            with _lock:
                entry["status"] = "ready"
        raise
    with _lock:
        entry["status"] = "applied"
    return receipt


def _apply_entry(project_dir, site_id: str, pid: str, entry: dict[str, Any], body: dict[str, Any],
                 expected: int) -> dict[str, Any]:
    stored = input_hash(site_id, entry["action"], entry["base_revision"], entry["inputs"])
    reviewed = body.get("input_hash", entry["input_hash"])
    if stored != entry["input_hash"] or reviewed != entry["input_hash"]:
        raise SceneError("revision_conflict", "This proposal changed since you reviewed it: review it again",
                         {"current_revision": None})
    with store.project_lock(project_dir):
        scene = store.read_scene(project_dir, site_id)
        if scene["state"] != "enabled":
            raise SceneError("not_found", "This site has no 3D scene yet: create it first")
        if scene["last_receipt"] and scene["last_receipt"]["proposal_id"] == pid:
            receipt = scene["last_receipt"]
        elif scene["revision"] != entry["base_revision"]:
            # A receipt further back in the chain is still read back (store.apply); otherwise refuse.
            receipt = store.apply(project_dir, site_id, {"id": pid, "base_revision": entry["base_revision"],
                                                         "input_hash": entry["input_hash"], "changes": {}},
                                  expected)
        else:
            buildings, _ = _buildings(entry["action"], scene["buildings"], scene["site"], entry["inputs"])
            receipt = store.apply(project_dir, site_id, {"id": pid, "base_revision": entry["base_revision"],
                                                         "input_hash": entry["input_hash"],
                                                         "changes": {"buildings": buildings}}, expected)
    return receipt


def has_ready(project_key: str, owner: str | None) -> bool:
    """Whether the caller has a live, unapplied proposal in this project."""
    with _lock:
        _evict(time.monotonic())
        return any(k[0] == project_key and e["owner"] == owner and e["status"] == "ready"
                   for k, e in _entries.items())
