"""
QA #14 — a client that goes away must stop the turn it was paying for.

Today the only way a turn ends early is an explicit POST to
`/{session_id}/abort`, which the panel sends on close. Every other way a
browser can vanish — the tab is killed, the laptop sleeps, the network
drops, the whole app quits — sends no abort, and the turn runs to
completion: more model tokens, and every remaining tool in the plan
actually executed, against a network nobody is watching.

The fix reuses the primitive `/abort` already flips (`session.abort_event`),
driven from the async request handler where `request` and the event loop
both exist. `_gen()` stays synchronous and untouched.

Why polling `request.is_disconnected()` is reliable here, since the obvious
reading says it should not be: Starlette runs its OWN `listen_for_disconnect`
concurrently with the response body whenever the server advertises ASGI
spec_version < 2.4 — which uvicorn's HTTP protocols do (2.3). That listener
sits permanently in `await receive()`, so it wins every delivery race
against an opportunistic poll. It does not matter, because uvicorn's
`receive()` is LEVEL-triggered: once the connection is gone it returns
`{"type": "http.disconnect"}` to every subsequent call, forever
(`h11_impl.py`'s `if self.disconnected or self.response_complete`). Both
consumers therefore observe the disconnect. A server whose `receive()` was
edge-triggered would break this, which is why the watcher is written to be
harmless when it never fires rather than depended on as the only stop.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from routers import chat as chat_router
from services import chat_service


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


class _FakeRequest:
    """
    Stands in for starlette.Request. `answers` is consumed one poll at a
    time; the last value repeats, mirroring uvicorn's level-triggered
    `receive()`.
    """

    def __init__(self, answers: list[bool]):
        self._answers = list(answers)
        self.polls = 0

    async def is_disconnected(self) -> bool:
        self.polls += 1
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0]


def _watcher(request, session, finished=None, poll_seconds=0.001):
    return chat_router._DisconnectWatcher(
        request, session, finished=finished, poll_seconds=poll_seconds,
    )


def test_watcher_aborts_the_turn_when_the_client_goes_away():
    """The whole point: a vanished client stops costing money."""
    session = chat_service.ChatSession()
    request = _FakeRequest([False, False, True])
    w = _watcher(request, session)

    asyncio.run(w._run())

    assert session.abort_event.is_set()


def test_watcher_leaves_a_connected_clients_turn_running():
    """
    A turn that is merely long must never be aborted. The watcher stops when
    the stream signals it is finished, having touched nothing.
    """
    session = chat_service.ChatSession()
    request = _FakeRequest([False])
    finished = threading.Event()

    async def drive():
        w = _watcher(request, session, finished=finished)
        task = asyncio.get_running_loop().create_task(w._run())
        # Let it poll a few times while "connected", then end the stream.
        await asyncio.sleep(0.05)
        finished.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(drive())

    assert request.polls > 0, "the watcher never polled"
    assert not session.abort_event.is_set()


def test_watcher_does_not_abort_a_turn_that_already_finished():
    """
    The ordering guard. `_gen()` sets `finished` before it disarms the
    watcher, so a watcher that wakes up in that window — and correctly sees a
    disconnected client, because the response is over — must NOT flip
    abort_event. Without the re-check it would abort whatever turn the
    session runs NEXT, since abort_event is session-scoped and this one is
    already done.
    """
    session = chat_service.ChatSession()
    request = _FakeRequest([True])
    finished = threading.Event()
    finished.set()
    w = _watcher(request, session, finished=finished)

    asyncio.run(w._run())

    assert not session.abort_event.is_set()


def test_watcher_survives_cancellation_without_touching_the_session():
    """`disarm()` cancels the task; that must not look like a disconnect."""
    session = chat_service.ChatSession()
    request = _FakeRequest([False])

    async def drive():
        w = _watcher(request, session, poll_seconds=5.0)
        task = asyncio.get_running_loop().create_task(w._run())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(drive())

    assert not session.abort_event.is_set()


def test_chat_stream_arms_and_disarms_a_watcher_for_every_turn(
    client, monkeypatch,
):
    """
    Wiring, asserted behaviourally rather than by reading the source: a real
    POST /stream must arm a watcher and must disarm it when the stream ends.
    A watcher that is armed and never disarmed is a task leaked per request.
    """
    events: list[str] = []
    real_cls = chat_router._DisconnectWatcher

    class RecordingWatcher(real_cls):  # type: ignore[misc,valid-type]
        def arm(self) -> None:
            events.append("arm")
            super().arm()

        def disarm(self) -> None:
            events.append("disarm")
            super().disarm()

    monkeypatch.setattr(chat_router, "_DisconnectWatcher", RecordingWatcher)

    resp = client.post(
        "/api/chat/stream",
        json={"session_id": "sess-watch-1",
              "script": [{"type": "session_done"}]},
    )

    assert resp.status_code == 200
    assert events == ["arm", "disarm"], (
        f"expected one arm and one disarm per stream; got {events}"
    )
