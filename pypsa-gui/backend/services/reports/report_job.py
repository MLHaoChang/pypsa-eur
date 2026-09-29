"""
The report generation job (WP3): the evidence-only document, enriched with
prose per section through the provider seam, audited, saved as a version.

Shape: the same one-job-at-a-time daemon-thread pattern as the Energy Hub
study (`services/adequacy/eh_study_runner.py` + `routers/results.py`):
a `record` dict published under `state["report_job"]` that the status route
serves minus `thread`/`stop_event`, a `stop_event` honoured between
sections, and a slot that refuses a second job while one runs.

What the job does NOT do:

* It never holds the PyPSA lock and never reads result state: the ROUTE
  collects the `Evidence` and renders the figures before the thread starts,
  so an LLM call can never sit inside a solver-state hold.
* It never writes a table or a figure: those are the collector's and WP4's.
  The model's prose goes IN FRONT of the code blocks the evidence-only
  section already had (its callouts, table and figure references, fields),
  which stay byte-identical.
* It never removes a required disclosure or an evidence gap: `assemble`
  inserted them as callouts, and a paragraph that repeats one is a
  repetition, not a replacement.

A section whose prose could not be produced keeps its code blocks, its
evidence status (an `ok` table is still `ok` evidence) and records the
failure in `note` — naming the profile and the model — and in the job's
`prose_failures`, which the meta records too.
"""
from __future__ import annotations

import contextvars
import json
import logging
import pathlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from models.report import Block, Bullets, Paragraph, ReportDocument, Section
from services.atomic_io import atomic_write_text
from services.reports import store
from services.reports.assemble import (
    DEFAULT_TITLE,
    EXECUTIVE_SUMMARY_ID,
    evidence_only_document,
)
from services.reports.evidence import Evidence, flatten_numbers, slice_for
from services.reports.generator import (
    SectionDraft,
    SectionFailure,
    generate_section,
)
from services.reports.number_audit import audit
from services.reports.prompts import system_blocks

logger = logging.getLogger(__name__)

THREAD_NAME = "report-generate"
# Per-section output cap when the profile sets none: a section is a few
# paragraphs, never a document.
SECTION_MAX_TOKENS = 2048
_PROSE_TYPES = ("paragraph", "bullets")
_FAILURE_PREFIX = "prose not established: "


class ReportJobInFlight(RuntimeError):
    """A report job is already running (→ 409 `report_job_in_flight`)."""


@dataclass
class ReportJob:
    project_name: str
    project_dir: pathlib.Path
    evidence: Evidence
    figure_pngs: dict[str, bytes]
    profile_id: str
    model: str
    max_tokens: int
    provider: Any
    language: str
    section_ids: list[str]
    instruction: str | None
    document: ReportDocument          # the document the job starts from
    regenerate_section: str | None
    report_id: str
    record: dict[str, Any]
    # True when the job creates `reports/<id>/` (v1); False when it appends
    # a version to an existing report (regenerate, or a base document).
    new_report: bool = True
    # WP11: the carried meta fields (`template_mode`, `template_language`) a
    # generate with `template_file_id` sets after ITS save — the document
    # itself carries `template_file_id`.
    meta_updates: dict[str, Any] = field(default_factory=dict)
    stop_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


# ── preparation ─────────────────────────────────────────────────────────────


def default_section_ids(document: ReportDocument, evidence: Evidence) -> list[str]:
    """
    The sections that get prose when the caller names none: the executive
    summary and every section the evidence established. A section that was
    not established keeps its code-only statement (there is nothing to
    narrate but the note, which the callout already carries), and the
    code-only surfaces (`adequacy_surfaces`, `pipeline`) stay tables.
    """
    out: list[str] = []
    for section in document.sections:
        sid = section.section_id
        if sid == EXECUTIVE_SUMMARY_ID:
            out.append(sid)
        elif sid in evidence.sections and evidence.sections[sid].status == "ok":
            out.append(sid)
    return out


