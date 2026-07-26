from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from db.models import AuthToken, Session, User
from services.auth_service import (
    consume_password_token,
    create_session,
    hash_password,
    issue_password_token,
    resolve_session,
    revoke_all_sessions_for_user,
    revoke_session,
    verify_password,
)


@pytest.fixture
def db(db_session):
    return db_session


def _make_user(db, *, status: str = "active") -> User:
    user = User(
        email=f"{uuid.uuid4()}@example.com",
        password_hash=None,
        status=status,
        is_super_admin=False,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_password_hash_roundtrip() -> None:
    password_hash = hash_password("secret-pass")

    assert password_hash != "secret-pass"
    assert verify_password("secret-pass", password_hash)
    assert not verify_password("wrong", password_hash)


def test_session_resolve_and_revoke(db) -> None:
    user = _make_user(db)

    raw_token, session = create_session(db, user.id)
    stored = db.scalar(select(Session).where(Session.id == session.id))

    assert stored is not None
    assert stored.token_hash != raw_token
    assert resolve_session(db, raw_token).id == user.id

    revoke_session(db, raw_token)

    assert resolve_session(db, raw_token) is None


def test_revoke_all_sessions_for_user(db) -> None:
    user = _make_user(db)
    other_user = _make_user(db)

    first_raw, _ = create_session(db, user.id)
    second_raw, _ = create_session(db, user.id)
    other_raw, _ = create_session(db, other_user.id)

    revoke_all_sessions_for_user(db, user.id)

    assert resolve_session(db, first_raw) is None
    assert resolve_session(db, second_raw) is None
    assert resolve_session(db, other_raw).id == other_user.id


def test_resolve_session_returns_none_when_expired(db) -> None:
    user = _make_user(db)
    raw_token, session = create_session(db, user.id)
    session.expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert resolve_session(db, raw_token) is None


def test_set_password_token_one_time(db) -> None:
    user = _make_user(db, status="invited")

    raw_token = issue_password_token(db, user.id, "set_password")
    stored = db.scalar(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == "set_password",
        )
    )

    assert stored is not None
    assert stored.token_hash != raw_token

    resolved_user = consume_password_token(db, raw_token, "set_password")

    assert resolved_user.id == user.id
    assert db.scalar(select(AuthToken.used_at).where(AuthToken.id == stored.id)) is not None
    assert consume_password_token(db, raw_token, "set_password") is None


def test_password_token_requires_matching_purpose(db) -> None:
    user = _make_user(db, status="invited")
    raw_token = issue_password_token(db, user.id, "reset_password")

    assert consume_password_token(db, raw_token, "set_password") is None
