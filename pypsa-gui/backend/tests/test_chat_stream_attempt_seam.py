"""
Phase D tripwire — `_stream_assistant_message` lifted out of `_run_turn_body`.

The retry loop, and the highest-risk cut in the plan. It drains one assistant
message off the provider stream, and on failure decides between three very
different things: retry, downgrade the model once, or surface the error and end
the turn.

Ported to the provider seam
---------------------------
Master wrote this file against the Anthropic SDK directly — a `client` with
`client.messages.stream(...)` returning a context manager plus
`get_final_message()`, and `_map_sdk_exception` monkeypatched to script the
error kinds. This branch put `services/llm_provider` under that: the seam now
takes a provider and an `LLMRequest`, drains `LLMEvent`s, and reads its error
kinds off `ProviderError` rather than from a mapping function. So the HARNESS
below is rewritten and every property master pinned is kept, one for one — the
test names and the reasoning are unchanged, because the risks are unchanged.

Two assertions get stronger on the way across, both about state the neutral
vocabulary made visible: the completed-stream test now checks the blocks AND
the usage that come off `message_done` (master could only check the identity of
an opaque final message), and the A8 test also checks `request.model`, because
under this seam the downgrade only reaches the wire if the request is re-read
per attempt.

The property that makes this dangerous to move
----------------------------------------------
**Retry is only safe before anything has been emitted.** Once a `token` or
`thinking` frame has reached the client, retrying replays the model's answer
from the start and the panel shows the text twice. `emitted_this_attempt` is
what prevents that, and it is per ATTEMPT, not per turn. An extraction that
hoists it, or that resets it in the wrong place, produces duplicated output for
a user under transient load — and every existing test still passes, because the
frames are individually well-formed. So the guard here asserts on the COUNT of
emitted text, not just its presence.

One correction to that reasoning, from actually running the mutation. Master's
prose says hoisting `emitted_this_attempt` out of the attempt loop produces the
duplicate output. It does not: the flag is only ever READ inside the same
attempt that can set it, and any attempt that sets it then leaves the loop
(terminal error, completed stream, or abort), so hoisting it is inert here —
the mutation survives all nine tests below. The term that actually carries the
guarantee is `and not emitted_this_attempt` in the `retriable` condition, and
dropping THAT turns `test_a_retriable_error_AFTER_emission_does_not_retry` red
with `attempts == 2`. The per-attempt reset stays, because it is what keeps the
inert version inert; the tripwire is on the condition.

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

from services import chat_service, llm_provider


def _ev(etype: str, **kw) -> llm_provider.LLMEvent:
    return llm_provider.LLMEvent(type=etype, **kw)


def _done(blocks=(), usage=None) -> llm_provider.LLMEvent:
    """The `message_done` event — the ONLY source of blocks and usage."""
    return llm_provider.LLMEvent(
        type="message_done", blocks=list(blocks), usage=dict(usage or {}),
    )


class _Provider:
    """
    Scripted per-attempt behaviour: each entry is either an exception to raise
    or an iterable of events to stream. A generator entry lets an attempt fail
    PART WAY through its stream, which is what the emitted-then-failed case
    needs.
    """

    name = "scripted"

    def __init__(self, script):
        self._script = list(script)
        self.attempts = 0
        self.models_seen: list[str] = []

    def stream(self, request):
        self.attempts += 1
        self.models_seen.append(request.model)
        if not self._script:
            raise AssertionError("script exhausted")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return iter(item)


def _seam():
    fn = getattr(chat_service, "_stream_assistant_message", None)
    assert fn is not None, "chat_service._stream_assistant_message does not exist yet"
    return fn


def _request(session, messages=None) -> llm_provider.LLMRequest:
    return llm_provider.LLMRequest(
        model=session.model,
        max_tokens=chat_service.MAX_OUTPUT_TOKENS_PER_TURN,
        system_blocks=[{"type": "text", "text": "sys", "stable": True}],
        tools=[],
        tools_stable=True,
        messages=messages if messages is not None else [
            {"role": "user", "content": "hi"},
        ],
        history_stable_anchor=None,
    )


def _drive(provider, session=None, messages=None, request=None):
    session = session or chat_service.ChatSession()
    request = request if request is not None else _request(session, messages)
    frames = []
    gen = _seam()(session, provider, request=request)
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
    blocks = [{"type": "text", "text": "hi"}]
    usage = {"input_tokens": 11, "output_tokens": 7}
    frames, out = _drive(_Provider([[
        _ev("text_delta", text="hi"), _done(blocks, usage),
    ]]))
    assert out.stop_turn is False
    # Stronger than master's identity check: the blocks the next turn replays
    # and the usage the session accrues both come off `message_done`, and a
    # seam that dropped either would still return an outcome.
    assert out.final_blocks == blocks
    assert out.final_usage == usage
    assert [n for n, _ in frames] == ["token"]


def test_a_retriable_error_before_any_emission_retries_silently():
    """
    Nothing has reached the client, so a second attempt is safe and the user
    sees one clean answer — no error frame for the swallowed failure.
    """
    boom = llm_provider.ProviderError("upstream_error", "transient")
    provider = _Provider([boom, [_ev("text_delta", text="second try"), _done()]])
    frames, out = _drive(provider)
    assert provider.attempts == 2
    assert out.stop_turn is False
    names = [n for n, _ in frames]
    assert names == ["token"], f"a retried attempt leaked frames: {names}"


def test_an_unmapped_exception_is_handled_like_a_mapped_one():
    """
    The provider contract says `stream` raises `ProviderError`. A provider bug
    that lets something else escape must NOT skip metrics and logging — this
    branch narrowed the clause once and that is exactly what happened.
    """
    provider = _Provider([RuntimeError("a provider bug")])
    frames, out = _drive(provider)
    assert out.stop_turn is True
    assert [n for n, _ in frames] == ["error", "session_done"]
    assert frames[0][1]["error_kind"] == "internal_error"


def test_a_retriable_error_AFTER_emission_does_not_retry():
    """
    The one that matters. A token already reached the client, so retrying would
    replay the answer. The seam must surface the error instead — and the text
    must appear EXACTLY ONCE.
    """
    def _fails_mid_stream():
        yield _ev("text_delta", text="half an ans")
        raise llm_provider.ProviderError("upstream_error", "died mid-stream")

    provider = _Provider([_fails_mid_stream()])
    frames, out = _drive(provider)
    assert provider.attempts == 1, (
        "it retried after emitting — the client would show the answer twice"
    )
    assert out.stop_turn is True
    names = [n for n, _ in frames]
    assert names == ["token", "error", "session_done"], names
    assert sum(1 for n, _ in frames if n == "token") == 1


def test_the_retry_budget_is_finite_and_ends_in_an_error():
    provider = _Provider(
        [llm_provider.ProviderError("upstream_error", "nope")]
        * (chat_service.MAX_STREAM_RETRIES + 1)
    )
    frames, out = _drive(provider)
    assert provider.attempts == chat_service.MAX_STREAM_RETRIES + 1
    assert out.stop_turn is True
    assert [n for n, _ in frames] == ["error", "session_done"]


def test_persistent_rate_limiting_on_opus_buys_one_sonnet_attempt():
    session = chat_service.ChatSession()
    session.model = chat_service.OPUS_MODEL
    request = _request(session)
    blocks = [{"type": "text", "text": "cheaper"}]
    # Exhaust the retries, then the fallback attempt succeeds.
    script = [llm_provider.ProviderError("rate_limited", "429")] * (
        chat_service.MAX_STREAM_RETRIES + 1
    )
    script.append([_ev("text_delta", text="cheaper"), _done(blocks)])
    provider = _Provider(script)
    frames, out = _drive(provider, session=session, request=request)

    assert session.model == chat_service.DEFAULT_MODEL, (
        "the session was not downgraded; the fallback outlives the turn"
    )
    fb = [p for n, p in frames if n == "model_fallback"]
    assert fb and fb[0] == {
        "from_model": chat_service.OPUS_MODEL,
        "to_model": chat_service.DEFAULT_MODEL,
        "reason": "rate_limited",
    }
    assert out.stop_turn is False and out.final_blocks == blocks
    # Downgrading `session.model` is only half the job under this seam: the
    # request carries the model to the provider, so it has to be re-read per
    # attempt or the "fallback" would keep asking for Opus.
    assert provider.models_seen[-1] == chat_service.DEFAULT_MODEL
    assert provider.models_seen[0] == chat_service.OPUS_MODEL


def test_the_fallback_is_granted_at_most_once():
    """
    `model_fallback_used` is per turn. Without it a rate-limited session would
    loop downgrading forever.
    """
    session = chat_service.ChatSession()
    session.model = chat_service.OPUS_MODEL
    provider = _Provider([llm_provider.ProviderError("rate_limited", "429")] * 20)
    frames, out = _drive(provider, session=session)
    assert sum(1 for n, _ in frames if n == "model_fallback") == 1
    assert out.stop_turn is True


def test_an_abort_mid_stream_ends_the_turn_immediately():
    session = chat_service.ChatSession()

    def _aborts_after_first():
        yield _ev("text_delta", text="a")
        session.abort_event.set()
        yield _ev("text_delta", text="b")

    provider = _Provider([_aborts_after_first()])
    frames, out = _drive(provider, session=session)
    assert out.stop_turn is True
    names = [n for n, _ in frames]
    assert names[-1] == "session_done"
    assert frames[-1][1] == {"reason": "aborted"}
    assert "b" not in "".join(p.get("delta", "") for n, p in frames if n == "token"), (
        "it kept streaming after the abort"
    )
