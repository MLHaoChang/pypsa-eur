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


def test_controls_survive_more_explicit_requests_than_the_provider_limit():
    selected = wiring._bounded_tools(TOOLS, " ".join(t["name"] for t in TOOLS))
    assert len(selected) == 128
    assert wiring._HARNESS_TOOL_NAMES <= {t["name"] for t in selected}


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


@pytest.fixture
def discovery_session():
    from harness.session import ChatSession
    from services import chat_tools
    previous = chat_tools.chat_session()
    session = ChatSession()
    chat_tools.set_chat_session(session)
    try:
        yield session
    finally:
        chat_tools.set_chat_session(previous)


def test_discovered_tools_outrank_generic_matches(discovery_session):
    initial = {t['name'] for t in wiring._bounded_tools(TOOLS, 'hello')}
    name = next(t['name'] for t in TOOLS if t['name'] not in initial)
    discovery_session.discovered_tools = (name,)
    selected = wiring._bounded_tools(TOOLS, 'hello')
    names = {t['name'] for t in selected}
    assert name in names
    assert len(selected) == 128
    assert wiring._HARNESS_TOOL_NAMES <= names
    assert selected == [t for t in TOOLS if t['name'] in names]


def test_discovery_replaces_not_accumulates(discovery_session):
    from services import chat_tools
    first = chat_tools.find_capabilities('create_component')
    assert discovery_session.discovered_tools == tuple(t['name'] for t in first['items'])
    second = chat_tools.find_capabilities('get_results', limit=1)
    expected = tuple(t['name'] for t in second['items'])
    assert expected and expected != tuple(t['name'] for t in first['items'])
    assert discovery_session.discovered_tools == expected
    chat_tools.find_capabilities(overview=True)
    assert discovery_session.discovered_tools == expected


def test_a_discovered_tool_is_callable_in_the_same_turn(monkeypatch, discovery_session):
    from harness.catalogue import safety_tier_for
    from harness.providers.fake import FakeProvider
    from services import chat_service, chat_tools, llm_config
    official = profile()
    llm_config.save_profiles([official], official.id)
    discovery_session.model = official.model
    discovery_session.profile_id, discovery_session.bound_wire = official.id, official.wire
    eligible = wiring._tools_payload()
    initial = {t['name'] for t in wiring._bounded_tools(eligible, 'Hello')}
    tool = next(t for t in eligible if t['name'] not in initial
                and safety_tier_for(t['name']) == 'read' and not t['input_schema']['required'])
    name = tool['name']
    called = []
    monkeypatch.setitem(chat_tools.DISPATCHERS, name, lambda **args: called.append(args) or {'ok': True})
    fake = FakeProvider([
        {'blocks': [{'type': 'tool_use', 'id': 'discover', 'name': 'find_capabilities',
                     'input': {'goal': name, 'limit': 1}}]},
        {'blocks': [{'type': 'tool_use', 'id': 'use-found-tool', 'name': name, 'input': {}}]},
        {'blocks': [{'type': 'text', 'text': 'Done.'}]},
    ])
    frames = list(chat_service.run_turn(discovery_session, 'Hello', provider=fake))
    assert name not in {t['name'] for t in fake.requests[0].tools}
    assert not any(e == 'tool_error' and d.get('error_kind') == 'tool_not_offered' for e, d in frames)
    assert called == [{}]
    assert name in {t['name'] for t in fake.requests[1].tools}


def test_discovery_never_bypasses_the_toolset(discovery_session):
    discovery_session.toolset = 'simulation'
    discovery_session.discovered_tools = ('create_component',)
    assert 'create_component' not in {t['name'] for t in wiring._tools_payload_for_profile(profile())}


PLAUSIBILITY_TOOLS = {"project_readiness", "validate_network", "diagnose_network"}


def _padded_catalogue(count=120):
    """TOOLS plus distractors whose names and descriptions repeat the VS2 query words.

    Half go before the catalogue, so the core tools' positions move too; 120
    leaves margin over the ~50 tools the LV08/LC-16 chain adds."""
    words = "Do these results make sense? Make sense of these results; results that make sense."
    distractors = [{"name": f"make_sense_of_these_results_{i}", "description": words,
                    "input_schema": {"type": "object", "properties": {}}} for i in range(count)]
    return distractors[:count // 2] + list(TOOLS) + distractors[count // 2:]


@pytest.mark.parametrize("query", [
    "Do these results make sense?",
    "Are these results plausible?",
    "PLAUSIBILITY, please: are the results realistic",
    "Sanity check the results",
])
def test_result_plausibility_queries_keep_the_input_checks(query):
    tools = _padded_catalogue()
    selected = wiring._bounded_tools(tools, query)
    names = {t["name"] for t in selected}
    assert PLAUSIBILITY_TOOLS <= names
    assert len(selected) <= 128
    assert [t for t in tools if t in selected] == selected
    assert wiring._HARNESS_TOOL_NAMES <= names


def test_unrelated_words_do_not_trigger_an_intent():
    tools = _padded_catalogue()
    names = {t["name"] for t in wiring._bounded_tools(tools, "What does this battery make?")}
    assert not (PLAUSIBILITY_TOOLS - wiring._HARNESS_TOOL_NAMES) & names


def test_intent_map_names_existing_read_tools():
    from harness.catalogue import safety_tier_for

    known = {t["name"] for t in TOOLS}
    for _, names in wiring._SELECTION_INTENTS:
        for name in names:
            assert name in known
            assert safety_tier_for(name) == "read"
