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

import http.client
import os
import socket
import sys
import threading
import time
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
# `main`/`settings`/`security` for the `lru_cache`d settings and allowlist.
# `matplotlib` because importing it is what makes `MPLBACKEND` too late.
# Measured end-to-end: set the variable BEFORE the import and `get_backend()`
# is `Agg`; set it after and you get `macosx` with `backend_macosx` loaded.
# (The resolution is lazy — `rcParams` still holds the auto sentinel straight
# after the import — so the mechanism is the deferred read, not an eager one.
# The earlier comment here asserted the eager version, which is not what the
# interpreter does.) `pypsa` is listed separately
# rather than relied on as a proxy for matplotlib: it does pull it in, but so
# would `gui.py`, seaborn, cartopy, or a PyInstaller hidden import, and any of
# those would otherwise let this function succeed with its most
# safety-critical variable already dead.
_FREEZES_THE_ENVIRONMENT = ("main", "pypsa", "matplotlib", "settings", "security")

# Everything `build_environment` OWNS. Absence is meaningful for some of these
# — see `apply_environment`.
_MANAGED = (
    "PYPSAGUI_LOCAL_MODE",
    "MPLBACKEND",
    "CORS_ALLOWED_ORIGINS",
    "PYPSAGUI_LEGACY_IMPORT_ROOT",
)


class BackendAlreadyImported(RuntimeError):
    """`apply_environment()` was called too late to have any effect."""


def origin_for_port(port: int) -> str:
    return f"http://{HOST}:{port}"


def app_url(port: int) -> str:
    """The URL the window loads. Its origin must be the allowlisted one."""
    return f"{origin_for_port(port)}/"


# Where a pre-desktop install kept its projects. A named constant because the
# test asserts on THIS rather than on whether the directory happens to exist:
# `pypsa-gui/.gitignore` ignores `backend/projects/`, so an existence-based
# assertion passes against a broken probe on every fresh clone.
PRE_DESKTOP_PROJECTS_DIR = (_BACKEND / "projects").resolve()


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
    candidate = (
        Path(backend_dir) / "projects"
        if backend_dir is not None
        else PRE_DESKTOP_PROJECTS_DIR
    )
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

    # SET what we chose and CLEAR what we deliberately did not. `update()`
    # alone cannot remove a key, and for `PYPSAGUI_LEGACY_IMPORT_ROOT` absence
    # is the decision: `build_environment` omits it when there is nothing to
    # migrate. An inherited one — from a shell profile, or a user-level Windows
    # variable that persists across reboots — would otherwise survive and send
    # `run_first_run_import` `copytree`-ing a tree the shell never chose into
    # the user's Documents. `tests/conftest.py` pops this exact variable
    # because exporting it is what the importer's rehearsal instructions teach.
    for name in _MANAGED:
        if name in env:
            os.environ[name] = env[name]
        else:
            os.environ.pop(name, None)
    for name, value in env.items():
        if name not in _MANAGED:
            os.environ[name] = value


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

# How long to wait for the server thread after each escalation rung, once the
# server is actually serving.
JOIN_TIMEOUT = 8.0

# The budget while lifespan STARTUP is still running. uvicorn's `_serve` awaits
# `startup()` before `main_loop()`, and `main.lifespan` runs
# `run_first_run_import()` synchronously — a `copytree` of the whole legacy
# tree. Nothing polls `should_exit` or `force_exit` in that window, so with one
# budget the ladder is GUARANTEED to reach the hard exit in 2x JOIN_TIMEOUT.
# The copy itself survives (staged, then renamed), but the import lock's
# `finally: lock.unlink()` does not run and `_LOCK_STALE_SECONDS` is 3600 — so
# for the next hour every relaunch silently skips the import and the user's
# projects do not appear.
STARTUP_JOIN_TIMEOUT = 120.0

# Worst case for `stop()` is TWICE whichever budget applies — `escalate_shutdown`
# waits once after `should_exit` and again after `force_exit` — so 16 s while
# serving and 240 s during startup. Task 4 drives `stop()` from the window's
# close handler, where a UI thread blocked past ~5 s is ghosted as *Not
# Responding* on Windows, so that step must run on a worker and not the UI
# thread. Stated here because the first version of this comment justified one
# 120 s budget and silently meant two.

