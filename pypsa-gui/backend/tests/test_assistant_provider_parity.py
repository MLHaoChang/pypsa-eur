"""New assistant workflow through native streaming transports and real HiGHS."""
import io
import json
import zipfile
from importlib import import_module

import httpx
import pytest

from harness.providers.anthropic import AnthropicProvider
from harness.providers.openai_compat import OpenAICompatProvider
from services import chat_tools, chat_service, llm_config
from services.pypsa_service import PyPSAService
from tests.test_workflow_tools import baseline, case, helpers_state  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS, _anthropic_stream, _compatible_stream, _last_result


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[p[0] for p in PROVIDERS])
def test_task_preview_solve_chart_export_delivery_native_streams(baseline, client, kind, model, base_url, auth):
    original_saved = (baseline[2] / 'network.nc').read_bytes()
    steps = [
        {'id': 'preview', 'tool': 'preview_project_changes', 'args': {'changes': case()['changes']}},
        {'id': 'apply', 'tool': 'apply_project_changes', 'args': {'preview_id': '$preview.preview_id'}},
        {'id': 'solve', 'tool': 'run_simulation', 'args': {}},
        {'id': 'chart', 'tool': 'create_chart', 'args': {'component': 'Generator', 'attribute': 'p', 'names': ['G1'], 'source': 'result'}},
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}},
        {'id': 'bundle', 'tool': 'build_delivery', 'args': {'file_ids': ['$chart.file_id', '$export.file_id']}},
    ]
    native = kind == 'anthropic'
    transport = httpx
    if native:
        import anthropic as sdk
        transport = import_module(next(c.__module__.split('.')[0] for c in sdk.DefaultHttpxClient.__mro__ if c.__module__.split('.')[0] in ('httpx', 'httpx2')))
    profile = llm_config.LLMProfile(id='assistant-parity', label='Test', preset='custom', wire='anthropic' if native else 'openai',
        base_url=base_url, model=model, tools=True, vision=False, auth=auth, fallback_model=None, max_output_tokens=256)
    llm_config.save_profiles([profile], profile.id)
    session = chat_service.ChatSession(model=model)
    session.profile_id, session.bound_wire = profile.id, profile.wire
    calls, outputs = [], []
    task_id = None

    def respond(request):
        nonlocal task_id
        body = json.loads(request.content)
        index = len(calls)
        if index:
            call_id, content, error = _last_result(body, native)
            assert call_id == f'call_{index-1}' and not error, content
            previous, _ = json.JSONDecoder().raw_decode(content[content.index('{'):])
            outputs.append(previous)
        if index == 0:
            name, args = 'find_capabilities', {'goal': 'parameter changes chart delivery'}
        elif index == 1:
            name, args = 'start_task', {'title': 'Refine, solve and deliver', 'request_id': 'native-flow', 'steps': steps}
        elif index == 2:
            task_id = outputs[-1]['task_id']
            name, args = 'resume_task', {'task_id': task_id}
        elif 3 <= index <= 8:
            state = outputs[-1].get('_task', outputs[-1])
            next_step = state['next_step']
            name, args = next_step['tool'], next_step['args']
        elif index == 9:
            name, args = 'get_task', {'task_id': task_id}
        elif index == 10:
            name, args = 'list_tasks', {}
        elif index == 11:
            bundle = next(result for result in outputs if result.get('files_total') == 2)
            name, args = 'get_file_delivery', {'file_id': bundle['file_id']}
        elif index == 12:
            export = next(result for result in outputs if result.get('filename') == 'network.nc')
            name, args = 'inspect_import', {'file_id': export['file_id']}
        elif index == 13:
            name, args = 'cancel_task', {'task_id': task_id}
        else:
            name, args = None, {}
        offered = {t['name'] if native else t['function']['name'] for t in body['tools']}
        assert not name or name in offered
        calls.append(name)
        stream = _anthropic_stream(model, f'call_{index}', name, args) if native else _compatible_stream(f'call_{index}', name, args)
        return transport.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)

    failures = []
    def checked(request):
        try:
            return respond(request)
        except Exception as exc:
            failures.append((repr(exc), calls, outputs[-1:]))
            raise
    with transport.Client(transport=transport.MockTransport(checked)) as http:
        if native:
            provider = AnthropicProvider(sdk.Anthropic(api_key='synthetic', base_url=base_url, http_client=http, max_retries=0))
        else:
            provider = OpenAICompatProvider(base_url, api_key='synthetic' if auth == 'bearer' else None, http_client=http, token_param='max_tokens')
        frames = []
        for event, data in chat_service.run_turn(session, 'Refine solve chart export_network_nc delivery', provider=provider):
            frames.append((event, data))
            if event == 'tool_pending_confirmation':
                session.record_decision(data['confirmation_token'], 'approve')
    assert not failures, failures
    assert frames[-1][0] == 'turn_done', frames
    assert not [d for e, d in frames if e in ('error', 'tool_error')], frames
    assert len([1 for e, _ in frames if e == 'tool_pending_confirmation']) == 2
    results = [d['result'] for e, d in frames if e == 'tool_result']
    assert next(r for r in results if r.get('task_id') and r['status'] == 'completed')['completed_steps'] == 6
    assert PyPSAService.get_network().generators.at['G1', 'marginal_cost'] == 20
    assert PyPSAService.get_network().objective == 200
    assert (baseline[2] / 'network.nc').read_bytes() == original_saved
    chart = next(r for r in results if r.get('filename', '').endswith('.png'))
    assert client.get(chart['download_url']).content.startswith(b'\x89PNG')
    bundle = next(r for r in results if r.get('files_total') == 2)
    with zipfile.ZipFile(io.BytesIO(client.get(bundle['download_url']).content)) as archive:
        assert len(json.loads(archive.read('manifest.json'))['files']) == 2
