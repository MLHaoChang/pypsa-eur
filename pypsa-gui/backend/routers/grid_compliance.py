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

from db.models import Project, User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services.grid_compliance import pack, revision_log, tracker
from services.grid_compliance.store import GridComplianceError
from services.http_filenames import content_disposition

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


def _project(proj: AuthorizedProject, db: DBSession) -> Project:
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
    errors = exc.errors() if isinstance(exc, ValidationError) else []
    if not errors:
        return HTTPException(
            422, {"error_kind": "gc_invalid", "message": str(exc).splitlines()[0]}
        )
    first = errors[0]
    field = ".".join(str(p) for p in first.get("loc", ())) or "value"
    return HTTPException(
        422,
        {
            "error_kind": "gc_invalid",
            "field": field,
            "message": f"{field}: {first.get('msg', 'is not valid')}",
        },
    )


def _call(fn, *args):
    try:
        return fn(*args)
    except (GridComplianceError, ValidationError) as exc:
        raise _http(exc) from exc


@router.get("/{name}")
def get_state(
    proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)
):
    """Every TRGC deliverable with status, blockers, data list, freeze, comments."""
    return _call(tracker.state, _project(proj, db))


@router.put("/{name}/deliverables/{code}/status")
def set_status(
    code: str,
    body: StatusBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Move a deliverable along the status ladder, guarded by its prerequisites."""
    _check_lock(proj, db, user)
    return _call(tracker.set_status, _project(proj, db), code, body.status)


@router.put("/{name}/deliverables/{code}/data")
def set_data(
    code: str,
    body: DataBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Replace a deliverable's data list."""
    _check_lock(proj, db, user)
    return _call(tracker.set_data, _project(proj, db), code, body.items)


@router.post("/{name}/deliverables/{code}/freeze")
def freeze(
    code: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Freeze the campus input and stamp its model-set id on the deliverable."""
    _check_lock(proj, db, user)
    return _call(tracker.freeze, _project(proj, db), code)


@router.post("/{name}/deliverables/{code}/comments", status_code=201)
def add_comment(
    code: str,
    body: CommentBody,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Log a TSO comment on a deliverable."""
    _check_lock(proj, db, user)
    return _call(revision_log.add, _project(proj, db), code, body.model_dump())


@router.patch("/{name}/deliverables/{code}/comments/{comment_id}")
def update_comment(
    code: str,
    comment_id: str,
    body: CommentPatch,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Answer or edit a TSO comment."""
    _check_lock(proj, db, user)
    return _call(
        revision_log.update,
        _project(proj, db),
        code,
        comment_id,
        body.model_dump(exclude_none=True),
    )


@router.post("/{name}/deliverables/{code}/pack")
def build_pack(
    code: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """The deliverable's report pack as a ZIP, recorded as its next pack version.
    A sync handler: FastAPI runs it in its threadpool, DB calls included."""
    _check_lock(proj, db, user)
    data, manifest = _call(pack.build, _project(proj, db), code)
    filename = f"TRGC-{code}-pack-v{manifest['pack_version']}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(filename)},
    )
