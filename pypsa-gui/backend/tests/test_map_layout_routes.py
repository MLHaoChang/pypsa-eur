"""
`GET/PUT /api/projects/{name}/map_layout` and the sidecar's travel with the
bundle.

Plan: docs/superpowers/plans/2026-10-06-visual-layers-2-map-view.md, M1.
Modelled on `test_sites_routes.py` (plan 3 S0). The foreign-lock 409 test
lives in `test_project_locks.py` beside `put_layout`'s, in the direct-handler
style, because a foreign lock cannot be produced over HTTP (the second
identity is another org and sees 404).

A Save-As is NOT a server-side carry for this sidecar — nor for `layout.json`:
`_carry_sidecars_on_move` moves `chat.jsonl` and copies `uploads/` only, and
the frontend re-homes the pending document to the new name through
`flushPendingMapLayoutToServer(saved, { previousProject })`. That half is
pinned in `frontend/src/pages/mapLayoutStore.test.ts`.
"""
from __future__ import annotations

import copy
import io
import json
import threading
import zipfile

import pytest

import main
from routers import projects as projects_router
from routers.projects import _BUNDLE_FILES
from services import map_layout_service as ml

EMPTY = {"version": 1, "routes": {}, "bubbles": {}}
DOC = {
    "version": 1,
    "routes": {
        "line:L1": {"points": [[6.8321, 53.4396], [6.8340, 53.4401]], "source": "user"},
        "tr:T1": {"points": [[6.83, 53.44]], "source": "osm"},
    },
    "bubbles": {"B1|Thermal": {"dx": 42.0, "dy": -18}},
}


def _get(client, name):
    r = client.get(f"/api/projects/{name}/map_layout")
    assert r.status_code == 200, r.text
    return r.json()


# ── GET / PUT ───────────────────────────────────────────────────────────────

def test_get_missing_project_404(client):
    assert client.get("/api/projects/does-not-exist/map_layout").status_code == 404


def test_get_empty_when_no_file(client, api_project):
    name = api_project("nomap")
    assert _get(client, name) == EMPTY


@pytest.mark.parametrize("raw", ["{not json", "[1]", '{"version": 1}'])
def test_get_empty_when_file_is_corrupt(client, api_project, project_storage_dir, raw):
    name = api_project("corrupt")
    (project_storage_dir(name) / ml.MAP_LAYOUT_FILE).write_text(raw)
    assert _get(client, name) == EMPTY


def test_put_then_get_round_trip(client, api_project):
    name = api_project("rt")
    r = client.put(f"/api/projects/{name}/map_layout", json=DOC)
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": name, "routes": 2, "bubbles": 1}
    assert _get(client, name) == DOC


def test_put_writes_the_sidecar_compactly_and_atomically(client, api_project, project_storage_dir):
    name = api_project("ondisk")
    assert client.put(f"/api/projects/{name}/map_layout", json=DOC).status_code == 200
    pdir = project_storage_dir(name)
    raw = (pdir / ml.MAP_LAYOUT_FILE).read_text()
    assert json.loads(raw) == DOC and ": " not in raw
    assert not any(p.name.endswith(".tmp") for p in pdir.iterdir())


@pytest.mark.parametrize("mutate, field", [
    (lambda d: d.__setitem__("version", 2), "version"),
    (lambda d: d.__setitem__("routes", []), "routes"),
    (lambda d: d["routes"].__setitem__("bus:B1", {"points": [[1, 1]], "source": "user"}), "routes: key 'bus:B1'"),
    (lambda d: d["routes"]["line:L1"].__setitem__("points", []), "routes['line:L1'].points"),
    (lambda d: d["routes"]["line:L1"].__setitem__("points", [[200, 0]]), "routes['line:L1'].points"),
    (lambda d: d["routes"]["line:L1"].__setitem__("source", "magic"), "routes['line:L1'].source"),
    (lambda d: d["bubbles"].__setitem__("B1", {"dx": 0, "dy": 0}), "bubbles: key 'B1'"),
    (lambda d: d["bubbles"]["B1|Thermal"].__setitem__("dx", "1"), "bubbles['B1|Thermal']"),
])
def test_put_invalid_422_names_the_field_and_writes_nothing(client, api_project, mutate, field):
    name = api_project("bad")
    d = copy.deepcopy(DOC)
    mutate(d)
    r = client.put(f"/api/projects/{name}/map_layout", json=d)
    assert r.status_code == 422, r.text
    assert field in r.json()["detail"]
    assert _get(client, name) == EMPTY


