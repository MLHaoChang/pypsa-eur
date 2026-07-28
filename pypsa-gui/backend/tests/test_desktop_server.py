"""
Bounded threaded uvicorn (phase 2a, Task 3).

Two things have to be true at quit, and they pull against each other: the
process must actually exit, and it must not exit while work is in flight.
uvicorn's defaults give you only the second half — `timeout_graceful_shutdown`
defaults to `None`, so `Server.shutdown` awaits `_wait_tasks_to_complete()`
with no bound, and that polls `while self.server_state.connections`. The app
holds an SSE stream open until the client disconnects. A hidden window's
JavaScript is still connected, so "wait for connections to close" waits for a
client that is never coming back.

Verified in the installed uvicorn 0.51.0 rather than assumed:

  * `shutdown()` wraps the wait in `asyncio.wait_for(..., timeout=
    self.config.timeout_graceful_shutdown)` and cancels the outstanding tasks
    when it expires. Setting the timeout is what makes the wait finite.
  * both poll loops in `_wait_tasks_to_complete` also test `not self.force_exit`,
    so setting that flag from another thread breaks them early.
  * `capture_signals()` returns immediately off the main thread, which is why
    the server may live on a worker at all.
  * `shutdown()` skips `lifespan.shutdown()` when `force_exit` is set. Costs
    nothing today — `main.lifespan` has no code after its `yield` — but it is
    why force is the second rung and not the first.

The escalation is a pure function with injected callables. The alternative —
driving it through real threads — can only assert the ladder by sleeping
through every rung, and cannot distinguish "the thread happened to die" from
"the implementation set the flag that killed it".
"""
from __future__ import annotations

import http.client
import os
import socket
import time
import urllib.request
from contextlib import asynccontextmanager, closing

import pytest
from unittest import mock

from fastapi import FastAPI
from starlette.responses import StreamingResponse

from desktop import launcher


# ── the escalation ladder ───────────────────────────────────────────────────


class _Ladder:
    """Records the rungs taken and controls which one lets the server die."""

    def __init__(self, dies_after: str | None) -> None:
        self.calls: list[str] = []
        self._dies_after = dies_after
        self._dead = False

    def request_exit(self) -> None:
        self.calls.append("request_exit")
        if self._dies_after == "request_exit":
            self._dead = True

    def force_exit(self) -> None:
        self.calls.append("force_exit")
        if self._dies_after == "force_exit":
            self._dead = True

    def wait_for_exit(self) -> bool:
        self.calls.append("wait")
        return self._dead

    def hard_exit(self) -> None:
        self.calls.append("hard_exit")

    def escalate(self) -> str:
        return launcher.escalate_shutdown(
            request_exit=self.request_exit,
            force_exit=self.force_exit,
            wait_for_exit=self.wait_for_exit,
            hard_exit=self.hard_exit,
        )


def test_a_server_that_stops_when_asked_is_never_forced():
    ladder = _Ladder(dies_after="request_exit")

    assert ladder.escalate() == "clean"
    assert ladder.calls == ["request_exit", "wait"]


def test_a_server_that_ignores_the_request_is_forced():
    """
    `force_exit` is the rung that breaks `_wait_tasks_to_complete`'s poll
    loops. It is second, not first, because it also makes uvicorn skip the
    lifespan shutdown.
    """
    ladder = _Ladder(dies_after="force_exit")

    assert ladder.escalate() == "forced"
    assert ladder.calls == ["request_exit", "wait", "force_exit", "wait"]


def test_a_server_that_will_not_die_is_abandoned():
    """
    The last rung exists because the alternative is a process the user cannot
    get rid of. `chat_service` holds a module-level `ThreadPoolExecutor` that
    is never shut down and CPython's atexit joiner blocks interpreter exit on
    it, so "the thread returned" is not the same as "the process will exit".

    In production `hard_exit` is `os._exit(0)` and never returns; the string is
    reachable only under a double like this one.
    """
    ladder = _Ladder(dies_after=None)

    assert ladder.escalate() == "abandoned"
    assert ladder.calls == [
        "request_exit", "wait", "force_exit", "wait", "hard_exit",
    ]


def test_the_hard_exit_is_never_the_first_resort():
    """
    Guards the ordering directly rather than inferring it from the return
    value: an implementation that hard-exits first would still return a
    plausible string, and would take the process down with unsaved work.
    """
    for dies_after in ("request_exit", "force_exit", None):
        ladder = _Ladder(dies_after=dies_after)
        ladder.escalate()
        if "hard_exit" in ladder.calls:
            assert ladder.calls.index("hard_exit") > ladder.calls.index("force_exit")


