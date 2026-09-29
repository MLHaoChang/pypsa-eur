"""
Report generation routes (WP3) — `/api/projects/{name}/reports/generate…`.

  POST /{name}/reports/generate                                 start the job → {status: "running", report_id}
  GET  /{name}/reports/generate/status                          the job record (204 when never run)
  POST /{name}/reports/generate/abort                           set the stop event (idempotent; 404 never run)
  POST /{name}/reports/{report_id}/sections/{section_id}/regenerate
                                                                one section again → version+1; with no
                                                                `instruction` the section's
                                                                `pending_instruction` (WP12/13) is used
                                                                and cleared on the new version
  POST /{name}/reports/{report_id}/template/plan                the "mapping" job (WP11): propose how an
                                                                untagged template's headings map onto the report

`POST …/generate` takes `template_file_id` (WP11): the new report is bound to
that upload and, when `language` is omitted, the template's detected
language is the report's (else "en").

Mounted under `/api/projects` right after `routers/reports.py` (WP1/WP5),
with the same `ProjectAccessDep` and the same edit-lock check as the
evidence-only POST. The evidence is collected HERE, in the request, exactly
as `routers/reports.py::create_report` collects it (`_current_eh_report`,
`_current_study_report`, the worksheet, WP4's figures) — so the worker
thread never touches result state or the PyPSA lock.

The provider is resolved through the seam the chat harness uses:
`llm_config.resolve_profile(None)` (the ACTIVE profile) and
`chat_service._provider_for_profile`. A profile that cannot be built answers
400 with the seam's own neutral kind (`missing_api_key`,
`sdk_not_installed`, …); the job record carries the profile id and model so
the report says who wrote it.

Job state lives in the session's solver state under `report_job` (the
`eh_study` shape: `thread` and `stop_event` never reach the wire).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from db.models import User
from db.session import get_db
from deps import optional_user
from routers.deps import AuthorizedProject, ProjectAccessDep
from routers.reports import (
    _bound,
    _check_lock,
    _current_eh_report,
    _current_study_report,
    _http,
    _latest_or_404,
    _render_figures,
    _resolve_template,
    _template_not_untagged,
)
from services.pypsa_service import PyPSAService
from services.reports import store, templates
from services.reports.report_job import (
    ReportJobInFlight,
    pending_instruction_of,
    public_record,
    start_mapping_job,
    start_report_job,
)

logger = logging.getLogger(__name__)
router = APIRouter()

JOB_KEY = "report_job"


class GenerateReportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = None
    # None → the template's detected language when one is given, else "en".
    language: str | None = None
    sections: list[str] | None = Field(default=None, min_length=1)
    instruction: str | None = None
    template_file_id: str | None = None


class ProposeMappingBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # None → the template's detected language, else the report's, else "en".
    language: str | None = None


class RegenerateSectionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instruction: str | None = None
    language: str | None = None


# ── seams (module attributes so a test can replace them) ────────────────────


def _resolve_profile() -> Any:
    from services import llm_config
    return llm_config.resolve_profile(None)


def _provider_for_profile(profile: Any) -> tuple[Any, str | None]:
    """`(provider, error_kind|None)` — the chat harness's own factory."""
    from services import chat_service
    return chat_service._provider_for_profile(profile)


def _provider_or_400(profile: Any) -> Any:
    provider, err = _provider_for_profile(profile)
    if provider is not None:
        return provider
    if err == "missing_api_key":
        raise HTTPException(400, {
            "error_kind": "missing_api_key",
            "message": f"The active LLM profile ({profile.id}) has no API key "
                       "configured; add one in the LLM settings or pick another "
                       "profile.",
        })
    if err == "sdk_not_installed":
        raise HTTPException(400, {
            "error_kind": "sdk_not_installed",
            "message": f"The SDK the active LLM profile ({profile.id}) needs is "
                       "not installed in this build.",
        })
    raise HTTPException(400, {
        "error_kind": err or "internal_error",
        "message": f"The active LLM profile ({profile.id}) cannot be used: {err}.",
    })


