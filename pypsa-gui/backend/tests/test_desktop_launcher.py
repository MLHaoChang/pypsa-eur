"""
Environment, socket, and import ordering (phase 2a, Task 2).

Three properties, and the ORDER between them is the whole point:

    bind_socket()  ->  build_environment(port)  ->  apply_environment()  ->  import main

`get_settings()` and `security.allowed_origins()` are both `lru_cache`d and the
CORS allowlist is read at import time, so an environment applied after `import
main` is applied to nothing — the app silently keeps the two Vite dev origins
and the shell's own origin is not allowlisted. The port has to exist before the
environment is built, which is why the socket is bound first rather than left
to uvicorn.

**What that does NOT mean: mutations being refused.** `_csrf_rejection` returns
`None` as its first statement in local mode, which `build_environment` turns
on, and the SPA is served by the backend at the same origin as the API — so the
window's requests are same-origin and never preflighted. The damage from a late
environment is a wrong allowlist and a wrong database path, not a broken app.
Overstating this is what got plan v2 rejected (verified constraint #1); it is
restated correctly here because the same overstatement reappeared in this
docstring three commits later.

**Why the import-ordering tests are subprocesses.** `tests/conftest.py` imports
`main` at module scope. An in-process `assert apply_environment() raises` is
therefore true for every test in this suite whatever the implementation does —
it would pass against a function whose body is `pass`. Each direction gets its
own interpreter.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from desktop import launcher

_BACKEND = Path(__file__).resolve().parent.parent


# ── the environment ─────────────────────────────────────────────────────────


def test_the_environment_pins_local_mode_and_a_headless_matplotlib():
    env = launcher.build_environment(51234)

    assert env["PYPSAGUI_LOCAL_MODE"] == "1"
    # Before `import pypsa`, or matplotlib resolves a windowing backend inside
    # a process that is about to own the GUI toolkit itself.
    assert env["MPLBACKEND"] == "Agg"


def test_the_allowlist_is_exactly_the_bound_origin():
    """
    Not "contains" — *exactly*.

    `settings.py` ships `http://localhost:5173,http://127.0.0.1:5173`. Note the
    second one is the loopback LITERAL, on the same host the shell binds — so
    the reason to drop them is not "no shell serves those" but that a developer
    running the Vite dev server alongside the app would be an allowlisted,
    credentialed origin against the desktop backend.
    """
    env = launcher.build_environment(51234)

    assert env["CORS_ALLOWED_ORIGINS"] == "http://127.0.0.1:51234"
    assert "5173" not in env["CORS_ALLOWED_ORIGINS"]
    assert "localhost" not in env["CORS_ALLOWED_ORIGINS"]


@pytest.mark.parametrize(
    "name",
    [
        "PROJECTS_ROOT",
        "LEGACY_ROOT",
        "FLAT_PROJECTS_ROOT",
        "PYPSAGUI_PROJECTS_ROOT",
        "PYPSAGUI_APP_DATA_DIR",
    ],
)
def test_the_shell_does_not_pin_any_storage_location(name):
    """
    Both spellings are live — `settings` binds the bare names through pydantic
    while `app_paths` reads the `PYPSAGUI_`-prefixed ones from `os.environ`
    directly — so both are asserted.

    `app_paths` already resolves these per-user and per-platform. A shell that
    pinned them would compute them from wherever the frozen app happens to be
    running, and a macOS `.app` launched from Finder has cwd `/`.

    **`DATABASE_URL` was in this list and has been removed** — see
    `test_the_shell_pins_the_database_because_a_stray_dotenv_outranks_the_default`.
    The reasoning above inverts for that one variable: not pinning it is what
    lets a cwd-relative value win.
    """
    assert name not in launcher.build_environment(51234)


def test_the_shell_pins_the_database_because_a_stray_dotenv_outranks_the_default(monkeypatch):
    """
    Measured, cwd `/`, nothing else changed:

        sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        unable to open database file
        uvicorn.error: Application startup failed. Exiting.

    `Settings.model_config` binds `env_file=<backend>/.env`, and pydantic ranks
    an env-file entry ABOVE a field default. `.env` ships a cwd-relative
    `sqlite+pysqlite:///./auth_dev.db` for dev, so `default_database_url()` —
    the absolute app-data path — never applies whenever that file exists. A
    macOS `.app` launched from Finder has cwd `/`, which no user can write to,
    so the app dies on the splash before any window appears.

    The five siblings above stay unpinned for the reason their test gives:
    anything the shell computed would be relative to where the frozen app
    happens to run. That argument does not reach this one. `app_data_dir()` is
    anchored at `~/Library/Application Support` / `%LOCALAPPDATA%` / `$XDG_DATA_HOME`
    and never at cwd, so pinning it removes a cwd dependency instead of adding one.
    """
    import app_paths

    # `tests/conftest.py:41` exports `DATABASE_URL=…:memory:` at import — for
    # the same reason this fix exists, so the suite never opens the developer's
    # real database. That makes every test in this file look to
    # `build_environment` like an operator who chose one, so the default branch
    # is unreachable until it is cleared.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    env = launcher.build_environment(51234)

    assert env["DATABASE_URL"] == app_paths.default_database_url()


def test_the_pinned_database_url_is_an_absolute_path(monkeypatch):
    """
    The property, not the spelling. `./auth_dev.db` is what broke the launch;
    asserting equality with `default_database_url()` alone would still pass if
    that helper ever regressed to a relative path.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)

    url = launcher.build_environment(51234)["DATABASE_URL"]

    assert url.startswith("sqlite")
    path = url.split("///", 1)[1]
    assert Path(path).is_absolute(), url