def test_put_a_list_body_is_422(client, api_project):
    name = api_project("list")
    assert client.put(f"/api/projects/{name}/map_layout", json=[1, 2]).status_code == 422


def test_put_preserves_unknown_keys(client, api_project):
    name = api_project("fwd")
    d = copy.deepcopy(DOC)
    d["future"] = {"a": 1}
    d["routes"]["line:L1"]["length_source"] = "route"
    assert client.put(f"/api/projects/{name}/map_layout", json=d).status_code == 200
    assert _get(client, name) == d


def test_put_over_limit_413(client, api_project):
    name = api_project("big")
    d = copy.deepcopy(DOC)
    d["padding"] = "x" * (projects_router._MAX_LAYOUT_BYTES + 1)
    r = client.put(f"/api/projects/{name}/map_layout", json=d)
    assert r.status_code == 413, r.text
    assert _get(client, name) == EMPTY


def test_the_cap_is_the_layout_cap():
    """One cap for both sidecars: the route passes `_MAX_LAYOUT_BYTES` through."""
    assert ml.MAX_MAP_LAYOUT_BYTES == projects_router._MAX_LAYOUT_BYTES


def test_get_permission_denied_is_access_error(client, api_project, monkeypatch):
    name = api_project("denied")

    def deny(_dir):
        raise PermissionError("nope")

    monkeypatch.setattr(ml, "read_map_layout", deny)
    r = client.get(f"/api/projects/{name}/map_layout")
    # The `_access_denied` shape: 503 with an actionable message naming the file.
    assert r.status_code == 503, r.text
    assert "denied" in r.json()["detail"] and ml.MAP_LAYOUT_FILE in r.json()["detail"]


def test_other_org_cannot_read_or_write(client, other_org_client, api_project):
    name = api_project("mine")
    assert other_org_client.get(f"/api/projects/{name}/map_layout").status_code == 404
    assert other_org_client.put(f"/api/projects/{name}/map_layout", json=DOC).status_code == 404


# ── pins ────────────────────────────────────────────────────────────────────

def test_map_layout_writes_never_snapshot_undo(client, api_project):
    """(pin) `/api/projects/` is outside the undo middleware's prefixes."""
    assert not any(p.startswith("/api/projects") for p in main._UNDO_PREFIXES)
    name = api_project("undo")
    before = client.get("/api/network/undo/info").json()["depth"]
    assert client.put(f"/api/projects/{name}/map_layout", json=DOC).status_code == 200
    assert client.get("/api/network/undo/info").json()["depth"] == before


def test_put_409_while_solving(client, api_project, session_ctx):
    """(pin) `/api/projects/` is in `_SOLVER_BLOCKING_PREFIXES`."""
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
        r = client.put(f"/api/projects/{name}/map_layout", json=DOC)
        assert r.status_code == 409, r.text
        assert r.json().get("code") == "solver_in_flight"
    finally:
        release.set()
        t.join(timeout=5)


# ── bundle travel ───────────────────────────────────────────────────────────

def test_map_layout_in_bundle_tuple():
    assert ml.MAP_LAYOUT_FILE in _BUNDLE_FILES
    assert ml.MAP_LAYOUT_FILE == "map_layout.json"


