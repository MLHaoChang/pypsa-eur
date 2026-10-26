"""LC-14 Tier A: native navigation dispatch/frames over every mocked provider wire."""
import pytest

from services.pypsa_service import PyPSAService
from tests.journey_wire import run_journey
from tests.oj_grid import NAME, build_oj_grid
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401


@pytest.fixture
def oj_grid(client, session_ctx, install_network):
    install_network(build_oj_grid())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    ctx = session_ctx(client)
    binding = PyPSAService.bind_request_context(ctx)
    cell = PyPSAService.bind_turn_cell()
    try:
        yield
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


@pytest.mark.parametrize('mode', ['expert', 'guided'])
@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[p[0] for p in PROVIDERS])
class TestNavigationJourney:
    def test_chat_sets_the_snapshot_it_names(self, oj_grid, client, kind, model, base_url, auth, mode):
        steps = [lambda r: ('ui_set_snapshot', {'snapshot_iso': '2026-06-01T12:00:00'}), lambda r: (None, {})]
        calls, results, frames, _ = run_journey(kind, model, base_url, auth, steps, 'Show 12:00 on the canvas.', mode=mode)
        assert calls == ['ui_set_snapshot', None]
        events = [d for e, d in frames if e == 'ui_event']
        assert len(events) == 1
        frame = events[0]
        assert frame['kind'] == 'set_snapshot'
        assert frame['snapshot_index'] == 12
        assert results[0]['snapshot_index'] == 12
        assert not [d for e, d in frames if e == 'tool_error']
        body = client.get('/api/network/snapshots').json()
        assert body['snapshots'][frame['snapshot_index']] == frame['snapshot_iso']

    def test_chat_refuses_a_snapshot_not_on_the_axis(self, oj_grid, kind, model, base_url, auth, mode):
        steps = [lambda r: ('ui_set_snapshot', {'snapshot_iso': '2026-06-02T00:00:00'}), lambda r: (None, {})]
        _, _, frames, _ = run_journey(kind, model, base_url, auth, steps, 'Show tomorrow on the canvas.', mode=mode)
        errors = [d for e, d in frames if e == 'tool_error']
        assert len(errors) == 1
        assert errors[0]['error_kind'] == 'invalid_navigation'
        assert not [d for e, d in frames if e == 'ui_event']

    def test_chat_opens_the_edit_card(self, oj_grid, kind, model, base_url, auth, mode):
        before = float(PyPSAService.get_network().lines.at['L2', 's_nom'])
        steps = [lambda r: ('ui_select_component', {'component_class': 'Line', 'name': 'L2', 'open_editor': True}),
                 lambda r: (None, {})]
        _, results, frames, _ = run_journey(kind, model, base_url, auth, steps, 'Open the edit form for L2.', mode=mode)
        events = [d for e, d in frames if e == 'ui_event']
        assert len(events) == 1
        assert events[0] == {'kind': 'open_properties', 'component_class': 'Line', 'name': 'L2'}
        assert results[0]['name'] == 'L2'
        assert results[0]['component_class'] == 'Line'
        assert not [d for e, d in frames if e in {'tool_pending_confirmation', 'tool_error'}]
        assert float(PyPSAService.get_network().lines.at['L2', 's_nom']) == before

    def test_chat_switches_to_hybrid_and_site(self, oj_grid, kind, model, base_url, auth, mode):
        steps = [lambda r: ('ui_open_panel', {'panel_id': 'map', 'canvas_view': 'hybrid'}),
                 lambda r: ('ui_open_panel', {'panel_id': 'map', 'canvas_view': 'site'}), lambda r: (None, {})]
        _, results, frames, _ = run_journey(kind, model, base_url, auth, steps, 'Show hybrid, then Site 3D.', mode=mode)
        events = [d for e, d in frames if e == 'ui_event']
        assert [e['canvas_view'] for e in events] == ['hybrid', 'site']
        assert [r['canvas_view'] for r in results] == ['hybrid', 'site']
