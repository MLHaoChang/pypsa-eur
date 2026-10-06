"""
Chat harness issue 20: the OpenAI-compatible wire reports usage and reasoning
the way the Anthropic wire does, so metrics, the daily cap and the panel mean
the same thing whichever provider a profile names.

Usage semantics are the Anthropic ones (harness/protocol.py, LLMEvent.usage):
`input_tokens` is UNCACHED input only, with cache reads and cache writes in
their own keys. The OpenAI-compatible vendors fold the cached part into
`prompt_tokens`, each under its own field name:

  * OpenAI    usage.prompt_tokens_details.cached_tokens
  * Kimi      usage.prompt_tokens_details.cached_tokens and .cache_write_tokens
              (also a top-level usage.cached_tokens)
  * DeepSeek  usage.prompt_cache_hit_tokens (+ prompt_cache_miss_tokens)

so the adapter subtracts them back out. Reasoning (`reasoning_content` /
`reasoning`) streams as thinking but is never replayed: it must not appear in
the `message_done` blocks.
"""
from __future__ import annotations

import json

import httpx
import pytest


def _sse(*chunks: dict) -> bytes:
    out = b""
    for c in chunks:
        out += b"data: " + json.dumps(c).encode() + b"\n\n"
    return out + b"data: [DONE]\n\n"


def _provider(body: bytes):
    from harness.providers.openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(
        "http://fake/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=body,
                                     headers={"content-type": "text/event-stream"}))))


def _request():
    from harness.protocol import LLMRequest
    return LLMRequest(
        model="m", max_tokens=64,
        system_blocks=[{"type": "text", "text": "SYS", "stable": True}],
        tools=[], tools_stable=True,
        messages=[{"role": "user", "content": "hi"}],
        history_stable_anchor=None,
    )


def _usage_for(usage_chunk: dict) -> dict:
    body = _sse({"choices": [{"delta": {"content": "ok"}}]},
                {"choices": [], "usage": usage_chunk})
    done = list(_provider(body).stream(_request()))[-1]
    assert done.type == "message_done"
    return done.usage


