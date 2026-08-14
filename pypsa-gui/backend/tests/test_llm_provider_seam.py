"""Seam tests for the provider extraction (2026-08-13 plan)."""
from __future__ import annotations


def test_redaction_module_scrubs_key_shapes(monkeypatch):
    from services import redaction
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123xyz")
    out = redaction.redact_for_log("boom: sk-ant-abc123xyz in trace")
    assert "sk-ant-abc123xyz" not in out
    assert "[REDACTED-API-KEY]" in out
    out2 = redaction.redact_secrets_in_str("password=hunter2 bearer tok123")
    assert "hunter2" not in out2 and "tok123" not in out2


def test_chat_service_redaction_aliases_still_exist():
    from services import chat_service, redaction
    assert chat_service._redact_for_log is redaction.redact_for_log
    assert chat_service._redact_secrets_in_str is redaction.redact_secrets_in_str


def test_llm_event_vocabulary_and_provider_error():
    from services.llm_provider import (
        ERROR_KINDS, EVENT_TYPES, LLMEvent, LLMRequest, ProviderError,
    )
    assert "unreachable" in ERROR_KINDS and "rate_limited" in ERROR_KINDS
    ev = LLMEvent(type="text_delta", text="hi")
    assert ev.blocks == [] and ev.usage == {}
    assert "message_done" in EVENT_TYPES
    err = ProviderError("rate_limited", "429 from upstream")
    assert err.kind == "rate_limited" and "429" in err.message
    # T2 — the seam's only silent coercion: an unrecognised kind string
    # collapses to "internal_error" rather than raising or passing through.
    assert ProviderError("bogus_kind", "m").kind == "internal_error"
    req = LLMRequest(
        model="m", max_tokens=10,
        system_blocks=[{"type": "text", "text": "s", "stable": True}],
        tools=[], tools_stable=True, messages=[], history_stable_anchor=None,
    )
    assert req.system_blocks[0]["stable"] is True


def test_fake_provider_scripts_and_records():
    import copy
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent, LLMRequest, ProviderError

    turns = [
        {"events": [LLMEvent(type="text_delta", text="he"),
                    LLMEvent(type="text_delta", text="llo")],
         "blocks": [{"type": "text", "text": "hello"}],
         "usage": {"input_tokens": 3, "output_tokens": 2}},
        ProviderError("rate_limited", "scripted 429"),
    ]
    p = FakeProvider(turns)
    req = LLMRequest(model="m", max_tokens=8,
                     system_blocks=[{"type": "text", "text": "s", "stable": True}],
                     tools=[{"name": "t", "description": "d", "input_schema": {}}],
                     tools_stable=True, messages=[], history_stable_anchor=None)
    got = list(p.stream(req))
    assert [e.type for e in got] == ["text_delta", "text_delta", "message_done"]
    assert got[-1].blocks == [{"type": "text", "text": "hello"}]
    assert got[-1].usage["input_tokens"] == 3
    # request recorded by value, not reference
    assert p.requests[0].system_blocks[0]["stable"] is True
    req.system_blocks[0]["stable"] = False
    assert p.requests[0].system_blocks[0]["stable"] is True

    import pytest
    with pytest.raises(ProviderError) as ei:
        list(p.stream(req))
    assert ei.value.kind == "rate_limited"


class _SeamFakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def __iter__(self):
        return iter(self._events)
    def get_final_message(self):
        return self._final


class _SeamFakeClient:
    def __init__(self, events, final):
        self.calls = []
        outer = self
        class _M:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                return _SeamFakeStream(events, final)
        self.messages = _M()


class _Ev:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Final:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage


def _seam_request(messages=None, anchor=None):
    from services.llm_provider import LLMRequest
    return LLMRequest(
        model="claude-sonnet-5", max_tokens=64,
        system_blocks=[{"type": "text", "text": "SYS", "stable": True}],
        tools=[{"name": "a", "description": "d", "input_schema": {"type": "object"}},
               {"name": "b", "description": "d", "input_schema": {"type": "object"}}],
        tools_stable=True,
        messages=messages if messages is not None else [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": [{"type": "text", "text": "prior"}]},
        ],
        history_stable_anchor=anchor,
    )


