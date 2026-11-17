"""The compliance tracker's file: ``<project>/grid_compliance/tracker.json``.

One JSON file per project holds every deliverable's status, data list, data
freeze, TSO comment log and pack history. Writes are atomic
(``services.atomic_io``) and serialised across processes by
``harness.durable.file_lock`` on a sibling lock file, so two edits never lose
each other. A file that is not this version's shape is refused
(``gc_tracker_unreadable``), never read as empty or overwritten; a busy lock
is ``gc_tracker_busy`` and a failed write ``gc_tracker_write_failed``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from harness.durable import file_lock
from services import project_registry
from services.atomic_io import atomic_write_text
from services.reports.revisions import RevisionRef

from .catalogue import BY_CODE, STATUSES

SUBDIR = "grid_compliance"
TRACKER_FILE = "tracker.json"
LOCK_FILE = "tracker.lock"
LOCK_TIMEOUT_S = 10

Status = Literal[STATUSES]  # type: ignore[valid-type]


class GridComplianceError(ValueError):
    """A typed refusal: ``kind`` is the HTTP ``error_kind``, ``status`` its code."""

    def __init__(
        self, kind: str, status: int, message: str, *, blockers: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.kind, self.status, self.message = kind, status, message
        self.blockers = list(blockers or [])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataItem(_Strict):
    item_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=200)
    #: Who supplies it, as a role ("plant owner", "TSO", "unit supplier").
    supplier: str = Field(default="", max_length=80)
    due: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: Literal["requested", "received", "checked"] = "requested"


class Freeze(_Strict):
    model_set_id: str
    data_hash: str
    frozen_at: str
    #: ``interim`` until GC-01's model-set freeze is on master.
    source: Literal["interim", "gc-01"]


class TsoComment(_Strict):
    comment_id: str
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    #: The grid-code clause or TRGC section the comment cites.
    clause: str = Field(default="", max_length=64)
    comment: str = Field(min_length=1, max_length=4000)
    response: str = Field(default="", max_length=4000)
    #: The report revision the response went out in ("Rev B").
    revision: str = Field(default="", max_length=16)
    status: Literal["open", "responded", "closed"] = "open"
    #: The pack (version and manifest digest) the response was issued in.
    pack: RevisionRef | None = None
    created_at: str
    updated_at: str


class DeliverableState(_Strict):
    status: Status = "not_started"
    status_at: str | None = None
    #: None until the data list is first edited; the catalogue's items stand in.
    data: list[DataItem] | None = None
    freeze: Freeze | None = None
    #: The model set each submission went out on, oldest first. A record is
    #: appended when the deliverable reaches ``submitted`` and never rewritten;
    #: moves after it are judged against the last one, not the current inputs.
    submissions: list[Freeze] = Field(default_factory=list)
    comments: list[TsoComment] = Field(default_factory=list)
    #: The next comment number; never reused, so ids stay unique.
    next_comment: int = 1
    packs: list[RevisionRef] = Field(default_factory=list)


class TrackerFile(_Strict):
    schema_version: Literal[1] = 1
    deliverables: dict[str, DeliverableState] = Field(default_factory=dict)

    @field_validator("deliverables")
    @classmethod
    def _known_codes(cls, value: dict) -> dict:
        unknown = sorted(set(value) - set(BY_CODE))
        if unknown:
            raise ValueError(f"unknown deliverable(s) {unknown}")
        return value


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file_lock(path.parent / LOCK_FILE, timeout=LOCK_TIMEOUT_S):
            yield
    except TimeoutError as exc:
        raise GridComplianceError(
            "gc_tracker_busy",
            409,
            "Another edit of the compliance tracker is in progress. Try again.",
        ) from exc


def tracker_path(project) -> Path:
    return project_registry.ensure_project_dir(project) / SUBDIR / TRACKER_FILE


def load(project) -> TrackerFile:
    path = tracker_path(project)
    if not path.exists():
        return TrackerFile()
    try:
        return TrackerFile.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError) as exc:
        raise GridComplianceError(
            "gc_tracker_unreadable",
            409,
            f"The compliance tracker file cannot be read ({type(exc).__name__}). "
            "Nothing was changed.",
        ) from exc


@contextmanager
def editing(project) -> Iterator[TrackerFile]:
    """Load the tracker under its lock, yield it, and write it back when the
    block exits without an exception."""
    path = tracker_path(project)
    with _lock(path):
        tracker = load(project)
        yield tracker
        text = TrackerFile.model_validate(tracker.model_dump()).model_dump_json(
            indent=1
        )
        try:
            atomic_write_text(path, text)
        except OSError as exc:
            raise GridComplianceError(
                "gc_tracker_write_failed",
                503,
                f"The compliance tracker could not be saved ({type(exc).__name__}). "
                "Nothing was changed.",
            ) from exc
