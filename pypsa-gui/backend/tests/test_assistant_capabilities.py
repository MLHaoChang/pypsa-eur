"""Real project, ordinary dispatch and file checks; no paid calls."""
import hashlib
import io
import json
import uuid
import zipfile

import pytest
from fastapi import HTTPException
from harness.providers.fake import FakeProvider
from services import assistant_tasks as tasks, assistant_tools as tools, chat_tools, chat_service, upload_service, undo_service
from services.pypsa_service import PyPSAService
from tests.test_workflow_tools import baseline, case, helpers_state  # noqa: F401
from tests.test_openai_comprehensive_live import setup_profile, turn


@pytest.fixture
def session(baseline):
    value = chat_service.ChatSession()
    chat_tools.set_chat_session(value)
    return value


def dispatch(session, name, args, decision='approve'):
    result, collector = [], []
    for event, data in chat_service._dispatch_real_tool_call(session, {'id': uuid.uuid4().hex, 'name': name, 'input': args}, collector):
        result.append((event, data))
        if event == 'tool_pending_confirmation':
            session.record_decision(data['confirmation_token'], decision)
    assert collector, result
    return result


def output(frames):
    return next(d['result'] for e, d in frames if e == 'tool_result')


def plan():
    return dict(title='Export and deliver', request_id='export-v1', steps=[
        {'id': 'export', 'tool': 'export_network_nc', 'args': {}},
        {'id': 'delivery', 'tool': 'build_delivery', 'args': {'file_ids': ['$export.file_id']}},
    ])


def test_real_task_export_delivery_recovers_new_session(baseline, session, client):
    ctx, project_id, root = baseline
    task = chat_tools.start_task(**plan())
    assert not ctx.results_unsaved
    assert chat_tools.start_task(**plan())['task_id'] == task['task_id']
    exported = output(dispatch(session, 'export_network_nc', {}))
    assert exported['_task']['completed_steps'] == 1
    assert exported['_task']['next_step']['args']['file_ids'] == [exported['file_id']]
    other = chat_service.ChatSession()
    chat_tools.set_chat_session(other)
    resumed = chat_tools.resume_task(task['task_id'])
    assert chat_tools.list_tasks()['items'][0]['task_id'] == task['task_id']
    delivery = output(dispatch(other, 'build_delivery', resumed['next_step']['args']))
    assert delivery['_task']['status'] == 'completed'
    assert chat_tools.resume_task(task['task_id'])['status'] == 'completed'
    saved = json.loads((root / 'assistant_tasks' / (task['task_id'] + '.json')).read_text())
    assert saved['steps'][0]['result']['file_id'] == exported['file_id']
    response = client.get(delivery['download_url'])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['project_id'] == project_id
        item = manifest['files'][0]
        assert hashlib.sha256(archive.read(item['archive_path'])).hexdigest() == exported['sha256']
    assert not ctx.results_unsaved


def test_dedup_conflicts_invalid_tools_and_references(session):
    chat_tools.start_task(**plan())
    with pytest.raises(HTTPException) as failure:
        chat_tools.start_task(title='Different', steps=plan()['steps'], request_id='export-v1')
    assert failure.value.detail['error_kind'] == 'task_request_conflict'
    for steps in ([{'id': 'a', 'tool': 'activate_project', 'args': {'name': 'Tool Baseline'}}],
                  [{'id': 'a', 'tool': 'missing_tool', 'args': {}}],
                  [{'id': 'a', 'tool': 'build_delivery', 'args': {'file_ids': ['$future.file_id']}}],
                  [{'id': 'a', 'tool': 'get_component', 'args': {}}]):
        with pytest.raises(HTTPException):
            chat_tools.start_task(title='Invalid', request_id='invalid', steps=steps)


def test_denied_confirmation_leaves_step_pending(baseline, session):
    preview = chat_tools.preview_project_changes(case()['changes'])
    task = chat_tools.start_task(title='Apply', request_id='deny', steps=[
        {'id': 'apply', 'tool': 'apply_project_changes', 'args': {'preview_id': preview['preview_id']}}])
    frames = dispatch(session, 'apply_project_changes', task['next_step']['args'], decision='deny')
    assert any(d.get('error_kind') == 'confirmation_denied' for _, d in frames)
    assert chat_tools.get_task(task['task_id'])['next_step']['status'] == 'pending'
    assert baseline[0].network.generators.at['G1', 'marginal_cost'] == 10


