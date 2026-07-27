"""
End-to-end local mode against the REAL build output (spec A5, D6).

test_serve_spa.py drives a hand-written dist. That is the right shape for
testing the routing logic, but the two traps the routing exists to avoid are
only real against actual build output: `index.html` genuinely IS the login
document, and every asset reference in it is genuinely root-absolute. So these
run against `frontend/dist/` and skip when it has not been built.

`POST /api/projects/{name}` is a DESTRUCTIVE SAVE — it serialises the current
in-memory network to disk under that name, overwriting anything already there.
It is safe here only because conftest pins PROJECTS_ROOT to a mkdtemp at import
(conftest.py:57-58); the project is deleted again afterwards so a rerun does not
inherit the previous run's state.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app_paths
import local_mode
import main
import settings as settings_module

_REAL_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@pytest.fixture
def real_dist_client(_auth_db, monkeypatch, tmp_path):
    if not (_REAL_DIST / "spa.html").is_file():
        pytest.skip("frontend not built — run `npm run build` in pypsa-gui/frontend")
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    # local mode's lifespan calls ensure_app_dirs(); without this it would
    # mkdir the developer's real ~/Library/Application Support/PyPSA GUI/.
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("FRONTEND_DIST", str(_REAL_DIST))
    # Set FIRST, then clear: clearing first repopulates from the old env.
    settings_module.get_settings.cache_clear()
    _engine, session_local = _auth_db
    with session_local() as db:
        local_mode.ensure_local_identity(db)
    try:
        with TestClient(main.app) as c:
            c.cookies.clear()
            yield c
    finally:
        settings_module.get_settings.cache_clear()
        with session_local() as db:
            local_mode.remove_local_identity(db)


def test_root_serves_the_react_entry_not_the_login_page(real_dist_client):
    """The trap: dist/index.html IS the login document."""
    body = real_dist_client.get("/").text
    assert 'data-pypsa-page="login"' not in body
    assert "/assets/" in body


def test_real_assets_resolve(real_dist_client):
    """
    The other trap: dist's asset references are root-absolute, so anything but a
    document-root mount 404s every one of them. Parsed out of the real document
    rather than hardcoded — the filenames are content-hashed and change on every
    build.
    """
    body = real_dist_client.get("/").text
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', body)
    assert assets, "no root-absolute asset references found in spa.html"
    for asset in assets[:5]:
        assert real_dist_client.get(asset).status_code == 200, asset


def test_full_local_journey(real_dist_client):
    assert real_dist_client.get("/api/health").json()["auth_enabled"] is False
    assert real_dist_client.get("/api/projects/").status_code == 200
    created = real_dist_client.post("/api/projects/smoke-test")
    assert created.status_code in (200, 201), created.text
    try:
        listed = real_dist_client.get("/api/projects/").json()
        assert any(p.get("name") == "smoke-test" for p in listed), listed
        assert real_dist_client.get("/api/network/buses").status_code == 200
    finally:
        # PROJECTS_ROOT is a session-scoped mkdtemp, so this survives the test
        # and a rerun would otherwise inherit it.
        real_dist_client.delete("/api/projects/smoke-test")


def test_no_writable_path_resolves_inside_the_source_tree(monkeypatch, tmp_path):
    """Spec D6 — as a test rather than a shell snippet, covering all four paths."""
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    for var in ("PROJECTS_ROOT", "LEGACY_ROOT", "FLAT_PROJECTS_ROOT", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    settings_module.get_settings.cache_clear()
    try:
        # _env_file=None is load-bearing: backend/.env carries a CWD-relative
        # DATABASE_URL, and pydantic-settings ranks dotenv ABOVE field defaults,
        # so without this the probe reads that value, resolves inside the source
        # tree, and fails for entirely the wrong reason.
        s = settings_module.Settings(_env_file=None)
        backend = Path(app_paths.__file__).resolve().parent
        db_file = Path(s.database_url.split("///", 1)[1])
        for p in (s.projects_root, s.legacy_root, s.flat_projects_root, db_file):
            resolved = Path(p).resolve()
            assert backend not in resolved.parents and resolved != backend, p
    finally:
        settings_module.get_settings.cache_clear()
