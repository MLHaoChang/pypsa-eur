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
    assert r.json()["detail"].startswith("sites[0].id")
    # Nothing was written.
    assert client.get(f"/api/projects/{name}/sites").json() == {"version": 1, "sites": []}


def test_put_over_limit_413(client, api_project):
    name = api_project("big")
    d = copy.deepcopy(DOC)
    d["sites"][0]["padding"] = "x" * (ss.MAX_SITES_BYTES + 1)
    assert client.put(f"/api/projects/{name}/sites", json=d).status_code == 413


def test_put_removes_orphan_site_dirs(client, api_project, project_storage_dir, monkeypatch):
    """Through `_force_rmtree` (the symlink-refusing remover), not a bare rmtree."""
    import routers.projects as projects_router

    name = api_project("orphans")
    pdir = project_storage_dir(name)
    _seed_context(pdir, "site_a")
    _seed_context(pdir, "zzz")
    removed = []
    real = projects_router._force_rmtree

    def recording(target):
        removed.append(target)
        real(target)

    monkeypatch.setattr(projects_router, "_force_rmtree", recording)
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    assert (pdir / ss.SITES_DIR / "site_a").exists()
    assert not (pdir / ss.SITES_DIR / "zzz").exists()
    assert removed == [pdir / ss.SITES_DIR / "zzz"]


def test_put_succeeds_when_orphan_prune_fails(client, api_project, monkeypatch):
    """The document is on disk; a prune problem is logged, not a 500."""
    name = api_project("prunefail")

    def boom(*a, **k):
        raise OSError("cannot list")

    monkeypatch.setattr(ss, "prune_site_dirs", boom)
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    assert client.get(f"/api/projects/{name}/sites").json() == DOC


def test_get_permission_denied_is_access_error(client, api_project, monkeypatch):
    name = api_project("denied")

    def deny(_dir):
        raise PermissionError("nope")

    monkeypatch.setattr(ss, "read_sites", deny)
    r = client.get(f"/api/projects/{name}/sites")
    # The `_access_denied` shape: 503 with an actionable message naming the file.
    assert r.status_code == 503, r.text
    assert "denied" in r.json()["detail"] and ss.SITES_FILE in r.json()["detail"]


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


def _add_second_bus_and_components(client):
    """Every placeable class on the fixture network (B1, gas, solar, L1)."""
    assert client.post("/api/network/buses", json={"name": "B2", "v_nom": 110.0}).status_code in (200, 201)
    made = [
        ("storage_units", {"name": "su1", "bus": "B1", "p_nom": 1.0, "max_hours": 2.0}),
        ("stores", {"name": "st1", "bus": "B1", "e_nom": 1.0}),
        ("lines", {"name": "ln1", "bus0": "B1", "bus1": "B2", "s_nom": 10.0, "r": 0.01, "x": 0.1}),
        ("links", {"name": "lk1", "bus0": "B1", "bus1": "B2", "p_nom": 10.0}),
        ("transformers", {"name": "tr1", "bus0": "B1", "bus1": "B2", "s_nom": 10.0, "r": 0.001, "x": 0.1}),
    ]
    for collection, body in made:
        r = client.post(f"/api/network/{collection}", json=body)
        assert r.status_code in (200, 201), (collection, r.text)


@pytest.mark.parametrize("collection, cls, old, body", [
    ("generators", "Generator", "gas", {"bus": "B1"}),
    ("loads", "Load", "L1", {"bus": "B1"}),
    ("storage_units", "StorageUnit", "su1", {"bus": "B1"}),
    ("stores", "Store", "st1", {"bus": "B1"}),
    ("lines", "Line", "ln1", {"bus0": "B1", "bus1": "B2"}),
    ("links", "Link", "lk1", {"bus0": "B1", "bus1": "B2"}),
    ("transformers", "Transformer", "tr1", {"bus0": "B1", "bus1": "B2"}),
])
def test_rename_via_put_renames_placement(client, api_project, collection, cls, old, body):
    name = api_project("ren")
    _add_second_bus_and_components(client)
    assert client.put(f"/api/projects/{name}/sites", json=_doc_with(f"{cls}:{old}")).status_code == 200
    # The terminal field(s) are required on the component PUT schemas; the rest merges.
    r = client.put(f"/api/network/{collection}/{old}", json={"name": f"{old}_renamed", **body})
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


