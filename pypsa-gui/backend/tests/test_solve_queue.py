"""
Integration tests for the sequential multi-project solve queue (Phase A / C1).

These drive REAL HiGHS solves through the dispatcher
(`services/solve_queue.py`) — the swap-based pipeline load_project ->
run_simulation -> save_project, run in the background dispatcher thread. The
happy-path test asserts results are persisted to the project's network.nc on
disk (the whole point of the queue: results survive without the project being
foreground). The abort test blocks the first job in `load_project` so the
second is deterministically still queued, then cancels it.

Run with the pixi env python:
    pixi run python -m pytest pypsa-gui/backend/tests/test_solve_queue.py
"""
from __future__ import annotations

import threading
import time
import uuid

import pypsa

from tests.conftest import build_network
from services.pypsa_service import PyPSAService
from services.solve_queue import solve_queue


def _wait_until(pred, timeout: float = 60.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _wait_for_terminal(job_id, timeout: float = 90.0) -> dict:
    # `job_id` is usually a JSON-echoed string (`some_job["id"]`); `_jobs` is
    # UUID-keyed, so it must be parsed before it can find anything.
    job_id = uuid.UUID(str(job_id))
    _wait_until(
        lambda: (solve_queue.get_job(job_id) or {}).get("status")
        in ("completed", "failed", "aborted"),
        timeout=timeout,
        interval=0.2,
    )
    return solve_queue.get_job(job_id)


def _save_project(client, name: str) -> None:
    """Persist the live network as a fresh on-disk project."""
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_enqueue_solves_and_persists(
    client, install_network, tmp_projects_dir, project_storage_dir, session_ctx
):
    # A saved, feasible single-bus project on disk. Storage is org-scoped since
    # Step 0a — `projects_root/<org>/<uuid>/`, not the flat `projects/<name>/`.
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    assert (project_storage_dir("P1") / "network.nc").exists()

    # Enqueue -> dispatcher loads from disk, solves, saves back.
    r = client.post("/api/simulation/queue", json={"project_id": "P1"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] in ("queued", "running")
    assert job["project_id"] == "P1"

    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done
    assert isinstance(done["objective"], (int, float)), done
    assert done["objective"] == done["objective"]  # not NaN
    assert done["solve_time"] is not None

    # The headline guarantee: LOPF dispatch persisted to disk without the
    # project ever being saved by a foreground action.
    n = pypsa.Network(str(project_storage_dir("P1") / "network.nc"))
    assert not n.generators_t.p.empty, "solved dispatch was not persisted to network.nc"
    assert n.generators_t.p.to_numpy().sum() > 0


def test_enqueue_nonexistent_project_404(client, tmp_projects_dir, session_ctx):
    r = client.post("/api/simulation/queue", json={"project_id": "NoSuchProject"})
    assert r.status_code == 404, r.text
    assert solve_queue.list_jobs() == []


def test_fifo_two_distinct_projects_start_in_enqueue_order(
    client, install_network, tmp_projects_dir, session_ctx
):
    """
    FIFO ordering across two DISTINCT projects. Task 8's idempotent enqueue
    made "two jobs of the SAME project" impossible via this route (the
    second POST now returns the first job with `already_queued: true`), so
    the two-project shape is the meaningful FIFO invariant left to assert
    here — same project-setup shape as
    `test_two_independent_projects_each_persist_own_results` below, which
    covers persistence; this one is specifically about ORDER.

    The dispatcher is ONE thread draining ONE `queue.Queue` (`self._q`,
    `services/solve_queue.py`) strictly FIFO, and each job runs to
    completion before the next is popped — so enqueue order must equal
    start order: job A (enqueued first over a synchronous `TestClient` call,
    so strictly before B) starts no later than job B, and B's `started_at`
    is only set after A's ENTIRE `_run_job` (hydrate + solve + save)
    returns. This is not a race: swap `self._q` for a `queue.LifoQueue` (or
    otherwise pop the more-recently-enqueued job first) and B starts before
    A, flipping the inequality below — verified by temporarily doing exactly
    that monkeypatch during development; see task-8-report.md.
    """
    install_network(build_network(gens_weight=1.0), name="P1")
    _save_project(client, "P1")
    install_network(build_network(gens_weight=3.0), name="P2")
    _save_project(client, "P2")

    a = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    b = client.post("/api/simulation/queue", json={"project_id": "P2"}).json()

    da = _wait_for_terminal(a["id"])
    db = _wait_for_terminal(b["id"])
    assert da["status"] == "completed", da
    assert db["status"] == "completed", db
    assert da["started_at"] <= db["started_at"]


def test_abort_queued_job_is_skipped(client, install_network, tmp_projects_dir, monkeypatch, session_ctx):
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    # Move the foreground OFF P1 so the queued P1 jobs take the BACKGROUND path
    # (project_id != active id) and hydrate from disk — that hydrate is the
    # deterministic block point below.
    install_network(build_network(), name="Foreground")

    # Block the FIRST job inside _hydrate_context_from_disk so the SECOND stays
    # queued deterministically (no reliance on solve timing). The dispatcher
    # imports the helper lazily per job, so patching the module attribute takes
    # effect.
    import routers.projects as pr

    real_hydrate = pr._hydrate_context_from_disk
    gate = threading.Event()

    def blocking_hydrate(ctx, src, name):
        gate.wait(timeout=30)
        return real_hydrate(ctx, src, name)

    monkeypatch.setattr(pr, "_hydrate_context_from_disk", blocking_hydrate)

    a = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    # A second HTTP enqueue of the SAME project while one is active is now
    # refused (idempotent enqueue, R15/R16: it returns job A again with
    # `already_queued: true` instead of creating a job B). This test's subject
    # is abort()'s queued-vs-running handling, not dedup, so build job B
    # directly through the raw `enqueue()` — deliberately left unguarded for
    # exactly this: the harness constructing a queue state directly
    # (`services/solve_queue.py::enqueue_unique` docstring). Carry job A's
    # `project_key` over so B is abortable through the HTTP route exactly like
    # a router-enqueued job would be — an unkeyed job fails `_may_abort` closed
    # outside local mode.
    b_id = solve_queue.enqueue("P1", project_key=a["project_key"]).id

    # Job A is now running (blocked in hydrate); job B must be queued behind it.
    _wait_until(lambda: solve_queue.get_job(uuid.UUID(str(a["id"])))["status"] == "running")
    assert solve_queue.get_job(b_id)["status"] == "queued"

    # Cancel the queued job B.
    r = client.post(f"/api/simulation/queue/{b_id}/abort")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "aborted"

    # Release A; it completes. B was cancelled -> skipped on pop, stays aborted.
    gate.set()
    da = _wait_for_terminal(a["id"])
    assert da["status"] == "completed", da
    assert solve_queue.get_job(b_id)["status"] == "aborted"

    # clear_finished drops both terminal jobs — but the ROUTE is super-admin
    # only since P-1 (the queue is process-global, so a clear crosses every
    # org), and the seeded user is an ORG admin. The service call is what this
    # test is really about: both jobs reached a terminal state and are
    # droppable. `test_solve_queue_authz.py` owns the HTTP gate itself.
    assert client.post("/api/simulation/queue/clear_finished").status_code == 403
    assert solve_queue.clear_finished() == 2
    assert solve_queue.list_jobs() == []


def _slow_network() -> pypsa.Network:
    """
    A network big enough that HiGHS spends a couple of seconds INSIDE the native
    `n.optimize()` call — so a mid-solve abort is exercised (not a cooperative
    pre-LP checkpoint). Many non-extendable gens × many snapshots = a fat LP.
    """
    import numpy as np
    import pandas as pd

    n = pypsa.Network()
    T = 4000
    n.set_snapshots(pd.date_range("2025-01-01", periods=T, freq="h"))
    for c in ("AC", "gas", "wind"):
        n.add("Carrier", c)
    n.add("Bus", "B1", carrier="AC")
    rng = np.random.default_rng(7)
    n.add("Load", "L1", bus="B1", p_set=100 + 80 * rng.random(T))
    for i in range(90):
        n.add("Generator", f"g{i}", bus="B1", carrier="gas",
              p_nom=500.0, marginal_cost=float(10 + i), p_max_pu=rng.random(T))
        n.add("Generator", f"e{i}", bus="B1", carrier="wind",
              p_nom_extendable=True, p_nom_max=2000.0,
              capital_cost=float(1e5 + i), marginal_cost=float(5 + i),
              p_max_pu=rng.random(T))
    return n


def test_abort_running_solve_is_fast_and_next_job_starts(
    client, install_network, tmp_projects_dir, session_ctx
):
    """
    The headline fix: aborting a RUNNING (mid-native-solve) queue job cancels
    HiGHS within ~1s (via the KeyboardInterrupt injection), the job ends
    `aborted`, and the NEXT queued job then runs to completion automatically.

    Pre-fix, abort waited for the full `n.optimize()` to finish (minutes on a
    real LP) and the single-threaded dispatcher couldn't start the next job
    until then.
    """
    # P_SLOW is a fat LP (seconds in native solve); P_FAST is the tiny default.
    install_network(_slow_network(), name="P_SLOW")
    _save_project(client, "P_SLOW")
    install_network(build_network(), name="P_FAST")
    _save_project(client, "P_FAST")
    # Move the foreground OFF both so each takes the BACKGROUND dispatcher path.
    install_network(build_network(), name="Foreground")

    a = client.post("/api/simulation/queue", json={"project_id": "P_SLOW"}).json()
    b = client.post("/api/simulation/queue", json={"project_id": "P_FAST"}).json()

    # Wait until P_SLOW is actually running AND has progressed past hydration +
    # modelling-assumptions into the native solve (so the abort lands mid-LP).
    _wait_until(
        lambda: solve_queue.get_job(uuid.UUID(str(a["id"])))["status"] == "running",
        timeout=30,
    )
    assert solve_queue.get_job(uuid.UUID(str(b["id"])))["status"] == "queued"
    time.sleep(3.0)  # let HiGHS get into the native solve window

    # Abort the RUNNING job. Must end `aborted` quickly (interrupt, not wait-out).
    t0 = time.time()
    r = client.post(f"/api/simulation/queue/{a['id']}/abort")
    assert r.status_code == 200, r.text
    da = _wait_for_terminal(a["id"], timeout=20)
    abort_latency = time.time() - t0
    assert da["status"] == "aborted", da
    # Generous bound for CI; the mechanism aborts in ~0.1-1s, NOT the minutes a
    # full solve-to-completion would take. 15s catches a regression to wait-out.
    assert abort_latency < 15.0, f"abort took {abort_latency:.1f}s — interrupt not working"

    # The next queued job auto-starts and completes (dispatcher freed promptly).
    db = _wait_for_terminal(b["id"], timeout=60)
    assert db["status"] == "completed", db
    assert isinstance(db["objective"], (int, float)), db


def test_enqueue_unsafe_name_is_404(client, tmp_projects_dir, session_ctx):
    """
    Was `..._is_400`; Step 0a changed the contract deliberately.

    Enqueue used to resolve through `_safe_project_dir`, whose name regex
    rejected traversal tokens with 400 before asking whose project it was — and
    accepted any WELL-FORMED name, including another org's, then handed the
    resolved path to a background thread that solves and SAVES it. Resolution
    now runs through the caller's org and ACL, so hostile and absent ids are
    one indistinguishable 404 and nothing is queued either way.
    """
    r = client.post("/api/simulation/queue", json={"project_id": "../evil"})
    assert r.status_code == 404, r.text
    assert solve_queue.list_jobs() == []


def test_abort_unknown_job_is_404(client, session_ctx):
    r = client.post("/api/simulation/queue/99999/abort")
    assert r.status_code == 404, r.text


def test_disowned_queue_job_reports_superseded(
    client, install_network, tmp_projects_dir, monkeypatch, session_ctx
):
    # When a newer run claims a job's ctx mid-solve (its solver_state["thread"]
    # changes away from this worker), the dispatcher must record the job as
    # SUPERSEDED — distinct from a user abort, and not leave the stale solve
    # condition under status="aborted". Simulate the takeover by stubbing
    # run_simulation to overwrite its own ctx thread via the state_update sink.
    install_network(build_network(), name="P1")
    _save_project(client, "P1")

    import services.solver_service as ss

    def _disowning_stub(config, n, lock, stop_event, log_queue, state_update=None, **kw):
        # A successor "claims" this ctx by setting a different thread handle.
        if state_update is not None:
            state_update(thread="successor-sentinel")
        return "ok", "optimal"

    monkeypatch.setattr(ss, "run_simulation", _disowning_stub)

    job = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "aborted", done
    assert done["condition"] == "superseded", done
    assert "Superseded" in (done.get("error") or ""), done


def test_abort_completed_job_is_noop(client, install_network, tmp_projects_dir, session_ctx):
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    job = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed"
    # Aborting an already-terminal job is a clean no-op that echoes the job.
    r = client.post(f"/api/simulation/queue/{job['id']}/abort")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


def test_solve_heartbeat_pings_while_active_and_stops_on_end():
    """
    `_SolveHeartbeat` must emit elapsed-time pings ONLY while a solve is active
    (between begin() and end()), and go fully silent the moment end() is called
    — so a long native solve looks alive without leaking pings into the quiet
    setup / restore phases.
    """
    import queue as _queue

    from services.solver_service import _SolveHeartbeat

    q: _queue.SimpleQueue = _queue.SimpleQueue()
    hb = _SolveHeartbeat(q, interval=0.4)
    hb.start()
    try:
        # Inactive (no begin yet) → no pings.
        time.sleep(0.7)
        assert q.empty(), "heartbeat emitted while inactive"

        hb.begin("Optimising")
        time.sleep(1.6)  # several 0.4s intervals
        hb.end()
        time.sleep(0.1)  # absorb any in-flight straggler into the "during" bucket

        during = []
        while not q.empty():
            during.append(str(q.get()))
        assert during, "heartbeat emitted nothing while active"
        assert all("elapsed" in ln and "Optimising" in ln for ln in during), during

        # After end(): fully silent over a window covering several intervals.
        time.sleep(1.2)
        after = 0
        while not q.empty():
            q.get()
            after += 1
        assert after == 0, f"heartbeat kept emitting after end(): {after}"
    finally:
        hb.shutdown()


def test_abort_watcher_injects_exactly_once():
    """
    Regression for the orphaned-native-solve bug: `_AbortWatcher` must inject a
    SINGLE KeyboardInterrupt per abort, never repeatedly.

    The old `_loop` re-injected every 0.2s while armed + stop_event set. linopy's
    HiGHS wrapper catches the FIRST interrupt, calls `h.cancelSolve()`, then waits
    for `h.run()` to finish in a SECOND un-guarded `finished.wait(0.1)` loop — a
    second injected interrupt landing there propagates out while the native solve
    is still running (orphan thread) and a later one can strand `restore_modelling`
    (it catches only `Exception`). The worker here mimics that "keep running after
    catching" loop: with the fix it logs exactly one hit, pre-fix it logged ~6.
    """
    from services.solver_service import _AbortWatcher

    stop_event = threading.Event()
    hits = {"n": 0}
    tid_box: dict[str, int] = {}
    started = threading.Event()
    stop_worker = threading.Event()

    def worker():
        tid_box["tid"] = threading.get_ident()
        started.set()
        # Bounce back to Python every 50ms (releasing the GIL in wait) so an
        # async-exc is always promptly deliverable — exactly linopy's poll loop.
        # Catch and KEEP RUNNING, mimicking the post-cancel finish-wait loop the
        # old re-injection used to break over and over.
        while not stop_worker.is_set():
            try:
                stop_worker.wait(0.05)
            except KeyboardInterrupt:
                hits["n"] += 1

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert started.wait(2)
    w = _AbortWatcher(stop_event, tid_box["tid"])
    w.start()
    w.arm()
    stop_event.set()
    # Hold the armed+set state well past several 0.2s ticks. Old code: ~5-6 hits.
    time.sleep(1.3)
    w.shutdown()
    stop_worker.set()
    t.join(2)
    assert hits["n"] == 1, f"expected exactly one injection, got {hits['n']}"


# ── A6: background results-bundle reader (view a finished job WITHOUT loading) ──
def test_results_bundle_after_queue_solve(client, install_network, tmp_projects_dir, session_ctx):
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    job = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    assert _wait_for_terminal(job["id"])["status"] == "completed"

    # Read the solved dispatch straight off disk — the active slot is NOT loaded.
    r = client.get("/api/projects/P1/results_bundle")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["available"] is True
    assert b["source"] == "lopf"
    assert b["source_available"]["lopf"] is True
    assert b["generators"] is not None
    assert b["generators"]["columns"], "no generator columns in dispatch payload"
    assert len(b["generators"]["data"]) == 4, "expected 4 snapshots of dispatch"
    assert b["carriers"], "carrier map should be populated"
    assert isinstance(b["objective"], (int, float))


def test_results_bundle_unsolved_is_204(client, install_network, tmp_projects_dir, session_ctx):
    install_network(build_network(), name="P1")  # never solved
    _save_project(client, "P1")
    r = client.get("/api/projects/P1/results_bundle")
    assert r.status_code == 204, r.text


def test_results_bundle_missing_project_404(client, tmp_projects_dir, session_ctx):
    r = client.get("/api/projects/NoSuch/results_bundle")
    assert r.status_code == 404, r.text


def test_results_bundle_does_not_touch_active_slot(client, install_network, tmp_projects_dir, session_ctx):
    # Solve + persist P1 via the queue so it has dispatch on disk.
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    job = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    assert _wait_for_terminal(job["id"])["status"] == "completed"
    # Bind the active slot to a DIFFERENT project, then read P1's bundle.
    install_network(build_network(), name="Foreground")
    before = session_ctx(client).loaded_project
    r = client.get("/api/projects/P1/results_bundle")
    assert r.status_code == 200, r.text
    # The core A6 invariant: a transient disk read must NOT swap the active slot.
    assert session_ctx(client).loaded_project == before == "Foreground"


def test_results_bundle_invalid_source_coerces_to_lopf(client, install_network, tmp_projects_dir, session_ctx):
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    job = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    assert _wait_for_terminal(job["id"])["status"] == "completed"
    r = client.get("/api/projects/P1/results_bundle", params={"source": "garbage"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "lopf"


# ── B4.3: dispatcher solves on its OWN ProjectContext (per-project isolation) ──
def test_background_solve_does_not_touch_foreground(
    client, install_network, tmp_projects_dir, project_storage_dir, session_ctx
):
    """
    Enqueue project B while project A is the foreground (active) ctx with UNSAVED
    in-memory edits. The background solve of B must leave A's active ctx — its
    network object, loaded-project binding, lifecycle state, transient registry —
    completely untouched, while persisting B's dispatch to disk.
    """
    # B exists on disk (saved, then we move the foreground off it).
    install_network(build_network(), name="B")
    _save_project(client, "B")
    assert (project_storage_dir("B") / "network.nc").exists()

    # A becomes the active foreground ctx; mutate it in memory WITHOUT saving.
    install_network(build_network(), name="A")
    a_network = session_ctx(client).network
    r = client.put("/api/network/loads/L1", json={"name": "L1", "bus": "B1", "p_set": 999.0})
    assert r.status_code == 200, r.text
    assert session_ctx(client).network.loads.at["L1", "p_set"] == 999.0
    a_status_before = client.get("/api/simulation/status").json()

    # Enqueue B → background path (B != active id "A").
    job = client.post("/api/simulation/queue", json={"project_id": "B"}).json()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done

    # Foreground A is untouched: same binding, same network object, edit intact.
    assert session_ctx(client).loaded_project == "A"
    assert session_ctx(client).network is a_network
    assert session_ctx(client).network.loads.at["L1", "p_set"] == 999.0
    # A's lifecycle state was not co-opted by B's solve.
    assert client.get("/api/simulation/status").json()["status"] == a_status_before["status"]
    # No transient LP-scaffolding rows leaked into the foreground ctx.
    assert not PyPSAService.has_any_transient_rows()

    # B's on-disk network carries the solved dispatch.
    nb = pypsa.Network(str(project_storage_dir("B") / "network.nc"))
    assert not nb.generators_t.p.empty, "B's solved dispatch was not persisted"
    assert nb.generators_t.p.to_numpy().sum() > 0


def test_two_independent_projects_each_persist_own_results(
    client, install_network, tmp_projects_dir, project_storage_dir, session_ctx
):
    """Two distinct saved projects each solve on their own ctx and persist."""
    install_network(build_network(gens_weight=1.0), name="P1")
    _save_project(client, "P1")
    install_network(build_network(gens_weight=3.0), name="P2")
    _save_project(client, "P2")

    j1 = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    j2 = client.post("/api/simulation/queue", json={"project_id": "P2"}).json()
    d1 = _wait_for_terminal(j1["id"])
    d2 = _wait_for_terminal(j2["id"])
    assert d1["status"] == "completed", d1
    assert d2["status"] == "completed", d2
    assert d1["objective"] is not None
    assert d2["objective"] is not None

    for name in ("P1", "P2"):
        n = pypsa.Network(str(project_storage_dir(name) / "network.nc"))
        assert not n.generators_t.p.empty, f"{name}: dispatch not persisted"
        assert n.generators_t.p.to_numpy().sum() > 0


def test_enqueue_resident_foreground_solves_in_place(client, install_network, tmp_projects_dir, session_ctx):
    """
    Enqueuing the project that IS the active foreground ctx solves the resident
    in-memory instance in place — dispatch lands on the live network AND the
    binding stays put.
    """
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    assert session_ctx(client).loaded_project == "P1"

    job = client.post("/api/simulation/queue", json={"project_id": "P1"}).json()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done

    # The live foreground network carries the solved dispatch (solved in place),
    # and the active binding is unchanged.
    assert not session_ctx(client).network.generators_t.p.empty
    assert session_ctx(client).loaded_project == "P1"


def test_enqueue_foreground_surfaces_status_for_run_button(
    client, install_network, tmp_projects_dir, registry_key_for, session_ctx
):
    """
    The "Run = enqueue the foreground" UX: clicking Run enqueues the ACTIVE
    project, the dispatcher solves the resident ctx in place, and the foreground
    `/api/simulation/status` reflects the result — so the AppHeader StatusBar
    updates exactly as it would for a direct `/run`.

    Acceptance for the smart Run button: enqueuing the active project must (1)
    solve in place (dispatch on the live network) AND (2) write the active ctx's
    solver_state so the status-polling endpoint reports `completed` + objective.
    """
    install_network(build_network(), name="P1")
    _save_project(client, "P1")
    # The registry key is `org:uuid` since Step 0a; the NAME is what a client
    # sends, so that is what this enqueues — mirroring the Run button.
    assert session_ctx(client).registry_key == registry_key_for("P1")

    # Enqueue the FOREGROUND project — the project the active ctx is bound to.
    job = client.post(
        "/api/simulation/queue", json={"project_id": "P1"}
    ).json()
    assert job["project_id"] == "P1"
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done

    # 1. Solved in place — dispatch on the live foreground network.
    assert not session_ctx(client).network.generators_t.p.empty

    # 2. The foreground status endpoint (what StatusBar polls) reflects the
    #    queue-driven solve: completed + a finite objective + a solve_time.
    s = client.get("/api/simulation/status").json()
    assert s["status"] == "completed", s
    assert s["running"] is False, s
    assert isinstance(s["objective"], (int, float)) and s["objective"] == s["objective"], s
    assert s["solve_time"] is not None, s