def test_anthropic_provider_translates_events_and_cache_markers():
    from services.llm_anthropic import AnthropicProvider

    block = _Ev("content_block_start",
                content_block=_Ev("tool_use", id="tu1", name="a"))
    fake = _SeamFakeClient(
        events=[_Ev("text", text="hi"), _Ev("thinking", thinking="hm"),
                block, _Ev("content_block_delta")],
        final=_Final(content=[{"type": "text", "text": "hi"}],
                     usage=_Ev("u", input_tokens=7, output_tokens=3,
                               cache_read_input_tokens=5,
                               cache_creation_input_tokens=1)),
    )
    events = list(AnthropicProvider(fake).stream(_seam_request(anchor=1)))
    kinds = [e.type for e in events]
    # unhandled SDK events surface as ping (abort latency preserved)
    assert kinds == ["text_delta", "thinking_delta", "tool_use_start",
                     "ping", "message_done"]
    assert events[0].text == "hi"
    assert events[2].tool_use_id == "tu1" and events[2].tool_name == "a"
    done = events[-1]
    assert done.blocks == [{"type": "text", "text": "hi"}]
    assert done.usage == {"input_tokens": 7, "output_tokens": 3,
                          "cache_read_tokens": 5, "cache_create_tokens": 1}

    kw = fake.calls[0]
    # stable → cache_control at the three sites, and NOWHERE in the request
    # does the word "stable" survive (the SDK would 400 on it).
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "stable" not in kw["system"][-1]
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kw["tools"][0]
    marked = kw["messages"][1]["content"][-1]
    assert marked["cache_control"] == {"type": "ephemeral"}
    assert kw["model"] == "claude-sonnet-5" and kw["max_tokens"] == 64


def test_anthropic_provider_maps_exceptions_to_provider_error():
    import pytest
    from services.llm_anthropic import AnthropicProvider
    from services.llm_provider import ProviderError

    class _Boom:
        def __init__(self):
            class _M:
                def stream(self, **kwargs):
                    raise RuntimeError("kaput sk-ant-secret123")
            self.messages = _M()

    with pytest.raises(ProviderError) as ei:
        list(AnthropicProvider(_Boom()).stream(_seam_request()))
    # no real `anthropic` classes matched → internal_error, message redacted
    assert ei.value.kind == "internal_error"
    assert "sk-ant-secret123" not in ei.value.message


def test_chat_service_seam_aliases_point_at_llm_anthropic():
    from services import chat_service, llm_anthropic
    assert chat_service._build_anthropic_client is llm_anthropic.build_client
    assert chat_service._map_sdk_exception is llm_anthropic.map_sdk_exception
    assert chat_service._serialise_for_anthropic is llm_anthropic.serialise_block
    assert (chat_service._with_history_cache_breakpoint
            is llm_anthropic.with_history_cache_breakpoint)


def test_run_turn_accepts_a_provider_and_emits_identical_frames():
    from services import chat_service
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent

    session = chat_service.ChatSession(model="claude-sonnet-5")
    fake = FakeProvider([
        {"events": [LLMEvent(type="text_delta", text="he"),
                    LLMEvent(type="text_delta", text="llo")],
         "blocks": [{"type": "text", "text": "hello"}],
         "usage": {"input_tokens": 5, "output_tokens": 2}},
    ])
    events = list(chat_service.run_turn(session, "hi", provider=fake))
    names = [n for n, _ in events]
    assert names[0] == "session_init"
    assert names.count("token") == 2
    # A successful turn with no further tool_use ends at `turn_done` — no
    # trailing `session_done` (pinned by test_chat_e2e.py: event_names[-1]
    # == "turn_done" for the identical no-more-tools scenario). The brief's
    # Step 1 snippet asserted names[-1] == "session_done"; corrected here to
    # match that pinned invariant rather than changing run_turn's behaviour.
    assert ("turn_done" in names) and (names[-1] == "turn_done")
    # the request carried the stable annotations (cache-cost guard)
    req = fake.requests[0]
    assert req.system_blocks[-1]["stable"] is True
    assert req.tools_stable is True
    assert req.max_tokens == chat_service.MAX_OUTPUT_TOKENS_PER_TURN
    # Against the registry, never a literal — chat_tools_schema's own doctrine
    # is "len(TOOLS) is the only source of truth". A hardcoded 117 broke the
    # first time this branch merged into a trunk whose other waves had grown
    # the registry (to 120), with a green branch gate on both sides.
    from services.chat_tools_schema import TOOLS
    assert len(req.tools) == len(TOOLS)