def test_rename_hook_noops_without_storage_dir(client, install_network, monkeypatch):
    """A scratch network bound to no project: the rename works, nothing is written."""
    from tests.conftest import build_network

    install_network(build_network())
    seen = []
    real = ss.rename_component_on_disk
    monkeypatch.setattr(ss, "rename_component_on_disk", lambda d, *a: (seen.append(d), real(d, *a)))
    monkeypatch.setattr(ss, "write_sites", lambda *a, **k: pytest.fail("nothing may be written for a scratch network"))
    r = client.put("/api/network/generators/gas", json={"name": "gas_scratch", "bus": "B1"})
    assert r.status_code == 200, r.text
    assert seen == [None]


def test_rename_hook_failure_never_fails_the_rename(client, api_project, monkeypatch):
    name = api_project("renfail")
    assert client.put(f"/api/projects/{name}/sites", json=_doc_with("Generator:gas")).status_code == 200

    def boom(*a, **k):
        raise OSError("sidecar unwritable")

    monkeypatch.setattr(ss, "write_sites", boom)
    r = client.put("/api/network/generators/gas", json={"name": "gas_still_renamed", "bus": "B1"})
    assert r.status_code == 200, r.text
    assert "gas_still_renamed" in [g["name"] for g in client.get("/api/network/generators").json()]


# ── site context routes (WP5, Task 5.3) ─────────────────────────────────────

CONTEXT_DOC = {
    "version": 1, "source": "overpass", "fetched_at": "2026-09-29T00:00:00Z",
    "bbox": [6.82, 53.42, 6.85, 53.45], "buildings": [], "lines": [], "areas": [],
    "terrain": None, "attribution": ["© OpenStreetMap contributors (ODbL)"],
}


def _stub_fetch(monkeypatch, result=None, error=None):
    """Replace the wire with a counter; the wire itself is tests/test_site_context.py."""
    from services import site_context as sc

    calls = []

    def fake(boundary, **kw):
        calls.append(boundary)
        if error is not None:
            raise error
        return copy.deepcopy(result if result is not None else CONTEXT_DOC)

    monkeypatch.setattr(sc, "fetch_context", fake)
    return calls


def test_context_get_404_on_cache_miss_without_touching_upstream(client, api_project, monkeypatch):
    calls = _stub_fetch(monkeypatch)
    name = api_project("ctxmiss")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    r = client.get(f"/api/projects/{name}/sites/site_a/context")
    assert r.status_code == 404, r.text
    assert calls == []


def test_context_post_fetches_caches_and_get_serves_the_cache(client, api_project, project_storage_dir, monkeypatch):
    calls = _stub_fetch(monkeypatch)
    name = api_project("ctxpost")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    r = client.post(f"/api/projects/{name}/sites/site_a/context")
    assert r.status_code == 200, r.text
    assert r.json()["attribution"] == CONTEXT_DOC["attribution"]
    assert calls == [DOC["sites"][0]["boundary"]]
    cached = project_storage_dir(name) / ss.SITES_DIR / "site_a" / "context.json"
    assert json.loads(cached.read_text())["version"] == 1
    # GET now serves the cache with zero upstream calls.
    assert client.get(f"/api/projects/{name}/sites/site_a/context").json() == r.json()
    assert len(calls) == 1


def test_context_delete_clears_the_cache(client, api_project, monkeypatch):
    _stub_fetch(monkeypatch)
    name = api_project("ctxdel")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    assert client.post(f"/api/projects/{name}/sites/site_a/context").status_code == 200
    assert client.delete(f"/api/projects/{name}/sites/site_a/context").json() == {"cleared": True}
    assert client.get(f"/api/projects/{name}/sites/site_a/context").status_code == 404
    assert client.delete(f"/api/projects/{name}/sites/site_a/context").json() == {"cleared": False}


