"""
SC-01a: the mounted site-scene routes (D01/D02, contracts.md §5) through the
real router: anchor, scene read, adoption, proposals and apply, and the legacy
`/sites` seam for an enabled Site (D03).
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import threading

import pandas as pd
import pypsa
import pytest

from services import site_service

ANCHOR = {"lat": 48.14, "lng": 11.58, "source": "manual", "confirmed": True}
SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]], "placements": {}}
BOX = {"id": "hall", "center": [0, 0], "width": 10, "depth": 20, "height": 8, "heading": 0, "unit": "m"}


def _empty_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    return n


@pytest.fixture
def geo(client, install_network, project_storage_dir):
    """A saved project with an EMPTY network (no Bus): the geometry-first case."""
    install_network(_empty_network(), name="geo")
    assert client.post("/api/projects/geo", params={"force": True, "rebind": True}).status_code == 200
    return project_storage_dir("geo")


def _put_site(client, site=SITE):
    r = client.put("/api/projects/geo/sites", json={"version": 1, "sites": [copy.deepcopy(site)]})
    assert r.status_code == 200, r.text


def _digest(project, site_id="campus"):
    from services.site_scene import store
    return store.legacy_site_digest(site_service.read_sites(project), site_id)


def _adopted(client, project):
    _put_site(client)
    r = client.post("/api/projects/geo/sites/campus/scene/adopt", json={"expected_digest": _digest(project)})
    assert r.status_code == 200, r.text
    return r.json()


def _kind(r):
    return r.json()["detail"]["error_kind"]


# ── D01: the site anchor ────────────────────────────────────────────────────

def test_a_saved_project_has_no_anchor_yet(client, geo):
    r = client.get("/api/projects/geo/site-anchor")
    assert r.status_code == 200
    assert r.json() == {"version": 1, "revision": 0, "anchor": None}


def test_anchor_put_is_compare_and_swap(client, geo):
    r = client.put("/api/projects/geo/site-anchor", json={"anchor": ANCHOR, "expected_revision": 0})
    assert r.status_code == 200, r.text
    assert r.json() == {"version": 1, "revision": 1, "anchor": {**ANCHOR, "lat": 48.14, "lng": 11.58}}
    stale = client.put("/api/projects/geo/site-anchor", json={"anchor": ANCHOR, "expected_revision": 0})
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_kind"] == "revision_conflict"
    assert stale.json()["detail"]["current_revision"] == 1
    assert client.get("/api/projects/geo/site-anchor").json()["revision"] == 1
    # The anchor is a sidecar: no sites.json, no network write, no dummy Bus.
    assert not (geo / site_service.SITES_FILE).exists()
    assert (geo / "site_anchor.json").exists()


@pytest.mark.parametrize("anchor,field,hint", [
    ({**ANCHOR, "lat": float("nan")}, "lat", None),
    ({**ANCHOR, "lat": 91, "lng": 120}, "lat", None),
    ({**ANCHOR, "lat": 91}, "lat", "swapped"),
    ({**ANCHOR, "lat": 120, "lng": 45}, "lat", "swapped"),
    ({**ANCHOR, "lng": -180.5}, "lng", None),
    ({**ANCHOR, "source": "bus"}, "source", None),
    ({**ANCHOR, "confirmed": "yes"}, "confirmed", None),
])
def test_anchor_put_refuses_invalid_input_naming_the_field(client, geo, anchor, field, hint):
    # Raw JSON, so a NaN reaches the server as the browser's JSON.parse never would.
    raw = json.dumps({"anchor": anchor, "expected_revision": 0}, allow_nan=True)
    r = client.put("/api/projects/geo/site-anchor", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert (detail["error_kind"], detail["field"], detail.get("hint")) == ("invalid_input", field, hint)
    assert not (geo / "site_anchor.json").exists()


def test_a_confirmed_zero_zero_anchor_is_valid(client, geo):
    body = {"anchor": {"lat": 0, "lng": 0, "source": "manual", "confirmed": True}, "expected_revision": 0}
    assert client.put("/api/projects/geo/site-anchor", json=body).status_code == 200


def test_another_org_cannot_read_or_write_the_anchor(client, other_org_client, geo):
    assert other_org_client.get("/api/projects/geo/site-anchor").status_code == 404
    r = other_org_client.put("/api/projects/geo/site-anchor", json={"anchor": ANCHOR, "expected_revision": 0})
    assert r.status_code == 404


def test_anchor_and_scene_writes_409_under_a_foreign_lock(client, geo, _auth_db, seeded_identity, second_identity,
                                                          project_row):
    from services import project_locks
    _put_site(client)
    _engine, session_local = _auth_db
    with session_local() as db:
        # The save left the caller holding the lock; hand it to another user.
        project_locks.release_lock(db, project_row("geo").id, seeded_identity["user_id"])
        assert project_locks.acquire_lock(db, project_row("geo").id, second_identity["user_id"]) is not None
    try:
        r = client.put("/api/projects/geo/site-anchor", json={"anchor": ANCHOR, "expected_revision": 0})
        assert (r.status_code, _kind(r)) == (409, "project_locked")
        r = client.post("/api/projects/geo/sites/campus/scene/adopt", json={"expected_digest": _digest(geo)})
        assert (r.status_code, _kind(r)) == (409, "project_locked")
        # Reads are never gated.
        assert client.get("/api/projects/geo/site-anchor").status_code == 200
        assert client.get("/api/projects/geo/sites/campus/scene").status_code == 200
    finally:
        with session_local() as db:
            project_locks.release_lock(db, project_row("geo").id, second_identity["user_id"])
            db.commit()


def test_the_anchor_travels_in_the_bundle():
    from routers.projects import _BUNDLE_FILES
    assert "site_anchor.json" in _BUNDLE_FILES


# ── D03: scene read and adoption ────────────────────────────────────────────

def test_get_scene_never_adopts(client, geo):
    _put_site(client)
    before = (geo / site_service.SITES_FILE).read_bytes()
    r = client.get("/api/projects/geo/sites/campus/scene")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"state", "site_id", "digest"}          # SC-01b: the adoption digest
    assert (body["state"], body["site_id"]) == ("unadopted", "campus")
    assert (geo / site_service.SITES_FILE).read_bytes() == before
    assert not (geo / "sites" / "campus" / "scene").exists()


def test_scene_of_an_unknown_site_is_404(client, geo):
    r = client.get("/api/projects/geo/sites/nope/scene")
    assert (r.status_code, _kind(r)) == (404, "not_found")


def test_adoption_is_explicit_and_digest_checked(client, geo):
    _put_site(client)
    before = (geo / site_service.SITES_FILE).read_bytes()
    r = client.post("/api/projects/geo/sites/campus/scene/adopt", json={"expected_digest": "0" * 64})
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")
    r = client.post("/api/projects/geo/sites/campus/scene/adopt", json={"expected_digest": _digest(geo)})
    assert r.status_code == 200, r.text
    scene = client.get("/api/projects/geo/sites/campus/scene").json()
    assert (scene["state"], scene["revision"], scene["buildings"]) == ("enabled", 1, [])
    assert (geo / site_service.SITES_FILE).read_bytes() == before
    again = client.post("/api/projects/geo/sites/campus/scene/adopt", json={"expected_digest": _digest(geo)})
    assert (again.status_code, _kind(again)) == (409, "scene_managed_site")


# ── proposals and apply on an empty network (V53, V54, V59, V64, V65) ──────

def _propose(client, base_revision, greenfield=(BOX,), action="generate_native"):
    return client.post("/api/projects/geo/sites/campus/scene/proposals",
                       json={"action": action, "base_revision": base_revision,
                             "inputs": {"greenfield": list(greenfield)}})


def test_generate_preview_then_apply_writes_no_network(client, geo):
    _adopted(client, geo)
    nc_before = hashlib.sha256((geo / "network.nc").read_bytes()).hexdigest()
    r = _propose(client, 1)
    assert r.status_code == 200, r.text
    prop = r.json()
    assert prop["status"] == "ready" and prop["base_revision"] == 1
    assert [b["id"] for b in prop["preview"]["buildings"]] == ["greenfield:hall"]
    # A preview persists nothing.
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 1
    assert client.get(f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}").json()["status"] == "ready"

    r = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}/apply",
                    json={"expected_revision": 1})
    assert r.status_code == 200, r.text
    receipt = r.json()
    assert (receipt["revision"], receipt["changed"]) == (2, ["buildings"])
    scene = client.get("/api/projects/geo/sites/campus/scene").json()
    assert scene["revision"] == 2 and scene["buildings"][0]["height_m"] == 8
    assert scene["site"]["buses"] == []
    assert hashlib.sha256((geo / "network.nc").read_bytes()).hexdigest() == nc_before
    # Re-applying returns the committed receipt, with no second write.
    again = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}/apply",
                        json={"expected_revision": 1})
    assert again.status_code == 200 and again.json() == receipt
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 2


def test_a_stale_proposal_is_refused_and_keeps_the_scene(client, geo):
    _adopted(client, geo)
    first, second = _propose(client, 1).json(), _propose(client, 1, [{**BOX, "id": "shed"}]).json()
    assert client.post(f"/api/projects/geo/sites/campus/scene/proposals/{first['id']}/apply",
                       json={"expected_revision": 1}).status_code == 200
    r = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{second['id']}/apply",
                    json={"expected_revision": 1})
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")
    assert r.json()["detail"]["current_revision"] == 2
    scene = client.get("/api/projects/geo/sites/campus/scene").json()
    assert [b["id"] for b in scene["buildings"]] == ["greenfield:hall"]


def test_concurrent_applies_have_one_winner(client, geo):
    _adopted(client, geo)
    props = [_propose(client, 1, [{**BOX, "id": f"b{i}"}]).json()["id"] for i in range(8)]
    barrier = threading.Barrier(8)

    def go(pid):
        barrier.wait(timeout=10)      # all eight leave together: no sleeps
        return client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply",
                           json={"expected_revision": 1})

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(go, props))
    assert sorted(r.status_code for r in results) == [200] + [409] * 7
    for r in results:
        if r.status_code == 409:
            assert r.json()["detail"] == {**r.json()["detail"], "error_kind": "revision_conflict",
                                          "current_revision": 2}
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 2


def test_a_cancelled_proposal_cannot_be_applied(client, geo):
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    assert client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/cancel").status_code == 200
    assert client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/cancel").status_code == 200
    r = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply", json={"expected_revision": 1})
    assert (r.status_code, _kind(r)) == (409, "proposal_cancelled")
    assert r.json()["detail"]["message"] == "This preview was discarded: generate again"


def test_an_unknown_proposal_is_expired(client, geo):
    _adopted(client, geo)
    r = client.post("/api/projects/geo/sites/campus/scene/proposals/p-unknown/apply", json={"expected_revision": 1})
    assert (r.status_code, _kind(r)) == (410, "proposal_expired")


def test_resource_limits_are_413_with_the_numbers(client, geo):
    _adopted(client, geo)
    r = _propose(client, 1, [{**BOX, "id": f"b{i}", "center": [i * 30, 0]} for i in range(2001)])
    assert (r.status_code, _kind(r)) == (413, "resource_limit")
    assert {k: r.json()["detail"][k] for k in ("limit", "max", "actual")} == \
        {"limit": "buildings", "max": 2000, "actual": 2001}


def test_invalid_geometry_is_422(client, geo):
    _adopted(client, geo)
    r = _propose(client, 1, [{**BOX, "height": -1}])
    assert (r.status_code, _kind(r)) == (422, "invalid_geometry")


def test_a_proposal_for_an_unadopted_site_is_refused(client, geo):
    _put_site(client)
    r = _propose(client, 0)
    assert (r.status_code, _kind(r)) == (404, "not_found")


def test_edit_buildings_corrects_one_building(client, geo):
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply", json={"expected_revision": 1})
    taller = {**BOX, "height": 12}
    r = _propose(client, 2, [taller], action="edit_buildings")
    assert r.status_code == 200, r.text
    rc = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{r.json()['id']}/apply",
                     json={"expected_revision": 2})
    assert rc.status_code == 200
    assert client.get("/api/projects/geo/sites/campus/scene").json()["buildings"][0]["height_m"] == 12


# ── the legacy /sites seam for an enabled Site ──────────────────────────────

def test_get_sites_projects_the_enabled_site_from_its_generation(client, geo):
    _adopted(client, geo)
    sites = client.get("/api/projects/geo/sites").json()["sites"]
    assert [s["id"] for s in sites] == ["campus"]


@pytest.mark.parametrize("mutate", [
    lambda d: d["sites"].clear(),
    lambda d: d["sites"][0].update(name="Renamed"),
    lambda d: d["sites"][0].update(placements={"Load:L1": {"x": 1.0, "y": 1.0, "heading": 0}}),
    lambda d: d["sites"][0].update(scene_enabled=False, origin={"lng": 0.0, "lat": 0.0}),
])
def test_a_stale_legacy_put_cannot_change_an_enabled_site(client, geo, mutate):
    _adopted(client, geo)
    doc = client.get("/api/projects/geo/sites").json()
    mutate(doc)
    r = client.put("/api/projects/geo/sites", json=doc)
    assert (r.status_code, _kind(r)) == (409, "scene_managed_site")
    assert (geo / "sites" / "campus" / "scene" / "current.json").exists()
    assert client.get("/api/projects/geo/sites/campus/scene").json()["state"] == "enabled"


def test_an_unchanged_legacy_put_still_saves_other_sites(client, geo):
    _adopted(client, geo)
    doc = client.get("/api/projects/geo/sites").json()
    doc["sites"].append({**copy.deepcopy(SITE), "id": "yard", "name": "Yard"})
    r = client.put("/api/projects/geo/sites", json=doc)
    assert r.status_code == 200, r.text
    assert (geo / "sites" / "campus" / "scene" / "current.json").exists()


# ── GK B conditions: ACL and lock on every route (1) ────────────────────────

WRITES = [
    ("put", "/api/projects/geo/site-anchor", {"anchor": ANCHOR, "expected_revision": 0}),
    ("post", "/api/projects/geo/sites/campus/scene/adopt", {"expected_digest": "0" * 64}),
    ("post", "/api/projects/geo/sites/campus/scene/proposals", {"action": "generate_native", "base_revision": 1,
                                                                "inputs": {"greenfield": [BOX]}}),
    ("post", "/api/projects/geo/sites/campus/scene/proposals/p-x/apply", {"expected_revision": 1}),
    ("post", "/api/projects/geo/sites/campus/scene/proposals/p-x/cancel", None),
]
READS = ["/api/projects/geo/site-anchor", "/api/projects/geo/sites/campus/scene",
         "/api/projects/geo/sites/campus/scene/proposals/p-x"]


def test_a_caller_without_project_access_gets_404_on_every_route(client, other_org_client, geo):
    _adopted(client, geo)
    for path in READS:
        assert other_org_client.get(path).status_code == 404, path
    for method, path, body in WRITES:
        assert getattr(other_org_client, method)(path, json=body).status_code == 404, path
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 1


def test_every_write_route_409s_under_a_foreign_lock_before_any_effect(client, geo, _auth_db, seeded_identity,
                                                                        second_identity, project_row):
    from services import project_locks
    _adopted(client, geo)
    _engine, session_local = _auth_db
    with session_local() as db:
        project_locks.release_lock(db, project_row("geo").id, seeded_identity["user_id"])
        assert project_locks.acquire_lock(db, project_row("geo").id, second_identity["user_id"]) is not None
    try:
        for method, path, body in WRITES:
            r = getattr(client, method)(path, json=body)
            assert (r.status_code, _kind(r)) == (409, "project_locked"), path
        assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 1
        assert not (geo / "site_anchor.json").exists()
        # The /flow verdict shows the project read-only for this caller.
        entry = client.get("/api/projects/geo/flow").json()["scene"]["actions"][0]
        assert entry["blocker"]["code"] == "read_only"
    finally:
        with session_local() as db:
            project_locks.release_lock(db, project_row("geo").id, second_identity["user_id"])


# ── (2) no scene or anchor field reaches a network input ────────────────────

def test_anchor_and_scene_writes_leave_the_network_input_fingerprint(client, geo):
    from services import workflow_tools
    from services.pypsa_service import PyPSAService

    def fingerprint():
        return workflow_tools._network_input_fingerprint(PyPSAService.get_active_context(), exact=True)

    before, nc = fingerprint(), (geo / "network.nc").read_bytes()
    assert client.put("/api/projects/geo/site-anchor", json={"anchor": ANCHOR, "expected_revision": 0}).status_code == 200
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    assert client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply",
                       json={"expected_revision": 1}).status_code == 200
    assert fingerprint() == before
    assert (geo / "network.nc").read_bytes() == nc


# ── (4) the proposal map ────────────────────────────────────────────────────

def test_a_cleared_map_answers_410(client, geo):
    from services.site_scene import proposals
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    proposals.clear()
    r = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply", json={"expected_revision": 1})
    assert (r.status_code, _kind(r)) == (410, "proposal_expired")
    assert r.json()["detail"]["message"] == "This preview expired: generate again"


def test_apply_refuses_inputs_other_than_the_reviewed_ones(client, geo):
    from services.site_scene import proposals
    _adopted(client, geo)
    prop = _propose(client, 1).json()
    # The reviewed hash does not match: refused.
    r = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}/apply",
                    json={"expected_revision": 1, "input_hash": "0" * 64})
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")
    assert r.json()["detail"]["message"] == "This preview changed since you reviewed it: review it again"
    # The stored inputs changed after the preview: refused, nothing written.
    entry = next(e for e in proposals._entries.values() if e["id"] == prop["id"])
    entry["inputs"]["greenfield"][0]["height"] = 99
    r = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}/apply",
                    json={"expected_revision": 1})
    assert (r.status_code, _kind(r)) == (409, "revision_conflict")
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 1


def test_another_projects_or_users_proposal_is_refused(client, install_network, geo):
    from services.site_scene import proposals
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    # Another user of the same project: the same answer as an unknown id.
    key = next(k for k in proposals._entries if k[1] == pid)
    with pytest.raises(Exception) as exc:
        proposals.get(key[0], "someone-else", "campus", pid)
    assert getattr(exc.value, "kind", None) == "proposal_expired"
    # Another project of the same user.
    install_network(_empty_network(), name="geo2")
    assert client.post("/api/projects/geo2", params={"force": True, "rebind": True}).status_code == 200
    _put_site_in = client.put("/api/projects/geo2/sites", json={"version": 1, "sites": [copy.deepcopy(SITE)]})
    assert _put_site_in.status_code == 200
    r = client.post(f"/api/projects/geo2/sites/campus/scene/proposals/{pid}/apply", json={"expected_revision": 1})
    assert (r.status_code, _kind(r)) == (410, "proposal_expired")


def test_the_map_is_bounded_by_count(client, geo, monkeypatch):
    from services.site_scene import proposals
    _adopted(client, geo)
    monkeypatch.setattr(proposals, "MAX_PROPOSALS", 3)
    ids = [_propose(client, 1).json()["id"] for _ in range(5)]
    live = {k[1] for k in proposals._entries}
    assert live == set(ids[2:])


def test_the_map_is_bounded_by_age(client, geo, monkeypatch):
    from services.site_scene import proposals
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    monkeypatch.setattr(proposals, "TTL_SECONDS", -1)
    r = client.get(f"/api/projects/geo/sites/campus/scene/proposals/{pid}")
    assert (r.status_code, _kind(r)) == (410, "proposal_expired")


# ── (7) /flow: electrical keys untouched, the scene block never stale ───────

def test_flow_keeps_the_electrical_payload_and_shows_an_apply_at_once(client, geo, monkeypatch):
    from services import flow_service

    built = []
    real = flow_service.project_flow

    def spy(*a, **k):
        built.append(real(*a, **k))
        return built[-1]

    monkeypatch.setattr(flow_service, "project_flow", spy)
    flow = client.get("/api/projects/geo/flow").json()
    served = {k: v for k, v in flow.items() if k != "scene"}
    # The electrical payload is byte-for-byte what the unchanged flow service built,
    # and the service's (possibly cached) dict never gained a scene key.
    assert json.dumps(served, sort_keys=True) == json.dumps(built[-1], sort_keys=True, default=str)
    assert "scene" not in built[-1]
    assert [a["action"] for a in flow["scene"]["actions"]] == [
        "open_site_creation", "create_plot", "generate_native", "assign_native", "show_connectivity",
        "play_results", "export_scene", "apply_proposal", "process_layout", "reconstruct_photos",
        "generate_blender"]
    _adopted(client, geo)
    assert client.get("/api/projects/geo/flow").json()["scene"]["entry"]["label"] == "Resume site setup"
    pid = _propose(client, 1).json()["id"]
    assert _by_action(client)["apply_proposal"]["available"]
    client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply", json={"expected_revision": 1})
    after = client.get("/api/projects/geo/flow").json()
    assert after["scene"]["entry"]["label"] == "Open 3D site"
    assert not _by_action(client)["apply_proposal"]["available"]
    assert {k: v for k, v in after.items() if k != "scene"} == served


def _by_action(client):
    return {a["action"]: a for a in client.get("/api/projects/geo/flow").json()["scene"]["actions"]}


# ── (9) raw body size before JSON parsing ───────────────────────────────────

def test_an_oversize_body_is_413_before_parsing(client, geo):
    from routers import site_scene
    _adopted(client, geo)
    cap = site_scene.MAX_PROPOSAL_BODY
    at_cap = b"{" + b" " * (cap - 2) + b"}"
    r = client.post("/api/projects/geo/sites/campus/scene/proposals", content=at_cap,
                    headers={"content-type": "application/json"})
    assert r.status_code == 422          # within the cap: parsed, then refused as input
    over = b"x" * (cap + 1)              # not JSON at all: refused on size, never parsed
    r = client.post("/api/projects/geo/sites/campus/scene/proposals", content=over,
                    headers={"content-type": "application/json"})
    assert (r.status_code, _kind(r)) == (413, "resource_limit")
    assert r.json()["detail"]["max"] == cap and r.json()["detail"]["actual"] == cap + 1
    assert r.json()["detail"]["message"].startswith("Too large: the request is over the ")
    r = client.put("/api/projects/geo/site-anchor", content=b"x" * (site_scene.MAX_SMALL_BODY + 1),
                   headers={"content-type": "application/json"})
    assert (r.status_code, _kind(r)) == (413, "resource_limit")


def test_a_proposal_for_a_site_without_a_boundary_is_refused(client, geo, monkeypatch):
    from services.site_scene import store
    from services.site_scene.verdicts import REASON_NO_BOUNDARY
    _adopted(client, geo)
    real = store.read_scene

    def two_points(*a, **k):
        # The legacy PUT refuses such a Site; an older or imported generation may still hold one.
        scene = real(*a, **k)
        return {**scene, "site": {**scene["site"], "boundary": scene["site"]["boundary"][:2]}}

    monkeypatch.setattr(store, "read_scene", two_points)
    r = _propose(client, 1)
    assert (r.status_code, _kind(r)) == (422, "invalid_input")
    assert r.json()["detail"]["message"] == REASON_NO_BOUNDARY


def test_a_committed_apply_reads_back_its_receipt_after_the_map_is_lost(client, geo):
    from services.site_scene import proposals
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    url = f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply"
    receipt = client.post(url, json={"expected_revision": 1}).json()
    proposals.clear()
    r = client.post(url, json={"expected_revision": 1})
    assert r.status_code == 200 and r.json() == receipt
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 2


def test_a_cancel_during_apply_does_not_relabel_the_commit(client, geo, monkeypatch):
    from services.site_scene import proposals, store
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    base = "/api/projects/geo/sites/campus/scene/proposals/" + pid
    real, cancels = store.apply, []

    def apply_then_cancel(*a, **k):
        # A cancel that lands between apply's status check and its commit.
        project_key = next(k for (k, p) in proposals._entries if p == pid)
        cancels.append(proposals.cancel(project_key, proposals._entries[(project_key, pid)]["owner"],
                                        "campus", pid)["status"])
        return real(*a, **k)

    monkeypatch.setattr(store, "apply", apply_then_cancel)
    r = client.post(base + "/apply", json={"expected_revision": 1})
    assert r.status_code == 200, r.text
    assert cancels == ["applying"]
    assert client.get(base).json()["status"] == "applied"


def test_a_failed_apply_releases_its_claim(client, geo):
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    base = "/api/projects/geo/sites/campus/scene/proposals/" + pid
    assert client.post(base + "/apply", json={"expected_revision": 7}).status_code == 409
    assert client.get(base).json()["status"] == "ready"


def test_flow_lists_sites_more_than_1000_m_from_the_anchor(client, geo):
    far = {**SITE, "id": "far", "origin": {"lng": 11.60, "lat": 48.14}}
    near = {**SITE, "origin": {"lng": 11.585, "lat": 48.14}}
    r = client.put("/api/projects/geo/sites", json={"version": 1, "sites": [near, far]})
    assert r.status_code == 200, r.text
    assert client.get("/api/projects/geo/flow").json()["scene"]["anchor_moved"] == []
    assert client.put("/api/projects/geo/site-anchor",
                      json={"anchor": ANCHOR, "expected_revision": 0}).status_code == 200
    # far is ~1.49 km east of the anchor, near ~0.37 km.
    assert client.get("/api/projects/geo/flow").json()["scene"]["anchor_moved"] == ["far"]


def test_an_undeclared_oversize_body_is_cut_at_the_cap():
    """Without a Content-Length, the body stream stops being read once it passes the cap."""
    import asyncio

    from starlette.requests import Request

    from routers import site_scene
    from services.site_scene.contract import SceneError
    parts = [b"x" * 6, b"x" * 6, b"x" * 6]
    read = []

    async def receive():
        read.append(parts.pop(0))
        return {"type": "http.request", "body": read[-1], "more_body": bool(parts)}

    request = Request({"type": "http", "method": "POST", "headers": []}, receive)
    with pytest.raises(SceneError) as e:
        asyncio.run(site_scene._json_body(request, 10))
    assert (e.value.kind, e.value.detail["actual"], len(read)) == ("resource_limit", 12, 2)
