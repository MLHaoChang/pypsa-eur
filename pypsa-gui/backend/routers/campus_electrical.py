"""`/api/campus-electrical`: the campus electrical study of a hub project
(plan C6).

A wrapper layer like ``/api/gridspine``. Each handler resolves and
authorizes the project, checks the edit lock on a write, and calls one
function in ``services/campus_electrical_service.py``, the same functions
the copilot's tools call. Drafting and running parse a network and solve
load flows, which takes seconds of CPU, so they run off the event loop.

The asset library the investment step buys from (plan C9) is under
``/{name}/library``: ``GET``, ``PUT`` (validated by the engine's loader; 422
names the entry and field, 413 over 1 MB) and ``POST /library/reset``.

The project's own grid codes (plan C10) are under ``/{name}/grid-codes``,
wrapping ``services/campus_grid_code_service.py``:

* ``GET /grid-codes``: the shipped, published and draft profiles, the
  uploaded documents, and whether extraction is available;
* ``POST /grid-codes/documents`` (multipart ``file``) and
  ``DELETE /grid-codes/documents/{document_id}``;
* ``POST /grid-codes/documents/{document_id}/extract``: the copilot's draft;
* ``POST /grid-codes/drafts``: a blank draft to fill in by hand;
* ``GET``, ``PUT`` and ``DELETE /grid-codes/drafts/{profile_id}``;
* ``POST /grid-codes/drafts/{profile_id}/confirm`` and ``.../publish``;
* ``GET`` and ``DELETE /grid-codes/published/{profile_id}``.

The upload is read under one ``UploadBudget`` of the PDF cap; parsing it and
the extraction (one API call, up to minutes) run off the event loop.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession
from starlette.concurrency import run_in_threadpool

from db.models import Project, User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services import campus_electrical_service as ce
from services import campus_grid_code_service as gc
from services.upload_guard import UploadBudget

router = APIRouter()


class DraftRequest(BaseModel):
    overwrite: bool = False


class CampusText(BaseModel):
    yaml: str = Field(max_length=ce.MAX_CAMPUS_BYTES)


class LibraryText(BaseModel):
    #: No ``max_length``: the service answers 413 over ``MAX_LIBRARY_BYTES``,
    #: where a model constraint would answer 422.
    yaml: str


class RunSettings(BaseModel):
    k: int = Field(ce.DEFAULTS["k"], ge=1, le=50)
    pf: float | None = Field(None, gt=0, le=1)
    profile: str = Field(ce.DEFAULTS["profile"], min_length=1, max_length=64)
    margin: float = Field(ce.DEFAULTS["margin"], ge=0, le=2)
    n_minus_1: bool = True
    #: Buy the electrical assets from the library after sizing (plan C9).
    invest: bool = True


class ExtractRequest(BaseModel):
    #: The draft's id; omitted, one is derived from the document's hash.
    profile_id: str | None = Field(None, min_length=1, max_length=64)
    overwrite: bool = False


class NewDraft(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(None, max_length=200)
    overwrite: bool = False


class ProfileText(BaseModel):
    yaml: str = Field(max_length=gc.MAX_PROFILE_BYTES)


class ConfirmLimit(BaseModel):
    #: ``voltage_bands[i]``, ``q_range_demand``, ``rvc_limit_pct`` or ``campus_voltage``.
    limit: str = Field(min_length=1, max_length=64)


class Publish(BaseModel):
    allow_unconfirmed: bool = False


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
    """Prepare, rank and size the campus at its critical hours, then buy the
    electrical assets from the library unless ``invest`` is false."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(ce.run, _row(proj, db), body.model_dump())


# ── the asset library (plan C9) ────────────────────────────────────────────

