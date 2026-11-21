"""The Excel input workbook routes that read no open network (IMPORTS-1, IMPORT-FLOW-1).

Mounted at /api/workbook, outside /api/io/: the undo, foreign-lock, harness-run,
solver and live-study gates guard the OPEN network, and these routes never touch
it. The template reads no project; a preview parses an upload and writes
nothing; an import creates a new project the caller owns and leaves it closed.
A project's own workbook is `GET /api/projects/{name}/workbook`.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from services import change_log_service, excel_workbook, project_registry
from services.http_filenames import content_disposition
from services.upload_guard import read_capped

router = APIRouter()


@router.get("/template")
def download_template():
    return Response(
        excel_workbook.template_bytes(),
        media_type=excel_workbook.MIME,
        headers={
            "Content-Disposition": content_disposition(
                "PyPSA Studio input workbook.xlsx"
            )
        },
    )


@router.post("/preview")
async def preview_workbook(
    file: UploadFile = File(...), user: User | None = Depends(optional_user)
):
    """What an import of this file would create, and what it could not read; nothing is written."""
    project_registry.require_user(user)
    data = await read_capped(file)
    prep = excel_workbook.prepare(data, file.filename, new_project=True)
    return {
        "sha256": prep.sha256,
        "suggested_name": prep.suggested_name,
        **excel_workbook.payload(prep),
    }


@router.post("/import")
async def import_workbook(
    file: UploadFile = File(...),
    expected_sha256: str = Form(...),
    name: str | None = Form(None),
    db: DBSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    """
    Create a new project from the previewed workbook (IMPORT-FLOW-1: never an overwrite).

    The upload must be the bytes the preview hashed (409 ``import_file_changed``),
    and every check runs again: a preview is never trusted. The project is not
    opened; the caller activates it.
    """
    user = project_registry.require_user(user)
    data = await read_capped(file)
    if hashlib.sha256(data).hexdigest() != expected_sha256.strip().lower():
        raise HTTPException(
            409,
            {
                "error_kind": "import_file_changed",
                "message": "The file is not the one that was previewed: preview it again before importing.",
            },
        )
    prep = excel_workbook.prepare(data, file.filename, new_project=True)
    project = excel_workbook.create_project(
        db, user, (name or "").strip() or prep.suggested_name, prep
    )
    from routers.io import _rederived_note

    n = prep.network
    change_log_service.log(
        "import",
        "Project",
        project.name,
        f"Imported Excel workbook '{prep.filename}' as '{project.name}' "
        f"({len(n.buses)} buses, {len(n.snapshots)} snapshots)"
        + _rederived_note(prep.rederived),
        org_id=str(project.org_id),
    )
    return {
        "project_id": str(project.id),
        "name": project.name,
        **excel_workbook.payload(prep),
    }