# ── the real server ─────────────────────────────────────────────────────────


def _health_app() -> FastAPI:
    """
    Built inside a function ON PURPOSE. It has no importable module path, so a
    `uvicorn.Config("module:app")` implementation cannot serve it — which is
    the constraint a frozen build imposes and this is how it gets asserted.
    """
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/forever")
    def forever():
        # Stands in for `/api/simulation/log_stream`, which keeps its
        # connection open until the client disconnects or a solve ends.
        async def stream():
            yield b"data: open\n\n"
            while True:
                await _sleep(3600)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def _refuse_to_hard_exit() -> None:
    raise AssertionError(
        "stop() escalated all the way to the hard exit in a unit test"
    )


def _serving(app, **kwargs):
    """
    Never lets a test reach the real `os._exit(0)`.

    Without this the default `hard_exit` takes down the PYTEST PROCESS, and it
    does so with status 0 — so a regression in the escalation ladder reads as a
    green run to anything that checks the exit code. Found by mutating the
    ladder to hard-exit first: the run produced no summary line at all and the
    mutant scored as a survivor.
    """
    sock = launcher.bind_socket()
    kwargs.setdefault("hard_exit", _refuse_to_hard_exit)
    return launcher.DesktopServer(app, sock, **kwargs)


def test_the_server_answers_health_on_the_socket_it_was_handed():
    server = _serving(_health_app())
    try:
        server.start()
        assert server.wait_healthy(30) is True
    finally:
        server.stop()


def test_wait_healthy_reports_failure_instead_of_hanging_when_startup_fails():
    """
    Constraint #17. uvicorn calls `sys.exit(STARTUP_FAILURE)` when lifespan
    startup raises — on the worker thread, where `SystemExit` kills that thread
    and nothing else. Nobody is told. Without an explicit liveness check the
    splash would sit on "Starting…" until the timeout expired, and the caller
    could not tell a slow first-run import from a dead backend.
    """
    @asynccontextmanager
    async def refuses_to_start(app):
        raise RuntimeError("lifespan startup failed on purpose")
        yield  # pragma: no cover

    server = _serving(FastAPI(lifespan=refuses_to_start))
    server.start()
    started = time.monotonic()
    try:
        healthy = server.wait_healthy(30)
    finally:
        server.stop()
    elapsed = time.monotonic() - started

    assert healthy is False
    # The point is that it noticed, not that it waited politely.
    assert elapsed < 15, f"waited {elapsed:.1f}s for a server that was already dead"


def test_wait_healthy_is_false_before_the_server_is_started():
    server = _serving(_health_app())

    assert server.wait_healthy(2) is False


def test_stop_returns_within_its_bound_while_a_response_is_still_streaming():
    """
    THE test for this task. Note what the setup buys: the connection is proven
    live — the first SSE chunk has been read — at the moment `stop()` is
    called. Without that proof the test passes against an unbounded shutdown,
    because a stream that already closed cannot hold anything up.

    The real endpoint has the same shape and an extra trapdoor:
    `/api/simulation/log_stream` returns immediately unless `_state` carries a
    log queue AND `status == "running"`, so seeding one of the two is not
    enough either. The composition smoke drives the real one.
    """
    server = _serving(_health_app(), graceful_timeout=2.0, join_timeout=8.0)
    server.start()
    assert server.wait_healthy(30) is True

    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        conn.request("GET", "/forever")
        response = conn.getresponse()
        assert response.status == 200
        # `response.readline()`, NOT `response.fp.readline()` — the body is
        # chunked, so reading the raw socket returns the hex chunk-size line.
        assert response.readline().strip() == b"data: open", "stream never opened"

        started = time.monotonic()
        stage = server.stop()
        elapsed = time.monotonic() - started

        # "clean", not "clean or forced": the graceful timeout expiring and
        # cancelling the streaming task is the mechanism under test. If the
        # timeout were left at uvicorn's `None` default, the escalation ladder
        # would still rescue the process via `force_exit` — so accepting
        # "forced" here would pass against the very bug this asserts against.
        assert stage == "clean", stage
        assert elapsed < 6, f"stop() took {elapsed:.1f}s with a stream open"
    finally:
        conn.close()
        server.stop()


def test_stopping_twice_is_not_an_error():
    """The shutdown path can plausibly run twice; the second is a no-op."""
    server = _serving(_health_app())
    server.start()
    assert server.wait_healthy(30) is True

    assert server.stop() == "clean"
    assert server.stop() == "already-stopped"


