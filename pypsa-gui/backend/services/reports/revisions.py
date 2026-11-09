"""Report comments become revisions (study harness issue 07c, #970 HH-08A).

A user reads a saved report and comments on one part of it. Each comment is
a durable item in `reports/<report_id>/comments.json`, beside the versions,
so it travels with the report through save-as, copy, snapshot and bundle
(`reports/` is a bundle directory). It stays `open` until a revision
addresses it or the user withdraws it. `addressed` and `withdrawn` are
terminal (GK B R9).

**The closed edit set.** An edit names one kind and replaces one part of the
comment's own anchored block, nothing else:

- `paragraph_text`: a `Paragraph`'s markdown;
- `bullet_text`: a `Bullets` block's items;
- `table_text`: a table's caption and column headers;
- `table_cells`: a table's cell text (number formats).

Headings, notes, callouts (disclosures, gaps), figures, fields and block
structure are outside the set. A comment anchored there, or anchored to a
whole section, stays open with its reason.

**What an edit never changes** (`check_invariants`, one named reason per
part, GK B R6):

- the numbers of every block and cell, as a multiset of signed value and
  unit (`changes_number`, or `drops_number` when only some are gone);
- digits the number audit exempts (years, ordinals, digits inside words);
- the block structure (`changes_structure`);
- the callouts, verbatim (`changes_disclosure`);
- the number audit, re-run on the new text by the job's own audit and never
  copied (`changes_audit`, R4);
- the evidence references: the table and figure refs and the source paths
  (`changes_evidence_ref`);
- the figures, which are the chart ranges (`changes_chart`);
- `evidence_hash` (`changes_evidence_hash`);
- the table shape (`changes_table_shape`);
- anything outside the anchored part (`edit_outside_anchor`).

A number may only be re-spelled to an equal value: thousands separators,
trailing zeros, unit spacing, `EUR`/`€`, `/a`/`/yr`. Rounding, a unit
conversion (240 min → 4 h, 0.2 → 20 %) or a sign change is a numeric repair,
which is out of scope (#970 08B/08C).

**A revision** is a new version (`store.save_version`) made against the
latest version's digest (`store.version_sha256`). A stale digest, or an
anchored block that changed since the comment, is refused
`revision_stale_base` and nothing is written. The digest check, the anchor
check, the save and the comment update run under `store.report_lock`, the
lock every writer of `reports/<id>/` takes (R5). The new version records
`revised_from {version, digest, comment_id}` (R7).

No model call is made here (GK B R1): the edit is the user's own text.
"""

from __future__ import annotations

import copy
import re
import secrets
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from models.report import (
    Bullets,
    Callout,
    FigureRef,
    Paragraph,
    ReportDocument,
    RevisedFrom,
    SectionAudit,
    TableRef,
)
from services.assurance_artifacts import canonical_sha256
from services.atomic_io import atomic_write_text
from services.reports import number_audit, store
from services.reports.evidence import NumberFact

COMMENTS_FILE = "comments.json"
SCHEMA_VERSION = 1
MAX_COMMENT_CHARS = 2000
_COMMENT_ID = re.compile(r"\A[0-9a-f]{16}\Z")

# Why a comment cannot be addressed by any edit of the closed set.
NO_EDIT_TARGET = "no_edit_target"
OUTSIDE_EDIT_SET = "outside_edit_set"
EDIT_KIND_MISMATCH = "edit_kind_mismatch"

REASON_TEXT = {
    NO_EDIT_TARGET: "The comment names a whole section, not one paragraph, bullet list or "
    "table, so no edit of the closed set can address it.",
    OUTSIDE_EDIT_SET: "The comment names a part a revision may not edit: a disclosure, gap "
    "or not-established box, a figure or a field.",
    EDIT_KIND_MISMATCH: "The edit's kind does not fit the commented block.",
    "changes_number": "The edit changes a number. A revision may re-spell a number to an "
    "equal value, never round, convert, move or change it.",
    "drops_number": "The edit drops a number the base states.",
    "changes_structure": "The edit adds, removes or reorders blocks or sections.",
    "changes_disclosure": "The edit changes a disclosure, gap or not-established box.",
    "changes_audit": "The number audit of the new text differs from the base's.",
    "changes_evidence_ref": "The edit changes an evidence reference (a table or figure ref, "
    "or a source path).",
    "changes_chart": "The edit changes a figure.",
    "changes_evidence_hash": "The edit changes the evidence the report was made from.",
    "changes_table_shape": "The edit changes a table's shape.",
    "edit_outside_anchor": "The edit changes something outside the commented block.",
}

