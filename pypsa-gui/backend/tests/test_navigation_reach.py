"""LC-14: validated read-only navigation to the native picker and edit cards."""
import json

import pandas as pd
import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools
from tests.test_chat_tools_schema_panels import _errors, _tool


@pytest.fixture
def navigation_network(client, install_network, bound_ctx):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2026-06-01', periods=24, freq='h'))
    n.add('Bus', 'B1')
    n.add('Bus', 'B2')
    n.add('Line', 'L1', bus0='B1', bus1='B2', x=0.1)
    install_network(n)
    with bound_ctx(client):
        yield n


def _refusal(call, field='snapshot_iso', status=400):
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == status
    detail = exc.value.detail
    assert detail['error_kind'] == 'invalid_navigation'
    assert detail['field'] == field
    assert detail['message'][0].isupper()
    assert 'snapshot_index' not in detail
    return detail['message']


def test_set_snapshot_returns_the_picker_index(navigation_network):
    event = chat_tools.ui_set_snapshot('2026-06-01T12:00:00')
    assert event == {'_ui_event': True, 'kind': 'set_snapshot',
                     'snapshot_iso': '2026-06-01T12:00:00', 'period': None, 'snapshot_index': 12}
    assert chat_tools.ui_set_snapshot('2026-06-01')['snapshot_index'] == 0


def test_set_snapshot_refuses_a_time_not_on_the_axis(navigation_network):
    for iso in ['2026-06-02T00:00:00', '2026-06-01T12:30:00', 'not a date']:
        message = _refusal(lambda: chat_tools.ui_set_snapshot(iso))
        assert '2026-06-01T00:00:00 … 2026-06-01T23:00:00' in message
    navigation_network.set_snapshots(pd.DatetimeIndex([]))
    assert 'no snapshots' in _refusal(lambda: chat_tools.ui_set_snapshot('2026-06-01'))


def test_set_snapshot_multi_period(navigation_network):
    n = navigation_network
    n.set_snapshots(pd.MultiIndex.from_product([
        [2030, 2040], pd.date_range('2030-01-01', periods=3, freq='h'),
    ]))
    n.investment_periods = [2030, 2040]
    iso = '2030-01-01T01:00:00'
    assert chat_tools.ui_set_snapshot(iso, period=2040)['snapshot_index'] == 4
    assert chat_tools.ui_set_snapshot('2040|' + iso)['snapshot_index'] == 4
    message = _refusal(lambda: chat_tools.ui_set_snapshot(iso))
    assert '2030' in message and '2040' in message
    _refusal(lambda: chat_tools.ui_set_snapshot('2030|' + iso, period=2040))
    from routers.network_time_axis import get_snapshots
    body = get_snapshots()
    assert body['snapshots'][4] == iso
    assert body['periods'][4] == 2040
    n.set_snapshots(pd.MultiIndex.from_tuples([
        (period, ts) for period in [2030, 2040]
        for ts in pd.date_range(f'{period}-01-01', periods=3, freq='h')
    ]))
    assert chat_tools.ui_set_snapshot('2040-01-01T01:00:00')['snapshot_index'] == 4


def test_set_snapshot_time_zone_mismatch_is_refused(navigation_network):
    for iso in ['2026-06-01T12:00:00Z', '2026-06-01T14:00:00+02:00']:
        message = _refusal(lambda: chat_tools.ui_set_snapshot(iso))
        assert 'time axis has no time zone' in message
        assert '2026-06-01T00:00:00 … 2026-06-01T23:00:00' in message
        assert 'not on the time axis' not in message


def test_open_editor_emits_open_properties(navigation_network):
    assert chat_tools.ui_select_component('Line', 'L1', open_editor=True) == {
        '_ui_event': True, 'kind': 'open_properties', 'component_class': 'Line', 'name': 'L1',
    }
    old = {'_ui_event': True, 'kind': 'select_component', 'component_class': 'Line', 'name': 'L1'}
    assert json.dumps(chat_tools.ui_select_component('Line', 'L1')) == json.dumps(old)
    assert json.dumps(chat_tools.ui_select_component('Line', 'L1', open_editor=False)) == json.dumps(old)


def test_open_editor_refusals(navigation_network):
    _refusal(lambda: chat_tools.ui_select_component('Carrier', 'AC', open_editor=True), 'component_class')
    with pytest.raises(HTTPException) as exc:
        chat_tools.ui_select_component('Line', 'nope', open_editor=True)
    assert exc.value.status_code == 404
    assert exc.value.detail['error_kind'] == 'component_not_found'
    assert exc.value.detail['component_class'] == 'Line'
    assert exc.value.detail['name'] == 'nope'
    assert exc.value.detail['message'][0].isupper()
    for bad in ['true', 1]:
        _refusal(lambda: chat_tools.ui_select_component('Line', 'L1', open_editor=bad), 'open_editor')


def test_canvas_view_pairs():
    for view in ['hybrid', 'site', 'satellite']:
        assert chat_tools.ui_open_panel('map', canvas_view=view)['canvas_view'] == view
    assert chat_tools.ui_open_panel('topology', canvas_view='blank')['canvas_view'] == 'blank'
    for panel, view in [('topology', 'hybrid'), ('results', 'site'), ('map', 'nope')]:
        _refusal(lambda: chat_tools.ui_open_panel(panel, canvas_view=view), 'canvas_view')


def test_schema_matches_dispatchers():
    from harness.catalogue import TOOL_ROUTES, _UI_EVENT, safety_tier_for

    for name, args in [
        ('ui_select_component', {'component_class': 'Line', 'name': 'L1', 'open_editor': True}),
        ('ui_open_panel', {'panel_id': 'map', 'canvas_view': 'hybrid'}),
        ('ui_set_snapshot', {'snapshot_iso': '2026-06-01', 'period': 2030}),
    ]:
        assert _errors(_tool(name)['input_schema'], args) == []
        assert safety_tier_for(name) == 'read'
        assert TOOL_ROUTES[name] == _UI_EVENT
    assert 'canvas_view' in _tool('ui_open_panel')['input_schema']['properties']
    assert _errors(_tool('ui_select_component')['input_schema'], {
        'component_class': 'Line', 'name': 'L1', 'open_editor': 'true',
    })