@router.get("/{name}/library")
def get_library(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The asset library a study buys from: the project's copy, else the shipped default."""
    return ce.get_library(_row(proj, db))


@router.put("/{name}/library")
def save_library(
    body: LibraryText,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Keep the user's library as the project's copy, validated by the engine's loader."""
    _check_lock(proj, db, user)
    return ce.save_library(_row(proj, db), body.yaml)


@router.post("/{name}/library/reset")
def reset_library(
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Delete the project's copy; the shipped default is used again."""
    _check_lock(proj, db, user)
    return ce.reset_library(_row(proj, db))


# ── the project's grid codes (plan C10) ────────────────────────────────────

@router.get("/{name}/grid-codes")
def list_grid_codes(proj: AuthorizedProject = ProjectAccessDep, db: DBSession = Depends(get_db)):
    """The shipped, published and draft profiles and the uploaded documents."""
    return gc.list_grid_codes(_row(proj, db))


@router.post("/{name}/grid-codes/documents")
async def upload_grid_code_document(
    file: UploadFile = File(...),
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Upload a grid code as a PDF; it is kept in the project by its hash."""
    _check_lock(proj, db, user)
    data = await UploadBudget(gc.MAX_PDF_BYTES).read(file)
    return await run_in_threadpool(gc.upload_document, _row(proj, db), data, file.filename, file.content_type)


@router.delete("/{name}/grid-codes/documents/{document_id}")
def delete_grid_code_document(
    document_id: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    _check_lock(proj, db, user)
    return gc.delete_document(_row(proj, db), document_id)


@router.post("/{name}/grid-codes/documents/{document_id}/extract")
async def extract_grid_code(
    document_id: str,
    body: ExtractRequest,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Draft a profile from the document (one API call); every limit is
    extracted until a person confirms it."""
    _check_lock(proj, db, user)
    return await run_in_threadpool(gc.extract, _row(proj, db), document_id, body.profile_id, body.overwrite)


@router.post("/{name}/grid-codes/drafts")
def new_grid_code_draft(
    body: NewDraft,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """A blank draft, every limit assumed, to fill in by hand."""
    _check_lock(proj, db, user)
    return gc.new_draft(_row(proj, db), body.profile_id, body.title, body.overwrite)


@router.get("/{name}/grid-codes/drafts/{profile_id}")
def get_grid_code_draft(profile_id: str, proj: AuthorizedProject = ProjectAccessDep,
                        db: DBSession = Depends(get_db)):
    return gc.get_draft(_row(proj, db), profile_id)


@router.put("/{name}/grid-codes/drafts/{profile_id}")
def save_grid_code_draft(
    profile_id: str,
    body: ProfileText,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Save the user's edit of a draft, validated by the profile loader."""
    _check_lock(proj, db, user)
    return gc.save_draft(_row(proj, db), profile_id, body.yaml)


@router.delete("/{name}/grid-codes/drafts/{profile_id}")
def delete_grid_code_draft(
    profile_id: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    _check_lock(proj, db, user)
    return gc.delete_draft(_row(proj, db), profile_id)


@router.post("/{name}/grid-codes/drafts/{profile_id}/confirm")
def confirm_grid_code_limit(
    profile_id: str,
    body: ConfirmLimit,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Confirm one extracted limit against its quote: it becomes code."""
    _check_lock(proj, db, user)
    return gc.confirm(_row(proj, db), profile_id, body.limit)


@router.post("/{name}/grid-codes/drafts/{profile_id}/publish")
def publish_grid_code(
    profile_id: str,
    body: Publish,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """Publish a draft for the study; 409 while a limit is unconfirmed unless
    allow_unconfirmed."""
    _check_lock(proj, db, user)
    return gc.publish(_row(proj, db), profile_id, body.allow_unconfirmed)


@router.get("/{name}/grid-codes/published/{profile_id}")
def get_published_grid_code(profile_id: str, proj: AuthorizedProject = ProjectAccessDep,
                            db: DBSession = Depends(get_db)):
    return gc.get_published(_row(proj, db), profile_id)


@router.delete("/{name}/grid-codes/published/{profile_id}")
def delete_published_grid_code(
    profile_id: str,
    proj: AuthorizedProject = ProjectAccessDep,
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    _check_lock(proj, db, user)
    return gc.delete_published(_row(proj, db), profile_id)
