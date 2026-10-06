"""
Templates for the study report (WP11 of
docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md):
the one place the routes ask "render this document into that template".

``render_with_template(doc, template, figure_bytes=…, plan=…)`` dispatches on
the template's mode:

* ``template is None`` → the bundled default writer
  (``docx_writer.render_document_docx``), mode ``None``;
* a **tagged** template (Jinja2 tags in the document) → WP9's
  ``template_tagged.render_tagged``;
* an **untagged** template (a corporate document with cover, TOC, headings)
  → WP10's ``template_untagged.render_untagged`` with the stored mapping
  plan, or ``default_mapping`` when none was accepted yet.

**Seams.** WP10's module is reached ONLY through the module-level accessors
below — ``_render_untagged()``, ``_default_mapping()``, ``_propose_mapping()``
and ``_plan_model()`` — which import ``services.reports.template_untagged``
on first use. Tests monkeypatch the accessors with fakes
(``monkeypatch.setattr(templates, "_render_untagged", lambda: fake)``), so
the routes, the job and the chat tools are pinned whether or not that module
is present in the checkout; ``_render_tagged`` is likewise a module attribute
(WP9's function, importable here). ``untagged_available()`` says whether the
real module can be imported — the phase-4 QA driver skips the untagged legs
when it cannot.

**The plan on the wire.** ``MappingPlanBody`` is the pinned ``MappingPlan``
shape as this module validates it at the HTTP boundary (``PUT …/template/plan``
and the chat tool). ``sanitise_plan`` applies WP10's own rule — a
``heading_index`` the outline does not have drops the entry with a note,
never an exception — plus the checks the routes need (a rename without a
text, an unknown section id, an insert after an unknown heading), and
returns the plan as the dict the store keeps together with the notes it
added. When WP10's model is importable, the dict is additionally
round-tripped through it, so what is stored is exactly what
``render_untagged`` accepts.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from models.report import ReportDocument
from services.reports import template_tagged
from services.reports.docx_reader import (
    TemplateMode,
    TemplateOutline,
    TemplateReadError,
    read_template,
)
from services.reports.docx_writer import render_document_docx
from services.reports.template_tagged import TaggedRenderError

__all__ = [
    "InvalidMappingPlan",
    "MappingEntryBody",
    "MappingPlanBody",
    "TaggedRenderError",
    "TemplateMode",
    "TemplateOutline",
    "TemplateReadError",
    "read_outline",
    "render_with_template",
    "sanitise_plan",
    "untagged_available",
]

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
REPORT_TEMPLATE_KIND = "report_template"


class InvalidMappingPlan(ValueError):
    """The body is not a mapping plan at all (→ 400 `invalid_mapping_plan`)."""


# ── the plan on the wire (the pinned WP10 shape) ────────────────────────────

class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MappingEntryBody(_Body):
    heading_index: int
    action: Literal["keep", "rename", "drop"]
    new_text: str | None = None
    section_ids: list[str] = Field(default_factory=list)


class MappingInsertBody(_Body):
    after_heading_index: int
    section_id: str
    heading: str


class MappingPlanBody(_Body):
    entries: list[MappingEntryBody] = Field(default_factory=list)
    inserted: list[MappingInsertBody] = Field(default_factory=list)
    placeholders: dict[str, str] = Field(default_factory=dict)
    unmapped_sections: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ── seams ───────────────────────────────────────────────────────────────────

_render_tagged = template_tagged.render_tagged
_render_default = render_document_docx


def _untagged_module():
    from services.reports import template_untagged
    return template_untagged


def _render_untagged():
    """WP10's `render_untagged(doc, template, plan, *, figure_bytes) -> bytes`."""
    return _untagged_module().render_untagged


def _default_mapping():
    """WP10's `default_mapping(outline, doc) -> MappingPlan`."""
    return _untagged_module().default_mapping


def _propose_mapping():
    """WP10's `propose_mapping(provider, *, base_request, outline, doc, language)`."""
    return _untagged_module().propose_mapping


def _plan_model():
    """WP10's `MappingPlan` when importable, else the wire shape above."""
    try:
        return _untagged_module().MappingPlan
    except ImportError:
        return MappingPlanBody


def untagged_available() -> bool:
    try:
        _untagged_module()
    except ImportError:
        return False
    return True


# ── reading ─────────────────────────────────────────────────────────────────

def read_outline(template: bytes) -> TemplateOutline:
    """WP8's outline of `template`; raises `TemplateReadError` for a non-docx."""
    return read_template(bytes(template))


# ── rendering ───────────────────────────────────────────────────────────────

