"""LC-12: a plan's job ends, and the conversation continues by itself, once.

A run step of a `start_task` plan waits on its job durably (the record lives
in the task file, `services/assistant_tasks.py`). When the job ends, the step
is settled and one continuation is due to the conversation that started it:
the same owner, project, session and plan. `run_turn(..., continuation_job_id=)`
claims it (at most once per job, durable across restarts and closed tabs) and
runs an ordinary turn whose user message is "job <id> finished: <status>".

Never after the plan was cancelled or the job aborted by the user; never in
another project (the task store is per project). The continuation carries no
approval: every card in that turn still needs its own click.
"""
import logging
import threading
import time

from harness import job_adapters
from services import assistant_tasks

logger = logging.getLogger(__name__)


def due(session) -> list[dict]:
    """Finished jobs of this conversation's plans that have not continued yet."""
    return assistant_tasks.due_continuations(session)


def claim(session, job_id: str) -> str:
    """Mark the job's continuation delivered and return the turn's message.

    Raises 409 `continuation_unavailable` when none is due to this session."""
    return assistant_tasks.claim_continuation(session, job_id)


def dismiss(session, job_id: str) -> bool:
    """The user stopped waiting: no continuation for this job. The job runs on."""
    return assistant_tasks.dismiss_continuation(session, job_id)


# A plan's run step holds the turn this long for a quick solve, so the plan
# goes on in the same turn; after it the turn is released and the
# continuation takes over.
GRACE_SECONDS = 10.0
POLL_SECONDS = 0.2
MAX_FOLLOW_SECONDS = 24 * 3600
_WAKE = threading.Event()  # never set in production; a poll interval, not a signal


def follow_job(ctx, run_token, task_path, ticket, job_id, *, prior_worker=None, solve_ctx=None) -> threading.Event:
    """Follow a plan's job to its end on its own thread (not the session's
    progress monitor, which stops when the chat closes), then settle the
    waiting step from that job's own record. Returns an event set once the
    step is settled. Takes no network lock.

    `prior_worker` is the solver worker before the run call: a job bound to
    it (or to none) has no solve of its own, so its outcome is unknown.
    `ctx` is the context the job is bound to (its identity); `solve_ctx` the
    one whose network the solve ran on, when the two differ."""
    settled = threading.Event()
    binding = job_adapters._ACTIVITY_BINDINGS.get(job_id)
    # A foreground solve is bound to its worker thread (or, without one, its start time).
    no_origin = binding is None or (binding[2] is prior_worker if binding[2] is not None else binding[3] is None)

    def run():
        try:
            deadline = time.monotonic() + MAX_FOLLOW_SECONDS
            status = "unknown"
            while not no_origin and time.monotonic() < deadline:
                status = job_adapters._snapshot_bound_job(job_id, ctx).get("status")
                if status != "running":
                    break
                _WAKE.wait(POLL_SECONDS)
            settle_from_record(ctx, run_token, task_path, ticket, job_id, status, solve_ctx=solve_ctx)
        except Exception:  # noqa: BLE001 — a lost settlement leaves the step waiting, never re-runs
            logger.exception("plan job %s could not be settled", job_id)
        finally:
            settled.set()

    threading.Thread(target=run, name=f"plan-job-{job_id[3:11]}", daemon=True).start()
    return settled


def _capture_of(job_id):
    """A queue job's HH-10 capture record, or None (no capture, or no such job)."""
    import uuid

    from services import solve_queue
    try:
        job = solve_queue.solve_queue.get_job(uuid.UUID(str(job_id)))
    except Exception:  # noqa: BLE001 — an unreadable job is read below from its run record
        return None
    capture = (job or {}).get("capture")
    return capture if isinstance(capture, dict) else None


