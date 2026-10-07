"""
`POST /api/chat/stream` must not run a turn inside someone else's session.

`session_owner_allows` was added for `/confirm`, `/rewind` and `/abort`, and
its docstring says why: `_SESSIONS` is a process global, those routes
"authenticated ... and authorized nothing", and "any signed-in caller who knew
a session id could truncate a stranger's conversation, kill their in-flight
turn, or supply the approval for their destructive tool. Verified cross-ORG."

`/stream` was not one of the three. It resolves the caller-supplied
`session_id` through `get_or_create_session`, which sets the owner on CREATE
ONLY and never reassigns it — so an EXISTING session belonging to someone else
is returned as-is and the turn runs inside it. The consequences are worse than
for the three that were fixed:

  * the outbound message array is seeded from that session's history, so the
    stranger's conversation — and the tool results in it — is sent to the
    provider on behalf of the caller, and can be elicited in the reply;
  * the caller's own message and the turn's blocks are appended to the
    stranger's thread;
  * the tools run with the CALLER's authority against whatever project that
    session is bound to.

And the id is not a secret: `/confirm`'s own comment records that "GET
/history hands `last_session_id` to any co-member who activates the project".

Refusal rather than silently minting a different session for the caller: the
client uses the id it sent for `/abort` and `/confirm` (the handler docstring
says so), so handing back a different one would leave those pointing at a
session that is not running the turn.
"""
from __future__ import annotations

import json

import pytest

from services import chat_service

ALICE_SESSION = "sess-owned-by-alice"


@pytest.fixture
def fake_provider(monkeypatch):
    """Scripted provider, so a permitted turn actually completes."""
    from services.llm_fake import FakeProvider

    state: dict = {}

    def _fake(profile, client=None):
        return state["provider"], None

    monkeypatch.setattr(chat_service, "_provider_for_profile", _fake)

    def _script(turns):
        state["provider"] = FakeProvider(turns)
        return state["provider"]

    return _script


def _ok_turn(text="ok"):
    from services.llm_provider import LLMEvent

    return {"events": [LLMEvent(type="text_delta", text=text)],
            "blocks": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.fixture
def alices_session(seeded_identity):
    sess = chat_service.get_or_create_session(
        ALICE_SESSION, owner_user_id=str(seeded_identity["user_id"]),
    )
    sess.messages.clear()
    sess.append_history_message({"role": "user", "content": "alice's private question"})
    try:
        yield sess
    finally:
        chat_service.drop_session(ALICE_SESSION)


def test_a_stranger_cannot_stream_into_that_session(
    other_org_client, alices_session, fake_provider,
):
    fake_provider([_ok_turn("should never run")])
    before = list(alices_session.messages)

    r = other_org_client.post("/api/chat/stream", json={
        "session_id": ALICE_SESSION, "message": "whose conversation is this?",
    })

    assert r.status_code == 403, (
        f"a stranger ran a turn in another user's session: {r.status_code} "
        f"{r.text[:300]}"
    )
    assert r.json()["detail"]["error_kind"] == "session_not_yours"
    assert list(alices_session.messages) == before, (
        "the stranger's message was appended to the owner's thread"
    )


def test_the_owner_can_still_stream_on_their_own_session(
    client, alices_session, fake_provider,
):
    """The control. The check must refuse strangers, not owners."""
    fake_provider([_ok_turn("hello back")])
    r = client.post("/api/chat/stream", json={
        "session_id": ALICE_SESSION, "message": "hello",
    })
    assert r.status_code == 200, r.text


def test_an_unknown_session_id_is_created_for_the_caller(
    other_org_client, fake_provider,
):
    """
    The second control, and the reason the check is scoped to sessions that
    ALREADY EXIST: `/stream` legitimately mints a session for a caller-chosen
    id, and refusing that would break every first turn.
    """
    fake_provider([_ok_turn("brand new")])
    r = other_org_client.post("/api/chat/stream", json={
        "session_id": "sess-brand-new", "message": "hi",
    })
    assert r.status_code == 200, r.text
    sess = chat_service.get_session("sess-brand-new")
    assert sess is not None and sess.owner_user_id is not None
    chat_service.drop_session("sess-brand-new")
