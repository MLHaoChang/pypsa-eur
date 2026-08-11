"""
Sequential multi-project solve queue (Phase A / capability C1).

The user can queue several saved projects; they solve one after another,
unattended, and their results are persisted to disk so they survive without the
project being foreground. This is the "open multiple projects and solve in a
sequential fashion" headline value.

DESIGN — per-project context (B4.3, supersedes the swap-based single slot).
netCDF/HDF5 is process-global thread-unsafe, so the dispatcher still runs jobs
strictly one at a time. But it no longer drains them through the foreground slot
(the old `load_project(id) -> run_simulation(active) -> save_project(id)`
pipeline, which co-opted the active `_active` context and its module-global
`_state`). Each job is solved on ITS OWN `ProjectContext` so the foreground —
a possibly-DIFFERENT project the user is editing — is never touched:

    active project (project_id == active id) → solve the RESIDENT instance in
        place (unsaved foreground edits included);
    other project → `build_context()` (own network + own mutation_lock + own
        solver_state) → `_hydrate_context_from_disk` → run_simulation →
        `_save_context(ctx, …)` — the active foreground ctx stays put.

The plumbing that makes this work, all per-context: `ctx.network` /
`ctx.mutation_lock` are passed to `run_simulation`; lifecycle + side-results are
written via a `ctx_state_update` sink onto `ctx.solver_state` (not the foreground
`_state`); `PyPSAService.solving_context(ctx)` retargets the transient-row
registry to the solving ctx; `_save_context` is the ctx-parameterised save (the
public `save_project` is a thin active-ctx wrapper over it). The netCDF I/O lock
stays GLOBAL — it guards process-global HDF5 state, so a background save and a
foreground save can't race on the file even though their mutation locks differ.

Serialization: one persistent daemon dispatcher thread owns a `queue.Queue` of
job ids and processes them strictly FIFO. Each job runs to completion (or abort)
before the next is popped, so only one solve runs at a time.

Known Phase-A limitation (documented, not fenced): a foreground `/run` started
concurrently with a queued solve of the SAME project both claim that one ctx's
`solver_state` (the resident in-place path) — the last writer wins. The save
path's identity guard (`expect=`) plus `_save_context`'s `_solver_in_flight_ctx`
gate still prevent a wrong-project overwrite, so the worst case is a redundant
solve — acceptable for the walk-away batch model.
"""
from __future__ import annotations

import itertools
import logging
import pathlib
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# A job in one of these states is finished and won't be processed (or re-aborted).
_TERMINAL = ("completed", "failed", "aborted")


