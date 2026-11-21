"""Model files (PSS/E, PowerFactory DGS, PLEXOS) into a new project (project-io 04, Slice A).

Mounted at /api/model-import, outside /api/io/: the undo, foreign-lock, harness-run,
solver and live-study gates guard the OPEN network, and these routes never touch it.
The format list reads the importer registry; a preview reads the uploads and writes
nothing; an import creates a new project the caller owns and leaves it closed
(IMPORT-FLOW-1: never an overwrite). The rules are in `services.model_import_service`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from services import change_log_service, project_registry
from services import model_import_service as svc

router = APIRouter()


@router.get("/formats")
def list_formats(user: User | None = Depends(optional_user)):
    """The model formats this build imports, from the importer registry."""
    project_registry.require_user(user)
    return {"formats": svc.formats()}


@router.post("/preview")
async def preview_model(
    files: list[UploadFile] = File(...),
    options: str | None = Form(None),
    user: User | None = Depends(optional_user),
):
    """What an import of these files would create, and what it could not carry; nothing is kept."""
    project_registry.require_user(user)
    st = None
    try:
        st = await svc.stage(files, options)
        await run_in_threadpool(svc.read, st)
        return svc.payload(st)
    finally:
        svc.discard(st)


@router.post("/import", status_code=201)
async def import_model(
    files: list[UploadFile] = File(...),
    expected_sha256: str = Form(...),
    name: str | None = Form(None),
    options: str | None = Form(None),
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Create a new project from the previewed files.

    The files and options must be the ones the preview hashed (409
    ``import_file_changed``), and every check runs again: a preview is never
    trusted. The project is not opened; the caller opens it.
    """
    user = project_registry.require_user(user)
    # Creating needs a workspace the caller may create in: refuse before reading anything.
    project_registry._org_id_for(db, user)
    st = None
    try:
        st = await svc.stage(files, options)
        if st.sha256 != expected_sha256.strip().lower():
            raise HTTPException(
                409,
                {
                    "error_kind": "import_file_changed",
                    "message": "The files or options are not the ones that were previewed: "
                    "preview them again before importing.",
                },
            )
        await run_in_threadpool(svc.read, st)
        body = svc.payload(st)
        project = svc.create_project(db, user, (name or "").strip() or body["suggested_name"], st)
    finally:
        svc.discard(st)
    change_log_service.log(
        "import",
        "Project",
        project.name,
        f"Imported {body['format_label']} '{', '.join(body['files'])}' as '{project.name}' "
        f"({body['bus_count']} buses, {body['snapshot_count']} snapshots)",
        org_id=str(project.org_id),
    )
    return {"project_id": str(project.id), "name": project.name, **body}
