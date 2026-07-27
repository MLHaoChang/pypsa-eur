"""
Retire the server-only surfaces in local mode (spec B6).

Three things that exist for a hosted multi-tenant deployment and are wrong on a
single-user desktop:

  routers/admin.py    nine multi-tenant endpoints, including a claim path that
                      shutil.moves whole project directories.
  login throttle      blocks for 15 minutes after 10 failed attempts, with a
                      process restart as the only escape. On a machine with one
                      user and no attacker, that is a support call.
  X-PyPSA-Replica     multi-replica test infrastructure. One process, one
                      machine, no proxy.

BOTH main.py guards must be RUNTIME, not import-time. conftest imports `main`
with PYPSAGUI_LOCAL_MODE unset, so an `if not is_local_mode():` wrapped around
include_router would register the router permanently and no fixture could
un-register it — these tests could never pass. That is the no-reload rule
colliding with the B6 requirement; runtime guards satisfy both.
"""
import pytest
from fastapi.testclient import TestClient

import local_mode
import main
import security


@pytest.fixture
def local_client(_auth_db, monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    # local mode's lifespan calls ensure_app_dirs(); without this it would
    # mkdir the developer's real ~/Library/Application Support/PyPSA GUI/.
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)


def test_admin_router_is_not_reachable(local_client):
    assert local_client.get("/api/admin/organizations").status_code in (404, 405)


def test_admin_router_still_works_in_web_mode(_auth_db, monkeypatch):
    """
    The guard must be conditional, not a deletion. Web mode still 401s here
    (no session cookie) rather than 404ing — which proves the route is
    registered and it is authentication, not the local-mode guard, refusing it.
    """
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    with TestClient(main.app) as c:
        c.cookies.clear()
        assert c.get("/api/admin/organizations").status_code == 401


def test_no_replica_header(local_client):
    present = {k.lower() for k in local_client.get("/api/health").headers}
    assert security.REPLICA_HEADER.lower() not in present


def test_replica_header_still_present_in_web_mode(client):
    """The default `client` fixture runs with local mode unset."""
    present = {k.lower() for k in client.get("/api/health").headers}
    assert security.REPLICA_HEADER.lower() in present


def test_login_throttle_is_disabled(monkeypatch):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    security.reset_login_throttle_for_tests()
    try:
        for _ in range(50):
            security.record_failed_login("127.0.0.1", local_mode.LOCAL_USER_EMAIL)
        assert security.login_retry_after("127.0.0.1", local_mode.LOCAL_USER_EMAIL) is None
    finally:
        security.reset_login_throttle_for_tests()


def test_throttle_still_active_in_web_mode(monkeypatch):
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    security.reset_login_throttle_for_tests()
    try:
        for _ in range(50):
            security.record_failed_login("127.0.0.1", "someone@example.com")
        assert security.login_retry_after("127.0.0.1", "someone@example.com") is not None
    finally:
        security.reset_login_throttle_for_tests()
