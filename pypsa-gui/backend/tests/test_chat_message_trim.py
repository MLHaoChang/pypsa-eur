"""A6 — pairing-aware session message history trim."""
from __future__ import annotations

import collections
import copy

from services import chat_service


def _user(text: str = "hi") -> dict:
    return {"role": "user", "content": text}


def _assistant_with_tool(tool_id: str = "t1", name: str = "get_meta") -> dict:
    return {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "calling"},
            {"type": "tool_use", "id": tool_id, "name": name, "input": {}},
        ],
    }


def _tool_results(tool_id: str = "t1") -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": "{}",
            }
        ],
    }


def _tool_use_ids(messages) -> set[str]:
    ids: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                ids.add(block["id"])
    return ids


def _tool_result_ids(messages) -> set[str]:
    ids: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                ids.add(block["tool_use_id"])
    return ids


def test_trim_does_not_orphan_tool_use_without_result():
    """Positional maxlen drop would orphan; pairing-aware trim must not."""
    dq = collections.deque()
    # Build many complete tool-using turns, then trim hard.
    for i in range(50):
        dq.append(_user(f"u{i}"))
        dq.append(_assistant_with_tool(f"tool-{i}"))
        dq.append(_tool_results(f"tool-{i}"))
    assert len(dq) == 150
    chat_service.trim_session_messages(dq, max_len=20)
    assert len(dq) <= 20
    uses = _tool_use_ids(dq)
    results = _tool_result_ids(dq)
    assert uses <= results, f"orphaned tool_use ids: {uses - results}"


def test_append_history_message_trims_over_cap(monkeypatch):
    monkeypatch.setattr(chat_service, "SESSION_MESSAGES_MAX", 6)
    sess = chat_service.ChatSession()
    for i in range(10):
        sess.append_history_message(_user(f"u{i}"))
        sess.append_history_message(_assistant_with_tool(f"t{i}"))
        sess.append_history_message(_tool_results(f"t{i}"))
    assert len(sess.messages) <= 6
    uses = _tool_use_ids(sess.messages)
    results = _tool_result_ids(sess.messages)
    assert uses <= results


def test_deque_has_no_silent_maxlen():
    sess = chat_service.ChatSession()
    assert sess.messages.maxlen is None


# ── Improvement #18 — history cache breakpoint ──────────────────────────────


def _cc(block) -> bool:
    return isinstance(block, dict) and "cache_control" in block


def _count_breakpoints(messages) -> int:
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            n += sum(1 for b in content if _cc(b))
    return n


def test_no_breakpoint_on_the_first_turn():
    """No completed history means no stable prefix worth a cache write."""
    msgs = [_user("first ever")]
    assert chat_service._with_history_cache_breakpoint(msgs, None) is msgs


def test_string_content_is_promoted_to_a_marked_text_block():
    msgs = [_user("earlier turn"), _user("current")]
    out = chat_service._with_history_cache_breakpoint(msgs, 0)

    assert out[0]["content"] == [{
        "type": "text",
        "text": "earlier turn",
        "cache_control": {"type": "ephemeral"},
    }]
    # The current turn is deliberately NOT marked.
    assert out[1]["content"] == "current"


def test_block_list_content_marks_only_the_last_block():
    msgs = [_assistant_with_tool("t1"), _user("current")]
    out = chat_service._with_history_cache_breakpoint(msgs, 0)

    blocks = out[0]["content"]
    assert not _cc(blocks[0]), "only the LAST block carries the breakpoint"
    assert _cc(blocks[-1])


def test_the_input_is_never_mutated():
    """
    Load-bearing: the retry path rebuilds the payload from the same `messages`
    and must produce byte-identical output. In-place marking would compound a
    breakpoint per retry and drift the cached prefix.
    """
    original = [_user("earlier"), _user("current")]
    snapshot = copy.deepcopy(original)

    chat_service._with_history_cache_breakpoint(original, 0)
    chat_service._with_history_cache_breakpoint(original, 0)

    assert original == snapshot, "helper mutated its input"


def test_anchor_stays_on_history_while_the_tool_loop_appends():
    """
    The agentic loop appends assistant/tool_result messages between API calls.
    The breakpoint must stay pinned to the completed history, not drift onto
    the moving tail — otherwise every iteration writes a fresh cache entry.
    """
    msgs = [_user("earlier"), _user("current")]
    anchor = 0

    # Simulate two loop iterations appending to the same list.
    msgs.append(_assistant_with_tool("t1"))
    msgs.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
    ]})

    out = chat_service._with_history_cache_breakpoint(msgs, anchor)

    assert _cc(out[0]["content"][-1]), "history lost its breakpoint"
    assert _count_breakpoints(out) == 1, (
        "the tool-loop tail must not pick up extra breakpoints — each one is a "
        "cache write at 1.25x"
    )


def test_out_of_range_anchor_is_a_no_op():
    msgs = [_user("only")]
    assert chat_service._with_history_cache_breakpoint(msgs, 5) is msgs
    assert chat_service._with_history_cache_breakpoint(msgs, -1) is msgs