def test_stopping_a_server_that_never_started_is_not_an_error():
    server = _serving(_health_app())

    assert server.stop() == "already-stopped"


def test_the_port_is_the_one_that_was_bound():
    sock = launcher.bind_socket()
    with closing(sock):
        server = launcher.DesktopServer(_health_app(), sock)
        assert server.port == sock.getsockname()[1]
        assert server.port != 0


def test_the_listener_is_closed_once_the_server_stops():
    """
    A stopped server must stop accepting. `Server.shutdown` closes the sockets
    it was handed; this pins that rather than trusting it.

    It asserts CONNECTIONS ARE REFUSED, not that the port can be re-bound —
    those are different claims and only the first one is true. A port that has
    just carried a connection sits in TIME_WAIT, so re-binding it fails with
    EADDRINUSE even though the listener is completely gone (reproduced with no
    uvicorn involved at all). Do not "fix" a future failure here by setting
    `SO_REUSEADDR`: Task 2 forbids it, on Windows it lets two launches bind the
    same live port, and the shell takes an ephemeral port every time anyway, so
    nothing ever needs to re-bind one.
    """
    server = _serving(_health_app())
    server.start()
    assert server.wait_healthy(30) is True
    port = server.port
    server.stop()

    with pytest.raises((ConnectionRefusedError, socket.timeout, OSError)):
        with closing(socket.create_connection(("127.0.0.1", port), timeout=3)):
            pass


