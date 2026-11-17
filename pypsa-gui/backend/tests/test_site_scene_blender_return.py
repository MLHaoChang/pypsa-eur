"""
Issue 06 slice (b): a Blender job's return becomes a reviewed proposal.

The artifact is stored the way `blender_job.run_queued` stores it; the returned
GLB is the native export, edited the way a Blender user (or a hostile worker)
would edit it.
"""

# ruff: noqa: F811 - the `geo` fixture is imported, then used as a parameter
from __future__ import annotations

import hashlib
import json
import math
import struct
import uuid

import pytest

from services.site_scene import blender_job, export, proposals
from services.site_scene import blender_worker as bw
from tests.test_site_scene_blender_job import geo  # noqa: F401 - fixture
from tests.test_site_scene_export import _scene_with_buildings
from tests.test_site_scene_routes import BOX, _kind, _propose

BASE = "/api/projects/geo/sites/campus/scene"
HALL = "greenfield:hall"


@pytest.fixture(autouse=True)
def _fresh_map():
    proposals.clear()
    yield
    proposals.clear()


def _split(glb: bytes) -> tuple[dict, bytes]:
    n = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20 : 20 + n])
    rest = glb[20 + n :]
    return doc, rest[8:] if rest else b""


def _join(doc: dict, blob: bytes) -> bytes:
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + (8 + len(blob) if blob else 0)
    out = (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(js), b"JSON")
        + js
    )
    if blob:
        out += struct.pack("<I4s", len(blob), b"BIN\x00") + blob
    return out


def _node(doc, fid):
    return next(
        n for n in doc["nodes"] if (n.get("extras") or {}).get("feature_id") == fid
    )


def _artifact(project, edit=None, meta_edit=None) -> str:
    snap = export.read_snapshot(project, "campus")
    glb, _ = export.build(snap)
    doc, blob = _split(glb)
    if edit:
        out = edit(doc, blob)
        if isinstance(out, tuple):
            doc, blob = out
    meta = {
        "artifact_id": None,
        "site_id": "campus",
        "revision": snap["revision"],
        "generation": snap["generation"],
        "generation_sha256": snap["generation_sha256"],
        "export_glb_sha256": hashlib.sha256(glb).hexdigest(),
        "blender_version": bw.PIN_VERSION,
        "created_at": "2026-11-17T00:00:00Z",
    }
    if meta_edit:
        meta_edit(meta)
    aid = str(uuid.uuid4())
    meta["artifact_id"] = aid
    blender_job._store(project, aid, bw.Result(_join(doc, blob), b"BLEND", ""), meta)
    return aid


def _create(client, aid, base=2):
    return client.post(
        f"{BASE}/proposals",
        json={
            "action": "blender_return",
            "base_revision": base,
            "inputs": {"artifact_id": aid},
        },
    )


def _apply(client, pid, rev=2, **kw):
    return client.post(
        f"{BASE}/proposals/{pid}/apply", json={"expected_revision": rev, **kw}
    )


def _scene(client):
    return client.get(BASE).json()


def _hall(scene):
    return next(b for b in scene["buildings"] if b["id"] == HALL)


def _move(dx=0.0, dz=0.0, deg=0.0):
    def edit(doc, _blob):
        n = _node(doc, HALL)
        t = n["translation"]
        n["translation"] = [t[0] + dx, t[1], t[2] + dz]
        h = math.radians(deg)
        n["rotation"] = [0.0, math.sin(h / 2), 0.0, math.cos(h / 2)]

    return edit


def _model_files(project):
    return {
        p: hashlib.sha256((project / p).read_bytes()).hexdigest()
        for p in ("network.nc", "sites.json")
        if (project / p).exists()
    }


