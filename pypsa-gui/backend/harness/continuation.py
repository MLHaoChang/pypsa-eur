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


def follow_job(ctx, run_token, task_path, ticket, job_id, *, prior_worker=None) -> threading.Event:
    """Follow a plan's job to its end on its own thread (not the session's
    progress monitor, which stops when the chat closes), then settle the
    waiting step from that job's own record. Returns an event set once the
    step is settled. Takes no network lock.

    `prior_worker` is the solver worker before the run call: a job bound to
    it (or to none) has no solve of its own, so its outcome is unknown."""
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
            settle_from_record(ctx, run_token, task_path, ticket, job_id, status)
        except Exception:  # noqa: BLE001 — a lost settlement leaves the step waiting, never re-runs
            logger.exception("plan job %s could not be settled", job_id)
        finally:
            settled.set()

    threading.Thread(target=run, name=f"plan-job-{job_id[3:11]}", daemon=True).start()
    return settled


def settle_from_record(ctx, run_token, task_path, ticket, job_id, status):
    """Settle from the job's own terminal record, never from live state.

    `completed` needs the job's identity-bound native status before AND after
    its run manifest is published, and the manifest's own objective and input
    fingerprint; anything less settles `uncertain`. Failed, aborted and
    unknown settle `failed`."""
    from services import run_evidence

    if status in ("completed", "failed", "aborted") and run_token is not None:
        run_evidence.publish_foreground_run(run_token, ctx)
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
    if not record or record.get("status") != "completed" or record.get("job_id") != job_id \
            or objective is None or not fingerprint:
        return assistant_tasks.settle_job(task_path, ticket, job_id, "completed", "uncertain")
    return assistant_tasks.settle_job(task_path, ticket, job_id, "completed", "completed",
                                      {"run_id": run_token["run_id"], "objective": objective,
                                       "fingerprint": fingerprint})