def test_scenario_copies_the_map_layout(client, api_project, project_storage_dir):
    name = api_project("base")
    assert client.put(f"/api/projects/{name}/map_layout", json=DOC).status_code == 200
    r = client.post(f"/api/projects/{name}/scenarios", json={"name": "base_child", "description": "x"})
    assert r.status_code in (200, 201), r.text
    child = r.json().get("name") or "base_child"
    assert (project_storage_dir(child) / ml.MAP_LAYOUT_FILE).exists()
    assert _get(client, child) == DOC
    # Diverge the child; the base is untouched.
    moved = copy.deepcopy(DOC)
    moved["routes"]["line:L1"]["points"] = [[7.0, 54.0]]
    assert client.put(f"/api/projects/{child}/map_layout", json=moved).status_code == 200
    assert _get(client, name) == DOC


def test_export_import_round_trips_the_map_layout(client, api_project):
    name = api_project("exp")
    assert client.put(f"/api/projects/{name}/map_layout", json=DOC).status_code == 200
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    assert ml.MAP_LAYOUT_FILE in zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    r = client.post("/api/projects/import_bundle?name=exp_imported",
                    files={"file": ("exp.pypsaproj.zip", r.content, "application/zip")})
    assert r.status_code in (200, 201), r.text
    imported = r.json().get("imported") or r.json().get("name") or "exp_imported"
    assert _get(client, imported) == DOC


def test_an_older_bundle_without_the_sidecar_still_imports(client, api_project):
    name = api_project("plain")
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200
    src = zipfile.ZipFile(io.BytesIO(r.content))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in src.namelist():
            if m != ml.MAP_LAYOUT_FILE:
                zf.writestr(m, src.read(m))
    r = client.post("/api/projects/import_bundle?name=plain_imported",
                    files={"file": ("old.pypsaproj.zip", buf.getvalue(), "application/zip")})
    assert r.status_code in (200, 201), r.text
    assert _get(client, r.json().get("imported") or "plain_imported") == EMPTY


def test_snapshot_restore_brings_the_map_layout_back(client, api_project, project_storage_dir):
    name = api_project("snap")
    assert client.put(f"/api/projects/{name}/map_layout", json=DOC).status_code == 200
    r = client.post(f"/api/projects/{name}/snapshots", json={"label": "v1"})
    assert r.status_code in (200, 201), r.text
    snap_id = r.json()["id"]
    d = project_storage_dir(name)
    snap_dirs = [p for p in (d / "snapshots").iterdir() if p.is_dir()]
    assert any((p / ml.MAP_LAYOUT_FILE).exists() for p in snap_dirs), snap_dirs
    assert client.put(f"/api/projects/{name}/map_layout", json=EMPTY).status_code == 200
    assert _get(client, name) == EMPTY
    assert client.post(f"/api/projects/{name}/snapshots/{snap_id}/restore").status_code == 200
    assert _get(client, name) == DOC


# ── rename hook ─────────────────────────────────────────────────────────────
#
# Renames go through `PUT /api/network/{collection}/{name}` with `name` in the
# body (services/network_crud.py::_update_component). The sidecar hook lives
# in that seam so every caller — the properties panel, the grid, the chat
# tools — keeps a branch's route across a rename.

def _doc_with(key):
    d = copy.deepcopy(DOC)
    d["routes"] = {key: {"points": [[1.0, 2.0]], "source": "user"}}
    return d


def _add_second_bus_and_branches(client):
    assert client.post("/api/network/buses", json={"name": "B2", "v_nom": 110.0}).status_code in (200, 201)
    made = [
        ("lines", {"name": "ln1", "bus0": "B1", "bus1": "B2", "s_nom": 10.0, "r": 0.01, "x": 0.1}),
        ("links", {"name": "lk1", "bus0": "B1", "bus1": "B2", "p_nom": 10.0}),
        ("transformers", {"name": "tr1", "bus0": "B1", "bus1": "B2", "s_nom": 10.0, "r": 0.001, "x": 0.1}),
    ]
    for collection, body in made:
        r = client.post(f"/api/network/{collection}", json=body)
        assert r.status_code in (200, 201), (collection, r.text)


