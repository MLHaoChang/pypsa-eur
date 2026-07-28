"""
Environment, socket, and import ordering (phase 2a, Task 2).

Three properties, and the ORDER between them is the whole point:

    bind_socket()  ->  build_environment(port)  ->  apply_environment()  ->  import main

`get_settings()` and `security.allowed_origins()` are both `lru_cache`d and the
CORS allowlist is read at import time, so an environment applied after `import
main` is applied to nothing — the app keeps the two Vite dev origins and every
mutation from the shell's ephemeral port is refused. The port has to exist
before the environment is built, which is why the socket is bound first rather
than left to uvicorn.

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
    Not "contains" — *exactly*. The two Vite dev origins ship as the default
    and they are `localhost:5173`, which no packaged shell ever serves; leaving
    them allowlisted widens the CSRF trust boundary for a browser page that
    happens to be running a dev server.
    """
    env = launcher.build_environment(51234)

    assert env["CORS_ALLOWED_ORIGINS"] == "http://127.0.0.1:51234"
    assert "5173" not in env["CORS_ALLOWED_ORIGINS"]
    assert "localhost" not in env["CORS_ALLOWED_ORIGINS"]


@pytest.mark.parametrize(
    "name",
    [
        "DATABASE_URL",
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
    directly — so all six are asserted.

    `app_paths` already resolves these per-user and per-platform. A shell that
    pinned them would compute them from wherever the frozen app happens to be
    running, and a macOS `.app` launched from Finder has cwd `/`.
    """
    assert name not in launcher.build_environment(51234)


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
    Pins WHERE the probe looks, without depending on whether this particular
    checkout has the directory — the same assertion holds either way.
    """
    expected = _BACKEND / "projects"

    assert launcher.resolve_legacy_root() == (
        expected.resolve() if expected.is_dir() else None
    )


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


@pytest.mark.parametrize("module", ["main", "pypsa", "settings"])
def test_applying_the_environment_after_the_backend_is_imported_raises(module):
    """
    The failure this prevents is silent. `get_settings()` is `lru_cache`d, so a
    late `os.environ` write leaves the app on the default allowlist and the
    default database URL while every variable reads correctly in `os.environ` —
    the shell looks configured and the app is not.

    `pypsa` is in the list for a different reason: importing it resolves the
    matplotlib backend, and `MPLBACKEND=Agg` set afterwards is set too late.
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