# NOT zero. An abandoned shutdown is not a clean quit, and the exit status is
# the only channel left to say so to an installer, a supervisor, or a crash
# reporter.
HARD_EXIT_STATUS = 70  # EX_SOFTWARE


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
        startup_join_timeout: float = STARTUP_JOIN_TIMEOUT,
        hard_exit: Callable[[], None] | None = None,
    ) -> None:
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._join_timeout = join_timeout
        self._startup_join_timeout = startup_join_timeout
        self._hard_exit = (
            hard_exit if hard_exit is not None
            else lambda: os._exit(HARD_EXIT_STATUS)
        )
        self._thread: threading.Thread | None = None
        self._stopped = False
        self._started = False
        # `stop()` mutates a latch and escalates; two callers arriving together
        # would both pass the check and both escalate.
        self._lifecycle = threading.Lock()

        self.config = uvicorn.Config(
            app,
            # `log_config=None` leaves logging alone. uvicorn's default config
            # attaches StreamHandlers to sys.stdout/sys.stderr, and a frozen
            # windowed build has neither — `local_bootstrap` already disables
            # alembic's logger for that reason. Logging belongs to the shell.
            log_config=None,
            # Pinned so a stray `WEB_CONCURRENCY` in the user's environment
            # cannot raise it: `uvicorn.Config` reads that variable when
            # `workers` is None, and `Server.startup` then shares the socket
            # via `sock.share(os.getpid())` on Windows — uvicorn would serve a
            # DUPLICATE while `shutdown()` closes the original we handed it.
            workers=1,
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
        """
        Once only. A second `start()` used to replace `self._thread` silently
        while the first kept serving on a daemon thread — two threads driving
        one `uvicorn.Server`, `main.lifespan` running twice (two first-run
        imports, two `PyPSAService.initialize()`), and `stop()` joining only
        the second. A restart after `stop()` is refused for a different reason:
        `_stopped` latches, so the new server could never be shut down.
        """
        with self._lifecycle:
            if self._stopped:
                raise RuntimeError("this server was already stopped; make a new one")
            if self._started:
                raise RuntimeError("this server is already started")
            self._started = True
            # daemon=True so a thread that ignores every rung of the escalation
            # cannot by itself keep the interpreter alive.
            self._thread = threading.Thread(
                target=self._server.run,
                kwargs={"sockets": [self._sock]},
                name="pypsa-gui-server",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        """
        Release the socket when the server never ran.

        `bind_socket()` happens before `import main`, and if that import — or
        lifespan startup — raises, uvicorn never reaches `shutdown()` and never
        closes the socket it was handed. It would stay listening for the life
        of the process, accepting connections nothing answers.
        """
        try:
            self._sock.close()
        except OSError:
            pass

    def _join_budget(self) -> float:
        """
        How long to wait per rung. Longer while lifespan startup is still
        running, because that is where the first-run `copytree` lives and
        nothing polls the exit flags until it returns — see
        `STARTUP_JOIN_TIMEOUT`. `Server.started` is uvicorn's own flag for
        "startup finished".
        """
        if getattr(self._server, "started", False):
            return self._join_timeout
        return self._startup_join_timeout

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

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._health_answers():
                return True
            if not self._thread.is_alive():
                return False
            time.sleep(0.1)
        return False

    def _health_answers(self) -> bool:
        """
        One probe, deliberately NOT through `urllib.request.urlopen`.

        `urlopen` builds the default opener, whose `ProxyHandler` reads
        `getproxies()`. So an exported `HTTP_PROXY` — or a corporate Windows
        box with "bypass proxy server for local addresses", which writes
        `<local>`, and CPython's `_proxy_bypass_winreg_override` honours that
        only for hosts with NO DOT — sends `http://127.0.0.1:<port>` to the
        proxy. Verified in the installed CPython, and not Windows-only:
        `proxy_bypass_environment` returns False outright when `no_proxy` is
        unset.

        The backend would boot perfectly, every poll would go to the proxy,
        and the shell would report a failed start on every launch — while
        leaking the ephemeral port to the proxy ten times a second.

        `http.client` consults no proxy configuration at all.
        """
        conn = http.client.HTTPConnection(HOST, self.port, timeout=2)
        try:
            conn.request("GET", "/api/health")
            return conn.getresponse().status == 200
        except (OSError, http.client.HTTPException):
            # `HTTPException` is NOT an `OSError`: `BadStatusLine`,
            # `ResponseNotReady` and `IncompleteRead` all escape a bare
            # `except OSError`. A truncated reply while the server is still
            # coming up would then propagate out of `wait_healthy` and kill
            # the bootstrap thread instead of counting as "not ready yet".
            return False
        finally:
            conn.close()

    def stop(self) -> str:
        """One of `clean`, `forced`, `abandoned`, `already-stopped`."""
        with self._lifecycle:
            if self._stopped or self._thread is None:
                already = self._stopped
                self._stopped = True
                never_ran = self._thread is None
            else:
                already = False
                never_ran = False
                self._stopped = True
                thread = self._thread

        if already or never_ran:
            # Outside the lock, and NOT skipped. The `_stopped` latch added in
            # the previous round returned from inside the `with` block, so this
            # never ran: a `stop()` on a server that was bound but never
            # started left the listener accepting for the life of the process
            # AND refused every later `start()`. That is precisely the path
            # `close()` exists for — `bind_socket()` succeeds, `import main`
            # raises, and the error handler calls `stop()` as the obvious
            # cleanup.
            self.close()
            return "already-stopped"

        def request_exit() -> None:
            self._server.should_exit = True

        def force_exit() -> None:
            self._server.force_exit = True

        def wait_for_exit() -> bool:
            # Re-read per rung: startup can finish between them.
            thread.join(self._join_budget())
            return not thread.is_alive()

        try:
            return escalate_shutdown(
                request_exit=request_exit,
                force_exit=force_exit,
                wait_for_exit=wait_for_exit,
                hard_exit=self._hard_exit,
            )
        finally:
            # uvicorn closes the sockets it was handed in `shutdown()`, but not
            # on the paths that never get there.
            self.close()
