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
worker future or solve thread). `lease_until` stays NULL in S1: the
owner-liveness lease that lets another replica or a restart read a record is
slice S2 (GK B (35) H1-H3).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass


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
                lease_until=None,
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
    return Record(
        id=row.id,
        tool=row.tool,
        fingerprint=row.fingerprint,
        job_id=row.job_id,
        opened=row.opened,
        opened_at=float(row.opened_at),
        opened_by=str(row.opened_by_user_id) if row.opened_by_user_id else None,
        owner_replica_id=row.owner_replica_id,
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
