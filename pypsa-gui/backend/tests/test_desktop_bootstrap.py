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


# ── the socket on a FAILED launch ───────────────────────────────────────────
#
# `bind_socket()` runs on the main thread before `import main`, so on every
# launch-failure path something already owns a LISTENING socket with a backlog
# of 128. The process then deliberately stays alive to show the error on the
# splash — so "the launch failed" and "the port is free again" are different
# claims, and only the first was ever true.


def _gui():
    """
    Deliberately not `pytest.importorskip`. A skip is how two desktop tests
    passed silently for a whole review cycle; these guard a socket that stays
    bound for the life of the process.
    """
    try:
        from desktop import gui
    except ImportError as exc:  # pragma: no cover - environment defect
        raise AssertionError(f"pywebview is missing from this environment: {exc}") from exc
    return gui


class _Sock:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _Server:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1
        return "never-ran"


def _failing_state(sock, server=None):
    return {"splash": _Splash(), "sock": sock, "server": server, "port": 51234,
            "env": {}, "main_window": None}


class _Splash:
    def evaluate_js(self, _js):
        return None

    def destroy(self):
        return None


def test_a_launch_that_raises_releases_the_listening_socket(monkeypatch):
    """
    `apply_environment` or `import main` raising is the common failure — a
    missing dependency, a bad environment, a broken migration. Before this,
    `_bootstrap`'s `except` called only `report_failure`, so the socket stayed
    bound and listening with nothing behind it for as long as the user left the
    error window open. A relaunch then cannot take the port either.
    """
    gui = _gui()
    sock = _Sock()
    monkeypatch.setattr(gui.launcher, "apply_environment",
                        lambda env: (_ for _ in ()).throw(RuntimeError("boom")))

    gui._bootstrap(_failing_state(sock))

    assert sock.closed == 1, "the listening socket was left bound after a failed launch"


def test_an_unhealthy_backend_releases_the_socket_through_the_server(monkeypatch):
    """
    The other route, and the one `DesktopServer.close()` was written for:
    `wait_healthy()` returns False, `bootstrap_sequence` returns False — a
    value `_bootstrap` DISCARDED. `stop()` routes to `close()` via `never_ran`,
    so the server owns the release when one exists.
    """
    gui = _gui()
    sock, server = _Sock(), _Server()
    monkeypatch.setattr(gui.launcher, "apply_environment", lambda env: None)
    monkeypatch.setattr(gui.bootstrap, "bootstrap_sequence", lambda **kw: False)

    gui._bootstrap(_failing_state(sock, server))

    assert server.stopped == 1, "a failed launch left the server holding the socket"
    assert sock.closed == 0, "the socket must be released THROUGH the server, not twice"


def test_a_successful_launch_releases_nothing(monkeypatch):
    """
    The obvious mutation to the two above is to close unconditionally, which
    would tear down the socket uvicorn is actively serving.
    """
    gui = _gui()
    sock, server = _Sock(), _Server()
    monkeypatch.setattr(gui.launcher, "apply_environment", lambda env: None)
    monkeypatch.setattr(gui.bootstrap, "bootstrap_sequence", lambda **kw: True)

    gui._bootstrap(_failing_state(sock, server))

    assert (server.stopped, sock.closed) == (0, 0)


# ── the splash and the message windows ──────────────────────────────────────


def test_the_splash_speaks_the_apps_own_brand_and_stays_self_contained():
    """
    The splash is the first thing a user sees, and on a first-run import it is
    on screen for minutes. It must look like the product that replaces it.

    Self-contained is not a style choice: the backend is NOT UP yet, so any
    external stylesheet, font or image request renders a blank rectangle for
    however long the fetch takes — precisely when the user is least sure the
    app is working.
    """
    from desktop import splash

    assert "#ff5252" in splash.HTML, "the splash is not using the brand red"
    assert "PyPSA" in splash.HTML

    for forbidden in ("<link", "src=\"http", "src='http", "@import", "url(http"):
        assert forbidden not in splash.HTML, f"the splash fetches something: {forbidden}"

    # The three hooks `gui.py` drives through `evaluate_js`. A redesign that
    # renames a hook fails here rather than at runtime on a user's machine,
    # where the only symptom is a splash frozen on "Starting…".
    for hook in ("window.__stage", "window.__detail", "window.__failed"):
        assert hook in splash.HTML, f"{hook} is gone; gui.py calls it"
    assert 'id="stage"' in splash.HTML


def test_a_message_window_actually_contains_its_message():
    """
    `gui.py` built these by `HTML.split("<script>")[0]` plus three `.replace()`
    calls against exact markup — a coupling with NO test and no failure mode.
    Change a tag in the splash and the replacements stop matching, so the user
    gets the splash's own text instead of "PyPSA GUI is already running".
    Nothing raises and nothing logs.
    """
    from desktop import splash

    out = splash.message_html("PyPSA GUI is already running", "Switch to the open window.")

    assert "PyPSA GUI is already running" in out
    assert "Switch to the open window." in out
    # No progress bar and no stage hooks: nothing here is going to progress.
    assert "window.__stage" not in out
    assert 'class="bar"' not in out


def test_a_message_window_escapes_what_it_is_given():
    """
    The lock-failure path puts a filesystem path in here, and app-data
    directories are user-named.
    """
    from desktop import splash

    out = splash.message_html("Could not start", "Locking /tmp/<script>alert(1)</script> failed")

    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
