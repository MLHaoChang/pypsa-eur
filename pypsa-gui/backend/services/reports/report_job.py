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

from models.report import Block, Bullets, Callout, Paragraph, ReportDocument, Section
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
from services.reports.formatting import is_withheld_block, withheld_placeholder
from services.reports.number_audit import audit
from services.reports.prompts import system_blocks

logger = logging.getLogger(__name__)

THREAD_NAME = "report-generate"
# Per-section output cap when the profile sets none: a section is a few
# paragraphs, never a document.
SECTION_MAX_TOKENS = 2048
_PROSE_TYPES = ("paragraph", "bullets")
_FAILURE_PREFIX = "prose not established: "
# WP12's additive field: a comment the user left in Word on this section,
# stored as the instruction for its next regeneration (increment 3). Read
# with `getattr` and cleared only when the field exists, so this module runs
# before and after the model gains it.
PENDING_INSTRUCTION_FIELD = "pending_instruction"


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
    # Study harness issue 07: a run's report. Each section's prose may quote
    # only the numbers of its own slice (its admitted facts, at most 12 for a
    # network chapter), and a paragraph or bullet with a number the audit
    # cannot verify against them is withheld, not flagged.
    strict: bool = False
    # (task path, boot): the run whose lease admits each send (C4, issue 24).
    run_binding: tuple[pathlib.Path, str] | None = None


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
                       meta_updates: dict[str, Any] | None = None,
                       strict: bool = False,
                       run_binding: tuple[pathlib.Path, str] | None = None) -> ReportJob:
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
        meta_updates=dict(meta_updates or {}), strict=strict,
        run_binding=run_binding,
    )
    if run_binding is not None:
        job.provider = RunSends(provider, *run_binding)
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


def pending_instruction_of(section: Section) -> str | None:
    """The section's `pending_instruction` (WP12), or None when absent or blank."""
    value = getattr(section, PENDING_INSTRUCTION_FIELD, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _without_pending(section: Section) -> Section:
    """The section with its pending instruction consumed (a no-op before WP12)."""
    if PENDING_INSTRUCTION_FIELD in Section.model_fields \
            and getattr(section, PENDING_INSTRUCTION_FIELD, None) is not None:
        return section.model_copy(update={PENDING_INSTRUCTION_FIELD: None})
    return section


def _code_blocks(section: Section) -> list[Block]:
    """The section's code-written blocks: not its prose, and not a strict
    job's withheld placeholder (it stood for prose that is written anew)."""
    return [b for b in section.blocks if b.type not in _PROSE_TYPES and not is_withheld_block(b)]


def _with_prose(section: Section, draft: SectionDraft, facts) -> Section:
    prose: list[Block] = [Paragraph(md=p) for p in draft.paragraphs]
    if draft.bullets:
        prose.append(Bullets(items=list(draft.bullets)))
    code_blocks = _code_blocks(section)
    return section.model_copy(update={
        "source": "llm",
        "blocks": prose + code_blocks,
        "note": _clean_note(section.note),
        "audit": audit(list(draft.paragraphs) + list(draft.bullets), facts),
    })


class RunSends:
    """A harness run's report sends (GK B C4): each one is reserved through
    `send_reservation.admit` under the run's live lease before the provider
    is called (`ReservingProvider`), and settled after. A refusal makes no
    provider call; its reason is kept in `refused` and the job stops."""

    def __init__(self, inner: Any, task_path: pathlib.Path, boot: str) -> None:
        from harness.send_reservation import ReservingProvider

        self._inner = ReservingProvider(inner, task_path, boot=boot)
        self.name = getattr(inner, "name", "run_report")
        self.refused: str | None = None

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)

    def stream(self, request: Any):
        from harness.send_reservation import SendRefused

        try:
            yield from self._inner.stream(request)
        except SendRefused as exc:
            self.refused = exc.reason
            raise

    def close(self) -> None:
        """Job end (GK B C4): a hold one of this job's sends left open (the
        provider raised before answering, so `ReservingProvider` kept it for a
        retry that will not come) settles `uncertain` at its bound, the
        runner's turn-end rule (`send_reservation.close_open`), limited to this
        job's own sends."""
        self._inner.settle_open()


