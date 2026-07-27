"""
Run the legacy import on first launch (phase 1b, Task 8 — spec F1).

Four properties, and every one of them is a way this could go wrong rather
than a feature:

  **Idempotence comes from receipts**, not an app-data marker. A marker is
  what lets a reinstall — new app-data, same projects folder — copy stale data
  over live work.

  **A zero-candidate run records nothing.** Otherwise the first launch before
  `PYPSAGUI_LEGACY_IMPORT_ROOT` is configured burns the marker and the import
  is skipped permanently.

  **A single-instance lock.** D11/H1 has not landed, so two launches can
  overlap; without the lock they interleave `copytree` calls into the same
  staging directory.

  **Startup never blocks on it.** A failed import must still yield a working
  app — the CLI is the retry path, and an app that will not boot is a worse
  outcome than an import that did not happen.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import local_mode
import main
from services import legacy_import
from settings import get_settings


def _write_project(root, name: str):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metadata.json").write_text(
        json.dumps({"name": name, "parent_project": None}), encoding="utf-8"
    )
    (directory / "network.nc").write_text(f"network:{name}", encoding="utf-8")
    return directory


@pytest.fixture
def desktop(_auth_db, tmp_path, monkeypatch):
    """A local-mode launch with isolated app data, projects root and legacy root."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setenv("PYPSAGUI_LOCAL_MODE", "1")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "Projects"))
    monkeypatch.setenv("PYPSAGUI_LEGACY_IMPORT_ROOT", str(legacy))
    get_settings.cache_clear()
    _engine, session_local = _auth_db
    try:
        yield legacy, session_local
    finally:
        with session_local() as db:
            local_mode.remove_local_identity(db)
        get_settings.cache_clear()


def _boot(session_local):
    with TestClient(main.app) as client:
        yield_health = client.get("/api/health")
    return yield_health


def _project_names(session_local) -> set[str]:
    from sqlalchemy import select

    from db.models import Project

    with session_local() as db:
        return {row.name for row in db.scalars(select(Project)).all()}


def test_a_first_launch_imports_the_legacy_tree(desktop):
    legacy, session_local = desktop
    _write_project(legacy, "Belgium Grid")

    with TestClient(main.app):
        pass

    assert _project_names(session_local) == {"Belgium Grid"}


def test_a_second_launch_imports_nothing_again(desktop):
    """From the receipts. An app-data marker would let a reinstall copy stale
    data over whatever the user has done since."""
    legacy, session_local = desktop
    _write_project(legacy, "Belgium Grid")

    with TestClient(main.app):
        pass
    with TestClient(main.app):
        pass

    assert _project_names(session_local) == {"Belgium Grid"}


def test_a_launch_with_no_candidates_does_not_burn_the_import(desktop):
    """
    The empty-root launch must leave the door open. Recording "done" here is
    what makes the import skip forever on a machine whose legacy root simply
    was not configured yet.
    """
    legacy, session_local = desktop

    with TestClient(main.app):
        pass
    assert _project_names(session_local) == set()

    _write_project(legacy, "Belgium Grid")
    with TestClient(main.app):
        pass

    assert _project_names(session_local) == {"Belgium Grid"}


def test_web_mode_never_imports(_auth_db, tmp_path, monkeypatch):
    """
    A hosted deployment has many tenants and no legacy tree of its own; an
    import at boot there would attach one user's directories to whichever org
    the code happened to pick.
    """
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _write_project(legacy, "Belgium Grid")
    monkeypatch.delenv("PYPSAGUI_LOCAL_MODE", raising=False)
    monkeypatch.setenv("PYPSAGUI_LEGACY_IMPORT_ROOT", str(legacy))
    get_settings.cache_clear()
    _engine, session_local = _auth_db
    try:
        with TestClient(main.app):
            pass
        assert _project_names(session_local) == set()
    finally:
        get_settings.cache_clear()


def test_a_failing_import_still_yields_a_booting_app(desktop, monkeypatch):
    """The CLI is the retry path. An app that will not start is a worse
    outcome than an import that did not happen."""
    legacy, session_local = desktop
    _write_project(legacy, "Belgium Grid")

    def _explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(legacy_import, "import_all", _explode)

    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200

    assert _project_names(session_local) == set()


def test_a_second_concurrent_start_does_not_double_import(desktop, monkeypatch):
    """
    The single-instance lock. D11/H1 has not landed, so two launches really can
    overlap — and without this they interleave `copytree` into one staging
    directory.
    """
    legacy, session_local = desktop
    _write_project(legacy, "Belgium Grid")

    calls = {"n": 0}
    real_import = legacy_import.import_all

    def _counting(*args, **kwargs):
        calls["n"] += 1
        # Re-enter while the lock is held: the second call must be refused
        # rather than running the copy a second time.
        main.run_first_run_import()
        return real_import(*args, **kwargs)

    monkeypatch.setattr(legacy_import, "import_all", _counting)

    with TestClient(main.app):
        pass

    assert calls["n"] == 1
    assert _project_names(session_local) == {"Belgium Grid"}


def test_the_lock_is_released_when_the_import_finishes(desktop):
    """Released in a `finally`, so the next launch is not refused by the
    previous one's lock."""
    legacy, session_local = desktop
    _write_project(legacy, "Belgium Grid")

    with TestClient(main.app):
        pass

    assert not main._import_lock_path().exists()
    _write_project(legacy, "Second Project")

    with TestClient(main.app):
        pass

    assert _project_names(session_local) == {"Belgium Grid", "Second Project"}


def test_a_fresh_lock_blocks_and_a_stale_one_is_reclaimed(desktop, monkeypatch):
    """
    A lock left by a KILLED process must not wedge every future launch. The
    trade is deliberate: a fresh lock is trusted (a real second instance), an
    old one is reclaimed, and the CLI is the retry path either way.
    """
    import os
    import time

    legacy, session_local = desktop
    _write_project(legacy, "Belgium Grid")

    lock = main._import_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("someone else", encoding="utf-8")

    with TestClient(main.app):
        pass
    assert _project_names(session_local) == set(), "a fresh lock must be respected"

    stale = time.time() - main._LOCK_STALE_SECONDS - 60
    os.utime(lock, (stale, stale))

    with TestClient(main.app):
        pass

    assert _project_names(session_local) == {"Belgium Grid"}
