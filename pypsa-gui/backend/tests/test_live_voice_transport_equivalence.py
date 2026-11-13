"""Issue 02 — typed and live-utterance input share one harness path (GK B V6–V8).

The same scripted read + carded write runs typed (no ids) and as a live
utterance (utterance_id + generation), through the Claude and OpenAI wires.
Both transports must give the same tool calls, SSE event types, card and write
count; corrections, duplicates and late utterances never repeat the write.

Claude runs through the SDK adapter over a scripted client (`FakeAnthropicClient`)
and OpenAI through `OpenAICompatProvider` over `httpx.MockTransport`, so neither
case depends on the Anthropic SDK's own HTTP transport
(`test_workflow_provider_parity[*-anthropic]` is red in some containers).
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from harness.providers.openai_compat import OpenAICompatProvider
from services import chat_service, chat_tools, llm_config, utterance_admission as ua
from tests.test_chat_e2e import (
    FakeAnthropicClient,
    _FakeFinalMessage,
    _FakeUsage,
    _text_block,
    _text_event,
    _tool_use_block,
    _tool_use_event,
)
from tests.test_workflow_provider_parity import _compatible_stream

READ = ("list_components", {"component_class": "Bus"})
WRITE = ("delete_component", {"component_class": "Bus", "name": "B2"})
SCRIPT = [READ, WRITE, None]  # None: the closing text answer
PROVIDERS = ["anthropic", "openai"]
OPENAI_URL = "https://api.openai.com/v1"


@pytest.fixture(autouse=True)
def _reset_admission():
    ua._reset_for_tests()
    yield
    ua._reset_for_tests()


@pytest.fixture
def world(tmp_projects_dir, install_network, monkeypatch):
    import harness.session as harness_session
    import pypsa

    monkeypatch.setattr(harness_session, "CONFIRMATION_TTL_SECONDS", 5.0)
    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("Bus", "B2")
    install_network(n, name=None)
    writes: list[dict] = []
    real = chat_tools.DISPATCHERS["delete_component"]

    def counted(*args, **kwargs):
        writes.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setitem(chat_tools.DISPATCHERS, "delete_component", counted)
    return n, writes


class _Wire:
    """One scripted model for one provider; each request takes the next step."""

    def __init__(self, kind, steps):
        self.kind = kind
        self.steps = list(steps)
        self._http = None
        self._kwargs = None

    def _anthropic_turn(self, index, step):
        if step is None:
            return (
                [_text_event("Done.")],
                _FakeFinalMessage(content=[_text_block("Done.")], usage=_FakeUsage()),
            )
        name, args = step
        call = f"call_{index}"
        return (
            [_tool_use_event(call, name, args)],
            _FakeFinalMessage(
                content=[_tool_use_block(call, name, args)], usage=_FakeUsage()
            ),
        )

    def run_kwargs(self, session):
        """Built once, so a second stream continues the same script."""
        if self._kwargs is None:
            self._kwargs = self._build(session)
        return self._kwargs

    def _build(self, session):
        if self.kind == "anthropic":
            client = FakeAnthropicClient(
                [self._anthropic_turn(i, s) for i, s in enumerate(self.steps)]
            )
            self.calls = client.calls
            return {"client": client}
        profile = llm_config.LLMProfile(
            id="equivalence",
            label="Equivalence",
            preset="custom",
            wire="openai",
            base_url=OPENAI_URL,
            model="gpt-6-luna",
            tools=True,
            vision=False,
            auth="bearer",
            fallback_model=None,
            max_output_tokens=256,
        )
        llm_config.save_profiles([profile], profile.id)
        session.profile_id, session.bound_wire = profile.id, "openai"
        self.calls = []

        def respond(request):
            body = json.loads(request.content)
            index = len(self.calls)
            self.calls.append(body)
            step = self.steps[index] if index < len(self.steps) else None
            name, args = step if step else (None, {})
            stream = _compatible_stream(f"call_{index}", name, args)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=stream
            )

        self._http = httpx.Client(transport=httpx.MockTransport(respond))
        return {
            "provider": OpenAICompatProvider(
                OPENAI_URL,
                api_key="synthetic-key",
                http_client=self._http,
                token_param="max_tokens",
            )
        }

    def close(self):
        if self._http is not None:
            self._http.close()


def _session(kind):
    return chat_service.ChatSession(
        model="claude-sonnet-5" if kind == "anthropic" else "gpt-6-luna"
    )


def _drive(session, text, wire, *, on_card=None, **ids):
    """Run one stream to its end, approving each card (after `on_card`)."""
    frames = []
    for event, data in chat_service.run_turn(
        session, text, **wire.run_kwargs(session), **ids
    ):
        frames.append((event, data))
        if event == "tool_pending_confirmation":
            if on_card is not None:
                on_card(data)
            session.record_decision(data["confirmation_token"], "approve")
    return frames


_VOLATILE = {
    "tool_use_id",
    "confirmation_token",
    "ttl_seconds",
    "ts",
    "timestamp",
    "usage",
    "duration_ms",
    "elapsed_ms",
    "job_id",
    "answer_id",
    "turn_id",
}


def _normalise(value):
    if isinstance(value, dict):
        return {
            k: _normalise(v) for k, v in sorted(value.items()) if k not in _VOLATILE
        }
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


def _digest(card):
    return hashlib.sha256(
        json.dumps(_normalise(card), sort_keys=True, default=str).encode()
    ).hexdigest()


def _observed(frames, writes):
    cards = [d for e, d in frames if e == "tool_pending_confirmation"]
    return {
        # Every call the model made, with its arguments (ids dropped).
        "tools": [
            (d["tool_name"], _normalise(d["args"]))
            for e, d in frames
            if e == "tool_request"
        ],
        "events": [e for e, _ in frames],
        "writes": len(writes),
        "cards": [_digest(c) for c in cards],
        "errors": [
            d.get("error_kind") for e, d in frames if e in ("error", "tool_error")
        ],
    }


def _bus_names(n):
    return sorted(n.buses.index)


@pytest.mark.parametrize("kind", PROVIDERS)
def test_typed_and_live_input_take_the_same_path(world, kind, request):
    n, writes = world
    runs = {}
    for transport in ("typed", "live"):
        # Restore B2, so each transport starts from the same network.
        if transport == "live":
            n.add("Bus", "B2")
            writes.clear()
        session = _session(kind)
        wire = _Wire(kind, SCRIPT)
        ids = {"utterance_id": "u1", "generation": 1} if transport == "live" else {}
        try:
            frames = _drive(session, "list the buses, then delete B2", wire, **ids)
        finally:
            wire.close()
        runs[transport] = (_observed(frames, writes), len(wire.calls))
        if transport == "live":
            # The live run went through admission: its id is now spent.
            again = ua.admit_utterance(
                ua.StableUtterance("u1", "again", 1, session.session_id)
            )
            assert again.status == "duplicate"
        assert _bus_names(n) == ["B1"]
        ua._reset_for_tests()

    typed, live = runs["typed"], runs["live"]
    assert typed[0]["writes"] == live[0]["writes"] == 1
    assert typed[0]["errors"] == live[0]["errors"] == []
    assert typed[0]["events"][-1] == "turn_done"
    assert typed[0]["tools"] == [READ, WRITE] and len(typed[0]["cards"]) == 1
    assert typed[0] == live[0]  # tools, args, event types, card digest, writes
    assert typed[1] == live[1] == len(SCRIPT)  # same number of model calls


def test_both_wires_reach_the_same_tools_and_write(world):
    n, writes = world
    seen = {}
    for kind in PROVIDERS:
        if kind == "openai":
            n.add("Bus", "B2")
            writes.clear()
        session = _session(kind)
        wire = _Wire(kind, SCRIPT)
        try:
            frames = _drive(
                session,
                "list the buses, then delete B2",
                wire,
                utterance_id=f"u-{kind}",
                generation=1,
            )
        finally:
            wire.close()
        observed = _observed(frames, writes)
        seen[kind] = (observed["tools"], observed["writes"], observed["cards"])
        ua._reset_for_tests()
    assert seen["anthropic"] == seen["openai"]
    assert seen["openai"][0] == [READ, WRITE] and seen["openai"][1] == 1


@pytest.mark.parametrize("kind", PROVIDERS)
def test_a_correction_during_the_write_drains_once_without_a_second_write(world, kind):
    n, writes = world
    session = _session(kind)
    wire = _Wire(kind, [*SCRIPT, None])  # the drained correction answers in text
    decisions = []

    def speak_correction(_card):
        decisions.append(
            ua.admit_utterance(
                ua.StableUtterance(
                    utterance_id="u2",
                    text="actually keep it brief",
                    generation=1,
                    session_id=session.session_id,
                )
            )
        )

    try:
        frames = _drive(
            session,
            "list the buses, then delete B2",
            wire,
            on_card=speak_correction,
            utterance_id="u1",
            generation=1,
        )
    finally:
        wire.close()

    assert [d.status for d in decisions] == ["queued"]
    assert len(writes) == 1 and _bus_names(n) == ["B1"]
    assert [e for e, _ in frames].count("turn_done") == 2
    # The drained turn asked the model about the correction, not a replay.
    last_user = [m for m in wire.calls[-1]["messages"] if m.get("role") == "user"][-1]
    assert "actually keep it brief" in json.dumps(last_user)
    assert "delete B2" not in json.dumps(last_user)
    assert [e for e, _ in frames].count("tool_pending_confirmation") == 1
    assert len(wire.calls) == len(SCRIPT) + 1
    assert ua._DEFAULT.active_lease(session.session_id) is None


@pytest.mark.parametrize("kind", PROVIDERS)
def test_a_duplicate_utterance_id_repeats_nothing(world, kind):
    n, writes = world
    session = _session(kind)
    # The second stream is scripted to delete again: only admission stops it.
    wire = _Wire(kind, [*SCRIPT, WRITE, None])
    try:
        _drive(
            session,
            "list the buses, then delete B2",
            wire,
            utterance_id="u1",
            generation=1,
        )
        calls_after_first = len(wire.calls)
        # The same final delivered again (a reconnect or a late delegation).
        n.add("Bus", "B2")
        again = _drive(
            session,
            "list the buses, then delete B2",
            wire,
            utterance_id="u1",
            generation=1,
        )
    finally:
        wire.close()

    assert [d.get("error_kind") for e, d in again if e == "error"] == ["duplicate"]
    assert len(writes) == 1 and "B2" in _bus_names(n)
    assert len(wire.calls) == calls_after_first  # no model call


@pytest.mark.parametrize("kind", PROVIDERS)
def test_a_late_utterance_from_an_older_generation_repeats_nothing(world, kind):
    n, writes = world
    session = _session(kind)
    # The second stream is scripted to delete again: only admission stops it.
    wire = _Wire(kind, [*SCRIPT, WRITE, None])
    try:
        _drive(
            session,
            "list the buses, then delete B2",
            wire,
            utterance_id="u1",
            generation=1,
        )
        calls_after_first = len(wire.calls)
        ua._DEFAULT.bump_generation(session_id=session.session_id)  # a rebind
        n.add("Bus", "B2")
        # A new id, but spoken before the rebind: its generation is stale.
        late = _drive(session, "delete B2", wire, utterance_id="u9", generation=1)
    finally:
        wire.close()

    assert [d.get("error_kind") for e, d in late if e == "error"] == [
        "stale_generation"
    ]
    assert len(writes) == 1 and "B2" in _bus_names(n)
    assert len(wire.calls) == calls_after_first
