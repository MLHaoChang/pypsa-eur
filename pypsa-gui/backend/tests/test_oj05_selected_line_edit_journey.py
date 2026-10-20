"""OJ-05 (VS-3): L2 selected, "Help me update the parameters of this line: s_nom 400."

Tier A: the `oj-grid` fixture solved once by real HiGHS, then a scripted model
over each real provider wire: resolve "this line" from the selection -> one
update_component. The edit card (owner Q3) must show L2 s_nom old -> 400 MVA
before anything changes, in Expert and Guided alike; after approval the native
read-back equals 400, undo is offered and the results are called stale. Only
provider HTTP is mocked.
"""
import pytest

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.oj_grid import NAME, build_oj_grid
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

MESSAGE = 'Help me update the parameters of this line: s_nom 400.'
SELECTED = {'selected_component': {'class': 'Line', 'name': 'L2'}}


@pytest.fixture
def oj_grid(client, session_ctx, install_network, monkeypatch):
    import harness.confirm as harness_confirm

    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            _solve_once(session)
        chat_tools.set_chat_session(None)
        yield
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def _steps():
    return [lambda r: ('resolve_reference', {'target': 'component', 'component_class': 'Line'}),
            lambda r: ('update_component', {'component_class': r[0]['target']['component_class'],
                                            'name': r[0]['target']['name'], 'attrs': {'s_nom': 400}}),
            lambda r: (None, {})]


def _decide(decision, cards):
    """The browser's half: read the card while the write is blocked, then decide."""
    def on_frame(event, data):
        if event == 'tool_pending_confirmation':
            cards.append({'card': data, 's_nom': float(PyPSAService.get_network().lines.at['L2', 's_nom'])})
            chat_tools.chat_session().record_decision(data['confirmation_token'], decision)
    return on_frame


def _s_nom():
    return float(PyPSAService.get_network().lines.at['L2', 's_nom'])


@pytest.mark.parametrize('mode', ['expert', 'guided'])
@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_selected_line_edit_shows_the_card_then_reads_back(oj_grid, kind, model, base_url, auth, mode):
    cards = []
    calls, results, frames, session = run_journey(kind, model, base_url, auth, _steps(), MESSAGE, mode=mode,
                                                  on_frame=_decide('approve', cards),
                                                  ui_context_extra=SELECTED)
    assert calls == ['resolve_reference', 'update_component', None]
    # "this line" is the selection, and the words say so.
    assert results[0]['status'] == 'resolved' and results[0]['via'] == 'selected'
    assert results[0]['target'] == {'component_class': 'Line', 'name': 'L2'}

    # One card, before the write, with the exact old -> new and unit.
    assert len(cards) == 1
    card = cards[0]['card']['edit_card']
    assert cards[0]['s_nom'] == 300.0
    assert card['version'] == 1 and card['kind'] == 'parameter_edit'
    typed = [row for row in card['rows'] if row['role'] == 'typed']
    assert typed == [{'component_class': 'Line', 'name': 'L2', 'field': 's_nom', 'label': typed[0]['label'],
                      'old': 300.0, 'new': 400.0, 'unit': 'MVA', 'role': 'typed'}]
    assert card['says'] == 'L2 s_nom 300 → 400 MVA'

    # Native read-back equals 400; the receipt says so, offers undo and calls results stale.
    assert _s_nom() == 400.0
    receipt = results[1]['_edit_receipt']
    assert receipt['outcome'] == 'verified' and receipt['digest'] == card['digest']
    assert receipt['rows'][0]['readback'] == 400.0
    assert receipt['undo'] == {'tool': 'undo_last', 'available': True}
    assert receipt['results_stale'] is True
    assert not [d for e, d in frames if e in {'error', 'tool_error'}]


@pytest.mark.parametrize('mode', ['expert', 'guided'])
def test_a_denied_edit_changes_nothing(oj_grid, mode):
    cards = []
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _steps(), MESSAGE, mode=mode,
                                            on_frame=_decide('deny', cards), ui_context_extra=SELECTED)
    assert len(cards) == 1 and _s_nom() == 300.0
    assert '_edit_receipt' not in (results[1] or {})


def test_undo_restores_the_old_value(oj_grid):
    cards = []
    _, _, _, session = run_journey(*PROVIDERS[1], _steps(), MESSAGE, on_frame=_decide('approve', cards),
                                   ui_context_extra=SELECTED)
    assert _s_nom() == 400.0
    calls, results, frames, _ = run_journey(*PROVIDERS[1], [lambda r: ('undo_last', {}), lambda r: (None, {})],
                                            'Undo that.', session=session, on_frame=_decide('approve', cards))
    assert calls == ['undo_last', None]
    assert _s_nom() == 300.0
    # The undo card has the same old -> new shape, and its receipt reads back 300.
    undo = cards[1]['card']['edit_card']
    restored = [(r['component_class'], r['name'], r['field'], r['old'], r['new']) for r in undo['rows']]
    # The write had also created carrier AC (shown on its card); undo removes it.
    assert restored == [('Carrier', 'AC', 'exists', True, False), ('Line', 'L2', 's_nom', 400.0, 300.0)]
    assert undo['says'] == 'removes Carrier AC; L2 s_nom 400 → 300 MVA'
    assert results[0]['_edit_receipt']['outcome'] == 'verified'



@pytest.mark.parametrize('attrs,code', [
    ({'s_nom': float('nan')}, 'validation_error'),  # refused at argument parsing, upstream
    ({'s_nom': 'four hundred'}, 'invalid_edit_value'),
    ({'capital_cost': 5.0}, 'derived_field'),
    ({'not_a_field': 1.0}, 'unknown_field'),
])
def test_unsupported_edits_refuse_before_any_card(oj_grid, attrs, code):
    cards = []
    steps = [lambda r: ('update_component', {'component_class': 'Line', 'name': 'L2', 'attrs': attrs}),
             lambda r: (None, {})]
    calls, results, frames, _ = run_journey(*PROVIDERS[1], steps, MESSAGE, on_frame=_decide('approve', cards))
    assert cards == [] and _s_nom() == 300.0
    errors = [d for e, d in frames if e == 'tool_error']
    assert errors and code in str(errors[0]), errors


def test_an_extendable_l2_refuses_before_any_card(oj_grid):
    """GK change 2: the optimiser chooses an extendable line's s_nom, so the
    edit refuses with the bounds instead of a card that would change nothing."""
    PyPSAService.get_network().lines.at['L2', 's_nom_extendable'] = True
    cards = []
    calls, results, frames, _ = run_journey(*PROVIDERS[1], _steps(), MESSAGE, on_frame=_decide('approve', cards),
                                            ui_context_extra=SELECTED)
    assert cards == [] and _s_nom() == 300.0
    errors = [d for e, d in frames if e == 'tool_error']
    assert errors and errors[0]['error_kind'] == 'extendable_capacity', errors
