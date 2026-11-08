"""Persisted records of job calls whose outcome is unknown (assistant-assurance
issue 16, slice S1).

`harness/job_holds.py` opens a hold on the chat that made the call. This module
is the one place that hold touches the database: one `assistant_job_holds` row
per hold, written when the hold opens and deleted on the evidence that clears
it, so a new chat or another tab can see that the operation may still run.

Every function opens its own short-lived `SessionLocal()` and RAISES on any
failure; the caller decides what a failure means (a failed write leaves the
hold on its chat only, a failed read denies: N4, GK B (35) H4/H6).

`owner_replica_id` names the process that holds the call's evidence (the
worker future or solve thread). `lease_until` is that owner's liveness lease
(slice S2, GK B (35) H1-H3): written at open, renewed by the owner only
(`renew`, never an upsert: K4) and compared on every clear of another owner's
record (`delete_if`), so a renewal that lands first wins (H2). Rows written by
an S1 replica carry NULL.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Record:
    id: str
    tool: str
    fingerprint: str
    job_id: str | None
    opened: str
    opened_at: float
    opened_by: str | None
    owner_replica_id: str
    project_id: str = ""
    lease_until: float | None = None


class MalformedRecord(ValueError):
    """A stored row that does not read as a hold record."""


def _session():
    from db.session import SessionLocal

    return SessionLocal()


def write(
    *,
    record_id: str,
    project_id: str,
    tool: str,
    fingerprint: str,
    job_id: str | None,
    opened: str,
    opened_at: float,
    opened_by: str | None,
    owner_replica_id: str,
    lease_until: float,
) -> None:
    from db.models import AssistantJobHold

    with _session() as db:
        db.add(
            AssistantJobHold(
                id=record_id,
                project_id=uuid.UUID(str(project_id)),
                tool=tool,
                fingerprint=fingerprint,
                job_id=job_id,
                opened=opened,
                opened_at=float(opened_at),
                opened_by_user_id=uuid.UUID(str(opened_by)) if opened_by else None,
                owner_replica_id=owner_replica_id,
                lease_until=float(lease_until),
            )
        )
        db.commit()


def set_job(record_id: str, job_id: str) -> None:
    """The late result named the job the record's call started."""
    from db.models import AssistantJobHold

    with _session() as db:
        row = db.get(AssistantJobHold, record_id)
        if row is not None:
            row.job_id = job_id
            db.commit()


def delete(record_id: str) -> None:
    from db.models import AssistantJobHold

    with _session() as db:
        db.query(AssistantJobHold).filter(AssistantJobHold.id == record_id).delete()
        db.commit()


def renew(record_id: str, owner_replica_id: str, lease_until: float) -> bool:
    """Extend the owner's own lease. Never re-creates a cleared record (K4):
    False when no row of this id and owner is left."""
    from db.models import AssistantJobHold

    with _session() as db:
        count = (
            db.query(AssistantJobHold)
            .filter(
                AssistantJobHold.id == record_id,
                AssistantJobHold.owner_replica_id == owner_replica_id,
            )
            .update({AssistantJobHold.lease_until: float(lease_until)})
        )
        db.commit()
        return count == 1


def delete_if(record_id: str, owner_replica_id: str, lease_until: float | None) -> bool:
    """Delete the record only while it still has this owner and this lease
    (H2): a renewal that landed after the read leaves it. True when deleted."""
    from db.models import AssistantJobHold

    lease = (
        AssistantJobHold.lease_until.is_(None)
        if lease_until is None
        else AssistantJobHold.lease_until == float(lease_until)
    )
    with _session() as db:
        count = (
            db.query(AssistantJobHold)
            .filter(
                AssistantJobHold.id == record_id,
                AssistantJobHold.owner_replica_id == owner_replica_id,
                lease,
            )
            .delete()
        )
        db.commit()
        return count == 1


def get(record_id: str) -> Record | None:
    from db.models import AssistantJobHold

    with _session() as db:
        row = db.get(AssistantJobHold, record_id)
        return None if row is None else _record(row)


