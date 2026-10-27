"""Old -> new edit cards for the report template, mapping and round-trip tools (Q3-CARDS-1).

`set_report_template`, `set_report_mapping` and `import_edited_report` change a report's
stored versions and meta, not the network. Each preview checks the caller's access to the
project first, reads the old value from the report store the tool writes, and takes the new
value from the route's own pure step (the template resolver, `routers.reports._validated_plan`,
`roundtrip_service.read_edited_copy` plus the merge); nothing is written, and the same refusals
the route raises (edit lock, report job running, unreadable file) are raised here.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from harness.edit_card import (STUDY_LEAF_CAP, _SECRET_KEY, Adapter, _note, _refuse, _same, _setting,
                               _settings_row, register)

_SECTION = "results"  # "Results -> reports": where the report viewer lives


# ── the report and the route's own refusals ─────────────────────────────────

def _report_state(args: dict, *, busy: str | None = None):
    """(project, report id, latest document, stored meta). Access first, then the route's own
    lookups and refusals (missing report, edit lock, a running report job)."""
    from routers import reports
    from services import chat_tools
    from services.reports import store

    project = chat_tools._report_project()  # the handler's own authorisation, before any read
    report_id = args.get("report_id")
    if not isinstance(report_id, str) or not report_id.strip():
        _refuse("empty_edit", "Name the report to edit.")
    doc = reports._latest_or_404(project, report_id)
    with chat_tools._acting() as (db, user):
        reports._check_lock(project, db, user)
    if busy and reports._job_slot_busy():
        _refuse("report_job_in_flight", busy, 409)
    try:
        meta = store.load_meta(project.directory, report_id)
    except store.ReportStoreError as exc:
        raise reports._http(exc) from exc
    return project, report_id, doc, meta


def _upload_name(project, file_id: str | None):
    """An upload's file name, or its id when the file is gone; None when nothing is bound."""
    from services import upload_service

    if not file_id:
        return None
    try:
        return upload_service.get_upload_meta(project.name, file_id, project_dir=project.directory).filename
    except HTTPException:
        return file_id


def _row(field: str, label: str, old, new, *, words: dict | None = None) -> dict:
    row = _settings_row("Report", field, label, old, new, says_name=label)
    if words:
        row["words"] = words
    return row


def _capped(typed: list[dict], notes: list[dict], what: str) -> list[dict]:
    """The notes first (they are the warnings), then at most STUDY_LEAF_CAP rows and one
    summary note for the rest."""
    rows = [*notes, *typed[:STUDY_LEAF_CAP]]
    if len(typed) > STUDY_LEAF_CAP:
        rows.append(_note("Settings", "Report", what, f"{len(typed) - STUDY_LEAF_CAP} more {what} change."))
    return rows


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


# ── set_report_template: the template bound to the report ───────────────────

def _plan_size(plan):
    if not plan:
        return None
    count = len(plan.get("entries") or []) if isinstance(plan, dict) else 0
    return f"{count} heading entr{'y' if count == 1 else 'ies'}"


def _template_state(project, doc, meta) -> dict[str, Any]:
    return {"template_file_id": _upload_name(project, doc.template_file_id),
            "template_mode": meta.template_mode, "template_language": meta.template_language,
            "mapping_plan": _plan_size(meta.mapping_plan)}


def _template_preview(args: dict) -> list[dict]:
    from pydantic import ValidationError

    from routers import reports

    project, report_id, doc, meta = _report_state(args)
    try:
        body = reports.BindTemplateBody(file_id=args.get("file_id") or None)
    except ValidationError as exc:
        _refuse("invalid_edit_value", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                               for e in exc.errors(include_url=False)[:3]))
    old = _template_state(project, doc, meta)
    if body.file_id is None:
        outline, new_name = None, None
    else:
        _data, outline = reports._resolve_template(project, body.file_id)  # the route's own checks
        new_name = _upload_name(project, body.file_id)
    changed = doc.template_file_id != body.file_id
    new = {"template_file_id": new_name,
           "template_mode": outline.mode if outline else None,
           "template_language": outline.language if outline else None,
           # a different template clears the stored plan, whose heading indices belong to the old outline
           "mapping_plan": None if changed else old["mapping_plan"]}
    differing = [key for key in new if not _same(old[key], new[key])]
    if not differing:
        _refuse("empty_edit", "The report already uses " + ("this template." if body.file_id else
                                                              "the built-in document layout."))
    labels = {"template_file_id": "Template", "template_mode": "Template type",
              "template_language": "Template language", "mapping_plan": "Mapping plan"}
    rows = []
    for key in ("template_file_id", "template_mode", "template_language", "mapping_plan"):
        if key != "template_file_id" and key not in differing:
            continue  # the template the user named always shows; the derived rows only when they move
        words = None
        if key == "template_file_id":
            words = {"old": old[key] or "Built-in default", "new": new[key] or "Built-in default"}
        elif key == "mapping_plan" and new[key] is None:
            words = {"old": old[key] or "not set", "new": "cleared"}
        rows.append(_row(key, labels[key], old[key], new[key], words=words))
    return rows