AUDIT_FAILED = "(the number audit could not read this statement)"
WITHHELD_NOTE = ("{n} statement(s) withheld: each quoted a number that is not among "
                 "this section's admitted facts.")


def admitted_facts(job: ReportJob, section: Section):
    """The numbers one section's prose may quote in a strict job: those of
    its own slice (payload and headline), nothing from another section."""
    from services.reports.evidence import _walk

    sl = _slice(job, section)
    out: list = []
    _walk(sl.get("payload") or {}, f"/sections/{section.section_id}/payload", "", out)
    _walk(sl.get("headline") or {}, "/headline", "", out)
    return out


def withhold_unverified(draft: SectionDraft, facts,
                        dropped: dict[str, list[int]] | None = None) -> tuple[SectionDraft, list[str]]:
    """The draft without each paragraph or bullet the number audit cannot
    verify against `facts`, and the numbers that withheld them. `dropped`,
    when given, receives per kind ("paragraphs", "bullets") the count of
    numbers each statement was withheld for, None for a kept one (0: the
    audit could not read it), so the caller can place a placeholder there."""
    tokens: list[str] = []
    marks: dict[str, list] = {"paragraphs": [], "bullets": []}

    def keep(text: str, kind: str) -> bool:
        try:
            bad = list(audit([text], facts).unverified)
            count = len(bad)
        except Exception:  # noqa: BLE001 -- fail closed (GK B C5): an audit that cannot run admits nothing
            logger.warning("report audit failed on a statement; it is withheld")
            bad = [AUDIT_FAILED]
            count = 0
        tokens.extend(bad)
        marks[kind].append(count if bad else None)
        return not bad
    kept = draft.model_copy(update={"paragraphs": [p for p in draft.paragraphs if keep(p, "paragraphs")],
                                    "bullets": [b for b in draft.bullets if keep(b, "bullets")]})
    if dropped is not None:
        dropped.update(marks)
    return kept, tokens


def _with_placeholders(section: Section, marks: dict[str, list]) -> Section:
    """`section` with a placeholder callout (V1) where each withheld
    paragraph stood, and after the bullets for each withheld bullet."""
    paragraphs = iter([b for b in section.blocks if b.type == "paragraph"])
    prose: list[Block] = []
    for mark in marks.get("paragraphs") or []:
        if mark is None:
            prose.append(next(paragraphs))
        else:
            prose.append(Callout(kind="gap", text=withheld_placeholder(mark)))
    prose.extend(paragraphs)
    prose.extend(b for b in section.blocks if b.type == "bullets")
    prose.extend(Callout(kind="gap", text=withheld_placeholder(mark))
                 for mark in marks.get("bullets") or [] if mark is not None)
    code_blocks = _code_blocks(section)
    return section.model_copy(update={"blocks": prose + code_blocks})


def _with_failure(section: Section, reason: str, job: ReportJob) -> Section:
    failure = (f"{_FAILURE_PREFIX}{reason} (profile {job.profile_id}, "
               f"model {job.model})")
    base = _clean_note(section.note)
    code_blocks = _code_blocks(section)
    return section.model_copy(update={
        "source": "code",
        "blocks": code_blocks,
        "note": f"{failure} — {base}" if base else failure,
        "audit": section.audit.model_copy(update={"verified": [], "unverified": []}),
    })


def _publish_report_evidence(record: dict[str, Any]) -> None:
    """Best-effort issue-06 run evidence for generate/regenerate/mapping."""
    try:
        from services import run_evidence as _re
        status = record.get("status")
        if status == "running" and "_run_evidence" not in record:
            _re.start_report_run(record)
        elif status in ("done", "failed", "aborted"):
            _re.publish_report_run(record)
    except Exception:  # noqa: BLE001
        pass


