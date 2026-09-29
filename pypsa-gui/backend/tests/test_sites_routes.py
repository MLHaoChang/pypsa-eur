"""
`GET/PUT /api/projects/{name}/sites` and the sidecar's travel with the bundle.

Plan: docs/superpowers/plans/2026-09-29-3d-site-view-phase1.md, Tasks 1.2,
1.3 (pins), 1.4 (HTTP half). The lock test lives in test_project_locks.py in
the direct-handler style, because a foreign lock cannot be produced over HTTP
(the second identity is another org and sees 404).
"""
from __future__ import annotations

import copy
import io
import json
import threading
import zipfile

import pytest

import main
from routers.projects import _BUNDLE_DIRS, _BUNDLE_FILES
from services import site_service as ss

DOC = {
    "version": 1,
    "sites": [{
        "id": "site_a",
        "name": "Campus",
        "buses": ["B1"],
        "boundary": [[6.83, 53.44], [6.84, 53.44], [6.84, 53.43], [6.83, 53.43]],
        "origin": {"lng": 6.835, "lat": 53.435},
        "placements": {"Generator:G1": {"x": 10.0, "y": -5.0, "heading": 45}},
    }],
}


def _with_site_id(doc, sid):
    d = copy.deepcopy(doc)
    d["sites"][0]["id"] = sid
    return d


def _seed_context(project_dir, sid="site_a"):
    d = project_dir / ss.SITES_DIR / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "context.json").write_text(json.dumps({"version": 1, "buildings": []}))
    return d


# ── GET / PUT ───────────────────────────────────────────────────────────────

def test_get_sites_missing_project_404(client):
    assert client.get("/api/projects/does-not-exist/sites").status_code == 404


def test_get_sites_empty_when_no_file(client, api_project):
    name = api_project("nosites")
    r = client.get(f"/api/projects/{name}/sites")
    assert r.status_code == 200, r.text
    assert r.json() == {"version": 1, "sites": []}


def test_put_then_get_round_trip(client, api_project):
    name = api_project("rt")
    r = client.put(f"/api/projects/{name}/sites", json=DOC)
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": name, "sites": 1}
    assert client.get(f"/api/projects/{name}/sites").json() == DOC


def test_put_invalid_422_names_the_field(client, api_project):
    name = api_project("bad")
    r = client.put(f"/api/projects/{name}/sites", json=_with_site_id(DOC, "../x"))
    assert r.status_code == 422, r.text
    assert "id" in r.text
    # Nothing was written.
    assert client.get(f"/api/projects/{name}/sites").json() == {"version": 1, "sites": []}


def test_put_over_limit_413(client, api_project):
    name = api_project("big")
    d = copy.deepcopy(DOC)
    d["sites"][0]["padding"] = "x" * (ss.MAX_SITES_BYTES + 1)
    assert client.put(f"/api/projects/{name}/sites", json=d).status_code == 413


def test_put_removes_orphan_site_dirs(client, api_project, project_storage_dir):
    name = api_project("orphans")
    pdir = project_storage_dir(name)
    _seed_context(pdir, "site_a")
    _seed_context(pdir, "zzz")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    assert (pdir / ss.SITES_DIR / "site_a").exists()
    assert not (pdir / ss.SITES_DIR / "zzz").exists()


def test_get_permission_denied_is_access_error(client, api_project, monkeypatch):
    name = api_project("denied")

    def deny(_dir):
        raise PermissionError("nope")

    monkeypatch.setattr(ss, "read_sites", deny)
    r = client.get(f"/api/projects/{name}/sites")
    assert r.status_code != 200
    assert r.json() != {"version": 1, "sites": []}
    assert "permission" in r.text.lower() or "access" in r.text.lower()


def test_other_org_cannot_read_or_write(client, other_org_client, api_project):
    name = api_project("mine")
    assert other_org_client.get(f"/api/projects/{name}/sites").status_code == 404
    assert other_org_client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 404


# ── pins (expected green the moment the route exists) ───────────────────────

def test_sites_writes_never_snapshot_undo(client, api_project):
    """(pin) `/api/projects/` is outside the undo middleware's prefixes."""
    assert not any(p.startswith("/api/projects") for p in main._UNDO_PREFIXES)
    name = api_project("undo")
    before = client.get("/api/network/undo/info").json()["depth"]
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    after = client.get("/api/network/undo/info").json()["depth"]
    assert after == before


def test_put_sites_409_while_solving(client, api_project, session_ctx):
    """(pin) `/api/projects/` is in `_SOLVER_BLOCKING_PREFIXES`; the
    middleware answers before the handler. Same trick as test_activate."""
    name = api_project("solving")
    started, release = threading.Event(), threading.Event()

    def _spin():
        started.set()
        release.wait(timeout=5)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    started.wait(timeout=5)
    session_ctx(client).solver_state["thread"] = t
    try:
        r = client.put(f"/api/projects/{name}/sites", json=DOC)
        assert r.status_code == 409, r.text
        assert r.json().get("code") == "solver_in_flight"
    finally:
        release.set()
        t.join(timeout=5)


