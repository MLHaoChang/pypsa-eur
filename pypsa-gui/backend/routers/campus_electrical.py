"""`/api/campus-electrical`: the campus electrical study of a hub project
(plan C6).

A wrapper layer like ``/api/gridspine``. Each handler resolves and
authorizes the project, checks the edit lock on a write, and calls one
function in ``services/campus_electrical_service.py``, the same functions
the copilot's tools call. Drafting and running parse a network and solve
load flows, which takes seconds of CPU, so they run off the event loop.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession
from starlette.concurrency import run_in_threadpool

from db.models import Project, User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import campus_electrical_service as ce

router = APIRouter()


class DraftRequest(BaseModel):
    overwrite: bool = False


class CampusText(BaseModel):
    yaml: str = Field(max_length=ce.MAX_CAMPUS_BYTES)


class RunSettings(BaseModel):
    k: int = Field(ce.DEFAULTS["k"], ge=1, le=50)
    pf: float | None = Field(None, gt=0, le=1)
    profile: str = Field(ce.DEFAULTS["profile"], min_length=1, max_length=64)
    margin: float = Field(ce.DEFAULTS["margin"], ge=0, le=2)
    n_minus_1: bool = True


def _row(proj: AuthorizedProject, db: DBSession) -> Project:
    project = db.get(Project, uuid.UUID(proj.uuid))
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{proj.name}' not found")
    return project


def _check_lock(proj: AuthorizedProject, db: DBSession, user: User | None) -> None:
    from routers.projects import _check_project_lock

    try:
        target = SimpleNamespace(id=uuid.UUID(proj.uuid), name=proj.name)
    except (TypeError, ValueError):
        return
    _check_project_lock(db, target, user)


@router.get("/{name}")
def get_state(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The campus file, the grid-code profiles, the last settings and results."""
    return ce.get_state(_row(proj, db))


@router.post("/{name}/draft")
async def draft(
    body: DraftRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Draft the campus file from the project's solved network."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(ce.draft, _row(proj, db), body.overwrite)


@router.put("/{name}/campus")
def save_campus(
    body: CampusText,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Save the user's campus file, validated by building it."""
    _check_lock(proj, db, user)
    return ce.save_campus(_row(proj, db), body.yaml)


@router.post("/{name}/run")
async def run(
    body: RunSettings,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Prepare, rank and size the campus at its critical hours."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(ce.run, _row(proj, db), body.model_dump())
