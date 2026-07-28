"""
Environment, socket, and import ordering for the desktop shell.

**Webview-free by contract.** Nothing here may import a GUI toolkit — the
backend test suite covers this module on a headless box, and `gui.py` is the
only place `webview` appears. Nor may it import the backend: the whole purpose
of `apply_environment` is to run *before* `import main`, and a module that
pulled in `settings` at its own import would refuse every launch.

**The order is the design, not a detail:**

    bind_socket() -> build_environment(port) -> apply_environment() -> import main

`get_settings()` and `security.allowed_origins()` are `lru_cache`d and the CORS
allowlist is read at import time, so an environment applied afterwards is
applied to nothing — and it fails *silently*, with every variable reading back
correctly from `os.environ` while the app runs on the defaults. Hence
`apply_environment` raises instead of trusting the caller to get it right.

The port has to be known before the environment can be built, which is why the
socket is bound here and handed to uvicorn rather than left for it to open.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import uvicorn

_BACKEND = Path(__file__).resolve().parent.parent

# The loopback literal, never the name. `localhost` resolves to `::1` first on
# macOS, so a server bound to the name and a client dialling the number never
# meet; `0.0.0.0` raises a Windows Firewall prompt and puts an unauthenticated
# API on the LAN.
HOST = "127.0.0.1"

# Modules whose import freezes configuration this shell still needs to set.
# `main`/`settings`/`security` for the `lru_cache`d settings and allowlist;
# `pypsa` because importing it resolves a matplotlib backend, and `MPLBACKEND`
# set afterwards is set too late.
_FREEZES_THE_ENVIRONMENT = ("main", "pypsa", "settings", "security")


class BackendAlreadyImported(RuntimeError):
    """`apply_environment()` was called too late to have any effect."""


def origin_for_port(port: int) -> str:
    return f"http://{HOST}:{port}"


def app_url(port: int) -> str:
    """The URL the window loads. Its origin must be the allowlisted one."""
    return f"{origin_for_port(port)}/"


def resolve_legacy_root(backend_dir: Path | None = None) -> Path | None:
    """
    Where a pre-desktop install left its projects (D10), or `None`.

    `None` is a normal outcome, not an error: it means the first-run import
    never fires, which is the declared F1 deviation for any machine with
    nothing to migrate — every fresh install. `run_first_run_import` already
    returns early when the variable is unset.

    A packaged build has no `backend/projects`, so this returns `None` there
    until workstream J supplies the packaged equivalent.
    """
    base = Path(backend_dir) if backend_dir is not None else _BACKEND
    candidate = base / "projects"
    return candidate.resolve() if candidate.is_dir() else None


def build_environment(port: int, legacy_root: Path | None = None) -> dict[str, str]:
    """
    The complete set of variables the shell pins. Deliberately small.

    Storage locations are conspicuously absent — `DATABASE_URL`,
    `PROJECTS_ROOT`, `LEGACY_ROOT`, `FLAT_PROJECTS_ROOT`,
    `PYPSAGUI_PROJECTS_ROOT`, `PYPSAGUI_APP_DATA_DIR`. `app_paths` already
    resolves those per-user and per-platform, and anything this process
    computed would be relative to wherever the frozen app happens to be
    running: a macOS `.app` launched from Finder has cwd `/`.
    """
    env = {
        "PYPSAGUI_LOCAL_MODE": "1",
        # Before `import pypsa`, in a process that is about to own the GUI
        # toolkit itself.
        "MPLBACKEND": "Agg",
        # EXACTLY the bound origin. The default carries the two Vite dev
        # origins, which no packaged shell serves; leaving them allowlisted
        # widens the trust boundary to any page on a running dev server.
        "CORS_ALLOWED_ORIGINS": origin_for_port(port),
    }
    if legacy_root is not None:
        # Absent, never empty: `settings.legacy_import_root` is `Path | None`
        # and an empty string coerces to `Path(".")` — the cwd, which the
        # importer would then inventory.
        env["PYPSAGUI_LEGACY_IMPORT_ROOT"] = str(legacy_root)
    return env


def apply_environment(env: dict[str, str]) -> None:
    """
    Publish the environment, refusing if it is already too late to matter.

    The check runs before any mutation, so a refused call leaves `os.environ`
    untouched rather than half-applied.
    """
    frozen = [name for name in _FREEZES_THE_ENVIRONMENT if name in sys.modules]
    if frozen:
        raise BackendAlreadyImported(
            "the environment must be applied before the backend is imported; "
            f"already imported: {', '.join(frozen)}"
        )
    os.environ.update(env)


def bind_socket(port: int = 0) -> socket.socket:
    """
    Bind and LISTEN on an ephemeral loopback port, for handoff to uvicorn.

    Listening here rather than at serve time is what closes the bind-to-serve
    race: the port is bound early because the environment needs it, and a
    socket that is bound but not listening refuses connections in that window
    instead of queueing them in the backlog.

    `SO_REUSEADDR` is deliberately NOT set. On Windows it permits binding a
    port another socket is actively listening on — the opposite of its POSIX
    meaning — so two launches would both succeed and one would silently
    receive nothing.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