def settle_from_record(ctx, run_token, task_path, ticket, job_id, status, *, solve_ctx=None):
    """Settle from the job's own terminal record, never from live state.

    `completed` needs the job's identity-bound native status before AND after
    its run manifest is published, and the manifest's own objective and input
    fingerprint, that fingerprint being the one the solve recorded under its
    lock with no edit landing before the publish; anything less settles
    `uncertain`. Failed, aborted and
    unknown settle `failed`."""
    from services import run_evidence

    solve_ctx = solve_ctx or ctx
    if status in ("completed", "failed", "aborted") and run_token is not None:
        run_evidence.publish_foreground_run(run_token, solve_ctx)
        if job_adapters._snapshot_bound_job(job_id, ctx).get("status") != status:
            # A later solve took the slot while publishing: the record may not be this job's.
            if status == "completed":
                return assistant_tasks.settle_job(task_path, ticket, job_id, "completed", "uncertain")
            status = "unknown"
    if status != "completed":
        native = status if status in ("failed", "aborted") else "unknown"
        return assistant_tasks.settle_job(task_path, ticket, job_id, native, "failed")
    record = (run_evidence._load_run(run_token["root"], run_token["project_id"], run_token["run_id"])
              if run_token is not None else None)
    objective = ((record or {}).get("metrics") or {}).get("objective", {}).get("value")
    fingerprint = (record or {}).get("fingerprint")
    # The step's fingerprint must be the solve's own, recorded under its lock.
    solved = (getattr(solve_ctx.network, "meta", None) or {}).get("assistant_solve_input_fingerprint")
    if not record or record.get("status") != "completed" or record.get("job_id") != job_id \
            or objective is None or not fingerprint or fingerprint != solved \
            or "inputs_changed_before_publish" in (record.get("warnings") or []):
        return assistant_tasks.settle_job(task_path, ticket, job_id, "completed", "uncertain")
    return assistant_tasks.settle_job(task_path, ticket, job_id, "completed", "completed",
                                      {"run_id": run_token["run_id"], "objective": objective,
                                       "fingerprint": fingerprint})


# ── Queued runs (study copilot slice 2) ──────────────────────────────────────
# `run_sensitivity_sweep` and `solve_queue_enqueue` steps wait on their
# solve-queue jobs the same way: one follower per step, never in the request
# thread, settled from native records only.

QUEUE_POLL_SECONDS = 1.0
MAX_QUEUE_FOLLOW_SECONDS = 6 * 3600  # then the step settles uncertain; nothing polls on


STUDY_STAGE_KINDS = {"options": "study_options", "tornado": "study_tornado"}


def plan_jobs(tool, args, result) -> list[dict]:
    """The jobs a plan step waits on (LC-45): a sweep or enqueue's queue jobs, a
    profile generation's queued job, or a decision study's run record.

    A profile job carries the project and the source and technology it was
    asked for, so its artifact is matched to that request (L1). A study job's
    `job_id` is `study:<study_id>:<stage>` and its `attempt` is the
    `started_at` of the record the step's own call started, so a later re-run's
    record never settles this step (L3)."""
    args = args if isinstance(args, dict) else {}
    if tool == "generate_renewable_profile":
        return [{**job, "kind": "profile_gen", "project_ref": str(args.get("project_id", "")),
                 "source": str(args.get("source") or "synthetic"), "technology": str(args.get("technology", ""))}
                for job in queue_jobs(result)]
    if tool != "run_decision_study":
        return queue_jobs(result)
    if not isinstance(result, dict) or not args.get("study_id"):
        return []
    stage = result.get("stage") if result.get("stage") in STUDY_STAGE_KINDS else "options"
    job = {"job_id": f"study:{args['study_id']}:{stage}", "kind": STUDY_STAGE_KINDS[stage],
           "project_id": str(args.get("project", "")), "study_id": str(args["study_id"])}
    job["attempt"] = _study_record(job).get("started_at")
    return [job]


def queue_jobs(result) -> list[dict]:
    """The queue jobs a sweep or enqueue result started, as {job_id, project_id},
    with the sweep case's `name` (LC-17: each case is reported by its name).

    A sweep case reused from an earlier solve started no job. The child's
    UUID is the second half of the job's `project_key` (`org:uuid`)."""
    if not isinstance(result, dict):
        return []
    if "cases" in result:
        found = [(case.get("job"), case.get("name")) for case in result["cases"] if isinstance(case, dict)]
    else:
        found = [(result, None)]
    return [{"job_id": str(job["id"]), "project_id": str(job.get("project_key", "")).split(":", 1)[-1],
             **({"name": str(name)} if name else {})}
            for job, name in found if isinstance(job, dict) and job.get("id")]


KNOWN_KINDS = frozenset({None, "profile_gen", *STUDY_STAGE_KINDS.values()})


