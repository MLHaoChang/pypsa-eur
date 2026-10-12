"""
Complementary paid conversations: recover, refine and transport real networks.

Opt in explicitly; all calls share the existing cumulative token/dollar meter.
Successful cases are cached against their oracle and relevant production source.
"""
from contextlib import contextmanager
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import zipfile

import pandas as pd
import pypsa
import pytest
from jsonschema import Draft202012Validator

from harness.catalogue import TOOLS
from harness.events import FRAMES
from harness.providers import wiring
from services import chat_service, chat_tools, dirty_state, project_registry
from services.pypsa_service import PyPSAService
from tests.live_api_support import MeteredProvider
from tests.test_openai_comprehensive_live import comprehensive_run as _comprehensive_run, paid, setup_profile, turn
from tests.test_workflow_tools import baseline as _baseline, helpers_state  # noqa: F401

# Expose shared fixtures in this module for pytest's fixture discovery.
comprehensive_run = _comprehensive_run
baseline = _baseline


@contextmanager
def live_case(test, comprehensive_run, max_calls):
    meter, encoder, report, checkpoint = comprehensive_run
    backend = Path(__file__).resolve().parents[1]
    sources = ['services/assistant_tasks.py', 'services/assistant_tools.py', 'services/chat_tools.py',
               'services/workflow_tools.py', 'services/solver_service.py', 'routers/io.py',
               'harness/catalogue.py', 'harness/loop.py', 'harness/providers/wiring.py',
               'harness/toolsets.py', 'harness/results.py']
    source = b''.join((backend / path).read_bytes() for path in sources)
    source += inspect.getsource(test).encode()
    source += inspect.getsource(conversation).encode() + inspect.getsource(production_session).encode()
    digest = hashlib.sha256(source).hexdigest()[:16]
    key = test.__name__ + ':' + digest
    if report['cases'].get(key, {}).get('status') == 'passed':
        pytest.skip('unchanged successful recovery conversation cached; no duplicate API calls')
    provider = MeteredProvider(meter, encoder, key, max_calls=max_calls, api_key=os.environ['OPENAI_API_KEY'])
    before_tokens, before_cost = meter.charged, meter.cost_nanodollars
    record = {'status': 'failed', 'level': 'production_catalogue_real_handlers', 'turns': []}
    try:
        yield provider, record
        record['status'] = 'passed'
    except Exception as exc:
        record['failure'] = type(exc).__name__
        raise
    finally:
        record.update(completions=provider.calls, charged_tokens=meter.charged - before_tokens,
                      upper_cost_dollars=(meter.cost_nanodollars - before_cost) / 1e9)
        report['cases'][key] = record
        checkpoint()
        print('LIVE_RECOVERY ' + json.dumps({'case': test.__name__, 'status': record['status'],
              'completions': provider.calls, 'tokens': record['charged_tokens'],
              'upper_dollars': record['upper_cost_dollars']}), flush=True)


def production_session(monkeypatch, domain='all'):
    prompt, payload = chat_service._build_system_prompt, wiring._tools_payload
    session = setup_profile(monkeypatch, {t['name'] for t in TOOLS}, max_output_tokens=1024)
    monkeypatch.setattr(chat_service, '_build_system_prompt', prompt)
    monkeypatch.setattr(wiring, '_tools_payload', payload)
    session.toolset = domain
    return session


def conversation(session, prompt, provider, record, expected_errors=()):
    frames = turn(session, prompt, provider)
    errors = [data for event, data in frames if event in ('error', 'tool_error')]
    outputs = [data for event, data in frames if event == 'tool_result']
    requests = [data for event, data in frames if event == 'tool_request']
    text = ''.join(data['delta'] for event, data in frames if event == 'token')
    record['turns'].append({'tools': [data['tool_name'] for data in requests],
                            'errors': errors, 'reply': text[:2000]})
    assert frames[-1][0] == 'turn_done', frames[-1:]
    assert {event for event, _ in frames} <= FRAMES
    assert sorted(data.get('error_kind') for data in errors) == sorted(expected_errors), errors
    schemas = {tool['name']: tool['input_schema'] for tool in TOOLS}
    for request in requests:
        Draft202012Validator(schemas[request['tool_name']]).validate(request['args'])
        terminal = [data for event, data in frames if event in ('tool_result', 'tool_error')
                    and data.get('tool_use_id') == request['tool_use_id']]
        assert len(terminal) == 1, request
        assert terminal[0]['tool_name'] == request['tool_name']
    assert text.strip(), 'model must interpret the actual results after tool continuation'
    return frames, outputs, text


def result(outputs, name):
    return next(data['result'] for data in outputs if data['tool_name'] == name)