def _sse_bytes(*chunks):
    out = b""
    for c in chunks:
        out += b"data: " + c + b"\n\n"
    return out + b"data: [DONE]\n\n"


def test_openai_compat_streams_text_tools_reasoning_and_usage():
    import httpx, json
    from services.llm_openai_compat import OpenAICompatProvider

    body = _sse_bytes(
        b'{"choices":[{"delta":{"reasoning_content":"hmm"}}]}',
        b'{"choices":[{"delta":{"content":"he"}}]}',
        b'{"choices":[{"delta":{"content":"llo"}}]}',
        b'{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        b'"function":{"name":"list_projects","arguments":"{\\"li"}}]}}]}',
        b'{"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"mit\\":2}"}}]}}]}',
        b'{"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":4}}',
    )
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(req.content)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})

    provider = OpenAICompatProvider(
        "http://localhost:11434/v1", api_key="k",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    req = _seam_request()
    events = list(provider.stream(req))
    kinds = [e.type for e in events]
    assert kinds[0] == "thinking_delta" and events[0].text == "hmm"
    assert kinds.count("text_delta") == 2
    assert kinds[-1] == "message_done"
    done = events[-1]
    assert {"type": "text", "text": "hello"} in done.blocks
    tool = [b for b in done.blocks if b["type"] == "tool_use"][0]
    assert tool["id"] == "c1" and tool["name"] == "list_projects"
    assert tool["input"] == {"limit": 2}
    assert done.usage == {"input_tokens": 11, "output_tokens": 4}
    # request translation
    sent = captured["json"]
    assert captured["auth"] == "Bearer k"
    assert sent["stream"] is True
    assert sent["messages"][0]["role"] == "system"
    assert sent["tools"][0]["function"]["name"] == "a"
    assert "cache_control" not in json.dumps(sent)  # stable dropped, silently


def test_openai_compat_maps_connect_and_status_errors():
    import httpx, pytest
    from services.llm_openai_compat import OpenAICompatProvider
    from services.llm_provider import ProviderError

    def refuse(req):
        raise httpx.ConnectError("refused")

    p = OpenAICompatProvider(
        "http://localhost:11434/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(ProviderError) as ei:
        list(p.stream(_seam_request()))
    assert ei.value.kind == "unreachable"

    for status, kind in [(401, "unauthorized"), (429, "rate_limited"),
                         (404, "invalid_request"), (500, "upstream_error")]:
        pp = OpenAICompatProvider(
            "http://h/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(
                lambda r, s=status: httpx.Response(s, json={"error": {}}))))
        with pytest.raises(ProviderError) as ei:
            list(pp.stream(_seam_request()))
        assert ei.value.kind == kind, status


def _openai_provider_scripted_hello():
    import httpx
    from services.llm_openai_compat import OpenAICompatProvider
    body = _sse_bytes(
        b'{"choices":[{"delta":{"content":"he"}}]}',
        b'{"choices":[{"delta":{"content":"llo"}}]}',
        b'{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}',
    )
    return OpenAICompatProvider(
        "http://fake/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, content=body,
                headers={"content-type": "text/event-stream"}))))


def _fake_provider_scripted_hello():
    from services.llm_fake import FakeProvider
    from services.llm_provider import LLMEvent
    return FakeProvider([
        {"events": [LLMEvent(type="text_delta", text="he"),
                    LLMEvent(type="text_delta", text="llo")],
         "blocks": [{"type": "text", "text": "hello"}],
         "usage": {"input_tokens": 5, "output_tokens": 2}},
    ])


def test_seam_same_harness_behaviour_across_providers():
    """The seam spec's core assertion: a test that only runs against the
    fake proves nothing about the abstraction."""
    from services import chat_service

    def frames(provider):
        session = chat_service.ChatSession(model="claude-sonnet-5")
        out = list(chat_service.run_turn(session, "hi", provider=provider))
        # session_id differs per run; compare names + token deltas only
        return ([n for n, _ in out],
                [p["delta"] for n, p in out if n == "token"])

    fake_names, fake_tokens = frames(_fake_provider_scripted_hello())
    oai_names, oai_tokens = frames(_openai_provider_scripted_hello())
    assert fake_names == oai_names
    assert fake_tokens == oai_tokens == ["he", "llo"]