def _study_state(job) -> tuple[str, str | None]:
    """(native status, reason) of a decision-study step's own run record.

    `done` is completed only when nothing was left unreached; otherwise it is
    `partial` with the reason (L4). A record that is gone, unreadable or no
    longer the one this step started is `unknown` (L3, L5)."""
    rec = _study_record(job)
    status = rec.get("status")
    if not status or rec.get("started_at") is None or rec.get("started_at") != job.get("attempt"):
        return "unknown", None
    if status == "done":
        left = (rec.get("pending") and "options not reached: " + ", ".join(map(str, rec["pending"]))
                or rec.get("budget_exhausted") and "the solve budget ran out"
                or rec.get("deadline_exceeded") and str(rec["deadline_exceeded"])
                or rec.get("error"))
        return ("partial", str(left)[:200]) if left else ("completed", None)
    if status == "aborting":
        return "running", None
    return status, (str(rec["error"])[:200] if rec.get("error") else None)


def _queue_job_status(job) -> str:
    """The job's own status from the solve queue, as the acting user sees it."""
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools
    if job.get("kind") not in KNOWN_KINDS:
        return "unknown"  # L2: a kind this code does not know is never completed
    if job.get("kind") in STUDY_STAGE_KINDS.values():
        return _study_state(job)[0]
    try:
        with chat_tools._acting() as (db, user):
            return _visible_job_or_404(db, user, job["job_id"]).get("status") or "unknown"
    except Exception:  # noqa: BLE001 — an unreadable job proves nothing
        return "unknown"


def _study_record(job) -> dict:
    """A decision study's run (or tornado) record, read as the acting user;
    {} when unreadable (an unreadable record proves nothing)."""
    from routers import studies as R
    from services import chat_tools
    handler = R.get_findings_tornado if job.get("kind") == "study_tornado" else R.get_study_run
    try:
        rec = chat_tools._route(handler, job["study_id"], project=chat_tools._authorized_project(job["project_id"]))
    except Exception:  # noqa: BLE001
        return {}
    return rec if isinstance(rec, dict) else {}


def _profile_artifact(job) -> dict | None:
    """The pending profile THIS `profile_gen` job wrote, or None (L1).

    The artifact must name the job's id and carry the source and technology the
    call asked for; it is read from the project the call named, so an earlier
    job's artifact or another project's never counts."""
    from services import chat_tools
    try:
        pending = chat_tools.list_pending_profiles(job["project_ref"]).get("pending") or []
    except Exception:  # noqa: BLE001 — unreadable evidence is no evidence
        return None
    found = next((r for r in pending if isinstance(r, dict) and r.get("job_id") == job["job_id"]), None)
    if found is None or found.get("source") != job.get("source"):
        return None
    if job.get("technology") and found.get("technology") != job["technology"]:
        return None
    return {"job_id": job["job_id"], "project_id": job["project_ref"], "source": found.get("source")}


def _queue_job_error(job) -> str | None:
    """A failed job's own error line, bounded; None when unreadable."""
    if job.get("kind") in STUDY_STAGE_KINDS.values():
        return _study_state(job)[1]
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools
    try:
        with chat_tools._acting() as (db, user):
            error = _visible_job_or_404(db, user, job["job_id"]).get("error")
    except Exception:  # noqa: BLE001 — an unreadable job names no cause
        return None
    return str(error)[:200] if error else None


def saved_queue_total(saved) -> float | None:
    """The queue's own total (`_compute_run_objective`: the LP's objective plus
    its constant, which carries existing-capacity CAPEX) recomputed on a saved
    network with the project's saved solver config; None when it cannot be told.

    No saved config (the function would price with the foreground's), inputs
    edited since the solve, or no LP objective is no total (GK B 38, A1). Shared
    by the plan-step settle and the experiment refresh (SH-39)."""
    from routers.simulation import _compute_run_objective
    from services import workflow_tools as wt
    cfg = saved.solver_state.get("solver_config")
    if (cfg is None or wt.solve_freshness(saved)["inputs_changed_since_solve"] is not False
            or getattr(saved.network, "objective", None) is None):
        return None
    return _compute_run_objective(saved.network, cfg)


