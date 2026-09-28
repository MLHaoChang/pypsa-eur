"""
Study-report routes (WP1) — `/api/projects/{name}/reports…`.

Mounted under `/api/projects` BEFORE the `/{name}` catch-all, with the same
`ProjectAccessDep` authorization as the FMEA worksheet: the handler never
derives a path from client input. Every path is `AuthorizedProject.directory`
plus an id the store validated against an anchored regex.

  GET    /{name}/reports                                   list (ReportMeta, newest first)
  GET    /{name}/reports/{report_id}[?version=N]           the document (latest by default)
  GET    /{name}/reports/{report_id}/versions/{v}          one version
  GET    /{name}/reports/{report_id}/figures/{figure_id}   PNG, inline
  DELETE /{name}/reports/{report_id}                       edit-lock checked

POST (generate) arrives with WP3, export with WP6.

Tenancy: another org's project is 404 from `ProjectAccessDep`, never 403.
The store's refusals map to `invalid_report_id` (400), `report_not_found`
(404) and `figure_not_found` (404); a malformed figure id is a 404 too, since
an id outside the slug charset cannot name a figure that exists.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession
from starlette.responses import FileResponse

from db.models import User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from services.http_filenames import content_disposition
from services.reports import store

logger = logging.getLogger(__name__)
router = APIRouter()


def _lock_target(project: AuthorizedProject) -> SimpleNamespace | None:
    """
    Adapt an `AuthorizedProject` into the shape `_check_project_lock` needs —
    the same local adapter `routers/adequacy_worksheet.py` keeps, for the same
    reason (a sibling router should not import a private helper from another).
    None when the uuid is not a real one, i.e. a unit test calling the handler
    directly; not a bypass, since an HTTP caller cannot choose this value.
    """
    try:
        return SimpleNamespace(id=uuid.UUID(project.uuid), name=project.name)
    except (TypeError, ValueError):
        return None


def _http(exc: store.ReportStoreError) -> HTTPException:
    """One place that maps the store's refusals to the wire."""
    if isinstance(exc, store.InvalidReportId):
        return HTTPException(400, {
            "error_kind": "invalid_report_id",
            "message": "Report ids are 16 lowercase hex characters.",
        })
    if isinstance(exc, (store.FigureNotFound, store.InvalidFigureId)):
        return HTTPException(404, {
            "error_kind": "figure_not_found",
            "message": "This report has no such figure.",
        })
    if isinstance(exc, store.ReportNotFound):
        return HTTPException(404, {
            "error_kind": "report_not_found",
            "message": "No such report (or version) in this project. "
                       "Generate one first.",
        })
    logger.warning("reports: unmapped store error %r", exc)
    return HTTPException(500, {"error_kind": "tool_error", "message": str(exc)})


@router.get("/{name}/reports")
def list_reports(project: AuthorizedProject = ProjectAccessDep) -> list[dict]:
    return [m.model_dump() for m in store.list_reports(project.directory)]


@router.get("/{name}/reports/{report_id}")
def get_report(report_id: str, version: int | None = None,
               project: AuthorizedProject = ProjectAccessDep) -> dict:
    try:
        return store.load_report(project.directory, report_id, version).model_dump()
    except store.ReportStoreError as exc:
        raise _http(exc) from exc


@router.get("/{name}/reports/{report_id}/versions/{version}")
def get_report_version(report_id: str, version: int,
                       project: AuthorizedProject = ProjectAccessDep) -> dict:
    try:
        return store.load_report(project.directory, report_id, version).model_dump()
    except store.ReportStoreError as exc:
        raise _http(exc) from exc


@router.get("/{name}/reports/{report_id}/figures/{figure_id}")
def get_report_figure(report_id: str, figure_id: str,
                      project: AuthorizedProject = ProjectAccessDep) -> FileResponse:
    """The figure PNG, inline — the viewer's `<img src>` and nothing else."""
    try:
        path = store.figure_path(project.directory, report_id, figure_id)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    return FileResponse(
        path=str(path),
        media_type="image/png",
        headers={"Content-Disposition": content_disposition(
            f"{figure_id}.png", disposition="inline",
        )},
    )


@router.delete("/{name}/reports/{report_id}")
def delete_report(report_id: str,
                  project: AuthorizedProject = ProjectAccessDep,
                  db: DBSession = Depends(get_db),
                  user: User | None = Depends(optional_user)) -> dict:
    """
    Remove a report and every version of it.

    A live FOREIGN edit lock refuses this the way `put_worksheet` is refused:
    `ProjectAccessDep` answers "may this caller see the project", not "may they
    destroy part of it while someone else is editing". Check-only, not acquire —
    deleting a report is not claiming the project.
    """
    from routers.projects import _check_project_lock

    # The id is validated BEFORE the lock check so a malformed id is a 400 on
    # a locked project too, and nothing is looked up for it.
    try:
        store.validate_report_id(report_id)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    _lock = _lock_target(project)
    if _lock is not None:
        _check_project_lock(db, _lock, user)
    try:
        store.delete_report(project.directory, report_id)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    return {"deleted": True, "report_id": report_id}
