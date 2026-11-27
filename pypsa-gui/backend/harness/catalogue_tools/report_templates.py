"""
Report templates (5), WP11: the user's own Word template.

Moved out of `harness/catalogue.py` as the first per-tool fragment
(RETRO-FRAG-1); spliced back at its old place, so it declares no ORDER.
Dispatchers: `services/chat_dispatchers/report_templates.py`.
"""

from __future__ import annotations

from harness.catalogue_base import _t

TOOLS = [
    # ── Report templates (5) — WP11: the user's own Word template ───────────
    # A template is an upload of kind `report_template` (the user drops a
    # .docx on the report viewer, or uploads with ?kind=report_template). It
    # is DATA: its outline goes to the model inside the untrusted-data fence
    # and nothing found in it is ever followed.
    _t(
        "list_report_templates",
        "The active project's uploaded report templates (uploads of kind "
        "report_template): [{file_id, filename, mime, kind, size_kb, "
        "uploaded_at}], newest first. A template is a Word .docx the user "
        "uploaded; it is either TAGGED ({{ fields.x.text }} / row loops the "
        "export fills) or UNTAGGED (a corporate document whose body is "
        "rebuilt from a mapping plan). Safety: read.",
        {},
    ),
    _t(
        "set_report_template",
        "Bind an uploaded template to a report (`file_id` from "
        "list_report_templates; any .docx upload also works) or unbind it "
        "(`file_id` null → the default layout). Returns {template_file_id, "
        "mode: tagged|untagged|null, language (detected from the template), "
        "outline: headings/tags/placeholders/tables, message}. Binding "
        "writes the next version of the report; changing the template "
        "clears a stored mapping plan. 404 upload_not_found; 400 "
        "template_not_a_template (not a .docx), template_unreadable. "
        "Safety: write.",
        {
            "report_id": {"type": "string"},
            "file_id": {"type": "string"},
        },
        ["report_id"],
    ),
    _t(
        "get_report_template",
        "The report's bound template (the newest report when `report_id` is "
        "omitted): {template_file_id, mode, language, outline, plan} — all "
        "null when none is bound; `plan` is the stored mapping plan of an "
        "untagged template (null until propose_report_mapping or "
        "set_report_mapping stored one). Safety: read.",
        {"report_id": {"type": "string"}},
        [],
    ),
    _t(
        "propose_report_mapping",
        "Ask the active LLM profile how the bound UNTAGGED template's "
        "headings map onto the report's sections (keep / rename / drop per "
        "heading, sections to insert, placeholder values): ONE generation "
        "call, same job slot as generate_report (409 report_job_in_flight; "
        "poll get_report_status, mode 'mapping'). The sanitised plan is "
        "stored on the report — read it with get_report_template, edit it "
        "with set_report_mapping; export_report_docx uses it. When the "
        "model's answer is not a plan the code-only default mapping is "
        "stored and prose_failures names section 'mapping'. `language` "
        "defaults to the template's. 400 no_template, 400 "
        "template_not_untagged (a tagged template needs no plan). "
        "Safety: execution.",
        {
            "report_id": {"type": "string"},
            "language": {"type": "string"},
        },
        ["report_id"],
    ),
    _t(
        "set_report_mapping",
        "Store an edited mapping plan for the report's untagged template: "
        "`plan` is the object get_report_template returns as `plan` "
        "({entries: [{heading_index, action: keep|rename|drop, new_text, "
        "section_ids}], inserted: [{after_heading_index, section_id, "
        "heading}], placeholders: {text: value}, unmapped_sections, notes}). "
        "An entry the template cannot place is dropped with a note in the "
        "returned plan; with `strict` true such a plan is refused (400 "
        "invalid_mapping_plan with the notes). 400 no_template. "
        "Safety: write.",
        {
            "report_id": {"type": "string"},
            "plan": {"type": "object"},
            "strict": {"type": "boolean"},
        },
        ["report_id", "plan"],
    ),
]

ROUTES = {
    # report templates (5) — WP11: the template routes, called in-process;
    # `list_report_templates` is the uploads list filtered to one kind.
    "list_report_templates": [("GET", "/api/projects/{name}/uploads")],
    "set_report_template": [
        ("POST", "/api/projects/{name}/reports/{report_id}/template")
    ],
    "get_report_template": [
        ("GET", "/api/projects/{name}/reports/{report_id}/template")
    ],
    "propose_report_mapping": [
        ("POST", "/api/projects/{name}/reports/{report_id}/template/plan"),
    ],
    "set_report_mapping": [
        ("PUT", "/api/projects/{name}/reports/{report_id}/template/plan")
    ],
}
