"""`/api/memory/dream`: start and read the dream job (assistant-memory issue 04).

Not a project route. The job reads only the caller's own chats and runs and
writes `proposed` items, which the Proposals tab confirms one by one
(COPILOT-1). One queued or running job per user, guaranteed by the table's
partial unique index (GK B D4); GET returns only the caller's own latest job
(D8). A separate module so the Memory page's item routes stay as they are.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import require_user
from harness import dream

router = APIRouter()


@router.get("/dream")
def latest_dream_job(
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """The caller's latest dream job, or `{"job": null}`."""
    return {"job": dream.view(dream.latest(db, user))}


@router.post("/dream", status_code=201)
def start_dream_job(
    db: DBSession = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Queue "Review my recent work". 409 `dream_job_live` when one is queued
    or running; 403 `memory_no_org` without an org membership."""
    try:
        job = dream.start(db, user, "manual")
    except dream.DreamError as exc:
        raise HTTPException(exc.status, {"error_kind": exc.kind, "message": str(exc)})
    dream.wake_default()
    return {"job": dream.view(job)}
