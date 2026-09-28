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
  POST   /{name}/reports                                   evidence-only report (WP5), edit-lock checked
  POST   /{name}/reports/{report_id}/export                the .docx as an agent_export upload (WP5)

`POST /{name}/reports` accepts only `mode: "evidence_only"` in phase 1 — the
generated mode (WP3's job) answers 400 `report_mode_not_supported` until it
lands. The evidence is read from the CURRENT session's result state exactly
the way the chat tools read it (`get_eh_reference_design`, the adequacy
`build_study_report`, the project's FMEA worksheet), never re-run.

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
from pydantic import BaseModel, ConfigDict
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


# ── WP5: evidence-only report + .docx export ────────────────────────────────

class CreateReportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: str = "evidence_only"
    title: str | None = None


class ExportReportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: int | None = None
    filename: str | None = None


def _check_lock(project: AuthorizedProject, db: DBSession, user: User | None) -> None:
    from routers.projects import _check_project_lock

    _lock = _lock_target(project)
    if _lock is not None:
        _check_project_lock(db, _lock, user)


def _current_eh_report() -> dict | None:
    """The stored EH `ReferenceDesignReport` of the session, as the chat tool reads it."""
    from routers import results as results_router

    body = results_router.get_eh_reference_design()
    if getattr(body, "status_code", None) == 204:
        return None
    return body


def _current_study_report() -> dict | None:
    """
    The adequacy write-up, through the SAME reader the chat tool uses
    (`chat_tools.build_study_report`: `get_adequacy_results` per surface, the
    campaign status, the asset-health provenance). None when the session has
    nothing to read from — the collector then states every surface as not
    established, which is the finding, not an error.
    """
    from services import chat_tools

    try:
        return chat_tools.build_study_report()
    except HTTPException as exc:
        logger.info("reports: no adequacy study report for this session: %s", exc.detail)
        return None


def _render_figures(eh_report: dict | None) -> dict[str, bytes]:
    """WP4's three PNGs from the EH sections that are established; each may be absent."""
    from services.reports.figures import capacity_mix_png, fmea_pareto_png, frontier_png

    out: dict[str, bytes] = {}
    if not isinstance(eh_report, dict):
        return out
    sections = eh_report.get("sections") or {}

    def payload(name: str) -> dict:
        state = sections.get(name) or {}
        if state.get("status") != "ok" or not isinstance(state.get("payload"), dict):
            return {}
        return state["payload"]

    fmea = payload("fmea_top")
    frontier = payload("frontier")
    sizing = payload("sizing")
    for figure_id, png in (
        ("fmea_pareto", fmea_pareto_png(fmea.get("top")) if fmea else None),
        ("frontier", frontier_png(frontier.get("points"), frontier.get("knee_index"))
         if frontier else None),
        ("capacity_mix", capacity_mix_png(sizing.get("by_carrier")) if sizing else None),
    ):
        if png:
            out[figure_id] = png
    return out


@router.post("/{name}/reports")
def create_report(body: CreateReportBody,
                  project: AuthorizedProject = ProjectAccessDep,
                  db: DBSession = Depends(get_db),
                  user: User | None = Depends(optional_user)) -> dict:
    """
    Build and store the evidence-only report (v1) from the session's current
    result state. Returns the `ReportMeta` fields plus `document`.
    """
    from services.adequacy.worksheet import load_worksheet
    from services.reports.assemble import DEFAULT_TITLE, evidence_only_document
    from services.reports.evidence import collect_evidence

    if body.mode != "evidence_only":
        raise HTTPException(400, {
            "error_kind": "report_mode_not_supported",
            "message": f"Report mode {body.mode!r} is not available yet: "
                       "generation lands in phase 2. Use mode 'evidence_only'.",
        })
    _check_lock(project, db, user)

    eh_report = _current_eh_report()
    study_report = _current_study_report()
    worksheet = load_worksheet(project.directory)
    evidence = collect_evidence(study_report=study_report, eh_report=eh_report,
                                worksheet=worksheet)
    figure_pngs = _render_figures(eh_report)
    title = (body.title or "").strip() or f"{DEFAULT_TITLE} — {project.name}"
    doc = evidence_only_document(evidence, title=title,
                                 report_id=store.new_report_id(),
                                 figure_pngs=figure_pngs)
    try:
        meta = store.create_report(project.directory, doc)
        for figure_id, png in figure_pngs.items():
            if figure_id in doc.figures:
                store.write_figure(project.directory, doc.report_id, figure_id, png)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    stored = store.load_report(project.directory, doc.report_id)
    return {**meta.model_dump(), "document": stored.model_dump()}


@router.post("/{name}/reports/{report_id}/export")
def export_report(report_id: str, body: ExportReportBody,
                  project: AuthorizedProject = ProjectAccessDep,
                  db: DBSession = Depends(get_db),
                  user: User | None = Depends(optional_user)) -> dict:
    """
    Render one version of a report to `.docx` and save it as an `agent_export`
    upload of the project, so it shows as a chip and downloads through the
    existing blob route. Returns the `UploadMeta` dict.
    """
    from services import upload_service
    from services.reports.docx_writer import DOCX_MIME, render_document_docx

    try:
        store.validate_report_id(report_id)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    _check_lock(project, db, user)
    try:
        doc = store.load_report(project.directory, report_id, body.version)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    figure_bytes: dict[str, bytes] = {}
    for figure_id in doc.figures:
        try:
            figure_bytes[figure_id] = store.figure_path(
                project.directory, report_id, figure_id).read_bytes()
        except (store.ReportStoreError, OSError):
            continue  # the writer states the figure was not produced
    data = render_document_docx(doc, figure_bytes=figure_bytes)
    target = (body.filename or "").strip() or f"report_{report_id}_v{doc.version}.docx"
    if not target.lower().endswith(".docx"):
        target += ".docx"
    meta = upload_service.add_upload(project.name, data, target, DOCX_MIME,
                                     kind="agent_export", project_dir=project.directory)
    return meta.model_dump()