@paid
def test_live_task_resumes_in_new_chat_without_reexport(baseline, monkeypatch, comprehensive_run, client, tmp_path):
    with live_case(test_live_task_resumes_in_new_chat_without_reexport, comprehensive_run, 12) as (provider, record):
        original = (baseline[2] / 'network.nc').read_bytes()
        first = production_session(monkeypatch, 'results')
        steps = [
            {'id': 'export', 'tool': 'export_network_nc', 'args': {}},
            {'id': 'chart', 'tool': 'create_chart', 'args': {'component': 'Generator',
             'attribute': 'marginal_cost', 'names': ['G1'], 'source': 'input'}},
            {'id': 'delivery', 'tool': 'build_delivery', 'args': {'file_ids': ['$export.file_id', '$chart.file_id']}},
        ]
        _, outputs, _ = conversation(first,
            'This is a disposable integration check on Tool Baseline. Start a durable task titled '
            '"Resume delivery" with request_id "live-recovery" and EXACTLY these steps: ' + json.dumps(steps)
            + '. Execute ONLY its first export step with returned exact args, then stop this turn and report '
            'that two steps remain. Do not chart or bundle yet. Normal confirmations apply.', provider, record)
        task_id = result(outputs, 'start_task')['task_id']
        exported = result(outputs, 'export_network_nc')
        assert [o['tool_name'] for o in outputs] == ['start_task', 'export_network_nc']
        stored = json.loads((baseline[2] / 'assistant_tasks' / (task_id + '.json')).read_text())
        assert stored['steps'][0]['status'] == 'completed' and stored['steps'][1]['status'] == 'pending'
        second = production_session(monkeypatch, 'results')
        assert second.session_id != first.session_id and not second.messages
        _, resumed, text = conversation(second,
            'A previous chat started a task titled "Resume delivery" for this project, completed its export '
            'and stopped. Use list_tasks to discover it, then resume_task with its actual ID. Execute its '
            'remaining next_step calls with exactly the resolved arguments, then get_task to verify completion. '
            'Reuse the prior export ID; do not export again or start a new task. Report the authenticated '
            'delivery download link. I authorize the remaining chart and ZIP creation; normal gates apply.', provider, record)
        assert 'export_network_nc' not in [o['tool_name'] for o in resumed]
        final = result(resumed, 'get_task')
        assert final['task_id'] == task_id and final['completed_steps'] == 3 and final['status'] == 'completed'
        delivery, chart = result(resumed, 'build_delivery'), result(resumed, 'create_chart')
        response = client.get(delivery['download_url'])
        assert response.status_code == 200 and delivery['download_url'] in text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert {f['file_id'] for f in manifest['files']} == {exported['file_id'], chart['file_id']}
            for item in manifest['files']:
                data = archive.read(item['archive_path'])
                assert hashlib.sha256(data).hexdigest() == item['sha256']
                if item['file_id'] == chart['file_id']:
                    assert data.startswith(b'\x89PNG\r\n')
                else:
                    path = tmp_path / 'resumed.nc'
                    path.write_bytes(data)
                    n = pypsa.Network(path)
                    assert len(n.snapshots) == 2 and n.generators.at['G1', 'marginal_cost'] == 10
        assert (baseline[2] / 'network.nc').read_bytes() == original
        assert not baseline[0].results_unsaved
        record.update(completed_steps=3, export_repeated=False, artifact_hashes_verified=2)