def _set(job: ReportJob, **updates: Any) -> None:
    with job.lock:
        job.record.update(updates)
        _publish_report_evidence(job.record)


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
        if isinstance(result, SectionDraft) and job.strict:
            own = admitted_facts(job, section)
            marks: dict[str, list] = {}
            kept, withheld = withhold_unverified(result, own, marks)
            try:
                written = _with_placeholders(_with_prose(section, kept, own), marks)
            except Exception:  # noqa: BLE001 -- fail closed (C5): no prose rather than an unaudited one
                logger.warning("report audit failed on section %s; its prose is withheld", sid)
                kept = kept.model_copy(update={"paragraphs": [], "bullets": []})
                withheld = withheld or [AUDIT_FAILED]
                written = section.model_copy(update={
                    "source": "code", "note": _clean_note(section.note),
                    "blocks": ([Callout(kind="gap", text=withheld_placeholder(0))]
                               + _code_blocks(section))})
            if withheld:
                n = len(result.paragraphs) + len(result.bullets) - len(kept.paragraphs) - len(kept.bullets)
                note = WITHHELD_NOTE.format(n=n)
                written = written.model_copy(update={
                    "note": f"{written.note} {note}" if written.note else note,
                    "audit": written.audit.model_copy(update={"unverified": withheld})})
                with job.lock:
                    job.record.setdefault("withheld", []).append({"section_id": sid, "statements": n})
        elif isinstance(result, SectionDraft):
            written = _with_prose(section, result, facts)
        else:
            assert isinstance(result, SectionFailure)
            if result.reason == "aborted":
                aborted = True
                break
            refused = getattr(job.provider, "refused", None)
            if refused is not None:
                # C4: the run's reservation refused the send; nothing more goes out.
                with job.lock:
                    job.record["refused"] = refused
                for rest in pending:
                    doc.sections[by_id[rest]] = _with_failure(doc.sections[by_id[rest]], f"send refused ({refused})", job)
                _progress(job, done, None)
                return doc, False
            written = _with_failure(section, result.reason, job)
            with job.lock:
                job.record["prose_failures"].append({
                    "section_id": sid, "reason": result.reason,
                    "raw_head": result.raw_head, "repairs": repairs})
        # A regenerate consumes the section's pending instruction (the
        # comment the user left in Word) whether it was the instruction used
        # or an explicit one replaced it: the new version answers it.
        if job.regenerate_section == sid:
            written = _without_pending(written)
        doc.sections[by_id[sid]] = written
        pending.pop(0)
        done += 1
        _progress(job, done, None)
    if aborted:
        for sid in pending:
            section = doc.sections[by_id[sid]]
            doc.sections[by_id[sid]] = _with_failure(section, "aborted", job)
        _progress(job, done, None)
    return doc, aborted


def _write_generation_meta(job: ReportJob, *, aborted: bool) -> None:
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
            "aborted": aborted,
        }
    atomic_write_text(path, json.dumps(raw, indent=2, ensure_ascii=False))


# AA-05 S4 (P1, V2): the assistant's report tools run the job strict. The
# switch is this context variable, set in-process by the chat tool around
# its route call, so no query, body or form field of the human routes can
# reach it (`routers/report_jobs._start` reads it).
_ASSISTANT_STRICT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "report_assistant_strict", default=False)


class assistant_strict:  # noqa: N801 -- used as `with assistant_strict():`
    """Run the enclosed report-route call strict (the assistant's tools)."""

    def __enter__(self) -> None:
        self._token = _ASSISTANT_STRICT.set(True)

    def __exit__(self, *exc: object) -> None:
        _ASSISTANT_STRICT.reset(self._token)


def assistant_strict_active() -> bool:
    return _ASSISTANT_STRICT.get()


class ReportDisclosureMissing(Exception):
    """A strict job's or export's output lost a required disclosure (P4)."""

    kind = "report_disclosure_missing"