def test_interrupted_write_requires_confirmed_reconciliation(session):
    task = chat_tools.start_task(**plan())
    ticket = tasks.begin_step(session, 'export_network_nc', {})
    tasks.finish_step(ticket, error='worker interrupted')
    assert chat_tools.resume_task(task['task_id'])['status'] == 'needs_review'
    with pytest.raises(HTTPException) as failure:
        tasks.begin_step(session, 'export_network_nc', {})
    assert failure.value.detail['error_kind'] == 'task_step_uncertain'
    denied = dispatch(session, 'resolve_task_step', {'task_id': task['task_id'], 'outcome': 'retry'}, decision='deny')
    assert any(d.get('error_kind') == 'confirmation_denied' for _, d in denied)
    assert chat_tools.get_task(task['task_id'])['status'] == 'needs_review'
    resolved = output(dispatch(session, 'resolve_task_step', {'task_id': task['task_id'], 'outcome': 'retry'}))
    assert resolved['next_step']['status'] == 'pending'


def test_active_origin_refuses_cross_session_resume(session, monkeypatch):
    from harness import session as sessions
    task = chat_tools.start_task(**plan())
    tasks.begin_step(session, 'export_network_nc', {})
    session._turn_in_flight = True
    monkeypatch.setitem(sessions._SESSIONS, session.session_id, session)
    chat_tools.set_chat_session(chat_service.ChatSession())
    with pytest.raises(HTTPException) as failure:
        chat_tools.resume_task(task['task_id'])
    assert failure.value.detail['error_kind'] == 'task_step_running'
    session._turn_in_flight = False
    assert chat_tools.resume_task(task['task_id'])['status'] == 'needs_review'


def test_completed_step_not_replayed_cancel_is_durable(session):
    task = chat_tools.start_task(**plan())
    ticket = tasks.begin_step(session, 'export_network_nc', {})
    tasks.finish_step(ticket, result={'file_id': '0123456789abcdef'})
    with pytest.raises(HTTPException) as failure:
        tasks.begin_step(session, 'export_network_nc', {})
    assert failure.value.detail['error_kind'] == 'task_step_already_completed'
    assert chat_tools.cancel_task(task['task_id'])['status'] == 'cancelled'
    with pytest.raises(HTTPException):
        chat_tools.resume_task(task['task_id'])


def test_task_owner_isolation_and_path_refusal(session, baseline):
    task = chat_tools.start_task(**plan())
    path = baseline[2] / 'assistant_tasks' / (task['task_id'] + '.json')
    record = json.loads(path.read_text())
    record['owner_id'] = str(uuid.uuid4())
    path.write_text(json.dumps(record))
    with pytest.raises(HTTPException) as failure:
        chat_tools.get_task(task['task_id'])
    assert failure.value.status_code == 404
    assert chat_tools.list_tasks()['items'] == []
    with pytest.raises(HTTPException):
        chat_tools.get_task('../../secrets')


def test_timed_out_wait_does_not_complete_evidence_dependency(session):
    task = chat_tools.start_task(title='Wait', request_id='wait', steps=[
        {'id': 'wait', 'tool': 'wait_for_job', 'args': {'job_id': 'job'}},
        {'id': 'next', 'tool': 'get_file_delivery', 'args': {'file_id': '$wait.job.file_id'}}])
    ticket = tasks.begin_step(session, 'wait_for_job', {'job_id': 'job'})
    result = tasks.finish_step(ticket, result={'timed_out': True})
    assert result['completed_steps'] == 0 and result['next_step']['status'] == 'failed'
    assert tasks.begin_step(session, 'project_readiness', {}) is None


def test_discovery_cached_bounded_and_reports_hidden_toolset(session):
    chat_tools.use_toolset('simulation')
    result = chat_tools.find_capabilities('create_component', limit=3)
    assert result['items'][0]['name'] == 'create_component'
    assert not result['items'][0]['in_current_toolset']
    assert 'component_class' in result['items'][0]['required']
    assert len(json.dumps(result)) <= 3800
    before = tools._search_index.cache_info().hits
    chat_tools.find_capabilities('create_component', limit=3)
    assert tools._search_index.cache_info().hits > before


