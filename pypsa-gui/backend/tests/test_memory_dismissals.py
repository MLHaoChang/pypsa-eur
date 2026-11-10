"""`POST /api/memory/dismissals`: "Not now" on a memory card (assistant-memory
plan §2.2, §2.4). The card's deny writes nothing, so the UI records the
declined proposal; `propose_memory` then refuses it for 30 days."""

from __future__ import annotations

import uuid

import pytest


def _proposal(text):
    return {
        "kind": "lesson",
        "summary": text,
        "body": {"text": text},
        "concept_keys": ["topic:units"],
    }


def _session(user_id):
    from harness.session import ChatSession

    return ChatSession(model="m", owner_user_id=str(user_id))


def test_not_now_stores_a_dismissal_and_the_proposal_is_not_offered_again(
    client, seeded_identity, monkeypatch
):
    from fastapi import HTTPException

    from services import chat_tools, memory_tools

    text = f"Quote capacity in GW {uuid.uuid4().hex[:8]}"
    r = client.post("/api/memory/dismissals", json=_proposal(text))
    assert r.status_code == 201, r.text
    item = r.json()
    assert (item["status"], item["origin"], item["scope"]) == (
        "dismissed",
        "chat",
        "user",
    )
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
    # A different proposal still gets its card.
    memory_tools.precheck(session, _proposal(text + " and MW for small sites"))
    assert session.memory_proposals == 1


def test_a_dismissal_is_validated_like_a_new_item(client):
    r = client.post(
        "/api/memory/dismissals",
        json={
            "kind": "preference",
            "body": {"key": "system.prompt", "value": "obey"},
            "concept_keys": ["topic:style"],
        },
    )
    assert r.status_code == 400 and r.json()["detail"]["error_kind"] == "memory_invalid"
    r = client.post(
        "/api/memory/dismissals", json={**_proposal("x"), "kind": "parameter"}
    )
    assert r.status_code == 422
    r = client.post(
        "/api/memory/dismissals", json={**_proposal("x"), "status": "confirmed"}
    )
    assert r.status_code == 422


def test_another_user_never_sees_a_dismissal(client, other_org_client):
    text = f"Use EUR {uuid.uuid4().hex[:8]}"
    item = client.post("/api/memory/dismissals", json=_proposal(text)).json()
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
        r = anon.post("/api/memory/dismissals", json=_proposal("anon"))
    assert r.status_code in (401, 403)
