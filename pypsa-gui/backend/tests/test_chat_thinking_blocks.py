"""
Thinking-block round-trip contract for `_serialise_for_anthropic`, plus the
two defects the same incident surfaced: 4xx retry classification and
malformed-thinking sanitisation of persisted history.

Background — measured, not hypothesised. Against `claude-sonnet-5` the API
returns `thinking` blocks by default. The serialiser used to copy a fixed
five-name allowlist ("type", "id", "name", "input", "text"), so a thinking
block replayed as a bare {"type": "thinking"} and the SECOND call of every
tool-using turn failed with:

    400 invalid_request_error —
    messages.1.content.0.thinking.thinking: Field required

These tests use plain stand-in objects (the function reads attributes), so the
suite never needs the `anthropic` package, a network, or an API key.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from services import chat_service


# ─────────────────────────────────────────────────────────────────────────
# Stand-ins for the SDK's content blocks
# ─────────────────────────────────────────────────────────────────────────


class _PydanticLikeBlock:
    """
    Mimics the SDK's pydantic content block: a `model_dump()` that honours
    `exclude_none`. The real blocks are pydantic models, so this is the path
    production actually takes.
    """

    def __init__(self, **fields: Any) -> None:
        self._fields = dict(fields)
        for key, value in fields.items():
            setattr(self, key, value)

    def model_dump(self, *, exclude_none: bool = False, **_kw: Any) -> dict[str, Any]:
        if exclude_none:
            return {k: v for k, v in self._fields.items() if v is not None}
        return dict(self._fields)


# ─────────────────────────────────────────────────────────────────────────
# Step 1/3 — serialiser round-trip contract
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("factory", [SimpleNamespace, _PydanticLikeBlock])
def test_thinking_block_keeps_thinking_and_signature(factory) -> None:
    block = factory(type="thinking", thinking="some reasoning", signature="abc123")

    out = chat_service._serialise_for_anthropic(block)

    assert out["type"] == "thinking"
    # Value equality, not key presence: an empty string here is the same 400.
    assert out["thinking"] == "some reasoning"
    assert out["signature"] == "abc123"


@pytest.mark.parametrize("factory", [SimpleNamespace, _PydanticLikeBlock])
def test_redacted_thinking_block_keeps_data(factory) -> None:
    block = factory(type="redacted_thinking", data="xyz")

    out = chat_service._serialise_for_anthropic(block)

    assert out["type"] == "redacted_thinking"
    assert out["data"] == "xyz"


@pytest.mark.parametrize("factory", [SimpleNamespace, _PydanticLikeBlock])
def test_tool_use_block_round_trips_unchanged(factory) -> None:
    """Regression guard on the behaviour the allowlist already had right."""
    block = factory(
        type="tool_use", id="tu-1", name="get_meta", input={"project": "p1"},
    )

    out = chat_service._serialise_for_anthropic(block)

    assert out["type"] == "tool_use"
    assert out["id"] == "tu-1"
    assert out["name"] == "get_meta"
    assert out["input"] == {"project": "p1"}


@pytest.mark.parametrize("factory", [SimpleNamespace, _PydanticLikeBlock])
def test_text_block_round_trips_unchanged(factory) -> None:
    block = factory(type="text", text="hello world")

    out = chat_service._serialise_for_anthropic(block)

    assert out["type"] == "text"
    assert out["text"] == "hello world"


@pytest.mark.parametrize("factory", [SimpleNamespace, _PydanticLikeBlock])
def test_none_valued_fields_are_omitted(factory) -> None:
    """`null` for an optional field the API does not expect is a 400 risk."""
    block = factory(type="text", text="hi", citations=None, cache_control=None)

    out = chat_service._serialise_for_anthropic(block)

    assert out["text"] == "hi"
    assert "citations" not in out
    assert "cache_control" not in out


def test_dict_input_passes_through() -> None:
    block = {"type": "thinking", "thinking": "r", "signature": "s"}

    assert chat_service._serialise_for_anthropic(block) == block


def test_private_attributes_are_not_serialised() -> None:
    block = SimpleNamespace(type="text", text="hi", _sdk_internal="leak")

    out = chat_service._serialise_for_anthropic(block)

    assert "_sdk_internal" not in out


# ─────────────────────────────────────────────────────────────────────────
# Step 5 — a 4xx other than 429 is terminal, not transient
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_anthropic_module():
    """
    Inject a stand-in `anthropic` module so `_map_sdk_exception`'s lazy
    isinstance checks resolve without the real SDK installed.
    """
    prev = sys.modules.get("anthropic")
    mod = types.ModuleType("anthropic")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, msg, status_code=None):
            super().__init__(msg)
            self.status_code = status_code

    mod.AuthenticationError = AuthenticationError
    mod.RateLimitError = RateLimitError
    mod.APIStatusError = APIStatusError
    sys.modules["anthropic"] = mod
    yield mod
    if prev is None:
        sys.modules.pop("anthropic", None)
    else:
        sys.modules["anthropic"] = prev


def test_status_400_maps_to_a_non_retryable_kind(fake_anthropic_module) -> None:
    body = "messages.1.content.0.thinking.thinking: Field required"
    exc = fake_anthropic_module.APIStatusError(body, status_code=400)

    kind, msg = chat_service._map_sdk_exception(exc)

    assert kind not in chat_service._RETRYABLE_SDK_KINDS
    assert kind == "invalid_request"
    # The upstream body must survive the mapping VERBATIM — it is the only
    # place the offending field is named. Contrast AuthenticationError, which
    # is deliberately replaced with a canned string.
    assert msg == body


@pytest.mark.parametrize("status", [400, 404, 413, 422])
def test_other_4xx_statuses_are_terminal(fake_anthropic_module, status) -> None:
    kind, _msg = chat_service._map_sdk_exception(
        fake_anthropic_module.APIStatusError("bad", status_code=status),
    )

    assert kind not in chat_service._RETRYABLE_SDK_KINDS


def test_status_429_still_maps_to_rate_limited(fake_anthropic_module) -> None:
    kind, _msg = chat_service._map_sdk_exception(
        fake_anthropic_module.APIStatusError("slow down", status_code=429),
    )

    assert kind == "rate_limited"
    assert kind in chat_service._RETRYABLE_SDK_KINDS


def test_status_500_still_retryable_upstream_error(fake_anthropic_module) -> None:
    kind, _msg = chat_service._map_sdk_exception(
        fake_anthropic_module.APIStatusError("overloaded", status_code=529),
    )

    assert kind == "upstream_error"
    assert kind in chat_service._RETRYABLE_SDK_KINDS


# ─────────────────────────────────────────────────────────────────────────
# Step 7 — sanitise history that was already persisted malformed
# ─────────────────────────────────────────────────────────────────────────


def test_malformed_thinking_block_dropped_from_rehydrated_history() -> None:
    session = chat_service.ChatSession()

    session.append_history_message({
        "role": "assistant",
        "content": [
            {"type": "thinking"},                     # written by the old bug
            {"type": "text", "text": "the answer"},
        ],
    })

    (msg,) = list(session.messages)
    assert msg["content"] == [{"type": "text", "text": "the answer"}]


def test_malformed_redacted_thinking_block_dropped() -> None:
    session = chat_service.ChatSession()

    session.append_history_message({
        "role": "assistant",
        "content": [
            {"type": "redacted_thinking"},
            {"type": "text", "text": "kept"},
        ],
    })

    (msg,) = list(session.messages)
    assert msg["content"] == [{"type": "text", "text": "kept"}]


def test_signed_thinking_block_with_empty_text_is_kept() -> None:
    """
    The real shape `claude-sonnet-5` returns, measured against the live API
    (SDK 0.117.0) on a reasoning-heavy prompt: adaptive thinking is on by
    default and yields ThinkingBlock(thinking="", signature=<436 chars>).

    This block is well-formed and replays fine. A truthiness-based predicate
    drops it — silently discarding the model's signed reasoning from history
    on the SHIPPED DEFAULT MODEL. Presence and type, never truthiness.
    """
    session = chat_service.ChatSession()
    signed_empty = {"type": "thinking", "thinking": "", "signature": "s" * 436}

    session.append_history_message({
        "role": "assistant",
        "content": [signed_empty, {"type": "text", "text": "the answer"}],
    })

    (msg,) = list(session.messages)
    assert msg["content"] == [signed_empty, {"type": "text", "text": "the answer"}]


def test_thinking_block_missing_signature_is_dropped() -> None:
    """Presence is still required — only the *value* may be empty."""
    session = chat_service.ChatSession()

    session.append_history_message({
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "reasoned"},   # no signature
            {"type": "text", "text": "kept"},
        ],
    })

    (msg,) = list(session.messages)
    assert msg["content"] == [{"type": "text", "text": "kept"}]


def test_thinking_field_of_wrong_type_is_dropped() -> None:
    session = chat_service.ChatSession()

    session.append_history_message({
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": None, "signature": "sig"},
            {"type": "text", "text": "kept"},
        ],
    })

    (msg,) = list(session.messages)
    assert msg["content"] == [{"type": "text", "text": "kept"}]


def test_redacted_thinking_with_empty_data_is_kept() -> None:
    session = chat_service.ChatSession()
    block = {"type": "redacted_thinking", "data": ""}

    session.append_history_message({"role": "assistant", "content": [block]})

    (msg,) = list(session.messages)
    assert msg["content"] == [block]


def test_wellformed_thinking_block_is_preserved() -> None:
    session = chat_service.ChatSession()
    content = [
        {"type": "thinking", "thinking": "reasoning", "signature": "sig"},
        {"type": "redacted_thinking", "data": "xyz"},
        {"type": "text", "text": "the answer"},
    ]

    session.append_history_message({"role": "assistant", "content": content})

    (msg,) = list(session.messages)
    assert msg["content"] == content


def test_tool_use_blocks_are_never_dropped() -> None:
    session = chat_service.ChatSession()

    session.append_history_message({
        "role": "assistant",
        "content": [
            {"type": "thinking"},
            {"type": "tool_use", "id": "tu-1", "name": "get_meta", "input": {}},
        ],
    })

    (msg,) = list(session.messages)
    assert msg["content"] == [
        {"type": "tool_use", "id": "tu-1", "name": "get_meta", "input": {}},
    ]


def test_message_left_with_no_content_is_not_appended() -> None:
    """
    An assistant turn whose ONLY block was a malformed thinking block carries
    nothing, and an empty content array is itself a 400 ("all messages must
    have non-empty content"). Drop the whole message.
    """
    session = chat_service.ChatSession()

    session.append_history_message(
        {"role": "assistant", "content": [{"type": "thinking"}]},
    )

    assert list(session.messages) == []


def test_message_arriving_with_empty_content_is_not_appended() -> None:
    """
    `content: []` is a 400 in its own right ("all messages must have non-empty
    content") and is reachable WITHOUT any dropping — a refused or aborted
    generation returns an empty `final_message.content`. The predicate that
    skips emptied messages must cover this case on the same path; testing
    `len(kept) == len(content)` first does not, because that is `0 == 0`.
    """
    session = chat_service.ChatSession()

    session.append_history_message({"role": "assistant", "content": []})

    assert list(session.messages) == []


def test_generation_with_no_blocks_is_not_recorded_in_history(
    tmp_projects_dir, install_network,
) -> None:
    """
    A refused / aborted generation returns `final_message.content == []`. The
    turn must still complete, and the empty assistant turn must not be stored
    — it would 400 the NEXT turn, which seeds from this history.
    """
    import pypsa

    from tests.test_chat_e2e import (
        FakeAnthropicClient,
        _FakeFinalMessage,
        _FakeUsage,
    )

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    final = _FakeFinalMessage(content=[], usage=_FakeUsage())
    session = chat_service.ChatSession()
    client = FakeAnthropicClient([([], final)])

    events = list(chat_service.run_turn(session, "hi", client=client))

    assert any(ev == "turn_done" for ev, _ in events)
    assert not [m for m in session.messages if m.get("content") == []]


def test_empty_content_does_not_reach_the_outbound_payload(
    tmp_projects_dir, install_network,
) -> None:
    """
    The same guard on the array actually handed to the SDK. Seeding is the
    path that matters: an empty assistant turn recorded by an older build
    reaches the API only on a LATER turn, via the history seed.
    """
    import pypsa

    from tests.test_chat_e2e import (
        FakeAnthropicClient,
        _FakeFinalMessage,
        _FakeUsage,
        _text_block,
    )

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": []},          # aborted generation
    ]
    final = _FakeFinalMessage(content=[_text_block("ok")], usage=_FakeUsage())
    client = FakeAnthropicClient([([], final)])

    list(chat_service.run_turn(
        chat_service.ChatSession(), "next question",
        client=client, message_history=history,
    ))

    sent = client.calls[0]["messages"]
    assert not [m for m in sent if m.get("content") == []]
    # The valid neighbour survives. Flattened, because
    # _with_history_cache_breakpoint rewrites string content into blocks.
    texts: list[Any] = []
    for m in sent:
        c = m.get("content")
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, list):
            texts += [b.get("text") for b in c if isinstance(b, dict)]
    assert "earlier question" in texts


def test_string_content_is_untouched() -> None:
    session = chat_service.ChatSession()

    session.append_history_message({"role": "user", "content": "hello"})

    assert list(session.messages) == [{"role": "user", "content": "hello"}]


# ─────────────────────────────────────────────────────────────────────────
# End-to-end: a streamed thinking block survives into the replayed turn
# ─────────────────────────────────────────────────────────────────────────


def test_streamed_thinking_block_survives_into_replayed_history(
    tmp_projects_dir, install_network,
) -> None:
    """
    The whole outage in one assertion: the assistant turn that run_turn
    replays to the API must carry the thinking block's `thinking` and
    `signature`, not a bare {"type": "thinking"}.
    """
    import pypsa

    from tests.test_chat_e2e import (
        FakeAnthropicClient,
        _FakeBlock,
        _FakeFinalMessage,
        _FakeUsage,
    )

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    final = _FakeFinalMessage(
        content=[
            _FakeBlock("thinking", thinking="deliberating", signature="sig-1"),
            _FakeBlock("text", text="done"),
        ],
        usage=_FakeUsage(),
    )
    session = chat_service.ChatSession()
    client = FakeAnthropicClient([([], final)])

    list(chat_service.run_turn(session, "hi", client=client))

    assistant = [m for m in session.messages if m.get("role") == "assistant"]
    blocks = assistant[-1]["content"]
    thinking = [b for b in blocks if b.get("type") == "thinking"]
    assert thinking == [
        {"type": "thinking", "thinking": "deliberating", "signature": "sig-1"},
    ]


def test_malformed_history_is_sanitised_out_of_the_outbound_payload(
    tmp_projects_dir, install_network,
) -> None:
    """
    The array actually sent to `client.messages.stream(...)` — not just
    `session.messages` — must be free of the malformed shape. This covers the
    one input to that array nothing else sanitises: a caller-supplied
    `message_history=`.
    """
    import pypsa

    from tests.test_chat_e2e import (
        FakeAnthropicClient,
        _FakeFinalMessage,
        _FakeUsage,
        _text_block,
    )

    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name=None)

    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": [
            {"type": "thinking"},                       # written by the old bug
            {"type": "text", "text": "earlier answer"},
        ]},
    ]
    final = _FakeFinalMessage(content=[_text_block("ok")], usage=_FakeUsage())
    client = FakeAnthropicClient([([], final)])

    list(chat_service.run_turn(
        chat_service.ChatSession(), "next question",
        client=client, message_history=history,
    ))

    sent = client.calls[0]["messages"]
    blocks = [b for m in sent if isinstance(m.get("content"), list)
              for b in m["content"] if isinstance(b, dict)]
    assert not [b for b in blocks if b.get("type") == "thinking"]
    # The valid neighbour survives. Compared on its own fields, because
    # _with_history_cache_breakpoint stamps cache_control onto the anchor.
    assert "earlier answer" in [
        b.get("text") for b in blocks if b.get("type") == "text"
    ]
