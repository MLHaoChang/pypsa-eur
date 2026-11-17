"""
Issue 06 slice (b): the reviewed return of a Blender job.

A `blender_return` proposal reads one stored artifact (`blender_job`) and
compares its returned GLB with the native export it was made from:

* The export base must be exact. The artifact's meta must name this site, and
  the current scene must still be at the meta's revision, generation and
  generation sha256. The export rebuilt from the current scene must hash to
  the meta's `export_glb_sha256`. Anything else is a stale return
  (`revision_conflict`); nothing is guessed or rebased.
* The returned GLB is untrusted (`glb_read`, GK B R1). Only a node's
  `extras.feature_id` is read from its extras. A node is matched to a building
  only by that id and only when the id is a current building's.
* A matched building whose mesh is still its exported prism, in its own frame,
  may come back moved and turned about the vertical. That rigid move is the
  one change a return proposes: the building's footprint is moved and turned
  the same way. Its height, source, evidence and every other field stay as
  they are.
* Everything else is a named unknown and is not applied:
  - a node with no id or an unknown id (reference-only geometry, never
    rebound);
  - an equipment node (placements are model-owned);
  - a duplicated id;
  - a tilted or lifted node;
  - an edited mesh (`height_changed` when only the roof moved);
  - a building the return no longer holds.
  The building stays as it is.

Extras never reach the model: nothing here opens the network, and the only
change Apply can commit is the buildings list (`proposals._apply_entry`).
"""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any

from services.site_scene import blender_job, export, glb_read, native
from services.site_scene.contract import SceneError

TOLERANCE_M = 1e-3
TILT_TOLERANCE = 1e-4
MAX_LISTED_UNKNOWNS = 200

STALE = "This Blender result was made from an earlier version of the scene: run the job again"


def _key(p) -> tuple[int, int, int]:
    return tuple(round(c / TOLERANCE_M) for c in p)


def _stale(field: str) -> SceneError:
    return SceneError("revision_conflict", STALE, {"current_revision": None, "field": field})


def check_base(meta: dict[str, Any], snap: dict[str, Any], scene: dict[str, Any]) -> bytes:
    """The export GLB of the current scene, when the meta names it exactly; otherwise a stale return."""
    for field in ("revision", "generation", "generation_sha256"):
        if meta.get(field) != snap[field]:
            raise _stale(field)
    if snap["revision"] != scene["revision"]:
        raise _stale("revision")
    glb, _manifest = export.build(snap)
    if meta.get("export_glb_sha256") != hashlib.sha256(glb).hexdigest():
        raise _stale("export_glb_sha256")
    return glb


def _shape(features: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for f in features:
        keys = {_key(p) for p in f["positions"]}
        out[f["id"]] = {"kind": f["kind"], "keys": keys}
    return out


def _mesh_reason(node: glb_read.Node, want: set) -> str | None:
    if node.positions is None:
        return "no_mesh"
    got = {_key(p) for p in node.positions}
    if got == want:
        return None
    # The same footprint at a different roof height: named, not applied.
    flat = {(x, z) for x, _, z in want}
    if {(x, z) for x, _, z in got} == flat and len({y for _, y, _ in got}) == 2:
        return "height_changed"
    return "mesh_edited"


def _yaw_deg(q) -> float:
    deg = math.degrees(2 * math.atan2(q[1], q[3]))
    return (deg + 180.0) % 360.0 - 180.0


def compare(scene: dict[str, Any], features: list[dict[str, Any]],
            nodes: list[glb_read.Node]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The building list a return would commit, and its review: changes, unknowns, reference-only count."""
    shapes = _shape(features)
    pivots = {f["id"]: f["translation"] for f in features}
    by_id: dict[str, list[glb_read.Node]] = {}
    unknowns: list[dict[str, Any]] = []
    reference_only = 0
    for n in nodes:
        if n.feature_id is None:
            if n.positions is not None:
                reference_only += 1
            continue
        if n.feature_id not in shapes:
            reference_only += 1
            unknowns.append({"id": None, "reason": "unknown_feature_id"})
            continue
        by_id.setdefault(n.feature_id, []).append(n)
    changes, buildings = [], []
    for b in scene["buildings"]:
        bid = b["id"]
        found = by_id.pop(bid, [])
        if bid not in shapes:
            buildings.append(b)
            continue
        reason = None
        if not found:
            reason = "missing_in_return"
        elif len(found) > 1:
            reason = "duplicate_feature_id"
        else:
            reason = _apply_one(b, shapes[bid]["keys"], pivots[bid], found[0], changes, buildings)
            if reason is None:
                continue
        unknowns.append({"id": bid, "reason": reason})
        buildings.append(b)
    for fid, found in sorted(by_id.items()):
        unknowns.append({"id": fid, "reason": "equipment_not_applied"
                         if shapes[fid]["kind"] == "equipment" else "not_a_building"})
    review = {"changes": changes, "unknowns": unknowns[:MAX_LISTED_UNKNOWNS],
              "unknown_count": len(unknowns), "reference_only": reference_only}
    return buildings, review


def _apply_one(b, want, pivot, node, changes, buildings) -> str | None:
    """Append the moved building and its change row; or return why the node is not applied."""
    q = node.rotation
    if abs(q[0]) > TILT_TOLERANCE or abs(q[2]) > TILT_TOLERANCE:
        return "tilted"
    if abs(node.translation[1] - pivot[1]) > TOLERANCE_M:
        return "lifted"
    reason = _mesh_reason(node, want)
    if reason:
        return reason
    # The pivot in glTF axes is (e, 0, -n); a yaw of theta about +Y turns the
    # Site plane counter-clockwise by theta.
    theta = 2 * math.atan2(q[1], q[3])
    c, s = math.cos(theta), math.sin(theta)
    pe, pn = pivot[0], -pivot[2]
    ne, nn = node.translation[0], -node.translation[2]
    moved = math.hypot(ne - pe, nn - pn)
    turned = _yaw_deg(q)
    if moved <= TOLERANCE_M and abs(turned) < 0.01:
        buildings.append(b)
        return None
    ring = [(ne + c * (x - pe) - s * (y - pn), nn + s * (x - pe) + c * (y - pn)) for x, y in b["footprint"]]
    ring = native.check_footprint(ring, f"Building {b['id']}")
    nb = copy.deepcopy(b)
    nb["footprint"] = [list(p) for p in ring]
    buildings.append(nb)
    changes.append({"id": b["id"], "moved_m": round(moved, 3), "turned_deg": round(turned, 2)})
    return None


def prepare(project_dir, scene: dict[str, Any], artifact_id: str,
            expect_sha256: str | None = None) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """(returned GLB sha256, buildings, review). `expect_sha256` is the reviewed GLB's, rechecked at Apply."""
    snap = export.read_snapshot(project_dir, scene["site_id"])
    data, meta = blender_job.read_return(project_dir, scene["site_id"], artifact_id, glb_read.MAX_RETURN_BYTES)
    sha = hashlib.sha256(data).hexdigest()
    if expect_sha256 is not None and sha != expect_sha256:
        raise SceneError("revision_conflict", "This Blender result changed since you reviewed it: review it again",
                         {"current_revision": None, "field": "artifact"})
    check_base(meta, snap, scene)
    nodes = glb_read.parse(data)
    features, _omissions = export._features(snap)
    buildings, review = compare(scene, features, nodes)
    review["artifact_id"] = artifact_id
    review["export_base"] = {k: meta[k] for k in ("revision", "generation", "generation_sha256",
                                                  "export_glb_sha256")}
    return sha, buildings, review