def test_a_moved_and_turned_building_is_a_reviewed_proposal_then_applied(client, geo):
    _scene_with_buildings(client, geo)
    before = _hall(_scene(client))
    aid = _artifact(geo, _move(dx=5.0, dz=-3.0, deg=90.0))
    r = _create(client, aid)
    assert r.status_code == 200, r.text
    prop = r.json()
    review = prop["preview"]["blender_return"]
    assert review["changes"] == [
        {
            "id": HALL,
            "moved_m": pytest.approx(math.hypot(5, 3), abs=1e-3),
            "turned_deg": pytest.approx(90.0, abs=0.01),
            "height_m": {"before": 8, "after": 8},
        }
    ]
    assert review["unknowns"] == [] and review["reference_only"] == []
    assert review["export_base"]["revision"] == 2
    # Nothing is written until Apply.
    assert _scene(client)["revision"] == 2
    r = _apply(client, prop["id"], input_hash=prop["input_hash"])
    assert r.status_code == 200, r.text
    after = _hall(_scene(client))
    # glTF -Z is north: dz=-3 moves the building 3 m north; 90 degrees turns it counter-clockwise.
    cx = sum(p[0] for p in before["footprint"]) / 4
    cy = sum(p[1] for p in before["footprint"]) / 4
    want = sorted(
        (cx + 5 - (y - cy), cy + 3 + (x - cx)) for x, y in before["footprint"]
    )
    got = sorted(tuple(p) for p in after["footprint"])
    assert [c for p in got for c in p] == pytest.approx(
        [c for p in want for c in p], abs=1e-3
    )
    assert {k: v for k, v in after.items() if k != "footprint"} == {
        k: v for k, v in before.items() if k != "footprint"
    }


def test_an_unedited_round_trip_proposes_exactly_nothing(client, geo):
    # G4: the axis mapping round-trips. Two buildings, one turned, so a sign slip shows.
    _scene_with_buildings(client, geo)
    turned = {**BOX, "id": "annex", "center": [40, -15], "heading": 30}
    p = _propose(client, 2, greenfield=(BOX, turned), action="edit_buildings").json()
    assert _apply(client, p["id"]).status_code == 200
    prop = _create(client, _artifact(geo), base=3).json()
    review = prop["preview"]["blender_return"]
    assert review == {**review, "changes": [], "unknowns": [], "reference_only": []}
    assert prop["preview"]["buildings"] == _scene(client)["buildings"]


def test_a_known_move_and_a_30_degree_yaw_come_back_as_exactly_those_values(
    client, geo
):
    _scene_with_buildings(client, geo)
    (row,) = _create(client, _artifact(geo, _move(dx=3.0, dz=4.0, deg=30.0))).json()[
        "preview"
    ]["blender_return"]["changes"]
    assert row == {
        "id": HALL,
        "moved_m": 5.0,
        "turned_deg": 30.0,
        "height_m": {"before": 8, "after": 8},
    }


def test_a_return_made_before_a_later_edit_is_stale(client, geo):
    _scene_with_buildings(client, geo)
    aid = _artifact(geo, _move(dx=1.0))
    p = _propose(client, 2, greenfield=({**BOX, "height": 12},)).json()
    assert _apply(client, p["id"]).status_code == 200
    r = _create(client, aid, base=3)
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")


@pytest.mark.parametrize(
    "field, value",
    [
        ("revision", 1),
        ("generation", "g000009-000000000000"),
        ("generation_sha256", "0" * 64),
        ("export_glb_sha256", "0" * 64),
    ],
)
def test_a_meta_that_does_not_name_the_current_export_is_stale(
    client, geo, field, value
):
    # G6: each part of the base differs alone.
    _scene_with_buildings(client, geo)
    aid = _artifact(geo, meta_edit=lambda m: m.update({field: value}))
    r = _create(client, aid)
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")
    assert r.json()["detail"]["field"] == field


def test_another_sites_artifact_is_not_found(client, geo):
    _scene_with_buildings(client, geo)
    aid = _artifact(geo, meta_edit=lambda m: m.update({"site_id": "other"}))
    r = _create(client, aid)
    assert (r.status_code, _kind(r)) == (404, "not_found")


def test_extras_cannot_reach_the_model_or_the_bindings(client, geo):
    _scene_with_buildings(client, geo)
    model = _model_files(geo)

    def edit(doc, _blob):
        n = _node(doc, HALL)
        n["extras"].update(
            {
                "height_m": 999,
                "p_nom": 1e9,
                "bindings": [{"class": "Generator", "uid": "x"}],
                "kind": "equipment",
            }
        )
        doc["scenes"][0]["extras"]["revision"] = 99

    prop = _create(client, _artifact(geo, edit)).json()
    assert prop["preview"]["blender_return"]["changes"] == []
    assert _apply(client, prop["id"]).status_code == 200
    assert _hall(_scene(client))["height_m"] == 8
    assert _model_files(geo) == model


