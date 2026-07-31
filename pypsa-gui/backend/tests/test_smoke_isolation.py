"""
The harness isolation guard must cover the database, not just the two paths.

Every acceptance harness in `backend/smoke/` sets `PYPSAGUI_APP_DATA_DIR` and
`PYPSAGUI_PROJECTS_ROOT` and treats that as full isolation. It is not.

`settings.py` declares `database_url` with `default_factory=app_paths.
default_database_url` — which DOES follow `PYPSAGUI_APP_DATA_DIR` — but it also
declares `env_file=backend/.env`, and pydantic-settings ranks the env file
ABOVE a default_factory. `backend/.env` pins a cwd-relative `DATABASE_URL`, so
the env file wins and the harness writes its auth database wherever it happened
to be launched from, while faithfully reporting that app-data was redirected.

Measured, not theorised: an agent running an "isolated" backend from the repo
root created a stray `auth_dev.db` there — a file on the credential gate's own
forbidden list, because it carries a password hash.

The three harnesses also each carried their own copy of the guard (4, 4 and 7
assertions), which is the drift this consolidates.
"""
from __future__ import annotations

import pytest

from smoke.isolation import IsolationError, require_isolated_environment


def _ok(tmp_path) -> dict[str, str]:
    return {
        "PYPSAGUI_APP_DATA_DIR": str(tmp_path / "appdata"),
        "PYPSAGUI_PROJECTS_ROOT": str(tmp_path / "projects"),
        "DATABASE_URL": f"sqlite+pysqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
    }


def test_a_fully_isolated_environment_is_accepted(tmp_path):
    require_isolated_environment(_ok(tmp_path))


@pytest.mark.parametrize("missing", [
    "PYPSAGUI_APP_DATA_DIR", "PYPSAGUI_PROJECTS_ROOT", "DATABASE_URL",
])
def test_every_variable_is_required(tmp_path, missing):
    env = _ok(tmp_path)
    del env[missing]
    with pytest.raises(IsolationError, match=missing):
        require_isolated_environment(env)


def test_database_url_is_required_even_when_both_paths_are_set(tmp_path):
    # The whole point. Both paths isolated, database not — which is exactly
    # what every harness looked like before this guard existed.
    env = _ok(tmp_path)
    del env["DATABASE_URL"]
    with pytest.raises(IsolationError, match="DATABASE_URL"):
        require_isolated_environment(env)


@pytest.mark.parametrize("var", ["PYPSAGUI_APP_DATA_DIR", "PYPSAGUI_PROJECTS_ROOT"])
def test_a_path_inside_documents_is_refused(tmp_path, var):
    env = _ok(tmp_path)
    env[var] = str(pytest.importorskip("pathlib").Path.home() / "Documents" / "scratch")
    with pytest.raises(IsolationError, match="Documents"):
        require_isolated_environment(env)


def test_a_sqlite_database_inside_documents_is_refused(tmp_path):
    from pathlib import Path
    env = _ok(tmp_path)
    target = (Path.home() / "Documents" / "scratch" / "db.sqlite").as_posix()
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{target}"
    with pytest.raises(IsolationError, match="Documents"):
        require_isolated_environment(env)


def test_a_cwd_relative_sqlite_url_is_refused(tmp_path):
    # `backend/.env`'s own form. A relative URL resolves against whatever
    # directory the harness was launched from, which is how the stray
    # auth_dev.db appeared at the repo root.
    env = _ok(tmp_path)
    env["DATABASE_URL"] = "sqlite+pysqlite:///./auth_dev.db"
    with pytest.raises(IsolationError, match="relative"):
        require_isolated_environment(env)


def test_a_non_sqlite_database_url_is_left_alone(tmp_path):
    # A Postgres URL has no filesystem path to police. Requiring it to be set
    # is the isolation guarantee; where it points is the operator's business.
    env = _ok(tmp_path)
    env["DATABASE_URL"] = "postgresql+psycopg://user@localhost:5432/throwaway"
    require_isolated_environment(env)


@pytest.mark.parametrize("bare", ["PROJECTS_ROOT", "FLAT_PROJECTS_ROOT", "LEGACY_ROOT"])
def test_a_bare_settings_name_is_refused(tmp_path, bare):
    # pydantic binds the bare field name directly, beating the default_factory
    # that reads PYPSAGUI_*. Only accept_coldstart.py checked this; the other
    # two harnesses ran with full isolation theatre.
    env = _ok(tmp_path)
    env[bare] = "/tmp/decoy"
    with pytest.raises(IsolationError, match=bare):
        require_isolated_environment(env)


def test_the_real_projects_tree_is_refused(tmp_path):
    from pathlib import Path
    backend = Path(__file__).resolve().parent.parent
    env = _ok(tmp_path)
    env["PYPSAGUI_PROJECTS_ROOT"] = str(backend / "projects")
    with pytest.raises(IsolationError, match="real projects tree"):
        require_isolated_environment(env)


def test_a_subdirectory_of_the_real_projects_tree_is_refused(tmp_path):
    # accept_shutdown.py's copy checked equality only, so a subdirectory of the
    # 113 MB real tree would have passed there. The shared guard checks
    # ancestry too, which is what accept_downloads.py's copy already did.
    from pathlib import Path
    backend = Path(__file__).resolve().parent.parent
    env = _ok(tmp_path)
    env["PYPSAGUI_PROJECTS_ROOT"] = str(backend / "projects" / "scratch")
    with pytest.raises(IsolationError, match="real projects tree"):
        require_isolated_environment(env)


def test_a_windows_absolute_sqlite_path_is_accepted(tmp_path):
    # This repo is developed on Windows and macOS both. `Path.is_absolute()`
    # answers only for the host platform, so a Windows URL read on macOS would
    # look relative and a correctly-isolated Windows harness would be refused.
    env = _ok(tmp_path)
    env["DATABASE_URL"] = "sqlite+pysqlite:///C:/acceptance/appdata/acc.db"
    require_isolated_environment(env)
    env["DATABASE_URL"] = r"sqlite+pysqlite:///C:\acceptance\appdata\acc.db"
    require_isolated_environment(env)


def test_a_windows_relative_sqlite_path_is_still_refused(tmp_path):
    env = _ok(tmp_path)
    env["DATABASE_URL"] = r"sqlite+pysqlite:///appdata\acc.db"
    with pytest.raises(IsolationError, match="relative"):
        require_isolated_environment(env)
