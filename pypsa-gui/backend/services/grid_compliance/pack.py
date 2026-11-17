"""The report pack of one TRGC deliverable (GC-09).

A ZIP holding:

* ``report/TRGC-<code>.docx``: the report skeleton, rendered by
  ``services.reports.docx_writer`` (and ``.pdf`` via ``services.reports.pdf``
  when LibreOffice is installed; otherwise listed under ``skipped``);
* ``data_list.csv`` and ``revision_log.csv``;
* ``tables/<package>-<table>.csv``: the judged tables the feeding packages
  produced (``sources``); a package with no output is ``pending`` in the
  manifest and stated in the skeleton as not established;
* ``manifest.json``: the deliverable, the model set it was frozen on, each
  feeding source's state, what was skipped, and every other file with its
  byte size and sha256.

The pack is built from a snapshot of the tracker taken under its lock, then
rendered outside it (DOCX and PDF conversion are slow), then recorded on the
deliverable as a ``RevisionRef`` (version and manifest digest) under the lock
again, so a TSO response can cite the pack it went out in. A pack recorded by
someone else in between is ``gc_pack_conflict``; nothing is recorded for a
pack that failed to build.

The model user guides (09, 10) and plant datasheets (11) carry their own
sections, filled from the model set and the export packs' manifests as those
land; until then each section states what it waits for.

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

from models.report import (
    Bullets,
    Callout,
    Field,
    Paragraph,
    ReportDocument,
    Section,
    Table,
    TableRef,
)
from services.reports import pdf as report_pdf
from services.reports.docx_writer import render_document_docx
from services.reports.revisions import RevisionRef

from . import catalogue as cat
from . import sources
from .store import DeliverableState, GridComplianceError, editing, load
from .tracker import data_hash, default_items, freeze_state, now, require_deliverable

#: Fixed timestamp for ZIP entries, so the archive depends on content only.
_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
GRID_CODE = (
    "Saudi Arabian Grid Code (SAGC) and its Technical Requirements "
    "for Grid Connection (TRGC) reports, published by SE / NGSA"
)

DATA_COLUMNS = ("item_id", "label", "supplier", "due", "status")
COMMENT_COLUMNS = (
    "comment_id",
    "date",
    "clause",
    "comment",
    "response",
    "revision",
    "status",
    "pack_version",
)

#: Sections of the documents GC-09 generates itself, with what fills them.
DOCUMENT_SECTIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "09": (
        ("model_overview", "RMS model overview", "GC-01"),
        ("model_parameters", "Model parameters and settings", "GC-04"),
        ("model_usage", "Running the RMS model", "GC-04"),
    ),
    "10": (
        ("model_overview", "EMT model overview", "GC-01"),
        ("model_parameters", "Model parameters and settings", "GC-06"),
        ("model_usage", "Running the EMT model", "GC-06"),
    ),
    "11": (
        ("plant_data", "Plant and unit data", "GC-01"),
        ("transformer_data", "Transformer and collector data", "GC-01"),
        ("control_data", "Plant controller data", "GC-01"),
    ),
}


def _csv(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pending_callout(package: str) -> Callout:
    return Callout(
        kind="not_established", text=f"The {cat.PACKAGE_LABELS[package]} is not in yet."
    )


def _skeleton(
    d: cat.Deliverable,
    entry: DeliverableState,
    fstate: str,
    tables: dict[str, Table],
    pending: list[str],
    version: int,
    stamp: str,
) -> ReportDocument:
    sections = [
        Section(
            section_id="scope",
            heading="Scope",
            source="code",
            status="ok",
            blocks=[
                Paragraph(
                    md=(
                        f"This report is TRGC {d.code}, {d.title}, of the grid-connection "
                        f"study chain (phase {d.phase}: {cat.PHASES[d.phase]}). It is "
                        f"assessed against the {GRID_CODE}."
                    )
                ),
                Field(key="Study tool", value=d.tool),
                Bullets(
                    items=[f"Requires TRGC {p}" for p in d.depends_on]
                    or ["No prerequisite deliverable"]
                ),
            ],
        )
    ]
    freeze = entry.freeze
    freeze_blocks = []
    if freeze:
        freeze_blocks = [
            Field(key="Model set", value=freeze.model_set_id),
            Field(key="Data hash", value=freeze.data_hash),
            Field(key="Frozen", value=freeze.frozen_at),
        ]
        if fstate != "fresh":
            freeze_blocks.append(
                Callout(
                    kind="gap",
                    text=f"Freeze state: {fstate.replace('_', ' ')}. "
                    "The inputs no longer match this freeze.",
                )
            )
    sections.append(
        Section(
            section_id="model_set",
            heading="Model set and data freeze",
            source="code",
            status="ok" if freeze else "not_established",
            note=None
            if freeze
            else "The data has not been frozen for this deliverable.",
            blocks=freeze_blocks,
        )
    )
    for section_id, heading, package in DOCUMENT_SECTIONS.get(d.code, ()):
        sections.append(
            Section(
                section_id=section_id,
                heading=heading,
                source="code",
                status="not_established",
                note="Generated once its source is in.",
                blocks=[_pending_callout(package)],
            )
        )
    sections.append(
        Section(
            section_id="inputs",
            heading="Input data",
            source="code",
            status="ok",
            blocks=[TableRef(table_id="data_list", caption="Data list")],
        )
    )
    result_blocks = [
        TableRef(table_id=t, caption=tables[t].caption)
        for t in tables
        if t not in ("data_list", "revision_log")
    ]
    result_blocks += [_pending_callout(p) for p in pending]
    sections.append(
        Section(
            section_id="results",
            heading="Results and judgment",
            source="code",
            status="not_established" if pending else "ok",
            note="Some feeding results are not in yet." if pending else None,
            blocks=result_blocks,
        )
    )
    sections.append(
        Section(
            section_id="tso_comments",
            heading="TSO comments and responses",
            source="code",
            status="ok",
            blocks=[TableRef(table_id="revision_log", caption="Revision log")],
        )
    )
    return ReportDocument(
        report_id=f"trgc-{d.code}",
        version=version,
        title=f"TRGC {d.code}: {d.title}",
        created_at=stamp,
        evidence_hash=(freeze.data_hash if freeze else "not-frozen"),
        mode="evidence_only",
        sections=sections,
        tables=tables,
    )


def _render(
    project,
    d: cat.Deliverable,
    entry: DeliverableState,
    version: int,
    include_pdf: bool,
) -> tuple[bytes, dict]:
    stamp = now()
    fstate = freeze_state(entry.freeze, data_hash(project))
    items = entry.data if entry.data is not None else default_items(d)
    data_rows = [[str(getattr(i, k) or "") for k in DATA_COLUMNS] for i in items]
    comment_rows = [
        [
            c.comment_id,
            c.date,
            c.clause,
            c.comment,
            c.response,
            c.revision,
            c.status,
            str(c.pack.version) if c.pack else "",
        ]
        for c in entry.comments
    ]
    tables: dict[str, Table] = {
        "data_list": Table(
            table_id="data_list",
            columns=list(DATA_COLUMNS),
            rows=data_rows,
            caption="Data list",
        ),
        "revision_log": Table(
            table_id="revision_log",
            columns=list(COMMENT_COLUMNS),
            rows=comment_rows,
            caption="TSO comments and responses",
        ),
    }
    files: dict[str, bytes] = {}
    source_states, pending = [], []
    for package in d.packages:
        got = sources.tables(project, package)
        source_states.append(
            {
                "package": package,
                "label": cat.PACKAGE_LABELS[package],
                "state": "pending" if got is None else "present",
            }
        )
        if got is None:
            pending.append(package)
            continue
        for t in got:
            key = f"{package}-{t.table_id}"
            tables[key] = t.model_copy(update={"table_id": key})
            files[f"tables/{key}.csv"] = _csv(t.columns, t.rows)
    docx = render_document_docx(
        _skeleton(d, entry, fstate, tables, pending, version, stamp), figure_bytes={}
    )
    files[f"report/TRGC-{d.code}.docx"] = docx
    skipped: list[dict] = []
    pdf_path = f"report/TRGC-{d.code}.pdf"
    if include_pdf and report_pdf.pdf_available():
        try:
            files[pdf_path] = report_pdf.convert_docx_to_pdf(docx)
        except report_pdf.PdfConversionError as exc:
            skipped.append({"path": pdf_path, "reason": str(exc)})
    else:
        skipped.append({"path": pdf_path, "reason": "PDF conversion is not available"})
    files["data_list.csv"] = _csv(DATA_COLUMNS, data_rows)
    files["revision_log.csv"] = _csv(COMMENT_COLUMNS, comment_rows)
    manifest = {
        "schema_version": 1,
        "deliverable": {
            "trgc": d.code,
            "title": d.title,
            "phase": d.phase,
            "tool": d.tool,
        },
        "pack_version": version,
        "generated_at": stamp,
        "grid_code": GRID_CODE,
        "model_set": entry.freeze.model_dump() if entry.freeze else None,
        "freeze_state": fstate,
        "sources": source_states,
        "skipped": skipped,
        "files": [
            {"path": p, "bytes": len(b), "sha256": _sha(b)}
            for p, b in sorted(files.items())
        ],
    }
    manifest_bytes = json.dumps(manifest, indent=1, sort_keys=True).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in [("manifest.json", manifest_bytes), *sorted(files.items())]:
            z.writestr(
                zipfile.ZipInfo(path, _ZIP_TIME),
                data,
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return buf.getvalue(), {**manifest, "digest": _sha(manifest_bytes)}


def build(project, code: str, *, include_pdf: bool = True) -> tuple[bytes, dict]:
    """The pack ZIP and its manifest; records the pack on the deliverable."""
    d = require_deliverable(code)
    entry = load(project).deliverables.get(code) or DeliverableState()
    version = len(entry.packs) + 1
    data, manifest = _render(project, d, entry, version, include_pdf)
    with editing(project) as tracker:
        current = tracker.deliverables.setdefault(code, DeliverableState())
        if len(current.packs) + 1 != version:
            raise GridComplianceError(
                "gc_pack_conflict",
                409,
                f"Another pack of TRGC {code} was made meanwhile. Build it again.",
            )
        current.packs.append(
            RevisionRef(version=version, digest=manifest.pop("digest"))
        )
    return data, manifest