def _plan_instance(plan: Any):
    """`plan` as the model `render_untagged` takes (a dict is validated)."""
    if plan is None or hasattr(plan, "model_dump"):
        return plan
    return _plan_model().model_validate(dict(plan))


def render_with_template(doc: ReportDocument, template: bytes | None, *,
                         figure_bytes: dict[str, bytes],
                         plan: Any = None) -> tuple[bytes, TemplateMode | None]:
    """
    `doc` rendered into `template` (a `.docx`), or into the default document
    when `template` is None. Returns `(docx bytes, mode)`.

    Raises `TemplateReadError` (not a Word document), `TaggedRenderError`
    (a tag the document cannot fill) — never a crash on a user's file.
    """
    if template is None:
        return _render_default(doc, figure_bytes=figure_bytes), None
    outline = read_outline(template)
    if outline.mode == "tagged":
        return _render_tagged(doc, template, figure_bytes=figure_bytes), "tagged"
    plan_obj = _plan_instance(plan)
    if plan_obj is None:
        plan_obj = _default_mapping()(outline, doc)
    return _render_untagged()(doc, template, plan_obj, figure_bytes=figure_bytes), "untagged"


# ── the sanitiser ───────────────────────────────────────────────────────────

def sanitise_plan(raw: Any, outline: TemplateOutline,
                  doc: ReportDocument | None = None) -> tuple[dict[str, Any], list[str]]:
    """
    `raw` (a plan as JSON) validated against the pinned shape and checked
    against `outline` (and `doc` when given): `(plan dict, notes added)`.

    Dropped with a note, never an exception: an entry whose `heading_index`
    the outline does not have, a `rename` without `new_text`, an insert after
    an unknown heading or for an unknown section, an unmapped section the
    document does not have; an unknown section id inside an entry is removed
    from that entry. A body that is not a plan at all raises
    `InvalidMappingPlan`.
    """
    if not isinstance(raw, Mapping):
        raise InvalidMappingPlan("a mapping plan is a JSON object")
    try:
        body = MappingPlanBody.model_validate(dict(raw))
    except ValidationError as exc:
        raise InvalidMappingPlan(_short(exc)) from exc

    known_headings = {h.index for h in outline.headings}
    known_sections = ({s.section_id for s in doc.sections} if doc is not None else None)
    notes: list[str] = []

    entries: list[dict[str, Any]] = []
    for entry in body.entries:
        if entry.heading_index not in known_headings:
            notes.append(f"entry for heading {entry.heading_index} dropped: the template "
                         f"has no heading with that index")
            continue
        if entry.action == "rename" and not (entry.new_text or "").strip():
            notes.append(f"entry for heading {entry.heading_index} dropped: rename "
                         f"without new_text")
            continue
        section_ids = list(entry.section_ids)
        if known_sections is not None:
            unknown = [s for s in section_ids if s not in known_sections]
            if unknown:
                notes.append(f"entry for heading {entry.heading_index}: unknown section "
                             f"id(s) removed: {', '.join(unknown)}")
                section_ids = [s for s in section_ids if s in known_sections]
        entries.append({
            "heading_index": entry.heading_index,
            "action": entry.action,
            "new_text": entry.new_text if entry.action == "rename" else None,
            "section_ids": section_ids,
        })

    inserted: list[dict[str, Any]] = []
    for ins in body.inserted:
        if ins.after_heading_index not in known_headings:
            notes.append(f"insert of {ins.section_id!r} after heading "
                         f"{ins.after_heading_index} dropped: no such heading")
            continue
        if known_sections is not None and ins.section_id not in known_sections:
            notes.append(f"insert of {ins.section_id!r} dropped: the report has no such section")
            continue
        inserted.append({"after_heading_index": ins.after_heading_index,
                         "section_id": ins.section_id, "heading": ins.heading})

    unmapped = list(body.unmapped_sections)
    if known_sections is not None:
        ghosts = [s for s in unmapped if s not in known_sections]
        if ghosts:
            notes.append(f"unmapped section(s) the report does not have removed: "
                         f"{', '.join(ghosts)}")
            unmapped = [s for s in unmapped if s in known_sections]

    plan: dict[str, Any] = {
        "entries": entries,
        "inserted": inserted,
        "placeholders": dict(body.placeholders),
        "unmapped_sections": unmapped,
        "notes": [*body.notes, *notes],
    }
    model = _plan_model()
    if model is not MappingPlanBody:
        try:
            plan = model.model_validate(plan).model_dump()
        except ValidationError as exc:
            raise InvalidMappingPlan(_short(exc)) from exc
    return plan, notes


def _short(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:4]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "invalid mapping plan"
