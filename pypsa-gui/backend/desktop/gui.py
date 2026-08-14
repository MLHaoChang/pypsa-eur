"""
The ONLY module that imports `webview`.

Everything with logic lives in `launcher.py`, `bootstrap.py` and
`services/shutdown.py`, all of which are webview-free and covered by the
backend suite on a headless box. This file is wiring: it owns the windows and
the main thread, and delegates every decision.

**The chain is fixed by `webview.start()`**, which blocks, must own the main
thread, and may be called once per process:

    main thread:  logging -> lock -> bind_socket() -> build_environment(port)
                  -> splash window -> webview.start(bootstrap)

    bootstrap:    apply_environment() -> import main -> serve(sockets=[sock])
                  -> wait_healthy() -> main window -> destroy splash

The bind precedes `build_environment` because the port comes from
`sock.getsockname()[1]`; get that wrong and the wrong CORS allowlist is frozen
at `import main`, silently. The main window is created before the splash is
destroyed because destroying the last window makes `start()` return.

API notes, measured against the installed pywebview 6.2.1 rather than assumed —
the one defect that most damaged the shutdown workstream was code written
against an API that did not exist:

  * `webview.__version__` does NOT exist; `importlib.metadata` does.
  * `closing` is the only event with `_should_lock=True`: handlers run
    synchronously on the caller's thread and the return value vetoes. That is
    what makes the tri-state `CloseHandler` work.
  * `destroy()` re-fires `closing` on winforms and gtk but NOT on cocoa.
  * `create_window(confirm_close=...)` is pywebview's OWN dialog and stays off:
    D12 is our shutdown sequence's step 2.
  * `start(private_mode=True)` is the DEFAULT and would be wrong here — see
    `_start_gui`.
"""
from __future__ import annotations

import logging
import multiprocessing
import sys
import threading
import uuid

import webview

from desktop import bootstrap, downloads, launcher, splash
from desktop.single_instance import AlreadyRunning, SingleInstance

logger = logging.getLogger(__name__)

# ── multiprocessing helper processes ────────────────────────────────────────
#
# A frozen app IS `sys.executable`, so when anything in the dependency stack
# creates a semaphore or a shared-memory block, `multiprocessing` starts its
# resource tracker by re-executing THIS BUNDLE with a `-c` command. Without
# the diversion below the bootloader ignores that `-c` and runs the app's
# entry point instead, so the helper opens a window, starts uvicorn, and
# takes the single-instance lock. OBSERVED on 2026-08-03: a live process whose
# argv was
#
#   PyPSA Studio -B -S -I -c from multiprocessing.resource_tracker import main;main(4)
#
# had become the whole application, orphaned to launchd, with the real parent
# gone — and while the parent was still alive it was that child which raised
# "PyPSA GUI is already running" during every solve.
#
# PyInstaller ships `pyi_rth_multiprocessing`, which targets exactly this and
# IS present in the bundle, but it only diverts when
# `set(sys.argv[1:idx]) == set(_args_from_interpreter_flags())` — and the
# bootloader configures its own interpreter rather than honouring `-B -S -I`,
# so in the child that equality fails and the hook returns silently.
#
# `multiprocessing.freeze_support()` is NOT sufficient either: its
# `is_forking()` fires only on `argv[1] == "--multiprocessing-fork"`, and the
# tracker's argv[1] is `-B`. It is still called below for the spawn path it
# does cover.
_MP_HELPER_PREFIXES = (
    "from multiprocessing.resource_tracker import main",
    "from multiprocessing.semaphore_tracker import main",   # Python < 3.8
    "from multiprocessing.forkserver import main",
    "import sys; from multiprocessing.forkserver import main",  # >= 3.13.13
)


def multiprocessing_helper_command(argv: list[str]) -> str | None:
    """
    The command this process must run as a multiprocessing helper, or None if
    it is a real launch.

    Deliberately matches on the KNOWN bootstrap prefixes rather than executing
    whatever follows `-c`: the argv is produced by our own process tree, but a
    narrow allowlist costs nothing and keeps this from becoming a general
    "run arbitrary code from the command line" path in a shipped binary.
    """
    if "-c" not in argv:
        return None
    idx = argv.index("-c")
    if idx + 1 >= len(argv):
        return None
    command = argv[idx + 1]
    return command if command.startswith(_MP_HELPER_PREFIXES) else None


