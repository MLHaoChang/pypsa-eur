"""Actual adapters, streamed arguments, real navigation and continuation; no spend."""
import json
from importlib import import_module

import httpx
import pytest
from jsonschema import Draft202012Validator

from harness.providers.anthropic import AnthropicProvider
from harness.providers.openai_compat import OpenAICompatProvider
from services import chat_service, llm_config
from tests.test_assistant_navigation_coverage import PANELS, RESULT_TABS
from tests.test_workflow_provider_parity import (
    PROVIDERS, _anthropic_stream, _compatible_stream, _last_result,
)
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[p[0] for p in PROVIDERS])
@pytest.mark.parametrize('targets', ['results', 'panels'])
def test_real_adapters_navigate_all_application_targets_and_continue(
    baseline, kind, model, base_url, auth, targets,
):
    anthropic = kind == 'anthropic'
    http_module = httpx
    if anthropic:
        import anthropic as sdk
        module = next(c.__module__.split('.')[0] for c in sdk.DefaultHttpxClient.__mro__
                      if c.__module__.split('.')[0] in ('httpx', 'httpx2'))
        http_module = import_module(module)
    wire = 'anthropic' if anthropic else 'openai'
    profile = llm_config.LLMProfile(
        id='navigation-parity', label='Navigation test', preset='custom',
        wire=wire, base_url=base_url, model=model, tools=True, vision=False,
        auth=auth, fallback_model=None, max_output_tokens=256,
    )
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, wire
    args = ([{'panel_id': 'results', 'results_tab': tab} for tab in RESULT_TABS]
            if targets == 'results' else [{'panel_id': panel} for panel in PANELS])
    echoed = []
    requests = []

    def respond(request):
        body = json.loads(request.content)
        index = len(requests)
        requests.append(body)
        if index:
            call_id, result, error = _last_result(body, anthropic)
            assert call_id == f'nav_{index-1}' and not error
            assert 'navigate' in result
            echoed.append(call_id)
        name = 'ui_open_panel' if index < len(args) else None
        values = args[index] if name else {}
        if name:
            declaration = next(t for t in body['tools']
                               if (t['name'] if anthropic else t['function']['name']) == name)
            schema = declaration['input_schema'] if anthropic else declaration['function']['parameters']
            Draft202012Validator(schema).validate(values)
        stream = (_anthropic_stream(model, f'nav_{index}', name, values) if anthropic
                  else _compatible_stream(f'nav_{index}', name, values))
        return http_module.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)

    with http_module.Client(transport=http_module.MockTransport(respond)) as http:
        provider = (AnthropicProvider(sdk.Anthropic(
            api_key='synthetic-key', base_url=base_url, http_client=http, max_retries=0,
        )) if anthropic else OpenAICompatProvider(
            base_url, api_key='synthetic-key' if auth == 'bearer' else None,
            http_client=http, token_param='max_tokens',
        ))
        frames = list(chat_service.run_turn(session, 'Open these views using ui_open_panel.', provider=provider))
    assert frames[-1][0] == 'turn_done'
    assert not [data for event, data in frames if event in ('error', 'tool_error')]
    events = [data for event, data in frames if event == 'ui_event']
    assert [{key: event[key] for key in values} for event, values in zip(events, args)] == args
    assert len(events) == len(args) == len(echoed)
    assert not [data for event, data in frames if event == 'tool_pending_confirmation']
