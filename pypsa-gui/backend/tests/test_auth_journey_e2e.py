"""
End-to-end API journey for multi-user auth (v1).

Walks the same path a browser reviewer follows:
  bootstrap super-admin → login → create org → invite user →
  set password from email token → login as invitee → /me + projects list

Marked ``auth_smoke`` so it runs with:
  pixi run python -m pytest -m auth_smoke
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import main
from db import session as db_session_module
from db.models import Organization, OrgMembership, User
from services import email_service
from services.auth_service import hash_password
from settings import get_settings

pytestmark = pytest.mark.auth_smoke

_TOKEN_RE = re.compile(r"/set-password\?token=([^\s\"'<>]+)")


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


@pytest.fixture
def client(auth_session_local):
    with TestClient(main.app) as test_client:
        yield test_client


def _seed_super_admin(session_local, *, email: str, password: str) -> User:
    with session_local() as db:
        user = User(
            email=email,
            password_hash=hash_password(password),
            status="active",
            is_super_admin=True,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _extract_set_password_token(body: str) -> str:
    match = _TOKEN_RE.search(body)
    assert match is not None, f"set-password token missing from email body: {body!r}"
    return match.group(1)


def test_auth_journey_landing_to_invited_member(
    client: TestClient,
    auth_session_local,
    mail_outbox,
) -> None:
    """Full happy path used for browser review of the auth UX."""
    super_email = f"super-{uuid.uuid4().hex[:8]}@example.com"
    super_password = "SuperSecret1!"
    member_email = f"member-{uuid.uuid4().hex[:8]}@example.com"
    member_password = "MemberSecret1!"
    org_name = f"Acme {uuid.uuid4().hex[:6]}"

    _seed_super_admin(auth_session_local, email=super_email, password=super_password)

    # 1) Landing login as platform super-admin
    login = client.post(
        "/api/auth/login",
        json={"email": super_email, "password": super_password},
    )
    assert login.status_code == 200, login.text
    assert "pypsa_gui_session" in login.cookies
    # Step 0a: every subsequent write in this journey is state-changing and
    # rides a session cookie, so it needs the double-submit CSRF token.
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["email"] == super_email
    assert me_body["is_super_admin"] is True

    # 2) Create organization
    org_resp = client.post("/api/admin/organizations", json={"name": org_name})
    assert org_resp.status_code == 201, org_resp.text
    org = org_resp.json()
    assert org["name"] == org_name
    org_id = org["id"]

    orgs = client.get("/api/admin/organizations")
    assert orgs.status_code == 200
    assert any(row["id"] == org_id for row in orgs.json())

    # 3) Invite member into that org
    invite = client.post(
        "/api/admin/users",
        json={"email": member_email, "role": "member", "org_id": org_id},
    )
    assert invite.status_code == 201, invite.text
    invite_body = invite.json()
    assert invite_body["email"] == member_email
    assert invite_body["status"] == "invited"
    assert invite_body["role"] == "member"
    assert invite_body["email_sent"] is True

    assert len(mail_outbox) == 1
    assert mail_outbox[0]["to"] == member_email
    assert mail_outbox[0]["metadata"]["purpose"] == "set_password"
    token = _extract_set_password_token(mail_outbox[0]["body"])

    # 4) Super-admin signs out; invitee sets password (public endpoint)
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    set_pw = client.post(
        "/api/auth/set-password",
        json={"token": token, "password": member_password},
    )
    assert set_pw.status_code == 200, set_pw.text
    assert set_pw.json()["ok"] is True

    with auth_session_local() as db:
        member = db.scalar(select(User).where(User.email == member_email))
        assert member is not None
        assert member.status == "active"
        membership = db.scalar(
            select(OrgMembership).where(OrgMembership.user_id == member.id)
        )
        assert membership is not None
        assert str(membership.org_id) == org_id
        org_row = db.get(Organization, membership.org_id)
        assert org_row is not None
        assert org_row.name == org_name

    # 5) Invitee logs in and reaches projects home data
    member_login = client.post(
        "/api/auth/login",
        json={"email": member_email, "password": member_password},
    )
    assert member_login.status_code == 200, member_login.text
    client.headers["X-CSRF-Token"] = member_login.json()["csrf_token"]

    member_me = client.get("/api/auth/me")
    assert member_me.status_code == 200
    member_me_body = member_me.json()
    assert member_me_body["email"] == member_email
    assert member_me_body["is_super_admin"] is False
    assert member_me_body["role"] == "member"
    assert member_me_body["org_id"] == org_id

    projects = client.get("/api/projects/")
    assert projects.status_code == 200
    assert isinstance(projects.json(), list)

    # Member cannot create orgs
    denied = client.post("/api/admin/organizations", json={"name": "Should Fail"})
    assert denied.status_code == 403