WINDOW_TITLE = "PyPSA Studio"
INITIAL_SIZE = (1440, 900)
# Below roughly this the workbench's side panels and canvas overlap. Enforced
# rather than advisory, because a window dragged smaller is indistinguishable
# from a broken layout.
MINIMUM_SIZE = (1024, 700)

# Sized for the two-column launch screen, and deliberately smaller than
# INITIAL_SIZE so the handoff to the main window reads as opening up rather
# than as one window replacing another. The SVG viewBox matches these numbers.
SPLASH_SIZE = (920, 580)


def _app_data():
    import app_paths

    return app_paths.app_data_dir()


def main() -> int:
    """Entry point. Returns a process exit status."""
    # FIRST, before logging, the webview settings or the single-instance lock:
    # a multiprocessing helper that reaches any of those has already lost. See
    # `multiprocessing_helper_command` for what this is defending against.
    helper = multiprocessing_helper_command(sys.argv)
    if helper is not None:
        exec(helper, {"__name__": "__main__"})   # noqa: S102 - allowlisted above
        return 0
    # Covers the `--multiprocessing-fork` spawn path, which the `-c` check
    # above does not see. A no-op on a normal launch.
    multiprocessing.freeze_support()

    bootstrap.install_file_logging()

    # Before any window exists, because the default is not merely "no
    # downloads": a `download` anchor then navigates the webview to the file
    # and the SPA is gone. See `downloads.py` — the reasoning is measured.
    downloads.apply(webview.settings)

    lock = SingleInstance(_app_data() / "single-instance.lock")
    try:
        lock.acquire()
    except AlreadyRunning:
        # A held lock says "someone is here"; it is not a channel, so we cannot
        # raise their window (a named follow-up). Telling the user plainly is
        # what this delivers.
        _tell_user_already_running()
        return 0
    except OSError:
        # NOT "already running" — `flock` also fails for ENOLCK/EOPNOTSUPP on
        # filesystems without advisory locking, which `PYPSAGUI_APP_DATA_DIR`
        # can point at. Saying "another instance is running" there is a lie
        # with no recovery.
        logger.exception("could not take the single-instance lock")
        _tell_user_lock_failed()
        return 70

    sock = launcher.bind_socket()
    port = sock.getsockname()[1]
    env = launcher.build_environment(port, launcher.resolve_legacy_root())

    window = webview.create_window(
        WINDOW_TITLE,
        html=splash.HTML,
        width=SPLASH_SIZE[0], height=SPLASH_SIZE[1],
        resizable=False,
        # pywebview's own close dialog stays OFF: our shutdown sequence's
        # step 2 is the confirmation, and two competing prompts would race.
        confirm_close=False,
    )

    state: dict = {"sock": sock, "env": env, "port": port, "splash": window,
                   "lock": lock, "server": None, "main_window": None}
    _start_gui(lambda: _bootstrap(state))

    # `start()` returned, so the last window is gone. Anything still holding
    # the process is a bug in the shutdown, not a reason to hang here.
    lock.release()
    return 0


def _start_gui(func) -> None:
    webview.start(
        func,
        # private_mode=True is pywebview's DEFAULT and is WRONG for this app:
        # it discards localStorage between launches, and the frontend keeps
        # `currentProject` there — so every launch would forget which project
        # was open. `storage_path` puts that state beside the database rather
        # than in a temporary directory.
        private_mode=False,
        storage_path=str(_app_data() / "webview"),
    )