# ── the server ──────────────────────────────────────────────────────────────

# How long uvicorn may wait for in-flight responses before cancelling them.
# The SSE stream never ends on its own, so this is what makes the wait finite.
GRACEFUL_TIMEOUT = 5.0

# How long to wait for the server thread after each escalation rung.
JOIN_TIMEOUT = 8.0


def escalate_shutdown(
    *,
    request_exit: Callable[[], None],
    force_exit: Callable[[], None],
    wait_for_exit: Callable[[], bool],
    hard_exit: Callable[[], None],
) -> str:
    """
    Ask, then insist, then leave.

    Separated from the thread mechanics so the ORDER is assertable. Driving
    this through real threads can only test the ladder by sleeping through
    every rung, and cannot tell "the thread happened to die" apart from "the
    implementation set the flag that killed it".

    Force is deliberately the second rung, not the first: uvicorn skips
    `lifespan.shutdown()` when `force_exit` is set.
    """
    request_exit()
    if wait_for_exit():
        return "clean"

    force_exit()
    if wait_for_exit():
        return "forced"

    # The process is going to exit whether or not the thread cooperates. A
    # window the user cannot close is the worse outcome, and by the time we
    # are here Task 4's shutdown sequence has already flushed and released.
    hard_exit()
    return "abandoned"  # unreachable in production; `os._exit` does not return


class DesktopServer:
    """
    uvicorn on a daemon thread, with a bounded stop.

    Takes the app OBJECT and the already-bound socket — never an import string
    and never a host/port. A frozen build has no importable module path for
    `main:app`, and the port has to be known before the environment is built,
    so by the time we get here the socket already exists.
    """

    def __init__(
        self,
        app,
        sock: socket.socket,
        *,
        graceful_timeout: float = GRACEFUL_TIMEOUT,
        join_timeout: float = JOIN_TIMEOUT,
        hard_exit: Callable[[], None] | None = None,
    ) -> None:
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._join_timeout = join_timeout
        self._hard_exit = hard_exit if hard_exit is not None else lambda: os._exit(0)
        self._thread: threading.Thread | None = None
        self._stopped = False

        self.config = uvicorn.Config(
            app,
            # `log_config=None` leaves logging alone. uvicorn's default config
            # attaches StreamHandlers to sys.stdout/sys.stderr, and a frozen
            # windowed build has neither — `local_bootstrap` already disables
            # alembic's logger for that reason. Logging belongs to the shell.
            log_config=None,
            # Without this the graceful wait is unbounded: `Server.shutdown`
            # passes it straight to `asyncio.wait_for`, whose `None` means
            # "forever", and the poll loop it wraps waits on connections that
            # an open SSE stream keeps alive indefinitely.
            timeout_graceful_shutdown=graceful_timeout,
        )
        self._server = uvicorn.Server(self.config)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        # daemon=True so a thread that ignores every rung of the escalation
        # cannot by itself keep the interpreter alive.
        self._thread = threading.Thread(
            target=self._server.run,
            kwargs={"sockets": [self._sock]},
            name="pypsa-gui-server",
            daemon=True,
        )
        self._thread.start()

    def wait_healthy(self, timeout: float) -> bool:
        """
        True once the backend answers, False if it gives up first.

        The liveness check is not an optimisation. uvicorn calls
        `sys.exit(STARTUP_FAILURE)` when lifespan startup raises, which on a
        worker thread raises `SystemExit` in that thread and nothing else —
        no exception surfaces anywhere the caller can see it. Without noticing
        the thread died, a failed boot is indistinguishable from a slow one
        until the whole timeout expires.

        The budget must accommodate the first-run import, which runs
        synchronously inside `lifespan` before uvicorn accepts any connection.
        """
        if self._thread is None:
            return False

        url = f"{origin_for_port(self.port)}/api/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            if not self._thread.is_alive():
                return False
            time.sleep(0.1)
        return False

    def stop(self) -> str:
        """One of `clean`, `forced`, `abandoned`, `already-stopped`."""
        if self._stopped or self._thread is None:
            return "already-stopped"
        self._stopped = True
        thread = self._thread

        def request_exit() -> None:
            self._server.should_exit = True

        def force_exit() -> None:
            self._server.force_exit = True

        def wait_for_exit() -> bool:
            thread.join(self._join_timeout)
            return not thread.is_alive()

        return escalate_shutdown(
            request_exit=request_exit,
            force_exit=force_exit,
            wait_for_exit=wait_for_exit,
            hard_exit=self._hard_exit,
        )
