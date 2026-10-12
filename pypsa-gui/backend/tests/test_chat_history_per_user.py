"""
CH-6, the `/history` half — each user gets back THEIR chat session.

`chat.jsonl` is per PROJECT and shared by everyone who works on it; a chat
session is per USER. `GET /api/chat/history` handed back the session of the
LAST turn in the file, whoever ran it, and minted it owned by whoever asked
first. So after Bob spoke, Alice's reload adopted Bob's thread, and the
`/stream` ownership check (the other half of CH-6) then refused her with
`session_not_yours` — or, had Alice asked first, Bob was locked out of his own.

Turns now record their author (`owner_user_id`), and `/history` returns the
caller's own last session. The register left this open on the grounds that
every repair was worse than the disease: reassigning ownership reopens the
hole, minting owner-less trips the fail-closed rule, not minting breaks
rehydration. All three were answers to "whose is THIS session"; the fix is to
stop asking that of a session that was never the caller's.
"""
from __future__ import annotations

import json
import types

import pypsa
import pytest

import local_mode
from routers import chat as chat_router
from services import chat_service
from services.pypsa_service import PyPSAService
from tests.test_chat_e2e import (
    FakeAnthropicClient, _FakeFinalMessage, _FakeUsage, _text_block, _text_event,
)

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


def _actor(user_id: str):
    return types.SimpleNamespace(id=user_id)


def _turn(session_id: str, text: str, author: str | None) -> dict:
    rec = {"ts": 1.0, "session_id": session_id, "model": chat_service.DEFAULT_MODEL,
           "user": text, "assistant": [{"type": "text", "text": "ok"}]}
    if author is not None:
        rec[chat_service.TURN_AUTHOR_KEY] = author
    return rec


@pytest.fixture
def project(tmp_projects_dir, install_network):
    n = pypsa.Network()
    n.add("Bus", "B1")
    install_network(n, name="Shared")
    chat_service._reset_sessions_for_tests()
    yield PyPSAService.get_active_context()
    chat_service._reset_sessions_for_tests()


@pytest.fixture
def server_mode(monkeypatch):
    monkeypatch.setattr(local_mode, "is_local_mode", lambda: False)


def _history(user_id: str | None) -> dict:
    return chat_router.chat_history(
        limit=200, actor=_actor(user_id) if user_id else None)


def test_each_user_gets_their_own_session(project, server_mode):
    chat_service.append_turn(project, _turn("s-alice", "alice asks", ALICE))
    chat_service.append_turn(project, _turn("s-bob", "bob asks", BOB))

    assert _history(ALICE)["last_session_id"] == "s-alice", (
        "Alice was handed the session of the last turn in the shared file — "
        "Bob's — and /stream would then refuse her as session_not_yours"
    )
    assert _history(BOB)["last_session_id"] == "s-bob"
    # And each was minted owned by the right person.
    assert chat_service.get_session("s-alice").owner_user_id == ALICE
    assert chat_service.get_session("s-bob").owner_user_id == BOB


def test_a_user_with_no_turns_gets_no_session(project, server_mode):
    chat_service.append_turn(project, _turn("s-bob", "bob asks", BOB))

    h = _history(ALICE)
    assert h["last_session_id"] is None
    assert len(h["turns"]) == 1, "the shared transcript itself still renders"
    assert chat_service.get_session("s-bob") is None, (
        "Alice's GET minted Bob's session — owned by Alice"
    )


def test_a_legacy_record_keeps_its_old_behaviour(project, server_mode):
    """No author key: the record predates it, and is handed back as before.
    Calling it nobody's would cost every server user their session continuity
    once on upgrade; what it would prevent is bounded by the next test and by
    `/stream`'s own ownership check."""
    chat_service.append_turn(project, _turn("s-old", "before the key", None))

    assert _history(ALICE)["last_session_id"] == "s-old"


def test_a_colleagues_authored_turn_outranks_no_legacy_one(project, server_mode):
    """Mixed file: Alice's legacy turn, then Bob's authored one. Alice gets
    the legacy session, never Bob's, although Bob's is the last."""
    chat_service.append_turn(project, _turn("s-old", "before the key", None))
    chat_service.append_turn(project, _turn("s-bob", "bob asks", BOB))

    assert _history(ALICE)["last_session_id"] == "s-old"


def test_a_session_registered_to_someone_else_is_neither_returned_nor_rebuilt(
    project, server_mode,
):
    """Defence in depth: the transcript says Alice, the registry says Bob (a
    forged or legacy record). Handing it back would get Alice a 403; and
    rebuilding it would overwrite Bob's live session from Alice's GET."""
    bobs, _ = chat_service.get_or_create_session_reporting(
        "s-x", owner_user_id=BOB)
    bobs.append_history_message({"role": "user", "content": "bob's live context"})
    chat_service.append_turn(project, _turn("s-x", "alice, supposedly", ALICE))

    assert _history(ALICE)["last_session_id"] is None
    assert [m["content"] for m in bobs.messages] == ["bob's live context"]


def test_the_author_does_not_reach_the_wire(project, server_mode):
    chat_service.append_turn(project, _turn("s-bob", "bob asks", BOB))

    for rec in _history(ALICE)["turns"]:
        assert chat_service.TURN_AUTHOR_KEY not in rec


def test_a_turn_records_its_author(project):
    """The write side — without it, every turn would read as legacy."""
    session = chat_service.ChatSession(owner_user_id=ALICE)
    client = FakeAnthropicClient([
        ([_text_event("hi")],
         _FakeFinalMessage(content=[_text_block("hi")], usage=_FakeUsage())),
    ])
    list(chat_service.run_turn(session, "hello", client=client))

    path = chat_service.get_persist_path(project)
    recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert recs and recs[-1][chat_service.TURN_AUTHOR_KEY] == ALICE


def test_an_imported_transcript_carries_no_author(project, server_mode):
    """An author key arriving through /chat/import names a user of another
    install, or is forged. Dropped, the turn is a legacy record: the importer
    can continue it, and the live-owner check still applies."""
    body = chat_router.ImportRequest(turns=[_turn("s-imported", "x", BOB)])
    assert chat_router.chat_import(body)["imported"] == 1

    path = chat_service.get_persist_path(project)
    recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert recs and chat_service.TURN_AUTHOR_KEY not in recs[-1]
    assert _history(ALICE)["last_session_id"] == "s-imported"
