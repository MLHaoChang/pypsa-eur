"""Edit-card cost rules the gatekeeper set (LC-04): a Store's overnight_cost
is annualised by PyPSA at solve time, so the card states the rule and the
inputs and claims no number; the solve then charges exactly that rule. A
StorageUnit's derived capital_cost is the same in the preview and the live
write on a staged (multi-period) template.

The Store is set through the card (update_component, approved), solved by the
ordinary run_simulation path, and its solved fixed cost is checked against
the hand formula overnight × annuity(r, lifetime) × nyears × e_nom.
"""
import json

import pytest

from project_templates import eh_templates
from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.oj_grid import build_oj_grid
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_tools import helpers_state  # noqa: F401


def _annuity(rate, years):
    return rate / (1 - (1 + rate) ** -years)


@pytest.fixture
def project(client, session_ctx, install_network, monkeypatch):
    import harness.confirm as harness_confirm

    cells = []

    def install(network, name):
        install_network(network)
        assert client.post(f'/api/projects/{name}').status_code == 200
        binding = PyPSAService.bind_request_context(session_ctx(client))
        cells.append((binding, PyPSAService.bind_turn_cell()))
        monkeypatch.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        return session

    yield install
    chat_tools.set_chat_session(None)
    for binding, cell in reversed(cells):
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _edit(session, args):
    frames, collector, cards = [], [], []
    for event, data in chat_service._dispatch_real_tool_call(
            session, {'id': 'tu-edit', 'name': 'update_component', 'input': args}, collector):
        frames.append((event, data))
        if event == 'tool_pending_confirmation':
            cards.append(data['edit_card'])
            session.record_decision(data['confirmation_token'], 'approve')
    assert not [d for e, d in frames if e == 'tool_error'], frames
    text = collector[-1]['content']
    text = text if isinstance(text, str) else text[0]['text']
    result, _ = json.JSONDecoder().raw_decode(text[text.index('{'):])
    return cards[0], result['_edit_receipt']


def _store_fixed_cost(name):
    """The solved fixed cost per MWh the LP charged, from the app's own cost facts
    (statistics.capex reads NaN after a solve: the app fills discount_rate only
    while solving)."""
    from services.solver.periodized_costs import periodized_capital_costs

    ctx = PyPSAService.get_active_context()
    return periodized_capital_costs(ctx.network, ctx.solver_state['solver_config'])['stores'][name]['fixed_cost']


def _nyears(network):
    nyears = network.nyears
    values = set(getattr(nyears, 'unique', lambda: [nyears])())
    assert len(values) == 1, values
    return float(values.pop())


def test_store_overnight_cost_single_year(project):
    n = build_oj_grid()
    n.stores.at['H2 tank', 'capital_cost'] = 3.0
    session = project(n, 'oj-store-cost')
    card, receipt = _edit(session, {'component_class': 'Store', 'name': 'H2 tank',
                                    'attrs': {'overnight_cost': 12_000.0, 'lifetime': 30.0}})
    notes = [r['note'] for r in card['rows'] if r['role'] == 'note']
    assert len(notes) == 1
    assert 'overnight_cost × annuity(discount rate 0.07' in notes[0] and 'lifetime 30.0' in notes[0]
    assert 'replaces the existing capital_cost 3 at solve time' in notes[0]
    assert not [r for r in card['rows'] if r['field'] == 'capital_cost' and r['role'] != 'note']
    assert receipt['outcome'] == 'verified'

    _solve_once(session)
    hours = len(PyPSAService.get_network().snapshots)
    hand = 12_000.0 * _annuity(0.07, 30) * hours / 8760  # per MWh over the 24 h horizon
    assert float(_store_fixed_cost('H2 tank')) == pytest.approx(hand, rel=1e-9)


def test_store_overnight_cost_staged_template(project):
    n = eh_templates.BUILDERS['eh_datacenter_staged']()
    bus = n.storage_units.bus.iloc[0]
    n.add('Store', 'tank', bus=bus, e_nom=100.0, build_year=2027)
    session = project(n, 'oj-store-staged')
    card, receipt = _edit(session, {'component_class': 'Store', 'name': 'tank',
                                    'attrs': {'overnight_cost': 12_000.0, 'lifetime': 30.0}})
    assert [r['role'] for r in card['rows'] if r['field'] == 'overnight_cost'] == ['typed', 'note']
    assert receipt['outcome'] == 'verified'

    _solve_once(session)
    hand = 12_000.0 * _annuity(0.07, 30) * _nyears(PyPSAService.get_network())
    assert float(_store_fixed_cost('tank')) == pytest.approx(hand, rel=1e-9)


def test_storage_unit_derive_matches_preview_on_a_staged_template(project):
    session = project(eh_templates.BUILDERS['eh_datacenter_staged'](), 'oj-su-staged')
    name = 'bess_new_2030'
    card, receipt = _edit(session, {'component_class': 'StorageUnit', 'name': name,
                                    'attrs': {'inv_energy_overnight': 150_000.0, 'inv_energy_lifetime': 15.0}})
    previewed = next(r['new'] for r in card['rows'] if r['field'] == 'capital_cost')
    assert receipt['outcome'] == 'verified'
    assert float(PyPSAService.get_network().storage_units.at[name, 'capital_cost']) == previewed
    note = next(r['note'] for r in card['rows'] if r['role'] == 'note' and r['field'] == 'capital_cost')
    assert 'discount rate' in note


def test_storage_unit_overnight_cost_is_refused(project):
    session = project(build_oj_grid(), 'oj-su-overnight')
    frames, collector = [], []
    for event, data in chat_service._dispatch_real_tool_call(
            session, {'id': 'tu-x', 'name': 'update_component',
                      'input': {'component_class': 'StorageUnit', 'name': 'Battery',
                                'attrs': {'overnight_cost': 1.0}}}, collector):
        frames.append((event, data))
    errors = [d for e, d in frames if e == 'tool_error']
    assert errors and errors[0]['error_kind'] == 'cost_set_by_parts'
    assert not [d for e, d in frames if e == 'tool_pending_confirmation']
