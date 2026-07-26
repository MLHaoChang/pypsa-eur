from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from db.models import ProjectLock


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _expires_at(ttl_seconds: int) -> datetime:
    return _now() + timedelta(seconds=ttl_seconds)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_expired(lock: ProjectLock) -> bool:
    return _as_utc(lock.expires_at) <= _now()


def _prune_expired(db: DBSession, project_id: uuid.UUID) -> ProjectLock | None:
    lock = db.get(ProjectLock, project_id)
    if lock is None:
        return None
    if not _is_expired(lock):
        return lock
    db.delete(lock)
    db.commit()
    return None


def acquire_lock(
    db: DBSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    ttl_seconds: int = 120,
) -> ProjectLock | None:
    lock = _prune_expired(db, project_id)
    expires_at = _expires_at(ttl_seconds)
    if lock is None:
        lock = ProjectLock(
            project_id=project_id,
            holder_user_id=user_id,
            acquired_at=_now(),
            expires_at=expires_at,
        )
        db.add(lock)
        db.commit()
        db.refresh(lock)
        return lock
    if lock.holder_user_id != user_id:
        return None
    lock.expires_at = expires_at
    db.commit()
    db.refresh(lock)
    return lock


def heartbeat_lock(
    db: DBSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    ttl_seconds: int = 120,
) -> bool:
    lock = _prune_expired(db, project_id)
    if lock is None or lock.holder_user_id != user_id:
        return False
    lock.expires_at = _expires_at(ttl_seconds)
    db.commit()
    return True


def release_lock(db: DBSession, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    lock = _prune_expired(db, project_id)
    if lock is None or lock.holder_user_id != user_id:
        return
    db.delete(lock)
    db.commit()


def release_all_for_user(db: DBSession, user_id: uuid.UUID) -> None:
    db.execute(delete(ProjectLock).where(ProjectLock.holder_user_id == user_id))
    db.commit()


def get_lock(db: DBSession, project_id: uuid.UUID) -> ProjectLock | None:
    return _prune_expired(db, project_id)


def list_locks_for_user(db: DBSession, user_id: uuid.UUID) -> list[ProjectLock]:
    return list(
        db.scalars(
            select(ProjectLock).where(ProjectLock.holder_user_id == user_id)
        ).all()
    )