def test_unmatched_and_edited_nodes_are_named_and_never_applied(client, geo):
    _scene_with_buildings(client, geo)

    def edit(doc, blob):
        hall = _node(doc, HALL)
        doc["nodes"].append(
            {"name": "stray", "mesh": hall["mesh"], "translation": [50, 0, 0]}
        )
        doc["nodes"].append(
            {
                "name": "ghost",
                "mesh": hall["mesh"],
                "extras": {"feature_id": "osm:none"},
            }
        )

    review = _create(client, _artifact(geo, edit)).json()["preview"]["blender_return"]
    # G7: listed by node, never only counted.
    assert [r["name"] for r in review["reference_only"]] == ["stray", "ghost"]
    assert [(u["name"], u["reason"]) for u in review["unknowns"]] == [
        ("ghost", "unknown_feature_id")
    ]
    assert review["changes"] == []


@pytest.mark.parametrize(
    "reason, edit",
    [
        (
            "tilted",
            lambda d, b: _node(d, HALL).update(
                rotation=[math.sin(0.1), 0, 0, math.cos(0.1)]
            ),
        ),
        ("lifted", lambda d, b: _node(d, HALL)["translation"].__setitem__(1, 2.0)),
        ("missing_in_return", lambda d, b: _node(d, HALL)["extras"].pop("feature_id")),
        ("no_mesh", lambda d, b: _node(d, HALL).pop("mesh")),
    ],
)
def test_a_node_that_is_not_a_rigid_move_is_a_named_unknown(client, geo, reason, edit):
    _scene_with_buildings(client, geo)
    before = _scene(client)["buildings"]
    prop = _create(client, _artifact(geo, edit)).json()
    review = prop["preview"]["blender_return"]
    assert {"id": HALL, "reason": reason} in review["unknowns"]
    assert review["changes"] == []
    assert prop["preview"]["buildings"] == before


def _scale_y(scale_all: bool):
    """Scale the hall's vertex Y: all of them (a taller prism) or one vertex only (an edited mesh)."""

    def edit(doc, blob):
        acc = doc["accessors"][
            doc["meshes"][_node(doc, HALL)["mesh"]]["primitives"][0]["attributes"][
                "POSITION"
            ]
        ]
        view = doc["bufferViews"][acc["bufferView"]]
        data = bytearray(blob)
        for k in range(acc["count"]):
            off = view["byteOffset"] + acc.get("byteOffset", 0) + 12 * k
            x, y, z = struct.unpack_from("<3f", data, off)
            if y > 0 and (scale_all or k == acc["count"] - 1):
                struct.pack_into("<3f", data, off, x, y * 2, z)
        acc["max"][1] *= 2 if scale_all else 1
        return doc, bytes(data)

    return edit


def test_an_edited_mesh_is_named_and_not_applied(client, geo):
    _scene_with_buildings(client, geo)
    review = _create(client, _artifact(geo, _scale_y(False))).json()["preview"][
        "blender_return"
    ]
    assert review["unknowns"] == [{"id": HALL, "reason": "mesh_edited"}]


def test_a_taller_prism_is_a_height_change_shown_before_and_after(client, geo):
    _scene_with_buildings(client, geo)
    prop = _create(client, _artifact(geo, _scale_y(True))).json()
    assert prop["preview"]["blender_return"]["changes"] == [
        {
            "id": HALL,
            "moved_m": 0.0,
            "turned_deg": 0.0,
            "height_m": {"before": 8, "after": 16.0},
        }
    ]
    assert _apply(client, prop["id"]).status_code == 200
    assert _hall(_scene(client))["height_m"] == 16.0


