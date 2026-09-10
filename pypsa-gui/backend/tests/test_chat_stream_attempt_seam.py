"""
Phase D tripwire — `_stream_assistant_message` lifted out of `_run_turn_body`.

The retry loop, and the highest-risk cut in the plan. It drains one assistant
message off the SDK stream, and on failure decides between three very different
things: retry, downgrade the model once, or surface the error and end the turn.

The property that makes this dangerous to move
----------------------------------------------
**Retry is only safe before anything has been emitted.** Once a `token` or
`thinking` frame has reached the client, retrying replays the model's answer
from the start and the panel shows the text twice. `emitted_this_attempt` is
what prevents that, and it is per ATTEMPT, not per turn. An extraction that
hoists it, or that resets it in the wrong place, produces duplicated output for
a user under transient SDK load — and every existing test still passes, because
the frames are individually well-formed. So the guard here asserts on the
COUNT of emitted text, not just its presence.

Three exits, all of which must survive
--------------------------------------
* the stream completes -> `break`, and the turn continues to tool dispatch;
* abort mid-stream -> one `session_done` with `reason="aborted"`, turn over;
* terminal or exhausted error -> `error` then `session_done`, turn over.

A generator cannot end its caller's turn, so the last two come back as
`stop_turn` on the returned outcome.

`model_fallback` is the odd one: a persistent `rate_limited` on Opus buys
exactly ONE extra attempt on Sonnet, granted by widening `max_attempts` rather
than by resetting `attempt`. It mutates `session.model`, which outlives the turn
— the session stays downgraded — so this is not a detail the seam may drop.
"""
from __future__ import annotations

import inspect

import pytest

from services import chat_service


class _Ev:
    def __init__(self, etype, **kw):
        self.type = etype
        for k, v in kw.items():
            setattr(self, k, v)


class _Final:
    def __init__(self, content=(), usage=None):
        self.content = list(content)
        self.usage = usage


class _Stream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield from self._events

    def get_final_message(self):
        return self._final


class _Client:
    """
    Scripted per-attempt behaviour: each entry is either an exception to raise
    or `(events, final_message)` to stream.
    """

    def __init__(self, script):
        self._script = list(script)
        self.attempts = 0
        outer = self

        class _M:
            def stream(self, **kwargs):
                outer.attempts += 1
                if not outer._script:
                    raise AssertionError("script exhausted")
                item = outer._script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return _Stream(*item)

        self.messages = _M()


def _seam():
    fn = getattr(chat_service, "_stream_assistant_message", None)
    assert fn is not None, "chat_service._stream_assistant_message does not exist yet"
    return fn


