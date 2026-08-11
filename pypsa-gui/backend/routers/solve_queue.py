"""
HTTP surface for the sequential multi-project solve queue (Phase A / C1).

Thin layer over `services.solve_queue.solve_queue` — the dispatcher singleton
owns all state and threading. Mounted under `/api/simulation/queue` in main.py.

Endpoints:
    POST   /api/simulation/queue                 enqueue a saved project
    GET    /api/simulation/queue                  list all jobs (FIFO order)
    POST   /api/simulation/queue/{job_id}/abort   abort running / cancel queued
    POST   /api/simulation/queue/clear_finished   drop terminal jobs from listing

AUTHORIZATION. All four routes take `db`/`user` and authorize against the
caller. Three of them did not until P-1, and the queue is a PROCESS-GLOBAL
singleton shared by every org — see the per-route docstrings for what each one
now refuses, and `_may_see` / `_may_abort` for the two different boundaries
they use and why they differ.
"""
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


class EnqueueRequest(BaseModel):
    project_id: str


@router.post("")
def enqueue_solve(
    req: EnqueueRequest,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Queue a project to solve. The dispatcher loads the SAVED version from disk,
    so the project must already exist on disk with a network — the caller (the
    frontend) saves the foreground project before enqueuing it. Returns the job,
    including its queue position and an `already_queued` flag that is true when
    an existing job was returned instead of a new one.

    AUTHORIZATION (Step 0a): the project arrives in the BODY, not the path, so
    this route is invisible to a path-parameter inventory — it was not among
    the 14 the plan counted, and it was the widest remaining hole: it used
    `_safe_project_dir(name)`, which resolves any org's project under the
    shared root, and then handed that path to a background thread that solves
    and SAVES it. Resolution now goes through the caller's org and ACL, and the
    authorized directory travels with the job, because the dispatcher runs with
    no request and no user and cannot authorize anything itself.
    """
    from services import project_registry

    project_registry.require_user(user)
    project = project_registry.resolve_project(db, user, req.project_id)
    project_dir = project_registry.project_dir(project)
    if not (project_dir / "network.nc").exists():
        raise HTTPException(
            404,
            f"Project '{project.name}' has no saved network on disk. Save the "
            "project before queuing it to solve.",
        )
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


# ── Authorization helpers (P-1) ─────────────────────────────────────────────
# The queue singleton is process-global and its jobs are ordered against ONE
# shared solver, so every caller in every org reads the same list. `project_key`
# is `org_uuid:project_uuid` (`services/solve_queue.py:374`), which is the only
# tenancy information a job carries — the dispatcher runs with no request and no
# user, so nothing downstream can authorize anything.
#
# Fields that name the work rather than measure it. `error` is included because
# a failure message routinely quotes a project name, a path or a solver detail.
_REDACTED = ("project_id", "project_key", "error")


def _org_prefix(db: DBSession, user: User) -> str | None:
    """
    The `project_key` prefix identifying the caller's org, or None if they have
    no membership. ONE membership lookup for the whole listing — the alternative
    (an ACL query per job) turns a polled endpoint into an N-query endpoint.
    """
    from services.tenancy_service import get_user_membership

    membership = get_user_membership(db, user.id)
    return None if membership is None else f"{membership.org_id}:"


def _project_uuid(job: dict, prefix: str | None) -> uuid.UUID | None:
    """
    The project a job names, but only when the key belongs to the caller's org.

    Returns None for another org's job, an unkeyed one, and a key whose uuid
    half does not parse — all three are "no project you could be entitled to",
    which is the only distinction the callers below need.
    """
    key = job.get("project_key")
    if key is None or prefix is None or not key.startswith(prefix):
        return None
    try:
        return uuid.UUID(key[len(prefix):])
    except ValueError:
        return None


def _may_see(job: dict, prefix: str | None, allowed: set[uuid.UUID]) -> bool:
    """
    Whether the caller may see WHOSE job this is — the PROJECT ACL, not the org.

    `allowed` is resolved once per request by `project_acl.accessible_project_ids`,
    so this stays a set membership test however long the queue is. Org
    membership alone would be the wrong boundary: `GET /api/projects/` already
    filters a caller's own project list through `can_access_project`, so an
    org-wide answer here would hand back names that endpoint refuses them.

    A job with no `project_key` is a legacy or hand-made artefact —
    `enqueue_solve` has stamped a key on every job since Step 0a. Under auth it
    cannot be attributed to an org, therefore it cannot be authorized, so it
    fails closed. In local mode there is exactly one tenant (`local_mode`, one
    seeded org + user), so the only possible owner IS the caller.

    A job whose project row has been DELETED is redacted for the same reason:
    there is no ACL left to consult. `_may_abort` deliberately answers
    differently — see there.
    """
    if job.get("project_key") is None:
        return local_mode.is_local_mode()
    project_id = _project_uuid(job, prefix)
    return project_id is not None and project_id in allowed


def _redact(job: dict) -> dict:
    """Blank the identifying fields, keep the shared-queue facts."""
    return {**job, **{field: None for field in _REDACTED}}


def _may_abort(db: DBSession, user: User, job: dict) -> bool:
    """
    Whether the caller may STOP this job.

    Same predicate as `_may_see` — `can_access_project` — reached directly
    rather than through the batch, because abort concerns exactly one job and
    the single-project path is the authoritative one `enqueue_solve` uses.
    `tests/test_project_acl.py` pins that the batch agrees with it.

    ONE deliberate difference. A job whose project row has since been DELETED
    is invisible in the listing (no ACL to consult, so identity fails closed)
    but stays abortable by its own org. `_delete_project_db` removes the row and
    the directory without touching the queue, so a running solve outlives its
    project; refusing here would strand it with no way to stop it and no way to
    free the shared solver. The listing still shows the job's id and status, so
    an operator can find it — they just cannot learn whose it was.
    """
    key = job.get("project_key")
    if key is None:
        return local_mode.is_local_mode()
    prefix = _org_prefix(db, user)
    project_id = _project_uuid(job, prefix)
    if project_id is None:
        return False

    from db.models import Project
    from services import project_acl

    project = db.get(Project, project_id)
    if project is None:
        return True  # orphaned by a delete; see above
    if f"{project.org_id}:" != prefix:
        return False  # the key's org half lied about the row's real owner
    return project_acl.can_access_project(db, user, project)


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


@router.get("")
def list_queue(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    All jobs in FIFO order. `current` is the id of the running job, if any.

    AUTHORIZATION (P-1): this REDACTS, it does not filter. `position`
    (`SolveJob.to_public`) is the 1-based place in a GLOBALLY sequential queue,
    because one solver serves every org — a caller's wait genuinely depends on
    jobs they do not own. Dropping other orgs' rows would leave them at
    "position 4" with one job visible and no way to reconcile the number, so
    queue depth (the thing they legitimately need) becomes unreadable.

    So every job comes back with `id`, `status`, `position` and its timings
    intact, and the fields that say WHOSE work it is are nulled for jobs the
    caller cannot access. `current` is the true running job id only when the
    caller may see it — otherwise null, since the id alone was enough to abort
    it before this change.

    "Cannot access" is the PROJECT ACL, resolved for the whole listing in one
    batch (`accessible_project_ids`). This endpoint is polled every 1.5s while
    a job is active, so a `can_access_project` call per job would be an N+1 on
    a hot path — and answering on org membership instead would leak names that
    `GET /api/projects/` already hides from the same caller.
    """
    from services import project_acl, project_registry

    project_registry.require_user(user)
    prefix = _org_prefix(db, user)
    jobs = solve_queue.list_jobs()
    allowed = project_acl.accessible_project_ids(
        db, user, (_project_uuid(job, prefix) for job in jobs)
    )
    seen = [_may_see(job, prefix, allowed) for job in jobs]
    current = next(
        (job["id"] for job, ok in zip(jobs, seen) if ok and job["status"] == "running"),
        None,
    )
    return {
        "jobs": [job if ok else _redact(job) for job, ok in zip(jobs, seen)],
        "current": current,
    }


@router.post("/{job_id}/abort")
def abort_job(
    job_id: int,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Abort a running job (signals its stop_event) or cancel a queued one.

    AUTHORIZATION (P-1): 404, never 403, when the caller may not touch this
    job, with the message the genuine not-found case uses BYTE FOR BYTE. Job
    ids are small sequential integers, so a 403 would confirm that id 7 exists
    and hand back the enumeration the redacted listing just took away — the
    same reasoning `project_registry.find_project` documents for projects.

    The job is fetched and authorized BEFORE `abort()`, which is destructive:
    it flips a queued job to cancelled or sets a running job's stop_event.
    """
    from services import project_registry

    project_registry.require_user(user)
    not_found = HTTPException(404, f"No solve job with id {job_id}.")
    job = solve_queue.get_job(job_id)
    if job is None or not _may_abort(db, user, job):
        raise not_found
    res = solve_queue.abort(job_id)
    if res is None:
        # Cleared by a concurrent clear_finished between the two lookups.
        raise not_found
    return res


@router.post("/clear_finished")
def clear_finished(
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Remove completed/failed/aborted jobs from the listing. Super-admin only.

    AUTHORIZATION (P-1): the queue is one process-global list, so this drops
    EVERY org's terminal jobs — an operation that crosses org boundaries by
    construction. `_is_org_admin` (`services/project_acl.py:37-39`) is the wrong
    gate for it: an org admin's authority stops at their own org. The
    instance-wide tier is `User.is_super_admin`, which is what `routers/admin.py`
    already treats as global.

    The service call is deliberately left unfiltered rather than given a
    per-caller predicate: this gate is what makes "clear everything" legitimate,
    and a predicate here would be dead code that reads like a second, weaker
    authorization path.
    """
    from services import project_registry

    project_registry.require_user(user)
    if not user.is_super_admin:
        raise HTTPException(
            403,
            "Clearing finished jobs empties the queue for every organization, "
            "so it is restricted to super-admins.",
        )
    return {"removed": solve_queue.clear_finished()}
