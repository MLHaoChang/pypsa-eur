"""`GET /api/chat/workflows` — the start menu, served from the harness
(chat harness issue 03)."""
from __future__ import annotations

import pytest


def _ids(payload: dict) -> list[str]:
    return [w["id"] for w in payload["workflows"]]


def test_unbound_menu_offers_open_project_only(client):
    r = client.get("/api/chat/workflows", params={"context": "unbound"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["context"] == "unbound"
    assert _ids(body) == ["open-project"]
    wf = body["workflows"][0]
    assert set(wf) == {"id", "title", "intent", "opening_request", "steps"}
    assert wf["opening_request"].startswith("I want to open or create a project")


@pytest.mark.parametrize("context,present,absent", [
    ("expert", ["hub-design", "build-network", "explain-results"], ["open-project", "investment-decision"]),
    ("guided", ["hub-design", "explain-results"], ["open-project", "build-network", "investment-decision"]),
])
def test_bound_menus_follow_the_registry(client, context, present, absent):
    r = client.get("/api/chat/workflows", params={"context": context})
    assert r.status_code == 200, r.text
    ids = _ids(r.json())
    for p in present:
        assert p in ids
    for a in absent:
        assert a not in ids
    # Menu order is the registry's `order`, hub-design first in both modes.
    assert ids[0] == "hub-design"


def test_default_context_is_unbound(client):
    r = client.get("/api/chat/workflows")
    assert r.status_code == 200
    assert _ids(r.json()) == ["open-project"]


def test_unknown_context_is_a_422(client):
    r = client.get("/api/chat/workflows", params={"context": "admin"})
    assert r.status_code == 422


def test_the_menu_matches_the_registry_exactly(client):
    from harness import workflows

    for ctx in ("unbound", "expert", "guided"):
        r = client.get("/api/chat/workflows", params={"context": ctx})
        assert _ids(r.json()) == [wf.id for wf in workflows.menu(ctx)]


def test_the_menu_needs_no_llm_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.get("/api/chat/workflows", params={"context": "expert"})
    assert r.status_code == 200 and r.json()["workflows"]