def _template_readback(args: dict, rows: list[dict]) -> list[Any]:
    project, _report_id, doc, meta = _report_state(args)
    stored = _template_state(project, doc, meta)
    return [None if row["role"] == "note" else _setting(stored[row["field"]]) for row in rows]


register("set_report_template", Adapter("settings_edit", _template_preview, _template_readback,
                                        section_id=_SECTION))


# ── set_report_mapping: the stored plan that places report sections under template headings ──

_BUSY_PLAN = ("A report job is running: wait for it to finish (or abort it) before saving a "
              "mapping plan; a mapping job would overwrite it when it finishes.")
_ENTRY_LABELS = {"action": "Action", "new_text": "New text", "section_ids": "Report sections"}
_MAX_NAMES = 3


def _names(ids, titles: dict) -> str | None:
    """Section titles the way the panel lists them; a long list is a count."""
    ids = list(ids or [])
    if not ids:
        return None
    if len(ids) > _MAX_NAMES:
        return _plural(len(ids), "section")
    return ", ".join(titles.get(sid, sid) for sid in ids)


def _short(text: str, width: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _plan_raw(plan, outline, titles: dict) -> dict[str, Any]:
    """Every value of a plan by field, defaults filled in as the panel shows them (an absent
    entry is `keep`, no new text, no sections)."""
    plan = plan if isinstance(plan, dict) else {}
    entries = {e.get("heading_index"): e for e in plan.get("entries") or [] if isinstance(e, dict)}
    out: dict[str, Any] = {}
    for index in dict.fromkeys([*(h.index for h in outline.headings), *entries]):
        entry = entries.get(index) or {}
        out[f"entries.{index}.action"] = entry.get("action") or "keep"
        out[f"entries.{index}.new_text"] = entry.get("new_text")
        out[f"entries.{index}.section_ids"] = _names(entry.get("section_ids"), titles)
    for ins in plan.get("inserted") or []:
        if isinstance(ins, dict):
            key = f"inserted.{ins.get('section_id')}"
            cell = f"after heading {ins.get('after_heading_index')} as “{_short(ins.get('heading') or '')}”"
            out[key] = f"{out[key]}; {cell}" if key in out else cell
    for key, value in (plan.get("placeholders") or {}).items():
        out[f"placeholders.{key}"] = value
    out["unmapped_sections"] = _names(plan.get("unmapped_sections"), titles)
    notes = plan.get("notes") or []
    out["notes"] = _plural(len(notes), "note") if notes else None
    return out


def _plan_cell(field: str, value):
    """A plan value in the panel's words; a secret-like placeholder only says it is set."""
    if field.startswith("placeholders.") and any(word in field.lower() for word in _SECRET_KEY):
        return None if value is None else "set"
    return _setting(value)


def _plan_label(field: str, headings: dict, titles: dict) -> str:
    kind, _, rest = field.partition(".")
    if kind == "entries":
        index, _, attr = rest.partition(".")
        heading = headings.get(int(index))
        where = f"Template heading {index}" + (f" ({_short(heading)})" if heading else "")
        return f"{where} · {_ENTRY_LABELS[attr]}"
    if kind == "inserted":
        return f"Inserted section · {titles.get(rest, rest)}"
    if kind == "placeholders":
        return f"Placeholder · {rest}"
    return {"unmapped_sections": "Not placed", "notes": "Plan notes"}[field]


def _plan_context(project, doc):
    from routers import reports

    _data, outline = reports._bound(project, doc)
    return outline, {s.section_id: s.heading for s in doc.sections}


def _mapping_preview(args: dict) -> list[dict]:
    from routers import reports

    project, _report_id, doc, meta = _report_state(args, busy=_BUSY_PLAN)
    plan_arg = args.get("plan")
    if not isinstance(plan_arg, dict):
        _refuse("invalid_edit_value", "The mapping plan is a JSON object.")
    body = dict(plan_arg)
    if args.get("strict"):
        body["strict"] = True
    plan, outline = reports._validated_plan(project, doc, body)  # the route's own sanitiser
    titles = {s.section_id: s.heading for s in doc.sections}
    headings = {h.index: h.text for h in outline.headings}
    before, after = _plan_raw(meta.mapping_plan, outline, titles), _plan_raw(plan, outline, titles)
    typed = []
    for field in dict.fromkeys([*before, *after]):
        if _same(before.get(field), after.get(field)):
            continue
        old, new = _plan_cell(field, before.get(field)), _plan_cell(field, after.get(field))
        if old == "set" and new == "set":
            new = "set (changed)"  # a secret never shows, but a rotation is not "unchanged"
        label = _plan_label(field, headings, titles)
        typed.append(_row(field, label, old, new))
    if not typed:
        _refuse("empty_edit", "The report already has this mapping plan.")
    raw_notes = body.get("notes")
    dropped = (plan.get("notes") or [])[len(raw_notes) if isinstance(raw_notes, list) else 0:]
    notes = [_note("Settings", "Report", "mapping_plan", text) for text in dropped[:_MAX_NAMES]]
    if len(dropped) > _MAX_NAMES:
        notes.append(_note("Settings", "Report", "mapping_plan", f"{len(dropped) - _MAX_NAMES} more entries are dropped."))
    return _capped(typed, notes, "mapping plan entries")


def _mapping_readback(args: dict, rows: list[dict]) -> list[Any]:
    project, _report_id, doc, meta = _report_state(args)
    outline, titles = _plan_context(project, doc)
    stored = _plan_raw(meta.mapping_plan, outline, titles)
    out = []
    for row in rows:
        if row["role"] == "note":
            out.append(None)
            continue
        cell = _plan_cell(row["field"], stored.get(row["field"]))
        out.append(row["new"] if cell == "set" and row["new"] == "set (changed)" else cell)
    return out


register("set_report_mapping", Adapter("settings_edit", _mapping_preview, _mapping_readback, section_id=_SECTION))


# ── import_edited_report: an edited Word copy becomes the report's next version ──

_BUSY_MERGE = ("A report job is writing this project's reports: wait for it to finish (or abort "
               "it) before merging an edited copy.")
_SOURCE_WORDS = {"llm": "assistant-written", "user_edit": "your edit", "code": "computed"}


def _block_text(block) -> str:
    for attr in ("md", "text"):
        if isinstance(getattr(block, attr, None), str):
            return getattr(block, attr)
    return " ".join(getattr(block, "items", None) or [])


def _section_cell(section) -> str:
    blocks = list(section.blocks or [])
    words = sum(len(_block_text(b).split()) for b in blocks)
    return f"{_SOURCE_WORDS.get(section.source, section.source)} · {_plural(len(blocks), 'block')} · {_plural(words, 'word')}"


def _blocks_json(section) -> list:
    return [b.model_dump(mode="json") for b in section.blocks or []]


def _instruction_cell(section):
    return _setting(section.pending_instruction)


def _merge_state(project, report_id: str, args: dict):
    """The edited copy read against the latest version, and the merged next version (nothing saved)."""
    from pydantic import ValidationError

    from models.report import ReportDocument
    from routers import reports
    from services import upload_service
    from services.reports import roundtrip_service

    try:
        body = reports.RoundTripBody(file_id=args.get("file_id"),
                                     bind_as_template=bool(args.get("bind_as_template", True)))
    except ValidationError as exc:
        _refuse("invalid_edit_value", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                               for e in exc.errors(include_url=False)[:3]))
    meta = upload_service.get_upload_meta(project.name, body.file_id, project_dir=project.directory)
    data = upload_service.get_upload_bytes(project.name, body.file_id, project_dir=project.directory)
    base = reports._latest_or_404(project, report_id)
    with reports._roundtrip_refusals(meta, body.file_id, report_id):
        result = roundtrip_service.read_edited_copy(base, data)
        merged = roundtrip_service._merge_round_trip()(base, result)
        if not isinstance(merged, ReportDocument):
            merged = ReportDocument.model_validate(merged)
    return body, base, result, merged, data


