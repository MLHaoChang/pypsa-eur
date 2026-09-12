"""
Phase 2 — solver-log bridge (F9 + F10 + M3/F8 invariants).

Exit criterion (f): solver bridge stub case correctly streams [PHASE] lines
as tool_progress payloads + unsubscribes on early SSE close (the
BufferedLogQueue's _subscribers dict is empty after the test) + the None
sentinel does NOT appear in the subscriber deque.

The tests work directly against `BufferedLogQueue` + `chat_service.
solver_log_bridge` so they exercise the contract without spinning up a
real solver thread.
"""
from __future__ import annotations

import threading
import time

import pypsa
import pytest

from routers.simulation import BufferedLogQueue
from services import chat_service


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def _bind_log_queue_to_ctx(install_network, q: BufferedLogQueue) -> ProjectContext:
    """Install a tiny network and inject a fake log_queue into solver_state."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)
    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    with ctx.solver_state_lock:
        ctx.solver_state["log_queue"] = q
    return ctx


def _wait_for_subscriber(q: BufferedLogQueue, timeout: float = 2.0) -> bool:
    """
    Poll `q._subscribers` until at least one subscriber registers.

    Phase 2 tests run a producer thread AFTER the bridge generator subscribes.
    Without this barrier the producer can push every line before the bridge
    even calls `log_queue.subscribe()`, and the new subscriber's deque starts
    empty.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with q._sub_lock:
            if q._subscribers:
                return True
        time.sleep(0.005)
    return False


# ─────────────────────────────────────────────────────────────────────────
# F10 — capture (ctx, log_queue) at tool entry
# ─────────────────────────────────────────────────────────────────────────


def test_solver_bridge_streams_phase_lines(install_network):
    q = BufferedLogQueue(maxlen=100)
    ctx = _bind_log_queue_to_ctx(install_network, q)
    session = chat_service.ChatSession()

    # Producer thread waits for the bridge to subscribe BEFORE pushing — else
    # the fanout misses every line that lands before subscribe() runs.
    done_flag = threading.Event()

    def producer():
        assert _wait_for_subscriber(q), "bridge never subscribed"
        q.put("[PHASE] Started")
        q.put("[PHASE] Optimising")
        q.put("[PHASE] Completed")
        time.sleep(0.05)
        done_flag.set()

    t = threading.Thread(target=producer)
    t.start()

    payloads: list[dict] = []
    for payload in chat_service.solver_log_bridge(
        session, ctx,
        poll_interval=0.01,
        is_solver_done=done_flag.is_set,
    ):
        payloads.append(payload)
    t.join()

    lines = [p["line"] for p in payloads]
    kinds = [p["kind"] for p in payloads]
    assert lines == ["[PHASE] Started", "[PHASE] Optimising", "[PHASE] Completed"]
    assert kinds == ["PHASE", "PHASE", "PHASE"]


# ─────────────────────────────────────────────────────────────────────────
# F8 / M3 — None sentinel does NOT appear in the subscriber deque (and so
# is never yielded by the bridge).
# ─────────────────────────────────────────────────────────────────────────


def test_solver_bridge_skips_none_sentinel(install_network):
    """
    The solver emits None to signal end-of-stream to the legacy log_stream
    consumer. The subscriber deque must NOT receive it; the bridge must end
    via is_solver_done rather than yielding a None payload.
    """
    q = BufferedLogQueue(maxlen=100)
    ctx = _bind_log_queue_to_ctx(install_network, q)
    session = chat_service.ChatSession()

    done_flag = threading.Event()

    def producer():
        assert _wait_for_subscriber(q), "bridge never subscribed"
        q.put("[PHASE] Started")
        q.put(None)   # close-signal: must NOT reach subscriber
        q.put("[PHASE] Completed")  # one more after the sentinel
        time.sleep(0.05)
        done_flag.set()

    t = threading.Thread(target=producer)
    t.start()

    payloads: list[dict] = []
    for payload in chat_service.solver_log_bridge(
        session, ctx,
        poll_interval=0.01,
        is_solver_done=done_flag.is_set,
    ):
        payloads.append(payload)
    t.join()

    lines = [p["line"] for p in payloads]
    assert None not in lines
    assert lines == ["[PHASE] Started", "[PHASE] Completed"]


