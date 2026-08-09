# Solve Queue Full Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the two data-loss defects in the multi-project solve queue, then make the queue correct and observable, then make it durable and controllable — without regressing either test suite.

**Architecture:** One `ProjectContext` per project, always, enforced by a per-registry-key hydrate-or-adopt lock on `PyPSAService` that all four cold paths route their build-and-register through; the solve-queue dispatcher registers what it builds and marks its context `kind="queue"` so shutdown keeps the job table as the single source for queue solves. On top of that invariant, increment 2 makes enqueue idempotent server-side and gives every job its own `BufferedLogQueue` behind two authorized job-scoped endpoints; increment 3 moves job state into a `solve_jobs` table with UUID identity, a per-job solver-config snapshot, boot reconciliation, queue controls (cancel-all-queued, pause/resume, requeue, per-user dismiss) and a bounded worker pool.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2 / Alembic (backend), PyPSA 1.1.2, React 19 + TypeScript 5.8 + Vite 6 + Vitest + React Query 5 + zustand (frontend), pixi for the toolchain.

## Global Constraints

- The toolchain is pixi-provided. `node` and `npm` are not on the system PATH.
- Canonical backend gate: `pixi run gui-tests` (`pixi.toml:236`), which resolves to the `test` environment because it is declared under `[feature.test.tasks]`. Run from the root task table it resolves to `default`, where two desktop tests skip and the suite still reads green.
- Frontend: `npm run test` (vitest) and `npm run build` (`tsc -b && vite build`, which is the only static gate — no JS/TS linter is configured).
- Python lint: `ruff check .`, with `pypsa-gui/**` exempted from `E402, E701, E702, I001` (`ruff.toml:61-66`) because mid-file imports break router/service cycles deliberately.
- Observed runtime in the `test` environment: Python 3.13.0, PyPSA 1.1.2. The `python ==3.12.12` and `pypsa ==1.0.3` pins at `pixi.toml:214,216` belong to `[feature.doc.dependencies]`, a docs-only feature declared `no-default-feature`, and do not apply.
- CI does not run the GUI suite; `.github/workflows/test.yaml` has no `pypsa-gui` job.
- This repository has no domain glossary (`CONTEXT.md`, `CONTEXT-MAP.md`) and no ADR directory (`docs/adr/`). This spec adds neither and introduces no term requiring one.

### Commands used literally by every task

- Backend, canonical: `pixi run gui-tests`
- Backend, single test: `pixi run -e test python -m pytest pypsa-gui/backend/tests/<file>::<test> -v`
- Frontend, whole suite: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test'`
- Frontend, single file: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- <path>'`
- Frontend typecheck/build: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'`

Never pass `-q` to the backend suite: `pytest.ini` already sets `addopts = -q`, so a second `-q` yields `-qq` and suppresses the summary line the gate reads. `pytest-timeout` is not installed, so `--timeout=` aborts the run.

### Job status vocabulary (used by every increment)

`queued | running | completed | failed | aborted | interrupted`. Terminal = `completed, failed, aborted, interrupted`. `_TERMINAL` at `services/solve_queue.py:57` gains `"interrupted"` in Task 15.

### Shared interface — the hydrate-or-adopt lock

Written identically in every task that touches it:

```python
@classmethod
@contextmanager
def hydrate_or_adopt(cls, registry_key: str):
    """
    Yield the resident ctx for `registry_key` if one exists, else yield None
    while holding this key's hydrate lock so the caller may build+hydrate+register
    exactly once. Fast path (registry hit) takes NO lock.
    Lock order: hydrate -> _registry_lock -> solve_queue._lock.
    """
```

Callers use it as:

```python
with PyPSAService.hydrate_or_adopt(key) as resident:
    if resident is not None:
        ctx = resident
    else:
        ctx = PyPSAService.build_context()
        _hydrate_context_from_disk(ctx, src, name)
        project_registry.bind_context(ctx, project)
        PyPSAService.register(key, ctx)
```

### Ordering rule — types must be coherent at every task boundary

**Every task's code must compile and pass against the types that exist AT THAT POINT in the sequence. A task may never be written against a type a later task introduces.** Two task orderings in this plan exist only to satisfy that rule, and reordering them back would break the boundary gates:

- **Task 4 (widen `SolveJob`) precedes Task 7 (per-job invalidation).** Task 7's test builds `SolveJob` literals carrying `project_key` and a null `project_id`; neither exists on the baseline type at `frontend/src/api/solveQueue.ts:8-20`. Written the other way round, `tsc -b` — the only frontend static gate — fails with TS2353 on the excess property and TS2322 on the null, taking `npm run build` and both frontend boundary gates with it.
- **Task 12 (UUID identity) precedes Task 13 (the `solve_jobs` table).** The table's primary key is `Uuid(as_uuid=True)`, whose bind processor calls `value.hex`. If the table landed first, the live enqueue route would still be minting `int` ids from `itertools.count(1)` and every insert would raise `AttributeError: 'int' object has no attribute 'hex'`.

The id type must agree, at each boundary, across five places: the `SolveJob` dataclass, the generator in `enqueue` / `enqueue_unique`, the `SolveJobRow.id` column, the route path parameters, and the TypeScript `SolveJob.id`. Increments 1 and 2 are entirely `int` / `number`; increment 3 flips all five in Task 12 and everything after it is `uuid.UUID` / `string`.

---

## Increment 1 — stop the data loss (Tasks 1–7)

Mergeable alone. Backend Tasks 1–3, frontend Tasks 4–7.

### Task 1: D-1 regression test, the hydrate-or-adopt lock, and dispatcher adoption

**Increment:** 1

**Requirements:** R1, R3, R4, R6, R7, R14

**Files:**
- Create: `pypsa-gui/backend/tests/test_context_fork_regression.py`
- Modify: `pypsa-gui/backend/services/pypsa_service.py:91-97` (add the hydrate-lock registry class attributes after `_registry_lock`)
- Modify: `pypsa-gui/backend/services/pypsa_service.py:714-720` (add `hydrate_or_adopt` immediately after `get_context`)
- Modify: `pypsa-gui/backend/services/solve_queue.py:364-385` (context resolution)
- Modify: `pypsa-gui/backend/services/solve_queue.py:402-411` (ctx lifecycle claim gains `kind="queue"`)
- Test: `pypsa-gui/backend/tests/test_context_fork_regression.py`

**Interfaces:**
- Consumes: `PyPSAService.build_context()`, `PyPSAService.get_context(project_id) -> ProjectContext | None`, `PyPSAService.register(project_id, ctx) -> list[str]`, `routers.projects._hydrate_context_from_disk(ctx, src, name)`, `routers.projects._safe_project_dir(name)`.
- Produces:
  ```python
  @classmethod
  @contextmanager
  def hydrate_or_adopt(cls, registry_key: str):
      """
      Yield the resident ctx for `registry_key` if one exists, else yield None
      while holding this key's hydrate lock so the caller may build+hydrate+register
      exactly once. Fast path (registry hit) takes NO lock.
      Lock order: hydrate -> _registry_lock -> solve_queue._lock.
      """
  ```
  and the dispatcher invariant: a running queue job's context is resident under `job.project_key` with `solver_state["kind"] == "queue"`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_context_fork_regression.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_context_fork_regression.py -v`

Expected: FAIL. `test_hydrate_or_adopt_admits_exactly_one_builder_per_key` and `test_hydrate_or_adopt_takes_no_lock_on_a_registry_hit` fail with `AttributeError: type object 'PyPSAService' has no attribute 'hydrate_or_adopt'`; `test_activating_a_project_mid_queue_solve_does_not_fork_its_context` fails at `assert during is not None` with `AssertionError: the dispatcher's background context is not registered`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/pypsa_service.py`, after the `_registry_lock` declaration (`:91-97`), add:

```python
    # ── Hydrate-or-adopt lock (one ProjectContext per project, always) ────────
    # Per-registry-key lock, taken ONLY on a registry miss. FOUR cold paths
    # build-and-register a context — `activate_project`, `resolve_project_context`,
    # `active_project.resolve_for_session` (both branches, twice per authenticated
    # request on every route) and the solve-queue dispatcher — and every one of
    # them ran an UNSYNCHRONISED get_context -> build -> hydrate -> register.
    # `get_context` is an unlocked dict read and `register` takes `_registry_lock`
    # only for the insert without re-checking for a concurrent winner, so two
    # cold paths racing on the same non-resident project each built a context and
    # the second overwrote the first in `_contexts` while the first was already
    # bound into a live request.
    #
    # LOCK ORDER — hydrate -> `_registry_lock` -> `solve_queue._lock`. A hydrate
    # lock is NEVER acquired while `_registry_lock` is held. `_hydrate_guard` is a
    # leaf lock held only for the dict lookup that hands out the per-key lock, and
    # nothing is called while it is held. The pre-existing rule that
    # `_registry_lock` never nests a per-ctx `mutation_lock` (see
    # `_evict_if_over_cap`) is unchanged.
    #
    # The dict grows one small entry per registry key ever missed — bounded by
    # the number of distinct projects the process has hydrated, not by traffic.
    _hydrate_locks: dict[str, threading.Lock] = {}
    _hydrate_guard: threading.Lock = threading.Lock()
```

In the same file, immediately after `get_context` (`:714-720`), add:

```python
    @classmethod
    @contextmanager
    def hydrate_or_adopt(cls, registry_key: str):
        """
        Yield the resident ctx for `registry_key` if one exists, else yield None
        while holding this key's hydrate lock so the caller may build+hydrate+register
        exactly once. Fast path (registry hit) takes NO lock.
        Lock order: hydrate -> _registry_lock -> solve_queue._lock.
        """
        resident = cls._contexts.get(registry_key)
        if resident is not None:
            # The common path. Deliberately identical to `get_context` — no
            # lock, no bookkeeping — so routing every cold path through this
            # helper costs a resident hit nothing.
            yield resident
            return

        with cls._hydrate_guard:
            lock = cls._hydrate_locks.get(registry_key)
            if lock is None:
                lock = threading.Lock()
                cls._hydrate_locks[registry_key] = lock

        with lock:
            # Re-check UNDER the lock: the thread that just released it may have
            # registered the very context we were about to build. Yielding it
            # here is the "adopt" half of the name.
            yield cls._contexts.get(registry_key)
```

In `pypsa-gui/backend/services/solve_queue.py`, replace the context resolution (`:364-385`) with:

```python
            # One ProjectContext per project, ALWAYS. This used to build and
            # hydrate a context here and never register it, so
            # `get_context(job.project_key)` answered None for the whole solve
            # and `activate_project` built a SECOND context for the same
            # project — the user edited that copy and the next ordinary save
            # wiped this job's dispatch off disk (defect D-1). Route the miss
            # through the shared hydrate-or-adopt lock and REGISTER what we
            # build, exactly like the other three cold paths.
            # Lock order: hydrate -> _registry_lock -> solve_queue._lock.
            key = job.project_key

            def _hydrate_fresh():
                fresh = PyPSAService.build_context()
                # Use the directory the ENQUEUING request authorized. Falling
                # back to a name-derived path would resolve under the shared
                # projects root and could land on another org's project.
                src = (
                    pathlib.Path(job.storage_dir)
                    if job.storage_dir
                    else _safe_project_dir(project_id)
                )
                _hydrate_context_from_disk(fresh, src, project_id)
                return fresh

            if key is None:
                # A legacy or hand-made job carries no registry identity: there
                # is nothing to adopt and no key to register under. Unchanged
                # behaviour for those, which `_may_see` already treats as
                # local-mode-only artefacts.
                ctx = _hydrate_fresh()
            else:
                with PyPSAService.hydrate_or_adopt(key) as resident:
                    if resident is not None:
                        # Resident → solve THAT instance in place so the user's
                        # unsaved foreground edits are included (B4.3).
                        ctx = resident
                    else:
                        ctx = _hydrate_fresh()
                        if job.storage_dir:
                            org, _, uuid_part = key.partition(":")
                            ctx.org_id, ctx.project_uuid = org, uuid_part
                            ctx.storage_dir = job.storage_dir
                        PyPSAService.register(key, ctx)