def prepare_report_job(*, project_name: str, project_dir: pathlib.Path,
                       evidence: Evidence, figure_pngs: dict[str, bytes],
                       profile: Any, provider: Any, language: str = "en",
                       sections: list[str] | None = None,
                       instruction: str | None = None,
                       base_document: ReportDocument | None = None,
                       regenerate_section: str | None = None,
                       title: str | None = None,
                       template_file_id: str | None = None,
                       meta_updates: dict[str, Any] | None = None) -> ReportJob:
    """
    Everything `run_report_job` needs, validated, with the record it will
    publish. Raises `KeyError` for a section id the document does not have.
    `template_file_id` (WP11) binds a NEW report to a template; a base
    document keeps its own binding.
    """
    new_report = base_document is None
    if base_document is not None:
        document = base_document.model_copy(deep=True)
        report_id = document.report_id
    else:
        report_id = store.new_report_id()
        document = evidence_only_document(
            evidence, title=(title or "").strip() or f"{DEFAULT_TITLE} — {project_name}",
            report_id=report_id, figure_pngs=figure_pngs)
        if template_file_id is not None:
            document = document.model_copy(update={"template_file_id": template_file_id})
    known = {s.section_id for s in document.sections}
    if regenerate_section is not None:
        wanted = [regenerate_section]
    elif sections:
        wanted = [str(s) for s in sections]
    else:
        wanted = default_section_ids(document, evidence)
    for sid in wanted:
        if sid not in known:
            raise KeyError(sid)
    profile_id = str(getattr(profile, "id", "") or "")
    model = str(getattr(profile, "model", "") or "")
    max_tokens = int(getattr(profile, "max_output_tokens", None) or SECTION_MAX_TOKENS)
    record: dict[str, Any] = {
        "status": "running",
        "report_id": report_id,
        "version": None,
        "mode": "regenerate" if regenerate_section else "generate",
        "section": regenerate_section,
        "progress": {"done": 0, "total": len(wanted), "current": None},
        "repairs": 0,
        "prose_failures": [],
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": None,
        "profile_id": profile_id,
        "model": model,
    }
    job = ReportJob(
        project_name=project_name, project_dir=pathlib.Path(project_dir),
        evidence=evidence, figure_pngs=dict(figure_pngs or {}),
        profile_id=profile_id, model=model, max_tokens=max_tokens,
        provider=provider, language=language, section_ids=wanted,
        instruction=instruction, document=document,
        regenerate_section=regenerate_section, report_id=report_id,
        record=record, new_report=new_report,
        meta_updates=dict(meta_updates or {}),
    )
    record["stop_event"] = job.stop_event
    return job


# ── per section ─────────────────────────────────────────────────────────────


def _summary_slice(job: ReportJob, section: Section) -> dict[str, Any]:
    """
    The executive summary's evidence: the headline and every disclosure,
    gap and not-established line — the whole negative space, by design.
    """
    ev = job.evidence
    return {
        "section_id": EXECUTIVE_SUMMARY_ID,
        "title": section.heading,
        "kind": "summary",
        "status": section.status,
        "objective": ev.objective,
        "headline": ev.headline,
        "completeness": {sid: s.status for sid, s in ev.sections.items()},
        "required_disclosures": list(ev.required_disclosures),
        "evidence_gaps": list(ev.evidence_gaps),
        "not_established": list(ev.not_established),
        "table_ids": [b.table_id for b in section.blocks if b.type == "table_ref"],
    }


def _slice(job: ReportJob, section: Section) -> dict[str, Any]:
    if section.section_id == EXECUTIVE_SUMMARY_ID:
        return _summary_slice(job, section)
    return slice_for(job.evidence, section.section_id)


def _clean_note(note: str | None) -> str | None:
    """The evidence's own note, without an earlier run's failure prefix."""
    if note and note.startswith(_FAILURE_PREFIX):
        rest = note.split(" — ", 1)
        return rest[1] if len(rest) == 2 and rest[1] else None
    return note


def _with_prose(section: Section, draft: SectionDraft, facts) -> Section:
    prose: list[Block] = [Paragraph(md=p) for p in draft.paragraphs]
    if draft.bullets:
        prose.append(Bullets(items=list(draft.bullets)))
    code_blocks = [b for b in section.blocks if b.type not in _PROSE_TYPES]
    return section.model_copy(update={
        "source": "llm",
        "blocks": prose + code_blocks,
        "note": _clean_note(section.note),
        "audit": audit(list(draft.paragraphs) + list(draft.bullets), facts),
    })