@dataclass
class SolveJob:
    """One queued solve of a single saved project."""

    id: int
    # Human-readable project NAME. What the UI shows and what the API has
    # always called `project_id`; kept under that key in `to_public` so the
    # frontend contract is unchanged.
    project_id: str
    # Resident-registry key (`org:uuid`) for the same project — the identity
    # the eviction protected-set and the activate guard compare against. Step 0a
    # split the two: names are unique per ORG, the registry is per PROCESS, so a
    # name is no longer an identity here.
    project_key: str | None = None
    # Authorized org-scoped storage directory, resolved at enqueue time by the
    # route that checked the caller's ACL. The dispatcher must NOT re-derive a
    # path from the name — it runs on a worker thread with no request, no user,
    # and no way to authorize anything.
    storage_dir: str | None = None
    status: str = "queued"          # queued | running | completed | failed | aborted
    objective: Any = None
    solve_time: Any = None
    condition: Any = None
    error: str | None = None
    enqueued_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    # threading.Event handed to run_simulation; set by abort() of a RUNNING job.
    stop_event: Any = None
    # True when abort() cancelled a job that was still QUEUED (skipped on pop).
    cancelled: bool = False

    def to_public(self, position: int | None) -> dict:
        """
        JSON-safe view for the status/list endpoints. `position` is the
        1-based place in the queue for a still-queued job, else None.
        """
        return {
            "id": self.id,
            "project_id": self.project_id,
            "project_key": self.project_key,
            "status": self.status,
            "position": position,
            "objective": self.objective,
            "solve_time": self.solve_time,
            "condition": self.condition,
            "error": self.error,
            "enqueued_at": self.enqueued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SolveQueue:
    """FIFO dispatcher: enqueue saved projects, solve them one at a time."""

    def __init__(self) -> None:
        # Guards _jobs / _order / _current_id / _dispatcher. Held only for short
        # bookkeeping — never across a solve (that would serialise the status
        # endpoints behind a multi-minute LP).
        self._lock = threading.Lock()
        self._jobs: dict[int, SolveJob] = {}
        self._order: list[int] = []                 # insertion order, stable listing
        self._q: queue.Queue[int] = queue.Queue()
        self._counter = itertools.count(1)
        self._current_id: int | None = None
        self._dispatcher: threading.Thread | None = None

    # ── public API ──────────────────────────────────────────────────────────
    def enqueue(
        self,
        project_id: str,
        *,
        project_key: str | None = None,
        storage_dir: str | None = None,
    ) -> SolveJob:
        """Append a job for `project_id` and ensure the dispatcher is running."""
        with self._lock:
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
        return job

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [self._jobs[jid].to_public(self._position_locked(jid))
                    for jid in self._order if jid in self._jobs]

    def get_job(self, job_id: int) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_public(self._position_locked(job_id)) if job else None

    def abort(self, job_id: int) -> dict | None:
        """
        Abort a RUNNING job (signal its stop_event) or cancel a QUEUED one
        (skip on pop). No-op on an already-terminal job. Returns the job view,
        or None if there is no such job.
        """
        ev = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "running":
                ev = job.stop_event
            elif job.status == "queued":
                job.cancelled = True
                job.status = "aborted"
                job.finished_at = time.time()
            pub = job.to_public(self._position_locked(job_id))
        # Signal outside the lock — stop_event.set() never blocks, but keep the
        # discipline that no external call happens while holding _lock.
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass
        return pub

    def clear_finished(self) -> int:
        """
        Drop terminal jobs from the listing. Returns the count removed.

        UNCONDITIONALLY GLOBAL, and there is no per-caller variant on purpose.
        One queue serves the whole process, so this empties EVERY org's finished
        jobs; any route reaching it must therefore be gated on an instance-wide
        role, not an org-scoped one (`routers/solve_queue.py:clear_finished`
        gates on `User.is_super_admin`). A `predicate=` parameter here would
        read like a second, weaker authorization path that no caller uses.
        """
        with self._lock:
            removed = [jid for jid in self._order
                       if self._jobs.get(jid) and self._jobs[jid].status in _TERMINAL]
            for jid in removed:
                self._jobs.pop(jid, None)
                self._order.remove(jid)
            return len(removed)

    def reset_for_tests(self) -> None:
        """
        Drain pending jobs + clear all bookkeeping. Leaves the dispatcher
        thread parked on an empty queue (it is a daemon; killing it is neither
        possible nor necessary). Used by the pytest harness between tests.

        Best-effort: signals the stop_event of any job currently mid-solve so it
        aborts (run_simulation returns "aborted" -> no save) rather than bleeding
        its solve into the next test. Doesn't join — a sub-second test solve will
        unwind on its own; the next test's reset + reset_network() supersede it.
        """
        ev = None
        with self._lock:
            cur = self._jobs.get(self._current_id) if self._current_id is not None else None
            ev = cur.stop_event if cur is not None else None
            try:
                while True:
                    self._q.get_nowait()
                    self._q.task_done()
            except queue.Empty:
                pass
            self._jobs.clear()
            self._order.clear()
            self._current_id = None
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass

    # ── internals ───────────────────────────────────────────────────────────
    def _ensure_dispatcher_locked(self) -> None:
        """Lazily start the dispatcher on first enqueue (caller holds _lock)."""
        if self._dispatcher is None or not self._dispatcher.is_alive():
            t = threading.Thread(
                target=self._dispatch_loop, name="solve-queue-dispatcher", daemon=True
            )
            self._dispatcher = t
            t.start()

    def _position_locked(self, job_id: int) -> int | None:
        """
        1-based position among not-yet-finished jobs in FIFO order. None for
        a running/terminal job (only queued jobs have a meaningful position).
        """
        job = self._jobs.get(job_id)
        if job is None or job.status != "queued":
            return None
        pos = 1
        for jid in self._order:
            j = self._jobs.get(jid)
            if j is None or j.status in _TERMINAL:
                continue
            if jid == job_id:
                return pos
            pos += 1
        return pos

    def _dispatch_loop(self) -> None:
        while True:
            jid = self._q.get()
            try:
                with self._lock:
                    job = self._jobs.get(jid)
                    if job is None or job.cancelled or job.status in _TERMINAL:
                        continue
                    self._current_id = jid
                self._run_job(job)
            except BaseException:
                # A job failure is recorded inside _run_job; this only catches a
                # bug in the dispatcher plumbing itself. Never let it kill the
                # thread or the whole queue stalls FOREVER (the symptom: a job
                # goes terminal but the next never starts and the queue shows
                # "nothing running"). BaseException — not just Exception — because
                # a stray async KeyboardInterrupt from the abort watcher must NOT
                # be able to unwind the `while True` and terminate the dispatcher.
                # (The watcher is single-shot now, so this is belt-and-braces.)
                logger.exception("solve_queue: dispatcher error on job %s", jid)
            finally:
                with self._lock:
                    self._current_id = None
                self._q.task_done()

    def _run_job(self, job: SolveJob) -> None:
        """
        Solve one queued project on ITS OWN ProjectContext and persist the
        result. Runs in the dispatcher thread; one job at a time.

        B4.3 — per-project isolation. The dispatcher no longer drains jobs
        through the foreground slot (the old swap-based load_project ->
        run_simulation -> save_project pipeline, which co-opted the active ctx).
        Instead it resolves the right context:

          * project IS the foreground (project_id == active id) → solve the
            RESIDENT in-memory instance in place, so unsaved foreground edits
            are included;
          * project is NOT the foreground → build a fresh background ctx (own
            network + own mutation_lock + own solver_state), hydrate it from
            disk, solve it, and save it — leaving the foreground active ctx
            (a DIFFERENT project) completely untouched.

        All lifecycle/result writes target the SOLVING ctx's solver_state (via
        `ctx_state_update`), never the foreground's `_state`. The transient-row
        marks land on the solving ctx via `PyPSAService.solving_context(ctx)`.
        """
        # Lazy imports: services must not import routers at module load (routers
        # import services -> cycle). The dispatcher is an orchestrator, so
        # reaching into the route-handler logic here is intentional.
        from routers import simulation as sim
        from routers.projects import (
            _hydrate_context_from_disk,
            _safe_project_dir,
            _save_context,
        )
        from services.pypsa_service import PyPSAService
        from services.solver_service import run_simulation

        project_id = job.project_id
        me = threading.current_thread()
        stop_event = threading.Event()
        log_queue = sim.BufferedLogQueue()

        final_status = "failed"
        condition: Any = None
        objective: Any = None
        solve_time: Any = None
        error: str | None = None
        t0 = time.time()
        ctx = None
        try:
            # Claim under the bookkeeping lock — but honour an abort that landed
            # in the pop->claim window (abort() flips a still-queued job to
            # cancelled+aborted; the dispatcher already vetted `cancelled` when it
            # popped, but an abort can arrive in the gap between that pop and this
            # claim). Without this re-check the unconditional status="running"
            # below would clobber the abort and the job would solve anyway. The
            # `finally` records the terminal state; returning here skips the solve.
            with self._lock:
                if job.cancelled:
                    final_status = "aborted"
                    return
                job.status = "running"
                job.started_at = time.time()
                job.stop_event = stop_event

            # 1. Resolve the context to solve. If the queued project IS the
            #    foreground, solve the resident instance in place (unsaved edits
            #    included). Otherwise build a fresh background ctx and hydrate it
            #    from disk — its own network + mutation_lock + solver_state, so
            #    the foreground active ctx (a different project) is never touched.
            # Step 0b: "is this the foreground?" is no longer a single answer —
            # each SESSION has its own active project. The rule that still means
            # what it meant is RESIDENCY: if this project already has a live
            # in-memory context, solve THAT one in place so the user's unsaved
            # edits are included; otherwise hydrate a private copy from disk.
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
                        # Bind UNCONDITIONALLY, and as a unit. `register` below
                        # is unconditional, so a context whose identity was
                        # only half-applied would sit in `_contexts[key]` with
                        # a `registry_key` that falls back to the DISPLAY NAME
                        # (project_context.py:198-200) — the registry saying
                        # one thing and the context another, which is the
                        # re-keying trap CLAUDE.md records. `bind_project`
                        # moves name + org_id + project_uuid + storage_dir
                        # together, so `ctx.registry_key == key` holds for a
                        # job with a storage_dir and for one without.
                        org, sep, uuid_part = key.partition(":")
                        keyed = bool(sep and org and uuid_part)
                        PyPSAService.bind_project(
                            project_id,
                            # A key that is not `org:uuid` can only come from a
                            # hand-made job; name-keying it back is what
                            # `registry_key` does for an unbound context, so
                            # the two still agree.
                            org_id=org if keyed else None,
                            project_uuid=uuid_part if keyed else None,
                            storage_dir=job.storage_dir,
                            ctx=ctx,
                        )
                        PyPSAService.register(key, ctx)

            n = ctx.network
            lock = ctx.mutation_lock
            config = ctx.solver_state["solver_config"]

            # `_state`-style writer scoped to THIS ctx (the per-context analogue
            # of sim._state_update). run_simulation pushes its side-results
            # through this sink, so they land on the solving ctx — for a
            # background solve that is NOT the foreground's state.
            def ctx_state_update(**kw):
                with ctx.solver_state_lock:
                    ctx.solver_state.update(kw)

            # 2. Claim the ctx lifecycle: status=running + a live worker handle
            #    (this thread) so a concurrent foreground /run on the SAME ctx
            #    409s, and wipe stale results so the fresh solve starts clean.
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

            # 3. Solve (synchronous; honours stop_event). Writes LOPF dispatch
            #    onto n's _t tables and side-results into ctx.solver_state via the
            #    injected sink. `solving_context(ctx)` retargets the transient-row
            #    registry to THIS ctx so the VOLL-slack / vintage-clone marks (and
            #    the restore-time clear) land on the solving ctx, not the
            #    foreground — the core B4.3 isolation guarantee.
            with PyPSAService.solving_context(ctx):
                status, condition = run_simulation(
                    config, n, lock, stop_event, log_queue, state_update=ctx_state_update
                )
            solve_time = round(time.time() - t0, 2)
            # Price with THIS job's config, not the foreground's — the myopic
            # branch reads capital_cost defaults (overnight_cost / discount_rate
            # fills) off it, and this runs outside `solving_context(ctx)`.
            objective = sim._compute_run_objective(n, config)
            if status == "aborted":
                final_status = "aborted"
            elif status in ("ok", "optimal"):
                final_status = "completed"
            else:
                final_status = "failed"

            # 4. Reflect the result in the ctx lifecycle AND disown the worker
            #    handle (thread=None) so `_save_context`'s `_solver_in_flight_ctx`
            #    gate passes — the solve is done and restore_modelling() reverted
            #    the transient LP transforms, so the network is clean to
            #    serialise. Per-ctx thread-identity guard exactly like the /run
            #    worker: if something disowned us mid-solve (nulls thread), honour
            #    the abandon — don't clobber its state and don't persist.
            if ctx.solver_state.get("thread") is not me:
                # Disowned: a newer run (a concurrent foreground /run on the same
                # project, or a reset) claimed this ctx's solver_state mid-solve.
                # Our result is discarded. Record it as SUPERSEDED — distinct
                # from a user abort, and accurate vs leaving the stale solve
                # `condition` (e.g. "optimal") under status="aborted". We do NOT
                # write ctx.solver_state: a live successor may own it and we'd
                # clobber its in-flight state (the SSE/_state view belongs to the
                # successor now; job.status here is the authoritative queue view).
                final_status = "aborted"
                condition = "superseded"
                error = "Superseded by a newer run on the same project"
            else:
                ctx_state_update(
                    status=final_status, condition=condition,
                    objective=objective, solve_time=solve_time, thread=None,
                    # Clear the OWNERSHIP mark with the worker handle it
                    # describes. `kind` outlives `thread` otherwise, and the
                    # context is a plain foreground one again the moment this
                    # returns. `/run` and `run_ac_pf` happen to overwrite
                    # `kind` on their own claim, but `shutdown._context_solves`
                    # skips on `kind` ALONE — so any future worker that claims
                    # `thread` without setting `kind` would inherit an
                    # invisible solve from the last queue job.
                    kind=None,
                )
                # 5. Persist only a clean, successful solve, via `_save_context`
                #    bound to THIS ctx (NOT save_project — that saves the active
                #    foreground). network.nc carries the LOPF dispatch;
                #    results_state.pkl carries AC-PF + lost-load (built from
                #    ctx.solver_state). expect=project_id arms the identity guard.
                if final_status == "completed":
                    # persist_user_ts ONLY when this ctx IS the foreground
                    # (resident solve-in-place): `_serialize_user_ts()` reads the
                    # foreground's module-global `_user_ts`, so writing it for a
                    # BACKGROUND project would clobber that project's own
                    # user_ts.json with the foreground's profiles. The background
                    # project's on-disk profiles + the netcdf's baked profiles are
                    # already correct, so we leave user_ts.json untouched there.
                    _save_context(
                        ctx, project_id, expect=project_id,
                        # `_user_ts` is still a module GLOBAL belonging to the
                        # process foreground. Persist it only when the context
                        # being solved IS that foreground; for anything else,
                        # writing it would stamp one project's profiles onto
                        # another's `user_ts.json`. Not writing it is safe — the
                        # netcdf already carries the profiles.
                        persist_user_ts=(ctx is PyPSAService._active),
                        storage_dir=(
                            pathlib.Path(job.storage_dir) if job.storage_dir else None
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 — record + continue draining
            final_status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            condition = "queue_error"
            logger.exception("solve_queue: job %s (%r) failed", job.id, project_id)
            try:
                if ctx is not None and ctx.solver_state.get("thread") is me:
                    with ctx.solver_state_lock:
                        # `kind` goes with `thread` here for the same reason as
                        # on the success path above: this context stops being
                        # queue-owned the instant we disown the worker.
                        ctx.solver_state.update(
                            status="failed", condition="queue_error",
                            thread=None, kind=None,
                        )
            except Exception:
                pass
        finally:
            with self._lock:
                job.status = final_status
                job.condition = condition
                job.objective = objective
                job.solve_time = solve_time
                job.error = error
                job.finished_at = time.time()
            # Close the SSE log stream for this job so the foreground consumer's
            # `done` event fires (run_simulation pushes None on its own success
            # path, but the abort/error paths above may not have).
            try:
                log_queue.put(None)
            except Exception:
                pass


# Module-level singleton — the router imports this instance.
solve_queue = SolveQueue()
