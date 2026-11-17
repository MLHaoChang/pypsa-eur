"""`/api/grid-compliance`: the compliance workflow of a project (GC-09).

The TRGC deliverable tracker, each deliverable's data list and data freeze,
its TSO comment / revision log, and its report pack. Each handler resolves
and authorizes the project, checks the edit lock on a write, and calls one
function of ``services.grid_compliance``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session as DBSession
from starlette.concurrency import run_in_threadpool

from db.models import Project, User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services.grid_compliance import pack, revision_log, tracker
from services.grid_compliance.store import GridComplianceError

router = APIRouter()


class StatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(min_length=1, max_length=32)


class DataBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[dict] = Field(max_length=200)


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    clause: str = ""
    comment: str
    response: str = ""
    revision: str = ""
    status: Literal["open", "responded", "closed"] = "open"


class CommentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str | None = None
    clause: str | None = None
    comment: str | None = None
    response: str | None = None
    revision: str | None = None
    status: Literal["open", "responded", "closed"] | None = None
    pack_version: int | None = Field(default=None, ge=1)


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


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, GridComplianceError):
        detail: dict = {"error_kind": exc.kind, "message": exc.message}
        if exc.blockers:
            detail["blockers"] = exc.blockers
        return HTTPException(exc.status, detail)
    return HTTPException(422, {"error_kind": "gc_invalid",
                               "message": str(exc).splitlines()[0]})


def _call(fn, *args):
    try:
        return fn(*args)
    except (GridComplianceError, ValidationError) as exc:
        raise _http(exc) from exc


@router.get("/{name}")
def get_state(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """Every TRGC deliverable with status, blockers, data list, freeze, comments."""
    return _call(tracker.state, _row(proj, db))


@router.put("/{name}/deliverables/{code}/status")
def set_status(
    code: str, body: StatusBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Move a deliverable along the status ladder, guarded by its prerequisites."""
    _check_lock(proj, db, user)
    return _call(tracker.set_status, _row(proj, db), code, body.status)


@router.put("/{name}/deliverables/{code}/data")
def set_data(
    code: str, body: DataBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Replace a deliverable's data list."""
    _check_lock(proj, db, user)
    return _call(tracker.set_data, _row(proj, db), code, body.items)


@router.post("/{name}/deliverables/{code}/freeze")
def freeze(
    code: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Freeze the campus input and stamp its model-set id on the deliverable."""
    _check_lock(proj, db, user)
    return _call(tracker.freeze, _row(proj, db), code)


@router.post("/{name}/deliverables/{code}/comments", status_code=201)
def add_comment(
    code: str, body: CommentBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Log a TSO comment on a deliverable."""
    _check_lock(proj, db, user)
    return _call(revision_log.add, _row(proj, db), code, body.model_dump())


@router.patch("/{name}/deliverables/{code}/comments/{comment_id}")
def update_comment(
    code: str, comment_id: str, body: CommentPatch,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Answer or edit a TSO comment."""
    _check_lock(proj, db, user)
    return _call(revision_log.update, _row(proj, db), code, comment_id,
                 body.model_dump(exclude_none=True))


@router.post("/{name}/deliverables/{code}/pack")
async def build_pack(
    code: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """The deliverable's report pack as a ZIP, recorded as its next pack version."""
    _check_lock(proj, db, user)
    project = _row(proj, db)
    try:
        data, manifest = await run_in_threadpool(pack.build, project, code)
    except GridComplianceError as exc:
        raise _http(exc) from exc
    filename = f"TRGC-{code}-pack-v{manifest['pack_version']}.zip"
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})
