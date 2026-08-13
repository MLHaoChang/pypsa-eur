"""
D-1 regression: a project must never be forked into two ProjectContexts.

The dispatcher used to build its background context with
`PyPSAService.build_context()` and never register it, so `get_context(key)`
answered None for the whole solve and `POST /api/projects/{id}/activate`
happily built a SECOND context for the same project. The user then edited that
second copy and the first ordinary save — `switchToProject` fires
`saveProjectQuietly` on every tab switch — wrote it over the dispatch the queue
had just persisted. The solve results vanished with no error anywhere.

This file also pins the lock's own contract: exactly one builder per key, NO
lock at all on the common path (a registry hit), the per-key entry is pruned
once the last waiter leaves (it must not grow forever on a long-lived
process), and a raise mid-body leaves neither a held lock nor a poisoned key.
"""
from __future__ import annotations

import threading
import time
import uuid

import pypsa
import pytest

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
    job_id = uuid.UUID(str(job_id))
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


def test_hydrate_or_adopt_prunes_its_lock_entry_when_the_last_waiter_leaves():
    """
    `_hydrate_locks` must not grow forever. Every miss registers a per-key
    entry; the ONLY safe place to remove it is the last waiter still using it,
    inside `hydrate_or_adopt` itself. Without pruning, a long-lived server
    process pins one permanent entry per registry key ever missed — and Task 2
    routes the per-session `scratch:<id>` key through this same helper, so
    every session ever created would leak one.
    """
    key = "probe-org:prune-me"
    assert key not in PyPSAService._hydrate_locks
    try:
        with PyPSAService.hydrate_or_adopt(key) as resident:
            assert resident is None
            # Mid-body: this thread is the sole waiter, so the entry must exist.
            assert key in PyPSAService._hydrate_locks
            ctx = PyPSAService.build_context()
            PyPSAService.register(key, ctx)
        assert key not in PyPSAService._hydrate_locks, (
            "the per-key lock entry was never pruned after the last waiter left"
        )
    finally:
        with PyPSAService._registry_lock:
            PyPSAService._contexts.pop(key, None)


def test_hydrate_or_adopt_leaves_no_lock_held_and_no_key_poisoned_after_a_raise():
    """
    A caller whose build/hydrate/register step raises must not leave the
    per-key lock held or its `_hydrate_locks` entry behind — either would
    deadlock or permanently skip adoption for every future miss on that key.
    """
    key = "probe-org:raises"
    assert key not in PyPSAService._hydrate_locks
    with pytest.raises(RuntimeError, match="boom"):
        with PyPSAService.hydrate_or_adopt(key) as resident:
            assert resident is None
            raise RuntimeError("boom")
    assert key not in PyPSAService._hydrate_locks, "a raise left the key's lock entry behind"

    # The key is neither poisoned nor deadlocked: a fresh miss proceeds normally.
    with PyPSAService.hydrate_or_adopt(key) as resident:
        assert resident is None
    assert key not in PyPSAService._hydrate_locks


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


def test_loading_a_project_mid_queue_solve_is_refused_and_does_not_fork_it(
    client, install_network, tmp_projects_dir, project_storage_dir,
    registry_key_for, session_ctx, monkeypatch,
):
    """
    The FIFTH registration path — `GET /api/projects/{name}`.

    `load_project` builds nothing, so it satisfies R2 as literally worded, but
    it ends by re-registering the CALLER'S OWN context under the target's
    registry key. Driven at a project the dispatcher is mid-solve on, that one
    line reproduces D-1 exactly: the dispatcher keeps solving (and, at
    completion, keeps SAVING) a context `get_context` can no longer resolve,
    while the session's freshly-loaded copy — read from the PRE-solve file —
    takes the registry slot and writes over the results on its next save.

    It is reachable: `GET` is a read method, so the `/api/projects/`
    solver-in-flight gate in `main.py` does not cover it, and the
    `load_project` chat tool and the clone wizard both drive it at an
    ARBITRARY project rather than the current one.

    The route now refuses instead (409), which is also the only safe answer
    for the clone flow — `load(src)` there is immediately followed by
    `save(dest, rebind=true)`, so handing back the live solving context would
    re-key it mid-solve.
    """
    from services import solver_service

    install_network(build_network(), name="X")
    _save_project(client, "X")
    key = registry_key_for("X")

    # Move the session off X and drop X from the registry so the dispatcher
    # takes the COLD path (build + hydrate + register).
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

    during = PyPSAService.get_context(key)
    assert during is not None, "the dispatcher's background context is not registered"
    session_before = session_ctx(client)
    bound_before = session_before.loaded_project

    # THE PROBE. Before the fix this returned 200 and replaced `_contexts[key]`
    # with the session's own context.
    r = client.get("/api/projects/X")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == "solver_in_flight", r.text

    # The dispatcher still owns the project's ONE context…
    assert PyPSAService.get_context(key) is during, "load forked a SECOND context for X"
    # …and the refusal is not half a load: the caller's own context is neither
    # reset nor re-bound, so a refused load costs the session nothing.
    assert session_ctx(client) is session_before
    assert session_before.loaded_project == bound_before

    release.set()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done

    # The results the refusal protected are on disk…
    on_disk = pypsa.Network(str(project_storage_dir("X") / "network.nc"))
    assert not on_disk.generators_t.p.empty, "the queue solve never reached disk"

    # …and the gate is temporary: once the job is terminal the load succeeds
    # again, registers ITS context under the same key, and reads back the
    # solved network rather than the pre-solve file the refusal protected.
    r = client.get("/api/projects/X")
    assert r.status_code == 200, r.text
    reloaded = PyPSAService.get_context(key)
    assert reloaded is not None and reloaded.loaded_project == "X"
    assert not reloaded.network.generators_t.p.empty
