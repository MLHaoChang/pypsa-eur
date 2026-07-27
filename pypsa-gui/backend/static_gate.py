"""
Server-side port of `frontend/vite.auth-gate.ts`.

That gate is a Vite DEV-SERVER plugin: it rewrites `req.url` per request and
emits nothing into `dist/`. Serving the built SPA from FastAPI therefore needs
the logic reimplemented, not copied. `vite.config.ts:13` also sets
`appType: 'mpa'`, which disables Vite's own SPA history fallback, so there is no
built-in behaviour to fall back on either.

Two traps a stock `StaticFiles(html=True)` mount walks into:
  * `dist/index.html` is the LOGIN page and contains no React entry. A catch-all
    that serves it for `/projects` renders a sign-in form instead of the app.
  * Wiring "/" to spa.html instead loops: spa.html's pre-React boot gate does
    `location.replace('/?needLogin=…')` on a 401, and "/" would serve spa.html
    again.

Deliberately pure functions — the FastAPI wiring lives in main.py so this stays
testable without a TestClient.

One shape difference from the TypeScript original, which is not an oversight:
`vite.auth-gate.ts` carries a three-state `backend` ('auth-on' / 'auth-off' /
'unreachable') because a dev-server plugin probes the API over HTTP and has to
fail closed when it cannot reach it. In-process there is no such state — this
code IS the backend — so the parameter collapses to `local_mode`.
"""
from __future__ import annotations

SPA = "spa.html"
LOGIN = "index.html"

# `/src/`, `/@` and `/node_modules/` from the TS original are dev-server-only
# and have no meaning against a built dist. `/img/` and `/brand.css` are the
# real dist's other two asset roots.
_ASSET_PREFIXES = ("/assets/", "/img/", "/api/", "/favicon")
_ASSET_FILES = ("/brand.css",)

Decision = tuple[str, str]


def is_static_asset(path: str) -> bool:
    """True for anything served verbatim rather than routed."""
    if path.startswith(_ASSET_PREFIXES) or path in _ASSET_FILES:
        return True
    leaf = path.rsplit("/", 1)[-1]
    # Any leaf with an extension is a file request — except .html, which is a
    # document this gate is responsible for routing.
    return "." in leaf and not leaf.endswith(".html")


def decide_route(path: str, *, local_mode: bool, authed: bool) -> Decision:
    """Which document to serve. Returns ("serve", filename) or ("redirect", location)."""
    if local_mode:
        # No login exists. Every HTML route is the app — including "/", which is
        # what breaks the redirect loop described above.
        return ("serve", SPA)

    if path == "/login.html":
        # login.html, not index.html. vite.auth-gate.ts:49 passes this path
        # through to dist/login.html; the two are byte-identical today, but the
        # port should not quietly substitute one file for the other.
        return ("serve", "login.html")

    if authed:
        if path in ("/", "/index.html"):
            return ("redirect", "/projects")
        return ("serve", SPA)

    if path == f"/{SPA}":
        return ("redirect", "/")
    return ("serve", LOGIN)
