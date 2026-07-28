"""
Dynamic origin (spec C4).

The desktop shell binds an ephemeral port, so nothing can be pinned at build
time. `settings.py:33` ships the two Vite dev origins as a default, and that one
string drives BOTH the CORS allowlist and the CSRF Origin check — browsers send
`Origin` on same-origin non-GET, so a shell on a random port would otherwise get
`403 csrf_origin_rejected` on every mutation.

`get_settings()` and `security.allowed_origins()` are both `lru_cache`d, which
makes the ORDER a contract the launcher depends on: put the value in os.environ
first, then clear. These are characterization tests — a failure means that
contract changed and the launcher is no longer safe.
"""
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import local_mode
import main
import security
import settings as settings_module


def _reset():
    settings_module.get_settings.cache_clear()
    security.reset_caches_for_tests()


def _request(headers: dict[str, str]) -> Request:
    """
    A bare Request for calling `_csrf_rejection` directly.

    Going through TestClient in web mode would need a real login to obtain the
    session cookie the check keys on; the cookie's *value* is irrelevant here,
    only its presence, so a hand-built scope tests the gate precisely. Starlette
    parses `request.cookies` out of the `cookie` header.
    """
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/network/reset",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


def test_origin_allowlist_follows_the_env(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:51234")
    _reset()
    try:
        assert security.is_allowed_origin("http://127.0.0.1:51234") is True
        assert security.is_allowed_origin("http://127.0.0.1:5173") is False
    finally:
        _reset()


def test_the_caches_must_be_cleared_after_the_env_changes(monkeypatch):
    """
    Documents the ordering constraint the desktop shell depends on: set the env
    FIRST, then clear. Clearing first re-populates from the already-mutated env
    on the next read, which is why the naive version of this test asserts the
    wrong thing.
    """
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:5173")
    _reset()
    try:
        assert security.is_allowed_origin("http://127.0.0.1:40000") is False
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:40000")
        assert security.is_allowed_origin("http://127.0.0.1:40000") is False  # stale cache
        _reset()
        assert security.is_allowed_origin("http://127.0.0.1:40000") is True
    finally:
        _reset()


@pytest.mark.parametrize(
    "origin_header",
    ["origin", "referer"],
)
def test_csrf_origin_gate_accepts_an_ephemeral_port(monkeypatch, origin_header):
    """
    The real spec-C4 assertion, and the one the local-mode test below CANNOT
    make: prove the CSRF origin gate itself follows CORS_ALLOWED_ORIGINS.

    Runs in WEB mode deliberately. `_csrf_rejection` short-circuits on local
    mode at its first statement, so under local mode the origin allowlist is
    never consulted and any assertion about it passes vacuously.

    A request that clears the origin gate falls through to the double-submit
    token check and is refused as `csrf_token_invalid` — a DIFFERENT code from
    `csrf_origin_rejected`. That distinction is the assertion: the origin was
    accepted, and only the missing token stopped it.
    """
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:51234")
    _reset()
    try:
        settings = settings_module.get_settings()
        value = (
            "http://127.0.0.1:51234"
            if origin_header == "origin"
            else "http://127.0.0.1:51234/index.html"
        )
        rejection = main._csrf_rejection(_request({
            "cookie": f"{settings.session_cookie_name}=irrelevant",
            origin_header: value,
        }))
        assert rejection is not None
        assert rejection.status_code == 403
        assert b"csrf_token_invalid" in rejection.body, rejection.body
    finally:
        _reset()


def test_csrf_origin_gate_still_rejects_a_foreign_origin(monkeypatch):
    """The gate must not have been loosened into accepting anything."""
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:51234")
    _reset()
    try:
        settings = settings_module.get_settings()
        rejection = main._csrf_rejection(_request({
            "cookie": f"{settings.session_cookie_name}=irrelevant",
            "origin": "http://evil.example",
        }))
        assert rejection is not None
        assert rejection.status_code == 403
        assert b"csrf_origin_rejected" in rejection.body, rejection.body
    finally:
        _reset()


def test_the_launcher_origin_is_the_one_the_allowlist_accepts(monkeypatch):
    """
    The seam between the desktop shell and this file's contract (phase 2a,
    Task 2). Above, the allowlist is driven by a hand-written string; here it
    is driven by the one the launcher actually emits, parsed by the real
    `_normalise_origin`.

    The chain is URL -> Origin -> allowlist, all three from the launcher, so a
    host that disagreed between `app_url` and `build_environment` — `localhost`
    in one and `127.0.0.1` in the other, which is exactly the mistake macOS
    invites — fails here rather than at the first mutation from the window.
    """
    from desktop import launcher

    port = 51234
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", launcher.build_environment(port)["CORS_ALLOWED_ORIGINS"]
    )
    _reset()
    try:
        origin = security.origin_of_referer(launcher.app_url(port))
        assert origin == "http://127.0.0.1:51234"
        assert security.is_allowed_origin(origin) is True
        # The name and the number are different origins to a browser.
        assert security.is_allowed_origin("http://localhost:51234") is False
    finally:
        _reset()


def test_local_mode_mutation_is_not_403ed_from_an_ephemeral_origin(
    _auth_db, monkeypatch, tmp_path,
):
    """
    The desktop-visible property, end to end: a mutation carrying an Origin the
    build could not have known about is not refused.

    Note what this does and does not prove. Local mode exempts itself from
    `_csrf_rejection` entirely, so this passes via the bypass, NOT via the
    origin allowlist — the two tests above are what actually cover the gate.
    It is still worth pinning: it is the exact request the packaged shell makes,
    and it would catch a regression that re-armed CSRF for local mode.
    """
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
            r = c.post("/api/network/reset", headers={"Origin": "http://127.0.0.1:51234"})
            assert r.status_code != 403, r.text
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)
