"""
R33/R35/R36 — a bounded pool, defaulting to one.

`services/solve_queue.py`'s docstring gave thread-unsafe HDF5 as the reason the
dispatcher ran strictly one job at a time. That was never the real protection:
`PyPSAService._netcdf_io_lock` is, and it is narrower than "one job at a time" —
it serialises the FILE I/O, not the solve. So more than one solve can run as
long as each has its own context and its own mutation lock, which
`build_context` guarantees by construction ("that distinctness IS the
concurrency").

`_current_id` was a singular slot, so `reset_for_tests` could reach exactly one
in-flight solve's stop event and the others would bleed into the next test —
the precise failure its docstring says it exists to prevent.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

from services.pypsa_service import PyPSAService
from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_the_default_is_one():
    import services.solve_queue as sq

    assert sq.MAX_CONCURRENT_SOLVES == int(
        os.environ.get("PYPSA_GUI_MAX_CONCURRENT_SOLVES", "1")
    )
    assert "PYPSA_GUI_MAX_CONCURRENT_SOLVES" in open(sq.__file__, encoding="utf-8").read()


def test_reset_for_tests_signals_every_in_flight_solve():
    """
    The singular `_current_id` could only reach one. With a pool, the others
    would keep solving into the next test.
    """
    solve_queue.reset_for_tests()
    events = []
    try:
        for _ in range(3):
            jid = uuid.uuid4()
            ev = threading.Event()
            events.append(ev)
            with solve_queue._lock:
                job = SolveJob(id=jid, project_id="Live", enqueued_at=0.0)
                job.status = "running"
                job.stop_event = ev
                solve_queue._jobs[jid] = job
                solve_queue._order.append(jid)
                solve_queue._running_ids.add(jid)

        solve_queue.reset_for_tests()

        assert all(ev.is_set() for ev in events), (
            "reset_for_tests reached only some of the in-flight solves"
        )
    finally:
        solve_queue.reset_for_tests()


def test_two_concurrent_jobs_share_no_context_and_no_mutation_lock(
    client, install_network, tmp_projects_dir, registry_key_for, monkeypatch,
):
    """R35 — and the netCDF I/O lock is still one shared instance."""
    import services.solve_queue as sq
    from services import solver_service

    monkeypatch.setattr(sq, "MAX_CONCURRENT_SOLVES", 2)
    solve_queue.reset_for_tests()
    solve_queue._dispatchers = []

    for name in ("Par1", "Par2"):
        install_network(build_network(), name=name)
        _save_project(client, name)

    seen: dict = {}
    both = threading.Barrier(2, timeout=90)

    def concurrent(config, n, lock, stop_event, log_queue, state_update=None):
        seen[id(n)] = lock
        both.wait()
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", concurrent)
    try:
        for name in ("Par1", "Par2"):
            r = client.post("/api/simulation/queue", json={"project_id": name})
            assert r.status_code == 200, r.text

        deadline = time.time() + 90
        while time.time() < deadline and len(seen) < 2:
            time.sleep(0.05)
        assert len(seen) == 2, "the two jobs never ran at the same time"

        networks = list(seen.keys())
        locks = list(seen.values())
        assert networks[0] != networks[1], "the two solves shared a network"
        assert locks[0] is not locks[1], "the two solves shared a mutation lock"
        assert (
            PyPSAService.get_netcdf_io_lock() is PyPSAService._netcdf_io_lock
        ), "the netCDF I/O lock is no longer a single shared instance"

        ctx1 = PyPSAService.get_context(registry_key_for("Par1"))
        ctx2 = PyPSAService.get_context(registry_key_for("Par2"))
        assert ctx1 is not None and ctx2 is not None
        assert ctx1 is not ctx2
    finally:
        solve_queue.reset_for_tests()


def test_every_running_jobs_context_is_protected_from_eviction(monkeypatch):
    """
    R36 — plural. The protected set already keys on `project_key`, so the fix is
    to prove it holds for MORE THAN ONE running job rather than only the one
    `_current_id` used to name.
    """
    monkeypatch.setattr(PyPSAService, "RESIDENT_CAP", 1)
    solve_queue.reset_for_tests()
    keys = ["orgA:one", "orgA:two"]
    try:
        for key in keys:
            jid = uuid.uuid4()
            with solve_queue._lock:
                job = SolveJob(id=jid, project_id=key.split(":")[1], project_key=key, enqueued_at=0.0)
                job.status = "running"
                solve_queue._jobs[jid] = job
                solve_queue._order.append(jid)
                solve_queue._running_ids.add(jid)
            PyPSAService._contexts[key] = PyPSAService.build_context()

        PyPSAService._evict_if_over_cap(protected_ids=set())

        for key in keys:
            assert PyPSAService.get_context(key) is not None, (
                f"{key} was evicted while its solve was running"
            )
    finally:
        for key in keys:
            PyPSAService._contexts.pop(key, None)
        solve_queue.reset_for_tests()


def test_a_freed_slot_wakes_a_worker_parked_on_the_cap(monkeypatch):
    """
    The admission-control `wait()` must have a matching `notify()` IN THE
    DISPATCHER'S OWN `finally`. Without it a parked worker never wakes and the
    job it popped never runs — a permanent stall, not a wrong answer.

    Reachable in this very suite: the concurrency test above monkeypatches the
    cap UP, `monkeypatch` restores it at teardown, and the dispatcher threads it
    spawned stay alive — leaving MORE live workers than the restored cap, which
    is exactly the state that reaches the wait.

    Drives the REAL `_dispatch_loop` (two live workers, cap of 1, two jobs) so
    it fails against a `finally` that discards without notifying. An earlier
    version of this test called `notify()` itself and passed pre-fix — it was
    testing `threading.Condition`, not this module.
    """
    import services.solve_queue as sq

    solve_queue.reset_for_tests()
    started: list = []
    release = threading.Event()
    first_running = threading.Event()

    def stub_run_job(job) -> None:
        started.append(job.id)
        if len(started) == 1:
            first_running.set()
            release.wait(timeout=30)      # hold slot until the test frees it
        with solve_queue._lock:
            job.status = "completed"

    monkeypatch.setattr(solve_queue, "_run_job", stub_run_job)
    try:
        # TWO live workers...
        monkeypatch.setattr(sq, "MAX_CONCURRENT_SOLVES", 2)
        with solve_queue._lock:
            solve_queue._dispatchers = []
            solve_queue._ensure_dispatcher_locked()
        # ...then a cap of ONE. More workers than slots is the state that parks
        # the second worker on `_slot_free.wait()`.
        monkeypatch.setattr(sq, "MAX_CONCURRENT_SOLVES", 1)

        first = solve_queue.enqueue("SlotOne")
        assert first_running.wait(timeout=30), "the first job never started"
        second = solve_queue.enqueue("SlotTwo")

        # The second worker is now parked on the cap: one slot, one occupant.
        time.sleep(0.2)
        assert len(started) == 1, f"the cap did not hold: {started}"

        release.set()   # first job finishes -> its `finally` must notify

        deadline = time.time() + 30
        while time.time() < deadline and len(started) < 2:
            time.sleep(0.05)
        assert len(started) == 2, (
            "the second job never started after a slot freed — the "
            "dispatcher's `finally` discarded without notifying the worker "
            "parked on the cap"
        )
        assert started == [first.id, second.id]
    finally:
        release.set()
        solve_queue.reset_for_tests()
