"""
P33b gate B-1: per-project state must not travel across a project switch.

`reset_network` used to hand the outgoing project's `_UndoState` OBJECT to the
next project's context (`undo=prev.undo`). Steps 0 and 4b of P33b keep both
contexts resident and make an undo land, so the shared stack became a
data-loss path: create A, create B, edit B, switch back to A — A's header
offered Undo (B's edit), and Undo replaced A's network with B's; a save then
wrote B's network into A's files. The same held through load and through a
restore of another project's Saved snapshot.

A switch to a DIFFERENT project now builds the new context with its own empty
undo stack and a counter of 0 (load / import / restore then restore the
project's saved counter), and does not inherit the outgoing project's result
state. The clears those routes perform (undo, dirty) apply to the new context,
never to the project the user is leaving. The gate's probes are kept here as
tests, in local mode (the desktop build: no session) and under sessions, with
the on-disk check after a save.
"""
from __future__ import annotations

import json

from services.pypsa_service import PyPSAService
from tests.test_local_mode_api import local_client  # noqa: F401 — fixture


def _buses(c):
    return sorted(b["name"] for b in c.get("/api/network/buses").json())


def _xs(c):
    return {b["name"]: float(b.get("x") or 0.0) for b in c.get("/api/network/buses").json()}


def _move(c, dx=1.0):
    b = c.get("/api/network/buses").json()[0]
    r = c.put(f"/api/network/buses/{b['name']}", json={**b, "x": float(b.get("x") or 0.0) + dx})
    assert r.status_code == 200, r.text


def _drop_resident(name):
    with PyPSAService._registry_lock:
        for k in [k for k, cc in PyPSAService._contexts.items() if cc.loaded_project == name]:
            PyPSAService._contexts.pop(k)


def _assert_no_bleed(c, a, a_buses, a_xs):
    """On A after B was edited: nothing to undo, undo refuses, A unchanged —
    in memory and, after a save and a cold re-activation, on disk."""
    info = c.get("/api/network/undo/info").json()
    assert info["depth"] == 0, (
        f"project {a!r} offers B's edit as its own undo (depth {info['depth']})")
    assert c.post("/api/network/undo").status_code == 409
    assert _buses(c) == a_buses and _xs(c) == a_xs
    r = c.post(f"/api/projects/{a}", params={"force": True})
    assert r.status_code == 200, r.text
    _drop_resident(a)
    assert c.post(f"/api/projects/{a}/activate").status_code == 200
    assert _buses(c) == a_buses and _xs(c) == a_xs, "A's files no longer hold A's network"


# ── B-1, local mode (the desktop build; the gate's repro) ─────────────────

def test_local_mode_template_switch_does_not_share_the_undo_stack(local_client):
    c = local_client
    assert c.post("/api/projects/from_template/eh_h2_hub", params={"name": "LA"}).status_code == 200
    a_buses, a_xs = _buses(c), _xs(c)
    assert c.post("/api/projects/from_template/eh_microgrid", params={"name": "LB"}).status_code == 200
    assert _buses(c) != a_buses
    _move(c)
    assert c.get("/api/network/undo/info").json()["depth"] == 1
    assert c.post("/api/projects/LA/activate").status_code == 200
    assert _buses(c) == a_buses
    _assert_no_bleed(c, "LA", a_buses, a_xs)


# ── B-1, sessions: template, load, another project's snapshot restore ─────

def _hub_project(client, install_network, name):
    from tests.test_energy_hub_frontier_fmea import _feeder_hub
    install_network(_feeder_hub())
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    return name


def _bleed_case(client, a, switch):
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    a_buses, a_xs = _buses(client), _xs(client)
    switch()
    assert _buses(client) != a_buses, "the probe needs distinct networks"
    _move(client)
    assert client.get("/api/network/undo/info").json()["depth"] == 1
    assert client.post(f"/api/projects/{a}/activate").status_code == 200
    assert _buses(client) == a_buses
    _assert_no_bleed(client, a, a_buses, a_xs)


def test_template_create_does_not_share_the_undo_stack(client, api_project):
    a = api_project("iso-tpl-a")

    def switch():
        r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "iso-tpl-b"})
        assert r.status_code == 200, r.text
    _bleed_case(client, a, switch)


def test_load_does_not_share_the_undo_stack(client, api_project, install_network):
    a = api_project("iso-load-a")
    _hub_project(client, install_network, "iso-load-b")

    def switch():
        assert client.get("/api/projects/iso-load-b").status_code == 200
    _bleed_case(client, a, switch)


def test_another_projects_restore_does_not_share_the_undo_stack(
        client, api_project, install_network):
    a = api_project("iso-rst-a")
    _hub_project(client, install_network, "iso-rst-b")
    snap = client.post("/api/projects/iso-rst-b/snapshots", json={"label": "s"}).json()["id"]

    def switch():
        r = client.post(f"/api/projects/iso-rst-b/snapshots/{snap}/restore")
        assert r.status_code == 200, r.text
    _bleed_case(client, a, switch)


def test_bundle_import_does_not_share_the_undo_stack(client, api_project, install_network):
    a = api_project("iso-imp-a")
    _hub_project(client, install_network, "iso-imp-src")
    bundle = client.get("/api/projects/iso-imp-src/bundle").content

    def switch():
        r = client.post("/api/projects/import_bundle",
                        files={"file": ("b.zip", bundle, "application/zip")},
                        params={"name": "iso-imp-b"})
        assert r.status_code == 200, r.text
    _bleed_case(client, a, switch)


