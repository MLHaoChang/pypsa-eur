"""
A tool the user was told failed must not run afterwards.

`services/chat_service` runs every non-solver tool on ONE module-level
`ThreadPoolExecutor(max_workers=8)` shared by all sessions, and waits with
`future.result(timeout=PER_TOOL_TIMEOUT_SECONDS)`. That measures QUEUE + RUN, not
run, and the timeout path abandons the future without cancelling it. Three
consequences, reproduced below rather than argued:

  * **A lie.** A tool that never got a thread is reported as having "exceeded the
    execution deadline". It exceeded nothing; it never started.
  * **A mutation after a refusal.** The abandoned future is still queued, so the
    handler runs when a slot frees — minutes later, long after the user read
    `tool_timeout`. For a destructive write that the user APPROVED and was then
    told had timed out, the deletion lands anyway, unreported.
  * **Cross-session interference.** Eight hung handlers in other sessions are
    enough. The victim is a trivial read in a session that did nothing wrong.

The fix keeps one budget and spends it correctly: waiting for a worker gets its
own grace period, the execution deadline is measured from the moment the handler
actually starts, and a tool that never started is CANCELLED so it cannot run
later — then reported for what it is.

What this does NOT fix, stated so it is not mistaken for fixed: the pool is
still shared, and a thread lost to a hung handler is still lost (a Python thread
cannot be killed). A pool jammed by orphans still refuses work — it now refuses
it promptly and accurately instead of silently deferring it. Per-session
isolation stays an open item.

OPEN-ITEMS item 4. Found by an independent QA review, 2026-09-12.
"""
import threading
import time

import pytest

from services import chat_service, chat_tools


_BUDGET = 2.0


@pytest.fixture
def saturated_pool():
    """
    Occupy every worker of the shared executor, and guarantee release.

    Yields `(release, max_workers)`. The `finally` is not politeness: a leaked
    hog would hold a pool slot for the rest of the session and every later
    chat-tool test would inherit this one's failure.
    """
    release = threading.Event()
    up = threading.Semaphore(0)

    def hog():
        up.release()
        release.wait(60)

    workers = chat_service._TOOL_EXECUTOR._max_workers
    try:
        for _ in range(workers):
            chat_service._TOOL_EXECUTOR.submit(hog)
        for _ in range(workers):
            assert up.acquire(timeout=20), "the shared pool never picked up the hogs"
        yield release, workers
    finally:
        release.set()


def _dispatch(tool_name: str):
    """Drive one tool_use through the real dispatch path; return (frames, results)."""
    collector: list[dict] = []
    frames = list(chat_service._dispatch_real_tool_call(
        chat_service.ChatSession(),
        {"id": "t1", "name": tool_name, "input": {}},
        collector,
    ))
    return frames, collector


def _errors(frames):
    return [payload for name, payload in frames if name == "tool_error"]


def test_a_tool_reported_as_failed_is_never_run_afterwards(
    saturated_pool, monkeypatch,
):
    """
    The headline. Read this as: the user approved a destructive tool, was told it
    timed out, and the deletion happened anyway once the pool drained.
    """
    release, _workers = saturated_pool
    ran: list[int] = []
    monkeypatch.setitem(chat_tools.DISPATCHERS, "starve_probe",
                        lambda: (ran.append(1), {"ok": True})[1])
    monkeypatch.setattr(chat_service, "PER_TOOL_TIMEOUT_SECONDS", 0.5)

    frames, results = _dispatch("starve_probe")
    assert _errors(frames), "the starved tool was not reported as failed at all"
    assert results and results[0]["is_error"] is True
    assert not ran, "it started despite the pool being fully occupied"

    # Drain the pool and give the abandoned work every chance to run.
    release.set()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not ran:
        time.sleep(0.05)
    assert not ran, (
        "the handler ran AFTER the user was told the tool failed — for an "
        "approved destructive tool that is a mutation landing after its refusal"
    )


def test_a_starved_tool_does_not_claim_it_exceeded_a_deadline(
    saturated_pool, monkeypatch,
):
    """
    Accuracy of the report, separately from the execution bug.

    "exceeded the 0.5s execution deadline" tells the model and the user to retry
    with less work, or to look for a slow network call. Neither is the problem.
    The operator needs to know the tool pool is full.
    """
    _release, _workers = saturated_pool
    monkeypatch.setitem(chat_tools.DISPATCHERS, "starve_probe2", lambda: {"ok": True})
    monkeypatch.setattr(chat_service, "PER_TOOL_TIMEOUT_SECONDS", 0.5)

    frames, _results = _dispatch("starve_probe2")
    errors = _errors(frames)
    assert errors, "no tool_error frame"
    err = errors[0]
    assert err["error_kind"] != "tool_timeout", (
        "a tool that never started was reported as having run too long: "
        f"{err['message']!r}"
    )
    assert "deadline" not in err["message"], (
        f"the message still blames a deadline nothing reached: {err['message']!r}"
    )


def test_the_execution_deadline_is_measured_from_the_handler_start(
    saturated_pool, monkeypatch,
):
    """
    Queue time must not be charged to the handler.

    Timing, because the property is about time. The handler runs for well under
    the budget but only gets a thread partway through it, so the old
    queue-inclusive measurement reports a timeout for a tool that finished
    comfortably inside its own deadline. Margins are ~0.8s on either side of the
    budget; if this one ever goes flaky, widen `_BUDGET`, do not delete it.
    """
    release, _workers = saturated_pool
    monkeypatch.setitem(chat_tools.DISPATCHERS, "slowish_probe",
                        lambda: (time.sleep(1.2), {"ok": True})[1])
    monkeypatch.setattr(chat_service, "PER_TOOL_TIMEOUT_SECONDS", _BUDGET)

    freeing = threading.Timer(1.5, release.set)
    freeing.start()
    try:
        started = time.monotonic()
        frames, results = _dispatch("slowish_probe")
        elapsed = time.monotonic() - started
    finally:
        freeing.cancel()

    assert elapsed > _BUDGET, (
        f"the pool freed too early for this test to mean anything ({elapsed:.2f}s)"
    )
    assert not _errors(frames), (
        "a handler that ran for 1.2s of a 2.0s budget was reported as timed out "
        "because it spent 1.5s waiting for a thread"
    )
    assert results and results[0].get("is_error") is not True