# ─────────────────────────────────────────────────────────────────────────
# F9 — try/finally unsubscribe on early close (generator GC'd)
# ─────────────────────────────────────────────────────────────────────────


def test_solver_bridge_unsubscribes_on_early_close(install_network):
    """
    Closing the generator before the solver finishes must release the
    subscriber — _subscribers must be empty afterwards. F9 invariant.
    """
    q = BufferedLogQueue(maxlen=100)
    ctx = _bind_log_queue_to_ctx(install_network, q)
    session = chat_service.ChatSession()

    # Producer thread emits lines AFTER the bridge subscribes (so the new
    # subscriber's deque actually receives them) and never finishes — the
    # bridge would block forever without an external close.
    stop_producer = threading.Event()

    def producer():
        assert _wait_for_subscriber(q), "bridge never subscribed"
        while not stop_producer.is_set():
            q.put("[PHASE] tick")
            time.sleep(0.01)

    t = threading.Thread(target=producer)
    t.start()

    bridge = chat_service.solver_log_bridge(
        session, ctx,
        poll_interval=0.01,
        is_solver_done=lambda: False,
    )

    try:
        # Consume one payload, then close the generator (simulating an SSE
        # client disconnect mid-stream).
        first = next(bridge)
        assert first["line"] == "[PHASE] tick"
        bridge.close()
    finally:
        stop_producer.set()
        t.join(timeout=2.0)

    # F9 — finally clause MUST have called unsubscribe.
    with q._sub_lock:
        assert len(q._subscribers) == 0, (
            f"subscriber leaked after early close: {q._subscribers!r}"
        )


def test_solver_bridge_unsubscribes_on_exception_propagation(install_network):
    """If the consumer raises while iterating, the finally still unsubscribes."""
    q = BufferedLogQueue(maxlen=100)
    ctx = _bind_log_queue_to_ctx(install_network, q)
    session = chat_service.ChatSession()

    stop_producer = threading.Event()

    def producer():
        assert _wait_for_subscriber(q), "bridge never subscribed"
        while not stop_producer.is_set():
            q.put("[PHASE] tick")
            time.sleep(0.01)

    t = threading.Thread(target=producer)
    t.start()
    bridge = chat_service.solver_log_bridge(
        session, ctx, poll_interval=0.01, is_solver_done=lambda: False,
    )
    try:
        next(bridge)
        # throw() drives the generator to its finally clause.
        with pytest.raises(RuntimeError):
            bridge.throw(RuntimeError("consumer crashed"))
    finally:
        stop_producer.set()
        t.join(timeout=2.0)
    with q._sub_lock:
        assert len(q._subscribers) == 0


# ─────────────────────────────────────────────────────────────────────────
# Abort path — session.abort_event terminates the bridge cleanly
# ─────────────────────────────────────────────────────────────────────────


def test_solver_bridge_abort_event_terminates(install_network):
    """
    Pre-setting abort_event causes the bridge to exit on first iteration
    without emitting any payloads, and unsubscribe runs cleanly.
    """
    q = BufferedLogQueue(maxlen=100)
    ctx = _bind_log_queue_to_ctx(install_network, q)
    session = chat_service.ChatSession()
    session.abort_event.set()  # pre-set so the bridge exits on first iteration

    payloads: list[dict] = []
    for payload in chat_service.solver_log_bridge(
        session, ctx,
        poll_interval=0.01,
        is_solver_done=lambda: False,
    ):
        payloads.append(payload)

    # No lines were pushed AFTER subscribe (the queue was empty) so the
    # bridge should exit without emitting anything and finally unsubscribes.
    assert payloads == []
    with q._sub_lock:
        assert len(q._subscribers) == 0


# ─────────────────────────────────────────────────────────────────────────
# F10 — no active solver (log_queue is None) → bridge yields nothing
# ─────────────────────────────────────────────────────────────────────────


def test_solver_bridge_noop_when_no_active_solver(install_network):
    """If solver_state['log_queue'] is None the bridge is a no-op generator."""
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)
    from services.pypsa_service import PyPSAService
    ctx = PyPSAService.get_active_context()
    # Ensure no log_queue
    with ctx.solver_state_lock:
        ctx.solver_state.pop("log_queue", None)
    session = chat_service.ChatSession()

    payloads = list(chat_service.solver_log_bridge(session, ctx))
    assert payloads == []


