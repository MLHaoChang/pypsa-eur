"""
Local mode over the real app (spec workstream B).

NO importlib.reload and NO sys.modules surgery anywhere in this file.
`local_mode.is_local_mode()` reads os.environ per call, so the app object
conftest already imported serves both modes and a fixture only has to flip the
environment. Reloading would not work anyway — `del sys.modules["db.session"]`
is a no-op for `from db import session`, so the "reloaded" app keeps the old
module object and its monkeypatched SessionLocal, seeds into conftest's shared
database, and leaves `security`/`settings` split-brained for the rest of the
session.

Each fixture removes the seeded identity afterwards: conftest's
`_reset_tenant_tables` truncates only the project tables — users, orgs and
memberships persist for the whole session by design — so seeding without
cleanup leaks a super-admin into every later test.
"""
import pytest
from fastapi.testclient import TestClient

import local_mode
import main


@pytest.fixture
def local_client(_auth_db, monkeypatch, tmp_path):
    """Cookie-less client with local mode on, seeded into conftest's database."""
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


def test_health_reports_auth_disabled(local_client):
    """
    The SPA's boot contract: AuthModeProvider overwrites its compile-time flag
    from this value, and spa.html's pre-React gate skips /api/auth/me when it
    is false. Flipping this one field is what turns the login gate off.
    """
    assert local_client.get("/api/health").json()["auth_enabled"] is False


def test_health_still_reports_enabled_in_web_mode(client):
    """The default `client` fixture runs with local mode unset."""
    assert client.get("/api/health").json()["auth_enabled"] is True


def test_api_reachable_without_a_session_cookie(local_client):
    r = local_client.get("/api/projects/")
    assert r.status_code == 200, r.text


def test_mutation_succeeds_without_a_csrf_token(local_client):
    r = local_client.post("/api/network/reset")
    assert r.status_code != 403, r.text


def test_seeded_identity_is_visible(local_client):
    r = local_client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    assert r.json()["is_super_admin"] is True


def test_web_mode_still_401s_without_a_cookie(_auth_db, monkeypatch):
    """The web path must be completely unaffected."""
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    with TestClient(main.app) as c:
        c.cookies.clear()
        assert c.get("/api/projects/").status_code == 401


# ── a project whose folder the user deleted in Finder ───────────────────────


def test_a_project_whose_directory_is_gone_is_reported_as_MISSING(local_client, monkeypatch, tmp_path):
    """
    D13 puts projects in `~/Documents/PyPSA GUI/Projects/<name>/` **so a human
    can navigate them**. So a local user deleting a folder in Finder is the
    designed workflow meeting its obvious consequence, not misuse — and it
    cannot happen in the web deployment at all, where nobody has the disk.

    Measured in the packaged app: the folder was gone, `GET /api/projects/`
    still listed the project, and `GET /api/projects/<name>` returned **404**.
    The list is DB-backed by design ("a storage key with no row is invisible
    here"), and the reverse — a row with no storage — had no signal at all: the
    stub branch reports `bus_count: 0`, which is exactly what a real, empty
    project reports.

    Reported rather than hidden or purged. Hiding it loses the user's only
    handle on a row they still need to delete; purging it would drop rows for a
    project on a network share or external disk that simply is not mounted yet.
    """
    monkeypatch.setenv("PYPSAGUI_PROJECTS_ROOT", str(tmp_path / "projects"))

    assert local_client.post("/api/network/reset").status_code == 200
    assert local_client.post(
        "/api/network/buses", json={"name": "B0", "v_nom": 380.0}
    ).status_code == 201
    assert local_client.post("/api/projects/Vanishing").status_code == 200

    listed = {p["name"]: p for p in local_client.get("/api/projects/").json()}
    assert listed["Vanishing"]["missing"] is False, "a live project must not be flagged"

    # The Finder delete.
    import shutil
    from services import project_registry
    from db import session as db_session
    from db.models import Project as _Project

    with db_session.SessionLocal() as db:
        row = db.query(_Project).filter(_Project.name == "Vanishing").one()
        shutil.rmtree(project_registry.project_dir(row))

    listed = {p["name"]: p for p in local_client.get("/api/projects/").json()}
    assert "Vanishing" in listed, "the row is still there, so the user needs to see it"
    assert listed["Vanishing"]["missing"] is True, (
        "the project's files are gone but the list reports it as an ordinary "
        "empty project — clicking it 404s with no explanation"
    )