@pytest.mark.parametrize("vendor_usage,want", [
    pytest.param(
        {"prompt_tokens": 100, "completion_tokens": 4},
        {"input_tokens": 100, "output_tokens": 4,
         "cache_read_tokens": 0, "cache_create_tokens": 0},
        id="no-cache-fields"),
    pytest.param(
        {"prompt_tokens": 100, "completion_tokens": 4,
         "prompt_tokens_details": {"cached_tokens": 80}},
        {"input_tokens": 20, "output_tokens": 4,
         "cache_read_tokens": 80, "cache_create_tokens": 0},
        id="openai"),
    pytest.param(
        {"prompt_tokens": 100, "completion_tokens": 4, "cached_tokens": 60,
         "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 30}},
        {"input_tokens": 10, "output_tokens": 4,
         "cache_read_tokens": 60, "cache_create_tokens": 30},
        id="kimi"),
    pytest.param(
        {"prompt_tokens": 100, "completion_tokens": 4, "cached_tokens": 60},
        {"input_tokens": 40, "output_tokens": 4,
         "cache_read_tokens": 60, "cache_create_tokens": 0},
        id="kimi-top-level-only"),
    pytest.param(
        {"prompt_tokens": 100, "completion_tokens": 4,
         "prompt_cache_hit_tokens": 70, "prompt_cache_miss_tokens": 30},
        {"input_tokens": 30, "output_tokens": 4,
         "cache_read_tokens": 70, "cache_create_tokens": 0},
        id="deepseek"),
    pytest.param(
        {"prompt_tokens": 10, "completion_tokens": 4,
         "prompt_tokens_details": {"cached_tokens": 50}},
        {"input_tokens": 0, "output_tokens": 4,
         "cache_read_tokens": 50, "cache_create_tokens": 0},
        id="inconsistent-report-never-goes-negative"),
    pytest.param(
        {"prompt_tokens": 100, "completion_tokens": 4,
         "prompt_tokens_details": None, "cached_tokens": "x"},
        {"input_tokens": 100, "output_tokens": 4,
         "cache_read_tokens": 0, "cache_create_tokens": 0},
        id="malformed-fields-read-as-zero"),
])
def test_openai_wire_usage_has_anthropic_semantics(vendor_usage, want):
    assert _usage_for(vendor_usage) == want


def test_no_usage_chunk_still_reports_nothing():
    """An endpoint that never sends usage (stream_options ignored) must stay
    'never reported', not become an honest zero (W-3 in session.accrue_usage)."""
    body = _sse({"choices": [{"delta": {"content": "ok"}}]})
    assert list(_provider(body).stream(_request()))[-1].usage == {}


def test_reasoning_streams_as_thinking_and_is_never_replayed():
    body = _sse(
        {"choices": [{"delta": {"reasoning_content": "first, "}}]},
        {"choices": [{"delta": {"reasoning": "then"}}]},
        {"choices": [{"delta": {"content": "answer"}}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    )
    events = list(_provider(body).stream(_request()))
    assert [(e.type, e.text) for e in events if e.type == "thinking_delta"] == [
        ("thinking_delta", "first, "), ("thinking_delta", "then")]
    done = events[-1]
    assert done.blocks == [{"type": "text", "text": "answer"}]
    assert not any(b.get("type") in ("thinking", "redacted_thinking") for b in done.blocks)


def test_a_turn_on_the_openai_wire_shows_thinking_and_cached_usage():
    """End to end through run_turn: the panel gets `thinking` frames and the
    `turn_done` usage carries the cache read, as it does on the Anthropic wire."""
    from harness import loop

    body = _sse(
        {"choices": [{"delta": {"reasoning_content": "hmm"}}]},
        {"choices": [{"delta": {"content": "hello"}}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 2,
                                  "prompt_tokens_details": {"cached_tokens": 90}}},
    )
    session = loop.ChatSession(model="m")
    frames = list(loop.run_turn(session, "hi", provider=_provider(body)))
    names = [n for n, _ in frames]
    assert ("thinking", {"delta": "hmm"}) in frames
    assert names.index("thinking") < names.index("token")
    usage = dict(frames)["turn_done"]["usage"]
    assert usage["input_tokens"] == 10
    assert usage["cache_read_tokens"] == 90
    assert usage["output_tokens"] == 2
    assert usage["reported"] is True
    # the replayed assistant message carries no reasoning
    last = session.messages[-1]
    assert last["role"] == "assistant"
    assert all(b.get("type") != "thinking" for b in last["content"])


# ── the parity probe reports usage and thinking per prompt (issue 20) ──────

def _smoke():
    from smoke import run_chat_smoke
    return run_chat_smoke


def _result(name, usage, kinds=()):
    s = _smoke()
    r = s.PromptResult(name=name, passed=True)
    r.usage = usage
    r.frame_kinds = set(kinds)
    return r


def test_the_battery_has_a_reasoning_prompt_the_stub_answers_with_thinking():
    s = _smoke()
    import importlib.util, pathlib
    stub_path = pathlib.Path(s.__file__).with_name("stub_openai_endpoint.py")
    spec = importlib.util.spec_from_file_location("stub_for_battery", stub_path)
    stub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stub)
    w4 = [p for p in s.build_workflow_prompts() if p.name == "W4_reasoning"]
    assert len(w4) == 1 and stub._THINK in w4[0].message
    assert w4[0].expected_tools == set()


def test_per_turn_usage_is_the_difference_of_the_session_totals():
    """`turn_done.usage` is the SESSION's running total; the probe prints
    what each turn added."""
    s = _smoke()
    results = [
        _result("W1", {"input_tokens": 3, "output_tokens": 3, "cache_read_tokens": 8,
                       "cache_create_tokens": 0, "reported": True}),
        _result("W2", {"input_tokens": 9, "output_tokens": 5, "cache_read_tokens": 24,
                       "cache_create_tokens": 0, "reported": True}),
    ]
    deltas = s.per_turn_usage(results)
    assert deltas[0] == {"input_tokens": 3, "output_tokens": 3,
                         "cache_read_tokens": 8, "cache_create_tokens": 0}
    assert deltas[1] == {"input_tokens": 6, "output_tokens": 2,
                         "cache_read_tokens": 16, "cache_create_tokens": 0}
    assert s.format_usage(deltas[1]) == "in=6 cached=16 written=0 out=2"
    assert s.format_usage(None) == "usage not reported"


def test_a_wire_that_reports_no_usage_fails_the_battery():
    s = _smoke()
    results = [_result("W1_ask_user_card", None),
               _result("W2_start_workflow", {"input_tokens": 1, "reported": False})]
    problems = s.check_workflow_results(results)
    assert any("W1_ask_user_card" in p and "usage" in p for p in problems)
    assert any("W2_start_workflow" in p and "usage" in p for p in problems)
