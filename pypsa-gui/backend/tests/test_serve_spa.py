"""
Serving the built SPA from FastAPI (spec workstream C).

Placement is the whole task. FastAPI matches routes in registration order, and
`@app.get("/api/health")` is declared AFTER the last include_router — so a
catch-all inserted between them matches GET /api/health first,
`is_static_asset("/api/health")` returns True, and health 404s. That breaks
spa.html's pre-React boot gate and AuthModeProvider, which is a confusing
failure to diagnose from the browser. `test_api_routes_are_not_swallowed` is the
regression guard.

The dist fixture builds a throwaway tree rather than using the real
frontend/dist: the real one has content-hashed asset names that change on every
build, and depending on a build artifact would make these tests fail on a clean
checkout.
"""
import pytest
from fastapi.testclient import TestClient

import local_mode
import main
import settings as settings_module


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html data-pypsa-page='login'></html>", encoding="utf-8")
    (d / "login.html").write_text("<html data-pypsa-page='login'></html>", encoding="utf-8")
    (d / "spa.html").write_text("<html id='spa'></html>", encoding="utf-8")
    (d / "assets" / "spa.js").write_text("console.log(1)", encoding="utf-8")
    (d / "brand.css").write_text("body{}", encoding="utf-8")
    return d


@pytest.fixture
def local_spa_client(_auth_db, dist, monkeypatch, tmp_path):
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    # local mode's lifespan calls ensure_app_dirs(); without this it would
    # mkdir the developer's real ~/Library/Application Support/PyPSA GUI/.
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    # Set FIRST, then clear — clearing first would repopulate from the old env
    # on the next read. Same ordering contract as test_dynamic_origin.
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


def test_serves_spa_at_root(local_spa_client):
    r = local_spa_client.get("/")
    assert r.status_code == 200 and "id='spa'" in r.text


@pytest.mark.parametrize("path", ["/projects", "/app", "/admin/users"])
def test_serves_spa_for_deep_links(local_spa_client, path):
    r = local_spa_client.get(path)
    assert r.status_code == 200 and "id='spa'" in r.text


def test_assets_are_served_verbatim(local_spa_client):
    """
    Load-bearing for the mount being resolved per request rather than at import.
    `frontend_dist` points at the real dist when main.py is imported, so a mount
    that captured its directory then would serve THAT tree and 404 here.
    """
    assert local_spa_client.get("/assets/spa.js").status_code == 200
    assert local_spa_client.get("/brand.css").status_code == 200


def test_api_routes_are_not_swallowed(local_spa_client):
    """Regression guard for the catch-all placement."""
    r = local_spa_client.get("/api/health")
    assert r.status_code == 200 and "auth_enabled" in r.json()


def test_unknown_asset_404s_rather_than_returning_html(local_spa_client):
    assert local_spa_client.get("/assets/missing.js").status_code == 404


def test_traversal_is_refused(local_spa_client):
    assert local_spa_client.get("/assets/../../settings.py").status_code == 404


def test_head_is_supported(local_spa_client):
    assert local_spa_client.head("/projects").status_code == 200