def test_preview_apply_atomic_stale_refusal_and_undo(baseline, session):
    ctx = baseline[0]
    undo_service.clear()
    preview = chat_tools.preview_project_changes(case()['changes'])
    assert preview['diff'][0]['before'] == 10 and preview['diff'][0]['after'] == 20
    assert ctx.network.generators.at['G1', 'marginal_cost'] == 10 and not ctx.results_unsaved
    applied = output(dispatch(session, 'apply_project_changes', {'preview_id': preview['preview_id']}))
    assert applied['applied'] and not applied['saved']
    assert PyPSAService.get_network().generators.at['G1', 'marginal_cost'] == 20
    assert undo_service.depth() == 1
    assert chat_tools.apply_project_changes(preview['preview_id'])['reused']
    chat_tools.undo_last()
    assert PyPSAService.get_network().generators.at['G1', 'marginal_cost'] == 10
    stale = chat_tools.preview_project_changes(case()['changes'])
    chat_tools.bulk_update_components('Generator', ['G1'], {'p_nom': 50})
    with pytest.raises(HTTPException) as failure:
        chat_tools.apply_project_changes(stale['preview_id'])
    assert failure.value.detail['error_kind'] == 'preview_stale'
    assert PyPSAService.get_network().generators.at['G1', 'marginal_cost'] == 10


@pytest.mark.parametrize('changes', [
    [{'component_class': 'Generator', 'names': ['missing'], 'attribute': 'marginal_cost', 'value': 20}],
    [{'component_class': 'Generator', 'names': ['G1'], 'attribute': 'bus', 'value': 20}],
    [{'component_class': 'Generator', 'names': ['G1'], 'attribute': 'p_nom_extendable', 'value': 1}],
    [{'component_class': 'Generator', 'names': ['G1'], 'attribute': 'p_nom', 'value': float('inf')}],
    case()['changes'] * 2,
])
def test_invalid_preview_does_not_modify_project(baseline, changes):
    with pytest.raises(HTTPException):
        chat_tools.preview_project_changes(changes)
    assert baseline[0].network.generators.at['G1', 'marginal_cost'] == 10


def test_preview_busy_and_owner_refusal(session, baseline):
    preview = chat_tools.preview_project_changes(case()['changes'])
    path = baseline[2] / 'assistant_previews' / (preview['preview_id'] + '.json')
    record = json.loads(path.read_text())
    record['owner_id'] = 'other'
    path.write_text(json.dumps(record))
    with pytest.raises(HTTPException) as failure:
        chat_tools.apply_project_changes(preview['preview_id'])
    assert failure.value.status_code == 404
    baseline[0].solver_state['status'] = 'running'
    with pytest.raises(HTTPException) as failure:
        chat_tools.preview_project_changes(case()['changes'])
    assert failure.value.detail['error_kind'] == 'project_busy'


