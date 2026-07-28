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
from pathlib import Path

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