def _queue_run(job) -> dict | None:
    """A completed job's evidence, or None when any native record disagrees.

    The run manifest (`run_evidence.publish_queue_run`) names the job, its kind
    and objective; the solve's input fingerprints are on the child's saved
    network, stamped under the solve lock by `workflow_tools._record_solve_inputs`
    and compared by `workflow_tools.solve_freshness` with the same
    `_network_input_fingerprint`. An edit saved after the solve fails it."""
    from fastapi import HTTPException
    from harness.durable import network_guard
    from services import chat_tools, project_registry, run_evidence, workflow_tools as wt
    run_id = run_evidence.queue_run_id(job["job_id"])
    try:
        # Read-only, never activated; under the lock a child save takes, as the
        # experiment refresh reads it. A busy lock is no verdict: retry later.
        with network_guard(), chat_tools._acting() as (db, user):
            child = project_registry.resolve_project(db, user, job["project_id"])
            root = project_registry.project_dir(child)
            manifest = run_evidence._load_run(root, str(child.id), run_id)
            if (not manifest or manifest.get("status") != "completed" or manifest.get("job_id") != job["job_id"]
                    or manifest.get("kind") != "solve_queue" or not run_evidence._evidence_available(root, manifest)):
                return None
            objective = (manifest.get("metrics") or {}).get("objective", {}).get("value")
            saved = wt._load_context(child)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("error_kind") == "assurance_context_changed":
            raise QueueBusy() from exc
        return None
    except Exception:  # noqa: BLE001 — unreadable evidence is no evidence
        return None
    # The manifest stores the job's objective unrounded: the queue's own total,
    # recomputed on the saved network and compared exactly (`saved_queue_total`).
    # No total is no evidence (GK B 38, A1).
    if isinstance(objective, bool) or not isinstance(objective, (int, float)):
        return None
    total = saved_queue_total(saved)
    if total is None or total != objective:
        return None
    return {"job_id": job["job_id"], "run_id": run_id, "project_id": job["project_id"], "objective": objective}


class QueueBusy(Exception):
    """The network lock was busy; the evidence read is retried, not judged."""


def queue_outcome(jobs) -> tuple[str, str, dict]:
    """(step status, native status, result) for a queued step's jobs.

    Completed only when every job completed with intact evidence. Failed only
    when no job completed (each failed or was aborted): a retry then repeats
    nothing that landed. Anything else, a mix included, is uncertain.

    Whatever the step's status, `cases` holds each job's own outcome (LC-17):
    a failed case is named with its job's error and never hides the others.
    A completed job whose evidence disagrees is `unverified`, never completed."""
    runs, natives, cases = [], [], []
    for job in jobs:
        native = _queue_job_status(job)
        natives.append(native)
        case = {k: job[k] for k in ("name", "project_id", "job_id", "kind") if job.get(k)}
        if native == "completed":
            # HH-10 (issue 05, C6f): the capture is read first. A harness run's captured
            # solve that was not applied ran, and its result is not the project's, whatever
            # the evidence says. The step is done; the run's solve gate reads why
            # (`run_gates._not_applied`) and the run stops `inputs_changed`.
            capture = None if job.get("kind") else _capture_of(job.get("job_id"))
            if job.get("kind") == "profile_gen":
                run = _profile_artifact(job)
            elif job.get("kind") in STUDY_STAGE_KINDS.values():
                run = {"job_id": job["job_id"], "study_id": job["study_id"]}
            elif capture is not None and capture.get("applied") is not True:
                run = {"job_id": job.get("job_id"), "capture": {"applied": False, "reason": capture.get("reason")}}
            else:
                run = _queue_run(job)
            runs.append(run)
            case["status"] = ("not_applied" if run and "capture" in run else "completed") if run else "unverified"
        else:
            case["status"] = native
            if native == "failed" or job.get("kind") in STUDY_STAGE_KINDS.values():
                case["error"] = _queue_job_error(job)
        cases.append(case)
    if natives and all(n == "completed" for n in natives) and all(runs):
        return "completed", "completed", {"runs": runs, "cases": cases}
    if natives and all(n in ("failed", "aborted") for n in natives):
        return "failed", "aborted" if "aborted" in natives else "failed", {"cases": cases}
    return "uncertain", "completed" if "completed" in natives else "unknown", {"cases": cases}


def settle_queue_from_record(task_path, ticket):
    """Settle a waiting queued step from its jobs' own records."""
    import json
    from pathlib import Path
    try:
        task = json.loads(Path(task_path).read_text())
        step = next(s for s in task["steps"] if s["id"] == ticket[1])
        jobs, key = step["job"]["jobs"], step["job"]["job_id"]
    except (OSError, ValueError, KeyError, StopIteration):
        return None
    status, native, result = queue_outcome(jobs)  # QueueBusy propagates: the follower retries
    return assistant_tasks.settle_job(task_path, ticket, key, native, status, result or None)


