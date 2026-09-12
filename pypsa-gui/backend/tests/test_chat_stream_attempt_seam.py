"""
Phase D tripwire — `_stream_assistant_message` lifted out of `_run_turn_body`.

The retry loop, and the highest-risk cut in the plan. It drains one assistant
message off the stream, and on failure decides between three very different
things: retry, downgrade the model once, or surface the error and end the turn.

DRIVEN THROUGH A PROVIDER, NOT AN SDK CLIENT. The extraction on master streamed
via `client.messages.stream(...)` and returned the SDK's own message object; the
harness here scripted that shape. This line also reaches OpenAI-compatible
endpoints, so the seam takes an `LLMProvider` and a prepared `LLMRequest`, and
returns normalised blocks + usage. Every property below is the one master's
version asserted — only the double changed, from a fake SDK client to a fake
provider, and failures are raised as the `ProviderError` the provider contract
documents instead of monkeypatching `_map_sdk_exception`.

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

ONE CORRECTION TO THAT REASONING, from actually running the mutation (master,
same file). Hoisting `emitted_this_attempt` out of the attempt loop does NOT
produce the duplicate: the flag is only ever read inside the same attempt that
can set it, and any attempt that sets it then leaves the loop (terminal error,
completed stream, or abort), so the hoist is inert and survives every case
below. The term that actually carries the guarantee is
`and not emitted_this_attempt` in the `retriable` condition; dropping THAT
turns `test_a_retriable_error_AFTER_emission_does_not_retry` red with
`attempts == 2`. The per-attempt reset stays, because it is what keeps the
inert version inert — but the tripwire is on the condition, not the reset.

Three exits, all of which must survive
--------------------------------------
* the stream completes -> `break`, and the turn continues to tool dispatch;
* abort mid-stream -> one `session_done` with `reason="aborted"`, turn over;
* terminal or exhausted error -> `error` then `session_done`, turn over.

A generator cannot end its caller's turn, so the last two come back as
`stop_turn` on the returned outcome.

`model_fallback` is the odd one: a persistent `rate_limited` on a profile that
DECLARES a fallback buys exactly ONE extra attempt on that model, granted by
widening `max_attempts` rather than by resetting `attempt`. It mutates
`session.model`, which outlives the turn — the session stays downgraded — so
this is not a detail the seam may drop. And the once-only flag is per TURN while
this seam runs once per assistant STEP, so it travels in and back out; the last
case here is what pins that.
"""
from __future__ import annotations

import inspect

import pytest

from services import chat_service, llm_provider


def _ev(etype, **kw):
    return llm_provider.LLMEvent(type=etype, **kw)


class _Profile:
    """The two fields the seam reads off the turn's profile."""

    def __init__(self, fallback_model=None, pid="p1"):
        self.fallback_model = fallback_model
        self.id = pid


class _Provider:
    """
    Scripted per-attempt behaviour: each entry is either an exception to raise
    or a list of events to stream.
    """

    name = "fake"

    def __init__(self, script):
        self._script = list(script)
        self.attempts = 0
        # What each attempt actually asked the provider for. The fallback only
        # reaches the wire if `request.model` is re-read per attempt.
        self.models_seen = []

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


def _request():
    return llm_provider.LLMRequest(
        model="m", max_tokens=16,
        system_blocks=[{"type": "text", "text": "sys", "stable": True}],
        tools=[], tools_stable=True,
        messages=[{"role": "user", "content": "hi"}],
        history_stable_anchor=None,
    )


def _drive(provider, session=None, profile=None, model_fallback_used=False):
    """
    Run the seam to completion, returning (frames, outcome).

    `request.model` is deliberately NOT set from the session here: the seam
    re-reads `session.model` into the request on every attempt, which is the
    mechanism the A8 fallback depends on, and `_Provider.models_seen` is what
    checks it actually happened.
    """
    session = session or chat_service.ChatSession()
    frames = []
    gen = _seam()(
        session, provider,
        request=_request(),
        profile=profile or _Profile(),
        model_fallback_used=model_fallback_used,
    )
    try:
        while True:
            frames.append(next(gen))
    except StopIteration as stop:
        return frames, stop.value


