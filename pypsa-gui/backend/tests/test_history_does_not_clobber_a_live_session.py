"""
`GET /api/chat/history` must not rebuild a session that is mid-turn.

The handler clears `sess.messages` and rebuilds it from `chat.jsonl`. Only the
three-line PROFILE adoption above it was guarded by
`session_was_freshly_minted` — and the comment explaining that guard reasons
exactly about the hazard it did not cover: "GET /history must stay read-only
w.r.t. an ALREADY-LIVE session", a GET "racing a same-wire rebind mid-turn (two
tabs sharing a session_id, or a reload)".

The message rebuild is the more destructive half. A live turn has already
appended its user message and the assistant's `tool_use` block, and is about to
append the matching `tool_result`. Clearing in between drops the `tool_use` and
leaves an ORPHAN `tool_result` in the history — and `_sanitise_history_message`
drops malformed thinking blocks, not orphan results — so every later turn of
that session is rejected by the provider.

The transcript is also the wrong source mid-turn: it holds only completed
turns, so rebuilding from it discards the in-flight one wholesale.
"""
from __future__ import annotations

import pytest

from services import chat_service


SESSION_ID = "hist-live"


def _seed_transcript(directory, session_id: str) -> None:
    """One completed turn in the project's chat.jsonl naming `session_id`.

    The handler derives `last_session_id` from the LAST record here and
    resolves that session — so without this the GET touches a different
    session entirely and the test passes while proving nothing.
    """
    import json

    path = directory / chat_service.CHAT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "session_id": session_id,
            "user": "an earlier, completed turn",
            "assistant": [{"type": "text", "text": "done"}],
        }) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def live_session(api_project, project_storage_dir):
    name = api_project("hist-proj")
    _seed_transcript(project_storage_dir(name), SESSION_ID)

    sess = chat_service.get_or_create_session(SESSION_ID)
    sess.messages.clear()
    sess.append_history_message({"role": "user", "content": "do the thing"})
    sess.append_history_message({
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "tu-1", "name": "x", "input": {}}],
    })
    with sess._lock:
        sess._turn_in_flight = True
    try:
        yield sess
    finally:
        with sess._lock:
            sess._turn_in_flight = False
        sess.messages.clear()


def test_the_fixture_really_does_reach_this_session(client, live_session):
    """Guard on the guard: if the handler stops resolving SESSION_ID, the
    assertions below would pass without exercising anything."""
    r = client.get("/api/chat/history")
    assert r.status_code == 200, r.text
    assert r.json().get("last_session_id") == SESSION_ID


def test_a_live_session_keeps_its_in_flight_messages(client, live_session):
    before = list(live_session.messages)

    r = client.get("/api/chat/history")
    assert r.status_code == 200, r.text

    assert list(live_session.messages) == before, (
        "GET /history rebuilt a session with a turn in flight; the tool_use "
        "block the running turn is about to answer was dropped, so its "
        "tool_result becomes an orphan and every later turn 400s"
    )


def test_the_tool_use_block_survives(client, live_session):
    client.get("/api/chat/history")

    blocks = [
        b for m in live_session.messages
        if isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    assert [b["id"] for b in blocks] == ["tu-1"]


def test_an_idle_session_is_still_rebuilt(client, api_project, project_storage_dir):
    """
    The control. Threading the prior conversation into the next turn is what
    the rebuild is FOR — guarding it must not disable it for the ordinary
    case, only for a session that is mid-turn.
    """
    name = api_project("hist-idle")
    _seed_transcript(project_storage_dir(name), "hist-idle-sess")

    sess = chat_service.get_or_create_session("hist-idle-sess")
    sess.messages.clear()
    with sess._lock:
        assert sess._turn_in_flight is False

    r = client.get("/api/chat/history")
    assert r.status_code == 200, r.text
    assert sess.messages, "an idle session was not rehydrated from the transcript"
