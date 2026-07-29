"""
The launch chain and its logging (phase 2a, Task 5).

`webview.start()` blocks, must own the main thread, and may be called once per
process. That fixes the shape of the whole launch:

    main thread:  lock -> bind_socket() -> build_environment(port, legacy_root)
                  -> create splash -> webview.start(bootstrap)

    bootstrap:    apply_environment() -> import main -> serve(sockets=[sock])
                  -> wait_healthy() -> main window -> destroy splash

Two orderings inside that are load-bearing and neither is obvious:

  * the port comes from `sock.getsockname()[1]`, so the BIND precedes
    `build_environment` — get it wrong and the wrong allowlist is frozen at
    `import main`, silently.
  * the main window is created BEFORE the splash is destroyed. Destroying the
    last window makes `start()` return, which ends the process.

`bootstrap_sequence` takes every step as a callable for the same reason
`shutdown_sequence` does: it is the only way to assert the ORDER without a
window, and `gui.py` — the one module that imports `webview` — stays a thin
wiring layer over it.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from desktop import bootstrap

_BACKEND = Path(__file__).resolve().parent.parent


class _Chain:
    """Records the order of every bootstrap step."""

    def __init__(self, *, healthy=True):
        self.calls: list[str] = []
        self._healthy = healthy

    def _step(self, name, result=None):
        self.calls.append(name)
        return result

    def run(self, **overrides):
        kwargs = dict(
            apply_environment=lambda: self._step("apply_environment"),
            import_backend=lambda: self._step("import_backend", object()),
            serve=lambda app: self._step("serve"),
            wait_healthy=lambda: self._step("wait_healthy", self._healthy),
            show_main_window=lambda: self._step("show_main_window"),
            destroy_splash=lambda: self._step("destroy_splash"),
            report_failure=lambda message: self._step("report_failure"),
            progress=lambda stage: self.calls.append(f"progress:{stage}"),
        )
        kwargs.update(overrides)
        return bootstrap.bootstrap_sequence(**kwargs)


def test_the_backend_is_imported_only_after_the_environment_is_applied():
    """
    `get_settings()` and `security.allowed_origins()` are `lru_cache`d and the
    CORS allowlist is read at import, so an environment applied afterwards is
    applied to nothing — silently, with every variable reading back correctly.
    `apply_environment` raises in that case, but the ORDER is what stops it
    ever arising.
    """
    chain = _Chain()
    chain.run()

    steps = [c for c in chain.calls if not c.startswith("progress:")]
    assert steps.index("apply_environment") < steps.index("import_backend")


def test_the_main_window_is_created_BEFORE_the_splash_is_destroyed():
    """
    Destroying the last window makes `webview.start()` return, which ends the
    process. Destroy the splash first and the app exits during its own launch —
    on a fast machine, before the user sees anything at all.
    """
    chain = _Chain()
    chain.run()

    steps = [c for c in chain.calls if not c.startswith("progress:")]
    assert steps.index("show_main_window") < steps.index("destroy_splash")


def test_the_window_is_not_shown_until_the_backend_answers():
    """
    Otherwise the window loads before uvicorn is listening and the user sees a
    connection error rather than the app.
    """
    chain = _Chain()
    chain.run()

    steps = [c for c in chain.calls if not c.startswith("progress:")]
    assert steps.index("wait_healthy") < steps.index("show_main_window")


def test_a_backend_that_never_starts_is_REPORTED_not_left_on_the_splash():
    """
    Constraint #17: uvicorn calls `sys.exit(STARTUP_FAILURE)` when lifespan
    startup raises — on a worker thread, where `SystemExit` kills that thread
    and nothing else. Nobody is told. Without an explicit failure path the
    splash sits on "Starting…" forever and the user's only move is Force Quit.
    """
    chain = _Chain(healthy=False)

    ok = chain.run()

    assert ok is False
    steps = [c for c in chain.calls if not c.startswith("progress:")]
    assert "report_failure" in steps
    assert "show_main_window" not in steps, "a window was opened onto a dead backend"
    assert "destroy_splash" not in steps, "the splash went away with nothing to replace it"


def test_the_progress_stages_are_reported_in_order():
    """
    The stages exist because the first-run import runs synchronously inside
    `lifespan` and uvicorn accepts no connection until it returns — a copy of
    the whole legacy tree with no feedback otherwise.
    """
    chain = _Chain()
    chain.run()

    stages = [c.split(":", 1)[1] for c in chain.calls if c.startswith("progress:")]
    assert stages == list(bootstrap.STAGES), stages


def test_the_import_stage_has_no_timeout():
    """
    Constraint #3. The progress budget and the give-up timeout are DIFFERENT
    numbers: 113 MB over an OneDrive-redirected Documents folder can take
    minutes, and a shell that gave up would abandon a half-finished import
    whose lock then blocks the retry for an hour.
    """
    assert bootstrap.HEALTH_TIMEOUT is None or bootstrap.HEALTH_TIMEOUT > 600


# ── logging (constraint #16) ────────────────────────────────────────────────


def test_file_logging_writes_somewhere_the_user_can_reach(tmp_path, monkeypatch):
    """
    Constraint #16: `logging.getLogger(__name__)` with NO `basicConfig` and no
    `FileHandler` anywhere in the backend. In a frozen windowed build every
    `logger.exception` goes nowhere — including the first-run import's and the
    shutdown's, which are the two that matter most.
    """
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))

    path = bootstrap.install_file_logging()
    try:
        logging.getLogger("pypsa-gui.test").error("a message the user must be able to find")

        assert path is not None and path.exists(), path
        assert "a message the user must be able to find" in path.read_text()
    finally:
        bootstrap.remove_file_logging()


def test_installing_file_logging_twice_does_not_double_every_line(tmp_path, monkeypatch):
    """
    The shell installs this before `import main`, and a retry path could call
    it again. Two handlers on the root logger write every record twice, which
    turns a log into something nobody trusts.
    """
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path))

    path = bootstrap.install_file_logging()
    bootstrap.install_file_logging()
    try:
        logging.getLogger("pypsa-gui.test").error("exactly once please")

        assert path.read_text().count("exactly once please") == 1
    finally:
        bootstrap.remove_file_logging()


def test_logging_failure_does_not_stop_the_app_starting(tmp_path, monkeypatch):
    """
    A read-only or missing app-data directory must not be the reason the app
    will not launch. Logging is a diagnostic, not a prerequisite.
    """
    # A FILE where the directory should be, so `mkdir` fails for real. An
    # earlier version used a path containing a null byte, which `setenv` itself
    # rejects — so the test raised before ever reaching the function, and
    # proved nothing about it.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(blocker / "appdata"))

    assert bootstrap.install_file_logging() is None      # must not raise


# ── what must NOT be imported ───────────────────────────────────────────────


def test_importing_the_launcher_pulls_in_no_gui_toolkit():
    """
    Task 3's and Task 2's test files import `desktop.launcher`. If it reached
    `webview`, every one of them would fail collection on a headless box —
    which is every CI box.
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(_BACKEND)!r})
            from desktop import launcher, bootstrap  # noqa: F401

            leaked = [m for m in sys.modules if m == "webview" or m.startswith("webview.")]
            assert not leaked, leaked
            print("CLEAN")
        """)],
        capture_output=True, text=True, timeout=300,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEAN" in result.stdout


def test_importing_the_backend_resolves_no_windowing_toolkit():
    """
    `MPLBACKEND=Agg` fixes one known offender inside a ~500 MB closure; this
    catches the class. A windowing toolkit resolved at import competes with
    pywebview for the process's GUI ownership, and on macOS that is a hang
    rather than an error.

    Subprocess, because `conftest` has already imported `main` in this one.
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(_BACKEND)!r})
            from desktop import launcher
            launcher.apply_environment(launcher.build_environment(51234, None))

            import main  # noqa: F401

            banned = ("tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
                      "matplotlib.backends.backend_macosx",
                      "matplotlib.backends.backend_qtagg",
                      "matplotlib.backends.backend_tkagg")
            found = [m for m in banned if m in sys.modules]
            assert not found, found
            print("CLEAN")
        """)],
        capture_output=True, text=True, timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEAN" in result.stdout