def follow_queue_job(task_path, ticket) -> threading.Event:
    """One follower per queued step, on its own thread with the caller's
    identity. It polls the step's jobs until all are terminal, then settles;
    at the cap it settles uncertain. A cancelled plan stops it quietly: the
    jobs run on and are reported as they are, nothing settles the plan."""
    import contextvars
    import json
    from pathlib import Path
    settled = threading.Event()
    assistant_tasks.track_queue_follower(ticket)

    def run():
        try:
            deadline = time.monotonic() + MAX_QUEUE_FOLLOW_SECONDS
            while True:
                task = json.loads(Path(task_path).read_text())
                if task.get("status") == "cancelled":
                    return
                step = next(s for s in task["steps"] if s["id"] == ticket[1])
                if step["status"] != "waiting" or step.get("attempt_id") != ticket[2]:
                    return
                ended = all(_queue_job_status(job) not in ("queued", "running") for job in step["job"]["jobs"])
                if ended or time.monotonic() >= deadline:
                    try:
                        settle_queue_from_record(task_path, ticket)
                        return
                    except QueueBusy:
                        if time.monotonic() >= deadline:
                            raise
                _WAKE.wait(min(QUEUE_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        except Exception:  # noqa: BLE001 — a lost settlement leaves the step waiting, never re-runs
            logger.exception("plan queue step %s could not be settled", ticket[1])
        finally:
            assistant_tasks.forget_queue_follower(ticket)
            settled.set()

    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(run,), name=f"plan-queue-{ticket[1][:8]}", daemon=True).start()
    return settled


# ── Engine loops (study harness issue 05, PR C) ──────────────────────────────
# A result rule's `run_margin_loop` / `run_coupling_loop` step waits on the
# loop's own record in the run context's solver state (`_state[study]`), not
# on a queue job. One follower per step, settled once, from that record only.

ENGINE_POLL_SECONDS = 0.5
ENGINE_LOCK_SECONDS = 5.0
MAX_ENGINE_FOLLOW_SECONDS = 24 * 3600  # then the step settles `timed_out` (H2): `deadline_at` on the step
_ENGINE_FOLLOWERS: dict = {}  # ticket -> this process's live follower thread


def following(ticket) -> bool:
    """Whether this process has a live follower for the engine step `ticket`."""
    thread = _ENGINE_FOLLOWERS.get(tuple(ticket))
    return thread is not None and thread.is_alive()


def expire_engine(task_path, ticket, *, instance, now, lapsed_ok=False) -> bool:
    """H2: settle a waiting engine step `timed_out` once its wall-clock
    `deadline_at` (recorded at start) has passed. Under `runner._file_lock`,
    by the run's lease holder only; the boot sweep (`lapsed_ok`) may also act
    on a lapsed lease, never on another instance's live one. True when it
    settled the step."""
    from harness import runner
    from services import assistant_tasks as tasks
    with runner._file_lock(task_path):
        task = runner._read(task_path) or {}
        lease = (task.get("run") or {}).get("runner") or {}
        if lease.get("lease_owner") != instance and not (lapsed_ok and lease.get("lease_until", 0) <= now):
            return False
        step = next((s for s in task.get("steps", []) if s["id"] == ticket[1]), None)
        if step is None or step.get("attempt_id") != ticket[2] or step["status"] != "waiting":
            return False
        deadline = (step.get("job") or {}).get("deadline_at")
        if not isinstance(deadline, (int, float)) or now < deadline:
            return False
        # The loop must not hold `restore="final"` after its step settled: stop it
        # (GK A (12)). Stopped BEFORE the settle, so a reader that sees the step
        # settled `timed_out` always sees the loop stopped.
        tasks._abort_engine(task, step)
        if tasks.settle_engine(task_path, ticket, None, "timed_out") is None:
            return False
    return True


def engine_record(ctx, study, job):
    """(state, record) of the step's own loop: "running", "ended" or "lost".

    The slot is read under the solver-state lock and is the step's only when
    its `started_at` is the step's and, while the study handle is registered,
    its origin is that handle's (C8, #1038). A stranger loop in the slot, an
    empty slot (the process restarted) or a running record whose worker died
    is "lost": its status is never this step's."""
    from services.adequacy.coupling import LOOP_STATUSES
    from services.run_gates import LOOP_FIELDS
    lock = getattr(ctx, "solver_state_lock", None)
    if lock is None:
        return "lost", None
    with lock:
        record = ctx.solver_state.get(study)
        if not isinstance(record, dict) or record.get("started_at") is None \
                or record.get("started_at") != job.get("started_at"):
            return "lost", None
        binding = job_adapters._ACTIVITY_BINDINGS.get(job.get("job_id"))
        if binding is not None and (binding[0] != id(ctx) or not job_adapters._same_origin(record, binding[2],
                                                                                         binding[3])):
            return "lost", None
        status = record.get("status")
        thread = record.get("thread")
        copy = {k: record[k] for k in LOOP_FIELDS if k in record}
    if status in LOOP_STATUSES:
        return "ended", copy
    if status == "running" and (not isinstance(thread, threading.Thread) or thread.is_alive()
                                or thread.ident is None):
        return "running", copy
    return "lost", None  # a running record whose worker is gone, or a status no loop sets


def settle_engine(task_path, ticket, *, instance, ctx):
    """Settle a waiting engine step from its loop's record, once (C7): only
    while the step still waits on this attempt, only by the process holding
    the run's live lease (H1), under the lease's file lock. Returns the state
    read, or None when nothing was written."""
    from harness import runner
    from services import assistant_tasks as tasks
    with runner._file_lock(task_path):
        task = runner._read(task_path)
        if task is None:
            return None
        lease = (task.get("run") or {}).get("runner") or {}
        if lease.get("lease_owner") != instance:
            return None  # a stale process writes nothing
        step = next((s for s in task["steps"] if s["id"] == ticket[1]), None)
        if step is None or step.get("attempt_id") != ticket[2] or step["status"] != "waiting":
            return None
        state, record = engine_record(ctx, step["job"]["study"], step["job"])
        if state == "running":
            return state
        if state != "ended":
            tasks.settle_engine(task_path, ticket, None, "lost")
            return state
        # H3: whether the loop's result is still the project's is decided under the project's
        # mutation lock, and a held setting joins the chain there; busy now, the next poll retries.
        if not ctx.mutation_lock.acquire(timeout=ENGINE_LOCK_SECONDS):
            return "running"
        try:
            from harness import run_loops
            record["applied"] = run_loops.engine_applied(task_path, ctx, step, record)
            tasks.settle_engine(task_path, ticket, record, "ended")
        finally:
            ctx.mutation_lock.release()
        return state


def follow_engine(task_path, ticket, ctx, *, instance) -> threading.Event:
    """Follow a waiting engine step on its own thread until its loop ends,
    then settle it (`settle_engine`). The step's wall-clock `deadline_at`
    settles it `timed_out` (H2, `expire_engine`); a restart does not reset it.
    A cancelled run is settled by the cancel path's abort; this thread then
    finds the step no longer waiting and stops. A follower that dies leaves
    the step to the next tick (`run_loops.recover_engines`)."""
    import contextvars
    settled = threading.Event()

    def run():
        from harness import runner
        try:
            while True:
                task = runner._read(task_path)
                step = next((s for s in (task or {}).get("steps", []) if s["id"] == ticket[1]), None)
                if step is None or step.get("attempt_id") != ticket[2] or step["status"] != "waiting":
                    return
                deadline = (step.get("job") or {}).get("deadline_at")
                deadline = deadline if isinstance(deadline, (int, float)) else time.time() + MAX_ENGINE_FOLLOW_SECONDS
                if time.time() >= deadline:
                    expire_engine(task_path, ticket, instance=instance, now=time.time())
                    return
                state = settle_engine(task_path, ticket, instance=instance, ctx=ctx)
                if state not in (None, "running"):
                    return
                if state is None and ((task.get("run") or {}).get("runner") or {}).get("lease_owner") != instance:
                    return  # taken over: the new holder follows it
                _WAKE.wait(min(ENGINE_POLL_SECONDS, max(0.0, deadline - time.time())))
        except Exception:  # noqa: BLE001 — a lost settlement leaves the step to the next tick's re-follow
            logger.exception("engine step %s could not be settled", ticket[1])
        finally:
            settled.set()

    context = contextvars.copy_context()
    thread = threading.Thread(target=context.run, args=(run,), name=f"engine-{ticket[1][:12]}", daemon=True)
    for key in [k for k, t in _ENGINE_FOLLOWERS.items() if not t.is_alive()]:
        _ENGINE_FOLLOWERS.pop(key, None)
    _ENGINE_FOLLOWERS[tuple(ticket)] = thread
    thread.start()
    return settled
