"""OJ-09: "Open project oj-grid-copy and show me its values" over real provider wires.

Exact resolution, then an ordinary activation (a confirmation in Guided mode,
the existing write-tier policy in Expert), then a native read of that project.
Only the external provider HTTP is mocked.
"""
import json
from importlib import import_module

import pytest

from services import chat_service, chat_tools
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401

COPY = 'oj-grid-copy'


@pytest.fixture
def named_copy(baseline, client):
    """A second project whose generator differs from the active baseline."""
    response = client.post(f'/api/projects/{baseline[1]}/scenarios', json={'name': COPY})
    assert response.status_code == 201, response.text
    copy_id = next(p['id'] for p in client.get('/api/projects/').json() if p['name'] == COPY)
    assert client.post(f'/api/projects/{copy_id}/activate').status_code == 200
    assert client.put('/api/network/generators/G1', json={'name': 'G1', 'bus': 'B1', 'marginal_cost': 25}).status_code == 200
    assert client.post(f'/api/projects/{COPY}').status_code == 200
    assert client.post(f'/api/projects/{baseline[1]}/activate').status_code == 200
    assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10
    return copy_id


def _journey(kind, model, base_url, auth, decision, mode='guided'):
    import httpx

    from harness.providers.anthropic import AnthropicProvider
    from harness.providers.openai_compat import OpenAICompatProvider
    from services import llm_config
    from tests.test_workflow_provider_parity import _anthropic_stream, _compatible_stream, _last_result

    native = kind == 'anthropic'
    transport = httpx
    if native:
        import anthropic as sdk
        transport = import_module(next(cls.__module__.split('.')[0] for cls in sdk.DefaultHttpxClient.__mro__
                                  if cls.__module__.split('.')[0] in ('httpx', 'httpx2')))
    profile = llm_config.LLMProfile(id='oj09', label='OJ-09 fixture', preset='custom',
                                    wire='anthropic' if native else 'openai', base_url=base_url,
                                    model=model, tools=True, vision=False, auth=auth,
                                    fallback_model=None, max_output_tokens=256)
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    chat_tools.set_chat_session(session)
    session.profile_id, session.bound_wire = profile.id, profile.wire
    # Each step's arguments come from the previous native result, as a model's would.
    steps = [lambda r: ('resolve_project_target', {'project_name': COPY}),
             lambda r: ('activate_project', {'project_id': r[0]['project_id']}),
             lambda r: ('use_toolset', {'domain': 'network'}),
             lambda r: ('get_component', {'component_class': 'Generator', 'name': 'G1'}),
             lambda r: (None, {})]
    calls, results, failures = [], [], []

    def respond(request):
        try:
            body = json.loads(request.content)
            index = len(calls)
            if index:
                call_id, content, error = _last_result(body, native)
                assert call_id == f'oj09-{index-1}', call_id
                parsed = None
                if not error and '{' in content:
                    parsed, _ = json.JSONDecoder().raw_decode(content[content.index('{'):])
                results.append(parsed if parsed is not None else {'error': content})
            name, args = steps[min(index, len(steps) - 1)](results)
            if decision == 'deny' and index == 2:
                name, args = None, {}
            calls.append(name)
            stream = (_anthropic_stream(model, f'oj09-{index}', name, args) if native
                      else _compatible_stream(f'oj09-{index}', name, args))
            return transport.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)
        except Exception as error:
            failures.append(repr(error))
            raise

    frames = []
    with transport.Client(transport=transport.MockTransport(respond)) as http:
        if native:
            provider = AnthropicProvider(sdk.Anthropic(api_key='synthetic', base_url=base_url,
                                                       http_client=http, max_retries=0))
        else:
            provider = OpenAICompatProvider(base_url, api_key='synthetic' if auth == 'bearer' else None,
                                            http_client=http, token_param='max_tokens')
        for event, data in chat_service.run_turn(session, f'Open project {COPY} and show me its generator.',
                                                 provider=provider, ui_context={'ui_mode': mode}):
            frames.append((event, data))
            if event == 'tool_pending_confirmation':
                assert data['tool_name'] == 'activate_project'
                session.record_decision(data['confirmation_token'], decision)
    assert not failures, failures
    return calls, results, frames


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_named_project_is_resolved_activated_on_approval_then_read(named_copy, client, kind, model, base_url, auth):
    calls, results, frames = _journey(kind, model, base_url, auth, 'approve')
    assert calls == ['resolve_project_target', 'activate_project', 'use_toolset', 'get_component', None]
    assert results[0]['project_id'] == named_copy and results[0]['project_name'] == COPY
    assert sum(event == 'tool_pending_confirmation' for event, _ in frames) == 1
    assert any(event == 'project_rebound' for event, _ in frames)
    # The answer is about the named project, never the previous one.
    assert results[3]['name'] == 'G1' and results[3]['marginal_cost'] == 25
    assert frames[-1][0] == 'turn_done'
    assert not [d for e, d in frames if e in {'error', 'tool_error'}]


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS[:2], ids=[row[0] for row in PROVIDERS[:2]])
def test_denied_activation_leaves_the_previous_project_active(named_copy, client, kind, model, base_url, auth):
    calls, results, frames = _journey(kind, model, base_url, auth, 'deny')
    assert calls == ['resolve_project_target', 'activate_project', None]
    assert results[0]['project_id'] == named_copy
    assert not any(event == 'project_rebound' for event, _ in frames)
    assert client.get('/api/network/meta').json()['loaded_project'] == 'Tool Baseline'
    assert client.get('/api/network/generators').json()[0]['marginal_cost'] == 10


def test_expert_activation_follows_the_existing_write_policy(named_copy, client):
    calls, results, frames = _journey(*PROVIDERS[1][:4], 'approve', mode='expert')
    assert calls == ['resolve_project_target', 'activate_project', 'use_toolset', 'get_component', None]
    assert not any(event == 'tool_pending_confirmation' for event, _ in frames)
    assert results[3]['marginal_cost'] == 25
