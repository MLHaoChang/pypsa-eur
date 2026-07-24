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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers.projects import _safe_project_dir
from services.solve_queue import solve_queue

router = APIRouter()


class EnqueueRequest(BaseModel):
    project_id: str


@router.post("")
def enqueue_solve(req: EnqueueRequest):
    """
    Queue a project to solve. The dispatcher loads the SAVED version from disk,
    so the project must already exist on disk with a network — the caller (the
    frontend) saves the foreground project before enqueuing it. Returns the
    created job, including its queue position.
    """
    name = req.project_id
    # _safe_project_dir rejects traversal / unsafe names (raises 400/404 itself)
    # and resolves the on-disk dir. A project with no network.nc was never saved.
    project_dir = _safe_project_dir(name)
    if not (project_dir / "network.nc").exists():
        raise HTTPException(
            404,
            f"Project '{name}' has no saved network on disk. Save the project "
            "before queuing it to solve.",
        )
    job = solve_queue.enqueue(name)
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
