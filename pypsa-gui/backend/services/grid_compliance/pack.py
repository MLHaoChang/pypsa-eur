"""The report pack of one TRGC deliverable (GC-09).

A ZIP holding:

* ``report/TRGC-<code>.docx``: the report skeleton, rendered by
  ``services.reports.docx_writer`` (and ``.pdf`` via ``services.reports.pdf``
  when LibreOffice is installed; otherwise listed under ``skipped``);
* ``data_list.csv`` and ``revision_log.csv``;
* ``tables/<package>-<table>.csv``: the judged tables the feeding packages
  produced (``sources``); a package with no output is listed under
  ``pending`` and stated in the skeleton as not established;
* ``manifest.json``: the deliverable, the model set it was frozen on, and
  every other file with its byte size and sha256.

Each pack is recorded on the deliverable as a ``RevisionRef`` (version and
manifest digest), so a TSO response can cite the pack it went out in.

GC-TEXT-1: nothing in a pack carries a project, customer, consultant, EPC or
OEM name. The skeleton cites the TRGC deliverable and the public grid code
only; the project is not named.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

from models.report import (Bullets, Callout, Field, Paragraph, ReportDocument,
                           Section, Table, TableRef)
from services.reports import pdf as report_pdf
from services.reports.docx_writer import render_document_docx
from services.reports.revisions import RevisionRef

from . import catalogue as cat
from . import sources
from .store import DeliverableState, editing
from .tracker import data_hash, require_deliverable

#: Fixed timestamp for ZIP entries, so the archive depends on content only.
_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
GRID_CODE = ("Saudi Arabian Grid Code (SAGC) and its Technical Requirements "
             "for Grid Connection (TRGC) reports, published by SE / NGSA")

DATA_COLUMNS = ("item_id", "label", "supplier", "due", "status")
COMMENT_COLUMNS = ("comment_id", "date", "clause", "comment", "response",
                   "revision", "status", "pack_version")


def _csv(header: tuple[str, ...] | list[str], rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _skeleton(d: cat.Deliverable, entry: DeliverableState,
              tables: dict[str, Table], pending: list[str], version: int,
              digest: str | None, now: str) -> ReportDocument:
    sections = [Section(
        section_id="scope", heading="Scope", source="code", status="ok",
        blocks=[Paragraph(md=(
            f"This report is TRGC {d.code}, {d.title}, of the grid-connection "
            f"study chain (phase {d.phase}: {cat.PHASES[d.phase]}). It is "
            f"assessed against the {GRID_CODE}.")),
            Field(key="Study tool", value=d.tool),
            Bullets(items=[f"Requires TRGC {p}" for p in d.depends_on]
                    or ["No prerequisite deliverable"])])]
    freeze = entry.freeze
    freeze_blocks = []
    if freeze:
        freeze_blocks = [Field(key="Model set", value=freeze.model_set_id),
                         Field(key="Data hash", value=freeze.data_hash),
                         Field(key="Frozen", value=freeze.frozen_at)]
        if digest != freeze.data_hash:
            freeze_blocks.append(Callout(
                kind="gap", text="The campus input changed after this freeze."))
    sections.append(Section(
        section_id="model_set", heading="Model set and data freeze", source="code",
        status="ok" if freeze else "not_established",
        note=None if freeze else "The data has not been frozen for this deliverable.",
        blocks=freeze_blocks))
    sections.append(Section(
        section_id="inputs", heading="Input data", source="code", status="ok",
        blocks=[TableRef(table_id="data_list", caption="Data list")]))
    result_blocks = [TableRef(table_id=t, caption=tables[t].caption)
                     for t in tables if t not in ("data_list", "revision_log")]
    result_blocks += [Callout(kind="not_established",
                              text=f"Results from {p} are not in yet.")
                      for p in pending]
    sections.append(Section(
        section_id="results", heading="Results and judgment", source="code",
        status="ok" if not pending else "not_established",
        note=None if not pending else "Some feeding results are not in yet.",
        blocks=result_blocks))
    sections.append(Section(
        section_id="tso_comments", heading="TSO comments and responses",
        source="code", status="ok",
        blocks=[TableRef(table_id="revision_log", caption="Revision log")]))
    return ReportDocument(
        report_id=f"trgc-{d.code}", version=version,
        title=f"TRGC {d.code}: {d.title}", created_at=now,
        evidence_hash=(freeze.data_hash if freeze else "not-frozen"),
        mode="evidence_only", sections=sections, tables=tables)


def build(project, code: str, *, include_pdf: bool = True) -> tuple[bytes, dict]:
    """The pack ZIP and its manifest. Records the pack on the deliverable."""
    d = require_deliverable(code)
    digest = data_hash(project)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with editing(project) as tracker:
        entry = tracker.deliverables.setdefault(code, DeliverableState())
        version = len(entry.packs) + 1
        items = entry.data if entry.data is not None else [
            {"item_id": f"item-{i + 1}", "label": label, "supplier": "",
             "due": None, "status": "requested"}
            for i, label in enumerate(d.data_items)]
        data_rows = [[(i if isinstance(i, dict) else i.model_dump())[k] or ""
                      for k in DATA_COLUMNS] for i in items]
        comment_rows = [[c.comment_id, c.date, c.clause, c.comment, c.response,
                         c.revision, c.status, c.pack.version if c.pack else ""]
                        for c in entry.comments]
        tables: dict[str, Table] = {
            "data_list": Table(table_id="data_list", columns=list(DATA_COLUMNS),
                               rows=[[str(v) for v in r] for r in data_rows],
                               caption="Data list"),
            "revision_log": Table(table_id="revision_log",
                                  columns=list(COMMENT_COLUMNS),
                                  rows=[[str(v) for v in r] for r in comment_rows],
                                  caption="TSO comments and responses"),
        }
        pending: list[str] = []
        files: dict[str, bytes] = {}
        for package in d.packages:
            got = sources.tables(project, package)
            if got is None:
                pending.append(package)
                continue
            for t in got:
                key = f"{package}-{t.table_id}"
                tables[key] = t.model_copy(update={"table_id": key})
                files[f"tables/{key}.csv"] = _csv(t.columns, t.rows)
        doc = _skeleton(d, entry, tables, pending, version, digest, now)
        docx = render_document_docx(doc, figure_bytes={})
        files[f"report/TRGC-{code}.docx"] = docx
        skipped: list[str] = []
        if include_pdf and report_pdf.pdf_available():
            try:
                files[f"report/TRGC-{code}.pdf"] = report_pdf.convert_docx_to_pdf(docx)
            except report_pdf.PdfConversionError as exc:
                skipped.append(f"report/TRGC-{code}.pdf: {exc}")
        else:
            skipped.append(f"report/TRGC-{code}.pdf: PDF conversion is not available")
        files["data_list.csv"] = _csv(DATA_COLUMNS, data_rows)
        files["revision_log.csv"] = _csv(COMMENT_COLUMNS, comment_rows)
        freeze = entry.freeze
        manifest = {
            "schema_version": 1,
            "deliverable": {"trgc": d.code, "title": d.title, "phase": d.phase,
                            "tool": d.tool},
            "pack_version": version,
            "generated_at": now,
            "grid_code": GRID_CODE,
            "model_set": freeze.model_dump() if freeze else None,
            "stale": bool(freeze and freeze.data_hash != digest),
            "pending": pending,
            "skipped": skipped,
            "files": [{"path": p, "bytes": len(b), "sha256": _sha(b)}
                      for p, b in sorted(files.items())],
        }
        manifest_bytes = json.dumps(manifest, indent=1, sort_keys=True).encode()
        entry.packs.append(RevisionRef(version=version, digest=_sha(manifest_bytes)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in [("manifest.json", manifest_bytes), *sorted(files.items())]:
            z.writestr(zipfile.ZipInfo(path, _ZIP_TIME), data)
    return buf.getvalue(), manifest
