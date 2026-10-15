"""Issue 06 first slice — immutable run manifests, list_project_runs, get_run_evidence.

Covers completed / failed / running runs in one project, project + ACL scoping,
typed ``evidence_unavailable`` (never newest-run detail), atomic/immutable
publication, duplicate completion, pagination and bounded retention.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness import catalogue
from services import chat_tools
from services import run_evidence as re_

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'
OTHER_PROJECT = '22222222-2222-2222-2222-222222222222'

T_COMPLETED = '2026-10-01T10:00:00+00:00'
T_FAILED = '2026-10-02T10:00:00+00:00'
T_RUNNING = '2026-10-03T10:00:00+00:00'


@pytest.fixture
def roots(tmp_path, monkeypatch):
    state = {'project': PROJECT, 'revoked': False}

    def root_for(project_id):
        root = tmp_path / project_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def authority():
        if state['revoked']:
            raise HTTPException(404, 'Project not found')
        return root_for(state['project']), state['project'], OWNER

    monkeypatch.setattr(re_, '_authority', authority)
    state['root_for'] = root_for
    return state


def _publish_completed(root, run_id='run-completed', **kw):
    return re_.publish_run(
        root, PROJECT,
        run_id=run_id, kind='simulation', status='completed',
        started_at=kw.pop('started_at', T_COMPLETED),
        finished_at='2026-10-01T10:05:00+00:00',
        mode='lopf', fingerprint='fp-completed',
        metrics={'objective': {'value': 1234.5, 'unit': 'EUR'},
                 'solve_seconds': {'value': 42.0, 'unit': 's'}},
        sections={'summary': {'total_cost': {'value': 1234.5, 'unit': 'EUR'},
                              'peak_load': {'value': 88.0, 'unit': 'MW'}}},
        **kw,
    )


def _three_runs(root):
    _publish_completed(root)
    re_.publish_run(
        root, PROJECT, run_id='run-failed', kind='simulation', status='failed',
        started_at=T_FAILED, finished_at='2026-10-02T10:01:00+00:00',
        mode='lopf', fingerprint='fp-failed',
        error={'error_kind': 'solver_infeasible', 'message': 'Infeasible.'},
    )
    re_.start_run(root, PROJECT, run_id='run-running', kind='simulation',
                  started_at=T_RUNNING, mode='lopf', fingerprint='fp-running')


# ── list ────────────────────────────────────────────────────────────────

def test_list_completed_failed_and_running_newest_first(roots):
    _three_runs(roots['root_for'](PROJECT))
    out = re_.list_project_runs()
    assert [r['run_id'] for r in out['items']] == ['run-running', 'run-failed', 'run-completed']
    by_id = {r['run_id']: r for r in out['items']}
    assert by_id['run-running']['status'] == 'running'
    assert by_id['run-running']['finished_at'] is None
    assert by_id['run-failed']['status'] == 'failed'
    assert by_id['run-completed']['status'] == 'completed'
    assert by_id['run-completed']['started_at'] == T_COMPLETED
    assert by_id['run-completed']['evidence_available'] is True
    assert by_id['run-failed']['evidence_available'] is False
    assert out['total_count'] == 3 and out['has_more'] is False


def test_list_status_filter_and_pagination(roots):
    _three_runs(roots['root_for'](PROJECT))
    page1 = re_.list_project_runs(limit=2)
    assert [r['run_id'] for r in page1['items']] == ['run-running', 'run-failed']
    assert page1['has_more'] is True and page1['total_count'] == 3
    page2 = re_.list_project_runs(limit=2, offset=2)
    assert [r['run_id'] for r in page2['items']] == ['run-completed']
    assert page2['has_more'] is False
    only_failed = re_.list_project_runs(status='failed')
    assert [r['run_id'] for r in only_failed['items']] == ['run-failed']
    assert only_failed['total_count'] == 1


@pytest.mark.parametrize('kwargs', [
    {'limit': 0}, {'limit': 51}, {'limit': True}, {'offset': -1}, {'status': 'bogus'},
])
def test_list_rejects_invalid_arguments(roots, kwargs):
    with pytest.raises(HTTPException) as exc:
        re_.list_project_runs(**kwargs)
    assert exc.value.status_code == 422


def test_list_empty_project(roots):
    out = re_.list_project_runs()
    assert out['items'] == [] and out['total_count'] == 0 and out['has_more'] is False


@pytest.mark.parametrize('target', ['run', 'manifest', 'section', 'evidence_root'])
def test_evidence_reads_refuse_symlinks_outside_the_owned_project(roots, tmp_path, target):
    root = roots['root_for'](PROJECT)
    _publish_completed(root)
    run = root / 'run_evidence' / 'run-completed'
    outside = tmp_path / 'outside'
    outside.mkdir()
    if target == 'section':
        path = run / 'sections' / 'summary.json'
        external = outside / 'summary.json'
        external.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(external)
        out = re_.get_run_evidence('run-completed')
        assert out['evidence_available'] is False
        with pytest.raises(HTTPException) as exc:
            re_.get_run_evidence('run-completed', 'summary')
        assert exc.value.detail['error_kind'] == 'evidence_unavailable'
        return
    if target == 'manifest':
        path = run / 'manifest.json'
        external = outside / 'manifest.json'
    elif target == 'run':
        path, external = run, outside / 'run-completed'
    else:
        path, external = root / 'run_evidence', outside / 'run_evidence'
    path.rename(external)
    path.symlink_to(external, target_is_directory=target != 'manifest')
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-completed')
    assert exc.value.status_code == 404


def test_corrupt_manifest_section_names_cannot_escape_sections_directory(roots):
    root = roots['root_for'](PROJECT)
    _publish_completed(root)
    run = root / 'run_evidence' / 'run-completed'
    manifest = run / 'manifest.json'
    record = json.loads(manifest.read_text())
    record['sections']['../private'] = record['sections'].pop('summary')
    (run / 'sections' / 'summary.json').rename(run / 'private.json')
    manifest.write_text(json.dumps(record))
    out = re_.get_run_evidence('run-completed')
    assert out['evidence_available'] is False
    assert out['sections'] == [{'name': '../private', 'available': False}]


def test_publication_does_not_follow_a_symlinked_sections_directory(roots, tmp_path):
    root = roots['root_for'](PROJECT)
    run = root / 'run_evidence' / 'run-completed'
    run.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (run / 'sections').symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException) as exc:
        _publish_completed(root)
    assert exc.value.status_code == 422
    assert list(outside.iterdir()) == []
    assert not (run / 'manifest.json').exists()


# ── get ─────────────────────────────────────────────────────────────────

def test_get_completed_run_returns_values_units_and_section(roots):
    _three_runs(roots['root_for'](PROJECT))
    out = re_.get_run_evidence('run-completed', section='summary')
    assert out['run_id'] == 'run-completed'
    assert out['status'] == 'completed'
    assert out['mode'] == 'lopf' and out['fingerprint'] == 'fp-completed'
    assert out['started_at'] == T_COMPLETED
    assert out['metrics']['objective'] == {'value': 1234.5, 'unit': 'EUR'}
    assert out['section'] == 'summary'
    assert out['data']['peak_load'] == {'value': 88.0, 'unit': 'MW'}


def test_get_without_section_lists_available_sections(roots):
    _three_runs(roots['root_for'](PROJECT))
    out = re_.get_run_evidence('run-completed')
    assert out['sections'] == [{'name': 'summary', 'available': True}]
    assert 'data' not in out


def test_get_failed_run_reports_failure_without_artifacts(roots):
    _three_runs(roots['root_for'](PROJECT))
    out = re_.get_run_evidence('run-failed')
    assert out['status'] == 'failed'
    assert out['error']['error_kind'] == 'solver_infeasible'
    assert out['sections'] == []


def test_get_running_run_has_no_evidence_yet(roots):
    _three_runs(roots['root_for'](PROJECT))
    out = re_.get_run_evidence('run-running')
    assert out['status'] == 'running' and out['finished_at'] is None
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-running', section='summary')
    detail = exc.value.detail
    assert detail['error_kind'] == 'evidence_unavailable'
    assert detail['run_id'] == 'run-running' and detail['reason'] == 'run_not_finished'


def test_unknown_run_is_not_found_not_newest(roots):
    _three_runs(roots['root_for'](PROJECT))
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-nope')
    assert exc.value.status_code == 404
    assert exc.value.detail['error_kind'] == 'run_not_found'
    assert 'run-running' not in json.dumps(exc.value.detail)


@pytest.mark.parametrize('bad', ['../etc/passwd', 'a/b', '', '..', 'x' * 200, None, 5])
def test_malformed_run_id_never_touches_filesystem(roots, bad):
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence(bad)
    assert exc.value.status_code == 404
    assert exc.value.detail['error_kind'] == 'run_not_found'


def test_missing_artifact_is_typed_unavailable_never_newest_detail(roots):
    root = roots['root_for'](PROJECT)
    _three_runs(root)
    _publish_completed(root, run_id='run-newest-ok', started_at='2026-10-04T10:00:00+00:00')
    # Old run loses its artifacts (retention, manual cleanup, ...).
    for f in (root / 'run_evidence' / 'run-completed' / 'sections').glob('*'):
        f.unlink()
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-completed', section='summary')
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail['error_kind'] == 'evidence_unavailable'
    assert detail['run_id'] == 'run-completed'
    assert detail['section'] == 'summary'
    assert detail['reason'] == 'artifact_missing'
    blob = json.dumps(detail)
    assert 'run-newest-ok' not in blob and 'fp-newest' not in blob
    # And the summary view flags it rather than substituting anything.
    out = re_.get_run_evidence('run-completed')
    assert out['run_id'] == 'run-completed'
    assert out['sections'] == [{'name': 'summary', 'available': False}]
    listed = {r['run_id']: r for r in re_.list_project_runs()['items']}
    assert listed['run-completed']['evidence_available'] is False


def test_corrupt_artifact_is_unavailable(roots):
    root = roots['root_for'](PROJECT)
    _publish_completed(root)
    (root / 'run_evidence' / 'run-completed' / 'sections' / 'summary.json').write_text('{"tampered": 1}')
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-completed', section='summary')
    assert exc.value.detail['error_kind'] == 'evidence_unavailable'
    assert exc.value.detail['reason'] == 'artifact_corrupt'


def test_undeclared_section_is_unavailable_with_choices(roots):
    _publish_completed(roots['root_for'](PROJECT))
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-completed', section='dispatch')
    detail = exc.value.detail
    assert detail['error_kind'] == 'evidence_unavailable'
    assert detail['reason'] == 'section_not_recorded'
    assert detail['available_sections'] == ['summary']


def test_invalid_section_name_is_422(roots):
    _publish_completed(roots['root_for'](PROJECT))
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-completed', section='../summary')
    assert exc.value.status_code == 422


# ── scoping / ACL ───────────────────────────────────────────────────────

def test_runs_are_scoped_to_the_active_project(roots):
    _publish_completed(roots['root_for'](PROJECT))
    roots['project'] = OTHER_PROJECT
    assert re_.list_project_runs()['items'] == []
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-completed')
    assert exc.value.status_code == 404
    assert exc.value.detail['error_kind'] == 'run_not_found'


def test_manifest_copied_across_projects_is_not_served(roots):
    import shutil
    src = roots['root_for'](PROJECT)
    _publish_completed(src)
    dst = roots['root_for'](OTHER_PROJECT)
    shutil.copytree(src / 'run_evidence', dst / 'run_evidence')
    roots['project'] = OTHER_PROJECT
    assert re_.list_project_runs()['items'] == []
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('run-completed', section='summary')
    assert exc.value.detail['error_kind'] == 'run_not_found'


def test_acl_revocation_blocks_list_and_get(roots):
    _publish_completed(roots['root_for'](PROJECT))
    assert re_.list_project_runs()['total_count'] == 1
    roots['revoked'] = True
    with pytest.raises(HTTPException) as exc:
        re_.list_project_runs()
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException):
        re_.get_run_evidence('run-completed', section='summary')


# ── immutability / publication ──────────────────────────────────────────

def test_duplicate_identical_completion_is_idempotent(roots):
    root = roots['root_for'](PROJECT)
    first = _publish_completed(root)
    again = _publish_completed(root)
    assert again == first
    assert re_.list_project_runs()['total_count'] == 1


def test_conflicting_second_completion_is_refused_and_original_kept(roots):
    root = roots['root_for'](PROJECT)
    _publish_completed(root)
    with pytest.raises(HTTPException) as exc:
        re_.publish_run(
            root, PROJECT, run_id='run-completed', kind='simulation', status='failed',
            started_at=T_COMPLETED, finished_at=T_FAILED, mode='lopf',
            fingerprint='fp-completed', error={'error_kind': 'x', 'message': 'late'},
        )
    assert exc.value.status_code == 409
    assert exc.value.detail['error_kind'] == 'run_already_finalized'
    assert re_.get_run_evidence('run-completed')['status'] == 'completed'


def test_running_marker_is_replaced_by_terminal_manifest(roots):
    root = roots['root_for'](PROJECT)
    re_.start_run(root, PROJECT, run_id='r1', kind='simulation', started_at=T_RUNNING,
                  mode='lopf', fingerprint='fp1')
    assert re_.get_run_evidence('r1')['status'] == 'running'
    re_.publish_run(root, PROJECT, run_id='r1', kind='simulation', status='interrupted',
                    started_at=T_RUNNING, finished_at='2026-10-03T10:02:00+00:00',
                    mode='lopf', fingerprint='fp1')
    assert re_.get_run_evidence('r1')['status'] == 'interrupted'
    assert re_.list_project_runs()['total_count'] == 1


def test_failed_publication_leaves_no_partial_run(roots, monkeypatch):
    root = roots['root_for'](PROJECT)

    def boom(*a, **k):
        raise OSError('disk full')

    with monkeypatch.context() as patch:
        patch.setattr(re_.os, 'replace', boom)
        with pytest.raises(OSError):
            _publish_completed(root)
    assert re_.list_project_runs()['items'] == []
    assert not list((root / 'run_evidence').rglob('*.tmp'))


def test_invalid_status_and_nonfinite_numbers_refused(roots):
    root = roots['root_for'](PROJECT)
    with pytest.raises(HTTPException) as exc:
        re_.publish_run(root, PROJECT, run_id='r', kind='simulation', status='weird',
                        started_at=T_RUNNING, finished_at=T_RUNNING)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        _publish_completed(root, run_id='r2', metrics_override=None) if False else re_.publish_run(
            root, PROJECT, run_id='r3', kind='simulation', status='completed',
            started_at=T_RUNNING, finished_at=T_RUNNING,
            metrics={'x': {'value': float('nan'), 'unit': 'MW'}},
        )
    assert re_.list_project_runs()['items'] == []


def test_oversized_section_refused(roots):
    root = roots['root_for'](PROJECT)
    with pytest.raises(HTTPException) as exc:
        re_.publish_run(root, PROJECT, run_id='big', kind='simulation', status='completed',
                        started_at=T_RUNNING, finished_at=T_RUNNING,
                        sections={'summary': {'blob': 'x' * (re_.MAX_SECTION_BYTES + 1)}})
    assert exc.value.status_code == 413
    assert exc.value.detail['error_kind'] == 'evidence_too_large'


# ── retention ───────────────────────────────────────────────────────────

def test_retention_prunes_old_artifacts_but_keeps_manifest(roots, monkeypatch):
    monkeypatch.setattr(re_, 'MAX_RUNS_WITH_ARTIFACTS', 2)
    root = roots['root_for'](PROJECT)
    for i in range(1, 4):
        _publish_completed(root, run_id=f'r{i}', started_at=f'2026-10-0{i}T10:00:00+00:00')
    items = {r['run_id']: r for r in re_.list_project_runs()['items']}
    assert items['r1']['evidence_available'] is False
    assert items['r2']['evidence_available'] is True and items['r3']['evidence_available'] is True
    with pytest.raises(HTTPException) as exc:
        re_.get_run_evidence('r1', section='summary')
    assert exc.value.detail['error_kind'] == 'evidence_unavailable'
    assert exc.value.detail['run_id'] == 'r1'
    assert re_.get_run_evidence('r3', section='summary')['run_id'] == 'r3'


# ── chat-tool parity ────────────────────────────────────────────────────

@pytest.mark.parametrize('name', ['list_project_runs', 'get_run_evidence'])
def test_tools_registered_in_catalogue_dispatchers_and_endpoint_map(name):
    tool = next(t for t in catalogue.TOOLS if t['name'] == name)
    assert tool['input_schema']['type'] == 'object'
    assert callable(chat_tools.DISPATCHERS[name])
    assert name in catalogue.TOOL_ROUTES
    assert catalogue.safety_tier_for(name) == 'read'


def test_get_run_evidence_schema_requires_run_id():
    tool = next(t for t in catalogue.TOOLS if t['name'] == 'get_run_evidence')
    assert tool['input_schema']['required'] == ['run_id']
    assert 'section' in tool['input_schema']['properties']


def test_dispatchers_serve_the_same_results(roots):
    _three_runs(roots['root_for'](PROJECT))
    listed = chat_tools.DISPATCHERS['list_project_runs'](limit=3)
    assert [r['run_id'] for r in listed['items']] == ['run-running', 'run-failed', 'run-completed']
    got = chat_tools.DISPATCHERS['get_run_evidence'](run_id='run-completed', section='summary')
    assert got['data']['total_cost']['value'] == 1234.5
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS['get_run_evidence'](run_id='run-nope')
    assert exc.value.detail['error_kind'] == 'run_not_found'