def test_the_health_probe_ignores_a_proxy_configured_in_the_environment(monkeypatch):
    """
    `urllib.request.urlopen` builds the default opener, whose `ProxyHandler`
    reads `getproxies()` — so a machine with `HTTP_PROXY` exported, or a
    corporate Windows box with "bypass proxy for local addresses" (which writes
    `<local>`, and CPython's `_proxy_bypass_winreg_override` only honours that
    for hosts with NO DOT), dials the proxy for `http://127.0.0.1:<port>`.

    Verified in the installed CPython: `proxy_bypass_environment` returns False
    outright when `no_proxy` is unset, so this is not Windows-only.

    Consequence: the backend boots perfectly, every health poll goes to the
    proxy, `wait_healthy` returns False, and the shell reports a failed start
    on every launch. It also leaks the ephemeral port to the proxy 10× a second.
    """
    server = _serving(_health_app())
    try:
        server.start()
        # A proxy that cannot possibly serve anything: port 1 is reserved and
        # nothing listens there. `monkeypatch` rather than `os.environ` directly —
        # an earlier version popped `no_proxy` without restoring it, which
        # leaks process-wide into every later test in the session.
        for var in ("http_proxy", "HTTP_PROXY", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(var, "http://127.0.0.1:1")
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        # urlopen memoises its opener process-wide; drop it so this test is not
        # decided by whichever earlier test happened to build it first.
        urllib.request.install_opener(None)

        assert server.wait_healthy(20) is True
    finally:
        urllib.request.install_opener(None)
        server.stop()


def test_the_join_budget_is_larger_while_lifespan_startup_is_still_running():
    """
    Quitting during the first-run import must not hard-exit in 16 seconds.

    uvicorn's `_serve` awaits `startup()` — which runs `main.lifespan`, which
    calls `run_first_run_import()` synchronously, a `copytree` of the whole
    legacy tree — BEFORE `main_loop()` begins. Nothing polls `should_exit` or
    `force_exit` in that window, so the escalation ladder is guaranteed to
    reach `os._exit(0)`: two joins of `JOIN_TIMEOUT` and out.

    The copy itself survives (`legacy_import` stages then renames), but
    `run_first_run_import`'s `finally: lock.unlink()` never runs, and
    `_LOCK_STALE_SECONDS` is 3600 — so for the next hour every relaunch
    silently skips the import and the user's projects simply do not appear.

    `Server.started` is uvicorn's own flag for "startup finished", so it is
    what distinguishes the two budgets.
    """
    # This test drives the ladder to its last rung on purpose, so it supplies
    # its own recording `hard_exit` instead of `_serving`'s refuse-to-exit guard.
    abandoned: list[int] = []
    server = _serving(
        _health_app(), join_timeout=3.0, startup_join_timeout=90.0,
        hard_exit=lambda: abandoned.append(1),
    )

    # Drive `stop()` against a thread that records what it was joined WITH.
    # Asserting on `_join_budget()` alone is not enough: nothing would then
    # connect the accessor to the escalation, and reverting `wait_for_exit` to
    # `thread.join(self._join_timeout)` — the whole defect — leaves such a test
    # passing. Verified: that mutant survived the accessor-only version.
    joined_with: list[float] = []

    class _RecordingThread:
        def join(self, timeout=None):
            joined_with.append(timeout)

        def is_alive(self):
            return True

    server._started = True
    server._thread = _RecordingThread()
    server._server.started = False           # still inside lifespan startup

    assert server.stop() == "abandoned"
    assert joined_with == [90.0, 90.0], joined_with

    # And the other side of the branch: once serving, the normal bound applies.
    joined_with.clear()
    other = _serving(
        _health_app(), join_timeout=3.0, startup_join_timeout=90.0,
        hard_exit=lambda: abandoned.append(1),
    )
    other._started = True
    other._thread = _RecordingThread()
    other._server.started = True
    assert other.stop() == "abandoned"
    assert joined_with == [3.0, 3.0], joined_with
    assert len(abandoned) == 2

    # Neither server ever ran, so nothing else will release these.
    server.close()
    other.close()


def test_abandoning_the_server_does_not_exit_with_success():
    """
    `os._exit(0)` tells an installer, a supervisor or a crash reporter that the
    app quit cleanly, when in fact a thread had to be abandoned — possibly
    mid-copy. The status is the only channel left at that point.
    """
    assert launcher.HARD_EXIT_STATUS != 0

    # The constant is not the contract — the default `hard_exit` is. Asserting
    # only the constant leaves `lambda: os._exit(0)` passing, which is exactly
    # the defect. Verified: that mutant survived the constant-only version.
    sock = launcher.bind_socket()
    with closing(sock):
        server = launcher.DesktopServer(_health_app(), sock)
        with mock.patch.object(os, "_exit") as fake_exit:
            server._hard_exit()
        fake_exit.assert_called_once_with(launcher.HARD_EXIT_STATUS)


def test_starting_twice_is_refused():
    """
    Without a guard, the second `start()` silently replaces `self._thread`
    while the first keeps serving on a daemon thread: two threads running one
    `uvicorn.Server`, `main.lifespan` running twice — two first-run imports and
    two `PyPSAService.initialize()` calls — and `stop()` joining only the
    second.
    """
    server = _serving(_health_app())
    try:
        server.start()
        assert server.wait_healthy(30) is True
        with pytest.raises(RuntimeError):
            server.start()
    finally:
        server.stop()


def test_a_stopped_server_cannot_be_restarted():
    """
    `_stopped` latches, so a restarted server would answer "already-stopped"
    to every future `stop()` and could never be shut down. Refusing is honest;
    the shell creates a new one per launch anyway.
    """
    server = _serving(_health_app())
    server.start()
    assert server.wait_healthy(30) is True
    server.stop()

    with pytest.raises(RuntimeError):
        server.start()


def test_the_socket_is_released_even_if_the_server_never_ran():
    """
    If `import main` raises after `bind_socket()`, or lifespan startup fails,
    uvicorn never reaches `shutdown()` and never closes the socket it was
    handed — it stays listening for the life of the process, accepting
    connections nothing will answer.
    """
    # Two paths, and only the second was covered before. Calling `close()`
    # directly tests a one-line wrapper; the fix that matters is `close()` in
    # `stop()`'s `finally`, because uvicorn's `_serve` runs `shutdown()` only
    # `if self.started` — so on a startup failure nothing else ever closes it.
    @asynccontextmanager
    async def refuses_to_start(app):
        raise RuntimeError("lifespan startup failed on purpose")
        yield  # pragma: no cover

    server = _serving(FastAPI(lifespan=refuses_to_start))
    port = server.port
    server.start()
    assert server.wait_healthy(30) is False
    server.stop()

    with pytest.raises((ConnectionRefusedError, socket.timeout, OSError)):
        with closing(socket.create_connection(("127.0.0.1", port), timeout=3)):
            pass


def test_stopping_a_server_that_was_never_started_still_releases_the_socket():
    """
    REGRESSION. `stop()` gained a `_stopped` latch that returns from inside the
    lifecycle lock, so it never reached the `close()` in its own `finally` —
    leaving the listener bound for the life of the process AND refusing every
    later `start()`.

    This is the exact path `close()`'s docstring describes: `bind_socket()`
    succeeds, `import main` raises, and the error handler calls `stop()`
    because that is the obvious cleanup.
    """
    server = _serving(_health_app())
    port = server.port

    assert server.stop() == "already-stopped"

    with pytest.raises((ConnectionRefusedError, socket.timeout, OSError)):
        with closing(socket.create_connection(("127.0.0.1", port), timeout=3)):
            pass


def test_a_concurrent_stop_does_not_close_the_socket_under_uvicorn():
    """
    REGRESSION, and the second one introduced by remediation in as many rounds.

    `stop()` computes two distinct states and then merges them at the use site:
    `never_ran` (nothing was ever started — safe to close) and `already`
    (another `stop()` is mid-escalation — uvicorn still owns the socket).
    Closing on `already` pulls the fd out from under `loop.create_server`,
    which fails with EBADF on the server thread, leaves `Server.started` False,
    and therefore skips `lifespan.shutdown()` entirely — uvicorn's `_serve`
    runs `shutdown()` only `if self.started`.

    Nothing is lost today because `main.lifespan` has no code after its
    `yield`. Task 4 is about to make that path load-bearing.
    """
    import asyncio
    import threading as _threading

    entered = _threading.Event()
    release = _threading.Event()

    @asynccontextmanager
    async def slow_startup(app):
        # Stands in for `run_first_run_import()` — a synchronous 113 MB
        # copytree inside lifespan, before uvicorn calls `create_server`.
        entered.set()
        await asyncio.get_running_loop().run_in_executor(None, release.wait)
        yield

    inner = _health_app()
    app = FastAPI(lifespan=slow_startup)
    app.router.routes.extend(inner.router.routes)

    server = _serving(app)
    try:
        server.start()
        assert entered.wait(30), "lifespan startup never began"

        # The window matters. AFTER `create_server` the event loop owns the
        # descriptor and closing our handle is invisible; DURING startup the
        # socket has not been handed over yet, so closing it makes
        # `create_server` fail with EBADF on the server thread, leaves
        # `Server.started` False, and skips `lifespan.shutdown()` — uvicorn
        # runs `shutdown()` only `if self.started`.
        server._stopped = True          # a concurrent stop() is mid-ladder
        assert server.stop() == "already-stopped"
        assert server._sock.fileno() != -1, (
            "the socket was closed out from under uvicorn during startup"
        )

        release.set()
        server._stopped = False
        assert server.wait_healthy(30) is True, (
            "the server never came up — the socket was pulled out from under it"
        )
    finally:
        release.set()
        server._stopped = False
        server.stop()


def test_the_health_probe_survives_a_malformed_reply():
    """
    `http.client` raises `HTTPException` subclasses — `BadStatusLine`,
    `ResponseNotReady`, `IncompleteRead` — and none of them is an `OSError`.
    Narrowing the except clause to `OSError` during the proxy fix meant a
    truncated reply while the server was still coming up would propagate out of
    `wait_healthy()` and kill the bootstrap thread rather than counting as
    "not ready yet".
    """
    server = _serving(_health_app())
    try:
        server.start()
        assert server.wait_healthy(30) is True

        with mock.patch.object(
            http.client.HTTPConnection, "request",
            side_effect=http.client.BadStatusLine("garbage"),
        ):
            assert server._health_answers() is False
    finally:
        server.stop()


def test_web_concurrency_in_the_environment_cannot_change_the_worker_count(monkeypatch):
    """
    `uvicorn.Config` reads `WEB_CONCURRENCY` when `workers` is None, and
    `Server.startup` shares the socket via `sock.share(os.getpid())` +
    `socket.fromshare` when `workers > 1 and is_windows`. That would have
    uvicorn serve a DUPLICATE socket while `shutdown()` closes the original we
    handed it — on Windows only, from a stray environment variable.
    """
    monkeypatch.setenv("WEB_CONCURRENCY", "4")

    sock = launcher.bind_socket()
    with closing(sock):
        server = launcher.DesktopServer(_health_app(), sock)
        assert server.config.workers == 1


@pytest.mark.parametrize("attr", ["log_config"])
def test_uvicorn_is_not_allowed_to_reconfigure_logging(attr):
    """
    uvicorn's default `LOGGING_CONFIG` attaches `StreamHandler`s to
    `sys.stdout` and `sys.stderr`. A frozen windowed build has neither, and
    `local_bootstrap` already disables alembic's logger for exactly that reason
    — writing to that handle can raise. Logging belongs to the shell (Task 5,
    constraint #16), so the server must leave it alone.
    """
    sock = launcher.bind_socket()
    with closing(sock):
        server = launcher.DesktopServer(_health_app(), sock)
        assert getattr(server.config, attr) is None
