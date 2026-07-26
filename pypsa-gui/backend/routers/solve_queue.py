"""
HTTP surface for the sequential multi-project solve queue (Phase A / C1).

Thin layer over `services.solve_queue.solve_queue` — the dispatcher singleton
owns all state and threading. Mounted under `/api/simulation/queue` in main.py.

Endpoints:
    POST   /api/simulation/queue                 enqueue a saved project
    GET    /api/simulation/queue                  list all jobs (FIFO order)
    POST   /api/simulation/queue/{job_id}/abort   abort running / cancel queued
    POST   /api/simulation/queue/clear_finished   drop terminal jobs from listing
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

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


@router.get("")
def list_queue():
    """All jobs in FIFO order. `current` is the id of the running job, if any."""
    jobs = solve_queue.list_jobs()
    current = next((j["id"] for j in jobs if j["status"] == "running"), None)
    return {"jobs": jobs, "current": current}


@router.post("/{job_id}/abort")
def abort_job(job_id: int):
    """Abort a running job (signals its stop_event) or cancel a queued one."""
    res = solve_queue.abort(job_id)
    if res is None:
        raise HTTPException(404, f"No solve job with id {job_id}.")
    return res


@router.post("/clear_finished")
def clear_finished():
    """Remove completed/failed/aborted jobs from the listing."""
    return {"removed": solve_queue.clear_finished()}
