"""
D03: immutable generations, one atomic pointer, adoption, receipts, the
asset_footprints takeover, the legacy-PUT guard and the copy closure.

Layout of a scene-enabled Site (contracts.md §2):

    <project>/sites/<site_id>/scene/current.json          the ONLY commit point
    <project>/sites/<site_id>/scene/generations/<gen>/generation.json

`current.json` = {version, revision, generation, sha256, last_receipt}.
A generation holds the complete Site envelope, its buildings, the
`asset_footprints` entries it owns, its parent and the receipt of the proposal
that produced it. Generations are never rewritten.

Commit protocol (apply and adopt): take the project lock; check the expected
revision; write the generation file, fsync it and its directory; write the
pointer to a temp file, fsync, `os.replace`, fsync the scene directory; release.
`services.atomic_io` is atomic but deliberately not durable (no fsync), so the
scene store does its own fsync here. A crash before the replace leaves the old
pointer and an orphan generation (cleanup may remove it); a crash after it
leaves the new pointer whose generation carries the receipt, so a retry reads
the receipt back instead of applying twice.

The legacy sites.json is never rewritten for an enabled Site and never a second
writer: GET projects enabled Sites from their generation, the legacy PUT is
refused when it would change one (scene_managed_site), and the rename/delete
hooks re-key or drop inside a new generation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import re
import threading
from typing import Any

from services import site_service
from services.site_scene.contract import SceneError

SCENE_DIR = "scene"
POINTER = "current.json"
GENERATIONS = "generations"
GENERATION_FILE = "generation.json"
SCHEMA_VERSION = 1

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def project_lock(project_dir: pathlib.Path) -> threading.RLock:
    """
    The per-project scene mutex: one writer per project directory in this
    process, held across every check and write of an anchor, adoption, apply,
    hook or closure copy. Re-entrant, so a proposal apply can hold it across
    its own checks and the store's commit. The routes check the native project
    edit lock (`_check_project_lock`, 409 `project_locked`) before taking it;
    this mutex orders the writers that pass that check.
    """
    with _locks_guard:
        return _locks.setdefault(str(project_dir.resolve()), threading.RLock())


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def _fsync_dir(path: pathlib.Path) -> None:
    if os.name == "nt":  # Windows cannot open a directory for fsync; os.replace is still atomic.
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _durable_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def scene_dir(project_dir: pathlib.Path, site_id: str) -> pathlib.Path:
    return site_service.site_dir(project_dir, site_id) / SCENE_DIR


def enabled_site_ids(project_dir: pathlib.Path) -> set[str]:
    """The backend's enabled set: a Site is enabled iff its pointer exists. Never a client flag."""
    root = project_dir / site_service.SITES_DIR
    if not root.is_dir():
        return set()
    return {p.parent.parent.name for p in root.glob(f"*/{SCENE_DIR}/{POINTER}")}


