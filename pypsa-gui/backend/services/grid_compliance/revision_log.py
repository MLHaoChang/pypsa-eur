"""The TSO comment / revision log of each deliverable (GC-09).

A comment carries the TSO's date, the clause it cites, its text, the
response, the report revision the response went out in, and a status.
Ids are ``C-001``, ``C-002`` ... per deliverable and never reused.

``services.reports.revisions`` anchors comments to blocks of a stored report
version; TSO comments cite grid-code clauses instead, so this log reuses its
``RevisionRef`` (version and digest) to tie a response to the pack it was
issued in, not its anchor machinery.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .store import DeliverableState, GridComplianceError, TsoComment, editing
from .tracker import require_deliverable

_EDITABLE = ("clause", "comment", "response", "revision", "status", "date")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add(project, code: str, fields: dict) -> dict:
    require_deliverable(code)
    unknown = set(fields) - set(_EDITABLE)
    if unknown:
        raise GridComplianceError("gc_comment_invalid", 422,
                                  f"Unknown comment field(s): {sorted(unknown)}.")
    with editing(project) as tracker:
        entry = tracker.deliverables.setdefault(code, DeliverableState())
        now = _now()
        comment = TsoComment.model_validate({
            **fields, "comment_id": f"C-{entry.next_comment:03d}",
            "created_at": now, "updated_at": now})
        _check(comment)
        entry.comments.append(comment)
        entry.next_comment += 1
    return comment.model_dump()


def update(project, code: str, comment_id: str, fields: dict) -> dict:
    require_deliverable(code)
    unknown = set(fields) - set(_EDITABLE) - {"pack_version"}
    if unknown:
        raise GridComplianceError("gc_comment_invalid", 422,
                                  f"Unknown comment field(s): {sorted(unknown)}.")
    with editing(project) as tracker:
        entry = tracker.deliverables.get(code) or DeliverableState()
        i = next((i for i, c in enumerate(entry.comments)
                  if c.comment_id == comment_id), None)
        if i is None:
            raise GridComplianceError(
                "gc_comment_not_found", 404,
                f"TRGC {code} has no comment {comment_id!r}.")
        data = entry.comments[i].model_dump()
        version = fields.pop("pack_version", None)
        data.update(fields)
        if version is not None:
            ref = next((p for p in entry.packs if p.version == version), None)
            if ref is None:
                raise GridComplianceError(
                    "gc_pack_not_found", 422,
                    f"TRGC {code} has no generated pack version {version!r}.")
            data["pack"] = ref.model_dump()
        data["updated_at"] = _now()
        comment = TsoComment.model_validate(data)
        _check(comment)
        entry.comments[i] = comment
    return comment.model_dump()


def _check(comment: TsoComment) -> None:
    if comment.status != "open" and not comment.response.strip():
        raise GridComplianceError(
            "gc_comment_invalid", 422,
            "A comment is marked responded or closed only once it has a response.")
