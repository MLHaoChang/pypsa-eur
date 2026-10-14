"""
Network-replacing routes must be what the NEXT request reads while the session
has a project active (OPEN-ITEMS item 19, "a network reset while a project is open
lands in the session's scratch slot"; numbered 12 until 2026-10-13).

`reset_network()` publishes a fresh UNBOUND context into the session's
`scratch:<sid>` slot, but `resolve_for_session` resolves the next request by
`sessions.active_project_id`. Unless a route moves the pointer (raw import,
`routers/io.py::_unbind_session`) or moves the re-bound context into the
project's slot (`rekey_context`: undo, snapshot restore, and
`_register_created_context` for template create and bundle import), its work
lands in a context no later request reads, and a bound copy left in scratch is
written back over the project when evicted.

Written 2026-09-30 beside `notes/2026-09-29-3d-site-view-phase2-qa.md` §7
(defects A-C), red on that tree; landed 2026-10-13, when every route was
already fixed on master. Each test was mutation-checked then: removing the
matching `rekey_context` call (or the solver-in-flight gate, for the last
test) turns it red. Every other test of these routes runs after
`install_network`, which clears every session pointer, i.e. on the scratch
path, where slot == pointer target.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from tests.conftest import build_network


def _buses(client) -> list[str]:
    r = client.get("/api/network/buses")
    assert r.status_code == 200, r.text
    return sorted(b["name"] for b in r.json())


def _four_bus_nc(tmp_path):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    for i in range(4):
        n.add("Bus", f"X{i}", v_nom=20.0)
    p = tmp_path / "campus.nc"
    n.export_to_netcdf(str(p))
    return p


@pytest.fixture
def active_alpha(client, api_project):
    name = api_project("alpha")               # 1 bus: B1
    r = client.post(f"/api/projects/{name}/activate")
    assert r.status_code == 200, r.text
    assert _buses(client) == ["B1"]
    return name


def test_raw_netcdf_import_is_what_the_next_request_reads(client, active_alpha, tmp_path):
    nc = _four_bus_nc(tmp_path)
    with nc.open("rb") as f:
        r = client.post("/api/io/import/netcdf",
                        files={"file": ("campus.nc", f, "application/x-netcdf")})
    assert r.status_code == 200, r.text
    assert r.json()["buses"] == 4

    assert _buses(client) == ["X0", "X1", "X2", "X3"], (
        "import landed in scratch:<sid> but the pointer still names alpha")
    meta = client.get("/api/network/meta").json()
    assert not meta.get("loaded_project"), meta   # a raw import is UNBOUND

    # NewProjectWizard "Import from disk": import, then save-as a new name.
    r = client.post("/api/projects/campus", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    r = client.get("/api/projects/campus")          # load from disk
    assert r.status_code == 200, r.text
    assert r.json()["buses"] == 4, "save-as persisted the previous project's network"

    # Isolation: the project the user was on is untouched in memory and on disk.
    assert client.post(f"/api/projects/{active_alpha}/activate").status_code == 200
    assert _buses(client) == ["B1"]


def test_undo_with_project_active_reverts_the_edit(client, active_alpha, registry_key_for):
    from services.pypsa_service import PyPSAService

    r = client.post("/api/network/loads", json={"name": "Lx", "bus": "B1", "p_set": 5.0})
    assert r.status_code == 201, r.text
    assert client.get("/api/network/undo/info").json()["depth"] == 1

    r = client.post("/api/network/undo")
    assert r.status_code == 200, r.text
    assert r.json() == {"undone": True, "remaining": 0}

    loads = sorted(x["name"] for x in client.get("/api/network/loads").json())
    assert "Lx" not in loads, "undo restored into scratch:<sid>, not alpha's slot"
    meta = client.get("/api/network/meta").json()
    assert meta.get("loaded_project") == active_alpha, meta   # identity survives

    # No second context bound to alpha left anywhere in the registry.
    key = registry_key_for(active_alpha)
    bound = [k for k, c in PyPSAService._contexts.items() if c.registry_key == key]
    assert bound == [key], bound


def test_snapshot_restore_with_project_active_is_what_the_next_request_reads(
        client, active_alpha):
    r = client.post(f"/api/projects/{active_alpha}/snapshots", json={"label": "v1"})
    assert r.status_code in (200, 201), r.text
    snap = r.json()["id"]
    r = client.post("/api/network/buses", json={"name": "B2", "v_nom": 380.0, "carrier": "AC"})
    assert r.status_code in (200, 201), r.text
    assert _buses(client) == ["B1", "B2"]

    r = client.post(f"/api/projects/{active_alpha}/snapshots/{snap}/restore")
    assert r.status_code == 200, r.text
    assert _buses(client) == ["B1"], "restore landed in scratch:<sid>, not alpha's slot"


# ── Bound "ghost" contexts left in scratch:<sid> ────────────────────────────
# A route that binds the reset ctx to a project but leaves it in the scratch
# slot creates a second context bound to that project. The scratch slot is not
# eviction-protected while the session points at a project, and eviction is
# write-back, so the ghost's stale network is saved over the project's files.

def _evict_everything_unprotected(monkeypatch):
    from services.pypsa_service import PyPSAService
    monkeypatch.setattr(PyPSAService, "RESIDENT_CAP", 0)
    PyPSAService._evict_if_over_cap()


def test_undo_leaves_no_ghost_that_eviction_writes_back(client, active_alpha, monkeypatch):
    assert client.post("/api/network/loads",
                       json={"name": "Lx", "bus": "B1", "p_set": 5.0}).status_code == 201
    assert client.post("/api/network/undo").status_code == 200
    r = client.post("/api/network/buses", json={"name": "B9", "v_nom": 380.0, "carrier": "AC"})
    assert r.status_code in (200, 201), r.text
    r = client.post(f"/api/projects/{active_alpha}", params={"expect": active_alpha})
    assert r.status_code == 200, r.text
    _evict_everything_unprotected(monkeypatch)
    assert client.get(f"/api/projects/{active_alpha}").status_code == 200   # reload from disk
    assert _buses(client) == ["B1", "B9"], "a ghost ctx was written back over the saved project"


def test_template_create_leaves_no_ghost_that_eviction_writes_back(
        client, active_alpha, tmp_path, monkeypatch, registry_key_for):
    from routers import projects as projects_router
    from services.pypsa_service import PyPSAService

    d = tmp_path / "3bus"
    d.mkdir()
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=2, freq="h"))
    n.add("Bus", "T1")
    n.export_to_netcdf(str(d / "network.nc"))
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)

    r = client.post("/api/projects/from_template/3bus", params={"name": "tpl"})
    assert r.status_code == 200, r.text
    assert _buses(client) == ["T1"]
    key = registry_key_for("tpl")
    bound = [k for k, c in PyPSAService._contexts.items() if c.registry_key == key]
    assert bound == [key], bound

    r = client.post("/api/network/buses", json={"name": "T9", "v_nom": 380.0, "carrier": "AC"})
    assert r.status_code in (200, 201), r.text
    assert client.post("/api/projects/tpl", params={"expect": "tpl"}).status_code == 200
    _evict_everything_unprotected(monkeypatch)
    assert client.get("/api/projects/tpl").status_code == 200
    assert _buses(client) == ["T1", "T9"], "a ghost ctx was written back over the saved project"


# ── Undo and snapshot restore during a QUEUE solve of the active project ────

def test_undo_and_restore_are_refused_mid_queue_solve(
    client, install_network, tmp_projects_dir, registry_key_for, session_ctx,
    monkeypatch,
):
    import threading

    from services import solver_service
    from tests.test_context_fork_regression import _save_project, _wait_for_terminal

    install_network(build_network(), name="X")
    _save_project(client, "X")
    snap = client.post("/api/projects/X/snapshots", json={"label": "v1"}).json()["id"]
    assert client.post("/api/projects/X/activate").status_code == 200
    r = client.post("/api/network/buses", json={"name": "B2", "v_nom": 380.0, "carrier": "AC"})
    assert r.status_code in (200, 201), r.text
    assert client.get("/api/network/undo/info").json()["depth"] >= 1

    entered, release = threading.Event(), threading.Event()

    def blocking_run_simulation(config, n, lock, stop_event, log_queue, state_update=None):
        entered.set()
        assert release.wait(60)
        with lock:
            n.optimize(solver_name="highs")
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", blocking_run_simulation)
    job = client.post("/api/simulation/queue", json={"project_id": "X"}).json()
    try:
        assert entered.wait(60), "the dispatcher never reached run_simulation"
        before = _buses(client)
        r_undo = client.post("/api/network/undo")
        r_rst = client.post(f"/api/projects/X/snapshots/{snap}/restore")
        after = _buses(client)
    finally:
        release.set()
        _wait_for_terminal(job["id"])
    assert r_undo.status_code == 409, f"undo mid-queue-solve: {r_undo.status_code} {r_undo.text[:200]}"
    assert r_rst.status_code == 409, f"restore mid-queue-solve: {r_rst.status_code} {r_rst.text[:200]}"
    assert after == before


def test_bundle_import_leaves_no_ghost_that_eviction_writes_back(
        client, active_alpha, api_project, monkeypatch, registry_key_for):
    from services.pypsa_service import PyPSAService

    src = api_project("bsrc")                    # 1 bus: B1
    bundle = client.get(f"/api/projects/{src}/bundle").content
    assert client.post(f"/api/projects/{active_alpha}/activate").status_code == 200

    r = client.post("/api/projects/import_bundle",
                    files={"file": ("b.zip", bundle, "application/zip")},
                    params={"name": "bimp"})
    assert r.status_code == 200, r.text
    key = registry_key_for("bimp")
    bound = [k for k, c in PyPSAService._contexts.items() if c.registry_key == key]
    assert bound == [key], bound

    r = client.post("/api/network/buses", json={"name": "B9", "v_nom": 380.0, "carrier": "AC"})
    assert r.status_code in (200, 201), r.text
    assert client.post("/api/projects/bimp", params={"expect": "bimp"}).status_code == 200
    _evict_everything_unprotected(monkeypatch)
    assert client.get("/api/projects/bimp").status_code == 200
    assert _buses(client) == ["B1", "B9"], "a ghost ctx was written back over the saved project"