# ── bundle travel (Task 1.4) ────────────────────────────────────────────────

def test_sites_in_bundle_tuples():
    assert ss.SITES_FILE in _BUNDLE_FILES
    assert ss.SITES_DIR in _BUNDLE_DIRS


def test_scenario_copies_sites_and_context_dir(client, api_project, project_storage_dir):
    name = api_project("base")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    _seed_context(project_storage_dir(name))
    r = client.post(f"/api/projects/{name}/scenarios", json={"name": "base_child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = r.json().get("name") or "base_child"
    cdir = project_storage_dir(child)
    assert (cdir / ss.SITES_FILE).exists()
    assert (cdir / ss.SITES_DIR / "site_a" / "context.json").exists()
    assert client.get(f"/api/projects/{child}/sites").json() == DOC
    # Diverge the child; the base is untouched.
    moved = copy.deepcopy(DOC)
    moved["sites"][0]["placements"]["Generator:G1"]["x"] = 999.0
    assert client.put(f"/api/projects/{child}/sites", json=moved).status_code == 200
    assert client.get(f"/api/projects/{name}/sites").json() == DOC


def test_export_import_round_trips_sites(client, api_project, project_storage_dir):
    name = api_project("exp")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    _seed_context(project_storage_dir(name))
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert ss.SITES_FILE in names
    assert any(n.startswith(f"{ss.SITES_DIR}/site_a/") for n in names), names
    r = client.post("/api/projects/import_bundle?name=exp_imported",
                    files={"file": ("exp.pypsaproj.zip", r.content, "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or r.json().get("name") or "exp_imported"
    assert client.get(f"/api/projects/{imported}/sites").json() == DOC
    assert (project_storage_dir(imported) / ss.SITES_DIR / "site_a" / "context.json").exists()


def test_snapshot_restore_brings_sites_back(client, api_project):
    name = api_project("snap")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json()["id"]
    assert client.put(f"/api/projects/{name}/sites", json={"version": 1, "sites": []}).status_code == 200
    assert client.get(f"/api/projects/{name}/sites").json()["sites"] == []
    assert client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore").status_code == 200
    assert client.get(f"/api/projects/{name}/sites").json() == DOC


# ── rename hook (Task 1.6, D14) ─────────────────────────────────────────────
#
# There are no per-class rename routes. Renames go through
# `PUT /api/network/{collection}/{name}` with `name` in the body
# (services/network_crud.py::_update_component) and, for buses, through
# `POST /buses/{name}/rename` (services/network_buses.py::apply_rename_bus).
# The sidecar hook lives in those two seams so every caller — the properties
# panel, the grid, the chat tools — keeps placements across a rename.

def _doc_with(key):
    d = copy.deepcopy(DOC)
    d["sites"][0]["placements"] = {key: {"x": 1.0, "y": 2.0, "heading": 3}}
    return d


@pytest.mark.parametrize("collection, cls, old", [
    ("generators", "Generator", "gas"),
    ("loads", "Load", "L1"),
])
def test_rename_via_put_renames_placement(client, api_project, collection, cls, old):
    name = api_project("ren")
    assert client.put(f"/api/projects/{name}/sites", json=_doc_with(f"{cls}:{old}")).status_code == 200
    # `bus` is a required body field on the component PUT schemas; the rest merges.
    r = client.put(f"/api/network/{collection}/{old}", json={"name": f"{old}_renamed", "bus": "B1"})
    assert r.status_code == 200, r.text
    placements = client.get(f"/api/projects/{name}/sites").json()["sites"][0]["placements"]
    assert placements == {f"{cls}:{old}_renamed": {"x": 1.0, "y": 2.0, "heading": 3}}


def test_rename_bus_route_renames_placement(client, api_project):
    name = api_project("renbus")
    assert client.put(f"/api/projects/{name}/sites", json=_doc_with("Bus:B1")).status_code == 200
    r = client.post("/api/network/buses/B1/rename", json={"new_name": "B1x"})
    assert r.status_code == 200, r.text
    placements = client.get(f"/api/projects/{name}/sites").json()["sites"][0]["placements"]
    assert "Bus:B1x" in placements and "Bus:B1" not in placements


def test_rename_hook_noops_without_storage_dir(client, install_network):
    """A scratch network bound to no project: the rename works, nothing is written."""
    from tests.conftest import build_network

    install_network(build_network())
    r = client.put("/api/network/generators/gas", json={"name": "gas_scratch", "bus": "B1"})
    assert r.status_code == 200, r.text


def test_rename_hook_failure_never_fails_the_rename(client, api_project, monkeypatch):
    name = api_project("renfail")
    assert client.put(f"/api/projects/{name}/sites", json=_doc_with("Generator:gas")).status_code == 200

    def boom(*a, **k):
        raise OSError("sidecar unwritable")

    monkeypatch.setattr(ss, "write_sites", boom)
    r = client.put("/api/network/generators/gas", json={"name": "gas_still_renamed", "bus": "B1"})
    assert r.status_code == 200, r.text
    assert "gas_still_renamed" in [g["name"] for g in client.get("/api/network/generators").json()]