def _roundtrip_preview(args: dict) -> list[dict]:
    project, report_id, _doc, meta = _report_state(args, busy=_BUSY_MERGE)
    body, base, result, merged, data = _merge_state(project, report_id, args)
    typed, notes = [], []
    old_by_id = {s.section_id: s for s in base.sections}
    in_copy = {s.section_id for s in result.sections if s.section_id}
    absent = []
    for section in merged.sections:
        old = old_by_id.get(section.section_id)
        if old is None:
            continue
        if _blocks_json(old) != _blocks_json(section):
            typed.append(_row(f"sections.{section.section_id}", f"Section · {section.heading}",
                              _section_cell(old), _section_cell(section)))
        if not _same(old.pending_instruction, section.pending_instruction) or old.comments != section.comments:
            typed.append(_row(f"instructions.{section.section_id}", f"Pending instruction · {section.heading}",
                              _instruction_cell(old), _instruction_cell(section)))
        if section.section_id not in in_copy and old.note != section.note:
            absent.append(section.heading)
    template_changed = False
    if body.bind_as_template and base.template_file_id != body.file_id:
        template_changed = True
        typed.append(_row("template_file_id", "Template", _upload_name(project, base.template_file_id),
                          _upload_name(project, body.file_id),
                          words={"old": _upload_name(project, base.template_file_id) or "Built-in default",
                                 "new": _upload_name(project, body.file_id)}))
        if meta.mapping_plan:
            typed.append(_row("mapping_plan", "Mapping plan", _plan_size(meta.mapping_plan), None,
                              words={"old": _plan_size(meta.mapping_plan), "new": "cleared"}))
    added_tables = [t for t in merged.tables if t not in base.tables]
    if not typed and not absent and not added_tables:
        _refuse("empty_edit", "The edited copy changes nothing in the report.")
    if absent:
        notes.append(_note("Settings", "Report", "sections",
                           f"{_plural(len(absent), 'section')} missing from the edited copy stay in the report with "
                           f"a note: {', '.join(_short(h) for h in absent[:_MAX_NAMES])}"
                           + (", …" if len(absent) > _MAX_NAMES else "") + "."))
    if added_tables:
        notes.append(_note("Settings", "Report", "tables", f"{_plural(len(added_tables), 'edited table')} are added."))
    if result.accepted_tracked_changes:
        notes.append(_note("Settings", "Report", "tracked_changes",
                           f"{_plural(result.accepted_tracked_changes, 'tracked change')} in the file are accepted."))
    if result.unmatched:
        notes.append(_note("Settings", "Report", "unmatched",
                           f"{_plural(len(result.unmatched), 'piece')} of content could not be placed in any section "
                           "and will NOT be merged."))
    version = _row("version", "Report version", base.version, merged.version)
    return [version, *_capped(typed, notes, "report sections")]


def _roundtrip_readback(args: dict, rows: list[dict]) -> list[Any]:
    from services.reports import store

    project, report_id, _doc, meta = _report_state(args)
    latest = store.load_report(project.directory, report_id)
    by_id = {s.section_id: s for s in latest.sections}
    out = []
    for row in rows:
        field = row["field"]
        kind, _, rest = field.partition(".")
        if row["role"] == "note":
            out.append(None)
        elif field == "version":
            out.append(latest.version)
        elif field == "template_file_id":
            out.append(_upload_name(project, latest.template_file_id))
        elif field == "mapping_plan":
            out.append(_plan_size(meta.mapping_plan))
        elif kind == "sections":
            out.append(_section_cell(by_id[rest]) if rest in by_id else None)
        else:
            out.append(_instruction_cell(by_id[rest]) if rest in by_id else None)
    return out


register("import_edited_report", Adapter("settings_edit", _roundtrip_preview, _roundtrip_readback,
                                         section_id=_SECTION))
