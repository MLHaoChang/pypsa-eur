from __future__ import annotations

import uuid
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import main
from db import session as db_session_module
from db.models import AuthToken, Organization, OrgMembership, Project, User
from services import email_service
from services.auth_service import hash_password
from settings import get_settings


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSA_GUI_AUTH_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:5173")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    monkeypatch.setenv("LEGACY_ROOT", str(tmp_path / "legacy-unclaimed"))
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


def test_member_lists_org_members(member_client, auth_session_local) -> None:
    # The logged-in member and a second member of the SAME org should both be
    # returned by the non-admin directory. A user in a DIFFERENT org must not.
    with auth_session_local() as db:
        me = db.scalar(select(User).where(User.is_super_admin.is_(False)))
        assert me is not None
        my_membership = db.scalar(select(OrgMembership).where(OrgMembership.user_id == me.id))
        assert my_membership is not None
        my_org_id = my_membership.org_id

    colleague = _create_user(auth_session_local, email="colleague@example.com")
    _add_membership(auth_session_local, user_id=colleague.id, org_id=my_org_id, role="admin")

    other_org = _create_org(auth_session_local)
    outsider = _create_user(auth_session_local, email="outsider@example.com")
    _add_membership(auth_session_local, user_id=outsider.id, org_id=other_org.id, role="member")

    response = member_client.get("/api/auth/org-members")

    assert response.status_code == 200
    rows = response.json()
    emails = {row["email"] for row in rows}
    assert emails == {me.email, "colleague@example.com"}
    assert "outsider@example.com" not in emails
    for row in rows:
        assert set(row.keys()) == {"id", "email", "role"}
    by_email = {row["email"]: row for row in rows}
    assert by_email["colleague@example.com"]["role"] == "admin"


def test_org_members_requires_authentication(auth_session_local) -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/auth/org-members")

    assert response.status_code == 401


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


def test_admin_lists_and_claims_legacy_project_tree(
    admin_client,
    auth_session_local,
) -> None:
    legacy_root = get_settings().legacy_root
    (legacy_root / "Root").mkdir(parents=True)
    (legacy_root / "Child").mkdir(parents=True)
    (legacy_root / "Root" / "metadata.json").write_text(
        json.dumps({"name": "Root", "parent_project": None}),
        encoding="utf-8",
    )
    (legacy_root / "Child" / "metadata.json").write_text(
        json.dumps({"name": "Child", "parent_project": "Root"}),
        encoding="utf-8",
    )
    (legacy_root / "Root" / "network.nc").write_text("root", encoding="utf-8")
    (legacy_root / "Child" / "network.nc").write_text("child", encoding="utf-8")

    listed = admin_client.get("/api/admin/legacy-projects")

    assert listed.status_code == 200
    assert {row["name"] for row in listed.json()} == {"Root", "Child"}

    with auth_session_local() as db:
        admin = db.scalar(select(User).where(User.is_super_admin.is_(False)))
        assert admin is not None
        membership = db.scalar(select(OrgMembership).where(OrgMembership.user_id == admin.id))
        assert membership is not None

    claimed = admin_client.post(
        "/api/admin/legacy-projects/Root/claim",
        json={
            "owner_id": str(admin.id),
            "member_ids": [],
            "include_descendants": True,
        },
    )

    assert claimed.status_code == 201, claimed.text
    assert claimed.json()["root"]["name"] == "Root"
    assert {row["name"] for row in claimed.json()["claimed"]} == {"Root", "Child"}

    with auth_session_local() as db:
        root_project = db.scalar(select(Project).where(Project.name == "Root"))
        child_project = db.scalar(select(Project).where(Project.name == "Child"))
        assert root_project is not None
        assert child_project is not None
        assert root_project.org_id == membership.org_id
        assert child_project.parent_project_id == root_project.id


def test_admin_email_status_and_test_email(admin_client, mail_outbox) -> None:
    response = admin_client.get("/api/admin/email/status")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["smtp_host"] == "localhost"
    assert body["smtp_port"] == 1025
    assert body["smtp_from"] == "noreply@localhost"

    test_response = admin_client.post(
        "/api/admin/email/test",
        json={"to": "deliver@example.com"},
    )

    assert test_response.status_code == 200
    assert test_response.json() == {"ok": True, "to": "deliver@example.com"}
    assert len(mail_outbox) == 1
    assert mail_outbox[0]["to"] == "deliver@example.com"
    assert mail_outbox[0]["subject"] == "PyPSA GUI email test"


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