_ANCHOR_KINDS = {
    "paragraph": "paragraph_text",
    "bullets": "bullet_text",
}


# ── errors ──────────────────────────────────────────────────────────────────


class RevisionError(ValueError):
    """A typed refusal: `kind` is the HTTP `error_kind`, `status` its code."""

    def __init__(
        self, kind: str, status: int, message: str, *, reasons: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.kind, self.status, self.message = kind, status, message
        self.reasons = list(reasons or [])


def _comments_unreadable(report_id: str, why: str) -> RevisionError:
    return RevisionError(
        "report_comments_unreadable",
        409,
        f"The comments of report {report_id} cannot be read ({why}). Nothing was changed.",
    )


# ── the stored shape ────────────────────────────────────────────────────────


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Anchor(_Strict):
    section_id: str = Field(min_length=1, max_length=200)
    block_index: int | None = Field(default=None, ge=0)
    table_id: str | None = Field(default=None, min_length=1, max_length=200)


class RevisionRef(_Strict):
    version: int
    digest: str


class Comment(_Strict):
    comment_id: str
    text: str = Field(min_length=1, max_length=MAX_COMMENT_CHARS)
    anchor: Anchor
    base_version: int
    base_digest: str
    anchor_sha: str | None
    status: Literal["open", "addressed", "withdrawn"]
    reason: str | None = None
    reasons: list[str] = Field(default_factory=list)
    revision: RevisionRef | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None

    @model_validator(mode="after")
    def _fields_agree(self) -> Comment:
        return _consistent(self)


KNOWN_REASONS = frozenset(REASON_TEXT)


def _consistent(comment: Comment) -> Comment:
    """R8: a status and its fields agree; a reason is one this module gives."""
    if (comment.status == "addressed") != (comment.revision is not None):
        raise ValueError("an addressed comment, and only one, carries its revision")
    if comment.status == "addressed" and (comment.reason or comment.reasons):
        raise ValueError("an addressed comment has no open reason")
    if comment.reason != (comment.reasons[0] if comment.reasons else None):
        raise ValueError("reason is the first of reasons")
    if not set(comment.reasons) <= KNOWN_REASONS:
        raise ValueError("unknown reason")
    return comment


class CommentsFile(_Strict):
    schema_version: Literal[1]
    report_id: str
    comments: list[Comment]


class ParagraphEdit(_Strict):
    kind: Literal["paragraph_text"]
    md: str = Field(min_length=1)


class BulletEdit(_Strict):
    kind: Literal["bullet_text"]
    items: list[str] = Field(min_length=1)


class TableTextEdit(_Strict):
    kind: Literal["table_text"]
    caption: str | None = None
    columns: list[str]


class TableCellsEdit(_Strict):
    kind: Literal["table_cells"]
    rows: list[list[str]]


Edit = Annotated[
    ParagraphEdit | BulletEdit | TableTextEdit | TableCellsEdit,
    Field(discriminator="kind"),
]


# ── the comments file ───────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _path(project_dir, report_id: str):
    return store.report_dir(project_dir, report_id) / COMMENTS_FILE


def load_comments(project_dir, report_id: str) -> list[Comment]:
    """Every comment of the report, oldest first. No file → []. A file that
    is not this version's shape, names another report or repeats an id is a
    `report_comments_unreadable` refusal, never an empty list (R8)."""
    store.load_meta(project_dir, report_id)  # ReportNotFound first
    path = _path(project_dir, report_id)
    if not path.exists():
        return []
    try:
        parsed = CommentsFile.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        raise _comments_unreadable(report_id, type(exc).__name__) from exc
    if parsed.report_id != report_id:
        raise _comments_unreadable(report_id, "the file names another report")
    ids = [c.comment_id for c in parsed.comments]
    if len(set(ids)) != len(ids) or not all(_COMMENT_ID.match(i) for i in ids):
        raise _comments_unreadable(report_id, "duplicate or malformed comment ids")
    return parsed.comments


def _write_comments(project_dir, report_id: str, comments: list[Comment]) -> None:
    body = CommentsFile(
        schema_version=SCHEMA_VERSION, report_id=report_id, comments=comments
    )
    atomic_write_text(_path(project_dir, report_id), body.model_dump_json(indent=2))


# ── anchors ─────────────────────────────────────────────────────────────────


def _section(doc: ReportDocument, section_id: str):
    return next((s for s in doc.sections if s.section_id == section_id), None)


def _anchored(doc: ReportDocument, anchor: Anchor) -> tuple[str, Any] | None:
    """`(what, object)` the anchor names in `doc`: `("section", Section)`,
    `("block", Block)` or `("table", Table)`; None when it names nothing."""
    section = _section(doc, anchor.section_id)
    if section is None:
        return None
    if anchor.table_id is not None:
        if anchor.block_index is not None:
            return None
        referenced = any(
            isinstance(b, TableRef) and b.table_id == anchor.table_id
            for b in section.blocks
        )
        table = doc.tables.get(anchor.table_id)
        return ("table", table) if referenced and table is not None else None
    if anchor.block_index is None:
        return ("section", section)
    if anchor.block_index >= len(section.blocks):
        return None
    return ("block", section.blocks[anchor.block_index])


def _anchor_sha(found: tuple[str, Any]) -> str | None:
    what, obj = found
    return None if what == "section" else canonical_sha256(obj.model_dump(mode="json"))


def _unsupported_reason(found: tuple[str, Any]) -> str | None:
    what, obj = found
    if what == "section":
        return NO_EDIT_TARGET
    if what == "block" and getattr(obj, "type", None) not in _ANCHOR_KINDS:
        return OUTSIDE_EDIT_SET
    return None


def _fits(found: tuple[str, Any], edit: Any) -> bool:
    what, obj = found
    if what == "table":
        return edit.kind in ("table_text", "table_cells")
    return _ANCHOR_KINDS.get(getattr(obj, "type", None)) == edit.kind


# ── numbers ─────────────────────────────────────────────────────────────────

_UNIT_SPELLING = {
    "EUR": "€",
    "h/a": "h/yr",
    "MWh/a": "MWh/yr",
    "€/a": "€/yr",
    "/a": "/yr",
}
_MINUS = "-−–"
_DIGIT_WORD = re.compile(r"[\w.,]*\d[\w.,]*")


# A word after a unitless token is read as its unit when it looks like one:
# it carries a capital or a symbol (`MWh/a`, `kV`, `€/t`), or it is a common
# lower-case unit. The audit's own unit list is not the guard (GK B Q2).
_UNIT_WORD = re.compile(r"\A[ \u2009\u00a0]?([A-Za-z€%‰‱°/²³·]+(?:/[A-Za-z]+)?)")
_LOWER_UNITS = frozenset(
    {
        "s",
        "sec",
        "secs",
        "second",
        "seconds",
        "min",
        "mins",
        "minute",
        "minutes",
        "h",
        "hr",
        "hrs",
        "hour",
        "hours",
        "d",
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "yr",
        "yrs",
        "year",
        "years",
        "a",
        "t",
        "kt",
        "kg",
        "g",
        "m",
        "km",
        "km²",
        "m²",
        "ha",
        "x",
        "pu",
        "p.u",
    }
)


def _unit_word(text: str, end: int) -> str | None:
    m = _UNIT_WORD.match(text[end:])
    if m is None:
        return None
    word = m.group(1)
    if word in _LOWER_UNITS or any(not c.islower() for c in word):
        return _UNIT_SPELLING.get(word.replace("EUR", "€"), word.replace("EUR", "€"))
    return None


def _unit(token: number_audit.NumberToken, text: str = "") -> str | None:
    unit = token.unit_hint
    if unit is None:
        return _unit_word(text, token.end)
    unit = unit.replace("EUR", "€") if unit.startswith("EUR") else unit
    return _UNIT_SPELLING.get(unit, unit)


def _negative(text: str, start: int) -> bool:
    """A minus right before the number, or one space before it ("- 5 MW").
    A hyphen ("N-1") or a subtraction ("8 - 5") reads negative too; the
    reading is the same on both sides of an edit, so only a minus added or
    removed shows as a change."""
    i = start - 1
    if i >= 0 and text[i] in " \u2009\u00a0":
        i -= 1
    return i >= 0 and text[i] in _MINUS


def numbers(text: str) -> Counter:
    """The numbers `text` states, as a multiset of `(signed value, unit)`,
    plus every digit run the audit exempts (a year, an ordinal, digits inside
    a word) kept verbatim, so an edit cannot change those either."""
    text = text or ""
    out: Counter = Counter()
    spans: list[tuple[int, int]] = []
    for token in number_audit.extract_numbers(text):
        sign = -1.0 if _negative(text, token.start) else 1.0
        value = float(f"{sign * token.value:.12g}")
        out[("number", value, _unit(token, text))] += 1
        spans.append((token.start, token.end))
    residual = list(text)
    for a, b in spans:
        residual[a:b] = " " * (b - a)
    for word in _DIGIT_WORD.findall("".join(residual)):
        out[("digits", word.strip(".,"), None)] += 1
    return out


def _block_texts(block: Any) -> list[str]:
    if isinstance(block, Paragraph):
        return [block.md]
    if isinstance(block, Bullets):
        return list(block.items)
    if isinstance(block, (TableRef, FigureRef)):
        return [block.caption or ""]
    return []


def _number_parts(doc: ReportDocument) -> dict[tuple, Counter]:
    parts: dict[tuple, Counter] = {}
    for section in doc.sections:
        for i, block in enumerate(section.blocks):
            parts[("block", section.section_id, i)] = sum(
                (numbers(t) for t in _block_texts(block)), Counter()
            )
    for table_id, table in doc.tables.items():
        parts[("caption", table_id)] = numbers(table.caption or "")
        for c, column in enumerate(table.columns):
            parts[("column", table_id, c)] = numbers(column)
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row):
                parts[("cell", table_id, r, c)] = numbers(cell)
    return parts