def _state() -> Any:
    from routers.simulation import _state
    return _state


# ── evidence ────────────────────────────────────────────────────────────────


def _evidence_for(project: AuthorizedProject):
    """The session's evidence and WP4's figures, as the evidence-only POST reads them."""
    from services.adequacy.worksheet import load_worksheet
    from services.reports.evidence import collect_evidence

    eh_report = _current_eh_report()
    study_report = _current_study_report()
    worksheet = load_worksheet(project.directory)
    evidence = collect_evidence(study_report=study_report, eh_report=eh_report,
                                worksheet=worksheet)
    if not any(s.status == "ok" for s in evidence.sections.values()):
        raise HTTPException(400, {
            "error_kind": "no_evidence",
            "message": "Nothing to report on: no Energy Hub reference design, no "
                       "reliability surface and no expert worksheet rows exist in "
                       "this session. Run a study first (run_eh_study or an "
                       "adequacy study), or create an evidence-only report.",
        })
    return evidence, _render_figures(eh_report)


def _in_flight(exc: Exception) -> HTTPException:
    return HTTPException(409, {
        "error_kind": "report_job_in_flight",
        "message": "A report is already being generated — wait for it to "
                   "finish or abort it.",
    })


def _start(project: AuthorizedProject, **kwargs: Any) -> str:
    try:
        return start_report_job(
            project_name=project.name, project_dir=project.directory,
            lock=PyPSAService.get_solver_state_lock(), state=_state(), **kwargs)
    except ReportJobInFlight as exc:
        raise _in_flight(exc) from exc
    except KeyError as exc:
        raise HTTPException(404, {
            "error_kind": "report_section_not_found",
            "message": f"The report has no section {exc.args[0]!r}.",
        }) from exc


# ── routes ──────────────────────────────────────────────────────────────────


@router.post("/{name}/reports/generate")
def generate_report(body: GenerateReportBody,
                    project: AuthorizedProject = ProjectAccessDep,
                    db: DBSession = Depends(get_db),
                    user: User | None = Depends(optional_user)) -> dict:
    """
    Start a generated report (v1) from the session's current result state on
    the active LLM profile. Poll `GET …/generate/status`; read the document
    through `GET /{name}/reports/{report_id}` once `done` or `aborted`.
    """
    _check_lock(project, db, user)
    template_kwargs: dict[str, Any] = {}
    language = body.language
    if body.template_file_id is not None:
        # Resolved BEFORE the provider and the evidence: a bad template is a
        # 4xx with nothing claimed, as an unknown section is.
        _data, outline = _resolve_template(project, body.template_file_id)
        template_kwargs = {
            "template_file_id": body.template_file_id,
            "meta_updates": {"template_mode": outline.mode,
                             "template_language": outline.language},
        }
        language = language or outline.language
    profile = _resolve_profile()
    provider = _provider_or_400(profile)
    evidence, figure_pngs = _evidence_for(project)
    report_id = _start(
        project, evidence=evidence, figure_pngs=figure_pngs, profile=profile,
        provider=provider, language=language or "en",
        sections=body.sections, instruction=body.instruction, title=body.title,
        **template_kwargs)
    return {"status": "running", "report_id": report_id}