def test_a_height_the_editor_would_refuse_is_refused_through_the_return(
    client, geo, monkeypatch
):
    # G5: the same validator as an edit (native's height cap).
    from services.site_scene import native

    _scene_with_buildings(client, geo)
    monkeypatch.setattr(native, "MAX_HEIGHT_M", 10.0)
    r = _create(client, _artifact(geo, _scale_y(True)))
    assert (r.status_code, _kind(r)) == (422, "invalid_geometry")


def test_two_nodes_with_one_id_refuse_the_whole_return(client, geo):
    _scene_with_buildings(client, geo)
    r = _create(
        client, _artifact(geo, lambda d, b: d["nodes"].append(dict(_node(d, HALL))))
    )
    assert (r.status_code, _kind(r)) == (422, "invalid_geometry")
    assert r.json()["detail"]["reason"] == "ambiguous_feature_id"


def test_an_external_uri_in_the_return_is_refused(client, geo):
    _scene_with_buildings(client, geo)
    aid = _artifact(geo, lambda d, b: d["buffers"][0].update(uri="file:///etc/passwd"))
    r = _create(client, aid)
    assert (r.status_code, _kind(r)) == (422, "invalid_geometry")
    assert r.json()["detail"]["reason"] == "external_uri"


def test_a_return_replaced_after_review_is_refused_at_apply(client, geo):
    _scene_with_buildings(client, geo)
    aid = _artifact(geo, _move(dx=2.0))
    prop = _create(client, aid).json()
    glb = geo / blender_job.ARTIFACTS / aid / "scene.glb"
    doc, blob = _split(glb.read_bytes())
    _move(dx=40.0)(doc, blob)
    glb.write_bytes(_join(doc, blob))
    r = _apply(client, prop["id"])
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")
    assert _scene(client)["revision"] == 2


def test_a_concurrent_edit_between_review_and_apply_is_refused(client, geo):
    _scene_with_buildings(client, geo)
    prop = _create(client, _artifact(geo, _move(dx=2.0))).json()
    p = _propose(client, 2, greenfield=({**BOX, "height": 12},)).json()
    assert _apply(client, p["id"]).status_code == 200
    r = _apply(client, prop["id"], rev=3)
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")
    assert _hall(_scene(client))["height_m"] == 12


def test_a_duplicate_apply_and_a_lost_map_read_back_the_one_receipt(client, geo):
    _scene_with_buildings(client, geo)
    prop = _create(client, _artifact(geo, _move(dx=2.0))).json()
    first = _apply(client, prop["id"])
    assert first.status_code == 200
    assert _apply(client, prop["id"]).json() == first.json()
    proposals.clear()  # a restart: the outcome is read back from the chain
    again = _apply(client, prop["id"])
    assert again.status_code == 200 and again.json() == first.json()
    assert first.json()["route_only"] is True
    assert _scene(client)["revision"] == 3


def test_apply_rechecks_the_lock_and_the_owner(
    client,
    other_org_client,
    geo,
    _auth_db,
    seeded_identity,
    second_identity,
    project_row,
):
    from services import project_locks

    _scene_with_buildings(client, geo)
    prop = _create(client, _artifact(geo, _move(dx=2.0))).json()
    assert _apply(other_org_client, prop["id"]).status_code == 404
    _engine, session_local = _auth_db
    with session_local() as db:
        project_locks.release_lock(
            db, project_row("geo").id, seeded_identity["user_id"]
        )
        assert (
            project_locks.acquire_lock(
                db, project_row("geo").id, second_identity["user_id"]
            )
            is not None
        )
    try:
        r = _apply(client, prop["id"])
        assert (r.status_code, _kind(r)) == (409, "project_locked")
        assert _scene(client)["revision"] == 2
    finally:
        with session_local() as db:
            project_locks.release_lock(
                db, project_row("geo").id, second_identity["user_id"]
            )


def test_the_client_cannot_supply_the_reviewed_hash_itself(client, geo):
    _scene_with_buildings(client, geo)
    r = client.post(
        f"{BASE}/proposals",
        json={
            "action": "blender_return",
            "base_revision": 2,
            "inputs": {"artifact_id": _artifact(geo), "glb_sha256": "0" * 64},
        },
    )
    assert (r.status_code, _kind(r)) == (422, "invalid_input")