def _drive(client, session=None, messages=None):
    session = session or chat_service.ChatSession()
    frames = []
    gen = _seam()(
        session, client,
        system_blocks=[{"type": "text", "text": "sys"}],
        tools_with_cache=[],
        messages=messages if messages is not None else [{"role": "user", "content": "hi"}],
        history_cache_anchor=None,
    )
    try:
        while True:
            frames.append(next(gen))
    except StopIteration as stop:
        return frames, stop.value


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The retry path sleeps with capped exponential backoff; not here."""
    monkeypatch.setattr(chat_service.time, "sleep", lambda _s: None)


def test_the_seam_is_a_generator_returning_an_outcome():
    assert inspect.isgeneratorfunction(_seam())


def test_a_completed_stream_returns_the_final_message():
    final = _Final(content=["block"])
    frames, out = _drive(_Client([([_Ev("text", text="hi")], final)]))
    assert out.stop_turn is False
    assert out.final_message is final
    assert [n for n, _ in frames] == ["token"]


def test_a_retriable_error_before_any_emission_retries_silently():
    """
    Nothing has reached the client, so a second attempt is safe and the user
    sees one clean answer — no error frame for the swallowed failure.
    """
    import anthropic  # noqa: F401 — only to confirm the mapping path exists

    boom = RuntimeError("upstream blew up")
    final = _Final()
    client = _Client([boom, ([_Ev("text", text="second try")], final)])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(chat_service, "_map_sdk_exception",
                   lambda _e: ("upstream_error", "transient"))
        frames, out = _drive(client)
    assert client.attempts == 2
    assert out.stop_turn is False
    names = [n for n, _ in frames]
    assert names == ["token"], f"a retried attempt leaked frames: {names}"


def test_a_retriable_error_AFTER_emission_does_not_retry(monkeypatch):
    """
    The one that matters. A token already reached the client, so retrying would
    replay the answer. The seam must surface the error instead — and the text
    must appear EXACTLY ONCE.
    """
    monkeypatch.setattr(chat_service, "_map_sdk_exception",
                        lambda _e: ("upstream_error", "transient"))

    class _FailsMidStream(_Stream):
        def __iter__(self):
            yield _Ev("text", text="half an ans")
            raise RuntimeError("died mid-stream")

    session = chat_service.ChatSession()
    client = _Client([])
    outer = client

    class _M:
        def stream(self, **kw):
            outer.attempts += 1
            return _FailsMidStream([], _Final())

    client.messages = _M()

    frames, out = _drive(client, session=session)
    assert client.attempts == 1, (
        "it retried after emitting — the client would show the answer twice"
    )
    assert out.stop_turn is True
    names = [n for n, _ in frames]
    assert names == ["token", "error", "session_done"], names
    assert sum(1 for n, _ in frames if n == "token") == 1


def test_the_retry_budget_is_finite_and_ends_in_an_error(monkeypatch):
    monkeypatch.setattr(chat_service, "_map_sdk_exception",
                        lambda _e: ("upstream_error", "nope"))
    client = _Client([RuntimeError("x")] * (chat_service.MAX_STREAM_RETRIES + 1))
    frames, out = _drive(client)
    assert client.attempts == chat_service.MAX_STREAM_RETRIES + 1
    assert out.stop_turn is True
    assert [n for n, _ in frames] == ["error", "session_done"]


def test_persistent_rate_limiting_on_opus_buys_one_sonnet_attempt(monkeypatch):
    monkeypatch.setattr(chat_service, "_map_sdk_exception",
                        lambda _e: ("rate_limited", "429"))
    session = chat_service.ChatSession()
    session.model = chat_service.OPUS_MODEL
    final = _Final()
    # Exhaust the retries, then the fallback attempt succeeds.
    script = [RuntimeError("429")] * (chat_service.MAX_STREAM_RETRIES + 1)
    script.append(([_Ev("text", text="cheaper")], final))
    frames, out = _drive(_Client(script), session=session)

    assert session.model == chat_service.DEFAULT_MODEL, (
        "the session was not downgraded; the fallback outlives the turn"
    )
    fb = [p for n, p in frames if n == "model_fallback"]
    assert fb and fb[0] == {
        "from_model": chat_service.OPUS_MODEL,
        "to_model": chat_service.DEFAULT_MODEL,
        "reason": "rate_limited",
    }
    assert out.stop_turn is False and out.final_message is final


def test_the_fallback_is_granted_at_most_once(monkeypatch):
    """
    `model_fallback_used` is per turn. Without it a rate-limited session would
    loop downgrading forever.
    """
    monkeypatch.setattr(chat_service, "_map_sdk_exception",
                        lambda _e: ("rate_limited", "429"))
    session = chat_service.ChatSession()
    session.model = chat_service.OPUS_MODEL
    client = _Client([RuntimeError("429")] * 20)
    frames, out = _drive(client, session=session)
    assert sum(1 for n, _ in frames if n == "model_fallback") == 1
    assert out.stop_turn is True


def test_an_abort_mid_stream_ends_the_turn_immediately():
    session = chat_service.ChatSession()

    class _AbortsAfterFirst(_Stream):
        def __iter__(self):
            yield _Ev("text", text="a")
            session.abort_event.set()
            yield _Ev("text", text="b")

    client = _Client([])

    class _M:
        def stream(self, **kw):
            client.attempts += 1
            return _AbortsAfterFirst([], _Final())

    client.messages = _M()
    frames, out = _drive(client, session=session)
    assert out.stop_turn is True
    names = [n for n, _ in frames]
    assert names[-1] == "session_done"
    assert frames[-1][1] == {"reason": "aborted"}
    assert "b" not in "".join(p.get("delta", "") for n, p in frames if n == "token"), (
        "it kept streaming after the abort"
    )