def _bootstrap(state: dict) -> None:
    """Runs on pywebview's worker thread once the GUI loop is up."""
    window = state["splash"]

    def progress(stage: str) -> None:
        try:
            window.evaluate_js(splash.set_stage_js(stage))
        except Exception:
            logger.exception("could not update the splash")

    def import_backend():
        import main as backend

        return backend.app

    def serve(app) -> None:
        server = launcher.DesktopServer(app, state["sock"])
        state["server"] = server
        server.start()

    def wait_healthy() -> bool:
        server = state["server"]
        return server is not None and server.wait_healthy(bootstrap.HEALTH_TIMEOUT)

    def show_main_window() -> None:
        state["main_window"] = webview.create_window(
            WINDOW_TITLE,
            url=launcher.app_url(state["port"]),
            width=INITIAL_SIZE[0], height=INITIAL_SIZE[1],
            min_size=MINIMUM_SIZE,
            confirm_close=False,
        )
        _wire_close_handler(state)

    def destroy_splash() -> None:
        window.destroy()

    def report_failure(message: str) -> None:
        try:
            window.evaluate_js(splash.failed_js(message))
        except Exception:
            logger.exception("could not report the failure on the splash")

    def release_socket() -> None:
        """
        Give the port back on a launch that failed.

        `bind_socket()` runs on the main thread before `import main`, so by the
        time anything here can fail there is already a LISTENING socket with a
        backlog of 128 — and the process deliberately STAYS ALIVE to show the
        error on the splash. So the socket outlives the failure unless someone
        releases it, which partly defeats `bind_socket`'s own reason for
        listening early: refusing connections rather than queueing them in a
        backlog nothing reads.

        Through the server when one exists — `stop()` routes to `close()` via
        `never_ran`, and closing the raw socket underneath a live uvicorn is
        the EBADF hazard `stop()` documents at length.
        """
        server = state.get("server")
        try:
            if server is not None:
                server.stop()
            elif state.get("sock") is not None:
                state["sock"].close()
        except Exception:
            logger.exception("could not release the socket after a failed launch")

    try:
        if not bootstrap.bootstrap_sequence(
            apply_environment=lambda: launcher.apply_environment(state["env"]),
            import_backend=import_backend,
            serve=serve,
            wait_healthy=wait_healthy,
            show_main_window=show_main_window,
            destroy_splash=destroy_splash,
            report_failure=report_failure,
            progress=progress,
        ):
            # `bootstrap_sequence` returns False on the unhealthy-backend path
            # and reports to the splash itself. The return value was DISCARDED
            # here, which is how that path leaked the socket silently.
            release_socket()
    except Exception:
        logger.exception("the launch failed")
        release_socket()
        report_failure(
            "PyPSA GUI could not start. See pypsa-gui.log in your application "
            "data folder."
        )


def _wire_close_handler(state: dict) -> None:
    from routers.projects import _save_context
    from services import shutdown as shutdown_service

    window = state["main_window"]

    # Built HERE, at wire time, not inside `flush`. Two reasons, and the second
    # is the one that matters: WHERE the flush writes is a decision (see
    # `make_saver` — omitting `storage_dir` sent it to `flat_projects_root`
    # while `load_project` reads `projects_root`), and building it at wire time
    # is what lets a test assert this module still uses it. Constructed inside
    # `flush`, the only way to reach it was to run a whole shutdown.
    save = shutdown_service.make_saver(_save_context)

    def confirm(in_flight) -> bool:
        names = ", ".join(s.label for s in in_flight)
        interruptible = all(s.interruptible for s in in_flight)
        detail = "" if interruptible else (
            "\n\nOne of these is an AC power flow, which cannot be "
            "interrupted — quitting may not save its project."
        )
        # `create_confirmation_dialog` runs the modal on the GUI thread even
        # when called from this worker, which is what keeps it from hanging on
        # macOS. The shutdown sequence guards this call regardless: if it
        # raises we flush anyway rather than quit clean.
        return bool(window.create_confirmation_dialog(
            "Quit PyPSA GUI?",
            f"A solve is still running ({names}). Quitting will stop it.{detail}",
        ))

    def release_locks() -> None:
        import local_mode
        from db import session as db_session
        from services import project_locks

        with db_session.SessionLocal() as db:
            project_locks.release_all_for_user(db, local_mode.LOCAL_USER_ID)

    def flush(*, safe: bool):
        contexts = shutdown_service.resident_contexts()
        active = _active_context()

        from services import chat_service

        def flush_chat() -> None:
            # `flush_to_disk(ctx)` takes a CONTEXT — it is per-project, not
            # global. Wiring it as a bare zero-arg callable (which is what
            # `flush_all` expects) would have raised TypeError in the middle of
            # a shutdown. Caught by checking the signature rather than assuming
            # it, which is the discipline the ac_pf_thread defect earned.
            for ctx in contexts:
                chat_service.flush_to_disk(ctx)

        return shutdown_service.flush_all(
            contexts=contexts, active=active, save=save,
            flush_chat=flush_chat, safe=safe,
        )

    def run():
        return shutdown_service.shutdown_sequence(
            confirm=confirm,
            hide=window.hide,
            abort_and_wait=lambda: _abort_everything(),
            flush=flush,
            release_locks=release_locks,
            stop_executor=shutdown_service.stop_tool_executor,
            stop_server=lambda: state["server"].stop() if state["server"] else "no-server",
        )

    handler = shutdown_service.CloseHandler(run=run, destroy=window.destroy)
    window.events.closing += handler.on_closing
    state["close_handler"] = handler


