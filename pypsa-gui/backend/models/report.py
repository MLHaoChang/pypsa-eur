"""
`ReportDocument` — the one document shape the study-report feature reads and
writes (WP1 of docs/superpowers/plans/2026-09-28-llm-report-generation-increment-1.md).

The writer (WP5) renders it to `.docx`; the generator (WP3) fills its
sections; the viewer (WP7) shows it; the round trip (increment 3) merges an
edited Word file back into it. Nothing in here is a table cell or a figure
the model wrote: tables and figures are rendered from the evidence by code
and REFERENCED from the prose through `TableRef` / `FigureRef` blocks.

`extra="ignore"` on every model, as `models/upload_schemas.py::UploadMeta`
does, so a v1 reader loads a file a later schema wrote. `schema_version` is a
`Literal[1]` rather than an `int` so a genuinely incompatible file fails at
the boundary with a clear ValidationError instead of half-loading.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field as PField


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ── blocks ──────────────────────────────────────────────────────────────────

class Paragraph(_Model):
    type: Literal["paragraph"] = "paragraph"
    md: str


class Bullets(_Model):
    type: Literal["bullets"] = "bullets"
    items: list[str]


class TableRef(_Model):
    type: Literal["table_ref"] = "table_ref"
    table_id: str
    caption: str | None = None


class FigureRef(_Model):
    type: Literal["figure_ref"] = "figure_ref"
    figure_id: str
    caption: str | None = None


class Callout(_Model):
    """
    A tagged box: a required disclosure carried verbatim from the evidence, an
    evidence gap, or a "not established" statement. `kind` is closed so the
    writer's style mapping (`Disclosure` / `Gap` / sentence) cannot silently
    receive a fourth kind it has no style for.
    """

    type: Literal["callout"] = "callout"
    kind: Literal["disclosure", "gap", "not_established"]
    text: str


class Field(_Model):
    type: Literal["field"] = "field"
    key: str
    value: str


Block = Annotated[
    Paragraph | Bullets | TableRef | FigureRef | Callout | Field,
    PField(discriminator="type"),
]


# ── sections ────────────────────────────────────────────────────────────────

class VerifiedNumber(_Model):
    text: str
    path: str


class SectionAudit(_Model):
    """The number audit's verdict (WP3): a check, not a rewrite."""

    unverified: list[str] = PField(default_factory=list)
    verified: list[VerifiedNumber] = PField(default_factory=list)


SectionSource = Literal["llm", "user_edit", "code"]
# The completeness enum of the EH reference-design spec (§4): a section that
# was not established is STATED with its note, never omitted.
SectionStatus = Literal["ok", "not_established", "skipped"]


class Section(_Model):
    section_id: str
    heading: str
    source: SectionSource
    status: SectionStatus
    blocks: list[Block] = PField(default_factory=list)
    note: str | None = None
    audit: SectionAudit = PField(default_factory=SectionAudit)


# ── tables and figures (rendered by code, referenced by blocks) ─────────────

class Table(_Model):
    table_id: str
    columns: list[str]
    rows: list[list[str]]
    caption: str | None = None
    # JSON pointer into the evidence the rows came from, for the audit and
    # the viewer's citation.
    source_path: str | None = None


class Figure(_Model):
    figure_id: str
    # File name under `reports/<report_id>/figures/`; the store resolves it.
    png_file: str
    caption: str | None = None
    source_path: str | None = None


# ── the document ────────────────────────────────────────────────────────────

ReportMode = Literal["evidence_only", "generated"]


class ReportDocument(_Model):
    schema_version: Literal[1] = 1
    report_id: str
    version: int
    title: str
    language: str = "en"
    created_at: str  # ISO 8601, UTC
    # sha256 of the canonical evidence (WP2); the viewer compares it with the
    # current collector hash to show "evidence changed since v3".
    evidence_hash: str
    profile_id: str | None = None
    model: str | None = None
    mode: ReportMode
    # Increment 2: the upload id of a user template. Always None until then.
    template_file_id: str | None = None
    sections: list[Section] = PField(default_factory=list)
    tables: dict[str, Table] = PField(default_factory=dict)
    figures: dict[str, Figure] = PField(default_factory=dict)


class ReportMeta(_Model):
    """`reports/<report_id>/meta.json` — what the list route shows per report."""

    report_id: str
    title: str
    created_at: str
    updated_at: str
    latest_version: int
    mode: ReportMode
    evidence_hash: str
    profile_id: str | None = None
    model: str | None = None
