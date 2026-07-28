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
import socket
import time
from contextlib import asynccontextmanager, closing

import pytest
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
