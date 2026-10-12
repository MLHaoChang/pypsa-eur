"""Every catalogue entry remains discoverable/offerable; not a handler certification."""
import pytest

from harness.catalogue import TOOLS, safety_tier_for
from harness.providers.fake import FakeProvider
from harness.toolsets import CONTROL_TOOLS
from services import chat_service, chat_tools, llm_config
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

PROFILES = [
    ('anthropic', 'https://api.anthropic.com', 'claude-sonnet-4-6'),
    ('openai', 'https://api.openai.com/v1', 'gpt-4.1-mini'),
    ('openai', 'https://compatible.example/v1', 'configured-model'),
]


@pytest.mark.parametrize('kind', ['network', 'planning_dynamics'])
@pytest.mark.parametrize('wire,url,model', PROFILES, ids=['claude', 'openai', 'compatible'])
def test_every_discovered_eligible_tool_can_be_offered_on_the_next_model_request(
    baseline, kind, wire, url, model,
):
    if kind == 'planning_dynamics':
        study = chat_tools.gridspine_create_study('Discovery study', {'hours': 24, 'k': 1, 'screen': True})
        chat_tools.activate_project(study['id'])
    profile = llm_config.LLMProfile(
        id='reachability', label='Reachability fixture', preset='custom', wire=wire,
        base_url=url, model=model, tools=True, vision=False, auth='bearer',
        fallback_model=None, max_output_tokens=128,
    )
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, wire
    for tool in TOOLS:
        chat_tools.set_chat_session(session)
        chat_tools.use_toolset('simulation')
        discovered = chat_tools.find_capabilities(f"Find capability {tool['name']}", limit=1)
        assert len(discovered['items']) == 1, tool['name']
        item = discovered['items'][0]
        assert item['name'] == tool['name']
        assert item['required'] == tool['input_schema']['required']
        assert item['safety_tier'] == safety_tier_for(tool['name'])
        eligible = (kind == 'planning_dynamics' or not tool['name'].startswith('gridspine_')
                    or tool['name'] == 'gridspine_create_study')
        assert item['eligible_for_project'] is eligible, tool['name']
        chat_tools.use_toolset('all')
        provider = FakeProvider([{
            'blocks': [{'type': 'text', 'text': 'Capability checked.'}],
            'usage': {'input_tokens': 2, 'output_tokens': 1},
        }])
        frames = list(chat_service.run_turn(
            session, f"Explain how to use {tool['name']}", provider=provider,
        ))
        assert frames[-1][0] == 'turn_done', frames
        assert not [data for event, data in frames if event in ('error', 'tool_error')]
        offered = {t['name'] for t in provider.requests[0].tools}
        assert (tool['name'] in offered) is eligible, tool['name']
        assert CONTROL_TOOLS <= offered
        if url == 'https://api.openai.com/v1':
            assert len(offered) <= 128
        assert not [event for event, _ in frames if event == 'tool_call']


def test_unbound_discovery_labels_study_tools_ineligible(client, helpers_state):
    chat_tools.set_chat_session(chat_service.ChatSession())
    result = chat_tools.find_capabilities('gridspine_run_pipeline', limit=1)
    assert result['items'][0]['name'] == 'gridspine_run_pipeline'
    assert not result['items'][0]['eligible_for_project']
