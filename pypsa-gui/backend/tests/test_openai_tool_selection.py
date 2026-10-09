"""Tool limits must preserve reachability, allowlists, and other providers."""
from dataclasses import replace

import pytest

from harness.catalogue import TOOLS
from harness.providers import wiring
from services.llm_config import LLMProfile


def profile():
    return LLMProfile(id="test", label="Test", preset="openai", wire="openai", base_url=None,
                      model="gpt-6-luna", tools=True, vision=False, auth="bearer", fallback_model=None,
                      max_output_tokens=256)


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["name"])
def test_every_tool_remains_reachable_when_requested(tool):
    selected = wiring._bounded_tools(TOOLS, "Please call " + tool["name"])
    assert len(selected) == 128
    assert tool in selected
    assert [t for t in TOOLS if t in selected] == selected
    assert wiring._HARNESS_TOOL_NAMES <= {t["name"] for t in selected}


def test_recent_tools_remain_available_for_referential_followups():
    history = [{"role": "assistant", "content": [
        {"type": "tool_use", "name": "get_investment_case"},
        {"type": "tool_use", "name": "campus_get_library"}]}]
    selected = wiring._bounded_tools(TOOLS, "Do that again", history)
    assert {"get_investment_case", "campus_get_library"} <= {t["name"] for t in selected}


def test_profile_limits_only_official_endpoint_and_never_mutates_catalogue(monkeypatch):
    monkeypatch.setattr(wiring, "_tools_payload", lambda: list(TOOLS))
    original = list(TOOLS)
    official = profile()
    assert len(wiring._tools_payload_for_profile(official)) == 128
    assert len(wiring._tools_payload_for_profile(replace(official, preset="custom", base_url="https://api.openai.com/v1"))) == 128
    assert wiring._tools_payload_for_profile(replace(official, preset="custom", base_url="http://localhost:11434/v1")) == original
    assert wiring._tools_payload_for_profile(replace(official, preset="custom", wire="anthropic", base_url="https://api.anthropic.com")) == original
    assert wiring._tools_payload_for_profile(replace(official, tools=False)) == []
    assert TOOLS == original


def test_actual_selected_set_drives_metadata_and_dispatch_allowlist(monkeypatch):
    from harness.providers.fake import FakeProvider
    from services import chat_service, chat_tools, llm_config
    official = profile()
    llm_config.save_profiles([official], official.id)
    session = chat_service.ChatSession(model=official.model)
    session.profile_id, session.bound_wire = official.id, official.wire
    called = []
    monkeypatch.setitem(chat_tools.DISPATCHERS, "solve_ppa_price", lambda **args: called.append(args))
    fake = FakeProvider([
        {"blocks": [{"type": "tool_use", "id": "unoffered", "name": "solve_ppa_price",
                     "input": {"target_irr": 0.1, "target_year": 2030}}]},
        {"blocks": [{"type": "text", "text": "Refused."}]},
    ])
    frames = list(chat_service.run_turn(session, "Hello", provider=fake))
    assert next(d for e, d in frames if e == "session_init")["tool_count"] == 128
    assert len(fake.requests[0].tools) == 128
    assert "solve_ppa_price" not in {t["name"] for t in fake.requests[0].tools}
    assert next(d for e, d in frames if e == "tool_error")["error_kind"] == "tool_not_offered"
    assert called == []
