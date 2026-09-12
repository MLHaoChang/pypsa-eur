"""
R21 — the chat tool descriptions are part of the API.

`_route` reshapes nothing: `chat_tools.py`'s final statement is
`return handler(...)`, and `_truncate_result` caps size only, so the whole
enqueue payload reaches the model verbatim. The existing schema tests pin
name/signature/endpoint agreement, not response keys — so a new key arrives at
the model undeclared and nothing flags the drift. A model trusts a description
over the data, which makes a stale one worse than none.
"""
from __future__ import annotations

from services import chat_tools_schema


def _description(name: str) -> str:
    for tool in chat_tools_schema.TOOLS:
        if tool["name"] == name:
            return tool["description"]
    raise AssertionError(f"no tool named {name!r} in the schema")


def test_solve_queue_enqueue_declares_already_queued():
    text = _description("solve_queue_enqueue")
    assert "already_queued" in text, text


def test_solve_queue_enqueue_says_a_duplicate_is_not_an_error():
    text = _description("solve_queue_enqueue")
    assert "200" in text or "not an error" in text.lower(), text