def _with_failure(section: Section, reason: str, job: ReportJob) -> Section:
    failure = (f"{_FAILURE_PREFIX}{reason} (profile {job.profile_id}, "
               f"model {job.model})")
    base = _clean_note(section.note)
    code_blocks = [b for b in section.blocks if b.type not in _PROSE_TYPES]
    return section.model_copy(update={
        "source": "code",
        "blocks": code_blocks,
        "note": f"{failure} — {base}" if base else failure,
        "audit": section.audit.model_copy(update={"verified": [], "unverified": []}),
    })


def _set(job: ReportJob, **updates: Any) -> None:
    with job.lock:
        job.record.update(updates)


def _progress(job: ReportJob, done: int, current: str | None) -> None:
    with job.lock:
        job.record["progress"] = {"done": done, "total": len(job.section_ids),
                                  "current": current}


# ── the run ─────────────────────────────────────────────────────────────────


def _generate_all(job: ReportJob) -> tuple[ReportDocument, bool]:
    """Prose for every target section, in document order; `(doc, aborted)`."""
    doc = job.document
    facts = flatten_numbers(job.evidence)
    by_id = {s.section_id: i for i, s in enumerate(doc.sections)}
    targets = [sid for sid in job.section_ids if sid in by_id]
    base = {"model": job.model, "max_tokens": job.max_tokens,
            "system_blocks": system_blocks()}
    aborted = False
    done = 0
    pending = list(targets)
    while pending:
        sid = pending[0]
        if job.stop_event.is_set():
            aborted = True
            break
        _progress(job, done, sid)
        section = doc.sections[by_id[sid]]
        result = generate_section(
            job.provider, base_request=base, section_id=sid,
            title=section.heading, slice=_slice(job, section),
            language=job.language, instruction=job.instruction,
            stop_event=job.stop_event)
        repairs = int(getattr(result, "repairs", 0) or 0)
        with job.lock:
            job.record["repairs"] += repairs
        if isinstance(result, SectionDraft):
            doc.sections[by_id[sid]] = _with_prose(section, result, facts)
        else:
            assert isinstance(result, SectionFailure)
            if result.reason == "aborted":
                aborted = True
                break
            doc.sections[by_id[sid]] = _with_failure(section, result.reason, job)
            with job.lock:
                job.record["prose_failures"].append({
                    "section_id": sid, "reason": result.reason,
                    "raw_head": result.raw_head, "repairs": repairs})
        pending.pop(0)
        done += 1
        _progress(job, done, None)
    if aborted:
        for sid in pending:
            section = doc.sections[by_id[sid]]
            doc.sections[by_id[sid]] = _with_failure(section, "aborted", job)
        _progress(job, done, None)
    return doc, aborted


def _write_generation_meta(job: ReportJob) -> None:
    """
    `meta.json` gains a `generation` object (repairs, prose failures, the
    sections written). `ReportMeta` ignores unknown keys, so WP1's readers
    are unaffected; a later `save_version` rewrites the meta from the model
    and drops it, which is why the same facts also live in the job record.
    """
    path = store.report_dir(job.project_dir, job.report_id) / "meta.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("reports: meta.json unreadable after save (%s)", path)
        return
    with job.lock:
        raw["generation"] = {
            "repairs": job.record["repairs"],
            "prose_failures": list(job.record["prose_failures"]),
            "sections": list(job.section_ids),
            "language": job.language,
            "aborted": job.record["status"] == "aborted",
        }
    atomic_write_text(path, json.dumps(raw, indent=2, ensure_ascii=False))