# A generation id is a plain directory name this module wrote: never a path.
GENERATION_ID_RE = re.compile(r"^g\d{6}-[0-9a-f]{12}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_pointer(project_dir: pathlib.Path, site_id: str) -> dict[str, Any] | None:
    """
    The pointer, validated on every read: an imported or hand-edited pointer
    whose generation is not a plain id (`../x`, an absolute path, a `/`) is an
    integrity_error, so no read, copy or export ever follows it.
    """
    path = scene_dir(project_dir, site_id) / POINTER
    if not path.exists():
        return None
    try:
        pointer = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        pointer = None
    if not (isinstance(pointer, dict) and pointer.get("version") == SCHEMA_VERSION
            and isinstance(pointer.get("revision"), int) and not isinstance(pointer.get("revision"), bool)
            and isinstance(pointer.get("generation"), str) and GENERATION_ID_RE.match(pointer["generation"])
            and isinstance(pointer.get("sha256"), str) and _SHA256_RE.match(pointer["sha256"])):
        raise SceneError("integrity_error", "This site's saved scene is not readable: it was left as it is")
    return pointer


def _read_generation(project_dir: pathlib.Path, site_id: str, pointer: dict[str, Any]) -> dict[str, Any]:
    path = scene_dir(project_dir, site_id) / GENERATIONS / pointer["generation"] / GENERATION_FILE
    try:
        gen = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SceneError("integrity_error", "This site's saved scene is missing a file: it was left as it is") from None
    if sha256(gen) != pointer["sha256"]:
        raise SceneError("integrity_error", "This site's saved scene does not match its checksum: it was left as it is")
    return gen


def read_scene(project_dir: pathlib.Path, site_id: str) -> dict[str, Any]:
    """GET .../scene: the canonical state, or the explicit unadopted state. Never adopts."""
    pointer = _read_pointer(project_dir, site_id)
    if pointer is None:
        return {"state": "unadopted", "site_id": site_id}
    gen = _read_generation(project_dir, site_id, pointer)
    return {"state": "enabled", "site_id": site_id, "revision": pointer["revision"],
            "generation": pointer["generation"], "site": gen["site"], "buildings": gen["buildings"],
            "asset_footprints": gen["asset_footprints"], "last_receipt": pointer["last_receipt"]}


def legacy_site_digest(doc: dict[str, Any], site_id: str) -> str:
    """The digest adoption checks: the exact legacy Site AND the footprints it would take over."""
    for site in doc.get("sites", []):
        if site.get("id") == site_id:
            return sha256({"site": site, "asset_footprints": _owned_footprints(doc, site)})
    raise SceneError("not_found", "Site not found")


def _commit(project_dir: pathlib.Path, site_id: str, gen: dict[str, Any], revision: int,
            receipt: dict[str, Any] | None, crash: str | None) -> dict[str, Any]:
    digest = sha256(gen)
    gen_id = f"g{revision:06d}-{digest[:12]}"
    sdir = scene_dir(project_dir, site_id)
    _durable_write(sdir / GENERATIONS / gen_id / GENERATION_FILE, canonical(gen))
    if crash == "before_commit":
        raise OSError("injected crash before the pointer commit")
    pointer = {"version": SCHEMA_VERSION, "revision": revision, "generation": gen_id,
               "sha256": digest, "last_receipt": receipt}
    _durable_write(sdir / POINTER, canonical(pointer))
    if crash == "after_commit":
        raise OSError("injected crash after the pointer commit")
    return pointer


def _owned_footprints(doc: dict[str, Any], site: dict[str, Any]) -> dict[str, Any]:
    """The asset_footprints entries of the equipment placed on this Site."""
    fps = doc.get("asset_footprints") or {}
    return {k: copy.deepcopy(v) for k, v in fps.items() if k in (site.get("placements") or {})}


def adopt(project_dir: pathlib.Path, site_id: str, expected_legacy_digest: str,
          *, crash: str | None = None) -> dict[str, Any]:
    """Explicit adoption: snapshot the exact legacy envelope/placements/footprints into generation 1."""
    with project_lock(project_dir):
        if _read_pointer(project_dir, site_id) is not None:
            raise SceneError("scene_managed_site", "This site already has a 3D scene")
        doc = site_service.read_sites(project_dir)
        if legacy_site_digest(doc, site_id) != expected_legacy_digest:
            raise SceneError("revision_conflict", "This site changed since you opened it: reload it and try again")
        site = copy.deepcopy(next(s for s in doc["sites"] if s["id"] == site_id))
        gen = {"version": SCHEMA_VERSION, "parent": None, "site": site, "buildings": [],
               "asset_footprints": _owned_footprints(doc, site), "receipt": None}
        return _commit(project_dir, site_id, gen, 1, None, crash)


def _chain_receipt(project_dir: pathlib.Path, site_id: str, pointer: dict[str, Any],
                   proposal_id: str) -> dict[str, Any] | None:
    """The committed receipt of `proposal_id` anywhere in the pointed chain, else None."""
    gen_id: str | None = pointer["generation"]
    while gen_id and GENERATION_ID_RE.match(gen_id):
        path = scene_dir(project_dir, site_id) / GENERATIONS / gen_id / GENERATION_FILE
        if not path.exists():
            # A copy carries only the pointed generation (the closure), so its
            # history ends there: older receipts belong to the source project.
            return None
        gen = json.loads(path.read_text(encoding="utf-8"))
        receipt = gen.get("receipt")
        if receipt and receipt["proposal_id"] == proposal_id:
            return receipt
        gen_id = gen.get("parent")
    return None


APPLY_KEYS = frozenset({"buildings", "placements", "asset_footprints"})


def apply(project_dir: pathlib.Path, site_id: str, proposal: dict[str, Any], expected_revision: int,
          *, crash: str | None = None) -> dict[str, Any]:
    """
    POST .../proposals/{id}/apply. `proposal` = {id, base_revision, input_hash, changes}.

    Returns the receipt. Re-applying the same proposal returns its committed
    receipt (no second write); the same id with a different input_hash is
    refused. Any stale expected revision is a revision_conflict.
    """
    changes = proposal.get("changes") or {}
    if set(changes) - APPLY_KEYS:
        raise SceneError("invalid_input", "A proposal may only change buildings, placements and asset footprints")
    with project_lock(project_dir):
        pointer = _read_pointer(project_dir, site_id)
        if pointer is None:
            raise SceneError("not_found", "This site has no 3D scene yet: create it first")
        prior = _chain_receipt(project_dir, site_id, pointer, proposal["id"])
        if prior is not None:
            if prior["input_hash"] != proposal["input_hash"]:
                raise SceneError("invalid_input", "This proposal was already applied with different inputs: generate it again")
            return prior
        if expected_revision != pointer["revision"] or proposal.get("base_revision") != pointer["revision"]:
            raise SceneError("revision_conflict", "This site changed since you opened it: reload it and try again",
                             {"current_revision": pointer["revision"]})
        gen = copy.deepcopy(_read_generation(project_dir, site_id, pointer))
        if "buildings" in changes:
            gen["buildings"] = copy.deepcopy(changes["buildings"])
        if "placements" in changes:
            gen["site"]["placements"] = copy.deepcopy(changes["placements"])
        if "asset_footprints" in changes:
            gen["asset_footprints"] = copy.deepcopy(changes["asset_footprints"])
        revision = pointer["revision"] + 1
        receipt = {"proposal_id": proposal["id"], "input_hash": proposal["input_hash"],
                   "revision": revision, "changed": sorted(changes)}
        gen.update(parent=pointer["generation"], receipt=receipt)
        _commit(project_dir, site_id, gen, revision, receipt, crash)
        return receipt


# ── the legacy sites.json seam ──────────────────────────────────────────────

def project_sites(project_dir: pathlib.Path) -> dict[str, Any]:
    """GET /sites: enabled Sites and their owned footprints come from the generation; one shape either way."""
    doc = site_service.read_sites(project_dir)
    fps = dict(doc.get("asset_footprints") or {})
    sites = []
    for site in doc["sites"]:
        state = read_scene(project_dir, site["id"])
        if state["state"] == "enabled":
            sites.append(state["site"])
            fps.update(state["asset_footprints"])
        else:
            sites.append(site)
    out = {**doc, "sites": sites}
    if fps or "asset_footprints" in doc:
        out["asset_footprints"] = fps
    return out


def guard_legacy_put(project_dir: pathlib.Path, incoming: dict[str, Any]) -> None:
    """
    Refuse a whole-document PUT that would change or delete an enabled Site,
    or change/drop an asset_footprints entry an enabled Site's generation owns.
    The enabled set is the backend's; any client `scene_enabled` flag is ignored.
    """
    enabled = enabled_site_ids(project_dir)
    if not enabled:
        return
    current = project_sites(project_dir)
    by_id = {s["id"]: s for s in incoming.get("sites", []) if isinstance(s, dict)}
    in_fps = incoming.get("asset_footprints") or {}
    for site_id in sorted(enabled):
        state = read_scene(project_dir, site_id)
        mine = next(s for s in current["sites"] if s["id"] == site_id)
        theirs = {k: v for k, v in by_id.get(site_id, {}).items() if k != "scene_enabled"}
        if theirs != mine:
            raise SceneError("scene_managed_site",
                             "This site’s boundary and equipment positions are managed by its 3D scene: correct buildings in Generate",
                             {"site_id": site_id})
        for key, value in state["asset_footprints"].items():
            if in_fps.get(key) != value:
                raise SceneError("scene_managed_site",
                                 "An equipment footprint here is managed by its site’s 3D scene: correct buildings in Generate",
                                 {"site_id": site_id, "key": key})


def rename_component(project_dir: pathlib.Path, component_class: str, old: str, new: str) -> list[str]:
    """The rename hook for enabled Sites: re-key placements/footprints in a NEW generation."""
    old_key, new_key = f"{component_class}:{old}", f"{component_class}:{new}"
    with project_lock(project_dir):      # read and apply as one step: no apply can slip between
        touched = []
        for site_id in sorted(enabled_site_ids(project_dir)):
            state = read_scene(project_dir, site_id)
            placements, fps = state["site"].get("placements") or {}, state["asset_footprints"]
            if old_key not in placements and old_key not in fps:
                continue
            changes = {
                "placements": {(new_key if k == old_key else k): v for k, v in placements.items()},
                "asset_footprints": {(new_key if k == old_key else k): v for k, v in fps.items()},
            }
            pid = f"rename:{old_key}->{new_key}@{state['revision']}"
            apply(project_dir, site_id, {"id": pid, "base_revision": state["revision"],
                                         "input_hash": sha256(changes), "changes": changes}, state["revision"])
            touched.append(site_id)
        return touched


def remove_component(project_dir: pathlib.Path, component_class: str, name: str) -> list[str]:
    """The delete hook for enabled Sites: drop the key in a NEW generation."""
    key = f"{component_class}:{name}"
    with project_lock(project_dir):      # read and apply as one step: no apply can slip between
        touched = []
        for site_id in sorted(enabled_site_ids(project_dir)):
            state = read_scene(project_dir, site_id)
            placements, fps = state["site"].get("placements") or {}, state["asset_footprints"]
            if key not in placements and key not in fps:
                continue
            changes = {"placements": {k: v for k, v in placements.items() if k != key},
                       "asset_footprints": {k: v for k, v in fps.items() if k != key}}
            pid = f"remove:{key}@{state['revision']}"
            apply(project_dir, site_id, {"id": pid, "base_revision": state["revision"],
                                         "input_hash": sha256(changes), "changes": changes}, state["revision"])
            touched.append(site_id)
        return touched


# ── copy / snapshot / export closure ────────────────────────────────────────

def _closure_locked(project_dir: pathlib.Path, site_id: str) -> list[pathlib.Path]:
    pointer = _read_pointer(project_dir, site_id)
    if pointer is None:
        raise SceneError("not_found", "This site has no 3D scene yet: create it first")
    _read_generation(project_dir, site_id, pointer)
    sdir = scene_dir(project_dir, site_id).relative_to(project_dir)
    return [sdir / POINTER, sdir / GENERATIONS / pointer["generation"] / GENERATION_FILE]


def closure(project_dir: pathlib.Path, site_id: str) -> list[pathlib.Path]:
    """
    Relative paths a copy of an enabled Site may carry: its pointer and the one
    pointed generation. Orphan, superseded, failed and cache files under the
    same directory are excluded. A missing or corrupt pointed generation raises
    integrity_error; there is no fallback to copying the tree.
    """
    with project_lock(project_dir):
        return _closure_locked(project_dir, site_id)


def copy_closure(src: pathlib.Path, dst: pathlib.Path, site_id: str) -> list[pathlib.Path]:
    """Copy the closure under the source's project lock, so a concurrent apply cannot mix two generations."""
    with project_lock(src):
        paths = _closure_locked(src, site_id)
        blobs = [(rel, (src / rel).read_bytes()) for rel in paths]
    for rel, data in blobs:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_bytes(data)
    return paths


def closure_blobs(project_dir: pathlib.Path) -> list[tuple[pathlib.Path, bytes]]:
    """
    Every enabled Site's closure, as (path relative to the project, bytes),
    read under the scene mutex so a concurrent apply cannot mix generations.
    Raises integrity_error for a missing or corrupt pointed generation BEFORE
    the caller writes anything to a destination.
    """
    with project_lock(project_dir):
        out = []
        for site_id in sorted(enabled_site_ids(project_dir)):
            for rel in _closure_locked(project_dir, site_id):
                out.append((rel, (project_dir / rel).read_bytes()))
        return out


def is_scene_path(rel: pathlib.PurePath) -> bool:
    """Whether a path relative to the project lies inside some Site's scene directory."""
    parts = rel.parts
    return len(parts) >= 3 and parts[0] == site_service.SITES_DIR and parts[2] == SCENE_DIR


def ignore_scene_dirs(sites_root: pathlib.Path):
    """A `shutil.copytree` ignore callback that skips every `sites/<id>/scene` directory."""
    root = str(sites_root.resolve())

    def _ignore(directory: str, names: list[str]) -> set[str]:
        parent = pathlib.Path(directory).resolve()
        return {SCENE_DIR} if SCENE_DIR in names and str(parent.parent) == root else set()
    return _ignore
