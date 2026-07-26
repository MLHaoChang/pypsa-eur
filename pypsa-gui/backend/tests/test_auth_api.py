from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import main
from db.models import AuthToken, Session, User
from db import session as db_session_module
from services.auth_service import hash_password, issue_password_token, verify_password
from settings import get_settings


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:5173")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_session_local(db_engine, monkeypatch, auth_env):
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(db_session_module, "SessionLocal", testing_session_local)
    return testing_session_local


@pytest.fixture
def auth_client(auth_session_local):
    with TestClient(main.app) as client:
        yield client


def _create_user(
    session_local,
    *,
    password: str | None = None,
    status: str = "active",
) -> User:
    with session_local() as db:
        user = User(
            email=f"{uuid.uuid4()}@example.com",
            password_hash=hash_password(password) if password is not None else None,
            status=status,
            is_super_admin=False,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


@pytest.fixture
def user_with_password(auth_session_local) -> User:
    return _create_user(auth_session_local, password="secret-pass")


def test_login_sets_cookie(auth_client, user_with_password) -> None:
    response = auth_client.post(
        "/api/auth/login",
        json={"email": user_with_password.email, "password": "secret-pass"},
    )

    assert response.status_code == 200
    assert "pypsa_gui_session" in response.cookies
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]


def test_login_uses_secure_cookie_for_non_local_base_url(
    auth_client,
    auth_session_local,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.com")
    get_settings.cache_clear()
    user = _create_user(auth_session_local, password="secret-pass")

    response = auth_client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "secret-pass"},
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_me_requires_auth(auth_client) -> None:
    response = auth_client.get("/api/auth/me")

    assert response.status_code == 401


def test_protected_api_requires_auth_when_enabled(auth_client) -> None:
    response = auth_client.get("/api/changelog/")

    assert response.status_code == 401


def test_login_allows_me_and_logout_revokes_session(
    auth_client,
    auth_session_local,
    user_with_password,
) -> None:
    login_response = auth_client.post(
        "/api/auth/login",
        json={"email": user_with_password.email, "password": "secret-pass"},
    )

    assert login_response.status_code == 200

    me_response = auth_client.get("/api/auth/me")

    assert me_response.status_code == 200
    assert me_response.json()["email"] == user_with_password.email
    assert me_response.json()["status"] == "active"

    session_cookie = auth_client.cookies.get("pypsa_gui_session")
    assert session_cookie is not None

    logout_response = auth_client.post("/api/auth/logout")

    assert logout_response.status_code == 200
    assert auth_client.get("/api/auth/me").status_code == 401

    with auth_session_local() as db:
        stored_session = db.scalar(
            select(Session).where(Session.user_id == user_with_password.id)
        )

    assert stored_session is not None
    assert stored_session.revoked_at is not None


def test_forgot_password_returns_generic_success_for_unknown_email(auth_client) -> None:
    response = auth_client.post(
        "/api/auth/forgot-password",
        json={"email": "missing@example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_forgot_password_issues_reset_token_for_known_email(
    auth_client,
    auth_session_local,
    user_with_password,
) -> None:
    response = auth_client.post(
        "/api/auth/forgot-password",
        json={"email": user_with_password.email},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    with auth_session_local() as db:
        token = db.scalar(
            select(AuthToken).where(
                AuthToken.user_id == user_with_password.id,
                AuthToken.purpose == "reset_password",
            )
        )

    assert token is not None
    assert token.used_at is None


def test_set_password_consumes_token_and_activates_user(
    auth_client,
    auth_session_local,
) -> None:
    user = _create_user(auth_session_local, status="invited")
    with auth_session_local() as db:
        raw_token = issue_password_token(db, user.id, "set_password")

    response = auth_client.post(
        "/api/auth/set-password",
        json={"token": raw_token, "password": "new-secret-pass"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    with auth_session_local() as db:
        refreshed_user = db.get(User, user.id)
        stored_token = db.scalar(
            select(AuthToken).where(
                AuthToken.user_id == user.id,
                AuthToken.purpose == "set_password",
            )
        )

    assert refreshed_user is not None
    assert refreshed_user.status == "active"
    assert refreshed_user.password_hash is not None
    assert verify_password("new-secret-pass", refreshed_user.password_hash)
    assert stored_token is not None
    assert stored_token.used_at is not None


def test_reset_password_consumes_token_and_revokes_existing_sessions(
    auth_client,
    auth_session_local,
    user_with_password,
) -> None:
    login_response = auth_client.post(
        "/api/auth/login",
        json={"email": user_with_password.email, "password": "secret-pass"},
    )
    assert login_response.status_code == 200

    with auth_session_local() as db:
        raw_token = issue_password_token(db, user_with_password.id, "reset_password")

    response = auth_client.post(
        "/api/auth/reset-password",
        json={"token": raw_token, "password": "changed-secret-pass"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth_client.get("/api/auth/me").status_code == 401

    with auth_session_local() as db:
        refreshed_user = db.get(User, user_with_password.id)
        stored_token = db.scalar(
            select(AuthToken).where(
                AuthToken.user_id == user_with_password.id,
                AuthToken.purpose == "reset_password",
            )
        )
        sessions = db.scalars(
            select(Session).where(Session.user_id == user_with_password.id)
        ).all()

    assert refreshed_user is not None
    assert refreshed_user.password_hash is not None
    assert verify_password("changed-secret-pass", refreshed_user.password_hash)
    assert stored_token is not None
    assert stored_token.used_at is not None
    assert sessions
    assert all(session.revoked_at is not None for session in sessions)