def test_inspect_csv_hash_chart_and_delivery(baseline, client):
    meta = upload_service.add_upload('Tool Baseline', b'timestamp,MW\n2026-01-01,1\n2026-01-01,\nwrong,2\n', 'input.csv', 'text/csv')
    report = chat_tools.inspect_import(meta.file_id)
    columns = {c['name']: c for c in report['tables'][0]['columns']}
    assert columns['MW']['missing_in_sample'] == 1
    assert columns['timestamp']['time_axis']['invalid'] == 1
    assert columns['timestamp']['time_axis']['duplicates'] == 1
    assert report['sha256'] == hashlib.sha256(client.get(report['download_url']).content).hexdigest()
    chart = chat_tools.create_chart('Generator', 'marginal_cost', ['G1'])
    assert client.get(chart['download_url']).content.startswith(b'\x89PNG\r\n')
    bundle = chat_tools.build_delivery([chart['file_id'], meta.file_id])
    with zipfile.ZipFile(io.BytesIO(client.get(bundle['download_url']).content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['files']) == 2
        assert manifest['files'][0]['provenance']['input_fingerprint'] == chart['provenance']['input_fingerprint']
        assert all('..' not in p and not p.startswith('/') for p in archive.namelist())
    with pytest.raises(HTTPException):
        chat_tools.create_chart('Generator', 'p', ['G1'], source='result')


def test_native_import_confirmations_and_rebinding(baseline, session, monkeypatch):
    exported = chat_tools.export_network_nc()
    args = {'file_id': exported['file_id'], 'format': 'netcdf'}
    denied = dispatch(session, 'import_uploaded_network', args, decision='deny')
    assert any(d.get('error_kind') == 'confirmation_denied' for _, d in denied)
    assert PyPSAService.get_loaded_project() == 'Tool Baseline'
    configured = setup_profile(monkeypatch, {'import_uploaded_network'})
    fake = FakeProvider([{'blocks': [{'type': 'tool_use', 'id': 'import', 'name': 'import_uploaded_network', 'input': args}]},
                         {'blocks': [{'type': 'text', 'text': 'Imported.'}]}])
    frames = turn(configured, 'import_uploaded_network', fake)
    assert not [d for e, d in frames if e in ('error', 'tool_error')], frames
    assert any(e == 'project_rebound' and d['to'] is None for e, d in frames)
    assert PyPSAService.get_network().generators.at['G1', 'marginal_cost'] == 10


def test_delivery_corruption_duplicate_and_foreign_id_refusal(baseline):
    artifact = chat_tools.export_network_nc()
    for ids in ([artifact['file_id']] * 2, ['0000000000000000'], ['..']):
        with pytest.raises(HTTPException):
            chat_tools.build_delivery(ids)
    path = upload_service.get_upload_path('Tool Baseline', artifact['file_id'])
    path.write_bytes(b'corrupt')
    with pytest.raises(HTTPException) as failure:
        chat_tools.build_delivery([artifact['file_id']])
    assert failure.value.detail['error_kind'] == 'delivery_file_changed'


def test_detached_timeout_worker_blocks_reconciliation(session):
    from concurrent.futures import Future
    task = chat_tools.start_task(**plan())
    ticket = tasks.begin_step(session, 'export_network_nc', {})
    worker = Future()
    tasks.register_worker(ticket, worker)
    tasks.finish_step(ticket, error='tool_timeout')
    with pytest.raises(HTTPException) as failure:
        chat_tools.resolve_task_step(task['task_id'], 'retry')
    assert failure.value.detail['error_kind'] == 'task_step_running'
    worker.set_result(None)
    assert chat_tools.resolve_task_step(task['task_id'], 'retry')['next_step']['status'] == 'pending'


def test_checkpoint_failure_pairs_real_tool_id_and_retains_uncertainty(session, monkeypatch):
    task = chat_tools.start_task(**plan())
    original = tasks._save
    def fail_completed(path, record):
        if record['steps'][0]['status'] == 'completed':
            raise OSError('disk unavailable')
        return original(path, record)
    monkeypatch.setattr(tasks, '_save', fail_completed)
    frames = dispatch(session, 'export_network_nc', {})
    assert any(e == 'tool_error' and d['error_kind'] == 'task_checkpoint_failed' for e, d in frames)
    assert chat_tools.get_task(task['task_id'])['next_step']['status'] == 'running'
    assert chat_tools.resume_task(task['task_id'])['status'] == 'needs_review'


def test_workbook_inspection_and_expansion_limit(baseline):
    import pandas as pd
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer) as writer:
        pd.DataFrame({'asset': ['G1'], 'MW': [20]}).to_excel(writer, sheet_name='Generators', index=False)
        pd.DataFrame({'bus': ['B1']}).to_excel(writer, sheet_name='Buses', index=False)
    meta = upload_service.add_upload('Tool Baseline', buffer.getvalue(), 'network.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    report = chat_tools.inspect_import(meta.file_id)
    assert report['total_sheets'] == 2 and report['native_import_format'] == 'excel'
    assert report['tables'][0]['columns'][1]['name'] == 'MW'
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('data.xml', b'0' * (51 * 1024 * 1024))
    oversized = upload_service.add_upload('Tool Baseline', bomb.getvalue(), 'large.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    with pytest.raises(HTTPException) as failure:
        chat_tools.inspect_import(oversized.file_id)
    assert failure.value.detail['error_kind'] == 'import_expansion_too_large'


def test_preview_and_apply_obey_live_study_gate(baseline, session):
    from services.study_state import LIVE_NETWORK_STUDIES
    preview = chat_tools.preview_project_changes(case()['changes'])
    import threading
    baseline[0].solver_state[next(iter(LIVE_NETWORK_STUDIES))] = {'status': 'running', 'thread': threading.Thread()}
    with pytest.raises(HTTPException):
        chat_tools.preview_project_changes(case()['changes'])
    frames = dispatch(session, 'apply_project_changes', {'preview_id': preview['preview_id']})
    assert any(e == 'tool_error' and d['error_kind'] == 'study_in_flight' for e, d in frames)
    assert PyPSAService.get_network().generators.at['G1', 'marginal_cost'] == 10


def test_new_project_rebinding_detaches_durable_task(session, baseline):
    task = chat_tools.start_task(**plan())
    old = baseline[0].project_uuid
    baseline[0].project_uuid = str(uuid.uuid4())
    assert tasks.begin_step(session, 'project_readiness', {}) is None
    assert session.task_id is None
    baseline[0].project_uuid = old
    assert chat_tools.get_task(task['task_id'])['completed_steps'] == 0


def test_default_args_match_and_stale_parameter_chart_is_refused(session):
    task = chat_tools.start_task(title='Chart', request_id='default-source', steps=[
        {'id': 'chart', 'tool': 'create_chart', 'args': {'component': 'Generator', 'attribute': 'marginal_cost', 'names': ['G1']}}])
    result = output(dispatch(session, 'create_chart', {'component': 'Generator', 'attribute': 'marginal_cost', 'names': ['G1'], 'source': 'input'}))
    assert result['_task']['status'] == 'completed'
    output(dispatch(session, 'run_simulation', {}))
    from services.workflow_tools import _network_input_fingerprint
    network = PyPSAService.get_network()
    model = network._model
    fingerprint = _network_input_fingerprint(PyPSAService.get_active_context())
    assert network.meta['assistant_solve_input_fingerprint'] == fingerprint
    chat_tools.create_chart('Generator', 'p', ['G1'], 'result')
    assert network._model is model and model.solver_model is not None
    chat_tools.bulk_update_components('Generator', ['G1'], {'marginal_cost': 30})
    with pytest.raises(HTTPException) as failure:
        chat_tools.create_chart('Generator', 'p', ['G1'], 'result')
    assert failure.value.detail['error_kind'] == 'chart_results_unavailable'
    # Previewing a solved network also keeps the original model intact.
    chat_tools.preview_project_changes(case()['changes'])
    assert network._model is model


def test_native_network_file_upload_is_accepted_and_attached_as_metadata(baseline, client, monkeypatch):
    from harness.loop import _build_user_content
    data = (baseline[2] / 'network.nc').read_bytes()
    project_id = baseline[1]
    response = client.post(f'/api/projects/{project_id}/uploads', files={'file': ('uploaded.nc', data, 'application/x-netcdf')})
    assert response.status_code == 200, response.text
    file_id = response.json()['file_id']
    contents, failure = _build_user_content('Tool Baseline', [file_id], 'Inspect my network')
    assert failure is None
    text = contents[-1]['text']
    assert file_id in text and 'import_uploaded_network' in text
    assert all(block['type'] == 'text' for block in contents)


def test_new_network_and_metadata_tools_retain_foreign_lock_guards():
    gated = chat_tools._lock_gated_tool_names()
    assert {'apply_project_changes', 'import_uploaded_network', 'preview_project_changes', 'create_chart',
            'build_delivery', 'start_task', 'resume_task', 'cancel_task', 'resolve_task_step'} <= gated
    assert 'apply_project_changes' in chat_tools.UNDO_CAPTURED_TOOLS
    assert 'import_uploaded_network' in chat_tools.UNDO_CAPTURED_TOOLS


def test_apply_confirmation_contains_reviewable_diff_not_just_uuid(baseline, session):
    preview = chat_tools.preview_project_changes(case()['changes'])
    frames = dispatch(session, 'apply_project_changes', {'preview_id': preview['preview_id']}, decision='deny')
    card = next(d for e, d in frames if e == 'tool_pending_confirmation')
    assert card['args']['preview_id'] == preview['preview_id']
    assert card['args']['reviewed_changes'][0]['before'] == 10
    assert card['args']['reviewed_changes'][0]['after'] == 20
    assert 'unit' in card['args']['reviewed_changes'][0]


@pytest.mark.parametrize('format,exporter', [
    ('netcdf', 'export_network_nc'), ('csv_bundle', 'export_csv_bundle'),
    ('excel', 'export_excel'), ('matpower', 'export_matpower'),
])
def test_all_native_file_bridge_roundtrips(baseline, session, format, exporter):
    artifact = getattr(chat_tools, exporter)()
    frames = dispatch(session, 'import_uploaded_network', {'file_id': artifact['file_id'], 'format': format})
    assert not [d for e, d in frames if e == 'tool_error'], frames
    network = PyPSAService.get_network()
    assert len(network.buses) == 1 and len(network.generators) == 1 and len(network.loads) == 1
    assert float(network.generators.p_nom.iloc[0]) == 20
    assert float(network.loads.p_set.sum()) == 5
    assert float(network.generators.marginal_cost.iloc[0]) == 10
    if format in ('netcdf', 'csv_bundle', 'excel'):
        assert len(network.snapshots) == 2


def test_matpower_roundtrip_reactive_demand_and_impedance_units(baseline, session):
    n = PyPSAService.get_network()
    n.add('Bus', 'B2', v_nom=110)
    n.add('Load', 'L2', bus='B2', p_set=1, q_set=0.2)
    n.add('Line', 'Branch', bus0='B1', bus1='B2', r=0.1, x=0.2, b=0.00001, s_nom=10)
    artifact = chat_tools.export_matpower()
    frames = dispatch(session, 'import_uploaded_network', {'file_id': artifact['file_id'], 'format': 'matpower'})
    assert not [d for e, d in frames if e == 'tool_error'], frames
    imported = PyPSAService.get_network()
    assert imported.loads.p_set.sum() == 6 and imported.loads.q_set.sum() == 0.2
    assert imported.lines.r.iloc[0] == pytest.approx(0.1)
    assert imported.lines.x.iloc[0] == pytest.approx(0.2)
    assert imported.lines.b.iloc[0] == pytest.approx(0.00001)


@pytest.mark.parametrize('format,exporter', [
    ('netcdf', 'export_network_nc'), ('csv_bundle', 'export_csv_bundle'), ('excel', 'export_excel'),
])
def test_native_dynamic_roundtrip_solves_and_persists_evidence(baseline, session, format, exporter):
    import pandas as pd
    from services import project_registry
    before = (baseline[2] / 'network.nc').read_bytes()
    n = PyPSAService.get_network()
    n.loads_t.p_set = pd.DataFrame({'L1': [5., 7.]}, index=n.snapshots)
    n.snapshot_weightings.loc[:, 'objective'] = [2., 3.]
    artifact = getattr(chat_tools, exporter)()
    imported = dispatch(session, 'import_uploaded_network', {'file_id': artifact['file_id'], 'format': format})
    assert not [d for e, d in imported if e == 'tool_error'], imported
    n = PyPSAService.get_network()
    assert n.loads_t.p_set['L1'].tolist() == [5, 7]
    assert n.snapshot_weightings.objective.tolist() == [2, 3]
    name = 'Dynamic Roundtrip ' + format
    output(dispatch(session, 'save_project_as', {'name': name}))
    solved = dispatch(session, 'run_simulation', {})
    assert not [d for e, d in solved if e == 'tool_error'], solved
    # 10 currency/MWh * (5 MW * 2 hours + 7 MW * 3 hours).
    assert PyPSAService.get_network().objective == pytest.approx(310)
    output(dispatch(session, 'save_project', {'name': name}))
    evidence = chat_tools.get_study_evidence(section='simulation')
    assert evidence['available'] and evidence['objective'] == pytest.approx(310)
    assert evidence['project_id'] != baseline[1]
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, name)
        import pypsa
        saved = pypsa.Network(project_registry.project_dir(project) / 'network.nc')
    assert saved.objective == pytest.approx(310) and saved.loads_t.p_set['L1'].tolist() == [5, 7]
    assert (baseline[2] / 'network.nc').read_bytes() == before
