"""
B8 — instant in-memory project switch via `POST /api/projects/{id}/activate`.

The activate endpoint makes `id` the ACTIVE context with NO destructive
round-trip when the project is already resident (pure pointer swap under
`_registry_lock`), and a build-hydrate-register fallback the first time a saved
project is touched. These tests prove:
  * activating a RESIDENT project flips `get_active_id()` + `get_network()`;
  * activating a NON-resident saved project loads it AND makes it resident;
  * unknown id → 404, traversing id → 400;
  * a foreground solve in flight blocks the switch with 409;
  * `load_project` registers its bound ctx (so a later switch is instant).

Mirrors test_path_scoped_reads.py's on-disk setup: a POST to
`/api/projects/{name}` is a destructive SAVE of the active network (see
CLAUDE.md), so to put a project on disk WITHOUT leaving it resident we save it
while live, then install a different network, then drop the registry.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from routers import simulation as sim_router
from services.pypsa_service import PyPSAService


def _bus_network(bus_name: str) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", bus_name)
    n.add("Load", "L1", bus=bus_name, p_set=100.0)
    n.add("Generator", "gas", bus=bus_name, carrier="gas",
          p_nom=200.0, marginal_cost=50.0)
    return n


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _put_A_and_B_on_disk(client, install_network):
    """
    Two saved projects with distinct buses; leaves A as the active foreground
    and B on disk only (not resident in _contexts). Returns (A_bus, B_bus).
    """
    install_network(_bus_network("B_BUS"), name="B")
    _save_project(client, "B")
    install_network(_bus_network("A_BUS"), name="A")
    _save_project(client, "A")
    return "A_BUS", "B_BUS"


def test_activate_resident_swaps_active(client, install_network, tmp_projects_dir):
    _put_A_and_B_on_disk(client, install_network)
    # A path-scoped read makes B resident WITHOUT moving the active slot.
    client.get("/api/projects/B/network/buses")
    assert PyPSAService.get_context("B") is not None
    assert PyPSAService.get_active_id() == "A"
    ctx_b = PyPSAService.get_context("B")

    r = client.post("/api/projects/B/activate")
    assert r.status_code == 200, r.text
    # B9: the response carries an `evicted` list (empty here — well below cap).
    assert r.json() == {"activated": "B", "evicted": []}

    # Active flipped to B; the swap reused the SAME resident ctx (no re-hydrate).
    assert PyPSAService.get_active_id() == "B"
    assert PyPSAService.get_active_context() is ctx_b
    assert PyPSAService.get_network() is ctx_b.network
    assert [b["name"] for b in client.get("/api/network/buses").json()] == ["B_BUS"]


def test_activate_non_resident_loads_and_registers(
    client, install_network, tmp_projects_dir
):
    _put_A_and_B_on_disk(client, install_network)
    # B is on disk but NOT resident (save doesn't register into _contexts).
    assert PyPSAService.get_context("B") is None
    assert PyPSAService.get_active_id() == "A"

    r = client.post("/api/projects/B/activate")
    assert r.status_code == 200, r.text

    # B is now active AND resident (built + hydrated + registered).
    assert PyPSAService.get_active_id() == "B"
    assert PyPSAService.get_context("B") is not None
    assert PyPSAService.get_context("B").loaded_project == "B"
    assert [b["name"] for b in client.get("/api/network/buses").json()] == ["B_BUS"]


def test_activate_unknown_returns_404(client, install_network, tmp_projects_dir):
    install_network(_bus_network("A_BUS"), name="A")
    _save_project(client, "A")
    r = client.post("/api/projects/DoesNotExist/activate")
    assert r.status_code == 404, r.text


def test_activate_bad_id_returns_400(client, install_network, tmp_projects_dir):
    install_network(_bus_network("A_BUS"), name="A")
    _save_project(client, "A")
    # `%24evil` decodes to `$evil` — one path segment, fails the name regex → 400.
    r = client.post("/api/projects/%24evil/activate")
    assert r.status_code == 400, r.text


def test_activate_blocked_by_foreground_solve(
    client, install_network, tmp_projects_dir, monkeypatch
):
    _put_A_and_B_on_disk(client, install_network)
    assert PyPSAService.get_active_id() == "A"
    # Simulate a live foreground solve on the active (A) ctx by parking a real,
    # alive thread handle in its solver_state — `_solver_in_flight()` reads the
    # active ctx's thread under its lock and calls `.is_alive()`.
    import threading

    started = threading.Event()
    release = threading.Event()

    def _spin():
        started.set()
        release.wait(timeout=5)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    started.wait(timeout=5)
    sim_router._state_update(thread=t)
    try:
        assert sim_router._solver_in_flight() is True
        r = client.post("/api/projects/B/activate")
        assert r.status_code == 409, r.text
        # The block left the active slot untouched.
        assert PyPSAService.get_active_id() == "A"
    finally:
        release.set()
        t.join(timeout=5)


def test_activate_allowed_when_active_solve_is_a_queue_job(
    client, install_network, tmp_projects_dir, monkeypatch
):
    """
    Switching AWAY from a QUEUE-dispatcher solve of the active project is allowed
    (it keeps solving in the background on its captured ctx — B4.3). Only a legacy
    foreground /run blocks the switch. Here the same in-flight-thread state is
    present, but a RUNNING queue job for the active project makes the switch safe.
    """
    _put_A_and_B_on_disk(client, install_network)
    assert PyPSAService.get_active_id() == "A"

    import threading

    started = threading.Event()
    release = threading.Event()

    def _spin():
        started.set()
        release.wait(timeout=5)

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    started.wait(timeout=5)
    sim_router._state_update(thread=t)

    # Report a RUNNING queue job for the active project A — this is the signal
    # the activate guard uses to permit switching away from a queue solve.
    from services import solve_queue as sq_mod
    monkeypatch.setattr(
        sq_mod.solve_queue, "list_jobs",
        lambda: [{"project_id": "A", "status": "running"}],
    )
    try:
        assert sim_router._solver_in_flight() is True
        r = client.post("/api/projects/B/activate")
        assert r.status_code == 200, r.text  # allowed — A solves in background
        assert PyPSAService.get_active_id() == "B"
    finally:
        release.set()
        t.join(timeout=5)


def test_load_project_registers_bound_ctx(
    client, install_network, tmp_projects_dir
):
    """
    A foreground load (GET /{name}) makes the project resident for instant
    switching next time (B8 / B-2).
    """
    install_network(_bus_network("X_BUS"), name="X")
    _save_project(client, "X")
    # Drop the registry so the only way X becomes resident is load registering it.
    PyPSAService._contexts.clear()
    assert PyPSAService.get_context("X") is None

    r = client.get("/api/projects/X")
    assert r.status_code == 200, r.text

    ctx = PyPSAService.get_context("X")
    assert ctx is not None
    assert ctx.loaded_project == "X"
    # The registered ctx IS the active one (load binds the active ctx to X).
    assert ctx is PyPSAService.get_active_context()
