"""
The launch chain, and the logging that makes a failed launch diagnosable.

**Webview-free**, like `launcher.py` and for the same reason: the backend suite
covers this on a headless box, and `gui.py` is the only module that imports a
toolkit. `gui.py` is a thin wiring layer over `bootstrap_sequence`.

`webview.start()` blocks, must own the main thread, and may be called once per
process. That fixes the shape of the launch:

    main thread:  lock -> bind_socket() -> build_environment(port, legacy_root)
                  -> create splash -> webview.start(bootstrap)

    bootstrap:    apply_environment() -> import main -> serve(sockets=[sock])
                  -> wait_healthy() -> main window -> destroy splash

Two orderings inside are load-bearing and neither is obvious:

  * the port comes from `sock.getsockname()[1]`, so the BIND precedes
    `build_environment`. Get it wrong and the wrong CORS allowlist is frozen at
    `import main` — silently, because every variable still reads back correctly.
  * the main window is created BEFORE the splash is destroyed. Destroying the
    last window makes `start()` return, ending the process during its own
    launch.
"""
from __future__ import annotations

import logging
import logging.handlers
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Shown on the splash, in order. Data rather than inline strings so the test can
# assert the SEQUENCE — the window itself is not unit-testable and must not be
# faked into looking tested.
STAGES = (
    "Starting…",
    "Loading PyPSA…",
    "Importing your projects…",
    "Opening…",
)

# The give-up budget for `/api/health`, which is NOT the progress budget.
# Constraint #3: `run_first_run_import` runs synchronously inside `lifespan` and
# uvicorn accepts no connection until it returns — 113 MB, and on a
# OneDrive-redirected `Documents` that is minutes. A shell that gave up would
# abandon a half-finished import whose lock then blocks the retry for an hour
# (`_LOCK_STALE_SECONDS = 3600`), so the user's projects would silently not
# appear. The splash POLLS; it does not time this out on a human timescale.
HEALTH_TIMEOUT = 3600.0

_LOG_FILENAME = "pypsa-gui.log"
_file_handler: logging.Handler | None = None


def install_file_logging():
    """
    Give `logger.exception` somewhere to land. Returns the path, or None.

    Constraint #16: the backend calls `logging.getLogger(__name__)` with no
    `basicConfig` and no `FileHandler` anywhere, so in a frozen windowed build
    every exception goes nowhere — including the first-run import's and the
    shutdown's, which are the two that matter most. `local_bootstrap` already
    disables alembic's logger because writing to a closed console handle can
    itself raise, which is the same condition seen from the other side.

    Idempotent: the shell installs this before `import main` and a retry path
    could call it again, and two handlers on the root logger write every record
    twice.

    Never fatal. A read-only or missing app-data directory must not be the
    reason the app will not launch.
    """
    global _file_handler

    if _file_handler is not None:
        return getattr(_file_handler, "baseFilename", None)

    try:
        import app_paths

        directory = app_paths.app_data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _LOG_FILENAME

        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        ))
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        _file_handler = handler
        return path
    except Exception:
        # Deliberately not `logger.exception` — the handler that would record
        # it is the thing that just failed.
        return None


def remove_file_logging() -> None:
    """Mostly for tests; also lets a relaunch re-install cleanly."""
    global _file_handler

    if _file_handler is None:
        return
    try:
        logging.getLogger().removeHandler(_file_handler)
        _file_handler.close()
    except Exception:
        pass
    _file_handler = None


def bootstrap_sequence(
    *,
    apply_environment: Callable[[], None],
    import_backend: Callable[[], Any],
    serve: Callable[[Any], None],
    wait_healthy: Callable[[], bool],
    show_main_window: Callable[[], None],
    destroy_splash: Callable[[], None],
    report_failure: Callable[[str], None],
    progress: Callable[[str], None],
) -> bool:
    """
    Run the launch, on the pywebview worker. Returns whether the app came up.

    Every step is injected for the same reason `shutdown_sequence`'s are: it is
    the only way to assert the ORDER without a real window, and the orderings
    here are exactly what a reader would get wrong.
    """
    progress(STAGES[0])
    apply_environment()

    progress(STAGES[1])
    app = import_backend()

    progress(STAGES[2])
    serve(app)

    if not wait_healthy():
        # Constraint #17. uvicorn calls `sys.exit(STARTUP_FAILURE)` when
        # lifespan startup raises — on a worker thread, where `SystemExit`
        # kills that thread and nothing else, so nothing surfaces anywhere the
        # caller can see. Without this branch the splash sits on its last stage
        # forever and Force Quit is the user's only move.
        #
        # The splash is deliberately NOT destroyed: it is the only window, and
        # destroying it would make `start()` return and take the process with
        # it, leaving the user with an app that vanished instead of an app that
        # told them something.
        report_failure(
            "The PyPSA Studio backend did not start. Details are in the log file "
            "in your application data folder."
        )
        return False

    progress(STAGES[3])
    # BEFORE destroying the splash. Destroying the last window makes
    # `webview.start()` return, which ends the process — so the wrong order
    # here exits during launch, and on a fast machine before anything is seen.
    show_main_window()
    destroy_splash()
    return True
