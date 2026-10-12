"""Network-replacing routes must be visible to the NEXT request when the
session has a project active (auth mode, per-session contexts).

`reset_network()` publishes a fresh UNBOUND context into the session's
`scratch:<sid>` slot (`_publish_active`), but `resolve_for_session` resolves
the next request by `sessions.active_project_id` -> the project's registry
slot. Unless the route moves the pointer to scratch (raw import) or moves the
re-bound context back into the project's slot (undo, snapshot restore), the
work lands in a context no later request reads.

Every other test of these routes runs after `install_network`, which clears
every session pointer, i.e. on the scratch path, where slot == pointer target.
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