@router.post("/{name}/reports/{report_id}/template/plan")
def propose_template_plan(report_id: str, body: ProposeMappingBody,
                          project: AuthorizedProject = ProjectAccessDep,
                          db: DBSession = Depends(get_db),
                          user: User | None = Depends(optional_user)) -> dict:
    """
    Start the mapping job for the report's bound UNTAGGED template: one
    generation call on the active LLM profile proposes how the template's
    headings map onto the report's sections; the sanitised plan lands in the
    report's meta (`GET …/template` shows it, `PUT …/template/plan` edits
    it, the export uses it). Same slot as generate: `GET …/generate/status`
    (`mode: "mapping"`) and `POST …/generate/abort` cover it.
    """
    doc = _latest_or_404(project, report_id)
    _check_lock(project, db, user)
    _data, outline = _bound(project, doc)
    if outline.mode != "untagged":
        raise _template_not_untagged(outline.mode)
    try:
        # Through the accessor, so a checkout without WP10's module answers
        # here, before anything is claimed, and a test's fake is honoured.
        templates._propose_mapping()
    except ImportError as exc:
        raise HTTPException(500, {
            "error_kind": "tool_error",
            "message": "Untagged templates are not available in this build "
                       "(services.reports.template_untagged is missing).",
        }) from exc
    profile = _resolve_profile()
    provider = _provider_or_400(profile)
    language = body.language or outline.language or doc.language or "en"
    try:
        start_mapping_job(
            project_name=project.name, project_dir=project.directory,
            document=doc, outline=outline, profile=profile, provider=provider,
            language=language, lock=PyPSAService.get_solver_state_lock(), state=_state())
    except ReportJobInFlight as exc:
        raise _in_flight(exc) from exc
    return {"status": "running", "report_id": report_id}


@router.get("/{name}/reports/generate/status")
def get_generate_status(project: AuthorizedProject = ProjectAccessDep):
    """The job record without its thread handle and stop event; 204 when never run."""
    from starlette.responses import Response

    with PyPSAService.get_solver_state_lock():
        record = public_record(_state().get(JOB_KEY))
    if record is None:
        return Response(status_code=204)
    return record


@router.post("/{name}/reports/generate/abort")
def abort_generate(project: AuthorizedProject = ProjectAccessDep) -> dict:
    """Ask the running job to stop after the current section. Idempotent."""
    with PyPSAService.get_solver_state_lock():
        record = _state().get(JOB_KEY)
        if not record:
            raise HTTPException(404, {
                "error_kind": "report_job_not_found",
                "message": "No report generation has been run in this session.",
            })
        event = record.get("stop_event")
        status = record.get("status")
    if event is not None:
        event.set()
    return {"status": status, "aborting": status == "running"}


@router.post("/{name}/reports/{report_id}/sections/{section_id}/regenerate")
def regenerate_section(report_id: str, section_id: str, body: RegenerateSectionBody,
                       project: AuthorizedProject = ProjectAccessDep,
                       db: DBSession = Depends(get_db),
                       user: User | None = Depends(optional_user)) -> dict:
    """
    Write one section again, with an optional instruction, from the latest
    version of the report; the result is saved as the next version with
    every other section unchanged.

    WP13: when `instruction` is omitted and the section carries a
    `pending_instruction` (a comment the user left in Word, stored by the
    round trip), the job uses it; either way the new version clears it.
    The answer says which was used (`instruction_source`: "body",
    "pending" or null).
    """
    try:
        store.validate_report_id(report_id)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    _check_lock(project, db, user)
    try:
        base = store.load_report(project.directory, report_id)
    except store.ReportStoreError as exc:
        raise _http(exc) from exc
    section = next((s for s in base.sections if s.section_id == section_id), None)
    if section is None:
        raise HTTPException(404, {
            "error_kind": "report_section_not_found",
            "message": f"Report {report_id} has no section {section_id!r}.",
        })
    instruction = (body.instruction or "").strip() or None
    source: str | None = "body" if instruction else None
    if instruction is None:
        instruction = pending_instruction_of(section)
        source = "pending" if instruction else None
    profile = _resolve_profile()
    provider = _provider_or_400(profile)
    evidence, figure_pngs = _evidence_for(project)
    _start(
        project, evidence=evidence, figure_pngs=figure_pngs, profile=profile,
        provider=provider, language=body.language or base.language or "en",
        instruction=instruction, base_document=base,
        regenerate_section=section_id)
    return {"status": "running", "report_id": report_id, "version": base.version,
            "instruction_source": source}
