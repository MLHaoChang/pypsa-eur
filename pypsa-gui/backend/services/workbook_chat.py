"""Chat's way to the Excel input workbook (excel-input-workbook issue 06).

Three tools over the four `/api/workbook` routes, IMPORT-FLOW-1 throughout:

* `get_input_workbook` offers the blank template (`GET /api/workbook/template`)
  or a project's workbook as last saved (`GET /api/projects/{name}/workbook`)
  as a download the browser fetches itself. No bytes reach the model, nothing
  is written, and the project is checked readable before the offer is made.
* `preview_input_workbook` runs the preview route on a chat upload: what an
  import would create and what it could not read. It writes nothing.
* `import_input_workbook` runs the import route on the same upload as a NEW
  project; it is never an overwrite and leaves the project closed. Its
  confirmation carries the preview (`harness/workbook_card.py`), and the route
  refuses bytes that are not the ones that preview hashed.
"""

from __future__ import annotations

import hashlib
import io
from urllib.parse import quote

from fastapi import HTTPException

TEMPLATE_URL = "/api/workbook/template"
TEMPLATE_FILENAME = "PyPSA Studio input workbook.xlsx"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Items of each report list the model sees; the card shows more.
MODEL_LIST_CAP = 8
#: Items of each report list the confirmation card carries.
CARD_LIST_CAP = 50
_REPORT_LISTS = ("unmapped", "assumed", "warnings", "suggestions")


def _refuse(kind: str, message: str, status: int = 409):
    """A refusal with a literal kind (the error-kind manifest test reads it)."""
    raise HTTPException(
        status_code=status, detail={"error_kind": kind, "message": message}
    )


def _acting_user():
    """The caller, as the routes' `optional_user` would give it, or a 401."""
    from services import chat_tools

    return chat_tools._acting()


def get_input_workbook(project: str | None = None) -> dict:
    """The template, or `project`'s workbook as last saved, as a download offer."""
    name = (project or "").strip()
    if not name:
        return {
            "workbook_download": "template",
            "filename": TEMPLATE_FILENAME,
            "media_type": MIME,
            "download_url": TEMPLATE_URL,
            "note": "The blank input workbook; fill it in Excel, then attach it here to preview an import.",
        }
    if len(name) > 256:
        _refuse(
            "invalid_project_name",
            "Use an exact project name of at most 256 characters.",
            400,
        )
    from services import project_registry

    with _acting_user() as (db, user):
        found = project_registry.resolve_project(db, user, name)
        resolved = found.name
    return {
        "workbook_download": "project",
        "project": resolved,
        "filename": f"{resolved}.xlsx",
        "media_type": MIME,
        "download_url": f"/api/projects/{quote(resolved, safe='')}/workbook",
        "note": "The project as last saved; unsaved edits are not in it.",
    }


def _upload(file_id: str):
    """`(meta, bytes)` of a chat upload in the open project, its hash checked."""
    from services.assistant_tools import _file

    if not isinstance(file_id, str) or not file_id.strip():
        _refuse(
            "invalid_file_id",
            "Give the file_id of the workbook attached to this chat.",
            400,
        )
    _, _, _, meta, path = _file(file_id.strip())
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != meta.sha256:
        _refuse(
            "import_file_changed",
            "Uploaded bytes no longer match the content hash; upload the file again.",
        )
    return meta, data


def _upload_file(meta, data: bytes):
    from fastapi import UploadFile

    return UploadFile(filename=meta.filename, file=io.BytesIO(data))


def _capped(report: dict, cap: int) -> dict:
    """The report with each list cut to `cap` items and its full length kept."""
    out = dict(report)
    for key in _REPORT_LISTS:
        items = list(out.get(key) or [])
        out[key] = items[:cap]
        if len(items) > cap:
            out[f"{key}_total"] = len(items)
    return out


def preview(file_id: str, *, cap: int = CARD_LIST_CAP) -> dict:
    """The preview route's answer about the upload `file_id`; nothing is written."""
    from routers.workbook import preview_workbook
    from services import chat_tools

    meta, data = _upload(file_id)
    with _acting_user() as (_db, user):
        out = chat_tools._sync(preview_workbook(_upload_file(meta, data), user=user))
    return {
        "file_id": meta.file_id,
        "filename": meta.filename,
        **out,
        "report": _capped(out.get("report") or {}, cap),
    }


def preview_input_workbook(file_id: str) -> dict:
    out = preview(file_id, cap=MODEL_LIST_CAP)
    return {
        **out,
        "workbook_preview": True,
        "next_tool": "import_input_workbook",
        "note": "Nothing was written. Importing creates a new project from this file (never an overwrite) "
        "after the user confirms the preview card; pass this sha256 as expected_sha256.",
    }


def import_input_workbook(
    file_id: str, expected_sha256: str, name: str | None = None
) -> dict:
    from routers.workbook import import_workbook
    from services import chat_tools

    if not isinstance(expected_sha256, str) or not expected_sha256.strip():
        _refuse(
            "invalid_expected_sha256",
            "Preview the workbook first and pass its sha256.",
            400,
        )
    meta, data = _upload(file_id)
    with _acting_user() as (db, user):
        out = chat_tools._sync(
            import_workbook(
                _upload_file(meta, data),
                expected_sha256=expected_sha256,
                name=name,
                db=db,
                user=user,
            )
        )
    return {
        **out,
        "report": _capped(out.get("report") or {}, MODEL_LIST_CAP),
        "workbook_imported": True,
        "note": "A new project; nothing was overwritten and the open project is unchanged. "
        "It is closed: activate_project opens it.",
    }
