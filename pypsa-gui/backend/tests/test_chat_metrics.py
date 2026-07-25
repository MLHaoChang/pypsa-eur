"""
#20 — chat observability /metrics endpoint + metric increments.

Covers:
  * GET /api/chat/metrics returns the expected snapshot shape.
  * A happy-path run_turn bumps `turns` and accrues cumulative tokens.
  * A turn-terminal error records the matching error_kind.
  * Metrics reset is wired into the chat autouse fixture (no cross-test bleed).

The metrics globals live in chat_service (_METRICS under _METRICS_LOCK). The
autouse `_reset_chat_sessions` fixture below calls
`chat_service._reset_sessions_for_tests()`, which folds in the metrics reset —
so each test starts from a zeroed snapshot.
"""
from __future__ import annotations

import pytest

from services import chat_service


# ── Reuse the e2e FakeAnthropicClient shape (kept local to avoid a cross-file
#    test import). Mirrors test_chat_e2e._Fake* exactly. ───────────────────


class _FakeStreamEvent:
    def __init__(self, etype, **fields):
        self.type = etype
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeBlock:
    def __init__(self, btype, **fields):
        self.type = btype
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeFinalMessage:
    def __init__(self, content, usage):
        self.content = content
        self.usage = usage


class _FakeUsage:
    def __init__(self, *, input_tokens=10, output_tokens=20,
                  cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _FakeStream:
    def __init__(self, events, final_message):
        self._events = events
        self._final = final_message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        yield from self._events

    def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, client):
        self._client = client

    def stream(self, **kwargs):
        return self._client._next_turn(**kwargs)


class FakeAnthropicClient:
    def __init__(self, turns, raise_on_stream=None):
        self._turns = list(turns)
        self.messages = _FakeMessages(self)
        self.raise_on_stream = raise_on_stream
        self.calls = []

    def _next_turn(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        if not self._turns:
            raise AssertionError("FakeAnthropicClient: script exhausted")
        events, final = self._turns.pop(0)
        return _FakeStream(events, final)


def _text_event(text):
    return _FakeStreamEvent("text", text=text)


def _text_block(text):
    return _FakeBlock("text", text=text)


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    """Wipe sessions + metrics + rate buckets around every test."""
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def test_metrics_endpoint_shape(client):
    resp = client.get("/api/chat/metrics")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("turns", "retries", "errors_by_kind", "p50_ms", "p95_ms",
                "cumulative_tokens", "samples"):
        assert key in body, f"missing metrics key {key!r}"
    assert isinstance(body["errors_by_kind"], dict)
    assert set(body["cumulative_tokens"]) == {"input", "output"}


def test_metrics_increments_on_turn(tmp_projects_dir, install_network):
    """A happy-path turn bumps `turns` and accrues cumulative tokens + duration."""
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1")
    install_network(n, name=None)

    session = chat_service.ChatSession()
    turn_events = [_text_event("hi")]
    turn_final = _FakeFinalMessage(
        content=[_text_block("hi")], usage=_FakeUsage(input_tokens=11, output_tokens=7),
    )
    client_fake = FakeAnthropicClient([(turn_events, turn_final)])
    list(chat_service.run_turn(session, "hello", client=client_fake))

    snap = chat_service._metrics_snapshot()
    assert snap["turns"] >= 1
    assert snap["cumulative_tokens"]["input"] == 11
    assert snap["cumulative_tokens"]["output"] == 7
    assert snap["samples"] >= 1  # one duration recorded


def test_metrics_records_error_kind(tmp_projects_dir, monkeypatch, install_network):
    """A missing-API-key turn records errors_by_kind['missing_api_key']."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import pypsa
    n = pypsa.Network(); n.add("Bus", "B1")
    install_network(n, name=None)

    session = chat_service.ChatSession()
    list(chat_service.run_turn(session, "hi"))  # no client → missing_api_key

    snap = chat_service._metrics_snapshot()
    assert snap["errors_by_kind"].get("missing_api_key", 0) >= 1


def test_metrics_reset_clears_between_tests():
    """The autouse fixture's reset zeroed the counters for THIS test."""
    snap = chat_service._metrics_snapshot()
    assert snap["turns"] == 0
    assert snap["cumulative_tokens"] == {"input": 0, "output": 0}
    assert snap["errors_by_kind"] == {}


def test_percentile_helper_basic():
    vals = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert chat_service._percentile(sorted(vals), 0.0) == 0.1
    assert chat_service._percentile(sorted(vals), 1.0) == 0.5
    assert chat_service._percentile([], 0.5) == 0.0
