"""A2.1/A2.2/A2.6/A2.7: abnormal provider endings and the exact repair policy.

Every case checks the public surfaces on the wire, not just the verdict: the
SSE frames, the approved-answer record, chat history, chat export and the text
live speech would queue (``live_speech.AnswerCapture`` fed the real frames).
"""

import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from harness import budget, loop
from harness.protocol import LLMEvent, ProviderError
from harness.providers.openai_compat import OpenAICompatProvider
from services import live_speech
from tests.assurance_acceptance.support import (
    UNSAFE,
    approval,
    observed_draft,
    parse_sse,
    public_text,
    text_turn,
    tool_turn,
)

READ = {"component_class": "Load", "name": "L1"}


def public_surfaces(harness, frames, monkeypatch):
    """Every channel a user can see or hear after the turn, as text."""
    surfaces = {"sse": json.dumps(frames)}
    for route in ("/api/chat/history", "/api/chat/export"):
        response = harness.client.get(route)
        assert response.status_code == 200, response.text[:300]
        surfaces[route] = response.text
    found = [d for e, d in frames if e == "answer_approved"]
    spoken = []
    if found:
        record = harness.client.get(f"/api/chat/answers/{found[0]['answer_id']}")
        assert record.status_code == 200
        surfaces["answer"] = record.text
        monkeypatch.setattr(
            live_speech, "queue_answer", lambda *a, **k: spoken.append(a[3]) or "queued"
        )
        session = SimpleNamespace(
            assurance_turn=SimpleNamespace(
                approved={
                    "answer_id": found[0]["answer_id"],
                    "text": record.json()["text"],
                }
            ),
            abort_event=threading.Event(),
            session_id="speech-check",
        )
        capture = live_speech.AnswerCapture(session, "speech:1", 0)
        for event, data in frames:
            capture.note(event, data)
    surfaces["tts"] = "".join(spoken)
    return surfaces


def assert_nowhere(surfaces, *needles):
    for name, text in surfaces.items():
        for needle in needles:
            assert needle not in text, f"{needle[:40]!r} reached the {name} surface"


# ── F1: a truncated draft that still parses ends limited ────────────────────


