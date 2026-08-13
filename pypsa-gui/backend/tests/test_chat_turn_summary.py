"""
Improvement #11 — trimmed turns leave a trace instead of vanishing.

`trim_session_messages` drops the oldest complete turn group once the deque
passes SESSION_MESSAGES_MAX. Pairing-aware, so it never orphans a tool_use
— that half shipped. What did not is any record that the drop happened.

The agent does not experience this as "my context was trimmed". It
experiences it as those turns never occurring. A user who says "put that
generator back the way it was" is referring to a turn the agent can no
longer see and does not know it cannot see, so the reply is a confident
guess rather than "I no longer have that".

The summary is DETERMINISTIC, not an LLM call. The audit allows either
("an LLM call (or template)"), and a template is the right first move: an
extra model call inside `run_turn` would sit in a loop that already carries
a bounded retry, a model-fallback path, and cache breakpoints that must
stay byte-stable across retries — and it would have to be computed once per
turn rather than once per attempt or it would pay twice and move the
breakpoint under itself. A record of WHICH turns went and WHICH tools ran
recovers the referent, which is the actual failure; better prose does not.
"""
from __future__ import annotations

import collections

import pytest

from services import chat_service


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str, tools: list[str] | None = None) -> dict:
    blocks: list[dict] = [{"type": "text", "text": text}]
    for i, t in enumerate(tools or []):
        blocks.append({"type": "tool_use", "id": f"tu{i}", "name": t, "input": {}})
    return {"role": "assistant", "content": blocks}


def _tool_results(ids: list[str]) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": i, "content": "ok"} for i in ids
        ],
    }


def _turn(user_text: str, reply: str, tools: list[str] | None = None) -> list[dict]:
    msgs = [_user(user_text), _assistant(reply, tools)]
    if tools:
        msgs.append(_tool_results([f"tu{i}" for i in range(len(tools))]))
    return msgs


def _deque(turns: list[list[dict]]) -> collections.deque:
    d: collections.deque = collections.deque()
    for t in turns:
        d.extend(t)
    return d


def test_nothing_is_summarised_when_nothing_is_dropped():
    d = _deque([_turn("hello", "hi")])
    chat_service.trim_session_messages(d, max_len=100)

    assert len(d) == 2
    assert not chat_service.is_turn_summary(d[0])


def test_a_dropped_turn_leaves_a_summary_at_the_head():
    d = _deque([
        _turn("size the battery", "sized it", ["batch_create_components"]),
        _turn("now solve", "solved"),
        _turn("show me the cost", "here"),
    ])
    chat_service.trim_session_messages(d, max_len=4)

    assert chat_service.is_turn_summary(d[0]), "oldest turns vanished without trace"
    assert d[0]["role"] == "user"
    assert isinstance(d[0]["content"], str)


def test_the_summary_names_the_user_messages_that_were_dropped():
    """The referent is what the user lost — 'that generator' has to resolve."""
    d = _deque([
        _turn("size the battery for the German node", "ok"),
        _turn("keep this one", "ok"),
    ])
    chat_service.trim_session_messages(d, max_len=2)

    assert "size the battery for the German node" in d[0]["content"]


def test_the_summary_names_the_tools_that_ran():
    """
    What the agent DID is as load-bearing as what was said: "put it back"
    is unanswerable without knowing a batch_create ran.
    """
    d = _deque([
        _turn("build it", "done", ["batch_create_components", "run_simulation"]),
        _turn("later", "ok"),
    ])
    chat_service.trim_session_messages(d, max_len=2)

    assert "batch_create_components" in d[0]["content"]
    assert "run_simulation" in d[0]["content"]


def test_the_summary_says_how_many_turns_went():
    d = _deque([_turn(f"turn {i}", "ok") for i in range(6)])
    chat_service.trim_session_messages(d, max_len=4)

    assert "4" in d[0]["content"] or "four" in d[0]["content"].lower()


def test_repeated_trims_keep_exactly_one_summary():
    """
    The accumulation trap. A summary is itself the oldest message, so a
    naive implementation either drops it on the next trim — losing the
    record it just made — or prepends a second one and grows a pile of
    summaries that eventually fills the window it exists to protect.
    """
    d = _deque([_turn(f"turn {i}", "ok") for i in range(4)])
    chat_service.trim_session_messages(d, max_len=4)
    for i in range(4, 12):
        d.extend(_turn(f"turn {i}", "ok"))
        chat_service.trim_session_messages(d, max_len=4)

    summaries = [m for m in d if chat_service.is_turn_summary(m)]
    assert len(summaries) == 1
    # And it is still first: a summary buried mid-history would claim to
    # describe turns that are visible right above it.
    assert chat_service.is_turn_summary(d[0])


def test_the_summary_absorbs_later_drops_rather_than_being_dropped():
    d = _deque([_turn("the very first thing", "ok")])
    d.extend(_turn("second", "ok"))
    chat_service.trim_session_messages(d, max_len=2)
    assert "the very first thing" in d[0]["content"]

    for i in range(3, 9):
        d.extend(_turn(f"turn {i}", "ok"))
        chat_service.trim_session_messages(d, max_len=2)

    # The count keeps climbing even though the earliest text has aged out.
    assert chat_service.is_turn_summary(d[0])
    assert "6" in d[0]["content"] or "7" in d[0]["content"]


def test_the_summary_cannot_grow_without_bound():
    """
    It is prepended to every request from here on, so an unbounded summary
    would consume the context budget it exists to defend.
    """
    d: collections.deque = collections.deque()
    for i in range(200):
        d.extend(_turn(f"a fairly wordy user message number {i} " * 4, "ok",
                       [f"tool_{i}"]))
        chat_service.trim_session_messages(d, max_len=6)

    assert len(d[0]["content"]) <= chat_service.TURN_SUMMARY_MAX_CHARS


def test_trimming_still_never_orphans_a_tool_use():
    """
    The invariant that already shipped and must survive this change: an
    assistant tool_use whose tool_result was trimmed away is a 400 from
    Anthropic on the very next request.
    """
    d = _deque([
        _turn("one", "ok", ["run_simulation"]),
        _turn("two", "ok", ["get_meta"]),
        _turn("three", "ok"),
    ])
    chat_service.trim_session_messages(d, max_len=4)

    open_ids = set()
    for m in d:
        if m["role"] == "assistant" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    open_ids.add(b["id"])
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    open_ids.discard(b["tool_use_id"])
    assert not open_ids, f"orphaned tool_use ids: {open_ids}"


def test_the_first_surviving_message_is_one_anthropic_accepts():
    """
    The API requires the first message to be a user turn, and a bare
    tool_result block as message[0] is rejected. The summary has to be
    plain user text.
    """
    d = _deque([
        _turn("one", "ok", ["run_simulation"]),
        _turn("two", "ok"),
    ])
    chat_service.trim_session_messages(d, max_len=2)

    assert d[0]["role"] == "user"
    assert isinstance(d[0]["content"], str)


def test_a_session_appending_normally_gets_the_summary(monkeypatch):
    """
    End to end through the real entry point, not just the helper: the trim
    runs inside append_history_message, which is what run_turn calls.
    """
    monkeypatch.setattr(chat_service, "SESSION_MESSAGES_MAX", 4)
    session = chat_service.ChatSession()
    for i in range(8):
        for msg in _turn(f"message {i}", "ok"):
            session.append_history_message(msg)

    assert chat_service.is_turn_summary(session.messages[0])
    assert len(session.messages) <= 5  # cap + the summary