def _active_context():
    from services.pypsa_service import PyPSAService

    return PyPSAService._active


def _abort_everything() -> bool:
    from services import shutdown as shutdown_service
    from services.solve_queue import solve_queue

    # Gate the dispatcher BEFORE anything else, or aborting the running job
    # frees it to pop the next queued job, flip it to `running`, and hand the
    # process exit a fresh solve to kill — which boot then marks `interrupted`
    # and R25 never resumes. At the top of the function, not inside
    # `abort_queue`: with a queued-only backlog `abort_and_wait` returns on its
    # empty-`in_flight` fast path without ever calling `abort_queue`, and the
    # un-gated dispatcher could still start that job mid-flush. The ordering
    # matters too: with the gate set first, a job racing it either parks as
    # `queued` or completes its flip to `running` before the `list_jobs()`
    # snapshot below, which then signals it (see `_run_job`'s claim block).
    solve_queue.stop_dispatching()

    def abort_active() -> None:
        from routers.simulation import abort

        abort()

    def abort_queue() -> None:
        from services.solve_queue import solve_queue

        # RUNNING only. A queued job has no live thread to stop, it is persisted
        # in `solve_jobs`, and boot reconciliation re-enqueues it — so cancelling
        # it here would destroy work the user explicitly asked for in order to
        # shut down a fraction of a second sooner.
        #
        # `list_jobs()` returns `to_public()` dicts — `id` is `str(uuid.UUID)`
        # since Task 12 (R23), not the raw UUID `solve_queue.abort` expects.
        # Passing the string straight through used to look correct (both were
        # `int` pre-Task-12) but now misses every `_jobs` key: `abort()`
        # returns `None`, nothing is signalled, and the caller never learns —
        # a silent no-op on the desktop app's quit-time abort path. Preserve
        # the `uuid.UUID(...)` parse.
        for job in solve_queue.list_jobs():
            if job.get("status") == "running":
                solve_queue.abort(uuid.UUID(str(job["id"])))

    def wait(timeout: float) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not shutdown_service.solves_in_flight():
                return True
            time.sleep(0.25)
        return not shutdown_service.solves_in_flight()

    return shutdown_service.abort_and_wait(
        in_flight=shutdown_service.solves_in_flight(),
        abort_active=abort_active, abort_queue=abort_queue, wait=wait,
    )


def _tell_user_already_running() -> None:
    window = webview.create_window(
        WINDOW_TITLE,
        html=_message_html(
            "PyPSA GUI is already running",
            "Switch to the window that is already open. If you cannot find "
            "it, quit that copy first and try again.",
        ),
        width=440, height=200, resizable=False,
    )
    webview.start(private_mode=False, storage_path=str(_app_data() / "webview"))
    del window


def _tell_user_lock_failed() -> None:
    webview.create_window(
        WINDOW_TITLE,
        html=_message_html(
            "PyPSA GUI could not start",
            "The application data folder could not be locked. If it is on a "
            "network drive, try a local folder instead. Details are in "
            "pypsa-gui.log.",
        ),
        width=440, height=220, resizable=False,
    )
    webview.start(private_mode=False, storage_path=str(_app_data() / "webview"))


def _message_html(title: str, body: str) -> str:
    """
    Delegated to `splash.message_html`.

    This used to be string surgery on `splash.HTML` — `split("<script>")[0]`
    plus three `.replace()` calls against exact markup. That has no failure
    mode: redesign the splash and the replacements stop matching, so the user
    sees the splash's own text instead of "PyPSA GUI is already running", with
    nothing raised and nothing logged. It broke the moment the splash was
    rebranded, which is when the tests for it were finally written.
    """
    return splash.message_html(title, body)


if __name__ == "__main__":
    sys.exit(main())