def foreign(owner_replica_id: str) -> list[Record]:
    """Every readable record another process owns, on any project (the
    renewer's sweep). A malformed row is left out, so it stays (a read of it
    denies) and the other rows are still swept. Raises when the table cannot
    be read."""
    from db.models import AssistantJobHold

    with _session() as db:
        rows = (
            db.query(AssistantJobHold)
            .filter(AssistantJobHold.owner_replica_id != owner_replica_id)
            .order_by(AssistantJobHold.opened_at, AssistantJobHold.id)
            .all()
        )
        records = []
        for row in rows:
            try:
                records.append(_record(row))
            except MalformedRecord as exc:
                logger.warning("job hold sweep left a malformed row: %s", exc)
        return records


def solve_job_status(job_id: str) -> str | None:
    """The `solve_jobs` row's status, read strictly: None when no row has this
    id (or the id is no solve-queue id), raises when the table cannot be read."""
    from db.models import SolveJobRow

    try:
        key = uuid.UUID(str(job_id))
    except ValueError:
        return None
    with _session() as db:
        row = db.get(SolveJobRow, key)
        return None if row is None else row.status


def live_solve_jobs(project_id: str, kinds: tuple, since: float) -> int:
    """How many of the project's `solve_jobs` rows of these kinds (NULL reads
    `solve`) are not terminal and were enqueued at or after `since` (wall time).
    Raises when a table cannot be read."""
    from datetime import datetime

    from sqlalchemy import or_

    from db.models import Project, SolveJobRow
    from services.solve_queue import _TERMINAL

    with _session() as db:
        project = db.get(Project, uuid.UUID(str(project_id)))
        if project is None:
            return 0
        kind = SolveJobRow.kind.in_(kinds)
        if "solve" in kinds:
            kind = or_(kind, SolveJobRow.kind.is_(None))
        return (
            db.query(SolveJobRow)
            .filter(
                SolveJobRow.project_key == f"{project.org_id}:{project.id}",
                SolveJobRow.status.notin_(_TERMINAL),
                SolveJobRow.enqueued_at
                >= datetime.fromtimestamp(since, tz=UTC),
                kind,
            )
            .count()
        )


def exists(record_id: str) -> bool:
    from db.models import AssistantJobHold

    with _session() as db:
        return db.get(AssistantJobHold, record_id) is not None


def _record(row) -> Record:
    texts = (row.id, row.tool, row.fingerprint, row.opened, row.owner_replica_id)
    if not all(isinstance(value, str) and value for value in texts):
        raise MalformedRecord(f"hold record {row.id}: a required field is empty")
    if row.job_id is not None and not (isinstance(row.job_id, str) and row.job_id):
        raise MalformedRecord(f"hold record {row.id}: job_id")
    if not isinstance(row.opened_at, (int, float)) or not math.isfinite(row.opened_at):
        raise MalformedRecord(f"hold record {row.id}: opened_at")
    lease = row.lease_until
    if lease is not None and not (
        isinstance(lease, (int, float)) and math.isfinite(lease)
    ):
        raise MalformedRecord(f"hold record {row.id}: lease_until")
    return Record(
        id=row.id,
        tool=row.tool,
        fingerprint=row.fingerprint,
        job_id=row.job_id,
        opened=row.opened,
        opened_at=float(row.opened_at),
        opened_by=str(row.opened_by_user_id) if row.opened_by_user_id else None,
        owner_replica_id=row.owner_replica_id,
        project_id=str(row.project_id),
        lease_until=None if lease is None else float(lease),
    )


def active(project_id: str, tool: str) -> list[Record]:
    """Every record of `tool` on the project, oldest first. Raises when the
    table cannot be read or a row is malformed."""
    from db.models import AssistantJobHold

    with _session() as db:
        rows = (
            db.query(AssistantJobHold)
            .filter(
                AssistantJobHold.project_id == uuid.UUID(str(project_id)),
                AssistantJobHold.tool == tool,
            )
            .order_by(AssistantJobHold.opened_at, AssistantJobHold.id)
            .all()
        )
        return [_record(row) for row in rows]