# ── The outgoing project keeps its own state ──────────────────────────────

def test_the_project_left_keeps_its_own_undo_history_and_unsaved_flag(local_client):
    """The routes cleared undo and the dirty flag BEFORE the swap, i.e. on the
    project being left — so a resident A with unsaved edits read clean and
    lost its own undo history. They now clear the new context."""
    c = local_client
    assert c.post("/api/projects/from_template/eh_h2_hub", params={"name": "KA"}).status_code == 200
    xs0 = _xs(c)
    _move(c, 2.0)
    edited = _xs(c)
    info = c.get("/api/network/undo/info").json()
    assert info["depth"] == 1 and info["unsaved"] is True
    assert c.post("/api/projects/from_template/eh_microgrid", params={"name": "KB"}).status_code == 200
    info_b = c.get("/api/network/undo/info").json()
    assert info_b["depth"] == 0 and info_b["unsaved"] is False
    assert c.post("/api/projects/KA/activate").status_code == 200
    assert _xs(c) == edited
    info_a = c.get("/api/network/undo/info").json()
    assert info_a["unsaved"] is True, "A's unsaved edit reads as clean after a switch"
    assert info_a["depth"] == 1, "A lost its own undo history to the switch"
    assert c.post("/api/network/undo").status_code == 200
    assert _xs(c) == xs0


def test_a_template_project_starts_with_no_results_and_a_zero_counter(
        client, api_project, session_state, project_storage_dir):
    """S-4 and the result-state half of the B-1 audit: a template project does
    not inherit the previous project's stored report or edit counter, and its
    metadata.json carries its counter from creation."""
    from services.adequacy import eh_report as R
    from tests.test_eh_study_record_survives_reactivation import _report

    api_project("carry-a")
    r0 = client.get("/api/network/undo/info").json()["network_revision"]
    for _ in range(3):
        _move(client)
    assert client.get("/api/network/undo/info").json()["network_revision"] == r0 + 3 > 0
    R.store_eh_report(session_state(client), _report())
    assert client.get("/api/results/eh_reference_design").status_code == 200

    r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "carry-b"})
    assert r.status_code == 200, r.text
    assert client.get("/api/network/undo/info").json()["network_revision"] == 0
    assert client.get("/api/results/eh_reference_design").status_code == 204, (
        "the template project inherited the previous project's stored report")
    meta = json.loads((project_storage_dir("carry-b") / "metadata.json").read_text())
    assert meta["network_revision"] == 0


def test_template_create_does_not_evict_early(client, monkeypatch):
    """Gate N-3: the created ctx was counted twice (scratch + its key) during
    the cap check, so one unrelated project was evicted a create early."""
    monkeypatch.setattr(PyPSAService, "RESIDENT_CAP", 3)
    evicted = []
    orig = PyPSAService._save_evicted_ctx

    def spy(vid, vctx):
        evicted.append(vctx.loaded_project)
        return orig(vid, vctx)

    monkeypatch.setattr(PyPSAService, "_save_evicted_ctx", staticmethod(spy))
    for i in range(3):
        r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": f"cap-{i}"})
        assert r.status_code == 200, r.text
    assert evicted == [], f"evicted below the cap: {evicted}"
    with PyPSAService._registry_lock:
        resident = {c.loaded_project for c in PyPSAService._contexts.values()}
    assert {"cap-0", "cap-1", "cap-2"} <= resident


def test_an_io_import_copies_the_undo_stack_rather_than_sharing_it(
        local_client, tmp_path, monkeypatch):
    """An io import keeps the open workspace's history undoable (a COPY of the
    stack, including the capture pushed before the import), but the draft's
    later edits never reach the project it replaced."""
    import pypsa

    from services import undo_service
    # Every write gets its own capture (no 0.5 s coalescing), so the depths
    # below are exact rather than timing-dependent.
    monkeypatch.setattr(undo_service, "claim_push_slot", lambda *a, **k: True)
    c = local_client
    assert c.post("/api/projects/from_template/eh_h2_hub", params={"name": "IA"}).status_code == 200
    xs0 = _xs(c)
    _move(c, 3.0)
    assert c.get("/api/network/undo/info").json()["depth"] == 1
    n = pypsa.Network()
    n.add("Bus", "imported", x=0.0, y=0.0)
    path = tmp_path / "in.nc"
    n.export_to_netcdf(path)
    r = c.post("/api/io/import/netcdf",
               files={"file": ("in.nc", path.read_bytes(), "application/x-netcdf")})
    assert r.status_code == 200, r.text
    assert _buses(c) == ["imported"]
    assert c.get("/api/network/undo/info").json()["depth"] == 2   # the import is undoable
    _move(c, 1.0)                                                  # an edit in the draft
    assert c.get("/api/network/undo/info").json()["depth"] == 3
    edited = None
    assert c.post("/api/projects/IA/activate").status_code == 200
    edited = _xs(c)
    # IA's own stack: its edit, plus the capture of IA itself that the
    # middleware pushed before the import replaced the workspace. The draft's
    # edit (depth 3 above) is not on it.
    assert c.get("/api/network/undo/info").json()["depth"] == 2, (
        "the imported draft's edits landed on project IA's undo stack")
    assert c.post("/api/network/undo").status_code == 200
    assert _xs(c) == edited and "imported" not in _buses(c)
    assert c.post("/api/network/undo").status_code == 200
    assert _xs(c) == xs0
    assert c.post("/api/network/undo").status_code == 409
