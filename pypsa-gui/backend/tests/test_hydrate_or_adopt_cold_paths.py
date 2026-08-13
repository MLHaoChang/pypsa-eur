"""
R2 — all four cold paths build-and-register under the shared hydrate lock.

The dispatcher is covered by `tests/test_context_fork_regression.py`. This file
covers the other three, each driven by a real concurrent race with the hydrate
deliberately slowed so the pre-lock window is wide enough to lose.

`resolve_for_session` is the one that makes the invariant true rather than
nearly true: it runs TWICE per authenticated request on EVERY route (the
`undo_snapshot_middleware` half at `main.py:525` and the FastAPI-constructor
dependency at `deps.py:78`), so under the 1.5 s queue poll it is by a wide
margin the most frequently executed context builder in the process.
"""
from __future__ import annotations

import threading

from services.pypsa_service import PyPSAService
from tests.conftest import build_network


def _slow_hydrate(monkeypatch, delay: float = 0.15) -> None:
    """Widen the check-then-build-then-register window the lock closes."""
    import time

    from routers import projects as projects_router

    real = projects_router._hydrate_context_from_disk

    def slow(ctx, src, name):
        time.sleep(delay)
        return real(ctx, src, name)

    # Every cold path imports this LAZILY at call time, so patching the module
    # attribute reaches all three.
    monkeypatch.setattr(projects_router, "_hydrate_context_from_disk", slow)


def _race(fn, n: int = 3) -> list:
    barrier = threading.Barrier(n)
    results: list = []
    errors: list = []

    def go() -> None:
        try:
            barrier.wait(10)
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 — surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == [], errors
    assert len(results) == n, results
    return results


def _evict(key: str) -> None:
    with PyPSAService._registry_lock:
        PyPSAService._contexts.pop(key, None)


def test_resolve_project_context_builds_one_context_under_a_race(
    client, install_network, tmp_projects_dir, registry_key_for, _auth_db, monkeypatch,
):
    from db.models import User
    from routers import deps

    _engine, session_local = _auth_db
    install_network(build_network(), name="Cold1")
    r = client.post("/api/projects/Cold1", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    key = registry_key_for("Cold1")
    _evict(key)
    _slow_hydrate(monkeypatch)

    def resolve():
        with session_local() as db:
            user = db.query(User).first()
            return deps.resolve_project_context("Cold1", db, user)

    contexts = _race(resolve)
    assert all(c is contexts[0] for c in contexts), "resolve_project_context forked"
    assert PyPSAService.get_context(key) is contexts[0]


def test_resolve_for_session_builds_one_context_under_a_race(
    client, install_network, tmp_projects_dir, registry_key_for, _auth_db, monkeypatch,
):
    from services import active_project
    from services.auth_service import resolve_session_row
    from settings import get_settings

    _engine, session_local = _auth_db
    install_network(build_network(), name="Cold2")
    r = client.post("/api/projects/Cold2", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    assert client.post("/api/projects/Cold2/activate").status_code == 200
    key = registry_key_for("Cold2")
    _evict(key)
    _slow_hydrate(monkeypatch)

    raw = client.cookies.get(get_settings().session_cookie_name)
    assert raw, "client has no session cookie"

    def resolve():
        with session_local() as db:
            row = resolve_session_row(db, raw)
            assert row is not None
            ctx, _slot = active_project.resolve_for_session(db, row)
            return ctx

    contexts = _race(resolve)
    assert all(c is contexts[0] for c in contexts), "resolve_for_session forked"
    assert PyPSAService.get_context(key) is contexts[0]


def test_activate_builds_one_context_under_a_race(
    client, install_network, tmp_projects_dir, registry_key_for, _auth_db, monkeypatch,
):
    from db.models import User
    from routers import projects as projects_router
    from services.auth_service import resolve_session_row
    from settings import get_settings

    _engine, session_local = _auth_db
    install_network(build_network(), name="Cold3")
    r = client.post("/api/projects/Cold3", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    key = registry_key_for("Cold3")
    _evict(key)
    _slow_hydrate(monkeypatch)

    raw = client.cookies.get(get_settings().session_cookie_name)

    def activate():
        with session_local() as db:
            user = db.query(User).first()
            row = resolve_session_row(db, raw)
            projects_router.activate_project("Cold3", db, user, row)
            return PyPSAService.get_context(key)

    contexts = _race(activate)
    assert contexts[0] is not None
    assert all(c is contexts[0] for c in contexts), "activate forked a context"
