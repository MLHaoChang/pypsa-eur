from __future__ import annotations

import uuid
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import main
from db import session as db_session_module
from db.models import AuthToken, Organization, OrgMembership, User
from services import email_service
from services.auth_service import hash_password
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
def mail_outbox():
    email_service.reset_outbox_for_tests()
    yield email_service.OUTBOX
    email_service.reset_outbox_for_tests()


def _create_org(session_local, *, name: str | None = None) -> Organization:
    with session_local() as db:
        organization = Organization(
            name=name or f"Org {uuid.uuid4()}",
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(organization)
        db.commit()
        db.refresh(organization)
        return organization


def _create_user(
    session_local,
    *,
    email: str | None = None,
    password: str = "secret-pass",
    status: str = "active",
    is_super_admin: bool = False,
) -> User:
    with session_local() as db:
        user = User(
            email=email or f"{uuid.uuid4()}@example.com",
            password_hash=hash_password(password),
            status=status,
            is_super_admin=is_super_admin,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _add_membership(
    session_local,
    *,
    user_id,
    org_id,
    role: str,
) -> OrgMembership:
    with session_local() as db:
        membership = OrgMembership(user_id=user_id, org_id=org_id, role=role)
        db.add(membership)
        db.commit()
        db.refresh(membership)
        return membership


def _login(client: TestClient, *, email: str, password: str = "secret-pass") -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


@pytest.fixture
def member_client(auth_session_local) -> TestClient:
    org = _create_org(auth_session_local)
    member = _create_user(auth_session_local)
    _add_membership(auth_session_local, user_id=member.id, org_id=org.id, role="member")

    with TestClient(main.app) as client:
        _login(client, email=member.email)
        yield client


@pytest.fixture
def admin_client(auth_session_local) -> TestClient:
    org = _create_org(auth_session_local)
    admin = _create_user(auth_session_local)
    _add_membership(auth_session_local, user_id=admin.id, org_id=org.id, role="admin")

    with TestClient(main.app) as client:
        _login(client, email=admin.email)
        yield client


@pytest.fixture
def super_admin_client(auth_session_local) -> TestClient:
    super_admin = _create_user(auth_session_local, is_super_admin=True)

    with TestClient(main.app) as client:
        _login(client, email=super_admin.email)
        yield client


def test_member_cannot_create_user(member_client) -> None:
    response = member_client.post(
        "/api/admin/users",
        json={"email": "x@example.com", "role": "member"},
    )

    assert response.status_code == 403


def test_admin_creates_user_and_issues_token(admin_client, auth_session_local, mail_outbox) -> None:
    response = admin_client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "role": "member"},
    )

    assert response.status_code == 201
    assert response.json()["email"] == "new@example.com"
    assert response.json()["role"] == "member"
    assert response.json()["status"] == "invited"
    assert len(mail_outbox) == 1
    assert mail_outbox[0]["to"] == "new@example.com"
    assert mail_outbox[0]["metadata"]["purpose"] == "set_password"
    assert "/set-password?token=" in mail_outbox[0]["body"]

    with auth_session_local() as db:
        created_user = db.scalar(select(User).where(User.email == "new@example.com"))
        assert created_user is not None
        membership = db.scalar(
            select(OrgMembership).where(OrgMembership.user_id == created_user.id)
        )
        token = db.scalar(
            select(AuthToken).where(
                AuthToken.user_id == created_user.id,
                AuthToken.purpose == "set_password",
            )
        )

    assert membership is not None
    assert membership.role == "member"
    assert token is not None
    assert token.used_at is None


def test_super_admin_creates_organization(super_admin_client, auth_session_local) -> None:
    response = super_admin_client.post(
        "/api/admin/organizations",
        json={"name": "New Org"},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "New Org"

    with auth_session_local() as db:
        created_org = db.scalar(select(Organization).where(Organization.name == "New Org"))

    assert created_org is not None


def test_bootstrap_super_admin_script_creates_active_super_admin(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "bootstrap-super-admin.db"
    script_path = Path(__file__).resolve().parent.parent / "tools" / "bootstrap_super_admin.py"

    assert script_path.exists()

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    get_settings.cache_clear()

    spec = importlib.util.spec_from_file_location("bootstrap_super_admin", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    exit_code = module.main(["--email", "owner@example.com", "--password", "super-secret-pass"])

    assert exit_code == 0

    engine = create_engine(get_settings().database_url)
    try:
        testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        with testing_session_local() as db:
            created_user = db.scalar(select(User).where(User.email == "owner@example.com"))
            assert created_user is not None
            assert created_user.is_super_admin is True
            assert created_user.status == "active"
            assert created_user.password_hash is not None
    finally:
        engine.dispose()
