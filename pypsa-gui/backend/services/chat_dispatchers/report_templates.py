"""
Report templates (5), WP11: the dispatchers for the user's own Word template.

Moved out of `services/chat_tools.py` as the first per-tool fragment
(RETRO-FRAG-1). Schemas and routes: `harness/catalogue_tools/report_templates.py`.

Thin wrappers over the template routes (`routers/reports.py`,
`routers/report_jobs.py`), called in-process for the active project. A
template is DATA: the mapping job shows its outline to the model inside the
untrusted-data fence, and nothing found in a template is ever followed.
"""

from __future__ import annotations

from services.chat_tools import (
    _newest_report_id,
    _report_project,
    _require_active_project,
    _route,
)


def list_report_templates() -> list[dict]:
    """The active project's uploads of kind `report_template`, newest first."""
    from services import upload_service

    name = _require_active_project()
    return [
        {
            "file_id": m.file_id,
            "filename": m.filename,
            "mime": m.mime,
            "kind": m.kind,
            "size_kb": round(m.size / 1024, 1),
            "uploaded_at": m.uploaded_at,
        }
        for m in upload_service.list_uploads(name, kind="report_template")
    ]


def set_report_template(report_id: str, file_id: str | None = None) -> dict:
    """Bind an upload as the report's template (null unbinds); the route's outline back."""
    from routers.reports import BindTemplateBody, bind_template as _h

    project = _report_project()
    result = _route(
        _h, report_id, BindTemplateBody(file_id=file_id or None), project=project
    )
    if result.get("template_file_id") is None:
        message = (
            f"Report {report_id} now uses the default document layout; "
            "export_report_docx renders it with the bundled writer."
        )
    elif result.get("mode") == "tagged":
        message = (
            f"Report {report_id} is bound to a TAGGED template: its {{ }} tags "
            "are filled on export_report_docx; no mapping plan is needed."
        )
    else:
        message = (
            f"Report {report_id} is bound to an UNTAGGED template "
            f"(language {result.get('language') or 'undetected'}): "
            "propose_report_mapping proposes how its headings map onto the "
            "report, set_report_mapping edits the plan, export_report_docx "
            "rebuilds the body into it."
        )
    return {**result, "message": message}


def get_report_template(report_id: str | None = None) -> dict:
    """The bound template's outline and stored plan (the newest report when omitted)."""
    from routers.reports import get_template as _h

    project = _report_project()
    rid = report_id if report_id is not None else _newest_report_id(project)
    return _route(_h, rid, project=project)


def propose_report_mapping(report_id: str, language: str | None = None) -> dict:
    """Start the mapping job for the report's untagged template (same slot as generate)."""
    from routers.report_jobs import ProposeMappingBody, propose_template_plan as _h

    project = _report_project()
    result = _route(
        _h, report_id, ProposeMappingBody(language=language or None), project=project
    )
    return {
        **result,
        "message": (
            "Mapping proposal started — poll get_report_status until it is done "
            f"(mode 'mapping'), then read the plan with get_report_template('{report_id}')."
        ),
    }


def set_report_mapping(report_id: str, plan: dict, strict: bool = False) -> dict:
    """Store a (user- or model-edited) mapping plan; the sanitised plan back."""
    from routers.reports import put_template_plan as _h

    project = _report_project()
    body = dict(plan) if isinstance(plan, dict) else plan
    if isinstance(body, dict) and strict:
        body["strict"] = True
    return _route(_h, report_id, body, project=project)


DISPATCHERS = {
    "list_report_templates": list_report_templates,
    "set_report_template": set_report_template,
    "get_report_template": get_report_template,
    "propose_report_mapping": propose_report_mapping,
    "set_report_mapping": set_report_mapping,
}
