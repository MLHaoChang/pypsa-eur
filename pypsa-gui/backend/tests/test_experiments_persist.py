"""Issue 10 first slice — persist typed experiments without hidden execution."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness import catalogue, toolsets
from services import chat_tools
from services import experiments as ex

OWNER = 'owner-aaa'
PROJECT = '11111111-1111-1111-1111-111111111111'
OTHER = 'owner-bbb'


@pytest.fixture
def roots(tmp_path, monkeypatch):
    state = {'project': PROJECT, 'owner': OWNER}

    def authority():
        root = tmp_path / state['project']
        root.mkdir(parents=True, exist_ok=True)
        return root, state['project'], state['owner']

    monkeypatch.setattr(ex, '_authority', authority)
    ex._reset_for_tests()
    yield state
    ex._reset_for_tests()


def _cases():
    return [
        {
            'id': 'case-hi',
            'label': 'Higher battery cap',
            'changes': [
                {
                    'path': 'Generator.battery.p_nom',
                    'value': 7.5,
                    'value_type': 'number',
                    'unit': 'MW',
                },
            ],
        },
        {
            'id': 'case-flag',
            'label': 'Extendable',
            'changes': [
                {
                    'path': 'Generator.battery.p_nom_extendable',
                    'value': True,
                    'value_type': 'boolean',
                },
            ],
        },
    ]


def test_start_experiment_persists_without_executing(roots):
    out = ex.start_experiment(
        title='Battery sensitivity',
        domain='network',
        baseline_run_id='run-base',
        cases=_cases(),
        request_id='req-1',
    )
    assert out['status'] == 'planned'
    assert out['domain'] == 'network'
    assert out['baseline_run_id'] == 'run-base'
    assert out['case_count'] == 2
    assert out['executed'] is False
    loaded = ex.get_experiment(out['experiment_id'])
    assert loaded['cases'][0]['changes'][0]['value'] == 7.5
    assert loaded['cases'][0]['changes'][0]['unit'] == 'MW'
    assert loaded['cases'][1]['changes'][0]['value'] is True
    assert loaded['job_ids'] == []
    assert loaded['note'].startswith('Persisted only')


def test_request_id_deduplicates(roots):
    first = ex.start_experiment(
        title='A', domain='network', cases=_cases()[:1], request_id='same',
    )
    second = ex.start_experiment(
        title='B', domain='network', cases=_cases()[:1], request_id='same',
    )
    assert first['experiment_id'] == second['experiment_id']
    assert ex.list_experiments()['total_count'] == 1


def test_wrong_owner_cannot_read(roots):
    out = ex.start_experiment(
        title='A', domain='network', cases=_cases()[:1], request_id='r',
    )
    roots['owner'] = OTHER
    with pytest.raises(HTTPException) as failure:
        ex.get_experiment(out['experiment_id'])
    assert failure.value.detail['error_kind'] == 'experiment_not_found'


def test_invalid_value_type_refused(roots):
    with pytest.raises(HTTPException) as failure:
        ex.start_experiment(
            title='Bad',
            domain='network',
            cases=[{
                'id': 'c1',
                'changes': [{
                    'path': 'Generator.x.p_nom',
                    'value': 'not-a-number',
                    'value_type': 'number',
                }],
            }],
            request_id='bad',
        )
    assert failure.value.detail['error_kind'] == 'invalid_experiment_change'


def test_catalogue_and_dispatcher():
    for name in ('start_experiment', 'list_experiments', 'get_experiment'):
        assert name in chat_tools.DISPATCHERS
        assert name in toolsets.CONTROL_TOOLS
        assert catalogue.TOOL_ROUTES[name] == ['_service_call_']
        tool = next(t for t in catalogue.TOOLS if t['name'] == name)
        assert 'Safety:' in tool['description']