def _sse(*chunks):
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_truncated_draft_that_parses_still_ends_limited_on_the_wire(
    baseline, harness, monkeypatch, dispatched, finish_reason
):
    draft = observed_draft()
    json.loads(draft)  # the fragment is complete, valid JSON
    replies = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "read-load",
                                    "type": "function",
                                    "function": {
                                        "name": "get_component",
                                        "arguments": json.dumps(READ),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
        _sse(
            {"choices": [{"delta": {"content": draft}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": finish_reason}]},
        ),
    ]
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=replies[len(calls) - 1],
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        harness.install(
            OpenAICompatProvider(
                "https://wire.invalid/v1",
                api_key="k",
                http_client=http,
                token_param="max_tokens",
            )
        )
        sid = "assurance-trunc-" + finish_reason
        harness.session_ids.append(sid)
        response = harness.client.post(
            "/api/chat/stream",
            json={
                "session_id": sid,
                "message": "Read the selected project using get_component, then answer.",
                "assurance": {"version": 1, "mode": "strict"},
            },
        )
    assert response.status_code == 200
    frames = parse_sse(response.text)
    # One read and one draft: the truncated draft is never repaired.
    assert len(calls) == 2, "A truncated draft was repaired with another provider call"
    assert dispatched == ["get_component"]
    approved = approval(frames)
    assert approved["verdict"] == "limited", "A truncated draft was certified"
    assert approved["receipt"]["checked_block_ids"] == []
    assert_nowhere(public_surfaces(harness, frames, monkeypatch), draft, "load-input")


@pytest.mark.parametrize(
    "finish_reason,truncated",
    [("length", True), ("content_filter", True), ("stop", False)],
)
def test_message_done_carries_the_wire_stop_reason(finish_reason, truncated):
    """The neutral flag the gate reads, set by each text wire."""
    body = _sse(
        {"choices": [{"delta": {"content": "{}"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": finish_reason}]},
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=body
            )
        )
    ) as http:
        provider = OpenAICompatProvider(
            "https://wire.invalid/v1",
            api_key="k",
            http_client=http,
            token_param="max_tokens",
        )
        from harness.protocol import LLMRequest

        events = list(
            provider.stream(
                LLMRequest(
                    model="m",
                    max_tokens=16,
                    system_blocks=[],
                    tools=[],
                    tools_stable=False,
                    messages=[{"role": "user", "content": "x"}],
                    history_stable_anchor=None,
                )
            )
        )
    done = [e for e in events if e.type == "message_done"]
    assert done and done[0].truncated is truncated


def test_fake_truncated_draft_is_limited_and_untouched_draft_passes(
    baseline, harness, monkeypatch
):
    draft = observed_draft()
    truncated = dict(text_turn(draft), truncated=True)
    frames = harness.run([tool_turn("get_component", READ), truncated])
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    assert_nowhere(public_surfaces(harness, frames, monkeypatch), draft, "load-input")
    # Positive companion: the same draft without truncation is certified.
    passed = harness.run([tool_turn("get_component", READ), text_turn(draft)])
    assert approval(passed)["verdict"] == "passed"


# ── provider error before / after a fragment ────────────────────────────────


class ScriptedProvider:
    """Each turn is (events, final blocks, error raised after the events)."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    def stream(self, request):
        self.requests.append(request)
        events, blocks, error = self.turns.pop(0)
        yield from events
        if error is not None:
            raise error
        yield LLMEvent(type="message_done", blocks=blocks)


@pytest.mark.parametrize(
    "before", [False, True], ids=["after-fragment", "before-fragment"]
)
def test_provider_error_around_a_fragment_publishes_nothing(
    baseline, harness, monkeypatch, dispatched, before
):
    read = tool_turn("get_component", READ)
    fragment = [] if before else [LLMEvent(type="text_delta", text=UNSAFE)]
    failing = (fragment, None, ProviderError("upstream_error", "connection reset"))
    # Spare failing turns absorb any transport retry; none may publish.
    provider = ScriptedProvider(
        [(read["events"], read["blocks"], None)] + [failing] * 4
    )
    frames = harness.run(provider)
    # The turn ends as a typed error: no approval, no tokens, no retry.
    events = [e for e, _ in frames]
    assert "answer_approved" not in events and "token" not in events
    assert [d["error_kind"] for e, d in frames if e == "error"] == ["upstream_error"]
    assert events[-1] == "session_done"
    # A failure before any text may retry within the transport budget; once a
    # fragment has streamed, the turn never retries.
    attempts = budget.MAX_STREAM_RETRIES + 1 if before else 1
    assert len(provider.requests) == 1 + attempts
    assert dispatched == ["get_component"]
    assert_nowhere(public_surfaces(harness, frames, monkeypatch), UNSAFE)


# ── F3: an unsafe token split at every offset never assembles publicly ─────


def test_unsafe_text_split_at_every_offset_never_assembles(
    baseline, harness, monkeypatch
):
    """UNSAFE is ASCII, so each character offset is also a byte offset."""
    assert UNSAFE.isascii()
    for offset in range(1, len(UNSAFE)):
        turn = {
            "events": [
                LLMEvent(type="text_delta", text=UNSAFE[:offset]),
                LLMEvent(type="text_delta", text=UNSAFE[offset:]),
            ],
            "blocks": [{"type": "text", "text": UNSAFE}],
        }
        frames = harness.run([turn])
        assert UNSAFE not in public_text(frames), f"split at {offset} reassembled"
        assert approval(frames)["verdict"] == "limited"
        assert_nowhere(public_surfaces(harness, frames, monkeypatch), UNSAFE)


# ── A2.2: the right value under the wrong metric does not verify ────────────


@pytest.mark.parametrize("path", ["/p_set", "/q_set"])
def test_same_value_under_another_metric_is_not_certified(baseline, harness, path):
    draft = json.loads(observed_draft())
    draft["blocks"][0]["metric"] = "q_set"
    draft["blocks"][0]["source"]["path"] = path
    frames = harness.run(
        [tool_turn("get_component", READ), text_turn(json.dumps(draft))]
    )
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    assert "load-input" not in approved["receipt"]["checked_block_ids"]


# ── F4: the repair policy is exactly zero repairs ───────────────────────────


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    real = loop.dispatch_tool_call

    def spy(*args, **kwargs):
        block = args[1] if len(args) > 1 else kwargs["tu"]
        calls.append(block["name"])
        return (yield from real(*args, **kwargs))

    monkeypatch.setattr(loop, "dispatch_tool_call", spy)
    return calls


@pytest.mark.parametrize(
    "draft", ["{broken", UNSAFE, '{"schema_version":1,"blocks":[]}']
)
def test_invalid_draft_gets_zero_repairs_and_runs_no_tools(
    baseline, harness, dispatched, draft
):
    frames = harness.run([text_turn(draft)])
    assert approval(frames)["verdict"] == "limited"
    assert len(harness.provider.requests) == 1, "an invalid draft was repaired"
    assert dispatched == []


def test_invalid_draft_after_a_read_runs_only_that_read(baseline, harness, dispatched):
    frames = harness.run([tool_turn("get_component", READ), text_turn("{broken")])
    assert approval(frames)["verdict"] == "limited"
    assert len(harness.provider.requests) == 2
    assert dispatched == ["get_component"]


@pytest.mark.parametrize(
    "stop", ["max_tokens", "model_context_window_exceeded", "refusal", "end_turn"]
)
def test_anthropic_wire_reports_a_max_tokens_stop(stop):
    import anthropic as sdk

    from harness.protocol import LLMRequest
    from harness.providers.anthropic import AnthropicProvider
    from tests.test_workflow_provider_parity import _anthropic_stream

    module = next(
        c.__module__.split(".")[0]
        for c in sdk.DefaultHttpxClient.__mro__
        if c.__module__.split(".")[0] in {"httpx", "httpx2"}
    )
    http_module = __import__(module)
    body = _anthropic_stream("m", "none", None, {}).replace(
        b'"stop_reason": "end_turn"', f'"stop_reason": "{stop}"'.encode()
    )
    transport = http_module.MockTransport(
        lambda r: http_module.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )
    with http_module.Client(transport=transport) as http:
        provider = AnthropicProvider(
            sdk.Anthropic(
                api_key="k",
                base_url="https://wire.invalid",
                http_client=http,
                max_retries=0,
            )
        )
        events = list(
            provider.stream(
                LLMRequest(
                    model="m",
                    max_tokens=16,
                    system_blocks=[],
                    tools=[],
                    tools_stable=False,
                    messages=[{"role": "user", "content": "x"}],
                    history_stable_anchor=None,
                )
            )
        )
    done = [e for e in events if e.type == "message_done"]
    assert done and done[0].truncated is (stop != "end_turn")


def test_truncated_draft_with_interpretations_spends_no_review_call(
    baseline, harness, monkeypatch
):
    monkeypatch.setenv("PYPSA_GUI_ANSWER_REVIEW_MODE", "required")
    draft = json.loads(observed_draft())
    draft["interpretations"] = [
        {
            "id": "i1",
            "text": "The configured load is positive.",
            "block_ids": ["load-input"],
        }
    ]
    review = text_turn('{"version":1,"verdict":"pass","claim_ids":["i1"]}')
    frames = harness.run(
        [
            tool_turn("get_component", READ),
            dict(text_turn(json.dumps(draft)), truncated=True),
            review,
        ]
    )
    approved = approval(frames)
    assert approved["verdict"] == "limited"
    assert approved["receipt"]["reviewed_claim_ids"] == []
    assert len(harness.provider.requests) == 2, "a truncated draft was sent for review"
    assert "configured load is positive" not in public_text(frames)


def test_responses_wire_incomplete_never_reaches_message_done():
    """The Responses wire raises on response.incomplete instead of a flag."""
    from harness.providers import openai_responses

    events = [
        {"type": "response.output_text.delta", "delta": observed_draft()},
        {"type": "response.incomplete", "response": {}},
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    response = httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=body
    )
    seen = []
    with pytest.raises(ProviderError, match="response.incomplete"):
        for event in openai_responses.stream_response(response, None, {}):
            seen.append(event.type)
    assert "text_delta" in seen and "message_done" not in seen