# ── the audit, re-run (R4) ──────────────────────────────────────────────────


def _audited_texts(section) -> list[str]:
    out: list[str] = []
    for block in section.blocks:
        if isinstance(block, (Paragraph, Bullets)):
            out.extend(_block_texts(block))
    return out


def _facts(section) -> list[NumberFact]:
    """The facts the base's audit verified, read back from its own record:
    each verified token's value and unit, at the path it was verified to."""
    facts: list[NumberFact] = []
    for verified in section.audit.verified:
        tokens = number_audit.extract_numbers(verified.text)
        if tokens:
            facts.append(
                NumberFact(
                    path=verified.path, value=tokens[0].value, unit=tokens[0].unit_hint
                )
            )
    return facts


def reaudit(section, facts: list[NumberFact]) -> SectionAudit:
    """The job's own number audit (`number_audit.audit`) on the section's
    current paragraphs and bullets."""
    return number_audit.audit(_audited_texts(section), facts)


def _values(token_text: str) -> tuple:
    return tuple(t.value for t in number_audit.extract_numbers(token_text))


def _audit_key(result: SectionAudit) -> tuple:
    """What the audit found, by fact path and value, not by spelling (an
    audit token's text carries no sign or context; `numbers` checks those)."""
    verified = sorted((v.path, _values(v.text)) for v in result.verified)
    unverified = sorted(_values(u) for u in result.unverified)
    return tuple(verified), tuple(unverified)


