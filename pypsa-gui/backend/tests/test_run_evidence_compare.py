"""Issue 06 — compare_project_runs: software deltas, units, incomparable runs.

Never invents numbers: only recorded metrics are compared; runs that disagree on
mode/fingerprint, did not complete, or lost their evidence yield no deltas.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness import catalogue, toolsets
from services import chat_tools
from services import run_evidence as re_

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'
OTHER_PROJECT = '22222222-2222-2222-2222-222222222222'


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


def _publish(root, run_id, *, project=PROJECT, mode='lopf', fingerprint='fp-a',
             status='completed', started='2026-10-01T10:00:00+00:00', metrics=None,
             warnings=None, sections=True):
    kwargs = {}
    if status == 'completed':
        kwargs['metrics'] = metrics if metrics is not None else {
            'objective': {'value': 1000.0, 'unit': 'EUR'},
            'solve_seconds': {'value': 40.0, 'unit': 's'},
        }
        if sections:
            kwargs['sections'] = {'summary': {'peak_load': {'value': 88.0, 'unit': 'MW'}}}
    else:
        kwargs['error'] = {'error_kind': 'solver_infeasible', 'message': 'Infeasible.'}
    return re_.publish_run(
        root, project, run_id=run_id, kind='simulation', status=status,
        started_at=started, finished_at=started.replace('10:00:00', '10:05:00'),
        mode=mode, fingerprint=fingerprint, warnings=warnings, **kwargs,
    )


def test_deltas_are_run_minus_baseline_with_units(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base')
    _publish(root, 'new', started='2026-10-02T10:00:00+00:00', metrics={
        'objective': {'value': 1250.0, 'unit': 'EUR'},
        'solve_seconds': {'value': 30.0, 'unit': 's'},
    })
    out = re_.compare_project_runs('new', 'base')
    assert out['comparable'] is True and out['incomparable_reasons'] == []
    assert out['mode'] == 'lopf' and out['fingerprints_match'] is True
    assert out['inputs_differ'] is False
    assert out['deltas']['objective'] == {
        'run': {'value': 1250.0, 'unit': 'EUR'},
        'baseline': {'value': 1000.0, 'unit': 'EUR'},
        'delta': {'value': 250.0, 'unit': 'EUR'},
        'delta_pct': 25.0,
    }
    assert out['deltas']['solve_seconds']['delta'] == {'value': -10.0, 'unit': 's'}
    assert out['deltas']['solve_seconds']['delta_pct'] == -25.0
    assert out['run']['run_id'] == 'new' and out['baseline']['run_id'] == 'base'
    assert out['run']['started_at'] == '2026-10-02T10:00:00+00:00'
    assert out['unmatched_metrics'] == []


def test_zero_baseline_has_null_percentage_not_infinity(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', metrics={'curtailment': {'value': 0, 'unit': 'MWh'}})
    _publish(root, 'new', metrics={'curtailment': {'value': 5, 'unit': 'MWh'}})
    d = re_.compare_project_runs('new', 'base')['deltas']['curtailment']
    assert d['delta'] == {'value': 5, 'unit': 'MWh'} and d['delta_pct'] is None


def test_mode_mismatch_is_incomparable_with_no_deltas(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', mode='lopf')
    _publish(root, 'new', mode='ac_pf')
    out = re_.compare_project_runs('new', 'base')
    assert out['comparable'] is False and out['deltas'] == {}
    assert out['mode'] is None
    assert {'reason': 'mode_mismatch', 'run': 'ac_pf', 'baseline': 'lopf'} in out['incomparable_reasons']
    assert '1000' not in json.dumps(out['deltas'])


def test_mode_mismatch_not_overridable_by_allow_input_changes(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', mode='lopf')
    _publish(root, 'new', mode='ac_pf', fingerprint='fp-b')
    out = re_.compare_project_runs('new', 'base', allow_input_changes=True)
    assert out['comparable'] is False and out['deltas'] == {}
    assert [r['reason'] for r in out['incomparable_reasons']] == ['mode_mismatch']


def test_fingerprint_mismatch_incomparable_unless_opted_in(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', fingerprint='fp-a')
    _publish(root, 'new', fingerprint='fp-b', metrics={
        'objective': {'value': 900.0, 'unit': 'EUR'}})
    refused = re_.compare_project_runs('new', 'base')
    assert refused['comparable'] is False and refused['deltas'] == {}
    assert refused['inputs_differ'] is True and refused['fingerprints_match'] is False
    assert refused['incomparable_reasons'] == [
        {'reason': 'fingerprint_mismatch', 'run': 'fp-b', 'baseline': 'fp-a'}]
    allowed = re_.compare_project_runs('new', 'base', allow_input_changes=True)
    assert allowed['comparable'] is True and allowed['inputs_differ'] is True
    assert allowed['deltas']['objective']['delta'] == {'value': -100.0, 'unit': 'EUR'}
    assert [m['metric'] for m in allowed['unmatched_metrics']] == ['solve_seconds']


def test_unknown_fingerprint_is_incomparable_not_assumed_equal(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', fingerprint=None)
    _publish(root, 'new', fingerprint=None)
    out = re_.compare_project_runs('new', 'base')
    assert out['comparable'] is False
    assert out['incomparable_reasons'][0]['reason'] == 'fingerprint_unknown'
    assert out['fingerprints_match'] is False


def test_unknown_mode_is_incomparable(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', mode=None)
    _publish(root, 'new', mode=None)
    out = re_.compare_project_runs('new', 'base')
    assert out['comparable'] is False
    assert out['incomparable_reasons'][0]['reason'] == 'mode_unknown'


def test_failed_and_running_runs_are_incomparable(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base')
    _publish(root, 'bad', status='failed', started='2026-10-02T10:00:00+00:00')
    re_.start_run(root, PROJECT, run_id='live', kind='simulation', mode='lopf',
                  fingerprint='fp-a', started_at='2026-10-03T10:00:00+00:00')
    failed = re_.compare_project_runs('bad', 'base')
    assert failed['comparable'] is False and failed['deltas'] == {}
    assert failed['incomparable_reasons'][0] == {
        'reason': 'run_not_completed', 'role': 'run', 'run_id': 'bad', 'status': 'failed'}
    running = re_.compare_project_runs('base', 'live')
    assert running['comparable'] is False
    assert running['incomparable_reasons'][0]['role'] == 'baseline'
    assert running['incomparable_reasons'][0]['status'] == 'running'


def test_unit_mismatch_and_missing_metrics_are_listed_not_estimated(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', metrics={
        'objective': {'value': 1.0, 'unit': 'MEUR'},
        'peak': {'value': 10.0, 'unit': 'MW'},
        'only_base': {'value': 3.0, 'unit': 'MW'},
    })
    _publish(root, 'new', metrics={
        'objective': {'value': 1000000.0, 'unit': 'EUR'},
        'peak': {'value': 12.0, 'unit': 'MW'},
    })
    out = re_.compare_project_runs('new', 'base')
    assert list(out['deltas']) == ['peak']
    assert out['deltas']['peak']['delta'] == {'value': 2.0, 'unit': 'MW'}
    unmatched = {m['metric']: m for m in out['unmatched_metrics']}
    assert unmatched['objective'] == {
        'metric': 'objective', 'reason': 'units_differ',
        'run_unit': 'EUR', 'baseline_unit': 'MEUR'}
    assert unmatched['only_base']['reason'] == 'not_recorded_in_both'
    assert unmatched['only_base']['recorded_in'] == ['baseline']


def test_metrics_filter_and_unrecorded_name(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base')
    _publish(root, 'new')
    out = re_.compare_project_runs('new', 'base', metrics=['objective', 'nonexistent'])
    assert list(out['deltas']) == ['objective']
    assert out['deltas']['objective']['delta']['value'] == 0.0
    assert out['unmatched_metrics'] == [{
        'metric': 'nonexistent', 'reason': 'not_recorded_in_both', 'recorded_in': []}]


def test_warnings_from_both_runs_are_surfaced(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base', warnings=['load shed'])
    _publish(root, 'new', warnings=['unsaved inputs'])
    out = re_.compare_project_runs('new', 'base')
    assert out['warnings'] == {'run': ['unsaved inputs'], 'baseline': ['load shed']}


def test_pruned_evidence_is_typed_unavailable_for_that_run_only(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base')
    _publish(root, 'new', started='2026-10-02T10:00:00+00:00')
    for f in (root / 'run_evidence' / 'base' / 'sections').glob('*'):
        f.unlink()
    with pytest.raises(HTTPException) as exc:
        re_.compare_project_runs('new', 'base')
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail['error_kind'] == 'evidence_unavailable'
    assert detail['run_id'] == 'base' and detail['reason'] == 'artifact_missing'
    assert 'new' not in json.dumps({k: v for k, v in detail.items() if k != 'message'})


def test_unknown_run_is_not_found_naming_the_id(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base')
    with pytest.raises(HTTPException) as exc:
        re_.compare_project_runs('base', 'ghost')
    assert exc.value.status_code == 404
    assert exc.value.detail['error_kind'] == 'run_not_found'
    assert exc.value.detail['run_id'] == 'ghost'


def test_other_project_run_is_not_found(roots):
    _publish(roots['root_for'](PROJECT), 'base')
    other = roots['root_for'](OTHER_PROJECT)
    _publish(other, 'foreign', project=OTHER_PROJECT)
    # A run id from another project's directory tree is invisible here.
    with pytest.raises(HTTPException) as exc:
        re_.compare_project_runs('foreign', 'base')
    assert exc.value.detail['error_kind'] == 'run_not_found'
    # Even if the manifest were copied across, its project_id binds it.
    import shutil
    shutil.copytree(other / 'run_evidence' / 'foreign',
                    roots['root_for'](PROJECT) / 'run_evidence' / 'foreign')
    with pytest.raises(HTTPException) as exc2:
        re_.compare_project_runs('foreign', 'base')
    assert exc2.value.detail['error_kind'] == 'run_not_found'


def test_acl_revocation_refuses(roots):
    root = roots['root_for'](PROJECT)
    _publish(root, 'base')
    _publish(root, 'new')
    roots['revoked'] = True
    with pytest.raises(HTTPException) as exc:
        re_.compare_project_runs('new', 'base')
    assert exc.value.status_code == 404


@pytest.mark.parametrize('kwargs', [
    {'run_id': 'a', 'baseline_run_id': 'a'},
    {'run_id': '../x', 'baseline_run_id': 'b'},
    {'run_id': 'a', 'baseline_run_id': None},
    {'run_id': 'a', 'baseline_run_id': 'b', 'metrics': []},
    {'run_id': 'a', 'baseline_run_id': 'b', 'metrics': ['bad name']},
    {'run_id': 'a', 'baseline_run_id': 'b', 'metrics': 'objective'},
    {'run_id': 'a', 'baseline_run_id': 'b', 'allow_input_changes': 'yes'},
])
def test_invalid_arguments_are_422(roots, kwargs):
    with pytest.raises(HTTPException) as exc:
        re_.compare_project_runs(**kwargs)
    assert exc.value.status_code == 422


def test_catalogue_dispatcher_route_and_toolset_parity():
    names = {t['name'] for t in catalogue.TOOLS}
    assert 'compare_project_runs' in names
    assert chat_tools.DISPATCHERS['compare_project_runs'] is re_.compare_project_runs
    assert 'compare_project_runs' in catalogue.TOOL_ROUTES
    assert catalogue.safety_tier_for('compare_project_runs') == 'read'
    assert 'compare_project_runs' in toolsets.CONTROL_TOOLS
    tool = next(t for t in catalogue.TOOLS if t['name'] == 'compare_project_runs')
    assert tool['input_schema']['required'] == ['run_id', 'baseline_run_id']