@paid
def test_live_stale_preview_refused_then_repaired(baseline, monkeypatch, comprehensive_run):
    with live_case(test_live_stale_preview_refused_then_repaired, comprehensive_run, 12) as (provider, record):
        original = (baseline[2] / 'network.nc').read_bytes()
        session = production_session(monkeypatch)
        changes = [{'component_class': 'Generator', 'names': ['G1'], 'attribute': 'marginal_cost', 'value': 30}]
        _, first, _ = conversation(session,
            'For a disposable integration check, call preview_project_changes with changes ' + json.dumps(changes)
            + '. Do not apply or solve yet; report the before/after values and stop.', provider, record)
        stale_id = result(first, 'preview_project_changes')['preview_id']
        # An independent editor changes the input between model turns.
        chat_tools.bulk_update_components('Generator', ['G1'], {'marginal_cost': 15})
        _, outputs, text = conversation(session,
            'An independent editor changed G1 marginal_cost to 15 after the preview. For this integration '
            'check, attempt apply_project_changes ONCE with preview_id ' + stale_id + '. It must refuse '
            'stale inputs. After the refusal create a fresh preview setting G1 marginal_cost to 30, review '
            'the returned 15→30 diff, apply that NEW preview once, and run_simulation once. '
            'Report the actual final solve objective and explicitly mention the stale preview refusal. '
            'Do not force, save, retry the old preview, or switch projects. I authorize this bounded '
            'change and solve; normal confirmation cards apply.', provider, record, expected_errors=('preview_stale',))
        fresh = result(outputs, 'preview_project_changes')
        assert fresh['preview_id'] != stale_id
        assert fresh['diff'][0]['before'] == 15 and fresh['diff'][0]['after'] == 30
        assert result(outputs, 'apply_project_changes')['preview_id'] == fresh['preview_id']
        stale = json.loads((baseline[2] / 'assistant_previews' / (stale_id + '.json')).read_text())
        assert not stale['applied']
        n = PyPSAService.get_network()
        assert n.generators.at['G1', 'marginal_cost'] == 30 and n.objective == pytest.approx(300)
        assert n.generators_t.p['G1'].tolist() == [5, 5]
        assert '300' in text and 'stale' in text.lower()
        assert (baseline[2] / 'network.nc').read_bytes() == original
        assert sum(o['tool_name'] == 'run_simulation' for o in outputs) == 1
        record.update(expected_objective=300, stale_preview_applied=False)


@paid
def test_live_excel_import_resolve_save_and_evidence(baseline, monkeypatch, comprehensive_run, client):
    # Leave room for useful status/metadata verification, in addition to the
    # mandatory actions and final reply. The cumulative spending gate still
    # reserves every HTTP attempt before sending it.
    with live_case(test_live_excel_import_resolve_save_and_evidence, comprehensive_run, 18) as (provider, record):
        original = (baseline[2] / 'network.nc').read_bytes()
        # Time-varying demand makes a lost-sheet/horizon bug observable in cost.
        baseline[0].network.loads_t.p_set = pd.DataFrame({'L1': [5., 7.]}, index=baseline[0].network.snapshots)
        dirty_state.mark_dirty()
        session = production_session(monkeypatch)
        frames, outputs, text = conversation(session,
            'In this disposable integration check, export the CURRENT network using export_excel, then '
            'inspect_import with its actual file_id, then import_uploaded_network with that same ID and '
            'format "excel". Save the imported network as a NEW project "Live Excel Roundtrip" using '
            'save_project_as; never overwrite Tool Baseline. Then run_simulation once, save_project with '
            'name "Live Excel Roundtrip" to persist its solved results, then get_study_evidence with '
            'section "simulation" for the current project. Finally export_network_nc and return its '
            'authenticated download link, evidence objective and number of snapshots. '
            'Execute in that order, using real returned IDs. I authorize these bounded imports, new-project '
            'saves and solve; all normal confirmations apply. Do not invent results or send file bytes as '
            'tool arguments. Stop and explain any blocker.', provider, record)
        expected = ['export_excel', 'inspect_import', 'import_uploaded_network', 'save_project_as',
                    'run_simulation', 'save_project', 'get_study_evidence', 'export_network_nc']
        names = [o['tool_name'] for o in outputs]
        # Unforced models may verify solver status/download metadata. Preserve
        # exact effect order and counts while permitting those useful reads.
        assert [name for name in names if name in expected] == expected
        assert set(names) <= set(expected) | {'use_toolset', 'get_meta', 'get_simulation_status', 'get_file_delivery'}
        assert result(outputs, 'inspect_import')['native_import_format'] == 'excel'
        assert any(e == 'project_rebound' for e, _ in frames)
        assert PyPSAService.get_loaded_project() == 'Live Excel Roundtrip'
        n = PyPSAService.get_network()
        assert len(n.snapshots) == 2 and n.loads_t.p_set['L1'].tolist() == [5, 7]
        assert n.generators_t.p['G1'].tolist() == [5, 7] and n.objective == pytest.approx(120)
        evidence = result(outputs, 'get_study_evidence')
        assert evidence['available'] and evidence['basis'] == 'last_saved_results'
        assert evidence['objective'] == pytest.approx(120) and evidence['project_id'] != baseline[1]
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, 'Live Excel Roundtrip')
            saved = pypsa.Network(project_registry.project_dir(project) / 'network.nc')
        assert saved.objective == pytest.approx(120) and saved.loads_t.p_set['L1'].tolist() == [5, 7]
        exported = result(outputs, 'export_network_nc')
        response = client.get(exported['download_url'])
        assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == exported['sha256']
        assert exported['download_url'] in text and '120' in text
        assert (baseline[2] / 'network.nc').read_bytes() == original
        record.update(expected_objective=120, snapshots=2, dynamic_load_preserved=True, saved_evidence_verified=True)