def _audited(section) -> bool:
    return bool(section.audit.verified or section.audit.unverified)


def _audits_equal(base_section, new_section) -> bool:
    """R4. A section the base audited: the audit re-run on the new text
    equals the one re-run on the base's text, against the base's facts, and
    the new section stores exactly that re-run (its spellings, not the
    base's). A section the base never audited (code-rendered) stores none."""
    if not _audited(base_section):
        return not _audited(new_section)
    facts = _facts(base_section)
    rerun = reaudit(new_section, facts)
    return (
        _audit_key(reaudit(base_section, facts)) == _audit_key(rerun)
        and new_section.audit.model_dump() == rerun.model_dump()
    )


# ── the invariants (R6) ─────────────────────────────────────────────────────


def _structure(doc: ReportDocument) -> list:
    return [(s.section_id, [b.type for b in s.blocks]) for s in doc.sections]


def _callouts(doc: ReportDocument) -> list:
    return [
        (s.section_id, i, b.kind, b.text)
        for s in doc.sections
        for i, b in enumerate(s.blocks)
        if isinstance(b, Callout)
    ]


def _evidence_refs(doc: ReportDocument) -> list:
    refs = [
        (
            s.section_id,
            i,
            b.type,
            getattr(b, "table_id", None) or getattr(b, "figure_id", None),
        )
        for s in doc.sections
        for i, b in enumerate(s.blocks)
        if isinstance(b, (TableRef, FigureRef))
    ]
    refs += [
        ("table", k, t.table_id, t.source_path) for k, t in sorted(doc.tables.items())
    ]
    refs += [
        ("figure", k, f.figure_id, f.source_path)
        for k, f in sorted(doc.figures.items())
    ]
    return refs


def _figures(doc: ReportDocument) -> dict:
    return {k: f.model_dump(mode="json") for k, f in doc.figures.items()}