@pytest.mark.parametrize("collection, kind, old", [
    ("lines", "line", "ln1"),
    ("links", "link", "lk1"),
    ("transformers", "tr", "tr1"),
])
def test_rename_via_put_keeps_the_route(client, api_project, collection, kind, old):
    name = api_project("ren")
    _add_second_bus_and_branches(client)
    assert client.put(f"/api/projects/{name}/map_layout", json=_doc_with(f"{kind}:{old}")).status_code == 200
    r = client.put(f"/api/network/{collection}/{old}", json={"name": f"{old}_renamed", "bus0": "B1", "bus1": "B2"})
    assert r.status_code == 200, r.text
    routes = _get(client, name)["routes"]
    assert routes == {f"{kind}:{old}_renamed": {"points": [[1.0, 2.0]], "source": "user"}}


def test_update_without_rename_leaves_the_route_alone(client, api_project):
    name = api_project("upd")
    _add_second_bus_and_branches(client)
    assert client.put(f"/api/projects/{name}/map_layout", json=_doc_with("line:ln1")).status_code == 200
    r = client.put("/api/network/lines/ln1", json={"name": "ln1", "bus0": "B1", "bus1": "B2", "s_nom": 20.0})
    assert r.status_code == 200, r.text
    assert "line:ln1" in _get(client, name)["routes"]


def test_rename_of_a_non_branch_does_not_touch_the_sidecar(client, api_project, monkeypatch):
    name = api_project("gen")
    assert client.put(f"/api/projects/{name}/map_layout", json=DOC).status_code == 200
    monkeypatch.setattr(ml, "write_map_layout", lambda *a, **k: pytest.fail("a generator has no route"))
    r = client.put("/api/network/generators/gas", json={"name": "gas_renamed", "bus": "B1"})
    assert r.status_code == 200, r.text
    assert _get(client, name) == DOC


def test_rename_hook_noops_without_storage_dir(client, install_network, monkeypatch):
    """A scratch network bound to no project: the rename works, nothing is written."""
    from tests.conftest import build_network

    install_network(build_network())
    assert client.post("/api/network/buses", json={"name": "B2", "v_nom": 110.0}).status_code in (200, 201)
    assert client.post("/api/network/lines", json={"name": "ln1", "bus0": "B1", "bus1": "B2", "s_nom": 10.0, "r": 0.01, "x": 0.1}).status_code in (200, 201)
    seen = []
    real = ml.rename_component_on_disk
    monkeypatch.setattr(ml, "rename_component_on_disk", lambda d, *a: (seen.append(d), real(d, *a)))
    monkeypatch.setattr(ml, "write_map_layout", lambda *a, **k: pytest.fail("nothing may be written for a scratch network"))
    r = client.put("/api/network/lines/ln1", json={"name": "ln1_scratch", "bus0": "B1", "bus1": "B2"})
    assert r.status_code == 200, r.text
    assert seen == [None]


def test_rename_hook_failure_never_fails_the_rename(client, api_project, monkeypatch):
    name = api_project("renfail")
    _add_second_bus_and_branches(client)
    assert client.put(f"/api/projects/{name}/map_layout", json=_doc_with("line:ln1")).status_code == 200

    def boom(*a, **k):
        raise OSError("sidecar unwritable")

    monkeypatch.setattr(ml, "write_map_layout", boom)
    r = client.put("/api/network/lines/ln1", json={"name": "ln1_still_renamed", "bus0": "B1", "bus1": "B2"})
    assert r.status_code == 200, r.text
    assert "ln1_still_renamed" in [ln["name"] for ln in client.get("/api/network/lines").json()]
