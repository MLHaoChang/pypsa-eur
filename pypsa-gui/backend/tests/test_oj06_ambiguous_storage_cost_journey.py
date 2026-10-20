"""OJ-06 (VS-3): nothing selected, "Change the storage cost to 150."

Two storage components exist (Battery, a StorageUnit; H2 tank, a Store) and
"cost" names several fields. Tier A over each real provider wire: resolve the
storage reference (ambiguous), read both classes' cost fields from the native
schema, then ONE ask_user Choice naming each component with its class and
each editable cost field with its unit and basis. Nothing is written, no card
is shown and no derived field is offered. Only provider HTTP is mocked.
"""
import pytest

from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.oj_grid import NAME, build_oj_grid
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

MESSAGE = 'Change the storage cost to 150.'


@pytest.fixture
def oj_grid(client, session_ctx, install_network):
    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    try:
        yield
    finally:
        PyPSAService.reset_request_context(binding)


def _options(results):
    """Built only from this turn's native results, as the prompt asks."""
    names = {c['component_class']: c['name'] for c in results[0]['candidates']}
    options = []
    for schema in results[1:5]:
        klass = schema['component_class']
        for field in schema['items']:
            if not field['editable'] or 'asset' not in field['sources']:
                continue
            unit = field['unit_display'] or field['unit_storage'] or 'no unit'
            options.append({'label': f"{names[klass]} ({klass}): {field['label']}, {unit}",
                            'description': field['description']})
    return options


def _steps():
    return [lambda r: ('resolve_reference', {'target': 'component', 'component_class': 'storage'}),
            lambda r: ('get_component_schema', {'component_class': 'StorageUnit', 'query': 'cost', 'limit': 20}),
            lambda r: ('get_component_schema', {'component_class': 'StorageUnit', 'query': 'cost', 'limit': 20,
                                                'offset': r[1]['returned']}),
            lambda r: ('get_component_schema', {'component_class': 'Store', 'query': 'cost', 'limit': 20}),
            lambda r: ('get_component_schema', {'component_class': 'Store', 'query': 'cost', 'limit': 20,
                                                'offset': r[3]['returned']}),
            lambda r: ('ask_user', {'title': 'Which storage cost?',
                                    'question': 'Which component and which cost field should be 150?',
                                    'options': _options(r)}),
            lambda r: (None, {})]


@pytest.mark.parametrize('mode', ['expert', 'guided'])
@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_ambiguous_storage_cost_asks_one_choice(oj_grid, kind, model, base_url, auth, mode):
    before = PyPSAService.get_network().storage_units.copy(), PyPSAService.get_network().stores.copy()
    calls, results, frames, _ = run_journey(kind, model, base_url, auth, _steps(), MESSAGE, mode=mode,
                                            answer='Which one did you mean?')
    assert calls == ['resolve_reference', *['get_component_schema'] * 4, 'ask_user', None]

    # Two candidates, named by the native resolver, never guessed.
    assert results[0]['status'] == 'ambiguous'
    assert {(c['component_class'], c['name']) for c in results[0]['candidates']} == {
        ('StorageUnit', 'Battery'), ('Store', 'H2 tank')}
    assert results[2]['has_more'] is False and results[4]['has_more'] is False

    # One Choice: each editable cost field, with its unit and basis; no derived field.
    choices = [d for e, d in frames if e == 'choice_request']
    assert len(choices) == 1
    labels = [o['label'] for o in choices[0]['options']]
    assert labels == [o['label'] for o in _options(results)]
    assert 'Battery (StorageUnit): Power part: overnight cost, EUR/kW' in labels
    assert 'Battery (StorageUnit): Energy part: overnight cost, EUR/kWh' in labels
    assert 'H2 tank (Store): Overnight cost, EUR/kWh' in labels
    assert 'Battery (StorageUnit): Marginal cost, EUR/MWh' in labels
    assert len(labels) == 6  # the app-described fields; raw PyPSA columns only when named
    assert not [label for label in labels if 'Capital cost' in label]
    assert all(o.get('description') for o in choices[0]['options'])
    assert not any(o.get('recommended') for o in choices[0]['options'])

    # Nothing changed and no edit card was shown.
    assert not [d for e, d in frames if e == 'tool_pending_confirmation']
    assert PyPSAService.get_network().storage_units.equals(before[0])
    assert PyPSAService.get_network().stores.equals(before[1])
