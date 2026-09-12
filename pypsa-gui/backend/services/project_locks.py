from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
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
        try:
            db.commit()
        except IntegrityError:
            # A concurrent acquire inserted the lock first: treat as contention
            # (caller maps None to HTTP 409) rather than surfacing a 500.
            db.rollback()
            return None
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


def serialize_lock(
    db: DBSession, project_id: uuid.UUID, user_id: uuid.UUID | None
) -> dict[str, object] | None:
    """
    The `lock` member of the shared `project_locked` wire shape, or None.

    Lives here rather than in `routers/projects.py` so the write middleware in
    `main.py` can reach it too: `main.py` cannot import a router module at
    request time without a circular import, and its 409 was the one emitter
    whose `detail` carried no holder — leaving the frontend's read-only banner
    with nobody to name. `routers.projects._serialize_project_lock` delegates
    here, so the three emitters cannot drift apart.

    Returns None rather than raising on a DB error (M1): `get_lock` is not
    read-only — `_prune_expired` DELETEs + commits an expired row, so two
    writes racing one expiry can raise `StaleDataError` from here. A 409 that
    says `lock: null` is strictly better than the 500 that failure would
    otherwise turn a correct refusal into.
    """
    from db.models import User

    try:
        lock = get_lock(db, project_id)
        if lock is None:
            return None
        holder = db.get(User, lock.holder_user_id)
        return {
            "holder_email": (
                holder.email if holder is not None else str(lock.holder_user_id)
            ),
            "yours": lock.holder_user_id == user_id,
        }
    except Exception:  # noqa: BLE001 — see the docstring
        return None


def list_locks_for_user(db: DBSession, user_id: uuid.UUID) -> list[ProjectLock]:
    return list(
        db.scalars(
            select(ProjectLock).where(ProjectLock.holder_user_id == user_id)
        ).all()
    )