def test_context_unknown_site_404(client, api_project, monkeypatch):
    calls = _stub_fetch(monkeypatch)
    name = api_project("ctxunknown")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    for method in ("get", "post", "delete"):
        assert getattr(client, method)(f"/api/projects/{name}/sites/nope/context").status_code == 404
    assert calls == []


@pytest.mark.parametrize("bad", ["%2E%2E", "..%2F..%2Fetc", "a%2Fb", "x" * 65, "sp%20ace", "a.b"])
def test_context_route_rejects_traversal_id(client, api_project, project_storage_dir, monkeypatch, bad):
    """
    Refused before any I/O: nothing appears on disk and the wire is never
    called. An id whose decoded form carries a slash never reaches the
    handler (the router answers 404/405 for the extra segments); every other
    shape reaches `_site_context_dir` and is 404 from `site_dir`'s check.
    """
    calls = _stub_fetch(monkeypatch)
    name = api_project("ctxtrav")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    before = sorted(p.as_posix() for p in project_storage_dir(name).rglob("*"))
    for method in ("get", "post", "delete"):
        r = getattr(client, method)(f"/api/projects/{name}/sites/{bad}/context")
        assert r.status_code in ((404, 405) if "%2F" in bad else (404,)), (method, bad, r.status_code)
    assert sorted(p.as_posix() for p in project_storage_dir(name).rglob("*")) == before
    assert calls == []


def test_context_post_502_carries_the_upstream_message(client, api_project, project_storage_dir, monkeypatch):
    from services import site_context as sc

    _stub_fetch(monkeypatch, error=sc.SiteContextUnavailable("Overpass at X answered HTTP 429 (rate limited); set PYPSAGUI_OVERPASS_URL"))
    name = api_project("ctx502")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    r = client.post(f"/api/projects/{name}/sites/site_a/context")
    assert r.status_code == 502, r.text
    assert "429" in r.json()["detail"] and "PYPSAGUI_OVERPASS_URL" in r.json()["detail"]
    assert not (project_storage_dir(name) / ss.SITES_DIR / "site_a" / "context.json").exists()


def test_context_post_returns_the_document_when_the_cache_cannot_be_written(client, api_project, monkeypatch):
    from services import site_context as sc

    _stub_fetch(monkeypatch)
    name = api_project("ctxnocache")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200

    def boom(*a, **k):
        raise OSError("read-only volume")

    monkeypatch.setattr(sc, "write_cached", boom)
    r = client.post(f"/api/projects/{name}/sites/site_a/context")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/projects/{name}/sites/site_a/context").status_code == 404


def test_context_post_409_while_solving(client, api_project, session_ctx, monkeypatch):
    """(pin) the context routes sit under `/api/projects/` too."""
    calls = _stub_fetch(monkeypatch)
    name = api_project("ctxsolving")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    started, release = threading.Event(), threading.Event()

    def _spin():
        started.set()
        release.wait(timeout=5)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    started.wait(timeout=5)
    session_ctx(client).solver_state["thread"] = t
    try:
        r = client.post(f"/api/projects/{name}/sites/site_a/context")
        assert r.status_code == 409, r.text
        assert r.json().get("code") == "solver_in_flight"
        assert calls == []
    finally:
        release.set()
        t.join(timeout=5)


def test_context_other_org_cannot_read_write_or_clear(client, other_org_client, api_project, monkeypatch):
    _stub_fetch(monkeypatch)
    name = api_project("ctxorg")
    assert client.put(f"/api/projects/{name}/sites", json=DOC).status_code == 200
    assert client.post(f"/api/projects/{name}/sites/site_a/context").status_code == 200
    for method in ("get", "post", "delete"):
        assert getattr(other_org_client, method)(f"/api/projects/{name}/sites/site_a/context").status_code == 404
