"""Issue 10 first slice — persist typed experiments without hidden execution."""
from __future__ import annotations

import json

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
    assert failure.value.status_code == 422
    assert failure.value.detail['error_kind'] == 'invalid_experiment_change'


def test_string_and_enum_changes_round_trip(roots):
    out = ex.start_experiment(
        title='  Carrier case  ',
        domain='reliability',
        cases=[{
            'id': 'carrier',
            'changes': [
                {
                    'path': 'Generator.wind.carrier',
                    'value': 'wind',
                    'value_type': 'enum',
                },
                {
                    'path': 'Bus.north.name',
                    'value': 'North',
                    'value_type': 'string',
                    'unit': None,
                },
            ],
        }],
        request_id='str-1',
        limits={'max_cost': 10},
    )
    loaded = ex.get_experiment(out['experiment_id'])
    assert out['title'] == 'Carrier case'
    assert loaded['domain'] == 'reliability'
    assert loaded['cases'][0]['changes'][0] == {
        'path': 'Generator.wind.carrier',
        'value': 'wind',
        'value_type': 'enum',
    }
    assert loaded['cases'][0]['changes'][1] == {
        'path': 'Bus.north.name',
        'value': 'North',
        'value_type': 'string',
    }
    assert loaded['limits'] == {'max_cost': 10}
    assert loaded['executed'] is False


@pytest.mark.parametrize('change', [
    {'path': 'Generator.battery.p_nom', 'value': True, 'value_type': 'number'},
    {'path': 'Generator.battery.p_nom', 'value': False, 'value_type': 'number'},
    {'path': 'Generator.battery.p_nom', 'value': float('inf'), 'value_type': 'number'},
    {'path': 'Generator.battery.p_nom', 'value': float('nan'), 'value_type': 'number'},
    {'path': '../secret.key', 'value': 1, 'value_type': 'number'},
    {'path': 'Generator/battery.p_nom', 'value': 1, 'value_type': 'number'},
    {'path': 'only_one_segment', 'value': 1, 'value_type': 'number'},
    {'path': 'Generator.battery.p_nom', 'value': '', 'value_type': 'string'},
    {'path': 'Generator.battery.p_nom', 'value': 'x' * 121, 'value_type': 'enum'},
    {
        'path': 'Generator.battery.p_nom',
        'value': 1,
        'value_type': 'number',
        'unit': 'M' * 25,
    },
])
def test_rejects_unsafe_or_non_finite_changes(roots, change):
    with pytest.raises(HTTPException) as failure:
        ex.start_experiment(
            title='Bad change',
            domain='network',
            cases=[{'id': 'c1', 'changes': [change]}],
            request_id='bad-change',
        )
    assert failure.value.status_code == 422
    assert failure.value.detail['error_kind'] == 'invalid_experiment_change'
    assert ex.list_experiments()['total_count'] == 0


def test_duplicate_case_ids_and_unknown_domain_refused(roots):
    with pytest.raises(HTTPException) as duplicate:
        ex.start_experiment(
            title='Dup',
            domain='network',
            cases=_cases()[:1] * 2,
            request_id='dup',
        )
    assert duplicate.value.status_code == 422
    assert duplicate.value.detail['error_kind'] == 'invalid_experiment_cases'

    with pytest.raises(HTTPException) as domain:
        ex.start_experiment(
            title='Domain',
            domain='finance',
            cases=_cases()[:1],
            request_id='domain',
        )
    assert domain.value.status_code == 422
    assert domain.value.detail['error_kind'] == 'invalid_experiment_domain'
    assert ex.list_experiments()['total_count'] == 0


def test_same_request_id_does_not_return_another_owners_plan(roots):
    first = ex.start_experiment(
        title='Owner A', domain='network', cases=_cases()[:1], request_id='shared',
    )
    roots['owner'] = OTHER
    second = ex.start_experiment(
        title='Owner B', domain='commercial', cases=_cases()[:1], request_id='shared',
    )
    assert second['experiment_id'] != first['experiment_id']
    assert second['title'] == 'Owner B'
    assert second['domain'] == 'commercial'
    with pytest.raises(HTTPException) as failure:
        ex.get_experiment(first['experiment_id'])
    assert failure.value.status_code == 404
    assert failure.value.detail['error_kind'] == 'experiment_not_found'
    assert [item['experiment_id'] for item in ex.list_experiments()['items']] == [
        second['experiment_id'],
    ]


def test_experiment_id_cannot_read_outside_the_experiments_directory(roots, tmp_path):
    secret = tmp_path / PROJECT / 'secret.json'
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(json.dumps({
        'experiment_id': 'leaked',
        'title': 'LEAKED-SECRET',
        'project_id': PROJECT,
        'owner_id': OWNER,
        'domain': 'network',
        'status': 'planned',
        'cases': [{'id': 'hidden', 'changes': []}],
    }))
    for experiment_id in ('../secret', r'..\secret', 'not-a-uuid'):
        with pytest.raises(HTTPException) as failure:
            ex.get_experiment(experiment_id)
        assert failure.value.status_code == 404
        assert failure.value.detail['error_kind'] == 'experiment_not_found'
        assert failure.value.detail.get('title') != 'LEAKED-SECRET'


def test_corrupt_index_does_not_block_a_new_plan(roots, tmp_path):
    index = tmp_path / PROJECT / 'experiments' / 'index.json'
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text('{not json')
    out = ex.start_experiment(
        title='After corruption',
        domain='gridspine',
        cases=_cases()[:1],
        request_id='fresh',
    )
    assert out['executed'] is False
    assert out['domain'] == 'gridspine'
    listed = ex.list_experiments()
    assert listed['total_count'] == 1
    assert listed['items'][0]['experiment_id'] == out['experiment_id']


@pytest.mark.parametrize('limit', [0, 21, True, False, 1.5, '10'])
def test_list_limit_must_be_an_integer_from_1_to_20(roots, limit):
    with pytest.raises(HTTPException) as failure:
        ex.list_experiments(limit=limit)
    assert failure.value.status_code == 422
    assert failure.value.detail['error_kind'] == 'invalid_experiment_limit'


def test_catalogue_and_dispatcher():
    for name in ('start_experiment', 'list_experiments', 'get_experiment'):
        assert name in chat_tools.DISPATCHERS
        assert name in toolsets.CONTROL_TOOLS
        assert catalogue.TOOL_ROUTES[name] == ['_service_call_']
        tool = next(t for t in catalogue.TOOLS if t['name'] == name)
        assert 'Safety:' in tool['description']