def _rate_limited():
    return llm_provider.ProviderError("rate_limited", "429")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The retry path sleeps with capped exponential backoff; not here."""
    monkeypatch.setattr(chat_service.time, "sleep", lambda _s: None)


def test_the_seam_is_a_generator_returning_an_outcome():
    assert inspect.isgeneratorfunction(_seam())


def test_a_completed_stream_returns_the_blocks_and_usage():
    blocks = [{"type": "text", "text": "hi"}]
    usage = {"input_tokens": 3, "output_tokens": 4}
    frames, out = _drive(_Provider([[
        _ev("text_delta", text="hi"),
        _ev("message_done", blocks=blocks, usage=usage),
    ]]))
    assert out.stop_turn is False
    assert out.final_blocks == blocks
    assert out.final_usage == usage
    assert [n for n, _ in frames] == ["token"]


def test_a_retriable_error_before_any_emission_retries_silently():
    """
    Nothing has reached the client, so a second attempt is safe and the user
    sees one clean answer — no error frame for the swallowed failure.
    """
    provider = _Provider([
        llm_provider.ProviderError("upstream_error", "transient"),
        [_ev("text_delta", text="second try"), _ev("message_done")],
    ])
    frames, out = _drive(provider)
    assert provider.attempts == 2
    assert out.stop_turn is False
    names = [n for n, _ in frames]
    assert names == ["token"], f"a retried attempt leaked frames: {names}"


def test_a_retriable_error_AFTER_emission_does_not_retry():
    """
    The one that matters. A token already reached the client, so retrying would
    replay the answer. The seam must surface the error instead — and the text
    must appear EXACTLY ONCE.
    """
    def _dies_mid_stream():
        yield _ev("text_delta", text="half an ans")
        raise llm_provider.ProviderError("upstream_error", "died mid-stream")

    class _P(_Provider):
        def stream(self, request):
            self.attempts += 1
            return _dies_mid_stream()

    provider = _P([])
    frames, out = _drive(provider)
    assert provider.attempts == 1, (
        "it retried after emitting — the client would show the answer twice"
    )
    assert out.stop_turn is True
    names = [n for n, _ in frames]
    assert names == ["token", "error", "session_done"], names
    assert sum(1 for n, _ in frames if n == "token") == 1


def test_an_unmapped_exception_is_still_metriced_and_surfaced():
    """
    The provider contract says `stream` raises `ProviderError`. A provider bug
    that lets something else out must NOT skip the terminal path — narrowing
    the except clause to `ProviderError` would let it escape `run_turn`
    entirely, and only the router's bare catch-all would notice.
    """
    provider = _Provider([ValueError("a provider bug")])
    frames, out = _drive(provider)
    assert out.stop_turn is True
    assert [n for n, _ in frames] == ["error", "session_done"]
    assert frames[0][1]["error_kind"] == "internal_error"


def test_the_retry_budget_is_finite_and_ends_in_an_error():
    provider = _Provider(
        [llm_provider.ProviderError("upstream_error", "nope")]
        * (chat_service.MAX_STREAM_RETRIES + 1)
    )
    frames, out = _drive(provider)
    assert provider.attempts == chat_service.MAX_STREAM_RETRIES + 1
    assert out.stop_turn is True
    assert [n for n, _ in frames] == ["error", "session_done"]


def test_persistent_rate_limiting_buys_one_fallback_attempt():
    session = chat_service.ChatSession()
    session.model = "big-model"
    profile = _Profile(fallback_model="small-model")
    # Exhaust the retries, then the fallback attempt succeeds.
    script = [_rate_limited()] * (chat_service.MAX_STREAM_RETRIES + 1)
    script.append([_ev("text_delta", text="cheaper"), _ev("message_done")])
    provider = _Provider(script)
    frames, out = _drive(provider, session=session, profile=profile)

    assert session.model == "small-model", (
        "the session was not downgraded; the fallback outlives the turn"
    )
    fb = [p for n, p in frames if n == "model_fallback"]
    assert fb and fb[0] == {
        "from_model": "big-model",
        "to_model": "small-model",
        "reason": "rate_limited",
        "profile_id": "p1",
    }
    assert out.stop_turn is False
    # Downgrading `session.model` is only half the job under this seam: the
    # REQUEST carries the model to the provider, so it has to be re-read per
    # attempt or the "fallback" would keep asking for the original model.
    assert provider.models_seen[0] == "big-model"
    assert provider.models_seen[-1] == "small-model"


def test_a_profile_that_declares_no_fallback_never_downgrades():
    """
    `fallback_model=None` is how a profile opts out — the built-in sonnet
    profile among them, which preserves the pre-Task-7 "sonnet never falls
    back" behaviour exactly.
    """
    session = chat_service.ChatSession()
    session.model = "only-model"
    provider = _Provider([_rate_limited()] * 20)
    frames, out = _drive(provider, session=session, profile=_Profile(None))
    assert not [n for n, _ in frames if n == "model_fallback"]
    assert session.model == "only-model"
    assert out.stop_turn is True


def test_the_fallback_is_granted_at_most_once_within_one_step():
    session = chat_service.ChatSession()
    session.model = "big-model"
    provider = _Provider([_rate_limited()] * 20)
    frames, out = _drive(provider, session=session,
                         profile=_Profile(fallback_model="small-model"))
    assert sum(1 for n, _ in frames if n == "model_fallback") == 1
    assert out.stop_turn is True


def test_the_once_only_flag_travels_in_and_back_out_across_steps():
    """
    THE PER-TURN BOUND. This seam runs once per assistant STEP, but the
    downgrade is bounded per TURN — so the flag is a parameter and comes back
    on the outcome. Owning it inside would re-arm the fallback on every step of
    an agentic turn, and each step would look individually correct.

    `docs/superpowers/findings/2026-09-09-chat-stream-loop-two-vestigial-guards.md`
    records the flag as redundant with the `session.model == OPUS_MODEL` test
    that used to sit beside it. That was true only for the hardcoded pair: once
    the fallback fired, `session.model` stopped being Opus. Reading
    `profile.fallback_model` instead removes that coincidence, so the flag is
    the only real bound — this is the case that fails if it is folded back in.
    """
    session = chat_service.ChatSession()
    session.model = "big-model"
    profile = _Profile(fallback_model="small-model")

    _frames, first = _drive(_Provider([_rate_limited()] * 20),
                            session=session, profile=profile)
    assert first.model_fallback_used is True, (
        "the outcome does not carry the flag back, so the caller cannot bound "
        "the downgrade across the turn"
    )

    frames, out = _drive(_Provider([_rate_limited()] * 20), session=session,
                         profile=profile,
                         model_fallback_used=first.model_fallback_used)
    assert not [n for n, _ in frames if n == "model_fallback"], (
        "a second assistant step downgraded again — the bound is per turn"
    )
    assert out.stop_turn is True


def test_an_abort_mid_stream_ends_the_turn_immediately():
    session = chat_service.ChatSession()

    def _aborts_after_first():
        yield _ev("text_delta", text="a")
        session.abort_event.set()
        yield _ev("text_delta", text="b")

    class _P(_Provider):
        def stream(self, request):
            self.attempts += 1
            return _aborts_after_first()

    frames, out = _drive(_P([]), session=session)
    assert out.stop_turn is True
    names = [n for n, _ in frames]
    assert names[-1] == "session_done"
    assert frames[-1][1] == {"reason": "aborted"}
    assert "b" not in "".join(p.get("delta", "") for n, p in frames if n == "token"), (
        "it kept streaming after the abort"
    )
