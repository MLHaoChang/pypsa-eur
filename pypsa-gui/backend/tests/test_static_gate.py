"""
Server-side port of the Vite SPA routing gate.

`frontend/vite.auth-gate.ts` is a DEV-SERVER plugin registered via
`configureServer` — it rewrites req.url per request and emits nothing into
dist/. Serving the built SPA from FastAPI therefore needs the logic
reimplemented rather than copied, and `vite.config.ts:13` sets
`appType: 'mpa'`, which disables Vite's own SPA history fallback.

Two traps a stock `StaticFiles(html=True)` catch-all walks straight into:
  * `dist/index.html` is the LOGIN document and carries no React entry, so
    serving it for `/projects` renders a sign-in form instead of the app.
  * Wiring "/" to spa.html instead loops — spa.html's pre-React boot gate does
    `location.replace('/?needLogin=…')` on a 401, and "/" would serve spa.html
    again.
"""
import pytest

from static_gate import decide_route, is_static_asset


@pytest.mark.parametrize("path", [
    "/assets/spa-B6BHlEqH.js", "/brand.css", "/img/logo.svg", "/favicon.ico", "/api/health",
])
def test_static_assets_pass_through(path):
    assert is_static_asset(path) is True


@pytest.mark.parametrize("path", ["/", "/projects", "/app", "/admin/users", "/login.html"])
def test_html_routes_are_not_static(path):
    assert is_static_asset(path) is False


@pytest.mark.parametrize("path", ["/", "/projects", "/app", "/admin/users"])
def test_local_mode_always_serves_the_spa(path):
    assert decide_route(path, local_mode=True, authed=False) == ("serve", "spa.html")


def test_local_mode_never_serves_the_login_document():
    """spa.html's boot gate redirects to '/' on a 401; serving index.html there loops."""
    assert decide_route("/", local_mode=True, authed=False) == ("serve", "spa.html")


def test_web_mode_anonymous_gets_the_login_document():
    assert decide_route("/", local_mode=False, authed=False) == ("serve", "index.html")
    assert decide_route("/projects", local_mode=False, authed=False) == ("serve", "index.html")


def test_web_mode_authed_deep_links_get_the_spa():
    assert decide_route("/projects", local_mode=False, authed=True) == ("serve", "spa.html")


def test_web_mode_authed_root_redirects_to_projects():
    assert decide_route("/", local_mode=False, authed=True) == ("redirect", "/projects")


def test_spa_html_is_never_served_directly_in_web_mode():
    assert decide_route("/spa.html", local_mode=False, authed=False) == ("redirect", "/")


def test_login_html_serves_login_html_not_index():
    """vite.auth-gate.ts:49 passes /login.html through to dist/login.html."""
    assert decide_route("/login.html", local_mode=False, authed=False) == ("serve", "login.html")
