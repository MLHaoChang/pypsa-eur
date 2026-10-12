"""One bounded, cached, real model workflow using production tool selection."""
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from harness.catalogue import TOOLS
from harness.providers import wiring
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.live_api_support import MeteredProvider
from tests.test_openai_comprehensive_live import comprehensive_run, paid, setup_profile, turn  # noqa: F401
from tests.test_workflow_tools import baseline, helpers_state  # noqa: F401


@paid
def test_live_assistant_task_and_delivery(baseline, monkeypatch, comprehensive_run, client):
    meter, encoder, report, checkpoint = comprehensive_run
    backend = Path(__file__).resolve().parents[1]
    files = ['services/assistant_tasks.py', 'services/assistant_tools.py', 'services/chat_tools.py',
             'harness/catalogue.py', 'harness/loop.py', 'harness/providers/wiring.py', 'harness/toolsets.py',
             'tests/test_assistant_capabilities_live.py']
    digest = hashlib.sha256(b''.join((backend / f).read_bytes() for f in files)).hexdigest()[:16]
    key = 'assistant_task_delivery:' + digest
    if report['cases'].get(key, {}).get('status') == 'passed':
        pytest.skip('unchanged successful live workflow cached; zero duplicate API calls')
    original_prompt, original_tools = chat_service._build_system_prompt, wiring._tools_payload
    session = setup_profile(monkeypatch, {t['name'] for t in TOOLS}, max_output_tokens=1024)
    monkeypatch.setattr(chat_service, '_build_system_prompt', original_prompt)
    monkeypatch.setattr(wiring, '_tools_payload', original_tools)
    provider = MeteredProvider(meter, encoder, key, max_calls=14, api_key=os.environ['OPENAI_API_KEY'])
    steps = [
        {'id': 'preview', 'tool': 'preview_project_changes', 'args': {'changes': [
            {'component_class': 'Generator', 'names': ['G1'], 'attribute': 'marginal_cost', 'value': 20}]}},
        {'id': 'apply', 'tool': 'apply_project_changes', 'args': {'preview_id': '$preview.preview_id'}},
        {'id': 'solve', 'tool': 'run_simulation', 'args': {}},
        {'id': 'chart', 'tool': 'create_chart', 'args': {'component': 'Generator', 'attribute': 'p', 'names': ['G1'], 'source': 'result'}},
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}},
        {'id': 'bundle', 'tool': 'build_delivery', 'args': {'file_ids': ['$chart.file_id', '$export.file_id']}},
    ]
    prompt = (
        'In this disposable integration test the active saved project is Tool Baseline. '
        'First use find_capabilities for goal "parameter changes chart delivery". Then start_task with title '
        '"Refine, solve and deliver", request_id "live-assistant", and EXACTLY this ordered steps array: '
        + json.dumps(steps) + '. Execute its next_step through ordinary tool calls with the exact returned args. '
        'Each successful result includes _task with the next resolved call. Never repeat completed steps. '
        'After the bundle completes, use get_task to verify all six steps completed. Report the actual solve '
        'objective and download link. I authorize these bounded changes, solve and exports in this disposable '
        'project; normal confirmation cards still apply. Execute now and stop if blocked; do not use unrelated '
        'tools, save or switch projects, invent tool arguments, or stop with only a plan.'
    )
    before = (baseline[2] / 'network.nc').read_bytes()
    record = {'status': 'failed', 'level': 'production_catalogue_real_handlers'}
    try:
        frames = turn(session, prompt, provider)
        assert frames[-1][0] == 'turn_done', frames[-1:]
        assert not [d for e, d in frames if e in ('error', 'tool_error')], frames
        outputs = [d for e, d in frames if e == 'tool_result']
        required = {'find_capabilities', 'start_task', 'preview_project_changes', 'apply_project_changes',
                    'run_simulation', 'create_chart', 'export_network_nc', 'build_delivery', 'get_task'}
        assert required <= {d['tool_name'] for d in outputs}
        final = next(d['result'] for d in outputs if d['tool_name'] == 'get_task')
        assert final['status'] == 'completed' and final['completed_steps'] == 6
        assert PyPSAService.get_network().objective == 200
        assert PyPSAService.get_network().generators.at['G1', 'marginal_cost'] == 20
        assert (baseline[2] / 'network.nc').read_bytes() == before
        bundle = next(d['result'] for d in outputs if d['tool_name'] == 'build_delivery')
        with zipfile.ZipFile(io.BytesIO(client.get(bundle['download_url']).content)) as archive:
            assert len(json.loads(archive.read('manifest.json'))['files']) == 2
        record.update(status='passed', tools=sorted(required), completions=provider.calls, objective=200, completed_steps=6)
    except Exception as exc:
        record['failure'] = type(exc).__name__
        raise
    finally:
        report['cases'][key] = record
        checkpoint()
