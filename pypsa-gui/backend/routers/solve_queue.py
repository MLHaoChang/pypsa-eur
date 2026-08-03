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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

import local_mode
from db.models import User
from db.session import get_db
from deps import optional_user
from services.solve_queue import solve_queue

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
    frontend) saves the foreground project before enqueuing it. Returns the
    created job, including its queue position.

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
    job = solve_queue.enqueue(
        project.name,
        project_key=project_registry.registry_key(project),
        storage_dir=str(project_dir),
    )
    return solve_queue.get_job(job.id)


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


def _may_see(job: dict, prefix: str | None) -> bool:
    """
    Whether the caller may see WHOSE job this is.

    A job with no `project_key` is a legacy or hand-made artefact —
    `enqueue_solve` has stamped a key on every job since Step 0a. Under auth it
    cannot be attributed to an org, therefore it cannot be authorized, so it
    fails closed. In local mode there is exactly one tenant (`local_mode`, one
    seeded org + user), so the only possible owner IS the caller.
    """
    key = job.get("project_key")
    if key is None:
        return local_mode.is_local_mode()
    return prefix is not None and key.startswith(prefix)


def _redact(job: dict) -> dict:
    """Blank the identifying fields, keep the shared-queue facts."""
    return {**job, **{field: None for field in _REDACTED}}


def _may_abort(db: DBSession, user: User, job: dict) -> bool:
    """
    Whether the caller may STOP this job — the full project ACL, not just the
    org, because aborting destroys work in progress.

    Stricter than `_may_see` on purpose. The listing's boundary is the org: it
    is polled, so it must cost one query, and it hands back no more than "your
    org has N jobs queued". Abort touches exactly one job, so it can afford the
    project lookup that `enqueue_solve` already performs, and least privilege
    says a caller who may not open a project may not kill its solve either.

    A job whose project row has since been DELETED stays abortable by its own
    org: the resource it guards is gone, the queue slot is not, and refusing
    would strand a running solve with no way to stop it.
    """
    key = job.get("project_key")
    if key is None:
        return local_mode.is_local_mode()
    org_part, _, uuid_part = key.partition(":")
    prefix = _org_prefix(db, user)
    if prefix is None or f"{org_part}:" != prefix:
        return False

    from services import project_acl, project_registry

    project = project_registry.find_project(db, user, uuid_part)
    if project is None:
        return True
    return project_acl.can_access_project(db, user, project)


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
    """
    from services import project_registry

    project_registry.require_user(user)
    prefix = _org_prefix(db, user)
    jobs = solve_queue.list_jobs()
    seen = [_may_see(job, prefix) for job in jobs]
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
