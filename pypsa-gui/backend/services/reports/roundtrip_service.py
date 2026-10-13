"""
The round trip as the routes and the chat tools use it (WP13 of
docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md):
one call that reads a user's edited Word copy back into the report as its
next version, and one that diffs two stored versions.

**Seams.** WP12's reader and merge (``services.reports.roundtrip``:
``read_edited_docx(data, base) -> RoundTripResult`` and
``merge_round_trip(base, result) -> ReportDocument``) are reached ONLY
through the module-level accessors ``_read_edited_docx()`` and
``_merge_round_trip()``, which import the module on first use — the same
pattern ``services/reports/templates.py`` keeps for WP10. Tests monkeypatch
the accessors with fakes shaped like the pinned ``RoundTripResult``, so the
routes are pinned whether or not that module is present in the checkout;
``roundtrip_available()`` says whether the real one can be imported (the
phase-5 QA driver skips the round-trip legs when it cannot).

**The additive fields.** ``Section.pending_instruction`` and
``Section.comments`` are WP12's; this module reads them with ``getattr`` and
never sets them, so it runs before and after the merge that adds them.

``run_round_trip`` writes a NEW VERSION (a version file is never rewritten):
the merge's document, bound to the uploaded file as the report's template
when asked (so the styling the user changed in Word survives the next
export), with the meta's ``template_mode`` / ``template_language`` set from
the file's outline and a stale ``mapping_plan`` cleared — exactly what
``POST …/template`` does when a template changes.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Any

from models.report import ReportDocument, ReportMeta
from services.reports import store, templates

__all__ = [
    "RoundTripNotAReport",
    "diff_versions",
    "roundtrip_available",
    "run_round_trip",
]

logger = logging.getLogger(__name__)

REPORT_ROUNDTRIP_KIND = "report_roundtrip"
CHANGE_KINDS = ("unchanged", "changed", "added", "removed")


class RoundTripNotAReport(ValueError):
    """Nothing in the file matched a section of the report (→ 400 `roundtrip_not_a_report`)."""


# ── seams ───────────────────────────────────────────────────────────────────

def _roundtrip_module():
    from services.reports import roundtrip
    return roundtrip


def _read_edited_docx():
    """WP12's `read_edited_docx(data: bytes, base: ReportDocument) -> RoundTripResult`."""
    return _roundtrip_module().read_edited_docx


def _merge_round_trip():
    """WP12's `merge_round_trip(base: ReportDocument, result) -> ReportDocument`."""
    return _roundtrip_module().merge_round_trip


def roundtrip_available() -> bool:
    try:
        _roundtrip_module()
    except ImportError:
        return False
    return True


# ── the round trip ──────────────────────────────────────────────────────────

def _matched_sections(result: Any) -> list[Any]:
    return [s for s in (getattr(result, "sections", None) or [])
            if getattr(s, "section_id", None)]


def _meta_has(field: str) -> bool:
    return field in ReportMeta.model_fields and field in store._CARRIED_META_FIELDS


def run_round_trip(project_dir: pathlib.Path, report_id: str, upload_bytes: bytes, *,
                   bind_as_template: bool, file_id: str | None = None,
                   ) -> tuple[ReportDocument, Any]:
    """
    Read `upload_bytes` (the user's edited copy) against the LATEST version
    of `report_id`, merge it, and save the merge as the next version.
    Returns `(the saved document, the RoundTripResult)`.

    Raises `store.ReportStoreError` (no such report), `TemplateReadError`
    (not a Word document — WP12 raises the reader's own class),
    `RoundTripNotAReport` (no `sec:` bookmark and no heading matched any
    section: nothing to merge, nothing written) and `ImportError` when
    WP12's module is not in the build — all BEFORE anything is written.
    `bind_as_template` with a `file_id` binds that upload as the report's
    template on the new version.
    """
    read = _read_edited_docx()
    merge = _merge_round_trip()
    base = store.load_report(project_dir, report_id)
    result = read(bytes(upload_bytes), base)
    if not _matched_sections(result):
        raise RoundTripNotAReport(
            "nothing in the file matched a section of the report: no sec:<id> "
            "bookmark and no heading names one of its sections")
    merged = merge(base, result)
    if not isinstance(merged, ReportDocument):
        merged = ReportDocument.model_validate(merged)
    merged = merged.model_copy(update={"report_id": base.report_id})
    template_changed = False
    if bind_as_template and file_id is not None:
        template_changed = merged.template_file_id != file_id
        merged = merged.model_copy(update={"template_file_id": file_id})
    version = store.save_version(project_dir, merged)
    meta_updates: dict[str, Any] = {}
    if bind_as_template and file_id is not None:
        try:
            outline = templates.read_outline(upload_bytes)
            meta_updates.update(template_mode=outline.mode,
                                template_language=outline.language)
        except templates.TemplateReadError:  # the reader accepted it; the outline is optional
            logger.info("reports: round-trip file %s has no readable outline", file_id)
        if template_changed:
            meta_updates["mapping_plan"] = None
        if _meta_has("roundtrip_file_id"):
            meta_updates["roundtrip_file_id"] = file_id
    if meta_updates:
        store.update_meta(project_dir, report_id, **meta_updates)
    return store.load_report(project_dir, report_id, version), result


# ── the diff ────────────────────────────────────────────────────────────────

def _blocks_json(section: Any) -> list[dict]:
    return [b.model_dump(mode="json") if hasattr(b, "model_dump") else dict(b)
            for b in (getattr(section, "blocks", None) or [])]


def diff_versions(a_doc: ReportDocument, b_doc: ReportDocument) -> list[dict]:
    """
    One row per section id in the union of both versions — `a`'s order,
    then `b`'s additions — with `change` = `added` (only in b), `removed`
    (only in a), `changed` (the blocks differ by JSON equality), else
    `unchanged`; `source_a` / `source_b` the sections' `source`; `heading`,
    `pending_instruction` and `comments` from `b` (from `a` for a removed
    section).
    """
    a_by = {s.section_id: s for s in a_doc.sections}
    b_by = {s.section_id: s for s in b_doc.sections}
    order = list(a_by) + [sid for sid in b_by if sid not in a_by]
    rows: list[dict] = []
    for sid in order:
        sa, sb = a_by.get(sid), b_by.get(sid)
        if sa is None:
            change = "added"
        elif sb is None:
            change = "removed"
        elif _blocks_json(sa) != _blocks_json(sb):
            change = "changed"
        else:
            change = "unchanged"
        ref = sb if sb is not None else sa
        rows.append({
            "section_id": sid,
            "heading": ref.heading,
            "change": change,
            "source_a": sa.source if sa is not None else None,
            "source_b": sb.source if sb is not None else None,
            "pending_instruction": getattr(sb, "pending_instruction", None) if sb is not None else None,
            "comments": list(getattr(sb, "comments", None) or []) if sb is not None else [],
        })
    return rows
