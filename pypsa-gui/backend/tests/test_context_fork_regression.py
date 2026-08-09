"""
D-1 regression: a project must never be forked into two ProjectContexts.

The dispatcher used to build its background context with
`PyPSAService.build_context()` and never register it, so `get_context(key)`
answered None for the whole solve and `POST /api/projects/{id}/activate`
happily built a SECOND context for the same project. The user then edited that
second copy and the first ordinary save — `switchToProject` fires
`saveProjectQuietly` on every tab switch — wrote it over the dispatch the queue
had just persisted. The solve results vanished with no error anywhere.

This file also pins the lock's own contract: exactly one builder per key, and
NO lock at all on the common path (a registry hit).
"""
from __future__ import annotations

import threading
import time

import pypsa

from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue
from tests.conftest import build_network


def _wait_until(pred, timeout: float = 60.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _wait_for_terminal(job_id, timeout: float = 90.0) -> dict:
    _wait_until(
        lambda: (solve_queue.get_job(job_id) or {}).get("status")
        in ("completed", "failed", "aborted", "interrupted"),
        timeout=timeout,
        interval=0.2,
    )
    return solve_queue.get_job(job_id)


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_hydrate_or_adopt_admits_exactly_one_builder_per_key():
    """
    Four threads miss the registry for the same key at once. Exactly one may
    build; the other three must adopt what it registered. Before the lock, all
    four built and the last `register` overwrote the other three in `_contexts`
    while at least one of them was already bound into a live request.
    """
    key = "probe-org:probe-race"
    built: list = []
    errors: list = []
    barrier = threading.Barrier(4)

    def racer() -> None:
        try:
            barrier.wait(10)
            with PyPSAService.hydrate_or_adopt(key) as resident:
                if resident is not None:
                    return
                ctx = PyPSAService.build_context()
                time.sleep(0.05)  # widen the window the lock exists to close
                built.append(ctx)
                PyPSAService.register(key, ctx)
        except Exception as exc:  # noqa: BLE001 — surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=racer) for _ in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert errors == [], errors
        assert len(built) == 1, f"{len(built)} contexts built for one registry key"
        assert PyPSAService.get_context(key) is built[0]
    finally:
        with PyPSAService._registry_lock:
            PyPSAService._contexts.pop(key, None)


def test_hydrate_or_adopt_takes_no_lock_on_a_registry_hit():
    """
    The common path is a hit, and it must stay lock-free: this nests two
    acquisitions of the SAME key on ONE thread. A plain `threading.Lock` taken
    on the fast path would deadlock here and the test would hang, not fail —
    which is exactly why the fast path returns before touching it.
    """
    key = "probe-org:resident"
    ctx = PyPSAService.build_context()
    PyPSAService.register(key, ctx)
    try:
        with PyPSAService.hydrate_or_adopt(key) as resident:
            assert resident is ctx
            with PyPSAService.hydrate_or_adopt(key) as again:
                assert again is ctx
    finally:
        with PyPSAService._registry_lock:
            PyPSAService._contexts.pop(key, None)


def test_activating_a_project_mid_queue_solve_does_not_fork_its_context(
    client, install_network, tmp_projects_dir, project_storage_dir,
    registry_key_for, session_ctx, monkeypatch,
):
    """
    The D-1 probe, end to end. X is saved and NOT resident; a queued solve is
    blocked inside `run_simulation`; the user activates X mid-solve.

    Before the fix: `get_context(key_X)` was None, activate built a second
    context, `/api/simulation/status` read `idle`, and one ordinary foreground
    save wiped the dispatch off disk.
    """
    from services import solver_service

    install_network(build_network(), name="X")
    _save_project(client, "X")
    key = registry_key_for("X")

    # Move the session off X and drop X from the registry so the dispatcher
    # takes the COLD path (build + hydrate), which is where the fork happened.
    install_network(build_network(), name="Y")
    _save_project(client, "Y")
    with PyPSAService._registry_lock:
        PyPSAService._contexts.pop(key, None)
    assert PyPSAService.get_context(key) is None

    entered = threading.Event()
    release = threading.Event()

    def blocking_run_simulation(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("probe: dispatcher entered run_simulation")
        entered.set()
        assert release.wait(60), "the probe never released the dispatcher"
        with lock:
            n.optimize(solver_name="highs")
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", blocking_run_simulation)

    job = client.post("/api/simulation/queue", json={"project_id": "X"}).json()
    assert entered.wait(60), "the dispatcher never reached run_simulation"

    # R3 — the dispatcher's context is registered under the key `get_context`
    # resolves, so it is no longer invisible.
    during = PyPSAService.get_context(key)
    assert during is not None, "the dispatcher's background context is not registered"

    # R4 — and it is marked as queue-owned.
    with during.solver_state_lock:
        assert during.solver_state.get("kind") == "queue"

    # R6 — activating X lands the session ON that context. No second context.
    r = client.post("/api/projects/X/activate")
    assert r.status_code == 200, r.text
    assert session_ctx(client) is during, "activate forked a SECOND context for X"
    assert PyPSAService.get_context(key) is during

    status = client.get("/api/simulation/status").json()
    assert status["status"] == "running", status

    # R6 — the log the session reads is THIS job's log. `/log_history` and
    # `/log_stream` bind to the same `_state["log_queue"]`; the history endpoint
    # asserts the binding without opening a stream that never ends.
    hist = client.get("/api/simulation/log_history").json()
    assert hist["running"] is True, hist
    assert any("dispatcher entered run_simulation" in line for line in hist["lines"]), hist

    release.set()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done

    # R7 — the activated context holds the results with no reload…
    assert not session_ctx(client).network.generators_t.p.empty

    # …and an ordinary foreground save does not remove dispatch from disk.
    r = client.post("/api/projects/X", params={"expect": "X"})
    assert r.status_code == 200, r.text
    on_disk = pypsa.Network(str(project_storage_dir("X") / "network.nc"))
    assert not on_disk.generators_t.p.empty, "a foreground save wiped the solve results"