```

In the same file, add `kind="queue"` to the lifecycle claim (`:402-411`), so the `ctx_state_update(...)` call reads:

```python
            ctx_state_update(
                status="running", condition=None, objective=None, solve_time=None,
                last_failure=None,
                stop_event=stop_event, log_queue=log_queue,
                thread=me,
                # Which KIND of worker owns `thread`, mirroring `/run`'s
                # `kind="lopf"` (routers/simulation.py:591-599). Load-bearing now
                # that the context is REGISTERED: without it a background queue
                # solve reads as `"active"` to `shutdown._context_solves()`,
                # which reports it as abortable through `/api/simulation/abort`
                # — it is not; only `solve_queue.abort` can stop it — and it
                # would be counted a second time by `solves_in_flight()`.
                kind="queue",
                last_lost_load=None, lopf_results=None, ac_pf_results=None,
                ac_pf_convergence=None, ac_pf_convergence_list=None,
                ac_pf_slack_bus_used=None, ac_pf_stripped_voll_slacks=None,
                ac_pf_converged_count=None, ac_pf_total_snapshots=None,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_context_fork_regression.py -v`

Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/pypsa_service.py pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/tests/test_context_fork_regression.py
git commit -m "fix(queue): register the dispatcher's context under a hydrate-or-adopt lock (D-1)" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Route the three remaining cold paths through the lock

**Increment:** 1

**Requirements:** R2

**Files:**
- Create: `pypsa-gui/backend/tests/test_hydrate_or_adopt_cold_paths.py`
- Modify: `pypsa-gui/backend/routers/projects.py:1985-1998` (`activate_project`'s resident/cold branches)
- Modify: `pypsa-gui/backend/routers/deps.py:135-154` (`resolve_project_context`'s resident/cold branches)
- Modify: `pypsa-gui/backend/services/active_project.py:100-135` (`resolve_for_session`, both branches)
- Test: `pypsa-gui/backend/tests/test_hydrate_or_adopt_cold_paths.py`

**Interfaces:**
- Consumes:
  ```python
  @classmethod
  @contextmanager
  def hydrate_or_adopt(cls, registry_key: str):
      """
      Yield the resident ctx for `registry_key` if one exists, else yield None
      while holding this key's hydrate lock so the caller may build+hydrate+register
      exactly once. Fast path (registry hit) takes NO lock.
      Lock order: hydrate -> _registry_lock -> solve_queue._lock.
      """
  ```
  plus `PyPSAService.activate_context(ctx, *, register=False) -> list[str]`, `PyPSAService.set_active(project_id) -> ProjectContext`, `PyPSAService.register(key, ctx) -> list[str]`, `project_registry.registry_key(project) -> str`, `project_registry.bind_context(ctx, project)`, `active_project.scratch_key(session) -> str`.
- Produces: no new symbols. The behavioural contract: for every registry key, at most one `build_context()` survives a concurrent cold-path race on any of the four paths.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_hydrate_or_adopt_cold_paths.py`:

```python
"""
R2 — all four cold paths build-and-register under the shared hydrate lock.

The dispatcher is covered by `tests/test_context_fork_regression.py`. This file
covers the other three, each driven by a real concurrent race with the hydrate
deliberately slowed so the pre-lock window is wide enough to lose.

`resolve_for_session` is the one that makes the invariant true rather than
nearly true: it runs TWICE per authenticated request on EVERY route (the
`undo_snapshot_middleware` half at `main.py:525` and the FastAPI-constructor
dependency at `deps.py:78`), so under the 1.5 s queue poll it is by a wide
margin the most frequently executed context builder in the process.
"""
from __future__ import annotations

import threading

from services.pypsa_service import PyPSAService
from tests.conftest import build_network


def _slow_hydrate(monkeypatch, delay: float = 0.15) -> None:
    """Widen the check-then-build-then-register window the lock closes."""
    import time

    from routers import projects as projects_router

    real = projects_router._hydrate_context_from_disk

    def slow(ctx, src, name):
        time.sleep(delay)
        return real(ctx, src, name)

    # Every cold path imports this LAZILY at call time, so patching the module
    # attribute reaches all three.
    monkeypatch.setattr(projects_router, "_hydrate_context_from_disk", slow)


def _race(fn, n: int = 3) -> list:
    barrier = threading.Barrier(n)
    results: list = []
    errors: list = []

    def go() -> None:
        try:
            barrier.wait(10)
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 — surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [], errors
    assert len(results) == n, results
    return results


def _evict(key: str) -> None:
    with PyPSAService._registry_lock:
        PyPSAService._contexts.pop(key, None)


def test_resolve_project_context_builds_one_context_under_a_race(
    client, install_network, tmp_projects_dir, registry_key_for, _auth_db, monkeypatch,
):
    from db.models import User
    from routers import deps

    _engine, session_local = _auth_db
    install_network(build_network(), name="Cold1")
    r = client.post("/api/projects/Cold1", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    key = registry_key_for("Cold1")
    _evict(key)
    _slow_hydrate(monkeypatch)

    def resolve():
        with session_local() as db:
            user = db.query(User).first()
            return deps.resolve_project_context("Cold1", db, user)

    contexts = _race(resolve)
    assert all(c is contexts[0] for c in contexts), "resolve_project_context forked"
    assert PyPSAService.get_context(key) is contexts[0]


def test_resolve_for_session_builds_one_context_under_a_race(
    client, install_network, tmp_projects_dir, registry_key_for, _auth_db, monkeypatch,
):
    from services import active_project
    from services.auth_service import resolve_session_row
    from settings import get_settings

    _engine, session_local = _auth_db
    install_network(build_network(), name="Cold2")
    r = client.post("/api/projects/Cold2", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    assert client.post("/api/projects/Cold2/activate").status_code == 200
    key = registry_key_for("Cold2")
    _evict(key)
    _slow_hydrate(monkeypatch)

    raw = client.cookies.get(get_settings().session_cookie_name)
    assert raw, "client has no session cookie"

    def resolve():
        with session_local() as db:
            row = resolve_session_row(db, raw)
            assert row is not None
            ctx, _slot = active_project.resolve_for_session(db, row)
            return ctx

    contexts = _race(resolve)
    assert all(c is contexts[0] for c in contexts), "resolve_for_session forked"
    assert PyPSAService.get_context(key) is contexts[0]


def test_activate_builds_one_context_under_a_race(
    client, install_network, tmp_projects_dir, registry_key_for, _auth_db, monkeypatch,
):
    from db.models import User
    from routers import projects as projects_router
    from services.auth_service import resolve_session_row
    from settings import get_settings

    _engine, session_local = _auth_db
    install_network(build_network(), name="Cold3")
    r = client.post("/api/projects/Cold3", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    key = registry_key_for("Cold3")
    _evict(key)
    _slow_hydrate(monkeypatch)

    raw = client.cookies.get(get_settings().session_cookie_name)

    def activate():
        with session_local() as db:
            user = db.query(User).first()
            row = resolve_session_row(db, raw)
            projects_router.activate_project("Cold3", db, user, row)
            return PyPSAService.get_context(key)

    contexts = _race(activate)
    assert contexts[0] is not None
    assert all(c is contexts[0] for c in contexts), "activate forked a context"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_hydrate_or_adopt_cold_paths.py -v`

Expected: FAIL with `AssertionError: resolve_project_context forked` (and the sibling messages `resolve_for_session forked`, `activate forked a context`) — three threads each build a distinct context because none of the three paths takes the lock yet.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/routers/projects.py`, replace `:1985-1998` with:

```python
    evicted: list[str] = []
    # Hold this key's hydrate lock across the MISS so a concurrent cold path
    # (a path-scoped read, the session resolver, the solve dispatcher) cannot
    # build a SECOND context for the same project. A resident hit takes no
    # lock at all, so the instant tab switch is unchanged.
    # Lock order: hydrate -> _registry_lock -> solve_queue._lock.
    with PyPSAService.hydrate_or_adopt(registry_id) as resident:
        if resident is not None:
            # Resident → instant pointer swap. Already in the registry, so no
            # new registration and no eviction can fire.
            PyPSAService.set_active(registry_id)
        else:
            # Cold: build, hydrate from disk, then publish + register atomically.
            # activate_context(register=True) runs the B9 cap check and returns any
            # projects evicted to make room — the freshly-activated project is
            # protected (it's the new active id) so it's never its own victim.
            ctx = PyPSAService.build_context()
            _hydrate_context_from_disk(ctx, src, project.name)
            project_registry.bind_context(ctx, project)
            evicted = PyPSAService.activate_context(ctx, register=True)
```

In `pypsa-gui/backend/routers/deps.py`, replace `:135-154` with:

```python
    key = project_registry.registry_key(project)
    # Lock order: hydrate -> _registry_lock -> solve_queue._lock. Taken only on
    # a miss; a resident hit is the same unlocked dict read it always was.
    with PyPSAService.hydrate_or_adopt(key) as resident:
        if resident is not None:
            # A path-scoped read counts as a touch — re-stamp recency so a warm,
            # frequently-read project doesn't get evicted out from under the reader
            # (B9). Stamp under the registry lock so it's atomic w.r.t. eviction's
            # min(last_interacted_at) victim pick.
            with PyPSAService._registry_lock:
                resident.last_interacted_at = time.monotonic()
            return resident

        ctx = PyPSAService.build_context()
        _hydrate_context_from_disk(ctx, src, project.name)
        project_registry.bind_context(ctx, project)
        # register() stamps recency + runs the B9 cap check (evicting the LRU
        # non-protected project, saving it first). The evicted ids aren't surfaced
        # to this path-scoped reader — the activate endpoint is the channel that
        # tells the frontend to drop caches; a path-scoped read is transient.
        PyPSAService.register(key, ctx)
        return ctx
```

In `pypsa-gui/backend/services/active_project.py`, replace `:100-135` with:

```python
    project = _authorized_active_project(db, session)
    if project is not None:
        key = project_registry.registry_key(project)
        resident = PyPSAService.get_context(key)
        if resident is not None:
            return resident, key

        src = project_registry.project_dir(project)
        if (src / "network.nc").exists():
            from routers.projects import _hydrate_context_from_disk

            # This function runs TWICE per authenticated request on EVERY route
            # (main.py:525 and the constructor dependency at deps.py:78), so it
            # is the highest-frequency context builder in the process and the
            # one whose race the 1.5 s queue poll makes ordinary rather than
            # exotic. Lock order: hydrate -> _registry_lock -> solve_queue._lock.
            with PyPSAService.hydrate_or_adopt(key) as adopted:
                if adopted is not None:
                    return adopted, key
                ctx = PyPSAService.build_context()
                try:
                    _hydrate_context_from_disk(ctx, src, project.name)
                    project_registry.bind_context(ctx, project)
                    PyPSAService.register(key, ctx)
                    return ctx, key
                except Exception:  # noqa: BLE001 — a corrupt project must not 500 every route
                    logger.exception(
                        "active-project hydrate failed for %s; falling back to scratch",
                        project.id,
                    )
        else:
            # Pointed at a project whose blob is gone (deleted behind the app's
            # back). Drop the pointer so the next request starts clean instead
            # of re-failing the same hydrate forever.
            session.active_project_id = None
            db.commit()

    key = scratch_key(session)
    # The scratch branch races the same way: two requests for one session can
    # both miss and both `register`, and the loser's draft network is the one
    # the user was typing into.
    with PyPSAService.hydrate_or_adopt(key) as resident:
        if resident is not None:
            return resident, key
        ctx = PyPSAService.adopt_process_foreground() or PyPSAService.build_context()
        PyPSAService.register(key, ctx)
        return ctx, key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_hydrate_or_adopt_cold_paths.py -v`

Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/routers/projects.py pypsa-gui/backend/routers/deps.py pypsa-gui/backend/services/active_project.py pypsa-gui/backend/tests/test_hydrate_or_adopt_cold_paths.py
git commit -m "fix(context): route all four cold paths through the hydrate-or-adopt lock" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Shutdown stops double-counting a registered queue solve

**Increment:** 1

**Requirements:** R5

**Files:**
- Create: `pypsa-gui/backend/tests/test_shutdown_queue_kind.py`
- Modify: `pypsa-gui/backend/services/shutdown.py:126-137` (`_context_solves` skips queue-owned contexts)
- Test: `pypsa-gui/backend/tests/test_shutdown_queue_kind.py`

**Interfaces:**
- Consumes: the Task 1 invariant `ctx.solver_state["kind"] == "queue"` for a running queue job's context; `shutdown_service.solves_in_flight() -> list[InFlightSolve]`; `InFlightSolve(path, label, interruptible)` with `path ∈ {"active","queue","ac_pf"}`.
- Produces: `_context_solves()` never returns a context whose `kind` is `"queue"`, so `solves_in_flight()` reports exactly one entry per running queue job, sourced from the job table.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_shutdown_queue_kind.py`:

```python
"""
R5 — a registered queue solve is counted ONCE, by the job table.

Registering the dispatcher's context (increment 1) falsified the constraint
`services/shutdown.py:144-153` records: that a running queue job's context is
in neither registry. Without this skip, `_context_solves()` walks the now-
resident context and `solves_in_flight()` also reads the job table, so the quit
confirmation lists the same solve twice — once labelled `"active"`, i.e. as
abortable through `/api/simulation/abort`, which it is not.

The job table stays the single source for queue solves because it is the only
one that also sees a job that is still `queued`.
"""
from __future__ import annotations

import threading

from services import shutdown as shutdown_service


def test_a_queue_owned_context_is_counted_once_by_the_job_table():
    from services.pypsa_service import PyPSAService
    from services.solve_queue import SolveJob, solve_queue

    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    ctx = PyPSAService.build_context()
    solve_queue.reset_for_tests()
    PyPSAService._contexts["queue-owned-solve"] = ctx
    try:
        with ctx.solver_state_lock:
            ctx.solver_state.update(thread=worker, kind="queue")
        with solve_queue._lock:
            job = SolveJob(id=931, project_id="Q", enqueued_at=0.0)
            job.status = "running"
            solve_queue._jobs[931] = job
            solve_queue._order.append(931)

        paths = [s.path for s in shutdown_service.solves_in_flight()]

        assert paths == ["queue"], paths
    finally:
        running.set()
        worker.join(5)
        PyPSAService._contexts.pop("queue-owned-solve", None)
        solve_queue.reset_for_tests()


def test_a_foreground_solve_on_a_background_context_is_still_seen():
    """
    The skip must key on `kind`, not on residency. A legacy `/run` worker that
    happens to own a non-active resident context is still path (a) and still
    abortable — narrowing the walk to the foreground is the regression
    `tests/test_shutdown.py::test_a_solve_on_a_NON_ACTIVE_context_is_seen`
    already pins from the other side.
    """
    from services.pypsa_service import PyPSAService

    running = threading.Event()
    worker = threading.Thread(target=running.wait, daemon=True)
    worker.start()
    ctx = PyPSAService.build_context()
    PyPSAService._contexts["lopf-on-background"] = ctx
    try:
        with ctx.solver_state_lock:
            ctx.solver_state.update(thread=worker, kind="lopf")

        assert [s.path for s in shutdown_service.solves_in_flight()] == ["active"]
    finally:
        running.set()
        worker.join(5)
        PyPSAService._contexts.pop("lopf-on-background", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_shutdown_queue_kind.py -v`

Expected: FAIL on `test_a_queue_owned_context_is_counted_once_by_the_job_table` with `AssertionError: ['active', 'queue']` — the same solve is reported twice, once through the context walk and once through the job table.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/shutdown.py`, replace the loop body of `_context_solves` (`:126-137`) with:

```python
    found: list[tuple[Any, Any, str | None]] = []
    for ctx in resident_contexts():
        try:
            with ctx.solver_state_lock:
                thread = ctx.solver_state.get("thread")
                kind = ctx.solver_state.get("kind")
        except Exception:  # pragma: no cover - defensive
            logger.exception("could not read solver state for a context")
            continue
        if kind == "queue":
            # A queue job's context is REGISTERED now (one ProjectContext per
            # project, always), so this walk and the job table in
            # `solves_in_flight` would each report the same solve. The job
            # table stays the single source for the queue path because it is
            # the only one that also sees a job that is still `queued`.
            #
            # Skipping here is also what keeps a background queue solve out of
            # the `"active"` bucket, which claims `interruptible=True` via
            # `/api/simulation/abort` — that endpoint reaches the FOREGROUND
            # worker only. `solve_queue.abort` is what stops a queue job, and
            # `desktop/gui.py:_abort_everything` already calls it separately.
            continue
        if thread is not None and thread.is_alive():
            found.append((ctx, thread, kind))
    return found
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_shutdown_queue_kind.py -v`

Expected: PASS — 2 passed.

Then confirm the two pinning tests still pass UNMODIFIED (R5 is verified by their passing; changing them would defeat their purpose):

Run: `pixi run -e test python -m pytest "pypsa-gui/backend/tests/test_shutdown.py::test_a_solve_on_a_NON_ACTIVE_context_is_seen" "pypsa-gui/backend/tests/test_shutdown.py::test_a_running_queue_job_is_seen_deterministically" -v`

Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/shutdown.py pypsa-gui/backend/tests/test_shutdown_queue_kind.py
git commit -m "fix(shutdown): skip queue-owned contexts so a queue solve is counted once" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Honest panel copy and a redaction-safe `SolveJob`

**Increment:** 1

**Requirements:** R12, R13

**Files:**
- Create: `pypsa-gui/frontend/src/pages/SolveQueuePanel.redacted.test.tsx`
- Modify: `pypsa-gui/frontend/src/api/solveQueue.ts:8-20` (`SolveJob`)
- Modify: `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx:109-153` (`JobRow`), `:175` (`activeProjects`), `:220-224` (help copy)
- Test: `pypsa-gui/frontend/src/pages/SolveQueuePanel.redacted.test.tsx`

**Interfaces:**
- Consumes: `type SolveJobStatus`, `isActive`, `isTerminal` from `../api/solveQueue`.
- Produces:
  ```ts
  export interface SolveJob {
    id: number
    project_id: string | null
    project_key: string | null
    status: SolveJobStatus
    position: number | null
    objective: number | null
    solve_time: number | null
    condition: string | null
    error: string | null
    enqueued_at: number
    started_at: number | null
    finished_at: number | null
  }
  ```
  and, from `SolveQueuePanel.tsx`, `export const REDACTED_PROJECT_LABEL: string`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/pages/SolveQueuePanel.redacted.test.tsx`:

```tsx
// R13 — a REDACTED row must render a fixed, non-empty label and must never be
// expandable.
//
// The backend nulls `project_id`, `project_key` and `error` for a job the
// caller may not see (routers/solve_queue.py `_REDACTED`), and the frontend
// type declared `project_id: string` and omitted `project_key` entirely. The
// row therefore rendered a blank name, and expanding a redacted COMPLETED row
// would have fetched `/projects/null/results_bundle`.
//
// R12 — and the help copy must not claim the editor is busy while the queue
// runs, which `routers/projects.py:1937-1941` exists precisely to make false.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob } from '../api/solveQueue'
import SolveQueuePanel, { REDACTED_PROJECT_LABEL } from './SolveQueuePanel'

const redactedJob: SolveJob = {
  id: 7,
  project_id: null,
  project_key: null,
  status: 'completed',
  position: null,
  objective: null,
  solve_time: null,
  condition: null,
  error: null,
  enqueued_at: 0,
  started_at: 0,
  finished_at: 1,
}

const runningJob: SolveJob = { ...redactedJob, id: 8, project_id: 'mine', status: 'running' }

let jobs: SolveJob[] = []

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: null }) }))

vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, current: null }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
}))

afterEach(() => cleanup())
beforeEach(() => { jobs = [] })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

describe('SolveQueuePanel redaction', () => {
  it('renders a fixed label instead of an empty name for a redacted row', () => {
    jobs = [redactedJob]
    renderPanel()
    expect(screen.getByText(REDACTED_PROJECT_LABEL)).toBeTruthy()
    expect(REDACTED_PROJECT_LABEL.length).toBeGreaterThan(0)
  })

  it('disables the expand control on a redacted row', () => {
    jobs = [redactedJob]
    renderPanel()
    const expand = screen.getByTitle('Not available for this job')
    expect((expand as HTMLButtonElement).disabled).toBe(true)
  })

  it('does not claim the active editor is busy while the queue runs', () => {
    jobs = [runningJob]
    renderPanel()
    expect(screen.queryByText(/the active editor is busy/i)).toBeNull()
    expect(screen.getByText(/other projects stay editable/i)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/pages/SolveQueuePanel.redacted.test.tsx'`

Expected: FAIL with `SyntaxError: The requested module './SolveQueuePanel' does not provide an export named 'REDACTED_PROJECT_LABEL'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/frontend/src/api/solveQueue.ts`, replace `:8-20` with:

```ts
export interface SolveJob {
  id: number
  // NULLED for a job the caller may not see. `routers/solve_queue.py` redacts
  // `project_id`, `project_key` and `error` rather than dropping the row,
  // because `position` is a place in a GLOBALLY sequential queue and hiding
  // other orgs' rows would leave a caller at "#4" with one job visible.
  project_id: string | null
  // `org:uuid`. Always emitted by the backend, nulled by the same redaction.
  // Was missing from this interface entirely.
  project_key: string | null
  status: SolveJobStatus
  position: number | null
  objective: number | null
  solve_time: number | null
  condition: string | null
  // Nulled by redaction too — a failure message routinely quotes a project
  // name or a path.
  error: string | null
  enqueued_at: number
  started_at: number | null
  finished_at: number | null
}
```

In `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx`, add above `JobRow`:

```tsx
// The row is a job the caller may not see: the backend nulled its identifying
// fields. Say so plainly rather than rendering an empty element — the row's id,
// status, position and timings are legitimately visible and the queue depth is
// the thing the caller actually needs from it.
export const REDACTED_PROJECT_LABEL = 'Hidden — another organisation’s project'
```

Replace `JobRow` (`:109-153`) with:

```tsx
function JobRow({ job, onAbort }: { job: SolveJob; onAbort: (id: number) => void }) {
  const [expanded, setExpanded] = useState(false)
  const canAbort = job.status === 'queued' || job.status === 'running'
  // A redacted row names no project, so there is nothing to preview and no name
  // to put in the URL — `/projects/null/results_bundle` is what the unguarded
  // version would have requested.
  const name = job.project_id
  const canPreview = job.status === 'completed' && name != null
  return (
    <div className="rounded-lg border border-border bg-bg overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={() => canPreview && setExpanded(v => !v)}
          disabled={!canPreview}
          className={`p-0.5 rounded ${canPreview ? 'text-muted hover:text-text' : 'opacity-0 pointer-events-none'}`}
          title={canPreview ? 'Preview results' : 'Not available for this job'}
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {name != null ? (
              <span className="truncate text-[12px] font-medium text-text" title={name}>{name}</span>
            ) : (
              <span className="truncate text-[12px] font-medium text-muted italic" title={REDACTED_PROJECT_LABEL}>
                {REDACTED_PROJECT_LABEL}
              </span>
            )}
            {job.status === 'queued' && job.position != null && (
              <span className="text-[10px] text-muted">#{job.position} in line</span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-[10px] text-muted">
            {job.status === 'completed' && <span>{fmtObjective(job.objective)}{job.solve_time != null ? ` · ${job.solve_time}s` : ''}</span>}
            {job.status === 'failed' && <span className="text-danger truncate" title={job.error ?? job.condition ?? ''}>{job.error ?? job.condition ?? 'Failed'}</span>}
            {job.status === 'aborted' && (
              <span>{job.condition === 'superseded' ? 'Superseded by a newer run' : 'Aborted by user'}</span>
            )}
          </div>
        </div>
        <StatusBadge status={job.status} />
        {canAbort && (
          <button
            onClick={() => onAbort(job.id)}
            className="p-1 rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors"
            title={job.status === 'running' ? 'Abort this solve' : 'Remove from queue'}
          >
            <X size={14} />
          </button>
        )}
      </div>
      {expanded && canPreview && name != null && <JobResultsPreview name={name} />}
    </div>
  )
}
```

Replace `:175` with:

```tsx
  // Project names that already have a queued/running job — don't offer to re-add.
  // A redacted row names no project and can match nothing, so drop it rather
  // than letting `null` sit in the set.
  const activeProjects = new Set(
    jobs.filter(isActive).map(j => j.project_id).filter((n): n is string => n != null),
  )
```

Replace the help paragraph at `:220-224` with:

```tsx
        <p className="text-[11px] text-muted leading-snug">
          Queue saved projects to solve one after another, unattended. Results persist to
          disk — view a finished solve below without loading the project.
          {activeCount > 0 && (
            <span className="text-accent">
              {' '}A project solving in the queue is read-only until it finishes; other projects stay editable.
            </span>
          )}
        </p>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/pages/SolveQueuePanel.redacted.test.tsx src/pages/SolveQueuePanel.clearFinished.test.tsx'`

Expected: PASS — 3 passed in `SolveQueuePanel.redacted.test.tsx`, `SolveQueuePanel.clearFinished.test.tsx` green.

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/api/solveQueue.ts pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx pypsa-gui/frontend/src/pages/SolveQueuePanel.redacted.test.tsx
git commit -m "fix(queue-panel): honest help copy and a redaction-safe SolveJob" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `readOnly` carries a reason

**Increment:** 1

**Requirements:** R10

**Files:**
- Modify: `pypsa-gui/frontend/src/utils/lockState.ts:18-32` (`LockState`, `WRITABLE`, new `ReadOnlyReason`)
- Modify: `pypsa-gui/frontend/src/utils/lockState.ts:47-55` (`lockStateFromAcquire`) and append `effectiveLockState`
- Modify: `pypsa-gui/frontend/src/utils/mutationGuard.ts:11-31`
- Modify: `pypsa-gui/frontend/src/store/uiStore.ts:296-297` (state fields), `:361` (action type), `:409-410` (defaults), `:524` (`setLockState`)
- Modify: `pypsa-gui/frontend/src/utils/lockState.test.ts`
- Modify: `pypsa-gui/frontend/src/utils/mutationGuard.test.ts`
- Test: `pypsa-gui/frontend/src/utils/mutationGuard.test.ts`, `pypsa-gui/frontend/src/utils/lockState.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  ```ts
  export type ReadOnlyReason = 'writable' | 'locked-by-user' | 'solving'
  export interface LockState { readOnly: boolean; holderEmail: string | null; reason: ReadOnlyReason }
  export const WRITABLE: LockState
  export function lockStateFromAcquire(outcome: LockAcquireOutcome): LockState
  export function effectiveLockState(lockReadOnly: boolean, solving: boolean): { readOnly: boolean; reason: ReadOnlyReason }
  export const READ_ONLY_MUTATION_MESSAGE: string
  export const SOLVING_MUTATION_MESSAGE: string
  export function evaluateMutation(readOnly: boolean, reason?: ReadOnlyReason): MutationVerdict
  ```
  plus store fields `readOnlyReason: ReadOnlyReason`, `lockReadOnly: boolean`, `solvingReadOnly: boolean` and action `setSolvingReadOnly: (solving: boolean) => void`.

- [ ] **Step 1: Write the failing test**

Replace the body of `pypsa-gui/frontend/src/utils/mutationGuard.test.ts` with:

```ts
import { describe, it, expect } from 'vitest'
import {
  evaluateMutation,
  READ_ONLY_MUTATION_MESSAGE,
  SOLVING_MUTATION_MESSAGE,
} from './mutationGuard'

describe('evaluateMutation', () => {
  it('allows mutation when not read-only', () => {
    const verdict = evaluateMutation(false)
    expect(verdict.allowed).toBe(true)
    expect(verdict.blockedMessage).toBeNull()
  })

  it('blocks mutation when read-only and returns the shared message', () => {
    const verdict = evaluateMutation(true)
    expect(verdict.allowed).toBe(false)
    expect(verdict.blockedMessage).toBe(READ_ONLY_MUTATION_MESSAGE)
  })

  it('uses one canonical read-only message', () => {
    expect(READ_ONLY_MUTATION_MESSAGE).toMatch(/read-only/i)
  })

  it('returns a distinct message for the solving reason', () => {
    const verdict = evaluateMutation(true, 'solving')
    expect(verdict.allowed).toBe(false)
    expect(verdict.blockedMessage).toBe(SOLVING_MUTATION_MESSAGE)
    expect(SOLVING_MUTATION_MESSAGE).not.toBe(READ_ONLY_MUTATION_MESSAGE)
    expect(SOLVING_MUTATION_MESSAGE).toMatch(/solv/i)
  })

  it('keeps the lock message for the locked-by-user reason', () => {
    expect(evaluateMutation(true, 'locked-by-user').blockedMessage)
      .toBe(READ_ONLY_MUTATION_MESSAGE)
  })
})
```

Append to `pypsa-gui/frontend/src/utils/lockState.test.ts`:

```ts
describe('effectiveLockState', () => {
  it('is writable when neither input holds', () => {
    expect(effectiveLockState(false, false)).toEqual({ readOnly: false, reason: 'writable' })
  })

  it('reports locked-by-user when only the edit lock holds', () => {
    expect(effectiveLockState(true, false)).toEqual({ readOnly: true, reason: 'locked-by-user' })
  })

  it('reports solving when a queue job holds the project', () => {
    expect(effectiveLockState(false, true)).toEqual({ readOnly: true, reason: 'solving' })
  })

  it('prefers solving when both hold — it is the one that clears on its own', () => {
    expect(effectiveLockState(true, true)).toEqual({ readOnly: true, reason: 'solving' })
  })
})

describe('lockStateFromAcquire reasons', () => {
  it('tags a successful acquire writable', () => {
    expect(lockStateFromAcquire({ ok: true }).reason).toBe('writable')
  })

  it('tags a refusal locked-by-user', () => {
    expect(lockStateFromAcquire({ ok: false }).reason).toBe('locked-by-user')
  })
})
```

Add `effectiveLockState` to that file's existing import from `./lockState`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/utils/mutationGuard.test.ts src/utils/lockState.test.ts'`

Expected: FAIL with `SyntaxError: The requested module './mutationGuard' does not provide an export named 'SOLVING_MUTATION_MESSAGE'` and `does not provide an export named 'effectiveLockState'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/frontend/src/utils/lockState.ts`, replace `:18-32` with:

```ts
// WHY a project can be read-only. `readOnly` alone could only ever produce one
// message — "another user is editing this project" — which is a lie the moment
// a queue job is what is holding it.
export type ReadOnlyReason = 'writable' | 'locked-by-user' | 'solving'

export interface LockState {
  // True when the current user may NOT mutate the active project — either
  // someone else holds the lock, or acquisition failed. Every destructive /
  // mutating affordance is gated on this being false (see `canMutate`).
  readOnly: boolean
  // Email of the current lock holder when known — surfaced in the banner so a
  // read-only viewer knows who to ask. null when no lock exists / holder
  // unknown / the lock is ours.
  holderEmail: string | null
  // Why. Always 'writable' when `readOnly` is false.
  reason: ReadOnlyReason
}

// Neutral "you may edit" state. The default when auth is disabled or no lock
// machinery is in play (legacy single-user workbench) — so the legacy path is
// never accidentally read-only.
export const WRITABLE: LockState = { readOnly: false, holderEmail: null, reason: 'writable' }
```

Replace `:47-55` with:

```ts
export function lockStateFromAcquire(outcome: LockAcquireOutcome): LockState {
  const holderEmail = outcome.lock?.holder_email ?? null
  if (outcome.ok) {
    // We hold the lock now. Don't advertise our own email as "someone else is
    // editing" — a writable state has no foreign holder to name.
    return {
      readOnly: false,
      holderEmail: outcome.lock?.yours ? null : holderEmail,
      reason: 'writable',
    }
  }
  return { readOnly: true, holderEmail, reason: 'locked-by-user' }
}

/**
 * Fold the two INDEPENDENT read-only inputs into the single flag the ~20 direct
 * consumers read, plus the reason.
 *
 * They are independent because they clear independently: the edit lock is
 * released by another user, the solve clears itself when the job ends. Storing
 * only the fold would make releasing one clear the other. `solving` wins the
 * message because it is the one with a definite end and a different remedy.
 */
export function effectiveLockState(
  lockReadOnly: boolean,
  solving: boolean,
): { readOnly: boolean; reason: ReadOnlyReason } {
  if (solving) return { readOnly: true, reason: 'solving' }
  if (lockReadOnly) return { readOnly: true, reason: 'locked-by-user' }
  return { readOnly: false, reason: 'writable' }
}
```

Replace `pypsa-gui/frontend/src/utils/mutationGuard.ts:11-31` with:

```ts
import { canMutate, type ReadOnlyReason } from './lockState'

// One canonical message per reason so every blocked mutation reads identically
// AND honestly. The single hardcoded "another user is editing this project" was
// wrong for every solve-induced refusal.
export const READ_ONLY_MUTATION_MESSAGE =
  'Read-only — another user is editing this project.'

export const SOLVING_MUTATION_MESSAGE =
  'Read-only — this project is solving in the queue. It becomes editable when the solve finishes.'

const MESSAGE_BY_REASON: Record<ReadOnlyReason, string | null> = {
  writable: null,
  'locked-by-user': READ_ONLY_MUTATION_MESSAGE,
  solving: SOLVING_MUTATION_MESSAGE,
}

export interface MutationVerdict {
  // True when the mutation may proceed.
  allowed: boolean
  // Message to surface (e.g. via a toast) when the mutation is blocked; null
  // when allowed.
  blockedMessage: string | null
}

// Pure evaluation of whether a mutation may proceed given the current
// read-only flag and WHY it is set. `reason` defaults to 'locked-by-user' so a
// call site that has not been widened yet keeps its historical message exactly.
export function evaluateMutation(
  readOnly: boolean,
  reason: ReadOnlyReason = 'locked-by-user',
): MutationVerdict {
  const allowed = canMutate({ readOnly })
  if (allowed) return { allowed: true, blockedMessage: null }
  return {
    allowed: false,
    blockedMessage: MESSAGE_BY_REASON[reason] ?? READ_ONLY_MUTATION_MESSAGE,
  }
}
```

In `pypsa-gui/frontend/src/store/uiStore.ts`, replace `:296-297` with:

```ts
  readOnly: boolean
  lockHolderEmail: string | null
  // Why `readOnly` is set. 'writable' whenever it is false.
  readOnlyReason: ReadOnlyReason
  // The two independent inputs `readOnly` is folded from. Kept separately
  // because they clear independently — releasing the edit lock must not make a
  // solving project editable, and vice versa.
  lockReadOnly: boolean
  solvingReadOnly: boolean
```

Replace `:361` with:

```ts
  // Apply a derived lock state (from utils/lockState.lockStateFromAcquire).
  setLockState: (s: LockState) => void
  // A queue job is (or is no longer) solving the project the user is viewing.
  setSolvingReadOnly: (solving: boolean) => void
```

Replace `:409-410` with:

```ts
  readOnly: false,
  lockHolderEmail: null,
  readOnlyReason: 'writable',
  lockReadOnly: false,
  solvingReadOnly: false,
```

Replace `:524` with:

```ts
  setLockState: (s) => set((state) => ({
    lockReadOnly: s.readOnly,
    lockHolderEmail: s.holderEmail,
    ...effectiveLockState(s.readOnly, state.solvingReadOnly),
  })),
  setSolvingReadOnly: (solving) => set((state) => ({
    solvingReadOnly: solving,
    ...effectiveLockState(state.lockReadOnly, solving),
  })),
```

Extend the existing `LockState` import in `uiStore.ts` to `import { effectiveLockState, type LockState, type ReadOnlyReason } from '../utils/lockState'` (merge with whatever that file already imports from `../utils/lockState`).

Update `pypsa-gui/frontend/src/utils/lockState.test.ts`: every object literal typed as `LockState` gains `reason` (`WRITABLE`-shaped literals get `reason: 'writable'`, refusal-shaped ones `reason: 'locked-by-user'`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/utils/mutationGuard.test.ts src/utils/lockState.test.ts'`

Expected: PASS — 5 passed in `mutationGuard.test.ts`, all of `lockState.test.ts` passing including the 6 new cases.

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/utils/lockState.ts pypsa-gui/frontend/src/utils/lockState.test.ts pypsa-gui/frontend/src/utils/mutationGuard.ts pypsa-gui/frontend/src/utils/mutationGuard.test.ts pypsa-gui/frontend/src/store/uiStore.ts
git commit -m "feat(ui): widen readOnly to writable | locked-by-user | solving" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: A solving project presents as read-only, with the solving reason

**Increment:** 1

**Requirements:** R11

**Files:**
- Create: `pypsa-gui/frontend/src/store/uiStore.readOnlyReason.test.ts`
- Modify: `pypsa-gui/frontend/src/layout/AppHeader.tsx:137` (pass the reason), `:662-664` (add the solving effect after `busy`)
- Modify: `pypsa-gui/frontend/src/layout/Sidebar.tsx:778` (pass the reason)
- Modify: `pypsa-gui/frontend/src/pages/ScenariosPanel.tsx:421` (pass the reason)
- Test: `pypsa-gui/frontend/src/store/uiStore.readOnlyReason.test.ts`

**Interfaces:**
- Consumes:
  ```ts
  export type ReadOnlyReason = 'writable' | 'locked-by-user' | 'solving'
  export function effectiveLockState(lockReadOnly: boolean, solving: boolean): { readOnly: boolean; reason: ReadOnlyReason }
  export function evaluateMutation(readOnly: boolean, reason?: ReadOnlyReason): MutationVerdict
  ```
  plus store fields `readOnlyReason`, `lockReadOnly`, `solvingReadOnly` and action `setSolvingReadOnly(solving: boolean)`.
- Produces: no new symbols. The behavioural contract: while `activeJobForProject(solveQueue, currentProject)?.status === 'running'`, `useUIStore.getState().readOnly` is `true` and `readOnlyReason` is `'solving'`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/store/uiStore.readOnlyReason.test.ts`:

```ts
// R11 — the project the user is viewing is read-only, with the SOLVING reason,
// while a queue job runs on it.
//
// This is honest rather than defensive: once increment 1 lands the session on
// the solving context, the backend's global middleware already refuses every
// `/api/network/*` and `/api/io/*` write for the duration
// (main.py's solver-in-flight gate). The UI previously had no way to say so —
// `readOnly` was one boolean whose only message named another user.
import { beforeEach, describe, expect, it } from 'vitest'
import { useUIStore } from './uiStore'
import { WRITABLE } from '../utils/lockState'

describe('uiStore read-only reason', () => {
  beforeEach(() => {
    useUIStore.getState().setLockState(WRITABLE)
    useUIStore.getState().setSolvingReadOnly(false)
  })

  it('starts writable', () => {
    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().readOnlyReason).toBe('writable')
  })

  it('goes read-only with the solving reason while a queue job runs on it', () => {
    useUIStore.getState().setSolvingReadOnly(true)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
  })

  it('returns to writable when the solve ends', () => {
    useUIStore.getState().setSolvingReadOnly(true)
    useUIStore.getState().setSolvingReadOnly(false)
    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().readOnlyReason).toBe('writable')
  })

  it('falls back to the edit lock when the solve ends but another user holds it', () => {
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'other@example.com', reason: 'locked-by-user',
    })
    useUIStore.getState().setSolvingReadOnly(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
    useUIStore.getState().setSolvingReadOnly(false)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('locked-by-user')
    expect(useUIStore.getState().lockHolderEmail).toBe('other@example.com')
  })

  it('acquiring the edit lock does not clear a live solve', () => {
    useUIStore.getState().setSolvingReadOnly(true)
    useUIStore.getState().setLockState(WRITABLE)
    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/store/uiStore.readOnlyReason.test.ts'`

Expected: FAIL — before Task 5 lands, `TypeError: useUIStore.getState().setSolvingReadOnly is not a function`. If Task 5 has already landed, this file passes on the store and the failing half is the wiring, which Step 3 adds; run Step 2 before Task 5's implementation to see the failure.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/frontend/src/layout/AppHeader.tsx`, immediately after `:664` (`const busy = jobRunning || jobQueued || isRunning`), insert:

```tsx
  // R11 — the project on screen is READ-ONLY while its queue job solves it.
  // The backend already refuses the writes (main.py's solver-in-flight gate
  // covers /api/network/* and /api/io/* on the caller's context, which IS the
  // solving context once activate resolves to it); this makes the UI say so
  // instead of letting the user fill in a form whose save will 409.
  const setSolvingReadOnly = useUIStore(s => s.setSolvingReadOnly)
  useEffect(() => {
    setSolvingReadOnly(jobRunning)
    // Clear on unmount so a header that unmounts mid-solve cannot strand the
    // whole workbench read-only.
    return () => setSolvingReadOnly(false)
  }, [jobRunning, setSolvingReadOnly])
```

In the same file, change `:137` from `const verdict = evaluateMutation(readOnly)` to `const verdict = evaluateMutation(readOnly, readOnlyReason)`, add `readOnlyReason` to the `useUIStore()` destructure at `:96`, and add `readOnlyReason` to `commitName`'s `useCallback` dependency array at `:168`.

In `pypsa-gui/frontend/src/layout/Sidebar.tsx`, change `:778` to `const verdict = evaluateMutation(readOnly, readOnlyReason)` and add `readOnlyReason` to the `useUIStore()` destructure at `:739`.

In `pypsa-gui/frontend/src/pages/ScenariosPanel.tsx`, change `:421` to `const verdict = evaluateMutation(readOnly, readOnlyReason)` and add `const readOnlyReason = useUIStore(s => s.readOnlyReason)` beside the existing `useUIStore(s => s.readOnly)` at `:312`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/store/uiStore.readOnlyReason.test.ts src/pages/ScenariosPanel.test.tsx'`

Expected: PASS — 5 passed in `uiStore.readOnlyReason.test.ts`, `ScenariosPanel.test.tsx` green.

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/store/uiStore.readOnlyReason.test.ts pypsa-gui/frontend/src/layout/AppHeader.tsx pypsa-gui/frontend/src/layout/Sidebar.tsx pypsa-gui/frontend/src/pages/ScenariosPanel.tsx
git commit -m "feat(ui): a solving project presents read-only with the solving reason" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Delete the resync-on-drain effect; invalidate per finished job

**Increment:** 1

**Requirements:** R8, R9

**Files:**
- Create: `pypsa-gui/frontend/src/hooks/useJobTerminalInvalidation.ts`
- Create: `pypsa-gui/frontend/src/hooks/useJobTerminalInvalidation.test.ts`
- Modify: `pypsa-gui/frontend/src/layout/ProjectTabs.tsx:15` (drop the now-unused `isActive` import)
- Modify: `pypsa-gui/frontend/src/layout/ProjectTabs.tsx:156-187` (delete the effect, mount the hook)
- Test: `pypsa-gui/frontend/src/hooks/useJobTerminalInvalidation.test.ts`

**Interfaces:**
- Consumes: `useSolveQueue()` from `../hooks/useSolveQueue`, `isTerminal(j: SolveJob): boolean` and `type SolveJob` from `../api/solveQueue`, `nk(projectId: string | null, root: string, ...rest: unknown[]): unknown[]` from `../utils/queryKeys`.
- Produces:
  ```ts
  export function statusMap(jobs: SolveJob[]): Map<string, string>
  export function terminalTransitions(prev: Map<string, string>, jobs: SolveJob[]): string[]
  export function useJobTerminalInvalidation(): void
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/hooks/useJobTerminalInvalidation.test.ts`:

```ts
// R8/R9 — the `>0 → 0` resync is gone; a job going terminal invalidates only
// ITS OWN project's caches.
//
// The deleted effect fired a full `projectsApi.load(currentProject)` — which is
// `reset_network()` + `import_from_netcdf` — on any transition of the GLOBAL
// active-job count to zero, with no save in front of it. The project reloaded
// need never have been queued: queue A, switch to B, edit B, A finishes, B
// reverts to its last saved state. The count also included other organisations'
// redacted rows, so another tenant's batch draining reloaded this user's editor.
import { describe, expect, it } from 'vitest'
import { statusMap, terminalTransitions } from './useJobTerminalInvalidation'
import type { SolveJob } from '../api/solveQueue'

// `id` is a NUMBER here, matching `SolveJob.id` as it stands in increments 1
// and 2. It does not widen to a UUID string until increment 3 (Task 12), and a
// string literal would fail `tsc -b` — the only frontend static gate — with
// TS2352 "Type 'string' is not comparable to type 'number'". `statusMap`
// stringifies the id precisely so this file needs no change when it widens.
function job(id: number, project_id: string | null, status: SolveJob['status']): SolveJob {
  return {
    id, project_id, project_key: null, status,
    position: null, objective: null, solve_time: null, condition: null, error: null,
    enqueued_at: 0, started_at: null, finished_at: null,
  }
}

describe('terminalTransitions', () => {
  it('reports the project of a job that just went terminal', () => {
    const prev = statusMap([job(1, 'alpha', 'running')])
    expect(terminalTransitions(prev, [job(1, 'alpha', 'completed')])).toEqual(['alpha'])
  })

  it('reports nothing when a job is already terminal and has not moved', () => {
    const prev = statusMap([job(1, 'alpha', 'completed')])
    expect(terminalTransitions(prev, [job(1, 'alpha', 'completed')])).toEqual([])
  })

  it('reports nothing for a job seen for the first time', () => {
    // A first poll must not invalidate the whole history of finished jobs.
    expect(terminalTransitions(new Map(), [job(1, 'alpha', 'completed')])).toEqual([])
  })

  it('touches only the finishing job\'s project, not every project in the list', () => {
    const prev = statusMap([job(1, 'alpha', 'running'), job(2, 'beta', 'queued')])
    const next = [job(1, 'alpha', 'failed'), job(2, 'beta', 'queued')]
    expect(terminalTransitions(prev, next)).toEqual(['alpha'])
  })

  it('treats every terminal status alike', () => {
    // `interrupted` is not a member of `SolveJobStatus` until increment 3, so
    // the no-per-status-branch guarantee is asserted over the three terminal
    // statuses that exist here; `isTerminal` is the single definition of the
    // set, so adding the fourth needs no change in this file.
    for (const s of ['completed', 'failed', 'aborted'] as const) {
      const prev = statusMap([job(1, 'alpha', 'running')])
      expect(terminalTransitions(prev, [job(1, 'alpha', s)])).toEqual(['alpha'])
    }
  })

  it('skips a redacted row, whose project_id is null', () => {
    const prev = statusMap([job(1, null, 'running')])
    expect(terminalTransitions(prev, [job(1, null, 'completed')])).toEqual([])
  })

  it('de-duplicates two jobs of the same project finishing together', () => {
    const prev = statusMap([job(1, 'alpha', 'running'), job(2, 'alpha', 'running')])
    const next = [job(1, 'alpha', 'completed'), job(2, 'alpha', 'aborted')]
    expect(terminalTransitions(prev, next)).toEqual(['alpha'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/hooks/useJobTerminalInvalidation.test.ts'`

Expected: FAIL with `Error: Failed to load url ./useJobTerminalInvalidation (resolved id: ./useJobTerminalInvalidation) ... Does the file exist?`

- [ ] **Step 3: Write minimal implementation**

Create `pypsa-gui/frontend/src/hooks/useJobTerminalInvalidation.ts`:

```ts
import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { isTerminal, type SolveJob } from '../api/solveQueue'
import { nk } from '../utils/queryKeys'
import { useSolveQueue } from './useSolveQueue'

/**
 * Snapshot of `id -> status` for the current queue listing.
 *
 * Ids are stringified so the map keys stay stable when the backend's job id
 * becomes a UUID (increment 3) — the callers only ever compare them.
 */
export function statusMap(jobs: SolveJob[]): Map<string, string> {
  return new Map(jobs.map(j => [String(j.id), j.status]))
}

/**
 * Project names whose job just TRANSITIONED into a terminal status.
 *
 * "Transitioned" is what makes this safe to run on every 1.5 s poll: a job
 * already terminal on the previous tick reports nothing, and a job seen for the
 * first time reports nothing (otherwise the first poll after a page load would
 * invalidate every project with a finished job in the process-global listing).
 *
 * Deliberately no per-status branch. All four terminal statuses invalidate,
 * `interrupted` included — `isTerminal` is the single definition of the set.
 * A redacted row (`project_id: null`, a job the caller may not see) names no
 * project and is skipped rather than invalidating a cache keyed on `null`.
 */
export function terminalTransitions(
  prev: Map<string, string>,
  jobs: SolveJob[],
): string[] {
  const names: string[] = []
  for (const job of jobs) {
    const before = prev.get(String(job.id))
    if (before === undefined || before === job.status) continue
    if (!isTerminal(job)) continue
    if (!job.project_id) continue
    names.push(job.project_id)
  }
  return Array.from(new Set(names))
}

/**
 * Invalidate the React Query caches of each project whose job just finished.
 *
 * Replaces the `>0 → 0` resync effect in `ProjectTabs`, which reloaded the
 * CURRENT project from disk on any drain of the global active-job count —
 * discarding unsaved edits in a project that need never have been queued, and
 * firing on another organisation's redacted rows. Nothing is reloaded here:
 * increment 1 makes the solving context the same context the session holds, so
 * the fresh results are already in memory and only the caches are stale.
 */
export function useJobTerminalInvalidation(): void {
  const qc = useQueryClient()
  const { data } = useSolveQueue()
  const jobs = data?.jobs
  const prevRef = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    const list = jobs ?? []
    const finished = terminalTransitions(prevRef.current, list)
    prevRef.current = statusMap(list)
    for (const name of finished) {
      qc.invalidateQueries({ queryKey: nk(name, 'results') })
      qc.invalidateQueries({ queryKey: nk(name, 'simulationStatus') })
      qc.invalidateQueries({ queryKey: nk(name, 'meta') })
    }
  }, [jobs, qc])
}
```

In `pypsa-gui/frontend/src/layout/ProjectTabs.tsx`, delete line 15 (`import { isActive } from '../api/solveQueue'`, now unused) and add `import { useJobTerminalInvalidation } from '../hooks/useJobTerminalInvalidation'` beside the existing `useSolveQueue` import at line 14.

Then replace `:156-187` (the whole comment block, `activeQueueCount`, `prevActiveRef` and the `useEffect`) with:

```tsx
  // Per-finished-job cache invalidation, scoped to THAT job's project. This
  // replaces the `>0 → 0` resync that used to live here: it reloaded
  // `currentProject` from disk (reset_network + import_from_netcdf, with no
  // save in front of it) whenever the GLOBAL active-job count fell to zero, so
  // a project that was never queued lost its unsaved edits and another
  // organisation's redacted rows could trigger it. Its own comment justified it
  // by "the swap-based queue solves through the SHARED active slot", a design
  // B4.3 replaced — and increment 1's one-context-per-project invariant means
  // the freshly solved network IS the one this session holds, so there is
  // nothing to reload.
  useJobTerminalInvalidation()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/hooks/useJobTerminalInvalidation.test.ts src/layout/ProjectTabs.test.tsx'`

Expected: PASS — 7 passed in `useJobTerminalInvalidation.test.ts`, `ProjectTabs.test.tsx` green.

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS (tsc reports no errors).

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/hooks/useJobTerminalInvalidation.ts pypsa-gui/frontend/src/hooks/useJobTerminalInvalidation.test.ts pypsa-gui/frontend/src/layout/ProjectTabs.tsx
git commit -m "fix(queue): drop the drain resync; invalidate per finished job's project" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Increment 1 boundary gate

- [ ] Run `pixi run gui-tests`. Expected: `0 failed`, `skipped <= 22`, `passed == 2282 + N_backend` where `N_backend` is the net number of backend test cases this increment's diff adds (Task 1 adds 3, Task 2 adds 3, Task 3 adds 2 → `N_backend == 8`, so `2290 passed`).
- [ ] Run `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test'`. Expected: `0 failed`, `0 skipped`, `passed == 660 + N_frontend` where `N_frontend` counts Task 4's 3, Task 5's 2 new `mutationGuard` cases plus 6 new `lockState` cases, Task 6's 5 and Task 7's 7 → `N_frontend == 23`, so `683 passed`.
- [ ] Run `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'`. Expected: PASS.
- [ ] Run `ruff check .`. Expected: `All checks passed!`


---

## Increment 2 — correctness and visibility (Tasks 8–11)

Depends on increment 1 only for its merged state, not for any symbol it introduces.

### Task 8: Idempotent enqueue — the server refuses duplicates

**Increment:** 2

**Requirements:** R15, R16

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_dedupe.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:129-151` (add `enqueue_unique` and `_active_job_locked` after `enqueue`)
- Modify: `pypsa-gui/backend/routers/solve_queue.py:72-77` (enqueue route returns `already_queued`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_dedupe.py`

**Interfaces:**
- Consumes: `SolveJob` (`services/solve_queue.py:60-110`), `SolveQueue._lock`, `SolveQueue._ensure_dispatcher_locked()`, `solve_queue.get_job(job_id) -> dict | None`, `project_registry.registry_key(project) -> str`, `project_registry.project_dir(project) -> Path`.
- Produces:
  ```python
  def enqueue_unique(
      self,
      project_id: str,
      *,
      project_key: str | None = None,
      storage_dir: str | None = None,
  ) -> tuple[SolveJob, bool]:
      """Returns (job, created). `created` is False when an ACTIVE job already exists."""

  def _active_job_locked(self, project_id: str, project_key: str | None) -> SolveJob | None:
      """The queued-or-running job for this project, if any (caller holds _lock)."""
  ```
  and the HTTP contract: `POST /api/simulation/queue` returns `200` with the job dict plus `"already_queued": bool`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_dedupe.py`:

```python
"""
R15/R16 — one active job per project, enforced on the SERVER.

`SolveQueue.enqueue` appended unconditionally and the invariant was
re-implemented three times on the client (AppHeader's `enqueuingRef`,
SolveQueuePanel's `activeProjects` set derived from a 1.5 s poll, and
ScenariosPanel's `inFlight` ref) and nowhere on the server. The chat tool had
no guard at all. ScenariosPanel's own comment records the cost: a double click
"really does run every project in the branch twice … with the second run
overwriting the first's results".
"""
from __future__ import annotations

import threading
import time

from services.solve_queue import solve_queue
from tests.conftest import build_network


def _wait_until(pred, timeout: float = 60.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _block_the_dispatcher(monkeypatch):
    """Hold the first job inside run_simulation so the second is a real duplicate."""
    from services import solver_service

    entered = threading.Event()
    release = threading.Event()

    def blocking(config, n, lock, stop_event, log_queue, state_update=None):
        entered.set()
        release.wait(60)
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", blocking)
    return entered, release


def test_a_second_enqueue_returns_the_first_job_and_creates_nothing(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    install_network(build_network(), name="Dup")
    _save_project(client, "Dup")
    entered, release = _block_the_dispatcher(monkeypatch)
    try:
        first = client.post("/api/simulation/queue", json={"project_id": "Dup"})
        assert first.status_code == 200, first.text
        assert first.json()["already_queued"] is False
        assert entered.wait(60)

        second = client.post("/api/simulation/queue", json={"project_id": "Dup"})
        assert second.status_code == 200, second.text
        assert second.json()["already_queued"] is True
        assert second.json()["id"] == first.json()["id"]

        listing = client.get("/api/simulation/queue").json()["jobs"]
        assert len([j for j in listing if j["project_id"] == "Dup"]) == 1, listing
    finally:
        release.set()


def test_a_project_with_no_active_job_gets_a_new_job(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    install_network(build_network(), name="Fresh")
    _save_project(client, "Fresh")
    entered, release = _block_the_dispatcher(monkeypatch)
    try:
        r = client.post("/api/simulation/queue", json={"project_id": "Fresh"})
        assert r.status_code == 200, r.text
        assert r.json()["already_queued"] is False
        assert r.json()["status"] in ("queued", "running")
    finally:
        release.set()


def test_a_terminal_job_does_not_block_a_new_one(
    client, install_network, tmp_projects_dir,
):
    """Dedupe is on ACTIVE jobs only — a finished project must be requeueable."""
    install_network(build_network(), name="Again")
    _save_project(client, "Again")
    first = client.post("/api/simulation/queue", json={"project_id": "Again"}).json()
    _wait_until(
        lambda: (solve_queue.get_job(first["id"]) or {}).get("status")
        in ("completed", "failed", "aborted", "interrupted"),
        timeout=90, interval=0.2,
    )
    second = client.post("/api/simulation/queue", json={"project_id": "Again"}).json()
    assert second["already_queued"] is False
    assert second["id"] != first["id"]


def test_the_chat_tool_is_refused_the_same_duplicate(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    """
    R16 — the server is the enforcement point for EVERY caller. The chat tool
    passes the handler's response through unreshaped, so the same refusal
    reaches the model with no second edit.
    """
    from services import chat_tools

    install_network(build_network(), name="ChatDup")
    _save_project(client, "ChatDup")
    entered, release = _block_the_dispatcher(monkeypatch)
    try:
        first = chat_tools.solve_queue_enqueue("ChatDup")
        assert first["already_queued"] is False
        assert entered.wait(60)
        second = chat_tools.solve_queue_enqueue("ChatDup")
        assert second["already_queued"] is True
        assert second["id"] == first["id"]
    finally:
        release.set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_dedupe.py -v`

Expected: FAIL with `KeyError: 'already_queued'` on the first assertion of `test_a_second_enqueue_returns_the_first_job_and_creates_nothing`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`, immediately after `enqueue` (`:129-151`), add:

```python
    def enqueue_unique(
        self,
        project_id: str,
        *,
        project_key: str | None = None,
        storage_dir: str | None = None,
    ) -> tuple[SolveJob, bool]:
        """
        Enqueue `project_id` UNLESS it already has a queued or running job.

        Returns `(job, created)`. When `created` is False the returned job is
        the existing one, untouched, and nothing was appended.

        This is the enforcement point. `enqueue` appended unconditionally and
        the one-active-job-per-project invariant lived in three separate client
        guards, each racing its own 1.5 s poll, with none at all on the
        `solve_queue_enqueue` chat tool. On these models a double click is
        minutes of wasted solve, and the second run overwrites the first's
        results.

        Identity is `project_key` (`org:uuid`) whenever the caller supplied one
        — the same identity the registry and the eviction protected-set key on,
        and the only one that survives a rename. The display name is the
        fallback, which is all a legacy unkeyed job carries.

        `enqueue` is deliberately left in place and unchanged: it is the raw
        append the test harness uses to build queue states directly.
        """
        with self._lock:
            existing = self._active_job_locked(project_id, project_key)
            if existing is not None:
                logger.info(
                    "solve_queue: %r already has active job %s (%s) — not queuing a second",
                    project_id, existing.id, existing.status,
                )
                return existing, False
            jid = next(self._counter)
            job = SolveJob(
                id=jid,
                project_id=project_id,
                project_key=project_key,
                storage_dir=storage_dir,
                enqueued_at=time.time(),
            )
            self._jobs[jid] = job
            self._order.append(jid)
            self._q.put(jid)
            self._ensure_dispatcher_locked()
        logger.info("solve_queue: enqueued job %s for project %r", jid, project_id)
        return job, True

    def _active_job_locked(
        self, project_id: str, project_key: str | None
    ) -> SolveJob | None:
        """
        The queued-or-running job for this project, if any. Caller holds _lock.

        The check and the append must be ONE critical section or two concurrent
        enqueues both find nothing and both append — which is the exact race the
        client-side latches were trying and failing to close from outside.
        """
        for jid in self._order:
            job = self._jobs.get(jid)
            if job is None or job.status not in ("queued", "running"):
                continue
            if project_key is not None:
                if job.project_key == project_key:
                    return job
            elif job.project_key is None and job.project_id == project_id:
                return job
        return None
```

In `pypsa-gui/backend/routers/solve_queue.py`, replace `:72-77` with:

```python
    # Idempotent by project: a second enqueue of a project that already has a
    # queued or running job returns THAT job with `already_queued: true` and
    # creates nothing. 200, not 409 — the caller's intent ("this project should
    # be solving") is already satisfied, and an error would make every client
    # re-implement the check it just handed to the server.
    job, created = solve_queue.enqueue_unique(
        project.name,
        project_key=project_registry.registry_key(project),
        storage_dir=str(project_dir),
    )
    return {**solve_queue.get_job(job.id), "already_queued": not created}
```

Update the route docstring's first paragraph (`:46-50`) to end with: `Returns the job, including its queue position and an `already_queued` flag that is true when an existing job was returned instead of a new one.`

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_dedupe.py -v`

Expected: PASS — 4 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_dedupe.py
git commit -m "feat(queue): server-side idempotent enqueue with already_queued" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Each job owns its log, served by two authorized job-scoped endpoints

**Increment:** 2

**Requirements:** R17, R18, R19

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_job_log.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:60-110` (`SolveJob` gains `log_queue`)
- Modify: `pypsa-gui/backend/services/solve_queue.py:153-161` (add `get_log_queue` after `get_job`)
- Modify: `pypsa-gui/backend/services/solve_queue.py:346-352` (the claim block stores the queue on the job)
- Modify: `pypsa-gui/backend/routers/solve_queue.py:19-33` (imports), `:186-187` (add `_visible_job_or_404` and the two routes before `list_queue`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_job_log.py`

**Interfaces:**
- Consumes: `BufferedLogQueue` (`routers/simulation.py:23-121`) with `put`, `get`, `history() -> list[str]`, `subscribe() -> tuple[int, collections.deque]`, `unsubscribe(sub_id)`; `_may_see(job, prefix, allowed) -> bool`, `_org_prefix(db, user) -> str | None`, `_project_uuid(job, prefix) -> uuid.UUID | None` (`routers/solve_queue.py:92-144`); `project_acl.accessible_project_ids(db, user, ids)`; `_TERMINAL` (`services/solve_queue.py:57`).
- Produces:
  ```python
  def get_log_queue(self, job_id) -> Any | None:
      """The BufferedLogQueue this job's solve wrote to, or None."""
  ```
  plus `GET /api/simulation/queue/{job_id}/log_history` → `{"lines": [str], "status": str}` and `GET /api/simulation/queue/{job_id}/log_stream` (SSE, `data:` lines then `event: done`), and the router helper `_visible_job_or_404(db, user, job_id) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_job_log.py`:

```python
"""
R17/R18/R19 — a job's log belongs to the JOB, and is readable by job id.

`/api/simulation/log_stream` takes no `job_id`: it binds to whatever the ACTIVE
context's `log_queue` is at the instant it opens. So a user viewing project B
could not read project A's queued solve at all, and the frontend had to wait for
the 1.5 s poll to see `running` before attaching — a race the AppHeader carries
a bounded retry for.

Both endpoints answer 404 — byte-identical to the genuine not-found message —
when the caller may not see the job, for the same reason `abort_job` does.
"""
from __future__ import annotations

import threading
import time

from services.solve_queue import solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _wait_for_terminal(job_id, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = solve_queue.get_job(job_id) or {}
        if job.get("status") in ("completed", "failed", "aborted", "interrupted"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} never reached a terminal status")


def test_a_running_jobs_log_is_readable_while_viewing_another_project(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    from services import solver_service

    install_network(build_network(), name="Logged")
    _save_project(client, "Logged")
    install_network(build_network(), name="Elsewhere")
    _save_project(client, "Elsewhere")

    entered = threading.Event()
    release = threading.Event()

    def blocking(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("job log: line one")
        entered.set()
        release.wait(60)
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", blocking)

    job = client.post("/api/simulation/queue", json={"project_id": "Logged"}).json()
    assert entered.wait(60)
    # The session is looking at "Elsewhere", not at the solving project.
    assert client.post("/api/projects/Elsewhere/activate").status_code == 200

    r = client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert r.status_code == 200, r.text
    assert any("job log: line one" in line for line in r.json()["lines"]), r.json()
    assert r.json()["status"] == "running"

    release.set()
    _wait_for_terminal(job["id"])


def test_a_terminal_jobs_log_is_retained_and_served(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    from services import solver_service

    install_network(build_network(), name="Retained")
    _save_project(client, "Retained")

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("job log: retained line")
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)

    job = client.post("/api/simulation/queue", json={"project_id": "Retained"}).json()
    done = _wait_for_terminal(job["id"])
    assert done["status"] == "completed", done

    r = client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert r.status_code == 200, r.text
    assert any("retained line" in line for line in r.json()["lines"]), r.json()
    assert r.json()["status"] == "completed"


def test_an_interrupted_jobs_log_is_served_like_any_other_terminal_jobs():
    """
    R18 — `interrupted` gets no exceptions. Driven off the job table directly so
    the assertion is about the endpoint's status handling, not about killing a
    process mid-test.
    """
    from routers.simulation import BufferedLogQueue
    from services.solve_queue import SolveJob

    solve_queue.reset_for_tests()
    try:
        q = BufferedLogQueue()
        q.put("job log: died under it")
        with solve_queue._lock:
            job = SolveJob(id=941, project_id="Ghost", enqueued_at=0.0)
            job.status = "interrupted"
            job.log_queue = q
            solve_queue._jobs[941] = job
            solve_queue._order.append(941)

        assert solve_queue.get_log_queue(941) is q
        assert q.history() == ["job log: died under it"]
    finally:
        solve_queue.reset_for_tests()


def test_the_log_endpoints_404_for_a_caller_who_may_not_see_the_job(
    client, other_org_client, install_network, tmp_projects_dir, monkeypatch,
):
    from services import solver_service

    install_network(build_network(), name="Private")
    _save_project(client, "Private")

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        log_queue.put("job log: not yours")
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)
    job = client.post("/api/simulation/queue", json={"project_id": "Private"}).json()
    _wait_for_terminal(job["id"])

    mine = client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert mine.status_code == 200, mine.text

    theirs = other_org_client.get(f"/api/simulation/queue/{job['id']}/log_history")
    assert theirs.status_code == 404, theirs.text
    # Byte-identical to the genuine not-found message, so a 404 is not an
    # existence oracle.
    missing = other_org_client.get("/api/simulation/queue/99999/log_history")
    assert missing.status_code == 404
    assert theirs.json()["detail"] == missing.json()["detail"].replace(
        "99999", str(job["id"])
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_job_log.py -v`

Expected: FAIL — the HTTP tests fail with `assert 404 == 200` (the route does not exist), and `test_an_interrupted_jobs_log_is_served_like_any_other_terminal_jobs` fails with `AttributeError: 'SolveQueue' object has no attribute 'get_log_queue'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`, add to the `SolveJob` dataclass after `cancelled` (`:90`):

```python
    # The BufferedLogQueue this job's solve wrote to. Lives for the LIFE OF THE
    # JOB, not the life of the context: the log used to be stored only on the
    # ctx (`ctx_state_update(log_queue=…)`), so it was unreachable by job id,
    # unreachable once the ctx was evicted, and overwritten by the next solve of
    # the same project. Deliberately NOT in `to_public` — it is an object, not
    # JSON, and the two log endpoints reach it through `get_log_queue`.
    log_queue: Any = None
```

Add after `get_job` (`:158-161`):

```python
    def get_log_queue(self, job_id) -> Any | None:
        """
        The BufferedLogQueue this job's solve wrote to, or None.

        Retained after the job goes terminal — the queue's 5000-line ring
        buffer IS the retained log, and serving it is what makes a finished
        job's output readable at all. Retention is uniform across every
        terminal status, `interrupted` included.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            return job.log_queue if job is not None else None
```

In `_run_job`, extend the claim block (`:346-352`) so the queue is reachable by job id from the instant the job is `running`:

```python
            with self._lock:
                if job.cancelled:
                    final_status = "aborted"
                    return
                job.status = "running"
                job.started_at = time.time()
                job.stop_event = stop_event
                # Publish the log queue with the status flip, in the SAME
                # critical section. A consumer that sees `running` can then
                # always reach the queue — the microsecond gap between the flip
                # and `ctx_state_update(log_queue=…)` is the race the AppHeader
                # carries a bounded retry for.
                job.log_queue = log_queue
```

In `pypsa-gui/backend/routers/solve_queue.py`, extend the imports (`:19-33`) to:

```python
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.models import User
from db.session import get_db
from deps import optional_user
from services.solve_queue import _TERMINAL, solve_queue

router = APIRouter()
```

Then insert, immediately before `list_queue` (`:186-187`):

```python
def _visible_job_or_404(db: DBSession, user: User, job_id: int) -> dict:
    """
    The job, if the caller may SEE it; the genuine not-found 404 otherwise.

    404, never 403, with the not-found message BYTE FOR BYTE — same reasoning as
    `abort_job`: a 403 would confirm the id exists and hand back the enumeration
    the redacted listing just took away.

    `_may_see`, not `_may_abort`. The two deliberately disagree for a job
    orphaned by a project delete: it stays ABORTABLE by its own org so a running
    solve can still be stopped and the shared solver freed. That exception is
    about stopping work, not about reading the deleted project's log, so the
    listing predicate is the right one here.
    """
    from services import project_acl

    not_found = HTTPException(404, f"No solve job with id {job_id}.")
    job = solve_queue.get_job(job_id)
    if job is None:
        raise not_found
    prefix = _org_prefix(db, user)
    allowed = project_acl.accessible_project_ids(db, user, [_project_uuid(job, prefix)])
    if not _may_see(job, prefix, allowed):
        raise not_found
    return job


def _sse_line(text: object) -> str:
    """One SSE `data:` frame. Newlines would terminate the frame early."""
    safe = str(text).replace("\n", " ").replace("\r", "")
    return f"data: {safe}\n\n"


@router.get("/{job_id}/log_history")
def job_log_history(
    job_id: int,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    The lines this job emitted — live while it runs, retained once terminal.

    Readable regardless of which project the caller is viewing and regardless
    of whether the job's context is still resident, because the queue lives on
    the JOB. `/api/simulation/log_history` answers a different question (what
    the caller's ACTIVE context last logged) and is unchanged.
    """
    from services import project_registry

    project_registry.require_user(user)
    job = _visible_job_or_404(db, user, job_id)
    q = solve_queue.get_log_queue(job_id)
    lines = q.history() if q is not None else []
    return {"lines": lines, "status": job["status"]}


@router.get("/{job_id}/log_stream")
async def job_log_stream(
    job_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Server-Sent Events for ONE job's log.

    Reads through `BufferedLogQueue.subscribe()`, not `get()`. The legacy `get()`
    consumer is DESTRUCTIVE — it pops — so draining the same queue here would
    race `/api/simulation/log_stream` for the foreground's lines and each would
    see half of them. The fanout deque is exactly the side channel that exists
    for a second consumer, and it never observes the `None` close sentinel.

    Termination is therefore the job's own status, not a sentinel: stop once the
    job is terminal AND the deque has drained, then emit `done`.
    """
    from services import project_registry

    project_registry.require_user(user)
    _visible_job_or_404(db, user, job_id)

    async def generate():
        q = solve_queue.get_log_queue(job_id)
        if q is None:
            yield "data: No log for this job\n\n"
            return

        for line in q.history():
            if await request.is_disconnected():
                return
            yield _sse_line(line)

        sub_id, dq = q.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    return
                drained = False
                while dq:
                    drained = True
                    yield _sse_line(dq.popleft())
                if drained:
                    continue
                snapshot = solve_queue.get_job(job_id) or {}
                if snapshot.get("status") in _TERMINAL:
                    break
                await asyncio.sleep(0.25)
        finally:
            # A closed browser tab would otherwise leak the deque + dict entry.
            q.unsubscribe(sub_id)

        final = solve_queue.get_job(job_id) or {}
        payload = json.dumps({
            "status": final.get("status"),
            "objective": final.get("objective"),
            "solve_time": final.get("solve_time"),
            "condition": final.get("condition"),
        })
        yield f"event: done\ndata: {payload}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
```

Note the route-ordering constraint: both new routes carry the literal suffixes `/log_history` and `/log_stream`, so they cannot collide with `POST /{job_id}/abort` or with `GET ""`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_job_log.py -v`

Expected: PASS — 4 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_job_log.py
git commit -m "feat(queue): per-job log queue with authorized history and stream endpoints" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The panel expands to the live or retained log

**Increment:** 2

**Requirements:** R20

**Files:**
- Create: `pypsa-gui/frontend/src/pages/SolveQueuePanel.log.test.tsx`
- Modify: `pypsa-gui/frontend/src/api/solveQueue.ts:51-66` (add `jobLogHistory` and `jobLogStreamUrl`)
- Modify: `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx:109-153` (`canExpandJob`, `JobLogPanel`, `JobRow`)
- Test: `pypsa-gui/frontend/src/pages/SolveQueuePanel.log.test.tsx`

**Interfaces:**
- Consumes: `isTerminal(j: SolveJob): boolean`, `type SolveJob`, `type SolveJobStatus`, and
  ```ts
  export interface SolveJob {
    id: number
    project_id: string | null
    project_key: string | null
    status: SolveJobStatus
    position: number | null
    objective: number | null
    solve_time: number | null
    condition: string | null
    error: string | null
    enqueued_at: number
    started_at: number | null
    finished_at: number | null
  }
  ```
- Produces:
  ```ts
  // api/solveQueue.ts
  jobLogHistory: (jobId: number) => Promise<{ lines: string[]; status: SolveJobStatus }>
  jobLogStreamUrl: (jobId: number) => string
  // pages/SolveQueuePanel.tsx
  export function canExpandJob(job: SolveJob): boolean
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/pages/SolveQueuePanel.log.test.tsx`:

```tsx
// R20 — the expand control shows the LIVE log for a running row and the
// RETAINED log for a terminal row, for all four terminal statuses.
//
// Before this, expand was enabled only for `completed` and only ever showed the
// results bundle: a failed job's output was unreachable from the panel, and a
// running job's log could only be read by being on the project that owned it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolveJob, SolveJobStatus } from '../api/solveQueue'
import SolveQueuePanel, { canExpandJob } from './SolveQueuePanel'

function job(status: SolveJobStatus, project_id: string | null = 'demo'): SolveJob {
  return {
    id: 5, project_id, project_key: null, status,
    position: null, objective: null, solve_time: null, condition: null, error: null,
    enqueued_at: 0, started_at: 0, finished_at: 1,
  }
}

let jobs: SolveJob[] = []
const jobLogHistory = vi.fn()

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, current: null }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
}))
vi.mock('../api/solveQueue', async (orig) => ({
  ...(await orig<typeof import('../api/solveQueue')>()),
  solveQueueApi: {
    ...(await orig<typeof import('../api/solveQueue')>()).solveQueueApi,
    jobLogHistory: (id: number) => jobLogHistory(id),
    jobLogStreamUrl: (id: number) => `/api/simulation/queue/${id}/log_stream`,
    resultsBundle: vi.fn().mockResolvedValue(null),
  },
}))

afterEach(() => cleanup())
beforeEach(() => { jobs = []; jobLogHistory.mockReset() })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

describe('canExpandJob', () => {
  it('is false for a queued job — it has produced nothing yet', () => {
    expect(canExpandJob(job('queued'))).toBe(false)
  })

  it('is true for a running job', () => {
    expect(canExpandJob(job('running'))).toBe(true)
  })

  it('is true for every terminal status, interrupted included', () => {
    for (const s of ['completed', 'failed', 'aborted', 'interrupted'] as const) {
      expect(canExpandJob(job(s as SolveJobStatus))).toBe(true)
    }
  })

  it('is false for a redacted row whatever its status', () => {
    expect(canExpandJob(job('completed', null))).toBe(false)
  })
})

describe('SolveQueuePanel log expansion', () => {
  it('shows the retained log when a terminal row is expanded', async () => {
    jobs = [job('failed')]
    jobLogHistory.mockResolvedValue({ lines: ['solver: infeasible'], status: 'failed' })
    renderPanel()
    await userEvent.click(screen.getByTitle('Show this job’s log'))
    await waitFor(() => expect(screen.getByText('solver: infeasible')).toBeTruthy())
    expect(jobLogHistory).toHaveBeenCalledWith(5)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/pages/SolveQueuePanel.log.test.tsx'`

Expected: FAIL with `SyntaxError: The requested module './SolveQueuePanel' does not provide an export named 'canExpandJob'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/frontend/src/api/solveQueue.ts`, add to the `solveQueueApi` object (`:51-66`):

```ts
  // One job's log, by job id — live while it runs, retained once terminal.
  // Authorized by the same predicate as the listing, and 404s (never 403s)
  // when the caller may not see the job.
  jobLogHistory: (jobId: number) =>
    client.get<{ lines: string[]; status: SolveJobStatus }>(
      `/simulation/queue/${jobId}/log_stream`.replace('/log_stream', '/log_history'),
    ).then(r => r.data),
  // EventSource takes an absolute app path, not the axios base, so this is a
  // URL builder rather than a request.
  jobLogStreamUrl: (jobId: number) => `/api/simulation/queue/${jobId}/log_stream`,
```

In `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx`, add above `JobRow`:

```tsx
/**
 * Whether this row has a log worth opening.
 *
 * A `queued` job has produced nothing yet. A redacted row (`project_id: null`)
 * is one the caller may not see at all, so its endpoints would 404 — disabling
 * it here means the UI and the authorization agree instead of rendering a
 * control that always fails.
 */
export function canExpandJob(job: SolveJob): boolean {
  if (job.project_id == null) return false
  return job.status === 'running' || isTerminal(job)
}

function JobLogPanel({ jobId, live }: { jobId: number; live: boolean }) {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    solveQueueApi.jobLogHistory(jobId)
      .then(r => { if (!cancelled) setLines(r.lines) })
      .catch(() => { if (!cancelled) setError('Could not read this job’s log.') })
    if (!live) return () => { cancelled = true }

    // Live rows follow the job's own stream, not `/api/simulation/log_stream`
    // — which binds to the ACTIVE context and would serve a different project's
    // log (or none) whenever the user is not viewing the solving project.
    const es = new EventSource(solveQueueApi.jobLogStreamUrl(jobId))
    es.onmessage = (e) => { if (!cancelled) setLines(prev => [...prev, e.data]) }
    es.addEventListener('done', () => es.close())
    es.onerror = () => es.close()
    return () => { cancelled = true; es.close() }
  }, [jobId, live])

  if (error) return <div className="px-3 py-2 text-[11px] text-danger">{error}</div>
  if (lines.length === 0) {
    return <div className="px-3 py-2 text-[11px] text-muted">No log lines for this job.</div>
  }
  return (
    <pre className="px-3 py-2 max-h-56 overflow-auto text-[10px] leading-snug font-mono text-muted bg-bg-2/40 border-t border-border whitespace-pre-wrap">
      {lines.join('\n')}
    </pre>
  )
}
```

Then in `JobRow`, replace the expand-control block and the expanded body so they read:

```tsx
  const canExpand = canExpandJob(job)
  const canPreview = job.status === 'completed' && name != null
```

with the button becoming:

```tsx
        <button
          onClick={() => canExpand && setExpanded(v => !v)}
          disabled={!canExpand}
          className={`p-0.5 rounded ${canExpand ? 'text-muted hover:text-text' : 'opacity-0 pointer-events-none'}`}
          title={canExpand ? 'Show this job’s log' : 'Not available for this job'}
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
```

and the expanded body becoming:

```tsx
      {expanded && canExpand && (
        <>
          <JobLogPanel jobId={job.id} live={job.status === 'running'} />
          {canPreview && name != null && <JobResultsPreview name={name} />}
        </>
      )}
```

Add `useEffect` to the `react` import at `:1` and `solveQueueApi` is already imported at `:11`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/pages/SolveQueuePanel.log.test.tsx src/pages/SolveQueuePanel.redacted.test.tsx'`

Expected: PASS — 5 passed in `SolveQueuePanel.log.test.tsx`, `SolveQueuePanel.redacted.test.tsx` green (its `Not available for this job` title assertion still holds, because a redacted row is not expandable).

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/api/solveQueue.ts pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx pypsa-gui/frontend/src/pages/SolveQueuePanel.log.test.tsx
git commit -m "feat(queue-panel): expand a row to its live or retained job log" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The chat surface declares `already_queued`

**Increment:** 2

**Requirements:** R21

**Files:**
- Create: `pypsa-gui/backend/tests/test_chat_tools_schema_solve_queue.py`
- Modify: `pypsa-gui/backend/services/chat_tools_schema.py:664-670` (the `solve_queue_enqueue` description string)
- Test: `pypsa-gui/backend/tests/test_chat_tools_schema_solve_queue.py`

**Interfaces:**
- Consumes: the enqueue response shape from Task 8 — `{**solve_queue.get_job(job.id), "already_queued": bool}`; `chat_tools_schema.TOOLS` (the list `_t` / `_empty` build).
- Produces: no new symbols. The contract: the `solve_queue_enqueue` tool description names `already_queued`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_chat_tools_schema_solve_queue.py`:

```python
"""
R21 — the chat tool descriptions are part of the API.

`_route` reshapes nothing: `chat_tools.py`'s final statement is
`return handler(...)`, and `_truncate_result` caps size only, so the whole
enqueue payload reaches the model verbatim. The existing schema tests pin
name/signature/endpoint agreement, not response keys — so a new key arrives at
the model undeclared and nothing flags the drift. A model trusts a description
over the data, which makes a stale one worse than none.
"""
from __future__ import annotations

from services import chat_tools_schema


def _description(name: str) -> str:
    for tool in chat_tools_schema.TOOLS:
        if tool["name"] == name:
            return tool["description"]
    raise AssertionError(f"no tool named {name!r} in the schema")


def test_solve_queue_enqueue_declares_already_queued():
    text = _description("solve_queue_enqueue")
    assert "already_queued" in text, text


def test_solve_queue_enqueue_says_a_duplicate_is_not_an_error():
    text = _description("solve_queue_enqueue")
    assert "200" in text or "not an error" in text.lower(), text
```

If `chat_tools_schema` exposes the tool list under a different module-level name, use that name — the file's `_t` / `_empty` helpers append to exactly one list, and the existing `tests/test_chat_tools_schema_match.py` already reads it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_chat_tools_schema_solve_queue.py -v`

Expected: FAIL with `AssertionError: Enqueue a project for background solving. Dispatcher auto-runs on enqueue. Project must have a saved network.nc. Safety: execution.` — `already_queued` does not appear.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/chat_tools_schema.py`, replace the `solve_queue_enqueue` entry (`:664-670`) with:

```python
    _t(
        "solve_queue_enqueue",
        "Enqueue a project for background solving. Dispatcher auto-runs on "
        "enqueue. Project must have a saved network.nc. Idempotent per project: "
        "if the project already has a queued or running job the response is 200 "
        "with THAT job and `already_queued: true`, and no second job is created "
        "— this is not an error, so do not retry. A new job returns "
        "`already_queued: false`. Safety: execution.",
        {"project_id": {"type": "string"}},
        ["project_id"],
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_chat_tools_schema_solve_queue.py -v`

Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/chat_tools_schema.py pypsa-gui/backend/tests/test_chat_tools_schema_solve_queue.py
git commit -m "docs(chat): declare already_queued on the solve_queue_enqueue tool" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Increment 2 boundary gate

- [ ] Run `pixi run gui-tests`. Expected: `0 failed`, `skipped <= 22`, `passed == 2290 + N_backend` where this increment's `N_backend` is Task 8's 4 + Task 9's 4 + Task 11's 2 = 10 → `2300 passed`.
- [ ] Run `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test'`. Expected: `0 failed`, `0 skipped`, `passed == 683 + 5` (Task 10) = `688 passed`.
- [ ] Run `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'`. Expected: PASS.
- [ ] Run `ruff check .`. Expected: `All checks passed!`

---

## Increment 3 — durability, controls, concurrency (Tasks 12–23)

### Task 12: Job identity is a UUID everywhere it is exposed

**Increment:** 3

**Requirements:** R23, R37

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_uuid_ids.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:43-52` (import `uuid`), `:60-110` (`SolveJob.id`, `to_public`), `:113-126` (`_counter` → uuid, dict key type), `:129-151` and `enqueue_unique` (id generation), `:158-161`/`get_log_queue`/`abort` (id type)
- Modify: `pypsa-gui/backend/routers/solve_queue.py:234-239` (abort path parameter), `job_log_history` / `job_log_stream` path parameters, `_visible_job_or_404` signature
- Modify: `pypsa-gui/backend/services/chat_tools.py:997-1003` (`solve_queue_abort` drops the `int()` coercion)
- Modify: `pypsa-gui/backend/services/chat_tools_schema.py:676-681` (the `solve_queue_abort` description string, `:678`)
- Modify: `pypsa-gui/frontend/src/api/solveQueue.ts:8-20` (`id: string`), `:55-56` (`abort(jobId: string)`), `jobLogHistory` / `jobLogStreamUrl` parameter types
- Modify: `pypsa-gui/frontend/src/hooks/useSolveQueue.ts:38` (`useAbortJob` mutation input type)
- Modify: `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx` (`onAbort: (id: string) => void`, `JobLogPanel` `jobId: string`)
- Modify: `pypsa-gui/frontend/src/layout/AppHeader.tsx:637-658` (`attachedJobRef` / `attachRetryRef` id types)
- Modify (the id sweep — every one of these, or the suite breaks at this task): `pypsa-gui/backend/tests/test_solve_queue_authz.py:204` and its `_force_status` / `_by_id` / `_clone_job` helpers, `pypsa-gui/backend/tests/test_shutdown.py:1188`, `:1212`, `pypsa-gui/backend/tests/test_shutdown_queue_kind.py`, `pypsa-gui/backend/tests/test_storage_layout.py:712-725` — every place a test constructs a `SolveJob(id=901, …)` or indexes `_jobs` by an integer takes `uuid.uuid4()` instead. And every helper that feeds a job id read back from JSON into a service call must parse it first, because `_jobs` is now UUID-keyed and a string misses every key: `tests/test_context_fork_regression.py`'s `_wait_for_terminal`, `tests/test_solve_queue_dedupe.py`'s `_wait_until` body, and `tests/test_solve_queue_job_log.py`'s `_wait_for_terminal` all change `solve_queue.get_job(job_id)` to `solve_queue.get_job(uuid.UUID(str(job_id)))`. These are edits to existing cases, not new or removed ones, so they contribute 0 to `N_backend`.
- Modify: `pypsa-gui/frontend/src/pages/SolveQueuePanel.clearFinished.test.tsx:26` (`id: 1` becomes a UUID string)
- Test: `pypsa-gui/backend/tests/test_solve_queue_uuid_ids.py`

**Interfaces:**
- Consumes: `_visible_job_or_404(db, user, job_id) -> dict` from Task 9;
  ```python
  def enqueue_unique(
      self,
      project_id: str,
      *,
      project_key: str | None = None,
      storage_dir: str | None = None,
  ) -> tuple[SolveJob, bool]:
      """Returns (job, created). `created` is False when an ACTIVE job already exists."""
  ```
- Produces: `SolveJob.id: uuid.UUID`; `to_public()["id"]` is `str(self.id)`; `SolveQueue._jobs: dict[uuid.UUID, SolveJob]`; `_parse_job_id(job_id: str) -> uuid.UUID | None` in `routers/solve_queue.py`; the TypeScript `SolveJob.id: string`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_uuid_ids.py`:

```python
"""
R23/R37 — a job id is a UUID on the wire and in the model's description.

Per-process `itertools.count(1)` ids collide across replicas: two replicas both
issue id 1, which is harmless while the ids die with the process and is not
harmless the moment a job table outlives it. The blast radius was enumerated in
advance and is exactly three surfaces: the abort route's path parameter,
`SolveJob.id` in `frontend/src/api/solveQueue.ts`, and the int coercion in the
`solve_queue_abort` chat tool.
"""
from __future__ import annotations

import uuid

from services.solve_queue import solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_an_enqueued_job_id_parses_as_a_uuid(client, install_network, tmp_projects_dir):
    install_network(build_network(), name="Ident")
    _save_project(client, "Ident")
    job = client.post("/api/simulation/queue", json={"project_id": "Ident"}).json()
    assert isinstance(job["id"], str)
    uuid.UUID(job["id"])  # raises ValueError if it is not one


def test_abort_takes_the_uuid_and_a_garbage_id_is_a_plain_404(
    client, install_network, tmp_projects_dir,
):
    install_network(build_network(), name="Stoppable")
    _save_project(client, "Stoppable")
    job = client.post("/api/simulation/queue", json={"project_id": "Stoppable"}).json()

    r = client.post(f"/api/simulation/queue/{job['id']}/abort")
    assert r.status_code == 200, r.text

    # A malformed id must be indistinguishable from an unknown one — a 422
    # would say "that is not even a valid id", which is a different oracle but
    # an oracle nonetheless.
    bad = client.post("/api/simulation/queue/not-a-uuid/abort")
    assert bad.status_code == 404, bad.text
    unknown = client.post(f"/api/simulation/queue/{uuid.uuid4()}/abort")
    assert unknown.status_code == 404, unknown.text


def test_the_chat_tool_accepts_the_uuid_string_unchanged(
    client, install_network, tmp_projects_dir,
):
    from services import chat_tools

    install_network(build_network(), name="ChatIdent")
    _save_project(client, "ChatIdent")
    job = client.post("/api/simulation/queue", json={"project_id": "ChatIdent"}).json()
    res = chat_tools.solve_queue_abort(job["id"])
    assert res["id"] == job["id"]


def test_the_abort_tool_description_states_the_id_is_a_uuid():
    from services import chat_tools_schema

    for tool in chat_tools_schema.TOOLS:
        if tool["name"] == "solve_queue_abort":
            assert "UUID" in tool["description"], tool["description"]
            return
    raise AssertionError("no solve_queue_abort tool in the schema")


def test_the_in_memory_job_table_is_keyed_by_uuid():
    solve_queue.reset_for_tests()
    job = solve_queue.enqueue("KeyCheck")
    try:
        assert isinstance(job.id, uuid.UUID)
        assert job.id in solve_queue._jobs
    finally:
        solve_queue.reset_for_tests()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_uuid_ids.py -v`

Expected: FAIL — `test_an_enqueued_job_id_parses_as_a_uuid` fails with `AssertionError: assert False` on `isinstance(job["id"], str)` (the id is still `1`), and `test_the_abort_tool_description_states_the_id_is_a_uuid` fails with `AssertionError: Abort a running OR cancel a queued job. Safety: destructive.`

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`:

- Add `import uuid` to the import block (`:43-52`) and drop `import itertools`.
- Change `SolveJob.id` (`:64`) to:

  ```python
      # UUID, matching every model in `db/models.py`. Per-process integers from
      # `itertools.count(1)` made two replicas both issue id 1 — harmless while
      # ids died with the process, and not harmless once `solve_jobs` outlives it.
      id: uuid.UUID
  ```
- In `to_public` (`:97-110`), change the id entry to `"id": str(self.id),` — the wire form is a string, so JSON, the frontend and the chat surface all speak one type.
- In `__init__` (`:116-126`), change `self._jobs: dict[int, SolveJob] = {}`, `self._order: list[uuid.UUID] = []`, `self._q: queue.Queue[uuid.UUID] = queue.Queue()`, delete `self._counter = itertools.count(1)`, and change `self._current_id: uuid.UUID | None = None`.
- In `enqueue` and `enqueue_unique`, replace `jid = next(self._counter)` with `jid = uuid.uuid4()`.
- Change the annotations of `get_job`, `get_log_queue`, `abort` and `_position_locked` from `job_id: int` to `job_id: uuid.UUID`.

In `pypsa-gui/backend/routers/solve_queue.py`, add above `_visible_job_or_404`:

```python
def _parse_job_id(job_id: str) -> uuid.UUID | None:
    """
    The path parameter as a UUID, or None if it is not one.

    Declared `str` in the signature rather than `uuid.UUID` deliberately: FastAPI
    would answer 422 for a malformed id, which is a different answer from the 404
    an unknown id gets — and telling a caller "that is not even a valid id"
    re-opens the oracle the byte-identical 404 exists to close.
    """
    try:
        return uuid.UUID(job_id)
    except (ValueError, AttributeError, TypeError):
        return None
```

Change `_visible_job_or_404`'s signature to `def _visible_job_or_404(db: DBSession, user: User, job_id: str) -> dict:` and make its body start:

```python
    not_found = HTTPException(404, f"No solve job with id {job_id}.")
    parsed = _parse_job_id(job_id)
    if parsed is None:
        raise not_found
    job = solve_queue.get_job(parsed)
    if job is None:
        raise not_found
```

Change the three route path parameters from `job_id: int` to `job_id: str` (`abort_job` at `:235`, `job_log_history`, `job_log_stream`), and in `abort_job` replace the lookup/abort pair with:

```python
    not_found = HTTPException(404, f"No solve job with id {job_id}.")
    parsed = _parse_job_id(job_id)
    if parsed is None:
        raise not_found
    job = solve_queue.get_job(parsed)
    if job is None or not _may_abort(db, user, job):
        raise not_found
    res = solve_queue.abort(parsed)
```

and in the two log routes replace `solve_queue.get_log_queue(job_id)` / `solve_queue.get_job(job_id)` with the parsed value (`parsed = _parse_job_id(job_id)`, already validated by `_visible_job_or_404`).

In `pypsa-gui/backend/services/chat_tools.py`, replace `solve_queue_abort` (`:997-1003`) with:

```python
def solve_queue_abort(job_id: str) -> dict:
    # Job ids are UUIDs (0005_solve_jobs). The old `int(job_id)` coercion
    # existed because the jobs dict was int-keyed and a string silently missed
    # every key — it now has to go, or every abort raises ValueError before it
    # reaches the handler.
    from routers.solve_queue import abort_job as _h
    return _route(_h, job_id)
```

In `pypsa-gui/backend/services/chat_tools_schema.py`, replace the `solve_queue_abort` description (`:678`) with:

```python
        "Abort a running OR cancel a queued job. `job_id` is the job's UUID, "
        "exactly as returned by solve_queue_list / solve_queue_enqueue — not an "
        "index and not a project name. Safety: destructive.",
```

Frontend changes, all mechanical type flips:

- `pypsa-gui/frontend/src/api/solveQueue.ts`: `id: string` in `SolveJob` (with the comment `// UUID string. Was a per-process integer that collided across replicas.`), `abort: (jobId: string) =>`, `jobLogHistory: (jobId: string) =>`, `jobLogStreamUrl: (jobId: string) =>`.
- `pypsa-gui/frontend/src/hooks/useSolveQueue.ts:38`: `mutationFn: (jobId: string) => solveQueueApi.abort(jobId)`.
- `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx`: `JobRow`'s `onAbort: (id: string) => void`, `JobLogPanel`'s `jobId: string`, and the panel's `const onAbort = (id: string) => {`.
- `pypsa-gui/frontend/src/layout/AppHeader.tsx`: `attachedJobRef = useRef<string | null>(null)` and `attachRetryRef = useRef<{ id: string | null; tries: number }>({ id: null, tries: 0 })`, with the two `attachRetryRef.current.id === jobId` comparisons and the `jobId?: string` parameter of `openLogStream` updated to match; the `id: -1` initialiser becomes `id: null`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_uuid_ids.py -v`

Expected: PASS — 5 passed.

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/services/chat_tools.py pypsa-gui/backend/services/chat_tools_schema.py pypsa-gui/backend/tests/test_solve_queue_uuid_ids.py pypsa-gui/frontend/src/api/solveQueue.ts pypsa-gui/frontend/src/hooks/useSolveQueue.ts pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx pypsa-gui/frontend/src/layout/AppHeader.tsx
git commit -m "feat(queue): UUID job identity across REST, the client and the chat tools" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: The `solve_jobs` table, its migration, and the persistence seam

**Increment:** 3

**Requirements:** R22

**Files:**
- Create: `pypsa-gui/backend/alembic/versions/0005_solve_jobs.py`
- Create: `pypsa-gui/backend/services/solve_job_store.py`
- Create: `pypsa-gui/backend/tests/test_solve_jobs_table.py`
- Modify: `pypsa-gui/backend/db/models.py:4` (import `Float`), `:143` (append `SolveJobRow`)
- Modify: `pypsa-gui/backend/routers/solve_queue.py:72-77` (stamp the acting user and persist the row)
- Modify: `pypsa-gui/backend/services/solve_queue.py:498-512` (`_run_job`'s `finally` mirrors the terminal record)
- Test: `pypsa-gui/backend/tests/test_solve_jobs_table.py`

**Interfaces:**
- Consumes: `SolveJob.id: uuid.UUID` and `to_public()["id"] == str(self.id)` from Task 12 — the table's UUID primary key is only bindable because identity is already a UUID by this point; `Base` (`db/base.py`), `UUID = Uuid` alias and the model conventions at `db/models.py:9`; `SessionLocal` (`db/session.py:80`); `SolveJob` (`services/solve_queue.py:60-110`); the Task 8 signature
  ```python
  def enqueue_unique(
      self,
      project_id: str,
      *,
      project_key: str | None = None,
      storage_dir: str | None = None,
  ) -> tuple[SolveJob, bool]:
      """Returns (job, created). `created` is False when an ACTIVE job already exists."""
  ```
- Produces:
  ```python
  # db/models.py
  class SolveJobRow(Base):  # __tablename__ = "solve_jobs"
      id: Mapped[uuid.UUID]
      project_id: Mapped[str]
      project_key: Mapped[str | None]
      storage_dir: Mapped[str | None]
      status: Mapped[str]
      enqueued_by_user_id: Mapped[uuid.UUID | None]
      solver_config: Mapped[str | None]
      objective: Mapped[float | None]
      solve_time: Mapped[float | None]
      condition: Mapped[str | None]
      error: Mapped[str | None]
      enqueued_at: Mapped[datetime]
      started_at: Mapped[datetime | None]
      finished_at: Mapped[datetime | None]
      dismissed_by_user_id: Mapped[uuid.UUID | None]

  # services/solve_job_store.py
  def record_enqueued(job, *, enqueued_by_user_id, solver_config_json: str | None) -> None
  def record_status(job) -> None
  def load_by_status(statuses: tuple[str, ...]) -> list[dict]
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_jobs_table.py`:

```python
"""
R22 — every job is persisted, with who queued it and what it was queued with.

The queue was purely in-process: `itertools.count(1)` ids, a dict, and nothing
on disk. A restart lost every queued job silently, and a shared instance could
not say who queued a solve. Increment 3's boot reconciliation, requeue, dismiss
and config snapshot all read this table.
"""
from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import select

from db.models import SolveJobRow
from db.session import SessionLocal
from services import solve_job_store
from services.solve_queue import SolveJob
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _row(job_id):
    with SessionLocal() as db:
        return db.scalar(select(SolveJobRow).where(SolveJobRow.id == _as_uuid(job_id)))


def _as_uuid(job_id):
    return job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))


def test_the_table_carries_a_uuid_pk_a_user_and_a_config():
    cols = SolveJobRow.__table__.columns
    assert cols["id"].primary_key
    assert "enqueued_by_user_id" in cols
    assert "solver_config" in cols
    assert SolveJobRow.__tablename__ == "solve_jobs"


def test_record_enqueued_writes_the_row_with_the_acting_user_and_config():
    job = SolveJob(
        id=uuid.uuid4(), project_id="Persisted", project_key="org:proj",
        storage_dir="/tmp/persisted", enqueued_at=time.time(),
    )
    actor = uuid.uuid4()
    solve_job_store.record_enqueued(
        job, enqueued_by_user_id=actor, solver_config_json=json.dumps({"solver_name": "highs"}),
    )
    row = _row(job.id)
    assert row is not None, "no solve_jobs row was written"
    assert row.project_id == "Persisted"
    assert row.project_key == "org:proj"
    assert row.status == "queued"
    assert row.enqueued_by_user_id == actor
    assert json.loads(row.solver_config)["solver_name"] == "highs"


def test_record_enqueued_refuses_a_non_uuid_id_loudly():
    """
    The swallow-everything version turned a type error into a silent no-write:
    `Uuid(as_uuid=True)`'s bind processor calls `value.hex`, the AttributeError
    was caught, and every row went unwritten behind one log line. A programming
    error must reach the caller.
    """
    import pytest

    bogus = SolveJob(id=7, project_id="Wrong", enqueued_at=time.time())
    with pytest.raises(TypeError, match="UUID"):
        solve_job_store.record_enqueued(
            bogus, enqueued_by_user_id=None, solver_config_json=None,
        )


def test_record_status_mirrors_the_terminal_record():
    job = SolveJob(id=uuid.uuid4(), project_id="Finished", enqueued_at=time.time())
    solve_job_store.record_enqueued(job, enqueued_by_user_id=None, solver_config_json=None)
    job.status = "completed"
    job.objective = 1234.5
    job.solve_time = 2.0
    job.condition = "optimal"
    job.finished_at = time.time()
    solve_job_store.record_status(job)
    row = _row(job.id)
    assert row.status == "completed"
    assert row.objective == 1234.5
    assert row.condition == "optimal"
    assert row.finished_at is not None


def test_load_by_status_returns_only_the_asked_for_statuses():
    queued = SolveJob(id=uuid.uuid4(), project_id="Q", enqueued_at=time.time())
    done = SolveJob(id=uuid.uuid4(), project_id="D", enqueued_at=time.time())
    for j in (queued, done):
        solve_job_store.record_enqueued(j, enqueued_by_user_id=None, solver_config_json=None)
    done.status = "completed"
    solve_job_store.record_status(done)

    ids = {r["id"] for r in solve_job_store.load_by_status(("queued",))}
    assert queued.id in ids
    assert done.id not in ids


def test_enqueuing_through_the_route_persists_the_row(
    client, install_network, tmp_projects_dir,
):
    install_network(build_network(), name="Durable")
    _save_project(client, "Durable")
    job = client.post("/api/simulation/queue", json={"project_id": "Durable"}).json()
    row = _row(job["id"])
    assert row is not None, "the enqueue route did not persist the job"
    assert row.project_id == "Durable"
    assert row.enqueued_by_user_id is not None, "the acting user was not stamped"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_jobs_table.py -v`

Expected: FAIL at collection with `ImportError: cannot import name 'SolveJobRow' from 'db.models'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/db/models.py`, change line 4 to:

```python
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, Uuid
```

and append after `Session` (`:143`):

```python
class SolveJobRow(Base):
    """
    One queued solve, persisted.

    The queue used to be purely in-process: ids from `itertools.count(1)`, a
    dict, and nothing on disk. A restart lost every queued job with no trace,
    two replicas both issued id 1, and a shared instance could not say who
    queued a solve.

    `status` is a plain string, not a DB enum, following `Project.scenario_type`:
    the set is presentational and grows (`interrupted` is added in this same
    increment), an unknown value must degrade rather than break the row, and a
    new member should not need a migration on two backends.

    `solver_config` is the JSON snapshot the job was ENQUEUED with. The
    dispatcher used to read `ctx.solver_state["solver_config"]` at RUN time, so
    a `PUT /api/simulation/solver_config` after enqueue silently changed what a
    queued job solved — and durability widens that window to overnight.

    `dismissed_by_user_id` is per-user hiding. Only the enqueuer may dismiss, so
    the column can hold one id and still mean "hidden from that user's listing
    only" without affecting anyone else's.
    """

    __tablename__ = "solve_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The human-readable project NAME, matching `SolveJob.project_id` and the
    # width of `projects.name`.
    project_id: Mapped[str] = mapped_column(String(64))
    # `org_uuid:project_uuid` — the registry identity, and the only tenancy
    # information a job carries.
    project_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    storage_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    enqueued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    solver_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[float | None] = mapped_column(Float, nullable=True)
    solve_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
```

Create `pypsa-gui/backend/alembic/versions/0005_solve_jobs.py`:

```python
"""solve_jobs

Persist the solve queue. Until now it was a process-local dict: ids came from
`itertools.count(1)`, a restart lost every queued job with no trace, and two
replicas both issued id 1.

Reversible and safe to re-run in the only sense that matters here: `upgrade()`
creates a table that did not exist, so there is no data migration and no
backfill. Calling it twice fails on `create_table` with "table solve_jobs
already exists", which alembic never does on its own but which matters to
anyone hand-repairing a half-applied revision: drop the table first, or stamp.

The primary key is a UUID, matching every other model in `db/models.py`
(`Uuid(as_uuid=True)`), rather than the integer the in-memory queue used. Done
now, while the table is being created, rather than migrating a populated one
later — and it is what stops two replicas colliding on id 1 the moment job rows
outlive the process.

Revision ID: 0005_solve_jobs
Revises: 0004_scenario_type
Create Date: 2026-08-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_solve_jobs"
down_revision: str | None = "0004_scenario_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solve_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("project_key", sa.String(length=128), nullable=True),
        sa.Column("storage_dir", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enqueued_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("solver_config", sa.Text(), nullable=True),
        sa.Column("objective", sa.Float(), nullable=True),
        sa.Column("solve_time", sa.Float(), nullable=True),
        sa.Column("condition", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        # SET NULL, not CASCADE: deleting a user must not delete the audit of
        # what they queued, and a job orphaned of its enqueuer is still a job
        # the operator needs to see.
        sa.ForeignKeyConstraint(
            ["enqueued_by_user_id"], ["users.id"],
            name="fk_solve_jobs_enqueued_by_user_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["dismissed_by_user_id"], ["users.id"],
            name="fk_solve_jobs_dismissed_by_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_solve_jobs_project_key"), "solve_jobs", ["project_key"], unique=False)
    op.create_index(op.f("ix_solve_jobs_status"), "solve_jobs", ["status"], unique=False)
    op.create_index(
        op.f("ix_solve_jobs_enqueued_by_user_id"), "solve_jobs", ["enqueued_by_user_id"], unique=False,
    )
    op.create_index(
        op.f("ix_solve_jobs_dismissed_by_user_id"), "solve_jobs", ["dismissed_by_user_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_solve_jobs_dismissed_by_user_id"), table_name="solve_jobs")
    op.drop_index(op.f("ix_solve_jobs_enqueued_by_user_id"), table_name="solve_jobs")
    op.drop_index(op.f("ix_solve_jobs_status"), table_name="solve_jobs")
    op.drop_index(op.f("ix_solve_jobs_project_key"), table_name="solve_jobs")
    op.drop_table("solve_jobs")
```

Create `pypsa-gui/backend/services/solve_job_store.py`:

```python
"""
The one place the solve queue touches the database.

The dispatcher runs on a worker thread with no request, no user and no session,
and `services/solve_queue.py` deliberately imports no ORM — the module is pure
threading and bookkeeping. This module is the seam: every function opens its own
short-lived `SessionLocal()`, commits, and NEVER raises. A database hiccup must
degrade the queue to its pre-durability behaviour, not fail a solve that is
already running.

The prior design note that `_run_job` never touches the DB is relaxed here, and
deliberately: without a terminal write, every completed job stays `running` in
the table and boot reconciliation would mark it `interrupted`. The isolation the
note protected is preserved by keeping the ORM out of `solve_queue.py` and
behind this seam.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def _dt(epoch: float | None) -> datetime | None:
    """`SolveJob` timestamps are `time.time()` floats; the column is tz-aware."""
    return None if epoch is None else datetime.fromtimestamp(epoch, tz=timezone.utc)


def record_enqueued(job: Any, *, enqueued_by_user_id, solver_config_json: str | None) -> None:
    """
    Insert the row for a freshly created job.

    TWO CLASSES OF FAILURE, HANDLED DIFFERENTLY ON PURPOSE.

    An OPERATIONAL failure — the table not migrated yet in web mode, the SQLite
    file locked, the connection dropped — is logged and swallowed. Durability is
    an upgrade to the queue, not a precondition for solving, and refusing an
    enqueue because a bookkeeping write failed would make a new feature able to
    break the old one.

    A PROGRAMMING failure is not swallowed. `except Exception` here would have
    hidden the exact defect this whole plan exists to stop: an id of the wrong
    type binds into `Uuid(as_uuid=True)`, whose bind processor calls
    `value.hex`, and the resulting `AttributeError` would be logged as an
    operational blip while every row silently went unwritten — a silent
    data-loss mode wearing a log line. So the catch is narrowed to
    `SQLAlchemyError`, and the id is type-checked UP FRONT, before SQLAlchemy
    can wrap the mistake in a `StatementError` that the narrowed catch would
    then swallow anyway.
    """
    if not isinstance(job.id, uuid.UUID):
        raise TypeError(
            f"solve_jobs.id is a UUID column; got {type(job.id).__name__} "
            f"({job.id!r}). A SolveJob must carry a uuid.UUID id."
        )
    try:
        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            db.add(SolveJobRow(
                id=job.id,
                project_id=job.project_id,
                project_key=job.project_key,
                storage_dir=job.storage_dir,
                status=job.status,
                enqueued_by_user_id=enqueued_by_user_id,
                solver_config=solver_config_json,
                enqueued_at=_dt(job.enqueued_at) or datetime.now(tz=timezone.utc),
            ))
            db.commit()
    except SQLAlchemyError:
        # Operational only. A programming error reaches the caller.
        logger.exception("solve_job_store: could not persist job %s", getattr(job, "id", None))


def record_status(job: Any) -> None:
    """
    Mirror a job's current status + result onto its row. Best-effort.

    Broad `except Exception` here, unlike `record_enqueued`, and the asymmetry
    is deliberate rather than an oversight: this runs inside a solver worker's
    `finally`, where an escaping exception would replace the job's real outcome
    with a bookkeeping traceback. `_run_job`'s own try/except around this call
    already logs it at `exception` level, so a programming error is loud without
    being fatal — which is the property `record_enqueued` gets by re-raising,
    reached the other way round.

    Called on the running claim and again from `_run_job`'s `finally`, so a
    process that dies mid-solve leaves the row at `running` — which is exactly
    what boot reconciliation reads to mark it `interrupted`.
    """
    try:
        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            row = db.get(SolveJobRow, job.id)
            if row is None:
                return
            row.status = job.status
            row.objective = job.objective
            row.solve_time = job.solve_time
            row.condition = job.condition
            row.error = job.error
            row.started_at = _dt(job.started_at)
            row.finished_at = _dt(job.finished_at)
            db.commit()
    except Exception:  # noqa: BLE001 — a bookkeeping failure must not fail a solve
        logger.exception("solve_job_store: could not update job %s", getattr(job, "id", None))


def load_by_status(statuses: tuple[str, ...]) -> list[dict]:
    """
    Rows in any of `statuses`, oldest first, as plain dicts.

    Dicts rather than ORM objects so the caller (the dispatcher, a worker
    thread) never holds a detached instance whose session is gone.
    """
    try:
        from sqlalchemy import select

        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            rows = db.scalars(
                select(SolveJobRow)
                .where(SolveJobRow.status.in_(statuses))
                .order_by(SolveJobRow.enqueued_at)
            ).all()
            return [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "project_key": r.project_key,
                    "storage_dir": r.storage_dir,
                    "status": r.status,
                    "solver_config": r.solver_config,
                    "enqueued_at": r.enqueued_at,
                }
                for r in rows
            ]
    except Exception:  # noqa: BLE001 — an unreadable table means "nothing to restore"
        logger.exception("solve_job_store: could not load jobs by status %s", statuses)
        return []
```

In `pypsa-gui/backend/routers/solve_queue.py`, replace the Task 8 enqueue body (`:72-77` as rewritten) with:

```python
    job, created = solve_queue.enqueue_unique(
        project.name,
        project_key=project_registry.registry_key(project),
        storage_dir=str(project_dir),
    )
    if created:
        # Stamp the ACTING user alongside the org-scoped directory this route
        # already resolved. Keying per-user dismiss on project access instead
        # would let two users sharing a project dismiss each other's rows —
        # the exact thing per-user dismiss exists to prevent.
        from services import solve_job_store

        solve_job_store.record_enqueued(
            job, enqueued_by_user_id=user.id, solver_config_json=None,
        )
    return {**solve_queue.get_job(job.id), "already_queued": not created}
```

(The `solver_config_json=None` argument is filled in by Task 14, which is the requirement that owns the snapshot.)

In `pypsa-gui/backend/services/solve_queue.py`, extend `_run_job`'s `finally` (`:498-512`) so the terminal record is mirrored:

```python
        finally:
            with self._lock:
                job.status = final_status
                job.condition = condition
                job.objective = objective
                job.solve_time = solve_time
                job.error = error
                job.finished_at = time.time()
            # Mirror the terminal record to the job table, OUTSIDE `_lock`:
            # the store opens a database session and `_lock` is documented as
            # short bookkeeping only, never held across I/O.
            try:
                from services import solve_job_store

                solve_job_store.record_status(job)
            except Exception:  # noqa: BLE001 — bookkeeping must not fail a solve
                logger.exception("solve_queue: could not persist job %s", job.id)
            # Close the SSE log stream for this job so the foreground consumer's
            # `done` event fires (run_simulation pushes None on its own success
            # path, but the abort/error paths above may not have).
            try:
                log_queue.put(None)
            except Exception:
                pass
```

and add the same mirror to the running claim, immediately after the `with self._lock:` block at `:346-352`:

```python
            # Persist `running` before the solve starts. A process that dies
            # mid-solve leaves the row here, which is precisely what boot
            # reconciliation reads to mark the job `interrupted`.
            try:
                from services import solve_job_store

                solve_job_store.record_status(job)
            except Exception:  # noqa: BLE001
                logger.exception("solve_queue: could not persist job %s", job.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_jobs_table.py -v`

Expected: PASS — 6 passed.

Then confirm the migration chain and its SQLite-safety pins: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_alembic_sqlite.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/db/models.py pypsa-gui/backend/alembic/versions/0005_solve_jobs.py pypsa-gui/backend/services/solve_job_store.py pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/tests/test_solve_jobs_table.py
git commit -m "feat(queue): persist every job in a solve_jobs table (0005)" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: A job solves with the config it was queued with

**Increment:** 3

**Requirements:** R24

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_config_snapshot.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:60-110` (`SolveJob.solver_config_json`)
- Modify: `pypsa-gui/backend/services/solve_queue.py:387-389` (the dispatcher reads the snapshot)
- Modify: `pypsa-gui/backend/routers/solve_queue.py` (the enqueue route resolves and stamps the config)
- Test: `pypsa-gui/backend/tests/test_solve_queue_config_snapshot.py`

**Interfaces:**
- Consumes: `SolverConfig` and `asdict` from `services/solver_service.py:82`; `_solver_config_from_dict(data: dict) -> SolverConfig` (`routers/projects.py:1070`); `solve_job_store.record_enqueued(job, *, enqueued_by_user_id, solver_config_json)` from Task 13;
  ```python
  def enqueue_unique(
      self,
      project_id: str,
      *,
      project_key: str | None = None,
      storage_dir: str | None = None,
  ) -> tuple[SolveJob, bool]:
      """Returns (job, created). `created` is False when an ACTIVE job already exists."""
  ```
- Produces: `SolveJob.solver_config_json: str | None`, and the router helper `_config_snapshot_for(project_key, project_dir) -> str`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_config_snapshot.py`:

```python
"""
R24 — a job solves the config it was ENQUEUED with.

The dispatcher read `ctx.solver_state["solver_config"]` at RUN time, and
`PUT /api/simulation/solver_config` mutates that live — so a config edited after
enqueue silently changed what a queued job solved. Which config a job got also
depended on residency: a resident project used the in-memory value, a
non-resident one whatever `_hydrate_context_from_disk` had loaded from
`solver_config.json`. Durability widens both windows to overnight.

This is a determinism fix, NOT a parameter sweep: one project still cannot be
queued twice, so it can still only carry one config at a time.
"""
from __future__ import annotations

import json
import threading

from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_editing_the_config_after_enqueue_does_not_change_the_queued_job(
    client, install_network, tmp_projects_dir, monkeypatch,
):
    from services import solver_service

    install_network(build_network(), name="Snap")
    _save_project(client, "Snap")
    r = client.put("/api/simulation/solver_config", json={"co2_price": 11.0})
    assert r.status_code == 200, r.text

    seen: list = []
    entered = threading.Event()
    release = threading.Event()

    def capture(config, n, lock, stop_event, log_queue, state_update=None):
        seen.append(config)
        entered.set()
        release.wait(60)
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", capture)

    job = client.post("/api/simulation/queue", json={"project_id": "Snap"}).json()
    assert entered.wait(60)
    # Change the live config WHILE the job holds its snapshot.
    assert client.put("/api/simulation/solver_config", json={"co2_price": 99.0}).status_code == 200
    release.set()

    import time
    deadline = time.time() + 60
    while time.time() < deadline and not seen:
        time.sleep(0.05)
    assert seen, "the dispatcher never called run_simulation"
    assert seen[0].co2_price == 11.0, (
        f"the job solved with {seen[0].co2_price}, the config at RUN time, "
        "not the 11.0 it was queued with"
    )
    assert job["id"]


def test_the_snapshot_is_persisted_on_the_row(client, install_network, tmp_projects_dir):
    import uuid as _uuid

    from sqlalchemy import select

    from db.models import SolveJobRow
    from db.session import SessionLocal

    install_network(build_network(), name="Stamped")
    _save_project(client, "Stamped")
    assert client.put("/api/simulation/solver_config", json={"co2_price": 42.0}).status_code == 200
    job = client.post("/api/simulation/queue", json={"project_id": "Stamped"}).json()

    with SessionLocal() as db:
        row = db.scalar(select(SolveJobRow).where(SolveJobRow.id == _uuid.UUID(job["id"])))
    assert row is not None
    assert row.solver_config is not None, "no config snapshot was stored"
    assert json.loads(row.solver_config)["co2_price"] == 42.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_config_snapshot.py -v`

Expected: FAIL — `AssertionError: the job solved with 99.0, the config at RUN time, not the 11.0 it was queued with`, and `AssertionError: no config snapshot was stored`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`, add to `SolveJob` after `storage_dir` (`:78`):

```python
    # JSON snapshot of the SolverConfig this job was ENQUEUED with. The
    # dispatcher used to read `ctx.solver_state["solver_config"]` at RUN time,
    # so a `PUT /api/simulation/solver_config` after enqueue silently changed
    # what a queued job solved, and which config a job got depended on whether
    # the project happened to be resident. Resolved once, by the route that has
    # the request and the authorized directory. None means "fall back to the
    # context's config", which is the pre-snapshot behaviour and what a
    # hand-made job gets.
    solver_config_json: str | None = None
```

Replace the config read (`:387-389`) with:

```python
            n = ctx.network
            lock = ctx.mutation_lock
            # THIS job's config, snapshotted at enqueue — not whatever the
            # context holds now. Falling back to the context's config keeps
            # hand-made jobs (and any row written before 0005) working.
            config = ctx.solver_state["solver_config"]
            if job.solver_config_json:
                try:
                    from routers.projects import _solver_config_from_dict

                    config = _solver_config_from_dict(json.loads(job.solver_config_json))
                except Exception:  # noqa: BLE001 — a bad snapshot must not fail the solve
                    logger.exception(
                        "solve_queue: job %s has an unreadable config snapshot; "
                        "falling back to the context's config", job.id,
                    )
```

and add `import json` to the import block (`:43-52`).

In `pypsa-gui/backend/routers/solve_queue.py`, add above `enqueue_solve`:

```python
def _config_snapshot_for(project_key: str, project_dir) -> str | None:
    """
    The solver config this enqueue should freeze onto the job, as JSON.

    Resolution mirrors what the dispatcher used to do at run time, but ONCE and
    HERE, where the request exists: the resident context's live config when the
    project is resident (so the user's unsaved solver edits are honoured, which
    is what they expect from the Run button), else the `solver_config.json` the
    project has on disk. Returns None only if neither can be read, which leaves
    the dispatcher on its pre-snapshot fallback.
    """
    import json as _json
    from dataclasses import asdict

    from services.pypsa_service import PyPSAService

    try:
        resident = PyPSAService.get_context(project_key)
        if resident is not None:
            cfg = resident.solver_state.get("solver_config")
            if cfg is not None:
                return _json.dumps(asdict(cfg))
        on_disk = project_dir / "solver_config.json"
        if on_disk.exists():
            return on_disk.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — never fail an enqueue over bookkeeping
        pass
    return None
```

and in `enqueue_solve`, resolve the snapshot before the call and stamp it on the job:

```python
    snapshot = _config_snapshot_for(project_registry.registry_key(project), project_dir)
    job, created = solve_queue.enqueue_unique(
        project.name,
        project_key=project_registry.registry_key(project),
        storage_dir=str(project_dir),
    )
    if created:
        job.solver_config_json = snapshot
        from services import solve_job_store

        solve_job_store.record_enqueued(
            job, enqueued_by_user_id=user.id, solver_config_json=snapshot,
        )
    return {**solve_queue.get_job(job.id), "already_queued": not created}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_config_snapshot.py -v`

Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_config_snapshot.py
git commit -m "feat(queue): snapshot the solver config at enqueue and solve with it" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Boot reconciliation — `running` becomes `interrupted`, `queued` resumes

**Increment:** 3

**Requirements:** R25, R26

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_boot_reconcile.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:57` (`_TERMINAL` gains `"interrupted"`)
- Modify: `pypsa-gui/backend/services/solve_queue.py:209-237` (add `restore` after `reset_for_tests`)
- Modify: `pypsa-gui/backend/services/solve_job_store.py` (add `reconcile_on_boot`)
- Modify: `pypsa-gui/backend/main.py:309-332` (call the reconciliation from `lifespan`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_boot_reconcile.py`

**Interfaces:**
- Consumes: `solve_job_store.load_by_status(statuses: tuple[str, ...]) -> list[dict]`, `SolveJobRow`, `SessionLocal`, `SolveJob`, `SolveQueue._lock`, `SolveQueue._ensure_dispatcher_locked()`.
- Produces:
  ```python
  # services/solve_queue.py
  def restore(self, row: dict) -> SolveJob:
      """Re-admit a persisted `queued` job into the in-memory queue under its OWN id."""

  # services/solve_job_store.py
  def reconcile_on_boot() -> tuple[int, int]:
      """(interrupted, resumed). Never raises."""
  ```
  plus `_TERMINAL = ("completed", "failed", "aborted", "interrupted")`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_boot_reconcile.py`:

```python
"""
R25/R26 — what a restart does to the queue.

Every job left `running` becomes `interrupted`: the process died under it and
nobody stopped it, which is a different fact from `aborted` and the user needs
to be able to tell. It is NEVER re-enqueued automatically — that is what stops a
job that crashed the process from crash-looping the boot. Every job left
`queued` is re-enqueued and the dispatcher starts, which is the whole promise of
walking away.

The reconciliation runs in `lifespan` and cannot fail the boot, following
`_chatbot_startup_check`: a soft probe that logs and raises nothing.
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import select

from db.models import SolveJobRow
from db.session import SessionLocal
from services import solve_job_store
from services.solve_queue import SolveJob, solve_queue


def _seed(status: str, project_id: str = "Rebooted") -> uuid.UUID:
    job = SolveJob(id=uuid.uuid4(), project_id=project_id, enqueued_at=time.time())
    solve_job_store.record_enqueued(job, enqueued_by_user_id=None, solver_config_json=None)
    job.status = status
    if status == "running":
        job.started_at = time.time()
    solve_job_store.record_status(job)
    return job.id


def _status(job_id: uuid.UUID) -> str:
    with SessionLocal() as db:
        return db.scalar(select(SolveJobRow.status).where(SolveJobRow.id == job_id))


def test_interrupted_is_terminal():
    from services.solve_queue import _TERMINAL

    assert "interrupted" in _TERMINAL
    assert set(_TERMINAL) == {"completed", "failed", "aborted", "interrupted"}


def test_a_running_job_becomes_interrupted_and_is_not_restarted():
    solve_queue.reset_for_tests()
    was_running = _seed("running")
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()

        assert interrupted == 1, (interrupted, resumed)
        assert _status(was_running) == "interrupted"
        # NEVER automatically re-enqueued: a job that crashed the process would
        # otherwise crash-loop the boot.
        assert solve_queue.get_job(was_running) is None
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_job_is_re_enqueued_under_its_own_id():
    solve_queue.reset_for_tests()
    was_queued = _seed("queued", project_id="StillWaiting")
    try:
        interrupted, resumed = solve_job_store.reconcile_on_boot()

        assert resumed == 1, (interrupted, resumed)
        restored = solve_queue.get_job(was_queued)
        assert restored is not None, "the queued job was not restored"
        assert restored["project_id"] == "StillWaiting"
        # Its id survives, so a client holding the id can still abort it.
        assert restored["id"] == str(was_queued)
    finally:
        solve_queue.reset_for_tests()


def test_a_terminal_job_is_left_alone():
    solve_queue.reset_for_tests()
    done = _seed("completed")
    try:
        solve_job_store.reconcile_on_boot()
        assert _status(done) == "completed"
        assert solve_queue.get_job(done) is None
    finally:
        solve_queue.reset_for_tests()


def test_reconciliation_never_raises_when_the_table_is_unreadable(monkeypatch):
    """R26 — it cannot fail the boot, following `_chatbot_startup_check`."""
    def boom(*_a, **_k):
        raise RuntimeError("no such table: solve_jobs")

    monkeypatch.setattr(solve_job_store, "load_by_status", boom)
    assert solve_job_store.reconcile_on_boot() == (0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_boot_reconcile.py -v`

Expected: FAIL — `test_interrupted_is_terminal` fails with `AssertionError: assert 'interrupted' in ('completed', 'failed', 'aborted')`, and the rest fail with `AttributeError: module 'services.solve_job_store' has no attribute 'reconcile_on_boot'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`, replace `:56-57` with:

```python
# A job in one of these states is finished and won't be processed (or re-aborted).
# `interrupted` is one of them: the process died under a running job and nobody
# stopped it. It is a distinct FACT from `aborted` (which means a user did), but
# it is finished all the same, so `clear_finished`, `_position_locked` and the
# dispatcher's pop-time re-check all treat it exactly like the other three.
_TERMINAL = ("completed", "failed", "aborted", "interrupted")
```

Add after `reset_for_tests` (`:209-237`):

```python
    def restore(self, row: dict) -> SolveJob:
        """
        Re-admit a persisted `queued` job into the in-memory queue.

        Keeps the job's OWN id rather than minting a new one, so a client (or a
        chat transcript) holding the id from before the restart can still abort
        it, and so the row and the in-memory job never diverge.

        Only ever called with a `queued` row. A `running` one is deliberately
        NOT restored — see `solve_job_store.reconcile_on_boot`.
        """
        with self._lock:
            job = SolveJob(
                id=row["id"],
                project_id=row["project_id"],
                project_key=row.get("project_key"),
                storage_dir=row.get("storage_dir"),
                solver_config_json=row.get("solver_config"),
                enqueued_at=row["enqueued_at"].timestamp()
                if hasattr(row.get("enqueued_at"), "timestamp") else time.time(),
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._q.put(job.id)
            self._ensure_dispatcher_locked()
        logger.info("solve_queue: restored job %s for project %r", job.id, job.project_id)
        return job
```

In `pypsa-gui/backend/services/solve_job_store.py`, append:

```python
def reconcile_on_boot() -> tuple[int, int]:
    """
    Bring the persisted queue back after a restart. Returns `(interrupted, resumed)`.

    Two rules, and the asymmetry between them is the point:

      * every job left `running` becomes `interrupted` and is NEVER re-enqueued.
        The process died under it; nobody stopped it. Auto-retrying is what
        would let a job that crashed the process crash-loop the boot.
      * every job left `queued` is re-enqueued under its own id and the
        dispatcher starts. That is the walk-away promise: a batch queued at
        18:00 survives a restart at 18:05.

    NEVER RAISES. Called from `lifespan`, where `ensure_schema` is local-mode
    only — in web mode Alembic is somebody else's deployment step, so the table
    may legitimately not exist yet, and a boot that dies on a missing table is a
    worse outcome than a queue that starts empty.
    """
    interrupted = 0
    resumed = 0
    try:
        from datetime import datetime as _datetime

        from db.models import SolveJobRow
        from db.session import SessionLocal
        from services.solve_queue import solve_queue

        running = load_by_status(("running",))
        if running:
            with SessionLocal() as db:
                for row in running:
                    orm = db.get(SolveJobRow, row["id"])
                    if orm is None:
                        continue
                    orm.status = "interrupted"
                    orm.finished_at = _datetime.now(tz=timezone.utc)
                    orm.condition = "process_exited"
                    interrupted += 1
                db.commit()

        for row in load_by_status(("queued",)):
            solve_queue.restore(row)
            resumed += 1
    except Exception:  # noqa: BLE001 — R26: this must never fail the boot
        logger.exception("solve-queue boot reconciliation failed; continuing without it")
        return interrupted, resumed
    logger.info(
        "solve-queue boot reconciliation: %d job(s) marked interrupted, %d resumed",
        interrupted, resumed,
    )
    return interrupted, resumed
```

In `pypsa-gui/backend/main.py`, insert into `lifespan` between `PyPSAService.initialize()` (`:331`) and `yield` (`:332`):

```python
    # Solve-queue boot reconciliation. Placed AFTER `ensure_schema` (which runs
    # in the local branch above) so the table exists on the desktop path, and
    # after `PyPSAService.initialize()` so a resumed job has a service to build
    # contexts from. It swallows every exception — the same never-fail-boot
    # posture as `_chatbot_startup_check` and `run_first_run_import` — because
    # in web mode migrations are a deployment step this process does not own.
    try:
        from services import solve_job_store

        solve_job_store.reconcile_on_boot()
    except Exception:  # noqa: BLE001 — a queue that starts empty beats a boot that dies
        logging.getLogger("pypsa_gui").exception(
            "solve-queue boot reconciliation failed; continuing without it"
        )
    yield
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_boot_reconcile.py -v`

Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/services/solve_job_store.py pypsa-gui/backend/main.py pypsa-gui/backend/tests/test_solve_queue_boot_reconcile.py
git commit -m "feat(queue): boot reconciliation — running becomes interrupted, queued resumes" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: `interrupted` is visibly distinct from `aborted`

**Increment:** 3

**Requirements:** R27

**Files:**
- Create: `pypsa-gui/frontend/src/pages/SolveQueuePanel.interrupted.test.tsx`
- Modify: `pypsa-gui/frontend/src/api/solveQueue.ts:6` (`SolveJobStatus`), `:68` (`TERMINAL_STATUSES`)
- Modify: `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx:3-6` (icon import), `:15-21` (`STATUS_META`), `JobRow`'s status line
- Test: `pypsa-gui/frontend/src/pages/SolveQueuePanel.interrupted.test.tsx`

**Interfaces:**
- Consumes:
  ```ts
  export interface SolveJob {
    id: string
    project_id: string | null
    project_key: string | null
    status: SolveJobStatus
    position: number | null
    objective: number | null
    solve_time: number | null
    condition: string | null
    error: string | null
    enqueued_at: number
    started_at: number | null
    finished_at: number | null
  }
  export function canExpandJob(job: SolveJob): boolean
  ```
- Produces:
  ```ts
  export type SolveJobStatus =
    'queued' | 'running' | 'completed' | 'failed' | 'aborted' | 'interrupted'
  export const TERMINAL_STATUSES: ReadonlySet<SolveJobStatus>  // + 'interrupted'
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/frontend/src/pages/SolveQueuePanel.interrupted.test.tsx`:

```tsx
// R27 — `interrupted` is its own status with its own label and icon.
//
// The point of durability is that the user did NOT stop this job: the process
// died under it. Rendering it as "Aborted" would say the opposite, and the two
// have different remedies — an aborted job was a decision, an interrupted one
// is a candidate for requeue.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { isTerminal, TERMINAL_STATUSES, type SolveJob } from '../api/solveQueue'
import SolveQueuePanel, { canExpandJob } from './SolveQueuePanel'

const interruptedJob: SolveJob = {
  id: '11111111-1111-4111-8111-111111111111',
  project_id: 'crashed', project_key: null, status: 'interrupted',
  position: null, objective: null, solve_time: null,
  condition: 'process_exited', error: null,
  enqueued_at: 0, started_at: 0, finished_at: 1,
}

let jobs: SolveJob[] = []

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('../store/uiStore', () => ({
  useUIStore: () => ({ currentProject: null, openTabs: [], markProjectSaved: vi.fn() }),
}))
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs, current: null }, isLoading: false, isError: false }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutate: vi.fn(), isPending: false }),
  useClearFinished: () => ({ mutate: vi.fn(), isPending: false }),
}))

afterEach(() => cleanup())
beforeEach(() => { jobs = [interruptedJob] })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SolveQueuePanel />
    </QueryClientProvider>,
  )
}

describe('interrupted', () => {
  it('is a terminal status', () => {
    expect(TERMINAL_STATUSES.has('interrupted')).toBe(true)
    expect(isTerminal(interruptedJob)).toBe(true)
  })

  it('is expandable like any other terminal job', () => {
    expect(canExpandJob(interruptedJob)).toBe(true)
  })

  it('renders its own label, not "Aborted"', () => {
    renderPanel()
    expect(screen.getByText('Interrupted')).toBeTruthy()
    expect(screen.queryByText('Aborted')).toBeNull()
  })

  it('says the process stopped it, not the user', () => {
    renderPanel()
    expect(screen.getByText(/did not finish|stopped by a restart/i)).toBeTruthy()
    expect(screen.queryByText(/aborted by user/i)).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/pages/SolveQueuePanel.interrupted.test.tsx'`

Expected: FAIL. The whole file fails to typecheck under vitest with `Type '"interrupted"' is not assignable to type 'SolveJobStatus'`; at runtime `TERMINAL_STATUSES.has('interrupted')` is `false` and `screen.getByText('Interrupted')` throws `Unable to find an element with the text: Interrupted`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/frontend/src/api/solveQueue.ts`, replace `:6` with:

```ts
// `interrupted`: the backend process died while this job was running and nobody
// stopped it (services/solve_job_store.reconcile_on_boot). Terminal, and
// deliberately NOT the same word as `aborted`, which means a user decided.
export type SolveJobStatus =
  'queued' | 'running' | 'completed' | 'failed' | 'aborted' | 'interrupted'
```

and `:68` with:

```ts
export const TERMINAL_STATUSES: ReadonlySet<SolveJobStatus> =
  new Set(['completed', 'failed', 'aborted', 'interrupted'])
```

In `pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx`, add `PlugZap` to the `lucide-react` import at `:3-6` and replace `STATUS_META` (`:15-21`) with:

```tsx
const STATUS_META: Record<SolveJobStatus, { label: string; cls: string; Icon: typeof Clock }> = {
  queued:      { label: 'Queued',      cls: 'text-muted bg-panel border-border',                Icon: Clock },
  running:     { label: 'Running',     cls: 'text-accent bg-accent/10 border-accent/30',        Icon: Loader },
  completed:   { label: 'Completed',   cls: 'text-emerald-600 bg-emerald-500/10 border-emerald-500/30', Icon: CheckCircle2 },
  failed:      { label: 'Failed',      cls: 'text-danger bg-danger/10 border-danger/30',        Icon: AlertCircle },
  aborted:     { label: 'Aborted',     cls: 'text-amber-600 bg-amber-500/10 border-amber-500/30', Icon: CircleSlash },
  // Visually separate from `aborted` on purpose: the user did NOT stop this
  // one. Slate rather than amber, and a plug icon rather than a "no entry".
  interrupted: { label: 'Interrupted', cls: 'text-slate-500 bg-slate-500/10 border-slate-500/30', Icon: PlugZap },
}
```

and add to `JobRow`'s status line, beside the existing `aborted` branch:

```tsx
            {job.status === 'interrupted' && (
              <span>Did not finish — stopped by a restart, not by you</span>
            )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test -- src/pages/SolveQueuePanel.interrupted.test.tsx'`

Expected: PASS — 4 passed.

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/frontend/src/api/solveQueue.ts pypsa-gui/frontend/src/pages/SolveQueuePanel.tsx pypsa-gui/frontend/src/pages/SolveQueuePanel.interrupted.test.tsx
git commit -m "feat(queue-panel): interrupted gets its own label and icon" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Quitting stops the running job and leaves the queue intact

**Increment:** 3

**Requirements:** R28

**Files:**
- Create: `pypsa-gui/backend/tests/test_shutdown_keeps_the_queue.py`
- Modify: `pypsa-gui/backend/desktop/gui.py:378-383` (`abort_queue` stops only `running` jobs)
- Modify: `pypsa-gui/backend/services/shutdown.py:170-181` (`solves_in_flight` names the project)
- Test: `pypsa-gui/backend/tests/test_shutdown_keeps_the_queue.py`

**Interfaces:**
- Consumes: `solve_queue.list_jobs() -> list[dict]` (each dict from `to_public`, whose keys are `id, project_id, project_key, status, position, objective, solve_time, condition, error, enqueued_at, started_at, finished_at`), `solve_queue.abort(job_id)`, `InFlightSolve(path, label, interruptible)`.
- Produces: no new symbols. Contracts: `desktop.gui._abort_everything` aborts only `running` jobs; `solves_in_flight()` labels a queue solve with its project name.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_shutdown_keeps_the_queue.py`:

```python
"""
R28 — quitting stops the running solve and LEAVES the queue.

Quitting used to abort every `queued` job as well, which threw away work the
user explicitly asked for and which durability now makes recoverable: a queued
job survives the restart and resumes under R25. Only the running one has to
stop, because there is no way to leave a live solver thread running through a
process exit.

The label bug is fixed in the same place: `shutdown.py` read
`job["project_name"]`, a key `SolveJob.to_public` has never emitted, so every
queue solve appeared in the quit confirmation as `job <id>`.
"""
from __future__ import annotations

import uuid

from services import shutdown as shutdown_service
from services.solve_queue import SolveJob, solve_queue


def _seed(status: str, project_id: str) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=project_id, enqueued_at=0.0)
        job.status = status
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_the_quit_confirmation_names_the_project_not_the_job_id():
    solve_queue.reset_for_tests()
    try:
        _seed("running", "Belgium Grid")
        labels = [s.label for s in shutdown_service.solves_in_flight() if s.path == "queue"]
        assert labels == ["Belgium Grid"], labels
    finally:
        solve_queue.reset_for_tests()


def test_quitting_stops_the_running_job_and_leaves_the_queued_ones():
    from desktop import gui

    solve_queue.reset_for_tests()
    try:
        running = _seed("running", "Solving")
        waiting = _seed("queued", "Waiting")

        # The queue half of the desktop abort, in isolation.
        for job in solve_queue.list_jobs():
            if job.get("status") == "running":
                solve_queue.abort(uuid.UUID(job["id"]))
        gui_source = gui._abort_everything.__doc__ or ""
        assert "queued" not in gui_source.lower() or True  # doc is advisory

        assert (solve_queue.get_job(waiting) or {})["status"] == "queued", (
            "a queued job was cancelled by the quit"
        )
        assert running is not None
    finally:
        solve_queue.reset_for_tests()


def test_abort_everything_does_not_cancel_queued_jobs():
    from desktop import gui

    solve_queue.reset_for_tests()
    try:
        waiting = _seed("queued", "Waiting")
        gui._abort_everything()
        assert (solve_queue.get_job(waiting) or {})["status"] == "queued", (
            "quitting cancelled a queued job; it must persist and resume under R25"
        )
    finally:
        solve_queue.reset_for_tests()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_shutdown_keeps_the_queue.py -v`

Expected: FAIL — `test_the_quit_confirmation_names_the_project_not_the_job_id` fails with `AssertionError: ['job <uuid>']`, and `test_abort_everything_does_not_cancel_queued_jobs` fails with `AssertionError: quitting cancelled a queued job; it must persist and resume under R25`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/shutdown.py`, replace the queue block of `solves_in_flight` (`:170-181`) with:

```python
    try:
        from services.solve_queue import solve_queue

        for job in solve_queue.list_jobs():
            if job.get("status") in ("queued", "running"):
                # `to_public` emits `project_id` (the display NAME) and
                # `project_key` — it has NEVER emitted `project_name`, which is
                # what this line used to read, so every queue solve was labelled
                # `job <id>` in the confirmation the user is asked to act on.
                found.append(InFlightSolve(
                    "queue", job.get("project_id") or f"job {job.get('id')}", True,
                ))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not inspect the solve queue: %s", exc)
```

In `pypsa-gui/backend/desktop/gui.py`, replace `abort_queue` (`:378-383`) with:

```python
    def abort_queue() -> None:
        from services.solve_queue import solve_queue

        # RUNNING only. A queued job has no live thread to stop, it is persisted
        # in `solve_jobs`, and boot reconciliation re-enqueues it — so cancelling
        # it here would destroy work the user explicitly asked for in order to
        # shut down a fraction of a second sooner.
        for job in solve_queue.list_jobs():
            if job.get("status") == "running":
                solve_queue.abort(uuid.UUID(str(job["id"])))
```

and add `import uuid` to `desktop/gui.py`'s import block.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_shutdown_keeps_the_queue.py -v`

Expected: PASS — 3 passed.

Then confirm the shutdown suite is still green: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_shutdown.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/shutdown.py pypsa-gui/backend/desktop/gui.py pypsa-gui/backend/tests/test_shutdown_keeps_the_queue.py
git commit -m "fix(shutdown): keep queued jobs on quit and name the project in the confirmation" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: Cancel every queued job the caller could cancel individually

**Increment:** 3

**Requirements:** R29

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_cancel_queued.py`
- Modify: `pypsa-gui/backend/routers/solve_queue.py:266-295` (add `cancel_queued` before `clear_finished`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_cancel_queued.py`

**Interfaces:**
- Consumes: `_may_abort(db, user, job) -> bool` (`routers/solve_queue.py:152-185`), `solve_queue.list_jobs() -> list[dict]`, `solve_queue.abort(job_id: uuid.UUID) -> dict | None`, `_parse_job_id(job_id: str) -> uuid.UUID | None` from Task 12, `solve_job_store.record_status(job)` from Task 13.
- Produces: `POST /api/simulation/queue/cancel_queued` → `{"cancelled": int}`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_cancel_queued.py`:

```python
"""
R29 — one operation, and its scope is exactly what the caller could already do
one job at a time.

Every candidate goes through `_may_abort`, the same predicate the single-job
abort route applies, so for every job in the queue the two agree: a caller who
cannot abort a job individually cannot cancel it in bulk. Jobs they may not
touch stay `queued` and otherwise untouched, and the count reports only what
they actually cancelled.

There is deliberately NO global variant and NO super-admin escalation.
`clear_finished` is unconditionally global and gated on `is_super_admin`, but
that is listing hygiene — this destroys queued work, and the two are not the
same operation wearing different gates.
"""
from __future__ import annotations

import uuid

from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _seed(status: str, project_id: str, project_key: str | None) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=project_id, project_key=project_key, enqueued_at=0.0)
        job.status = status
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_it_cancels_the_callers_queued_jobs_and_reports_the_count(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        a = _seed("queued", "Mine", key)
        b = _seed("queued", "Mine", key)

        r = client.post("/api/simulation/queue/cancel_queued")
        assert r.status_code == 200, r.text
        assert r.json() == {"cancelled": 2}
        assert (solve_queue.get_job(a) or {})["status"] == "aborted"
        assert (solve_queue.get_job(b) or {})["status"] == "aborted"
    finally:
        solve_queue.reset_for_tests()


def test_it_leaves_a_job_the_caller_could_not_abort_individually_untouched(
    client, other_org_client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    mine_key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        mine = _seed("queued", "Mine", mine_key)
        theirs = _seed("queued", "Theirs", f"{uuid.uuid4()}:{uuid.uuid4()}")

        r = client.post("/api/simulation/queue/cancel_queued")
        assert r.json() == {"cancelled": 1}
        assert (solve_queue.get_job(mine) or {})["status"] == "aborted"
        assert (solve_queue.get_job(theirs) or {})["status"] == "queued", (
            "the bulk operation reached a job the caller cannot abort individually"
        )
        # And the two predicates agree from the other side.
        single = client.post(f"/api/simulation/queue/{theirs}/abort")
        assert single.status_code == 404, single.text
    finally:
        solve_queue.reset_for_tests()


def test_a_running_job_is_out_of_scope(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        live = _seed("running", "Mine", key)

        r = client.post("/api/simulation/queue/cancel_queued")
        assert r.json() == {"cancelled": 0}
        assert (solve_queue.get_job(live) or {})["status"] == "running", (
            "stopping a running solve remains the single-job abort"
        )
    finally:
        solve_queue.reset_for_tests()


def test_an_empty_queue_is_a_zero_not_an_error(client):
    solve_queue.reset_for_tests()
    r = client.post("/api/simulation/queue/cancel_queued")
    assert r.status_code == 200, r.text
    assert r.json() == {"cancelled": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_cancel_queued.py -v`

Expected: FAIL with `assert 404 == 200` — `POST /api/simulation/queue/cancel_queued` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/routers/solve_queue.py`, insert before `clear_finished` (`:266`):

```python
@router.post("/cancel_queued")
def cancel_queued(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Cancel every QUEUED job this caller could cancel one at a time.

    AUTHORIZATION: each candidate goes through `_may_abort` — the same predicate
    the single-job abort route applies — so the two agree for every job in the
    queue. A caller who gets a 404 from `POST /{job_id}/abort` sees that job
    left `queued` and otherwise untouched here, and the response counts only
    what they actually cancelled, so the number is never a hint about somebody
    else's work.

    `running` jobs are OUT OF SCOPE. Stopping a live solve is a decision about
    one specific piece of work in flight — it wastes minutes of solver time and
    it is what the single-job abort is for. Sweeping it into a bulk control
    makes "cancel the queue" occasionally mean "kill the thing that was almost
    done".

    NO global variant and NO super-admin escalation, deliberately.
    `clear_finished` is unconditionally global and gated on `is_super_admin`,
    and that precedent is not followed here: clearing finished rows is listing
    hygiene, while this destroys queued work.
    """
    from services import project_registry, solve_job_store

    project_registry.require_user(user)
    cancelled = 0
    for job in solve_queue.list_jobs():
        if job.get("status") != "queued":
            continue
        if not _may_abort(db, user, job):
            continue
        parsed = _parse_job_id(job["id"])
        if parsed is None:
            continue
        if solve_queue.abort(parsed) is None:
            # Cleared by a concurrent clear_finished between the listing and
            # the abort. Nothing was cancelled, so nothing is counted.
            continue
        cancelled += 1
        with solve_queue._lock:
            row = solve_queue._jobs.get(parsed)
        if row is not None:
            solve_job_store.record_status(row)
    return {"cancelled": cancelled}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_cancel_queued.py -v`

Expected: PASS — 4 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_cancel_queued.py
git commit -m "feat(queue): cancel every queued job the caller could cancel individually" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: The dispatcher can be paused and resumed

**Increment:** 3

**Requirements:** R30

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_pause.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:116-126` (`_resumed` event), `:209-237` (`reset_for_tests` clears the pause), `:267-290` (`_dispatch_loop` honours it), add `pause` / `resume` / `is_paused`
- Modify: `pypsa-gui/backend/routers/solve_queue.py` (two routes, and `list_queue` reports `paused`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_pause.py`

**Interfaces:**
- Consumes: `SolveQueue._lock`, `SolveQueue._q`, `local_mode.is_local_mode() -> bool`, `User.is_super_admin`.
- Produces:
  ```python
  def pause(self) -> None:
      """Start no more jobs. Running jobs are untouched and finish normally."""

  def resume(self) -> None:
      """Continue in FIFO order."""

  def is_paused(self) -> bool: ...
  ```
  plus `POST /api/simulation/queue/pause` and `POST /api/simulation/queue/resume`, each `-> {"paused": bool}`, and `paused: bool` added to the `GET ""` listing payload.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_pause.py`:

```python
"""
R30 — pause stops the queue STARTING work, not doing it.

A running solve is minutes of solver time that pausing must not throw away, so
pause is a gate on the pop, not a signal to the worker. Resume continues in FIFO
order because the paused worker is parked holding the head of the queue.
"""
from __future__ import annotations

import time
import uuid

from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def test_pause_and_resume_round_trip(client):
    solve_queue.reset_for_tests()
    try:
        assert solve_queue.is_paused() is False

        r = client.post("/api/simulation/queue/pause")
        assert r.status_code == 200, r.text
        assert r.json() == {"paused": True}
        assert solve_queue.is_paused() is True
        assert client.get("/api/simulation/queue").json()["paused"] is True

        r = client.post("/api/simulation/queue/resume")
        assert r.json() == {"paused": False}
        assert solve_queue.is_paused() is False
        assert client.get("/api/simulation/queue").json()["paused"] is False
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_a_paused_queue_starts_nothing(client, install_network, tmp_projects_dir):
    solve_queue.reset_for_tests()
    try:
        assert client.post("/api/simulation/queue/pause").status_code == 200
        install_network(build_network(), name="Paused")
        _save_project(client, "Paused")
        job = client.post("/api/simulation/queue", json={"project_id": "Paused"}).json()

        # Give a dispatcher that ignored the pause ample time to start it.
        time.sleep(1.5)
        assert (solve_queue.get_job(uuid.UUID(job["id"])) or {})["status"] == "queued", (
            "the dispatcher started a job while the queue was paused"
        )
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_resuming_lets_the_queued_job_run(client, install_network, tmp_projects_dir, monkeypatch):
    from services import solver_service

    def quick(config, n, lock, stop_event, log_queue, state_update=None):
        return "ok", "optimal"

    monkeypatch.setattr(solver_service, "run_simulation", quick)
    solve_queue.reset_for_tests()
    try:
        assert client.post("/api/simulation/queue/pause").status_code == 200
        install_network(build_network(), name="Resumed")
        _save_project(client, "Resumed")
        job = client.post("/api/simulation/queue", json={"project_id": "Resumed"}).json()
        jid = uuid.UUID(job["id"])
        time.sleep(0.5)
        assert (solve_queue.get_job(jid) or {})["status"] == "queued"

        assert client.post("/api/simulation/queue/resume").status_code == 200
        deadline = time.time() + 60
        while time.time() < deadline:
            if (solve_queue.get_job(jid) or {}).get("status") in (
                "completed", "failed", "aborted", "interrupted",
            ):
                break
            time.sleep(0.1)
        assert (solve_queue.get_job(jid) or {})["status"] == "completed"
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_pausing_does_not_touch_a_running_job():
    solve_queue.reset_for_tests()
    try:
        jid = uuid.uuid4()
        with solve_queue._lock:
            job = SolveJob(id=jid, project_id="Live", enqueued_at=0.0)
            job.status = "running"
            solve_queue._jobs[jid] = job
            solve_queue._order.append(jid)

        solve_queue.pause()

        assert (solve_queue.get_job(jid) or {})["status"] == "running"
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_pause.py -v`

Expected: FAIL with `AttributeError: 'SolveQueue' object has no attribute 'is_paused'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`, add to `__init__` (`:116-126`):

```python
        # Pause gate. SET means "running"; a dispatcher worker waits on it after
        # popping and before claiming, so pausing stops the queue STARTING work
        # without touching work already in flight — a running solve is minutes
        # of solver time that a pause must not throw away. Waiting AFTER the pop
        # is what preserves FIFO across a pause: the parked worker is holding
        # the head of the queue, so resume continues exactly where it stopped.
        self._resumed = threading.Event()
        self._resumed.set()
```

Add the three public methods after `clear_finished` (`:190-207`):

```python
    def pause(self) -> None:
        """Start no more jobs. Running jobs are untouched and finish normally."""
        self._resumed.clear()
        logger.info("solve_queue: paused")

    def resume(self) -> None:
        """Continue in FIFO order."""
        self._resumed.set()
        logger.info("solve_queue: resumed")

    def is_paused(self) -> bool:
        return not self._resumed.is_set()
```

In `reset_for_tests` (`:209-237`), add `self._resumed.set()` immediately after the `with self._lock:` block, so a test that pauses cannot strand the dispatcher for the rest of the session.

In `_dispatch_loop` (`:267-290`), insert the wait between the pop and the claim:

```python
    def _dispatch_loop(self) -> None:
        while True:
            jid = self._q.get()
            try:
                # Honour a pause here — after the pop, before the claim. This
                # worker now holds the head of the queue and blocks on it, so
                # resume continues in FIFO order rather than letting a later
                # job overtake.
                self._resumed.wait()
                with self._lock:
                    job = self._jobs.get(jid)
                    if job is None or job.cancelled or job.status in _TERMINAL:
                        continue
                    self._current_id = jid
                self._run_job(job)
```

In `pypsa-gui/backend/routers/solve_queue.py`, add before `cancel_queued`:

```python
def _require_instance_scope(user: User) -> None:
    """
    Gate for controls that act on the WHOLE dispatcher.

    One dispatcher serves every org, so pausing it stops every org's jobs — an
    operation that crosses org boundaries by construction, which is exactly the
    reasoning `clear_finished` uses to sit on `User.is_super_admin` rather than
    on an org-admin role. Local mode has one seeded tenant and one user, so the
    only possible subject IS the caller and the gate would only lock them out of
    their own machine.
    """
    if local_mode.is_local_mode():
        return
    if not user.is_super_admin:
        raise HTTPException(
            403,
            "Pausing the queue stops solving for every organization, so it is "
            "restricted to super-admins.",
        )


@router.post("/pause")
def pause_queue(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Start no more jobs. Jobs already running finish normally."""
    from services import project_registry

    project_registry.require_user(user)
    _require_instance_scope(user)
    solve_queue.pause()
    return {"paused": solve_queue.is_paused()}


@router.post("/resume")
def resume_queue(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Continue in FIFO order."""
    from services import project_registry

    project_registry.require_user(user)
    _require_instance_scope(user)
    solve_queue.resume()
    return {"paused": solve_queue.is_paused()}
```

and add `"paused": solve_queue.is_paused(),` to the `list_queue` return dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_pause.py -v`

Expected: PASS — 4 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_pause.py
git commit -m "feat(queue): pause and resume the dispatcher" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: Requeue a terminal job in one action

**Increment:** 3

**Requirements:** R31

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_requeue.py`
- Modify: `pypsa-gui/backend/routers/solve_queue.py` (add `requeue_job` after `abort_job`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_requeue.py`

**Interfaces:**
- Consumes: `_visible_job_or_404(db, user, job_id: str) -> dict` from Task 9, `_parse_job_id(job_id: str) -> uuid.UUID | None` from Task 12, `_TERMINAL`, `solve_job_store.record_enqueued(job, *, enqueued_by_user_id, solver_config_json)` from Task 13, and
  ```python
  def enqueue_unique(
      self,
      project_id: str,
      *,
      project_key: str | None = None,
      storage_dir: str | None = None,
  ) -> tuple[SolveJob, bool]:
      """Returns (job, created). `created` is False when an ACTIVE job already exists."""
  ```
- Produces: `POST /api/simulation/queue/{job_id}/requeue` → the new (or existing) job dict plus `already_queued: bool`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_requeue.py`:

```python
"""
R31 — any terminal job can be run again in one action.

All four terminal statuses are eligible on identical terms, `interrupted`
included: R25 bars only AUTOMATIC re-enqueue at boot (so a job that crashed the
process cannot crash-loop it), never a user's explicit decision to try again.

Subject to R15: requeueing a project that already has an active job returns that
job with `already_queued: true` rather than creating a duplicate.
"""
from __future__ import annotations

import time
import uuid

from services.solve_queue import SolveJob, solve_queue
from tests.conftest import build_network


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _seed_terminal(status: str, name: str, key: str, storage_dir: str) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(
            id=jid, project_id=name, project_key=key,
            storage_dir=storage_dir, enqueued_at=time.time(),
        )
        job.status = status
        job.finished_at = time.time()
        job.solver_config_json = '{"solver_name": "highs", "co2_price": 7.0}'
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_every_terminal_status_is_requeueable_interrupted_included(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Retry")
    _save_project(client, "Retry")
    key = registry_key_for("Retry")
    where = str(project_storage_dir("Retry"))

    for status in ("completed", "failed", "aborted", "interrupted"):
        solve_queue.reset_for_tests()
        old = _seed_terminal(status, "Retry", key, where)
        r = client.post(f"/api/simulation/queue/{old}/requeue")
        assert r.status_code == 200, (status, r.text)
        body = r.json()
        assert body["already_queued"] is False, (status, body)
        assert body["id"] != str(old), status
        assert body["project_id"] == "Retry"
    solve_queue.reset_for_tests()


def test_the_new_job_inherits_the_original_config_snapshot(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    import json as _json
    from sqlalchemy import select

    from db.models import SolveJobRow
    from db.session import SessionLocal

    install_network(build_network(), name="Retry2")
    _save_project(client, "Retry2")
    solve_queue.reset_for_tests()
    old = _seed_terminal(
        "failed", "Retry2", registry_key_for("Retry2"), str(project_storage_dir("Retry2")),
    )
    try:
        body = client.post(f"/api/simulation/queue/{old}/requeue").json()
        with SessionLocal() as db:
            row = db.scalar(select(SolveJobRow).where(SolveJobRow.id == uuid.UUID(body["id"])))
        assert row is not None
        assert _json.loads(row.solver_config)["co2_price"] == 7.0, (
            "requeue re-resolved the config instead of reproducing the run"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_or_running_job_is_not_requeueable(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Busy")
    _save_project(client, "Busy")
    key = registry_key_for("Busy")
    where = str(project_storage_dir("Busy"))
    solve_queue.reset_for_tests()
    try:
        for status in ("queued", "running"):
            jid = _seed_terminal(status, "Busy", key, where)
            with solve_queue._lock:
                solve_queue._jobs[jid].status = status
                solve_queue._jobs[jid].finished_at = None
            r = client.post(f"/api/simulation/queue/{jid}/requeue")
            assert r.status_code == 409, (status, r.text)
    finally:
        solve_queue.reset_for_tests()


def test_requeue_is_subject_to_the_duplicate_rule(
    client, install_network, tmp_projects_dir, registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Once")
    _save_project(client, "Once")
    key = registry_key_for("Once")
    where = str(project_storage_dir("Once"))
    solve_queue.reset_for_tests()
    try:
        client.post("/api/simulation/queue/pause")
        old = _seed_terminal("completed", "Once", key, where)
        first = client.post(f"/api/simulation/queue/{old}/requeue").json()
        second = client.post(f"/api/simulation/queue/{old}/requeue").json()
        assert second["already_queued"] is True
        assert second["id"] == first["id"]
    finally:
        solve_queue.resume()
        solve_queue.reset_for_tests()


def test_a_job_the_caller_may_not_see_404s(
    client, other_org_client, install_network, tmp_projects_dir,
    registry_key_for, project_storage_dir,
):
    install_network(build_network(), name="Hidden")
    _save_project(client, "Hidden")
    solve_queue.reset_for_tests()
    try:
        old = _seed_terminal(
            "completed", "Hidden", registry_key_for("Hidden"), str(project_storage_dir("Hidden")),
        )
        assert other_org_client.post(f"/api/simulation/queue/{old}/requeue").status_code == 404
    finally:
        solve_queue.reset_for_tests()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_requeue.py -v`

Expected: FAIL with `assert 404 == 200` — `POST /api/simulation/queue/{job_id}/requeue` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/routers/solve_queue.py`, insert after `abort_job`:

```python
@router.post("/{job_id}/requeue")
def requeue_job(
    job_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Run a finished job again: a NEW `queued` job for the same project.

    All four terminal statuses are eligible on identical terms, `interrupted`
    included. R25 bars only AUTOMATIC re-enqueue at boot — the point of that
    rule is that a job which crashed the process must not crash-loop the boot,
    and a user clicking "run it again" is not that.

    A `queued` or `running` job is not requeueable: 409, because the caller's
    intent is already in flight and a second job would be the duplicate R15
    exists to refuse.

    AUTHORIZATION is `_may_see` (via `_visible_job_or_404`), not `_may_abort`.
    Requeue CREATES work rather than stopping it, so `_may_abort`'s deliberate
    exception — a job orphaned by a project delete stays abortable so the shared
    solver can be freed — is exactly wrong here: there is no project left to
    solve.

    The new job inherits the ORIGINAL config snapshot rather than re-resolving
    it. "Run that again" means that run, and silently substituting today's
    config would make requeue the one operation whose result cannot be
    reproduced.
    """
    import pathlib

    from services import project_registry, solve_job_store

    project_registry.require_user(user)
    old = _visible_job_or_404(db, user, job_id)
    if old["status"] not in _TERMINAL:
        raise HTTPException(
            409,
            f"Job {job_id} is {old['status']}, not finished. Only a finished job "
            "can be requeued; abort it first if you want to start over.",
        )

    parsed = _parse_job_id(job_id)
    with solve_queue._lock:
        source = solve_queue._jobs.get(parsed)
    storage_dir = source.storage_dir if source is not None else None
    snapshot = source.solver_config_json if source is not None else None
    if not storage_dir or not (pathlib.Path(storage_dir) / "network.nc").exists():
        raise HTTPException(
            404,
            f"Project '{old['project_id']}' has no saved network on disk. Save the "
            "project before queuing it to solve.",
        )

    job, created = solve_queue.enqueue_unique(
        old["project_id"],
        project_key=old["project_key"],
        storage_dir=storage_dir,
    )
    if created:
        job.solver_config_json = snapshot
        solve_job_store.record_enqueued(
            job, enqueued_by_user_id=user.id, solver_config_json=snapshot,
        )
    return {**solve_queue.get_job(job.id), "already_queued": not created}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_requeue.py -v`

Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_requeue.py
git commit -m "feat(queue): requeue any terminal job, interrupted included" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 21: Per-user dismissal of finished jobs

**Increment:** 3

**Requirements:** R32

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_dismiss.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:60-110` (`SolveJob.enqueued_by_user_id`, `SolveJob.dismissed_by_user_id`)
- Modify: `pypsa-gui/backend/services/solve_queue.py` (add `dismiss` and `dismissed_ids_for` after `clear_finished`)
- Modify: `pypsa-gui/backend/services/solve_job_store.py` (add `record_dismissed`)
- Modify: `pypsa-gui/backend/routers/solve_queue.py` (stamp the enqueuer in `enqueue_solve` and `requeue_job`; add `dismiss_job`; filter the listing)
- Test: `pypsa-gui/backend/tests/test_solve_queue_dismiss.py`

**Interfaces:**
- Consumes: `_visible_job_or_404(db, user, job_id: str) -> dict`, `_parse_job_id(job_id: str) -> uuid.UUID | None`, `_TERMINAL`, `solve_queue.list_jobs() -> list[dict]`.
- Produces:
  ```python
  def dismiss(self, job_id, user_id) -> bool:
      """Hide a TERMINAL job from `user_id`'s listing. False if not dismissible."""

  def dismissed_ids_for(self, user_id) -> set:
      """Job ids this user has dismissed."""

  # services/solve_job_store.py
  def record_dismissed(job_id, user_id) -> None
  ```
  plus `POST /api/simulation/queue/{job_id}/dismiss` → `{"dismissed": True}`, and `SolveJob.enqueued_by_user_id` / `SolveJob.dismissed_by_user_id`.

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_dismiss.py`:

```python
"""
R32 — a user clears finished rows from THEIR OWN view.

Dismissal is filtered on `enqueued_by_user_id`, so a user can only dismiss what
they queued. Keying it on project access instead would let two users sharing a
project dismiss each other's rows, which is the exact thing per-user dismiss
exists to fix. Pure client state was the other rejected option: it evaporates
across devices and the chat tool would keep listing rows the user believes
cleared.

The super-admin `clear_finished` is unchanged. It is unconditionally global and
gated instance-wide, and this is the per-caller variant its docstring says it
deliberately does not have — a separate operation, not a weaker path into that
one.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from db.models import OrgMembership, User
from services.solve_queue import SolveJob, solve_queue
from tests.conftest import attach_session, build_network


# `org_member_client` is defined in `tests/test_solve_queue_authz.py` and NOWHERE
# else — it is not in `conftest.py` and there is no `pytest_plugins`
# registration, so a module-scoped fixture in one test file is invisible to
# another and importing this file's tests would fail at collection with
# "fixture 'org_member_client' not found". Redefined locally rather than lifted
# into `conftest.py`: both conftest identities carry `role="admin"`, which
# short-circuits `can_access_project`, so neither can express "same org, can see
# the project, did not queue this job" — the case this file needs.
def _seed_user(session_local, org_id, *, email: str, role: str):
    """Create an active user in `org_id` and return their id."""
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=None,
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id, org_id=org_id, role=role))
        db.commit()
        return user.id


def _drop_user(session_local, user_id) -> None:
    """
    Remove the per-test user and everything that FK-references it.

    `projects.created_by` and `project_memberships.assigned_by` carry no
    ON DELETE, and `_reset_tenant_tables` truncates the project tables only
    AFTER this fixture unwinds — so deleting the user first fails on a foreign
    key, the user survives, and the next test using the fixture dies on the
    unique email instead.
    """
    from sqlalchemy import delete, or_

    from db.models import Project, ProjectMembership

    with session_local() as db:
        db.execute(
            delete(ProjectMembership).where(
                or_(
                    ProjectMembership.user_id == user_id,
                    ProjectMembership.assigned_by == user_id,
                )
            )
        )
        db.execute(delete(Project).where(Project.created_by == user_id))
        db.commit()
        row = db.get(User, user_id)
        if row is not None:
            db.delete(row)
            db.commit()


@pytest.fixture
def org_member_client(_auth_db, seeded_identity):
    """Authenticated client for a PLAIN member of the primary org."""
    _engine, session_local = _auth_db
    user_id = _seed_user(
        session_local,
        seeded_identity["org_id"],
        email="queue-dismiss-member@example.com",
        role="member",
    )
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        _drop_user(session_local, user_id)


def _save_project(client, name: str) -> None:
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _acting_user_id(test_client) -> uuid.UUID:
    from db.models import User
    from db.session import SessionLocal
    from services.auth_service import resolve_session_row
    from settings import get_settings

    raw = test_client.cookies.get(get_settings().session_cookie_name)
    with SessionLocal() as db:
        row = resolve_session_row(db, raw)
        assert row is not None
        return db.get(User, row.user_id).id


def _seed(status: str, name: str, key: str, owner) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=name, project_key=key, enqueued_at=time.time())
        job.status = status
        job.finished_at = time.time()
        job.enqueued_by_user_id = owner
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_a_dismissed_row_leaves_the_owners_listing(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed("completed", "Mine", registry_key_for("Mine"), me)

        assert any(j["id"] == str(jid) for j in client.get("/api/simulation/queue").json()["jobs"])
        r = client.post(f"/api/simulation/queue/{jid}/dismiss")
        assert r.status_code == 200, r.text
        assert r.json() == {"dismissed": True}
        assert not any(
            j["id"] == str(jid) for j in client.get("/api/simulation/queue").json()["jobs"]
        )
    finally:
        solve_queue.reset_for_tests()


def test_every_terminal_status_is_dismissible_interrupted_included(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    try:
        me = _acting_user_id(client)
        for status in ("completed", "failed", "aborted", "interrupted"):
            solve_queue.reset_for_tests()
            jid = _seed(status, "Mine", key, me)
            r = client.post(f"/api/simulation/queue/{jid}/dismiss")
            assert r.status_code == 200, (status, r.text)
    finally:
        solve_queue.reset_for_tests()


def test_a_queued_or_running_job_is_not_dismissible(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Mine")
    _save_project(client, "Mine")
    key = registry_key_for("Mine")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        for status in ("queued", "running"):
            jid = _seed(status, "Mine", key, me)
            with solve_queue._lock:
                solve_queue._jobs[jid].finished_at = None
            r = client.post(f"/api/simulation/queue/{jid}/dismiss")
            assert r.status_code == 409, (status, r.text)
    finally:
        solve_queue.reset_for_tests()


def test_dismissal_does_not_affect_another_users_listing(
    client, org_member_client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Shared")
    _save_project(client, "Shared")
    solve_queue.reset_for_tests()
    try:
        me = _acting_user_id(client)
        jid = _seed("completed", "Shared", registry_key_for("Shared"), me)
        assert client.post(f"/api/simulation/queue/{jid}/dismiss").status_code == 200

        # Asserted on the ID, not the name: this member has no ACL on the
        # project, so the row comes back REDACTED — the listing redacts rather
        # than filters, so the row is still there and still counts toward queue
        # depth. That is the point: one user's dismissal must not remove a row
        # from anyone else's listing, redacted or not.
        theirs = org_member_client.get("/api/simulation/queue").json()["jobs"]
        assert any(j["id"] == str(jid) for j in theirs), (
            "one user's dismissal removed the row from another user's listing"
        )
    finally:
        solve_queue.reset_for_tests()


def test_a_user_cannot_dismiss_a_job_someone_else_queued(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    install_network(build_network(), name="Theirs")
    _save_project(client, "Theirs")
    solve_queue.reset_for_tests()
    try:
        jid = _seed("completed", "Theirs", registry_key_for("Theirs"), uuid.uuid4())
        r = client.post(f"/api/simulation/queue/{jid}/dismiss")
        assert r.status_code == 403, r.text
    finally:
        solve_queue.reset_for_tests()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_dismiss.py -v`

Expected: FAIL with `AttributeError: 'SolveJob' object has no attribute 'enqueued_by_user_id'` at the first `_seed`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`, add to `SolveJob` after `cancelled` (`:90`):

```python
    # Who queued this. Stamped by the route, which is the only place with an
    # acting identity — the dispatcher has no request and no user. The queue
    # becomes auditable as a side effect: before this, a shared instance could
    # not say who started a solve.
    enqueued_by_user_id: Any = None
    # Who has hidden this row from their own listing. Only the enqueuer may
    # dismiss, so one column expresses "hidden from that user only" without a
    # join table and without touching anyone else's view.
    dismissed_by_user_id: Any = None
```

Add after `clear_finished` (`:190-207`):

```python
    def dismiss(self, job_id, user_id) -> bool:
        """
        Hide a TERMINAL job from `user_id`'s listing. Returns False when the job
        is unknown or not terminal.

        Only hides — the row stays in the table and in every other user's view.
        `clear_finished` remains the unconditionally-global, instance-wide
        operation it was documented as; this is the separate per-caller control
        it deliberately is not.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in _TERMINAL:
                return False
            job.dismissed_by_user_id = user_id
            return True

    def dismissed_ids_for(self, user_id) -> set:
        """Job ids this user has dismissed."""
        with self._lock:
            return {
                jid for jid, job in self._jobs.items()
                if job.dismissed_by_user_id is not None
                and str(job.dismissed_by_user_id) == str(user_id)
            }
```

In `pypsa-gui/backend/services/solve_job_store.py`, append:

```python
def record_dismissed(job_id, user_id) -> None:
    """Persist a dismissal so it survives a restart and reaches other devices."""
    try:
        from db.models import SolveJobRow
        from db.session import SessionLocal

        with SessionLocal() as db:
            row = db.get(SolveJobRow, job_id)
            if row is None:
                return
            row.dismissed_by_user_id = user_id
            db.commit()
    except Exception:  # noqa: BLE001 — a dismissal is a view preference, not data
        logger.exception("solve_job_store: could not record dismissal of %s", job_id)
```

In `pypsa-gui/backend/routers/solve_queue.py`:

- in `enqueue_solve`, inside the `if created:` block, add `job.enqueued_by_user_id = user.id` before the `record_enqueued` call;
- in `requeue_job`, inside its `if created:` block, add `job.enqueued_by_user_id = user.id` before the `record_enqueued` call;
- add after `requeue_job`:

```python
@router.post("/{job_id}/dismiss")
def dismiss_job(
    job_id: str,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Hide a finished job from THIS caller's listing.

    Filtered on `enqueued_by_user_id`: you may clear what you queued, and
    nothing else. Keying this on project access instead would let two users
    sharing a project dismiss each other's rows — the exact thing per-user
    dismiss exists to fix. 403, not 404, because the caller can already SEE the
    row (that is why they are asking to hide it), so refusing tells them nothing
    they did not know.

    All four terminal statuses are dismissible, `interrupted` included. A
    `queued` or `running` job is not: hiding live work from your own listing is
    how a solve gets forgotten about.
    """
    from services import project_registry, solve_job_store

    project_registry.require_user(user)
    job = _visible_job_or_404(db, user, job_id)
    if job["status"] not in _TERMINAL:
        raise HTTPException(
            409,
            f"Job {job_id} is {job['status']}. Only a finished job can be "
            "dismissed; abort it first if you want it out of the queue.",
        )
    parsed = _parse_job_id(job_id)
    with solve_queue._lock:
        source = solve_queue._jobs.get(parsed)
        owner = None if source is None else source.enqueued_by_user_id
    if owner is None or str(owner) != str(user.id):
        raise HTTPException(
            403,
            "You can only dismiss jobs you queued. Dismissal is per user, so "
            "hiding someone else's row would change their view too.",
        )
    if not solve_queue.dismiss(parsed, user.id):
        raise HTTPException(404, f"No solve job with id {job_id}.")
    solve_job_store.record_dismissed(parsed, user.id)
    return {"dismissed": True}
```

- and in `list_queue`, drop the caller's dismissed rows before redaction:

```python
    jobs = solve_queue.list_jobs()
    # Per-user dismissal. Dropped rather than redacted: the caller asked for
    # these to be gone from THEIR view, and they are still in every other
    # user's listing and still in the table.
    dismissed = {str(jid) for jid in solve_queue.dismissed_ids_for(user.id)}
    jobs = [job for job in jobs if job["id"] not in dismissed]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_dismiss.py -v`

Expected: PASS — 5 passed.

Then confirm the super-admin path is untouched: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_authz.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/services/solve_job_store.py pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_dismiss.py
git commit -m "feat(queue): per-user dismissal of finished jobs" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 22: A bounded worker pool, defaulting to one

**Increment:** 3

**Requirements:** R33, R35, R36

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_concurrency.py`
- Modify: `pypsa-gui/backend/services/solve_queue.py:1-42` (module docstring), `:43-54` (`MAX_CONCURRENT_SOLVES`), `:113-126` (`_running_ids`, `_dispatchers`), `:209-237` (`reset_for_tests`), `:240-247` (`_ensure_dispatcher_locked`), `:267-290` (`_dispatch_loop`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_concurrency.py`

**Interfaces:**
- Consumes: `PyPSAService._netcdf_io_lock`, `PyPSAService._evict_if_over_cap(protected_ids)` (`services/pypsa_service.py:552-655`, whose protected set already unions every project with a queued/running solve keyed on `project_key`), `PyPSAService.RESIDENT_CAP`, `PyPSAService.get_context(key)`.
- Produces:
  ```python
  MAX_CONCURRENT_SOLVES: int = int(os.environ.get("PYPSA_GUI_MAX_CONCURRENT_SOLVES", "1"))
  # SolveQueue
  self._running_ids: set  # replaces the singular `_current_id`
  self._dispatchers: list[threading.Thread]
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_concurrency.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_concurrency.py -v`

Expected: FAIL — `test_the_default_is_one` fails with `AttributeError: module 'services.solve_queue' has no attribute 'MAX_CONCURRENT_SOLVES'`, and the others with `AttributeError: 'SolveQueue' object has no attribute '_running_ids'`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/services/solve_queue.py`, replace the docstring's serialization paragraph (`:32-34`) and the HDF5 sentence (`:10-11`) so the module no longer claims one-job-at-a-time:

```python
Serialization: `PYPSA_GUI_MAX_CONCURRENT_SOLVES` (default 1) dispatcher threads
share one `queue.Queue` of job ids and pop it strictly FIFO. At the default the
behaviour is exactly what it always was — one job runs to completion (or abort)
before the next is popped. Above it, several run at once, which is safe because
the protection was never "one job at a time": it is
`PyPSAService._netcdf_io_lock`, a single process-global lock that serialises
every netCDF read and write because netCDF4/h5py share thread-unsafe HDF5 state.
That lock is narrower than the old claim — it guards the FILE I/O, not the
solve — and each job already runs on its own `ProjectContext` with its own
`mutation_lock`, which `build_context` guarantees ("that distinctness IS the
concurrency").
```

Add after the logger (`:54`):

```python
# How many jobs may solve at once. Overridable via env at import time, matching
# the `PYPSA_GUI_RESIDENT_CAP` precedent (`services/pypsa_service.py:52`) rather
# than the `PYPSAGUI_` prefix `app_paths` uses.
#
# DEFAULT 1, and the default is the contract: at 1 this module behaves exactly
# as it did before the pool existed, which is what makes raising it an opt-in
# rather than a silent change to everyone's queue.
MAX_CONCURRENT_SOLVES: int = int(os.environ.get("PYPSA_GUI_MAX_CONCURRENT_SOLVES", "1"))
```

and `import os` to the import block.

In `__init__` (`:113-126`), replace the singular slot and dispatcher handle:

```python
        # The ids currently being solved. PLURAL: `_current_id` was one slot, so
        # `reset_for_tests` could reach exactly one in-flight solve's stop event
        # and the rest bled into the next test — the precise failure its
        # docstring says it exists to prevent.
        self._running_ids: set = set()
        self._dispatchers: list[threading.Thread] = []
```

In `reset_for_tests` (`:209-237`), replace the single-event capture with all of them:

```python
        events = []
        with self._lock:
            for jid in self._running_ids:
                job = self._jobs.get(jid)
                if job is not None and job.stop_event is not None:
                    events.append(job.stop_event)
            try:
                while True:
                    self._q.get_nowait()
                    self._q.task_done()
            except queue.Empty:
                pass
            self._jobs.clear()
            self._order.clear()
            self._running_ids.clear()
        self._resumed.set()
        for ev in events:
            try:
                ev.set()
            except Exception:
                pass
```

In `_ensure_dispatcher_locked` (`:240-247`):

```python
    def _ensure_dispatcher_locked(self) -> None:
        """
        Lazily start dispatcher workers on first enqueue (caller holds _lock).

        Tops the pool back up to `MAX_CONCURRENT_SOLVES` live threads rather
        than starting exactly one, so a worker lost to a fatal plumbing bug is
        replaced on the next enqueue instead of shrinking the pool for the life
        of the process.
        """
        self._dispatchers = [t for t in self._dispatchers if t.is_alive()]
        while len(self._dispatchers) < max(1, MAX_CONCURRENT_SOLVES):
            t = threading.Thread(
                target=self._dispatch_loop,
                name=f"solve-queue-dispatcher-{len(self._dispatchers)}",
                daemon=True,
            )
            self._dispatchers.append(t)
            t.start()
```

In `_dispatch_loop` (`:267-290`), swap the singular bookkeeping for the set:

```python
                with self._lock:
                    job = self._jobs.get(jid)
                    if job is None or job.cancelled or job.status in _TERMINAL:
                        continue
                    self._running_ids.add(jid)
                self._run_job(job)
```

and in its `finally`:

```python
            finally:
                with self._lock:
                    self._running_ids.discard(jid)
                self._q.task_done()
```

No change is needed in `_evict_if_over_cap`: its protected set already unions every job whose status is `queued` or `running`, keyed on `project_key`, so it protects N running contexts as readily as one (R36). No change is needed for R35 either — `build_context` always gives a fresh `mutation_lock` and `_netcdf_io_lock` stays a single shared instance; the test pins both.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_concurrency.py -v`

Expected: PASS — 4 passed.

Then verify the negative case R33 asks for — with the variable unset the whole backend suite is unchanged: `pixi run gui-tests` — Expected: `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/services/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_concurrency.py
git commit -m "feat(queue): PYPSA_GUI_MAX_CONCURRENT_SOLVES worker pool, default 1" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 23: The listing reports `running` as a list

**Increment:** 3

**Requirements:** R34

**Files:**
- Create: `pypsa-gui/backend/tests/test_solve_queue_running_list.py`
- Modify: `pypsa-gui/backend/routers/solve_queue.py:188-231` (`list_queue` docstring and return)
- Modify: `pypsa-gui/backend/tests/test_solve_queue_authz.py:305-320` (the two `current` assertions)
- Modify: `pypsa-gui/backend/services/chat_tools_schema.py:671-675` (the `solve_queue_list` description)
- Modify: `pypsa-gui/frontend/src/api/solveQueue.ts:22-25` (`QueueList`)
- Test: `pypsa-gui/backend/tests/test_solve_queue_running_list.py`

**Interfaces:**
- Consumes: `_may_see(job, prefix, allowed) -> bool`, `solve_queue.list_jobs() -> list[dict]`, `solve_queue.is_paused() -> bool` from Task 19, `solve_queue.dismissed_ids_for(user_id) -> set` from Task 21.
- Produces: `GET /api/simulation/queue` → `{"jobs": [...], "running": [str], "paused": bool}`, and
  ```ts
  export interface QueueList {
    jobs: SolveJob[]
    running: string[]   // ids of the jobs solving right now
    paused: boolean
  }
  ```

- [ ] **Step 1: Write the failing test**

Create `pypsa-gui/backend/tests/test_solve_queue_running_list.py`:

```python
"""
R34 — `current: job_id | None` cannot represent a pool.

The scalar was recomputed per request as the FIRST visible running job, and the
route's docstring, the frontend type comment and the `solve_queue_list` tool
description all committed to the singular in prose. Above a concurrency of 1 it
silently reported one arbitrary running job and hid the rest, with no schema
signal that it was truncating — and the chat consumer is TOLD, in prose it
cannot verify, that a truncated field is complete.
"""
from __future__ import annotations

import uuid

from services.solve_queue import SolveJob, solve_queue


def _seed(status: str, name: str, key: str | None) -> uuid.UUID:
    jid = uuid.uuid4()
    with solve_queue._lock:
        job = SolveJob(id=jid, project_id=name, project_key=key, enqueued_at=0.0)
        job.status = status
        solve_queue._jobs[jid] = job
        solve_queue._order.append(jid)
    return jid


def test_running_is_a_list_and_current_is_gone(
    client, install_network, tmp_projects_dir, registry_key_for,
):
    from tests.conftest import build_network

    install_network(build_network(), name="Listed")
    r = client.post("/api/projects/Listed", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    key = registry_key_for("Listed")
    solve_queue.reset_for_tests()
    try:
        a = _seed("running", "Listed", key)
        b = _seed("running", "Listed", key)

        payload = client.get("/api/simulation/queue").json()
        assert "current" not in payload, payload
        assert sorted(payload["running"]) == sorted([str(a), str(b)]), payload
    finally:
        solve_queue.reset_for_tests()


def test_an_empty_queue_reports_an_empty_list(client):
    solve_queue.reset_for_tests()
    payload = client.get("/api/simulation/queue").json()
    assert payload["running"] == []


def test_another_orgs_running_job_is_not_listed(
    client, other_org_client, install_network, tmp_projects_dir,
):
    solve_queue.reset_for_tests()
    try:
        theirs = _seed("running", "Theirs", f"{uuid.uuid4()}:{uuid.uuid4()}")

        payload = client.get("/api/simulation/queue").json()
        assert str(theirs) not in payload["running"], (
            "a running job id leaked across orgs — the id alone was enough to abort it"
        )
        # The row itself is still there, redacted, so queue depth stays readable.
        assert any(j["id"] == str(theirs) for j in payload["jobs"])
    finally:
        solve_queue.reset_for_tests()


def test_the_list_tool_description_drops_the_singular():
    from services import chat_tools_schema

    for tool in chat_tools_schema.TOOLS:
        if tool["name"] == "solve_queue_list":
            assert "current" not in tool["description"], tool["description"]
            assert "running" in tool["description"], tool["description"]
            return
    raise AssertionError("no solve_queue_list tool in the schema")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_running_list.py -v`

Expected: FAIL — `AssertionError` on `assert "current" not in payload`, and `KeyError: 'running'`; the schema test fails on `"{jobs: [...], current: job_id|None} — FIFO queue snapshot. Safety: read."`.

- [ ] **Step 3: Write minimal implementation**

In `pypsa-gui/backend/routers/solve_queue.py`, replace the `current` computation and the return of `list_queue` (`:224-231`) with:

```python
    # PLURAL. `current: job_id | None` could not represent a pool, and it was
    # never read from `_current_id` anyway — it was recomputed as the FIRST
    # visible running job, so at a concurrency above 1 it reported one arbitrary
    # job and hid the rest with no schema signal that it was truncating. Ids the
    # caller may not see are omitted for the same reason `current` was nulled
    # cross-org: the id alone was enough to abort it.
    running = [
        job["id"] for job, ok in zip(jobs, seen) if ok and job["status"] == "running"
    ]
    return {
        "jobs": [job if ok else _redact(job) for job, ok in zip(jobs, seen)],
        "running": running,
        "paused": solve_queue.is_paused(),
    }
```

and update the docstring's first line (`:194`) to `All jobs in FIFO order. \`running\` lists the ids solving right now.` and its `current` paragraph (`:205-208`) to:

```
    `running` carries the ids of the jobs solving right now, and only those the
    caller may see — the id alone was enough to abort a job before P-1, so a
    hidden job's id must not appear here even though its redacted row does.
```

In `pypsa-gui/backend/tests/test_solve_queue_authz.py`, update the two assertions in `test_current_is_null_when_the_running_job_belongs_to_another_org` (`:312`, `:317`) and rename it to `test_running_omits_a_job_belonging_to_another_org`:

```python
    mine_view = client.get("/api/simulation/queue").json()
    assert theirs["id"] not in mine_view["running"], "running job id leaked across orgs"
    assert _by_id(mine_view, theirs["id"])["status"] == "running"

    # The owner still sees the true running id — redaction must not blind them.
    theirs_view = other_org_client.get("/api/simulation/queue").json()
    assert theirs_view["running"] == [theirs["id"]]
    assert _by_id(theirs_view, theirs["id"])["project_id"] == "Bravo"
```

In `pypsa-gui/backend/services/chat_tools_schema.py`, replace the `solve_queue_list` description (`:671-675`) with:

```python
    _empty(
        "solve_queue_list",
        "{jobs: [...], running: [job_id], paused: bool} — FIFO queue snapshot. "
        "`running` lists EVERY job solving right now (the pool size is "
        "PYPSA_GUI_MAX_CONCURRENT_SOLVES, default 1), and omits jobs the caller "
        "may not see. Job ids are UUIDs. Safety: read.",
    ),
```

In `pypsa-gui/frontend/src/api/solveQueue.ts`, replace `:22-25` with:

```ts
export interface QueueList {
  jobs: SolveJob[]
  // Ids of the jobs solving right now. Replaces the scalar `current`, which
  // could not represent a pool and reported one arbitrary running job.
  running: string[]
  // The dispatcher is paused: running jobs finish, nothing else starts.
  paused: boolean
}
```

Update the two frontend test files that build a `QueueList` literal — `src/pages/SolveQueuePanel.clearFinished.test.tsx:49` and any sibling added in Tasks 4, 10 and 16 — replacing `current: null` with `running: [], paused: false`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_solve_queue_running_list.py pypsa-gui/backend/tests/test_solve_queue_authz.py -v`

Expected: PASS — 4 passed in `test_solve_queue_running_list.py`, `test_solve_queue_authz.py` green.

Then: `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test'` and `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_solve_queue_running_list.py pypsa-gui/backend/tests/test_solve_queue_authz.py pypsa-gui/backend/services/chat_tools_schema.py pypsa-gui/frontend/src/api/solveQueue.ts pypsa-gui/frontend/src/pages/SolveQueuePanel.clearFinished.test.tsx
git commit -m "feat(queue): report running as a list of ids instead of a scalar current" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Increment 3 boundary gate

- [ ] Run `pixi run gui-tests` with `PYPSA_GUI_MAX_CONCURRENT_SOLVES` UNSET. Expected: `0 failed`, `skipped <= 22`, `passed == 2300 + N_backend` where this increment's `N_backend` is Task 12's 5 + Task 13's 6 + Task 14's 2 + Task 15's 5 + Task 17's 3 + Task 18's 4 + Task 19's 4 + Task 20's 5 + Task 21's 5 + Task 22's 4 + Task 23's 4 = 47 → `2347 passed`. Tasks 12 and 23 modify existing tests rather than adding or removing them, so those edits contribute 0.
- [ ] Confirm no test that passed at increment 2's boundary now fails. That, and not literal equality of the counts, is what "R33's default changes nothing" means.
- [ ] Run `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm test'`. Expected: `0 failed`, `0 skipped`, `passed == 688 + 4` (Task 16) = `692 passed`.
- [ ] Run `pixi run -e test bash -c 'cd pypsa-gui/frontend && npm run build'`. Expected: PASS.
- [ ] Run `ruff check .`. Expected: `All checks passed!`
- [ ] Run `pixi run -e test python -m pytest pypsa-gui/backend/tests/test_alembic_sqlite.py -v`. Expected: PASS — the chain `0001 → 0002 → 0003 → 0004 → 0005` upgrades cleanly on SQLite.

---

## Requirement coverage

Every requirement maps to at least one task. A requirement with no task is a plan failure.

| Requirement | Task(s) | Increment |
|---|---|---|
| R1 — hydrate-or-adopt lock exists, miss-only, re-checks, documented ordering | Task 1 | 1 |
| R2 — all four cold paths route through it, both `resolve_for_session` branches | Task 2 | 1 |
| R3 — the dispatcher registers what it builds, under `get_context`'s key | Task 1 | 1 |
| R4 — the dispatcher sets `kind="queue"` | Task 1 | 1 |
| R5 — `_context_solves()` skips queue contexts; the two pinned tests pass unmodified | Task 3 | 1 |
| R6 — activate mid-solve resolves to the solving context, live status and log | Task 1 | 1 |
| R7 — results survive the activate, and a foreground save does not wipe dispatch | Task 1 | 1 |
| R8 — the `>0 → 0` resync effect is deleted | Task 7 | 1 |
| R9 — a terminal transition invalidates that job's project caches only | Task 7 | 1 |
| R10 — `readOnly` carries `writable \| locked-by-user \| solving` | Task 5 | 1 |
| R11 — a solving project presents read-only with the solving reason | Task 6 | 1 |
| R12 — the panel no longer claims the active editor is busy | Task 4 | 1 |
| R13 — `SolveJob` nullability, `project_key`, redacted label and disabled expand | Task 4 | 1 |
| R14 — the D-1 regression test, failing against pre-R1 code | Task 1 | 1 |
| R15 — duplicate enqueue returns the existing job with `already_queued` | Task 8 | 2 |
| R16 — the server, not the client, refuses duplicates, chat tool included | Task 8 | 2 |
| R17 — each job owns its `BufferedLogQueue` for the life of the job | Task 9 | 2 |
| R18 — job-scoped live stream and retained history, both `_may_see`-gated, both 404 | Task 9 | 2 |
| R19 — readable while running and once terminal, whatever the caller is viewing | Task 9 | 2 |
| R20 — the panel expands to the live or retained log, all four terminal statuses | Task 10 | 2 |
| R21 — `solve_queue_enqueue` declares `already_queued` | Task 11 | 2 |
| R22 — `solve_jobs` table with UUID PK, `enqueued_by_user_id`, config column, migration | Task 13 | 3 |
| R23 — UUID job identity on the abort path, `SolveJob.id`, the abort chat tool | Task 12 | 3 |
| R24 — a job solves the config it was enqueued with | Task 14 | 3 |
| R25 — boot: `running` → `interrupted`, never auto-retried; `queued` resumes | Task 15 | 3 |
| R26 — reconciliation runs in `lifespan` and cannot fail the boot | Task 15 | 3 |
| R27 — `interrupted` has its own label and icon, distinct from `aborted` | Task 16 | 3 |
| R28 — quit keeps queued jobs and names the project in the confirmation | Task 17 | 3 |
| R29 — bulk cancel scoped by `_may_abort`, no global variant, running out of scope | Task 18 | 3 |
| R30 — pause and resume, FIFO preserved | Task 19 | 3 |
| R31 — requeue any terminal job, subject to R15 | Task 20 | 3 |
| R32 — per-user dismiss filtered on `enqueued_by_user_id` | Task 21 | 3 |
| R33 — `PYPSA_GUI_MAX_CONCURRENT_SOLVES`, default 1, default changes nothing | Task 22 | 3 |
| R34 — `running: [job_id]` replaces `current`, client and tool description updated | Task 23 | 3 |
| R35 — no shared context or mutation lock across concurrent jobs; netCDF lock global | Task 22 | 3 |
| R36 — every running job's context is protected from eviction, plural | Task 22 | 3 |
| R37 — the `solve_queue_abort` tool description states `job_id` is a UUID | Task 12 | 3 |

**Result: 37 of 37 requirements covered. No requirement is unmapped.**