def run_report_job(job: ReportJob) -> ReportDocument:
    """
    The worker body: generate, audit, save, publish the outcome on the
    record. Returns the saved document; raises after marking the record
    `failed` (the thread wrapper logs, a direct caller sees the exception).
    """
    try:
        doc, aborted = _generate_all(job)
        doc = doc.model_copy(update={
            "mode": "generated",
            "profile_id": job.profile_id or None,
            "model": job.model or None,
            "language": job.language,
            "created_at": datetime.now(tz=UTC).isoformat(),
        })
        if job.new_report:
            store.create_report(job.project_dir, doc)
            for figure_id, png in job.figure_pngs.items():
                if figure_id in doc.figures:
                    store.write_figure(job.project_dir, job.report_id, figure_id, png)
            version = 1
        else:
            version = store.save_version(job.project_dir, doc)
        if job.meta_updates:
            store.update_meta(job.project_dir, job.report_id, **job.meta_updates)
        saved = store.load_report(job.project_dir, job.report_id, version)
        _set(job, status="aborted" if aborted else "done", version=version,
             error=None, finished_at=time.time())
        _write_generation_meta(job)
        return saved
    except Exception as exc:
        _set(job, status="failed", error=str(exc), finished_at=time.time())
        raise


# ── the mapping job (WP11) ──────────────────────────────────────────────────
#
# The second job kind the slot serves: ONE generation call that proposes how
# an untagged template's headings map onto the report's sections
# (`template_untagged.propose_mapping`, reached through the accessors in
# `services/reports/templates.py`). It writes no version — the plan goes to
# `ReportMeta.mapping_plan` — and its record has `mode: "mapping"`, so the
# status and abort routes cover it unchanged. A `SectionFailure` from the
# proposal is not a failed job: the code-only `default_mapping` is stored and
# the failure is recorded in `prose_failures` as section `"mapping"`.

MAPPING_SECTION_ID = "mapping"


@dataclass
class MappingJob:
    project_name: str
    project_dir: pathlib.Path
    report_id: str
    document: ReportDocument
    outline: Any                      # docx_reader.TemplateOutline
    profile_id: str
    model: str
    max_tokens: int
    provider: Any
    language: str
    record: dict[str, Any]
    stop_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


def prepare_mapping_job(*, project_name: str, project_dir: pathlib.Path,
                        document: ReportDocument, outline: Any, profile: Any,
                        provider: Any, language: str) -> MappingJob:
    profile_id = str(getattr(profile, "id", "") or "")
    model = str(getattr(profile, "model", "") or "")
    max_tokens = int(getattr(profile, "max_output_tokens", None) or SECTION_MAX_TOKENS)
    record: dict[str, Any] = {
        "status": "running",
        "report_id": document.report_id,
        "version": document.version,
        "mode": "mapping",
        "section": None,
        "progress": {"done": 0, "total": 1, "current": MAPPING_SECTION_ID},
        "repairs": 0,
        "prose_failures": [],
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "thread": None,
        "stop_event": None,
        "profile_id": profile_id,
        "model": model,
    }
    job = MappingJob(
        project_name=project_name, project_dir=pathlib.Path(project_dir),
        report_id=document.report_id, document=document, outline=outline,
        profile_id=profile_id, model=model, max_tokens=max_tokens,
        provider=provider, language=language, record=record,
    )
    record["stop_event"] = job.stop_event
    return job


def run_mapping_job(job: MappingJob) -> dict[str, Any] | None:
    """
    The worker body: propose, sanitise, store on the meta, publish the
    outcome. Returns the stored plan (None when aborted before the call).
    """
    from services.reports import templates

    try:
        if job.stop_event.is_set():
            with job.lock:
                job.record.update(status="aborted", finished_at=time.time(),
                                  progress={"done": 0, "total": 1, "current": None})
            return None
        base = {"model": job.model, "max_tokens": job.max_tokens,
                "system_blocks": system_blocks()}
        result = templates._propose_mapping()(
            job.provider, base_request=base, outline=job.outline,
            doc=job.document, language=job.language)
        repairs = int(getattr(result, "repairs", 0) or 0)
        with job.lock:
            job.record["repairs"] += repairs
        if isinstance(result, SectionFailure):
            with job.lock:
                job.record["prose_failures"].append({
                    "section_id": MAPPING_SECTION_ID, "reason": result.reason,
                    "raw_head": result.raw_head, "repairs": repairs})
            proposed = templates._default_mapping()(job.outline, job.document)
        else:
            proposed = result
        raw = proposed.model_dump() if hasattr(proposed, "model_dump") else dict(proposed)
        plan, _notes = templates.sanitise_plan(raw, job.outline, job.document)
        store.update_meta(job.project_dir, job.report_id, mapping_plan=plan)
        with job.lock:
            job.record.update(status="done", error=None, finished_at=time.time(),
                              progress={"done": 1, "total": 1, "current": None})
        return plan
    except Exception as exc:
        with job.lock:
            job.record.update(status="failed", error=str(exc), finished_at=time.time())
        raise


