"""A6 — pairing-aware session message history trim."""
from __future__ import annotations

import collections

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
