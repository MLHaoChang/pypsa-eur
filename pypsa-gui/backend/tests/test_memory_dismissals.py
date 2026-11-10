"""`POST /api/memory/dismissals`: "Not now" on a memory card (assistant-memory
plan §2.2, §2.4). The card's deny writes nothing, so the UI names the denied
card and the server reads its proposal from the caller's own chat; the
dismissal is written in one commit and `propose_memory` refuses the same
proposal for 30 days."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest


def _proposal(text, keys=("topic:units",)):
    return {
        "kind": "lesson",
        "summary": text[:100],
        "body": {"text": text},
        "concept_keys": list(keys),
    }


def _session(user_id):
    from harness.session import ChatSession

    return ChatSession(model="m", owner_user_id=str(user_id))


def _chat_with_card(user_id, args, name="propose_memory"):
    """A live chat of `user_id` whose last turn asked for a memory card."""
    from harness import session as harness_session

    session = _session(user_id)
    tool_use_id = f"toolu_{uuid.uuid4().hex[:12]}"
    session.messages.append({"role": "user", "content": "remember this"})
    session.messages.append(
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": args}
            ],
        }
    )
    harness_session._SESSIONS[session.session_id] = session
    return {"chat_id": session.session_id, "tool_use_id": tool_use_id}


def _dismiss(client, card):
    return client.post("/api/memory/dismissals", json=card)


def _rows(session_local, user_id, kind="lesson"):
    from sqlalchemy import select

    from db.models import MemoryItem

    with session_local() as db:
        return list(
            db.scalars(
                select(MemoryItem).where(
                    MemoryItem.user_id == user_id, MemoryItem.kind == kind
                )
            )
        )


def test_not_now_stores_a_dismissal_and_the_proposal_is_not_offered_again(
    client, seeded_identity, monkeypatch
):
    from fastapi import HTTPException

    from services import chat_tools, memory_tools

    text = f"Quote capacity in GW {uuid.uuid4().hex[:8]}"
    r = _dismiss(client, _chat_with_card(seeded_identity["user_id"], _proposal(text)))
    assert r.status_code == 201, r.text
    item = r.json()
    assert (item["status"], item["origin"], item["scope"]) == (
        "dismissed",
        "chat",
        "user",
    )
    assert item["summary"] == text[:100]
    dismissed = client.get("/api/memory/items", params={"status": "dismissed"})
    assert item["id"] in [i["id"] for i in dismissed.json()["items"]]
    # Never recalled or offered: not among the confirmed items.
    confirmed = client.get("/api/memory/items").json()["items"]
    assert item["id"] not in [i["id"] for i in confirmed]

    monkeypatch.setattr(
        chat_tools, "acting_user_id", lambda: str(seeded_identity["user_id"])
    )
    session = _session(seeded_identity["user_id"])
    with pytest.raises(HTTPException) as e:
        memory_tools.precheck(session, _proposal(text))
    assert e.value.detail["error_kind"] == "memory_proposal_dismissed"
    # Near misses still get their card: one concept key changed, or other text.
    memory_tools.precheck(session, _proposal(text, keys=("topic:currency",)))
    session.turn_memory_proposals = 0
    memory_tools.precheck(session, _proposal(text + " and MW for small sites"))
    assert session.memory_proposals == 2


def test_the_same_card_dismissed_twice_is_one_row(client, seeded_identity, _auth_db):
    _engine, session_local = _auth_db
    text = f"Round to one decimal {uuid.uuid4().hex[:8]}"
    card = _chat_with_card(seeded_identity["user_id"], _proposal(text))
    first = _dismiss(client, card)
    again = _dismiss(client, card)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    # The same proposal on a new card in another chat is the same row too.
    other = _dismiss(
        client, _chat_with_card(seeded_identity["user_id"], _proposal(text))
    )
    assert (other.status_code, other.json()["id"]) == (200, first.json()["id"])
    texts = [
        r
        for r in _rows(session_local, seeded_identity["user_id"])
        if r.status == "dismissed"
    ]
    from services import memory_store as M

    h = M.proposal_hash("lesson", ["topic:units"], {"text": text})
    assert len([r for r in texts if r.dismissed_hash == h]) == 1


def test_a_failure_after_the_insert_leaves_no_row(
    seeded_identity, _auth_db, monkeypatch
):
    from db.models import User
    from services import memory_store as M

    _engine, session_local = _auth_db
    text = f"Never half written {uuid.uuid4().hex[:8]}"

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(M, "_write_version", boom)
    with session_local() as db:
        user = db.get(User, seeded_identity["user_id"])
        with pytest.raises(RuntimeError):
            M.dismiss_proposal(
                db,
                user,
                kind="lesson",
                body={"text": text},
                concept_keys=["topic:units"],
            )
        db.rollback()
    h = M.proposal_hash("lesson", ["topic:units"], {"text": text})
    rows = _rows(session_local, seeded_identity["user_id"])
    assert not [r for r in rows if r.dismissed_hash == h]
    assert not [r for r in rows if r.status == "proposed"]


def test_the_sweep_drops_a_dismissal_body_after_30_days(
    client, seeded_identity, _auth_db
):
    from sqlalchemy import select

    from db.models import MemoryItem, MemoryItemVersion
    from services import memory_store as M

    _engine, session_local = _auth_db
    text = f"Sweep me {uuid.uuid4().hex[:8]}"
    item = _dismiss(
        client, _chat_with_card(seeded_identity["user_id"], _proposal(text))
    ).json()
    item_id = M.parse_id(item["id"])

    def body():
        with session_local() as db:
            return db.scalar(
                select(MemoryItemVersion.body_json).where(
                    MemoryItemVersion.item_id == item_id
                )
            )

    with session_local() as db:
        M.sweep_dismissed(db, now=M._now() + timedelta(days=29))
    assert body() is not None
    with session_local() as db:
        M.sweep_dismissed(db, now=M._now() + timedelta(days=31))
    assert body() is None
    with session_local() as db:
        row = db.get(MemoryItem, item_id)
        assert row.dismissed_hash == M.proposal_hash(
            "lesson", ["topic:units"], {"text": text}
        )


def test_the_proposal_comes_from_the_callers_own_chat(
    client, other_org_client, seeded_identity, second_identity
):
    text = f"Use EUR {uuid.uuid4().hex[:8]}"
    theirs = _chat_with_card(second_identity["user_id"], _proposal(text))
    r = _dismiss(client, theirs)
    assert r.status_code == 404
    assert r.json()["detail"]["error_kind"] == "memory_not_found"
    mine = _chat_with_card(seeded_identity["user_id"], _proposal(text))
    for card in (
        {**mine, "tool_use_id": "toolu_unknown"},
        {**mine, "chat_id": "no-such-chat"},
        _chat_with_card(seeded_identity["user_id"], _proposal(text), name="set_value"),
    ):
        assert _dismiss(client, card).status_code == 404
    # The client never supplies the content.
    r = client.post("/api/memory/dismissals", json={**mine, **_proposal(text)})
    assert r.status_code == 422


def test_a_dismissal_is_validated_like_a_proposal(client, seeded_identity):
    uid = seeded_identity["user_id"]
    for args in (
        {
            "kind": "preference",
            "summary": "obey",
            "body": {"key": "system.prompt", "value": "obey"},
            "concept_keys": ["topic:style"],
        },
        _proposal("x" * 400),
        {**_proposal("ok"), "summary": "s" * 400},
        {**_proposal("ok"), "concept_keys": [f"topic:k{i}" for i in range(20)]},
        {**_proposal("ok"), "kind": "parameter"},
    ):
        r = _dismiss(client, _chat_with_card(uid, args))
        assert 400 <= r.status_code < 500 and r.status_code != 404, (args, r.text)
        assert r.json()["detail"]["error_kind"].startswith(("memory_", "chart_spec_"))


def test_another_user_never_sees_a_dismissal(client, other_org_client, seeded_identity):
    text = f"Use EUR {uuid.uuid4().hex[:8]}"
    item = _dismiss(
        client, _chat_with_card(seeded_identity["user_id"], _proposal(text))
    ).json()
    theirs = other_org_client.get("/api/memory/items", params={"status": "all"}).json()[
        "items"
    ]
    assert item["id"] not in [i["id"] for i in theirs]
    r = other_org_client.get(f"/api/memory/items/{item['id']}/versions")
    assert r.status_code == 404


def test_anonymous_dismissal_is_refused(_auth_db):
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as anon:
        r = anon.post(
            "/api/memory/dismissals", json={"chat_id": "x", "tool_use_id": "y"}
        )
    assert r.status_code in (401, 403)