# ── the slot and the thread ─────────────────────────────────────────────────

_SLOT_LOCK = threading.Lock()
_ACTIVE: dict[str, Any] | None = None


def record_is_running(record: dict[str, Any] | None) -> bool:
    if not record or record.get("status") != "running":
        return False
    t = record.get("thread")
    if t is None:
        return True  # published, not yet started
    return t.ident is None or t.is_alive()


def _launch(*, prepare, run, lock: threading.Lock, state: dict[str, Any]) -> str:
    """
    Claim the slot and start the daemon thread for one job, under ONE hold of
    `lock` so the claim is a claim: `prepare()` builds the job (its record is
    published under `state["report_job"]`), `run(job)` is the worker body.
    One job per process: a second start while one runs raises
    `ReportJobInFlight`. `prepare()`'s own exceptions (a `KeyError` for an
    unknown section) propagate before anything is claimed.
    """
    global _ACTIVE
    with lock:
        with _SLOT_LOCK:
            if record_is_running(state.get("report_job")) or record_is_running(_ACTIVE):
                raise ReportJobInFlight("a report is already being generated")
            job = prepare()

            def worker() -> None:
                try:
                    run(job)
                except Exception:  # noqa: BLE001 — recorded on the job
                    logger.exception("report job %s failed", job.report_id)

            ctx = contextvars.copy_context()
            thread = threading.Thread(target=lambda: ctx.run(worker),
                                      daemon=True, name=THREAD_NAME)
            job.record["thread"] = thread
            state["report_job"] = job.record
            _ACTIVE = job.record
            try:
                thread.start()
            except BaseException:
                state["report_job"] = None
                _ACTIVE = None
                raise
    return job.report_id


def start_report_job(*, project_name: str, project_dir: pathlib.Path,
                     evidence: Evidence, figure_pngs: dict[str, bytes],
                     profile: Any, provider: Any, language: str = "en",
                     sections: list[str] | None = None,
                     instruction: str | None = None,
                     base_document: ReportDocument | None = None,
                     regenerate_section: str | None = None,
                     title: str | None = None,
                     template_file_id: str | None = None,
                     meta_updates: dict[str, Any] | None = None,
                     lock: threading.Lock, state: dict[str, Any]) -> str:
    """
    Publish the record under `state["report_job"]` and start the daemon
    thread (see `_launch`). Raises `KeyError` for an unknown section id,
    before anything is claimed.
    """
    return _launch(
        prepare=lambda: prepare_report_job(
            project_name=project_name, project_dir=project_dir,
            evidence=evidence, figure_pngs=figure_pngs, profile=profile,
            provider=provider, language=language, sections=sections,
            instruction=instruction, base_document=base_document,
            regenerate_section=regenerate_section, title=title,
            template_file_id=template_file_id, meta_updates=meta_updates),
        run=run_report_job, lock=lock, state=state)


def start_mapping_job(*, project_name: str, project_dir: pathlib.Path,
                      document: ReportDocument, outline: Any, profile: Any,
                      provider: Any, language: str,
                      lock: threading.Lock, state: dict[str, Any]) -> str:
    """The mapping kind on the same slot: `state["report_job"]["mode"] == "mapping"`."""
    return _launch(
        prepare=lambda: prepare_mapping_job(
            project_name=project_name, project_dir=project_dir, document=document,
            outline=outline, profile=profile, provider=provider, language=language),
        run=run_mapping_job, lock=lock, state=state)


def public_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """The record for the wire: no thread handle, no event."""
    if not record:
        return None
    return {k: v for k, v in record.items() if k not in ("thread", "stop_event")}