def test_stable_markers_reach_all_sites_via_fake():
    """Cache-cost guard: without this, the tenfold input-cost regression is
    invisible (seam spec, capability section)."""
    from services import chat_service
    from services.llm_provider import LLMEvent
    from services.llm_fake import FakeProvider

    session = chat_service.ChatSession(model="claude-sonnet-5")
    fake = FakeProvider([
        {"events": [], "blocks": [{"type": "text", "text": "a"}],
         "usage": {}},
        {"events": [], "blocks": [{"type": "text", "text": "b"}],
         "usage": {}},
    ])
    list(chat_service.run_turn(session, "one", provider=fake))
    list(chat_service.run_turn(session, "two", provider=fake))
    first, second = fake.requests[0], fake.requests[1]
    assert first.system_blocks[-1]["stable"] and first.tools_stable
    assert first.history_stable_anchor is None      # no history yet
    assert second.history_stable_anchor is not None  # anchored to turn 1
    assert 0 <= second.history_stable_anchor < len(second.messages)


import os
import pytest


@pytest.mark.skipif(
    not os.environ.get("PYPSA_GUI_TEST_OLLAMA_URL"),
    reason="no local OpenAI-compatible endpoint configured")
def test_seam_against_live_local_endpoint():
    from services import chat_service
    from services.llm_openai_compat import OpenAICompatProvider
    provider = OpenAICompatProvider(
        os.environ["PYPSA_GUI_TEST_OLLAMA_URL"],
        api_key=os.environ.get("PYPSA_GUI_TEST_OLLAMA_KEY"))
    session = chat_service.ChatSession(
        model=os.environ.get("PYPSA_GUI_TEST_OLLAMA_MODEL", "qwen3:8b"))
    names = [n for n, _ in chat_service.run_turn(
        session, "Reply with the single word: ok", provider=provider)]
    # Successful turns end at turn_done (pinned by test_chat_e2e.py:213).
    assert names[0] == "session_init" and names[-1] == "turn_done"
    assert "token" in names


def test_run_turn_catches_non_provider_error_stream_failures():
    """I1 — restore the harness catch-all.

    Pre-branch, `except Exception` ran every stream failure through mapping →
    `_metric_error(kind)` + terminal `logger.error` + `error` frame +
    `session_done`. The provider extraction narrowed the clause to
    `except llm_provider.ProviderError`, so a provider that raises anything
    outside that typed contract (a bug in the provider, e.g. a bare
    ValueError) escaped `run_turn` entirely — no metric, no terminal log
    line, and the router only catches it generically as a bare internal_error
    frame, breaking the documented frame contract. This pins the restored
    catch-all: an unmapped exception still becomes error_kind="internal_error",
    still gets redacted before it reaches the frame, and still increments
    errors_by_kind.

    Note: with the catch-all restored, FakeProvider's own script-exhaustion
    signal (`AssertionError: FakeProvider: script exhausted`, used elsewhere
    in this file) is likewise swallowed into an internal_error frame rather
    than propagating as a loud test-failing traceback — this matches
    pre-branch semantics (a bare `except Exception` always did this) and is
    accepted here, not a new regression.
    """
    from services import chat_service
    from services.llm_provider import LLMRequest

    class _BoomProvider:
        name = "boom"

        def stream(self, request: LLMRequest):
            raise ValueError("boom sk-ant-leak123")
            yield  # pragma: no cover - never reached; makes this a generator

    chat_service._reset_metrics_for_tests()
    session = chat_service.ChatSession(model="claude-sonnet-5")
    events = list(chat_service.run_turn(session, "hi", provider=_BoomProvider()))
    names = [n for n, _ in events]

    assert "error" in names
    error_payload = dict(events[names.index("error")][1])
    assert error_payload["error_kind"] == "internal_error"
    assert "sk-ant-leak123" not in error_payload["message"]
    assert names[-1] == "session_done"

    snap = chat_service._metrics_snapshot()
    assert snap["errors_by_kind"]["internal_error"] == 1
