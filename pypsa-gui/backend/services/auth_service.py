from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DBSession

from db.models import AuthToken, Session, User
from settings import get_settings

_PASSWORD_HASHER = PasswordHash.recommended()


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _new_raw_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _PASSWORD_HASHER.verify(password, password_hash)


def create_session(db: DBSession, user_id: uuid.UUID) -> tuple[str, Session]:
    raw_token = _new_raw_token()
    session = Session(
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        expires_at=_now_utc() + timedelta(hours=get_settings().session_ttl_hours),
        revoked_at=None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return raw_token, session


def resolve_session(db: DBSession, raw_token: str) -> User | None:
    token_hash = _hash_token(raw_token)
    session = db.scalar(select(Session).where(Session.token_hash == token_hash))
    if session is None:
        return None
    if session.revoked_at is not None:
        return None
    if _ensure_utc(session.expires_at) <= _now_utc():
        return None
    user = db.get(User, session.user_id)
    if user is None or user.status != "active":
        return None
    return user


def revoke_session(db: DBSession, raw_token: str) -> None:
    token_hash = _hash_token(raw_token)
    session = db.scalar(select(Session).where(Session.token_hash == token_hash))
    if session is None or session.revoked_at is not None:
        return
    session.revoked_at = _now_utc()
    db.commit()


def revoke_all_sessions_for_user(db: DBSession, user_id: uuid.UUID) -> None:
    sessions = db.scalars(select(Session).where(Session.user_id == user_id)).all()
    revoked_at = _now_utc()
    changed = False
    for session in sessions:
        if session.revoked_at is None:
            session.revoked_at = revoked_at
            changed = True
    if changed:
        db.commit()


def issue_password_token(db: DBSession, user_id: uuid.UUID, purpose: str) -> str:
    now = _now_utc()
    db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )

    raw_token = _new_raw_token()
    auth_token = AuthToken(
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        purpose=purpose,
        expires_at=now + timedelta(hours=get_settings().password_token_ttl_hours),
        used_at=None,
    )
    db.add(auth_token)
    db.commit()
    return raw_token


def _claim_password_token(
    db: DBSession,
    raw_token: str,
    purpose: str,
) -> tuple[uuid.UUID, datetime] | None:
    now = _now_utc()
    token_hash = _hash_token(raw_token)
    auth_token = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash,
            AuthToken.purpose == purpose,
        )
    )
    if auth_token is None:
        return None

    claimed_rows = db.execute(
        update(AuthToken)
        .where(
            AuthToken.id == auth_token.id,
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed_rows.rowcount != 1:
        return None

    return auth_token.user_id, now


def consume_password_token(db: DBSession, raw_token: str, purpose: str) -> User | None:
    claimed = _claim_password_token(db, raw_token, purpose)
    if claimed is None:
        return None

    user_id, _claimed_at = claimed
    user = db.get(User, user_id)
    db.commit()
    return user


def set_password_from_token(
    db: DBSession,
    raw_token: str,
    purpose: str,
    password: str,
) -> User | None:
    claimed = _claim_password_token(db, raw_token, purpose)
    if claimed is None:
        return None

    user_id, now = claimed
    user = db.get(User, user_id)
    if user is None:
        return None

    user.password_hash = hash_password(password)
    user.status = "active"

    sessions = db.scalars(select(Session).where(Session.user_id == user.id)).all()
    for session in sessions:
        if session.revoked_at is None:
            session.revoked_at = now

    db.commit()
    db.refresh(user)
    return user
