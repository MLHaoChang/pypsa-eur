"""
`/unclaimed` is closed in local mode (phase 1b, Task 1).

`services/legacy_migrate._scan_root(projects_root, pre_auth_layout=True)`
classifies every non-UUID-named directory at the top of `projects_root` as a
claimable leftover, and `POST /unclaimed/{name}/import` then `shutil.move`s it
(`legacy_migrate.py:290`). While storage paths are `<org_uuid>/<project_uuid>/`
that scan matches nothing, because `_is_uuid_named` skips the org roots.

Task 4 puts project directories at the top of `projects_root` under their
human-readable names. From that commit on, every local project looks exactly
like a claimable leftover, and one call moves a live project out from under its
own row. `claim_legacy_project:230-232` refuses a name that already has a row,
so the exposure is projects whose directory name was **sanitised or
collision-suffixed** — precisely the ones Task 2 creates.

So this lands FIRST, before the layout moves. It is fully independent of the
rest of the phase.

Two doors, both closed here: `routers/projects.py` (mounted in local mode) and
`routers/admin.py` (already 404'd wholesale by phase 1a's router-level gate —
these routes carry their own guard as well, so the protection survives someone
re-opening the admin router). The frontend already treats 404 as "nothing to
import" (`api/projects.ts:83`), so no frontend change is needed.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import local_mode
import main
from services.legacy_migrate import list_legacy_projects
from settings import get_settings


@pytest.fixture
def legacy_tree(tmp_path, monkeypatch):
    """
    A pre-auth project sitting where the scanner looks.

    Without it every assertion below would pass on an empty tree, and the 404
    could not be distinguished from "nothing to list".
    """
    root = tmp_path / "legacy"
    project = root / "Old Study"
    project.mkdir(parents=True)
    (project / "metadata.json").write_text(
        json.dumps({"name": "Old Study", "parent_project": None}), encoding="utf-8"
    )
    (project / "network.nc").write_text("network", encoding="utf-8")

    monkeypatch.setenv("LEGACY_ROOT", str(root))
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "tenant-projects"))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def local_client(_auth_db, legacy_tree, monkeypatch, tmp_path):
    # Set the env FIRST, then clear the settings cache (global constraint).
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


def test_the_scanner_really_sees_the_seeded_project(legacy_tree):
    """
    Non-vacuity guard for everything below. If the fixture stops producing a
    listable project, the 404 assertions start passing for the wrong reason.
    """
    assert [p.name for p in list_legacy_projects()] == ["Old Study"]


def test_list_unclaimed_is_404_in_local_mode(local_client):
    """
    Local mode injects the local user, so the caller IS authenticated — without
    the guard this route returns 200 and lists "Old Study".
    """
    assert local_client.get("/api/projects/unclaimed").status_code == 404


def test_import_unclaimed_is_404_in_local_mode(local_client):
    """The one that matters: without the guard this `shutil.move`s the tree."""
    assert local_client.post("/api/projects/unclaimed/Old Study/import").status_code == 404


def test_both_routes_are_still_registered_in_web_mode(anon_client):
    """
    The guard must be conditional, not a deletion. 401 (not 404) proves the
    route is registered and it is authentication refusing the anonymous caller.
    Deleting the routes outright would pass a 404-only assertion.
    """
    assert anon_client.get("/api/projects/unclaimed").status_code == 401
    assert anon_client.post("/api/projects/unclaimed/Whatever/import").status_code == 401


def _bare_admin_app() -> FastAPI:
    """
    `routers/admin.py` mounted WITHOUT main.py's router-level local-mode gate.

    Phase 1a already 404s the whole admin router locally, so mounting through
    `main.app` cannot observe whether these two routes carry a guard of their
    own — the outer gate answers first either way. This is the only way to see
    the second layer.
    """
    from routers import admin

    app = FastAPI()
    app.include_router(admin.router, prefix="/api/admin")
    return app


def test_admin_legacy_doors_carry_their_own_guard(monkeypatch, legacy_tree):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    payload = {"owner_id": str(uuid.uuid4())}
    with TestClient(_bare_admin_app()) as c:
        assert c.get("/api/admin/legacy-projects").status_code == 404
        assert c.post("/api/admin/legacy-projects/Old Study/claim", json=payload).status_code == 404


def test_admin_legacy_doors_are_untouched_in_web_mode(monkeypatch, legacy_tree):
    """Same two routes, same bare app, local mode off: 401, not 404."""
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    payload = {"owner_id": str(uuid.uuid4())}
    with TestClient(_bare_admin_app()) as c:
        assert c.get("/api/admin/legacy-projects").status_code == 401
        assert c.post("/api/admin/legacy-projects/Old Study/claim", json=payload).status_code == 401
