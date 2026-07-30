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


# ── importing a folder of pre-desktop projects (packaged app) ───────────────


def test_import_folder_is_REFUSED_when_auth_is_on(client, tmp_path):
    """
    The security property, asserted before the feature.

    This route takes a SERVER-SIDE PATH from the request body and copies what it
    finds into the caller's project store. In the desktop app the server and the
    user are the same person, so that is merely a file dialog. On a web
    deployment it is an arbitrary-filesystem read for any authenticated user —
    point it at `/etc`, or at another tenant's storage root, and the importer
    inventories and copies whatever it can parse.

    So the gate is not "admin only", it is "this deployment has no other
    tenants". 404 rather than 403, matching the four `unclaimed` doors which
    are closed the same way and for the same reason.
    """
    r = client.post("/api/projects/import-folder", json={"path": str(tmp_path)})

    assert r.status_code == 404, r.text


def test_import_folder_previews_without_copying_anything(local_client, monkeypatch, tmp_path):
    """
    `apply=false` is the default because the destructive version of this button
    is one click on a path the user typed. `import_all(apply=False)` already
    reports what WOULD happen and touches nothing.
    """
    monkeypatch.setenv("PYPSAGUI_PROJECTS_ROOT", str(tmp_path / "dest"))
    source = tmp_path / "legacy"
    (source / "OldProject").mkdir(parents=True)
    (source / "OldProject" / "network.nc").write_bytes(b"not really a network")

    r = local_client.post("/api/projects/import-folder", json={"path": str(source)})

    assert r.status_code == 200, r.text
    body = r.json()
    # `would_import`, NOT `imported` — a dry run fills the former and leaves the
    # latter empty. Returning only `imported` made a preview of a folder full of
    # projects answer with every list empty, i.e. "nothing to import".
    assert body["applied"] is False
    assert "OldProject" in body["would_import"], body
    assert body["imported"] == [], body
    assert not (tmp_path / "dest" / "OldProject").exists(), "a preview must copy nothing"


def test_import_folder_rejects_a_path_that_is_not_a_directory(local_client, tmp_path):
    """
    A typed path is the input. Saying which of "does not exist" and "is a file"
    went wrong is the difference between a fixable mistake and a shrug.
    """
    missing = local_client.post("/api/projects/import-folder", json={"path": str(tmp_path / "nope")})
    assert missing.status_code == 400
    assert "does not exist" in missing.json()["detail"].lower()

    afile = tmp_path / "a.txt"
    afile.write_text("x")
    not_dir = local_client.post("/api/projects/import-folder", json={"path": str(afile)})
    assert not_dir.status_code == 400
    assert "not a folder" in not_dir.json()["detail"].lower()


def test_import_folder_actually_copies_when_applied(local_client, monkeypatch, tmp_path):
    """
    The preview test passing says nothing about the path that writes. And the
    property that matters most here is that the SOURCE survives: this is the
    button a user points at their old working directory, and `legacy_import`
    copies rather than moves for exactly that reason.
    """
    dest = tmp_path / "dest"
    monkeypatch.setenv("PYPSAGUI_PROJECTS_ROOT", str(dest))
    source = tmp_path / "legacy"
    (source / "OldProject").mkdir(parents=True)
    (source / "OldProject" / "network.nc").write_bytes(b"not really a network")

    r = local_client.post(
        "/api/projects/import-folder", json={"path": str(source), "apply": True}
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert "OldProject" in body["imported"], body
    assert body["failed"] == [] and body["collisions"] == [], body
    assert (source / "OldProject" / "network.nc").exists(), (
        "the importer moved the user's source instead of copying it"
    )
    assert "OldProject" in {p["name"] for p in local_client.get("/api/projects/").json()}


def test_importing_the_same_folder_twice_does_not_duplicate(local_client, monkeypatch, tmp_path):
    """
    A user who clicks Import twice, or points at the same folder next week,
    must not get `OldProject (2)`. Idempotence comes from the receipts the
    importer writes, not from a marker.
    """
    monkeypatch.setenv("PYPSAGUI_PROJECTS_ROOT", str(tmp_path / "dest"))
    source = tmp_path / "legacy"
    (source / "OldProject").mkdir(parents=True)
    (source / "OldProject" / "network.nc").write_bytes(b"not really a network")

    first = local_client.post(
        "/api/projects/import-folder", json={"path": str(source), "apply": True}
    ).json()
    second = local_client.post(
        "/api/projects/import-folder", json={"path": str(source), "apply": True}
    ).json()

    assert first["imported"] == ["OldProject"]
    assert second["imported"] == [], second
    assert second["already_imported"] == ["OldProject"], second
    names = [p["name"] for p in local_client.get("/api/projects/").json()]
    assert names.count("OldProject") == 1, names


# ── importing a project bundle ──────────────────────────────────────────────


def test_an_EMPTY_bundle_says_it_is_empty(local_client, tmp_path):
    """
    A user pointed the app at `3_nodes_system.pypsaproj.zip` in Documents and
    reported the import as broken. The file was **0 bytes** — a download that
    never wrote anything, from before the export path was fixed to fetch first
    and save second. The app was right to refuse it.

    But it said "Not a valid zip bundle: File is not a zip file", which reads as
    "this app cannot open my project" rather than "this file is empty". The user
    went looking for a bug in the importer. An empty file is the one malformed
    case worth naming, because the fix is somewhere else entirely: get the file
    again.
    """
    empty = tmp_path / "3_nodes_system.pypsaproj.zip"
    empty.write_bytes(b"")

    with empty.open("rb") as fh:
        r = local_client.post("/api/projects/import_bundle", files={"file": (empty.name, fh, "application/zip")})

    assert r.status_code == 400
    detail = r.json()["detail"].lower()
    assert "empty" in detail, detail
    assert "0 bytes" in detail or "no data" in detail, detail


def test_a_truncated_bundle_still_reports_the_underlying_reason(local_client, tmp_path):
    """
    The empty case is special-cased; everything else must keep the reason it had.
    A partial download is NOT empty and the user needs the distinction.
    """
    broken = tmp_path / "half.pypsaproj.zip"
    broken.write_bytes(b"PK\x03\x04 truncated junk that is not a zip")

    with broken.open("rb") as fh:
        r = local_client.post("/api/projects/import_bundle", files={"file": (broken.name, fh, "application/zip")})

    assert r.status_code == 400
    assert "empty" not in r.json()["detail"].lower(), r.json()
