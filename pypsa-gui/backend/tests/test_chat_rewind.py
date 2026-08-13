"""
Rewinding a session, so "retry" and "edit and resend" mean what they say.

Without this, a client-side retry is a lie. `session.messages` is the array
replayed to the Messages API on every turn, and it lives on the SERVER — so a
retry that only clears the screen re-sends the question with the previous
answer still sitting in the history two messages above it. The model reads its
own last answer and, reliably, says it again. The user watches the button do
nothing and concludes retry is broken, which it is.

What makes this non-trivial is that ROLE IS NOT ENOUGH to find a turn
boundary. In the Anthropic message format a tool_result comes back as
`role: "user"`, so "truncate at the last user message" cuts a turn in half and
leaves a tool_use with no matching tool_result — the exact 400 that
`_drop_oldest_turn_group` was written to avoid on the other end of the deque.
A turn START is a user message carrying no tool_result block, and that is what
this rewinds to.
"""
from __future__ import annotations

import collections

from services import chat_service


def _user(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _assistant_tool_use(tool_id: str) -> dict:
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": "get_meta", "input": {}},
    ]}


def _tool_result(tool_id: str) -> dict:
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"},
    ]}


def _session_with(msgs: list[dict]) -> chat_service.ChatSession:
    s = chat_service.ChatSession()
    s.messages = collections.deque(msgs)
    return s


def test_rewinding_one_turn_drops_the_question_and_its_answer():
    s = _session_with([
        _user("first"), _assistant("first answer"),
        _user("second"), _assistant("second answer"),
    ])

    dropped = chat_service.rewind_session(s, turns=1)

    assert dropped == 2
    assert [m["role"] for m in s.messages] == ["user", "assistant"]
    assert s.messages[0]["content"][0]["text"] == "first"


def test_rewinding_takes_the_whole_tool_using_turn_with_it():
    # The invariant this function exists for. Truncating at the last
    # role=="user" message would stop at the tool_result and leave the
    # assistant's tool_use above it unanswered — a 400 on the next call.
    s = _session_with([
        _user("first"), _assistant("first answer"),
        _user("size the battery"),
        _assistant_tool_use("tu-1"), _tool_result("tu-1"),
        _assistant_tool_use("tu-2"), _tool_result("tu-2"),
        _assistant("done"),
    ])

    chat_service.rewind_session(s, turns=1)

    assert [m["role"] for m in s.messages] == ["user", "assistant"]
    assert not any(
        chat_service._message_is_tool_results(m) for m in s.messages
    )


def test_rewinding_several_turns():
    s = _session_with([
        _user("a"), _assistant("A"),
        _user("b"), _assistant("B"),
        _user("c"), _assistant("C"),
    ])

    chat_service.rewind_session(s, turns=2)

    assert len(s.messages) == 2
    assert s.messages[0]["content"][0]["text"] == "a"


def test_rewinding_past_the_start_empties_rather_than_raising():
    s = _session_with([_user("only"), _assistant("answer")])
    assert chat_service.rewind_session(s, turns=9) == 2
    assert len(s.messages) == 0


def test_rewinding_nothing_is_a_no_op():
    s = _session_with([_user("a"), _assistant("A")])
    for n in (0, -1):
        assert chat_service.rewind_session(s, turns=n) == 0
        assert len(s.messages) == 2


def test_the_turn_summary_is_not_a_turn():
    # A11's synthetic summary is a role=="user" text message standing in for
    # many dropped turns. Rewinding into it would delete the only remaining
    # record of everything it summarises — a retry silently costing the user
    # the earlier half of their conversation.
    # String content, not a block list — that is the shape
    # `trim_session_messages` actually appends, and `is_turn_summary` requires
    # it (`isinstance(content, str)`). Building it as blocks made this test
    # fail against correct code.
    summary = {
        "role": "user",
        "content": chat_service.TURN_SUMMARY_PREFIX + " 4 earlier turns",
    }
    s = _session_with([summary, _user("recent"), _assistant("answer")])

    chat_service.rewind_session(s, turns=2)

    assert len(s.messages) == 1
    assert chat_service.is_turn_summary(s.messages[0])


def test_a_turn_in_flight_refuses_the_rewind():
    # `_run_turn_body` appends to this deque as the turn proceeds. Truncating
    # it underneath a live turn races that writer and can strand a tool_use.
    # Refusing is the only safe answer; the caller retries after turn_done.
    s = _session_with([_user("a"), _assistant("A")])
    s._turn_in_flight = True

    assert chat_service.rewind_session(s, turns=1) == 0
    assert len(s.messages) == 2