def disclosure_texts(doc: ReportDocument) -> list[str]:
    """The required-disclosure callouts of `doc`, in document order."""
    return [b.text for s in doc.sections for b in s.blocks
            if b.type == "callout" and b.kind == "disclosure"]


def missing_disclosures(required: list[str], present: list[str] | str) -> list[str]:
    """Each of `required` that `present` does not carry as often (V5).
    `present` is the written document's disclosure texts, or an export's
    body text, where each one must occur as often as it is required."""
    if isinstance(present, str):
        need: dict[str, int] = {}
        for text in required:
            need[text] = need.get(text, 0) + 1
        return [text for text, n in need.items() for _ in range(max(0, n - present.count(text)))]
    left = list(present)
    missing = []
    for text in required:
        if text in left:
            left.remove(text)
        else:
            missing.append(text)
    return missing


def run_report_job(job: ReportJob) -> ReportDocument:
    """
    The worker body: generate, audit, save, publish the outcome on the
    record. Returns the saved document; raises after marking the record
    `failed` (the thread wrapper logs, a direct caller sees the exception).

    A strict job checks before it saves that every required disclosure of
    the version it started from (the evidence's skeleton for a generate,
    the stored version for a regenerate, V5) is still in the document; a
    lost one fails the job with `report_disclosure_missing` and saves
    nothing.
    """
    required = disclosure_texts(job.document) if job.strict else []
    try:
        doc, aborted = _generate_all(job)
        lost = missing_disclosures(required, disclosure_texts(doc)) if job.strict else []
        if lost:
            raise ReportDisclosureMissing(
                f"{len(lost)} required disclosure(s) are missing from the written report; "
                "nothing was saved.")
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
        # Terminal status lets clients consume the generation receipt or
        # start another job, so publish it only after that receipt is saved.
        _write_generation_meta(job, aborted=aborted)
        _set(job, status="aborted" if aborted else "done", version=version,
             error=None, finished_at=time.time())
        return saved
    except Exception as exc:
        kind = {"error_kind": exc.kind} if isinstance(exc, ReportDisclosureMissing) else {}
        _set(job, status="failed", error=str(exc), finished_at=time.time(), **kind)
        raise
    finally:
        if isinstance(job.provider, RunSends):
            job.provider.close()
        if job.run_binding is not None:
            _settle_run_step(job)


def _settle_run_step(job: ReportJob) -> None:
    """Study harness 07 (B1-c): the job's outcome settles its run's report
    step as the job ends, not only on the runner's next tick, which a run
    stopped meanwhile (the budget) never gets."""
    from harness import run_report

    with job.lock:
        record = dict(job.record)
    try:
        run_report.settle_record(job.run_binding[0], record, owner=job.run_binding[1])
    except Exception:  # noqa: BLE001 -- the runner's tick settles it again
        logger.exception("report job %s could not settle its run step", job.report_id)


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
                _publish_report_evidence(job.record)
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
            _publish_report_evidence(job.record)
        return plan
    except Exception as exc:
        with job.lock:
            job.record.update(status="failed", error=str(exc), finished_at=time.time())
            _publish_report_evidence(job.record)
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


def slot_is_busy(state: dict[str, Any]) -> bool:
    """
    True while a report job (generate, regenerate or mapping) is running —
    the same test `_launch` makes before claiming the slot. The synchronous
    round-trip route asks this under the solver-state lock before it writes
    a version, so it never races a job that is about to save one.
    """
    with _SLOT_LOCK:
        return record_is_running(state.get("report_job")) or record_is_running(_ACTIVE)


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
            _publish_report_evidence(job.record)
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
                     strict: bool = False,
                     run_binding: tuple[pathlib.Path, str] | None = None,
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
            template_file_id=template_file_id, meta_updates=meta_updates, strict=strict,
            run_binding=run_binding),
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
    """The record for the wire: no thread handle, no event, no run-evidence token."""
    if not record:
        return None
    from services import run_evidence
    return run_evidence.wire_record(record)
