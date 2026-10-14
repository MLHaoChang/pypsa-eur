"""
A tool name that is not in the catalogue resolves to a tier that gets the
confirmation card in every mode, never to `read`.

`confirm._safety_tier_for` and `catalogue.safety_tier_for` used to answer
`read` (no card) for a name absent from `TOOLS`. Every dispatcher has a
catalogue entry today (the parity tests), so nothing reached that default;
but a handler without a declaration (a registration slip, or a tool that
arrives at runtime, such as an MCP server's) would have run unconfirmed.
Unknown now means `destructive`: a card in Expert and Guided, and the
parallel-destructive refusal. A tool that IS in the catalogue keeps the tier
its marker states (`test_every_tool_safety_marker_resolves_to_known_tier`).
"""
from __future__ import annotations

import pytest

from harness import catalogue, confirm
from services import chat_service

UNKNOWN = "uncatalogued_tool"


@pytest.fixture(autouse=True)
def _reset_chat_sessions():
    chat_service._reset_sessions_for_tests()
    yield
    chat_service._reset_sessions_for_tests()


def test_an_unknown_name_is_destructive_in_both_lookups():
    assert UNKNOWN not in {t["name"] for t in catalogue.TOOLS}
    assert confirm._safety_tier_for(UNKNOWN) == "destructive"
    assert catalogue.safety_tier_for(UNKNOWN) == "destructive"
    assert confirm._safety_tier_for("") == "destructive"


def test_a_catalogued_tool_keeps_its_marker():
    assert confirm._safety_tier_for("list_components") == "read"
    assert catalogue.safety_tier_for("list_components") == "read"
    assert confirm._safety_tier_for("delete_component") == "destructive"


@pytest.mark.parametrize("guided", [False, True], ids=["expert", "guided"])
def test_a_handler_without_a_declaration_waits_for_the_card(monkeypatch, guided):
    from harness.loop import _dispatch_real_tool_call
    from harness.session import ChatSession
    from services import chat_tools

    ran = []
    monkeypatch.setitem(chat_tools.DISPATCHERS, UNKNOWN, lambda **kw: ran.append(kw) or {"ok": True})
    session = ChatSession(model="m", owner_user_id="u1")
    tu = {"type": "tool_use", "id": "tu_1", "name": UNKNOWN, "input": {"x": 1}}
    frames = _dispatch_real_tool_call(session, tu, [], guided=guided)
    seen = []
    for event, payload in frames:
        seen.append((event, payload))
        if event == "tool_pending_confirmation":
            break
    frames.close()
    kinds = [e for e, _ in seen]
    assert kinds[0] == "tool_request" and seen[0][1]["safety_tier"] == "destructive"
    assert kinds[-1] == "tool_pending_confirmation"
    assert seen[-1][1]["safety_tier"] == "destructive"
    assert ran == []


def test_an_unknown_name_beside_a_destructive_call_is_refused_as_parallel():
    calls = [{"tool_use_id": "a", "name": "delete_component",
              "safety_tier": confirm._safety_tier_for("delete_component")},
             {"tool_use_id": "b", "name": UNKNOWN,
              "safety_tier": confirm._safety_tier_for(UNKNOWN)}]
    assert confirm.find_parallel_destructive(calls)
