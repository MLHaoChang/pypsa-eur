"""LC-03: one typed registry over every result view (LC-C3).

Each Results tab declares the read that grounds it, how the browser can prove
it is shown, its labels per mode and what it may not claim. A tab added to
the catalogue without an entry fails here, as does an entry whose read is
not a real read-tier tool.
"""
import dataclasses

import pytest

from harness.catalogue import RESULTS_TAB_ENUM, TOOLS, safety_tier_for
from services import result_views as rv

_TOOLS = {t['name']: t for t in TOOLS}


def test_every_results_tab_has_exactly_one_view():
    tabs = [v.tab for v in rv.RESULT_VIEWS if v.panel == 'results']
    assert sorted(tabs) == sorted(RESULTS_TAB_ENUM)
    assert len(set(v.view_id for v in rv.RESULT_VIEWS)) == len(rv.RESULT_VIEWS)


def test_views_are_immutable():
    view = rv.view('loadflow')
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.label = 'x'


@pytest.mark.parametrize('view', [v for v in rv.RESULT_VIEWS if v.live()], ids=lambda v: v.view_id)
def test_every_live_view_reads_through_real_read_tools(view):
    assert view.reads, f'{view.view_id} has no read'
    for read in view.reads:
        assert read.tool in _TOOLS, read.tool
        assert safety_tier_for(read.tool) == 'read', read.tool
        schema = _TOOLS[read.tool]['input_schema']
        assert set(read.args) <= set(schema.get('properties', {})), (read.tool, read.args)
        for key, value in read.args.items():
            enum = schema['properties'][key].get('enum')
            assert enum is None or value in enum, (read.tool, key, value)


def test_load_flow_is_the_only_view_that_proves_displayed_data_today():
    assert {v.view_id for v in rv.RESULT_VIEWS if v.verification == 'displayed_data'} == {'loadflow'}
    lf = rv.view('loadflow')
    assert [r.tool for r in lf.reads] == ['get_loadflow_summary']
    assert 'active-power proxy' in lf.claim_limits.lower()


def test_guided_labels_follow_guided_tabs():
    guided = {v.tab for v in rv.RESULT_VIEWS if v.guided_label}
    assert guided == {'summary', 'adequacy', 'fmea', 'flows'}


def test_gated_views_wait_for_their_tools():
    """Profile-generator reads (PR377) go live only once their tools exist."""
    gated = [v for v in rv.RESULT_VIEWS if v.requires_tools]
    assert gated, 'the renewable-profile views must be declared'
    for v in gated:
        assert v.live() == (set(v.requires_tools) <= set(_TOOLS))
    profile = {t for v in gated for t in v.requires_tools}
    assert {'preview_renewable_profile', 'list_pending_profiles'} <= profile


def test_no_historical_section_is_claimed_before_it_is_captured():
    assert all(v.historical_section is None for v in rv.RESULT_VIEWS)


def test_every_view_declares_its_units():
    assert all(v.units for v in rv.RESULT_VIEWS)


def test_a_results_view_id_is_its_tab():
    """list_result_views sends only the id; ui_open_panel takes it as results_tab."""
    assert all(v.view_id == v.tab for v in rv.RESULT_VIEWS if v.panel == 'results')