# ─────────────────────────────────────────────────────────────────────────
# Line classification covers the documented kinds
# ─────────────────────────────────────────────────────────────────────────


def test_classify_solver_line_kinds():
    classify = chat_service._classify_solver_line
    assert classify("[PHASE] Started") == "PHASE"
    assert classify("[VALIDATION] ERROR: ...") == "VALIDATION"
    assert classify("TRACEBACK: ...") == "TRACEBACK"
    assert classify("ERROR: ...") == "ERROR"
    assert classify("info-only message") == "INFO"


# ─────────────────────────────────────────────────────────────────────────
# The root-logger -> SSE bridge, which was dead in production.
#
# `solver_service` attaches `_ThreadScopedQueueHandler` (a
# `logging.handlers.QueueHandler`) to the ROOT logger for the duration of a
# solve, constructed with a `BufferedLogQueue`. `QueueHandler.enqueue` calls
# `put_nowait`. `BufferedLogQueue` defined `put` and not `put_nowait`, so
# every record raised `AttributeError` inside `Handler.emit` — which the
# logging module swallows by design. The solve log the user watches carried
# only the explicit `log_queue.put()` calls (the `[PHASE]` markers) and NONE
# of the `pypsa.*` / `linopy.*` / HiGHS output that the handler's own
# docstring calls "what the user reads".
#
# `test_s0_6_solve_log_excludes_other_threads_but_keeps_pypsa_lines` was
# written to prevent exactly this and passed throughout, because it builds
# the handler over a `queue.SimpleQueue` — which HAS `put_nowait` — instead
# of the type production passes. A substitute collaborator cannot show a
# defect that lives in the real one's interface.
# ─────────────────────────────────────────────────────────────────────────


def test_a_queuehandler_record_actually_reaches_the_queue():
    """
    The join, over the two REAL types. Neither half is wrong on its own:
    `QueueHandler` is stdlib, and `BufferedLogQueue.put` even coerces
    `LogRecord` to text for exactly this producer. They simply never met.
    """
    import logging
    import logging.handlers

    q = BufferedLogQueue(maxlen=100)
    handler = logging.handlers.QueueHandler(q)
    logger = logging.getLogger("test.pypsa.optimization")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("HIGHS_LINE Model status: Optimal")
    finally:
        logger.removeHandler(handler)

    assert any("HIGHS_LINE" in line for line in q.history()), (
        "the root-logger bridge dropped the record: the solve log the user "
        f"reads is empty of solver output. history={q.history()}"
    )


def test_it_really_is_a_drop_in_for_simplequeue():
    """
    The class docstring opens with "Drop-in for `queue.SimpleQueue` from the
    producer side". That claim is what `solver_service` relies on, and it
    was false — asserted here against `SimpleQueue` itself rather than a
    hand-listed set, so a method the stdlib starts calling later is covered
    by the same assertion instead of needing a new one.
    """
    import queue as _queue

    expected = {
        name for name in dir(_queue.SimpleQueue)
        if not name.startswith("_") and callable(getattr(_queue.SimpleQueue, name))
    }
    missing = sorted(name for name in expected if not hasattr(BufferedLogQueue, name))
    assert not missing, f"not a drop-in: {missing}"


def test_put_nowait_records_history_and_fans_out_exactly_like_put():
    """
    DISCRIMINATION. Satisfying `QueueHandler` by delegating to the inner
    `SimpleQueue.put_nowait` would make the AttributeError go away while
    leaving the record out of `history()` and out of every subscriber deque
    — the SSE replay and the chat bridge would stay just as empty, and the
    test above would still pass if it only checked the queue.
    """
    q = BufferedLogQueue(maxlen=100)
    sub_id, dq = q.subscribe()
    try:
        q.put_nowait("NOWAIT_LINE")
        assert "NOWAIT_LINE" in q.history(), "put_nowait skipped the history buffer"
        assert "NOWAIT_LINE" in list(dq), "put_nowait skipped the fanout subscribers"
        assert q.get(timeout=1) == "NOWAIT_LINE", "put_nowait skipped the queue itself"
    finally:
        q.unsubscribe(sub_id)
