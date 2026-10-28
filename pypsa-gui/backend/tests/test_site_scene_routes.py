"""
SC-01a: the mounted site-scene routes (D01/D02, contracts.md §5) through the
real router: anchor, scene read, adoption, proposals and apply, and the legacy
`/sites` seam for an enabled Site (D03).
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib

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
    ({**ANCHOR, "lat": 91}, "lat", None),
    ({**ANCHOR, "lat": 120, "lng": 45}, "lat", "swapped"),
    ({**ANCHOR, "lng": -180.5}, "lng", None),
    ({**ANCHOR, "source": "bus"}, "source", None),
    ({**ANCHOR, "confirmed": "yes"}, "confirmed", None),
])
def test_anchor_put_refuses_invalid_input_naming_the_field(client, geo, anchor, field, hint):
    r = client.put("/api/projects/geo/site-anchor", json={"anchor": anchor, "expected_revision": 0})
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


def test_anchor_and_scene_writes_409_under_a_foreign_lock(client, geo, _auth_db, second_identity, project_row):
    from services import project_locks
    _put_site(client)
    _engine, session_local = _auth_db
    with session_local() as db:
        assert project_locks.acquire_lock(db, project_row("geo").id, second_identity["user_id"]) is not None
        db.commit()
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
    assert r.json() == {"state": "unadopted", "site_id": "campus"}
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

    def go(pid):
        return client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply",
                           json={"expected_revision": 1}).status_code

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        codes = sorted(pool.map(go, props))
    assert codes == [200] + [409] * 7
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 2


def test_a_cancelled_proposal_cannot_be_applied(client, geo):
    _adopted(client, geo)
    pid = _propose(client, 1).json()["id"]
    assert client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/cancel").status_code == 200
    assert client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/cancel").status_code == 200
    r = client.post(f"/api/projects/geo/sites/campus/scene/proposals/{pid}/apply", json={"expected_revision": 1})
    assert (r.status_code, _kind(r)) == (409, "proposal_cancelled")


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