def test_an_operator_who_exported_a_database_url_keeps_it(monkeypatch):
    """
    A deliberate `DATABASE_URL=postgresql://…` in the environment is an operator
    decision; a `.env` on disk is not. They are distinguishable at this point in
    the chain and only here: `apply_environment` runs BEFORE `import main`, so
    `main.py`'s `load_dotenv` has not yet copied the file into `os.environ`.
    Anything already there was exported by a human.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone@example/db")

    assert "DATABASE_URL" not in launcher.build_environment(51234)


def test_an_empty_database_url_is_not_an_operator_decision(monkeypatch):
    """
    `DATABASE_URL=""` is inheritance noise, not a choice — `setx DATABASE_URL ""`
    on Windows persists across reboots and every shell, and a CI `env:` entry or
    a bare `export DATABASE_URL=` in a profile produces the same thing.

    A membership test (`"DATABASE_URL" not in os.environ`) reads it as an
    operator decision and suppresses the pin. Measured consequence, end to end:
    `env_ignore_empty` defaults to False, so `Settings().database_url` stays
    `''`; `db/session.py` builds the engine at module import; `import main` then
    raises `ArgumentError: Could not parse SQLAlchemy URL from given URL string`
    and the app dies on the splash — the exact failure this pin exists to
    eliminate, reached by a different door.

    `build_environment` states the identical rule eleven lines below this one,
    for `PYPSAGUI_LEGACY_IMPORT_ROOT`: *"Absent, never empty"*. It was not
    applied here.
    """
    monkeypatch.setenv("DATABASE_URL", "")

    assert "DATABASE_URL" in launcher.build_environment(51234)


def test_the_shell_must_not_manage_the_database_url():
    """
    `apply_environment` POPS every `_MANAGED` name that `build_environment`
    omitted — that is how an inherited `PYPSAGUI_LEGACY_IMPORT_ROOT` is
    neutralised. `DATABASE_URL` is omitted precisely when the operator set one,
    so managing it would delete the value the test above exists to protect.
    """
    assert "DATABASE_URL" not in launcher._MANAGED


def test_the_legacy_root_is_passed_through_when_there_is_one(tmp_path):
    """D10: the first-run import never fires unless the shell configures it."""
    env = launcher.build_environment(51234, tmp_path)

    assert env["PYPSAGUI_LEGACY_IMPORT_ROOT"] == str(tmp_path)


def test_no_legacy_root_means_the_variable_is_absent_not_empty():
    """
    `settings.legacy_import_root` is `Path | None`. An empty string would coerce
    to `Path(".")` — the current working directory — and the first-run import
    would inventory it.
    """
    assert "PYPSAGUI_LEGACY_IMPORT_ROOT" not in launcher.build_environment(51234, None)


# ── resolving the legacy root (D10) ─────────────────────────────────────────


def test_the_legacy_root_resolves_to_the_pre_desktop_project_store(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()

    assert launcher.resolve_legacy_root(tmp_path) == projects.resolve()


def test_an_absent_legacy_store_resolves_to_none(tmp_path):
    """
    Not an error. `None` means the import never fires, which is the declared F1
    deviation for a machine with nothing to migrate — every fresh install.
    """
    assert launcher.resolve_legacy_root(tmp_path) is None


def test_a_file_where_the_legacy_store_should_be_resolves_to_none(tmp_path):
    (tmp_path / "projects").write_text("not a directory")

    assert launcher.resolve_legacy_root(tmp_path) is None


def test_the_default_probe_is_the_backend_project_store():
    """
    Pins WHERE the probe looks, on the CONSTANT rather than on the outcome.

    The previous version asserted
    `resolve_legacy_root() == (expected if expected.is_dir() else None)`, which
    passes against `return None` on every machine where the directory is
    absent — and `pypsa-gui/.gitignore` ignores `backend/projects/`, so that is
    every fresh clone and every CI box. It only appeared to test something here
    because this developer has 113 MB of real projects sitting in that path.
    """
    assert launcher.PRE_DESKTOP_PROJECTS_DIR == (_BACKEND / "projects").resolve()


def test_the_default_probe_is_actually_the_one_resolve_uses(tmp_path):
    """
    Asserting on the constant is only worth anything if the function reads it.

    Points it at a directory that EXISTS and requires the answer to be that
    directory. The first attempt pointed it at a nonexistent path and asserted
    `is None` — which an implementation ignoring the constant also returns on
    any machine where `backend/projects/` is absent, i.e. every fresh clone,
    because `pypsa-gui/.gitignore` ignores it. That is byte-for-byte the
    vacuity this test was written to remove; it only bit here because this
    developer happens to have the directory.
    """
    import unittest.mock

    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()

    with unittest.mock.patch.object(
        launcher, "PRE_DESKTOP_PROJECTS_DIR", elsewhere
    ):
        assert launcher.resolve_legacy_root() == elsewhere.resolve()


# ── the socket ──────────────────────────────────────────────────────────────


def test_the_socket_binds_the_loopback_literal():
    """
    `127.0.0.1`, never `localhost`: on macOS that resolves to `::1` first, so a
    server bound to the name and a client dialling the number never meet — this
    repo's CLAUDE.md records it costing a session on the Vite dev server.
    `0.0.0.0` is the other wrong answer; it raises a Windows Firewall prompt on
    first launch and exposes the unauthenticated loopback API to the LAN.
    """
    sock = launcher.bind_socket()
    try:
        host, port = sock.getsockname()[:2]
        assert sock.family == socket.AF_INET
        assert host == "127.0.0.1"
        assert port != 0
    finally:
        sock.close()


def test_the_socket_does_not_set_so_reuseaddr():
    """
    On Windows `SO_REUSEADDR` permits binding a port another socket is actively
    listening on, which is the opposite of its POSIX meaning: two launches
    would both "succeed" and one would silently receive no connections.
    """
    sock = launcher.bind_socket()
    try:
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
    finally:
        sock.close()


def test_the_socket_is_already_listening_before_the_server_exists():
    """
    This is what closes the bind-to-serve race. The port has to be known before
    the environment is built, so it is bound well before uvicorn starts; if it
    were bound but not listening, a client connecting in that window would get
    ECONNREFUSED instead of waiting in the backlog.
    """
    sock = launcher.bind_socket()
    try:
        port = sock.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
    finally:
        sock.close()


def test_the_url_and_the_allowlisted_origin_agree():
    """
    The window loads `app_url`; the browser derives its `Origin` header from
    that URL. If the two ever disagree, every mutation from the shell is
    refused with `csrf_origin_rejected` in web mode and CORS-blocked from
    reading responses in both.
    """
    sock = launcher.bind_socket()
    try:
        port = sock.getsockname()[1]
        assert launcher.app_url(port).startswith(f"http://127.0.0.1:{port}")
        assert launcher.build_environment(port)["CORS_ALLOWED_ORIGINS"] == (
            f"http://127.0.0.1:{port}"
        )
    finally:
        sock.close()


# ── import ordering ─────────────────────────────────────────────────────────


def _subprocess(body: str) -> subprocess.CompletedProcess:
    script = f"import sys; sys.path.insert(0, {str(_BACKEND)!r})\n" + textwrap.dedent(
        body
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.mark.parametrize("module", ["main", "pypsa", "settings", "matplotlib"])
def test_applying_the_environment_after_the_backend_is_imported_raises(module):
    """
    The failure this prevents is silent. `get_settings()` is `lru_cache`d, so a
    late `os.environ` write leaves the app on the default allowlist and the
    default database URL while every variable reads correctly in `os.environ` —
    the shell looks configured and the app is not.

    `pypsa` is in the list for a different reason: importing it resolves the
    matplotlib backend, and `MPLBACKEND=Agg` set afterwards is set too late.

    `matplotlib` is listed in its OWN right, not covered by `pypsa`. Measured:
    `import matplotlib` alone resolves the backend to `macosx`, and setting
    `MPLBACKEND=Agg` after that leaves it on `macosx` with
    `backend_macosx` already loaded. Anything reaching matplotlib without
    going through pypsa — `gui.py`, `splash.py`, seaborn, cartopy, a
    PyInstaller hidden import — would otherwise let `apply_environment()`
    succeed with its most safety-critical variable already dead.
    """
    result = _subprocess(f"""
        import {module}  # noqa: F401
        from desktop import launcher

        try:
            launcher.apply_environment(launcher.build_environment(51234))
        except launcher.BackendAlreadyImported:
            print("REFUSED")
        else:
            print("APPLIED")
    """)

    assert result.returncode == 0, result.stderr
    assert "REFUSED" in result.stdout, result.stdout + result.stderr


def test_applying_the_environment_first_is_allowed_and_takes_effect():
    """
    The positive direction, and the one that proves the ordering contract end
    to end rather than just the guard: the value reaches `get_settings()`.

    The inherited environment already carries a different `CORS_ALLOWED_ORIGINS`
    (conftest sets one for the whole suite), so this also proves the launcher
    OVERRIDES rather than defers to what it inherited.
    """
    result = _subprocess("""
        from desktop import launcher

        launcher.apply_environment(launcher.build_environment(51234))

        import os
        assert os.environ["PYPSAGUI_LOCAL_MODE"] == "1"

        from settings import get_settings
        assert get_settings().cors_allowed_origins == "http://127.0.0.1:51234", (
            get_settings().cors_allowed_origins
        )

        import security
        assert security.is_allowed_origin("http://127.0.0.1:51234") is True
        assert security.is_allowed_origin("http://127.0.0.1:5173") is False
        print("OK")
    """)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_an_inherited_legacy_root_is_CLEARED_not_left_standing():
    """
    `build_environment` deliberately omits `PYPSAGUI_LEGACY_IMPORT_ROOT` when
    there is nothing to migrate — but `os.environ.update()` cannot remove a
    key, so an inherited one survived and `run_first_run_import` fired against
    a tree the shell never chose, `copytree`-ing it into the user's Documents.

    Not a hypothetical inheritance: `tests/conftest.py` pops this exact
    variable and says why — "a developer who exported it, exactly what the
    importer's rehearsal instructions teach". The harness defended against a
    state the product did not.

    Subprocess because the value has to be inherited from a real parent
    environment, which is the whole mechanism.
    """
    result = _subprocess("""
        import os
        os.environ["PYPSAGUI_LEGACY_IMPORT_ROOT"] = "/somewhere/the/shell/never/chose"

        from desktop import launcher
        launcher.apply_environment(launcher.build_environment(51234, None))

        assert "PYPSAGUI_LEGACY_IMPORT_ROOT" not in os.environ, os.environ[
            "PYPSAGUI_LEGACY_IMPORT_ROOT"
        ]
        print("CLEARED")
    """)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEARED" in result.stdout


def test_a_resolved_legacy_root_still_overrides_an_inherited_one():
    """The clearing must not throw away the value the shell DID choose."""
    result = _subprocess("""
        import os
        os.environ["PYPSAGUI_LEGACY_IMPORT_ROOT"] = "/the/stale/inherited/one"

        from desktop import launcher
        from pathlib import Path
        launcher.apply_environment(
            launcher.build_environment(51234, Path("/the/chosen/one"))
        )

        assert os.environ["PYPSAGUI_LEGACY_IMPORT_ROOT"] == "/the/chosen/one", (
            os.environ["PYPSAGUI_LEGACY_IMPORT_ROOT"]
        )
        print("OVERRIDDEN")
    """)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OVERRIDDEN" in result.stdout


def test_the_launcher_imports_no_part_of_the_backend():
    """
    The guard above can only fire if importing the launcher does not itself
    import what it is guarding against. A launcher that pulled in `settings`
    at module scope would refuse every launch.

    Also keeps the launcher importable on a box where the backend's own
    dependencies are absent — which is what Task 5's webview-free test needs.
    """
    result = _subprocess("""
        from desktop import launcher  # noqa: F401

        import sys
        leaked = [m for m in ("main", "pypsa", "settings", "security") if m in sys.modules]
        assert not leaked, leaked
        print("OK")
    """)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_a_frozen_build_is_told_where_its_SPA_is(monkeypatch, tmp_path):
    """
    Measured on the first frozen build that started: `/` returned **503
    "Frontend not built"** while `frontend/dist/spa.html` was sitting in the
    bundle.

    `settings.frontend_dist` defaults to `<backend>/../frontend/dist`, derived
    from `settings.__file__`. Inside a PyInstaller bundle that resolves to
    `Contents/frontend/dist`, one level above where the data actually lands.
    The user sees a JSON error page instead of the app, which reads as a broken
    backend rather than a packaging problem.

    The plan predicted this and left it to workstream I: *"`settings.frontend_dist`
    binds bare `FRONTEND_DIST` with no `PYPSAGUI_` alias. Workstream I will set
    it through `build_environment`."*

    Pinned to `sys._MEIPASS`, so it says nothing about an unfrozen run.
    """
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "_MEIPASS", str(tmp_path), raising=False)

    env = launcher.build_environment(51234)

    assert env["FRONTEND_DIST"] == str(dist)


def test_an_unfrozen_run_is_not_told_where_its_SPA_is():
    """
    The obvious mutation is to set it unconditionally from `_MEIPASS`, which is
    absent outside a bundle and would pin the SPA to a path built from `""`.
    Development must keep using the `settings` default.
    """
    assert "FRONTEND_DIST" not in launcher.build_environment(51234)
