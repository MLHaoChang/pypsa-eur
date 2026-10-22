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


def queue_jobs(result) -> list[dict]:
    """The queue jobs a sweep or enqueue result started, as {job_id, project_id}.

    A sweep case reused from an earlier solve started no job. The child's
    UUID is the second half of the job's `project_key` (`org:uuid`)."""
    if not isinstance(result, dict):
        return []
    if "cases" in result:
        found = [case.get("job") for case in result["cases"] if isinstance(case, dict)]
    else:
        found = [result]
    return [{"job_id": str(job["id"]), "project_id": str(job.get("project_key", "")).split(":", 1)[-1]}
            for job in found if isinstance(job, dict) and job.get("id")]


def _queue_job_status(job) -> str:
    """The job's own status from the solve queue, as the acting user sees it."""
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools
    try:
        with chat_tools._acting() as (db, user):
            return _visible_job_or_404(db, user, job["job_id"]).get("status") or "unknown"
    except Exception:  # noqa: BLE001 — an unreadable job proves nothing
        return "unknown"


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
    # The manifest stores the job's objective unrounded, the same float the
    # solve saved on the network: compared exactly.
    if (isinstance(objective, bool) or not isinstance(objective, (int, float))
            or wt.solve_freshness(saved)["inputs_changed_since_solve"] is not False
            or getattr(saved.network, "objective", None) != objective):
        return None
    return {"job_id": job["job_id"], "run_id": run_id, "project_id": job["project_id"], "objective": objective}


class QueueBusy(Exception):
    """The network lock was busy; the evidence read is retried, not judged."""


def queue_outcome(jobs) -> tuple[str, str, dict]:
    """(step status, native status, result) for a queued step's jobs.

    Completed only when every job completed with intact evidence. Failed only
    when no job completed (each failed or was aborted): a retry then repeats
    nothing that landed. Anything else, a mix included, is uncertain."""
    runs, natives = [], []
    for job in jobs:
        native = _queue_job_status(job)
        natives.append(native)
        if native == "completed":
            runs.append(_queue_run(job))
    if natives and all(n == "completed" for n in natives) and all(runs):
        return "completed", "completed", {"runs": runs}
    if natives and all(n in ("failed", "aborted") for n in natives):
        return "failed", "aborted" if "aborted" in natives else "failed", {}
    return "uncertain", "completed" if "completed" in natives else "unknown", {}


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
