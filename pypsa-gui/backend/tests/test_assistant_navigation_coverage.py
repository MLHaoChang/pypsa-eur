"""Phase A: real UI targets must be reachable through the shared harness."""
import pytest
from fastapi import HTTPException

from harness.catalogue import TOOLS
from services import chat_tools
from tests.test_chat_tools_schema_panels import _errors

RESULT_TABS = (
    'overview', 'capex', 'dispatch', 'loadflow', 'prices', 'economics',
    'emissions', 'curtailment', 'lostload', 'adequacy', 'storage', 'fmea',
    'investment', 'asset',
)
PANELS = (
    'timeseries', 'simparams', 'horizon', 'results', 'snapshots', 'issues',
    'overview', 'scenarios', 'compare', 'capacityBounds', 'solveQueue',
    'workspace', 'settings', 'gridspine', 'hubDesign', 'reports', 'campusElectrical',
)


def schema():
    return next(t['input_schema'] for t in TOOLS if t['name'] == 'ui_open_panel')


@pytest.mark.parametrize('tab', RESULT_TABS)
def test_assistant_can_request_every_actual_result_tab(tab):
    args = {'panel_id': 'results', 'results_tab': tab}
    assert _errors(schema(), args) == []
    assert chat_tools.ui_open_panel(**args) == {
        '_ui_event': True, 'kind': 'navigate', **args,
    }


@pytest.mark.parametrize('panel', PANELS)
def test_assistant_can_request_every_actual_panel(panel):
    assert _errors(schema(), {'panel_id': panel}) == []
    assert chat_tools.ui_open_panel(panel)['panel_id'] == panel


@pytest.mark.parametrize('args', [
    {'panel_id': 'unknown'},
    {'panel_id': 'results', 'results_tab': 'unknown'},
    {'panel_id': 'results', 'results_tab': ['fmea']},
    {'panel_id': 'results', 'bottom_tab': 'unknown'},
    {'panel_id': 'compare', 'compare_tab': 'unknown'},
    {'panel_id': None},
    {'panel_id': 'compare', 'compare_rail': 'false'},
    {'panel_id': 'compare', 'compare_a': 123},
])
def test_invalid_navigation_is_rejected_without_emitting_an_event(args):
    with pytest.raises(HTTPException) as failure:
        chat_tools.ui_open_panel(**args)
    assert failure.value.status_code == 400


@pytest.mark.parametrize('args', [
    {'panel_id': 'Results', 'results_tab': 'economics'},
    {'panel_id': 'HubDesign'},
    {'panel_id': 'BottomPanel', 'bottom_tab': 'Lines'},
    {'panel_id': 'Compare', 'compare_tab': 'loading', 'compare_rail': True},
])
def test_existing_navigation_aliases_and_controls_remain_supported(args):
    assert _errors(schema(), args) == []
    assert chat_tools.ui_open_panel(**args) == {'_ui_event': True, 'kind': 'navigate', **args}
