"""
U-1 — the HTTP surface that lets the packaged app be given an API key.

The routes live on the CHAT router rather than the admin one, and that is
load-bearing: `main.py` mounts `admin.router` behind
`Depends(local_mode.reject_in_local_mode)`, so every `/api/admin/*` route 404s
in the desktop app — the exact deployment that ships without `backend/.env` and
therefore has no key. A setting placed there would be unreachable in the only
place it is needed.

The gate is `is_super_admin`, matching `solve_queue.clear_finished` (P-1): one
process-global environment variable is shared by every organisation on the
instance, so an ORG admin has no authority over it. Both conftest identities
are org admins with `is_super_admin=False`, which is precisely the tier that
must be refused — a test using only `client` would pass against no gate at all.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app_paths
import main
from db.models import OrgMembership, User
from services import app_secrets
from tests.conftest import attach_session

KEY = "ANTHROPIC_API_KEY"
SAMPLE = "sk-ant-api03-EXAMPLE-not-a-real-key-wxyz"
ENDPOINT = "/api/chat/settings/api-key"


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    """Redirect `user.env` to a tmp dir and restore the process env after."""
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    previous_value = os.environ.get(KEY)
    previous_shell = app_secrets._SHELL_NAMES
    os.environ.pop(KEY, None)
    app_secrets._SHELL_NAMES = frozenset()
    yield
    app_secrets._SHELL_NAMES = previous_shell
    if previous_value is None:
        os.environ.pop(KEY, None)
    else:
        os.environ[KEY] = previous_value


def _seed_super_admin(session_local, org_id):
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"key-super-admin-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=None,
            status="active",
            is_super_admin=True,
            created_at=datetime.now(tz=timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id, org_id=org_id, role="admin"))
        db.commit()
        return user.id


@pytest.fixture
def super_admin_client(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    user_id = _seed_super_admin(session_local, seeded_identity["org_id"])
    try:
        with TestClient(main.app) as c:
            yield attach_session(c, session_local, user_id)
    finally:
        with session_local() as db:
            row = db.get(User, user_id)
            if row is not None:
                db.delete(row)
                db.commit()


# ── authorization ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "method, kwargs",
    [
        ("get", {}),
        ("put", {"json": {"value": SAMPLE}}),
        ("delete", {}),
    ],
)
def test_an_org_admin_is_refused(client, method, kwargs):
    """`client` is an ORG admin (`role="admin"`, `is_super_admin=False`)."""
    response = getattr(client, method)(ENDPOINT, **kwargs)
    assert response.status_code == 403, response.text
    assert "super-admin" in response.json()["detail"]


@pytest.mark.parametrize(
    "method, kwargs",
    [
        ("get", {}),
        ("put", {"json": {"value": SAMPLE}}),
        ("delete", {}),
    ],
)
def test_an_anonymous_caller_is_refused(anon_client, method, kwargs):
    response = getattr(anon_client, method)(ENDPOINT, **kwargs)
    assert response.status_code == 401, response.text


def test_a_refused_write_stores_nothing(client):
    client.put(ENDPOINT, json={"value": SAMPLE})
    assert not app_paths.user_env_file().exists()
    assert KEY not in os.environ


# ── the happy path ──────────────────────────────────────────────────────────
def test_a_super_admin_can_store_a_key_and_it_takes_effect_at_once(super_admin_client):
    response = super_admin_client.put(ENDPOINT, json={"value": SAMPLE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is True
    assert body["source"] == "settings"

    # The whole point of U-1: the chat panel comes alive with no restart, which
    # a packaged `.app` gives the user no way to perform.
    health = super_admin_client.get("/api/chat/health").json()
    assert health["anthropic_api_key_present"] is True


def test_the_key_is_never_returned_by_any_route(super_admin_client):
    super_admin_client.put(ENDPOINT, json={"value": SAMPLE})
    for response in (
        super_admin_client.get(ENDPOINT),
        super_admin_client.get("/api/chat/health"),
    ):
        assert SAMPLE not in response.text
    assert super_admin_client.get(ENDPOINT).json()["hint"] == "…" + SAMPLE[-4:]


def test_delete_forgets_the_key(super_admin_client):
    super_admin_client.put(ENDPOINT, json={"value": SAMPLE})
    response = super_admin_client.delete(ENDPOINT)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False
    assert super_admin_client.get("/api/chat/health").json()["anthropic_api_key_present"] is False


@pytest.mark.parametrize("bad", ["", "   ", "sk-ant-with\nnewline"])
def test_an_unusable_value_is_rejected_with_a_showable_message(super_admin_client, bad):
    response = super_admin_client.put(ENDPOINT, json={"value": bad})
    assert response.status_code == 422, response.text
    # The message is rendered verbatim in the settings form, so it has to be a
    # sentence rather than pydantic's nested error structure.
    assert isinstance(response.json()["detail"], str)
    assert KEY not in os.environ