def _table_shape(doc: ReportDocument) -> dict:
    return {
        k: (len(t.columns), [len(r) for r in t.rows]) for k, t in doc.tables.items()
    }


def _number_reason(base: ReportDocument, new: ReportDocument) -> str | None:
    a, b = _number_parts(base), _number_parts(new)
    if a == b:
        return None
    dropped = all(b.get(k, Counter()) <= v for k, v in a.items()) and set(b) <= set(a)
    return "drops_number" if dropped else "changes_number"


def _outside(doc: ReportDocument, anchor: Anchor | None) -> dict:
    """The document with the anchored part (and the section source an edit
    marks) blanked: what must stay equal outside the anchor."""
    raw = doc.model_dump(mode="json", exclude={"version", "revised_from", "created_at"})
    if anchor is None:
        return raw
    for section in raw["sections"]:
        if section["section_id"] != anchor.section_id:
            continue
        section["source"] = None
        if anchor.block_index is not None and anchor.block_index < len(
            section["blocks"]
        ):
            section["blocks"][anchor.block_index] = None
            section["audit"] = None
    if anchor.table_id in raw["tables"]:
        raw["tables"][anchor.table_id] = None
    return raw


def check_invariants(
    base: ReportDocument, new: ReportDocument, anchor: Anchor | None = None
) -> list[str]:
    """Every reason `new` is not a closed-set revision of `base` at `anchor`,
    in a fixed order; [] when it is one."""
    reasons: list[str] = []
    number = _number_reason(base, new)
    if number:
        reasons.append(number)
    if _structure(base) != _structure(new):
        reasons.append("changes_structure")
    if _callouts(base) != _callouts(new):
        reasons.append("changes_disclosure")
    # The anchored section's audit is re-run (R4); every other section's
    # stored audit must stay as it is (`edit_outside_anchor`).
    if anchor is not None and anchor.block_index is not None:
        old_s, new_s = (
            _section(base, anchor.section_id),
            _section(new, anchor.section_id),
        )
        if old_s is None or new_s is None or not _audits_equal(old_s, new_s):
            reasons.append("changes_audit")
    if _evidence_refs(base) != _evidence_refs(new):
        reasons.append("changes_evidence_ref")
    if _figures(base) != _figures(new):
        reasons.append("changes_chart")
    if base.evidence_hash != new.evidence_hash:
        reasons.append("changes_evidence_hash")
    if _table_shape(base) != _table_shape(new):
        reasons.append("changes_table_shape")
    if _outside(base, anchor) != _outside(new, anchor):
        reasons.append("edit_outside_anchor")
    return reasons


# ── applying an edit ────────────────────────────────────────────────────────


def apply_edit(base: ReportDocument, anchor: Anchor, edit: Any) -> ReportDocument:
    """`base` with `edit` applied to the anchored part only. A paragraph or
    bullet edit marks its section `user_edit` and re-runs its audit (R4)."""
    doc = base.model_copy(deep=True)
    if anchor.table_id is not None:
        table = doc.tables[anchor.table_id]
        if edit.kind == "table_text":
            update = {"caption": edit.caption, "columns": list(edit.columns)}
        else:
            update = {"rows": copy.deepcopy(edit.rows)}
        doc.tables[anchor.table_id] = table.model_copy(update=update)
        return doc
    index = next(
        i for i, s in enumerate(doc.sections) if s.section_id == anchor.section_id
    )
    section = doc.sections[index]
    blocks = list(section.blocks)
    old = blocks[anchor.block_index]
    blocks[anchor.block_index] = (
        old.model_copy(update={"md": edit.md})
        if edit.kind == "paragraph_text"
        else old.model_copy(update={"items": list(edit.items)})
    )
    edited = section.model_copy(update={"blocks": blocks, "source": "user_edit"})
    if _audited(section):
        edited = edited.model_copy(update={"audit": reaudit(edited, _facts(section))})
    doc.sections[index] = edited
    return doc


# ── the operations ──────────────────────────────────────────────────────────


def _latest(project_dir, report_id: str) -> tuple[ReportDocument, str]:
    doc = store.load_report(project_dir, report_id)
    return doc, store.version_sha256(project_dir, report_id, doc.version)


def _find(comments: list[Comment], comment_id: str) -> int:
    for i, c in enumerate(comments):
        if c.comment_id == comment_id:
            return i
    raise RevisionError(
        "report_comment_not_found", 404, f"No comment {comment_id!r} on this report."
    )


def _open_or_409(comment: Comment) -> None:
    if comment.status != "open":
        raise RevisionError(
            "report_comment_closed",
            409,
            f"Comment {comment.comment_id} is {comment.status}; it cannot change again.",
        )


def add_comment(
    project_dir, report_id: str, text: str, anchor: Anchor, *, user: str | None = None
) -> Comment:
    """Store a new open comment on the latest version. An anchor that names
    no section, block or table of it is refused `report_comment_anchor_invalid`
    and nothing is stored; one outside the closed set is stored open with
    its reason."""
    with store.report_lock(project_dir, report_id):
        comments = load_comments(project_dir, report_id)
        doc, digest = _latest(project_dir, report_id)
        found = _anchored(doc, anchor)
        if found is None:
            raise RevisionError(
                "report_comment_anchor_invalid",
                422,
                "The comment's anchor names no section, block or table of the "
                "report's latest version.",
            )
        reason = _unsupported_reason(found)
        now = _now()
        comment = Comment(
            comment_id=secrets.token_hex(8),
            text=text,
            anchor=anchor,
            base_version=doc.version,
            base_digest=digest,
            anchor_sha=_anchor_sha(found),
            status="open",
            reason=reason,
            reasons=[reason] if reason else [],
            created_at=now,
            updated_at=now,
            created_by=user,
        )
        _write_comments(project_dir, report_id, [*comments, comment])
        return comment


def withdraw(project_dir, report_id: str, comment_id: str) -> Comment:
    with store.report_lock(project_dir, report_id):
        comments = load_comments(project_dir, report_id)
        i = _find(comments, comment_id)
        _open_or_409(comments[i])
        comments[i] = comments[i].model_copy(
            update={"status": "withdrawn", "updated_at": _now()}
        )
        _write_comments(project_dir, report_id, comments)
        return comments[i]


def _keep_open(
    project_dir, report_id, comments, i, reasons: list[str]
) -> RevisionError:
    comments[i] = comments[i].model_copy(
        update={"reason": reasons[0], "reasons": reasons, "updated_at": _now()}
    )
    _write_comments(project_dir, report_id, comments)
    return RevisionError(
        "report_revision_unsupported",
        422,
        REASON_TEXT.get(reasons[0], reasons[0]),
        reasons=reasons,
    )


def address(
    project_dir, report_id: str, comment_id: str, base_digest: str, edit: Any
) -> tuple[Comment, ReportDocument]:
    """Address an open comment with `edit`, as a new version made against
    `base_digest`. Returns the addressed comment and the saved version.

    Refusals: `report_comment_closed` (409) for a terminal comment;
    `revision_stale_base` (409) when `base_digest` is not the latest
    version's or the anchored block changed since the comment, with nothing
    written; `report_revision_unsupported` (422) when the comment or the edit
    is outside the closed set, the comment then staying open with its
    reasons stored."""
    with store.report_lock(project_dir, report_id):
        comments = load_comments(project_dir, report_id)
        i = _find(comments, comment_id)
        comment = comments[i]
        _open_or_409(comment)
        base, digest = _latest(project_dir, report_id)
        found = _anchored(base, comment.anchor)
        if (
            base_digest != digest
            or found is None
            or _anchor_sha(found) != comment.anchor_sha
        ):
            raise RevisionError(
                "revision_stale_base",
                409,
                "The report changed since this revision was prepared (its digest or the "
                "commented block differs). Reload the latest version and try again; nothing "
                "was written.",
            )
        unsupported = _unsupported_reason(found)
        if unsupported:
            raise _keep_open(project_dir, report_id, comments, i, [unsupported])
        if not _fits(found, edit):
            raise _keep_open(project_dir, report_id, comments, i, [EDIT_KIND_MISMATCH])
        revised = apply_edit(base, comment.anchor, edit)
        reasons = check_invariants(base, revised, comment.anchor)
        if reasons:
            raise _keep_open(project_dir, report_id, comments, i, reasons)
        version = store.save_version(
            project_dir,
            revised,
            revised_from=RevisedFrom(
                version=base.version, digest=digest, comment_id=comment_id
            ),
        )
        saved = store.load_report(project_dir, report_id, version)
        comments[i] = comment.model_copy(
            update={
                "status": "addressed",
                "reason": None,
                "reasons": [],
                "revision": RevisionRef(
                    version=version,
                    digest=store.version_sha256(project_dir, report_id, version),
                ),
                "updated_at": _now(),
            }
        )
        _write_comments(project_dir, report_id, comments)
        return comments[i], saved
