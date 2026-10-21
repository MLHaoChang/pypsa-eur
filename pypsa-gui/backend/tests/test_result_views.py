"""LC-03: one typed registry over every result view (LC-C3).

Each Results tab declares the read that grounds it, how the browser can prove
it is shown, its labels per mode and what it may not claim. A tab added to
the catalogue without an entry fails here, as does an entry whose read is
not a real read-tier tool.
"""
import dataclasses
import json
from pathlib import Path

import pytest

from harness.catalogue import RESULTS_TAB_ENUM, TOOLS, safety_tier_for
from services import chat_service, chat_tools
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
    assert guided == {'summary', 'adequacy', 'fmea', 'flows', 'loadshape'}


def test_gated_views_wait_for_their_tools():
    """Profile-generator reads (PR377) go live only once their tools exist."""
    gated = [v for v in rv.RESULT_VIEWS if v.requires_tools]
    assert gated, 'the renewable-profile views must be declared'
    for v in gated:
        assert v.live() == (set(v.requires_tools) <= set(_TOOLS))
    profile = {t for v in gated for t in v.requires_tools}
    assert {'preview_renewable_profile', 'list_pending_profiles'} <= profile


def test_only_captured_sections_are_claimed():
    """A view claims a retained section only when run completion captures it
    (PR-C: load flow); every other view refuses past runs."""
    assert {v.view_id: v.historical_section for v in rv.RESULT_VIEWS
            if v.historical_section} == {'loadflow': 'loadflow'}


def test_every_view_declares_its_units():
    assert all(v.units for v in rv.RESULT_VIEWS)


def test_a_results_view_id_is_its_tab():
    """list_result_views sends only the id; ui_open_panel takes it as results_tab."""
    assert all(v.view_id == v.tab for v in rv.RESULT_VIEWS if v.panel == 'results')


# ── R0: the registry by card (card-workspaces.md §3, §8) ─────────────────────
# Data only: each results view names its card, the card's sub-option and the
# card's results tab. The ids are flow_scope's (W4a); whichever of W4a and R0
# merges second checks them against flow_scope.CARDS and AVAILABILITY.

CARD_IDS = ('capacity', 'grid', 'control', 'reliability', 'commercial', 'handoff')
CARD_TABS = {
    'capacity': {'build', 'operation', 'curtailment', 'costs_prices'},
    'grid': {'load_flow', 'grid_code', 'electrical_design', 'n1_security'},
    'control': {'island', 'grid_forming', 'dynamics'},
    'reliability': {'adequacy', 'outage_screening', 'stress_fmea'},
    'commercial': {'bill', 'contracts_revenue', 'investment_case', 'parties'},
    'handoff': {'reports', 'exports', 'specialist_packet'},
}
SUB_OPTIONS = {
    'capacity': {'sizing', 'dispatch', 'process_detail'},
    'grid': {'load_flow', 'grid_code', 'n1_sclopf'},
    'control': {'presets', 'island_checks', 'dynamics'},
    'reliability': {'goal_study', 'stress', 'outage_screening'},
    'commercial': {'tariffs', 'contracts', 'investment_case'},
    'handoff': {'reports', 'exports', 'specialist_packet'},
}
# §1.1's "New home" column. Summary is the landing and Asset Detail the drawer
# reached from every card (§3.7, §4): neither belongs to one card.
BY_CARD = {
    'summary': (None, None, None),
    'overview': ('capacity', 'sizing', 'build'),
    'capex': ('capacity', 'sizing', 'build'),
    'dispatch': ('capacity', 'dispatch', 'operation'),
    'flows': ('capacity', 'dispatch', 'operation'),
    'loadshape': ('capacity', 'dispatch', 'operation'),
    'storage': ('capacity', 'dispatch', 'operation'),
    'emissions': ('capacity', 'dispatch', 'operation'),
    'lostload': ('capacity', 'dispatch', 'operation'),
    'curtailment': ('capacity', 'dispatch', 'curtailment'),
    'prices': ('capacity', 'dispatch', 'costs_prices'),
    'economics': ('capacity', 'dispatch', 'costs_prices'),
    'loadflow': ('grid', 'load_flow', 'load_flow'),
    'adequacy': ('reliability', 'goal_study', 'adequacy'),
    'fmea': ('reliability', 'stress', 'stress_fmea'),
    'investment': ('commercial', 'investment_case', 'investment_case'),
    'asset': (None, None, None),
}


def test_every_results_view_names_its_card_option_and_tab():
    got = {v.view_id: (v.card, v.sub_option, v.card_tab)
           for v in rv.RESULT_VIEWS if v.panel == 'results'}
    assert got == BY_CARD


def test_card_fields_use_the_card_ids():
    assert rv.CARDS == CARD_IDS
    for v in rv.RESULT_VIEWS:
        if v.card is None:
            assert v.sub_option is None and v.card_tab is None, v.view_id
            continue
        assert v.sub_option in SUB_OPTIONS[v.card], v.view_id
        assert v.card_tab in CARD_TABS[v.card], v.view_id


def test_views_outside_results_have_no_card():
    assert all(v.card is None for v in rv.RESULT_VIEWS if v.panel != 'results')


def test_card_fields_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        rv.view('loadflow').card = 'capacity'


def test_list_result_views_payload_is_unchanged(monkeypatch):
    """R0 adds data only: the listing the model sees is byte-identical to the
    one before the card fields (no new ids, labels or keys)."""
    golden = json.loads((Path(__file__).parent / 'golden' / 'result_views_listing.json').read_text())
    monkeypatch.setattr(rv, '_probe', lambda v: ('available', None))
    monkeypatch.setattr(rv, 'shown_key', lambda: 'golden')
    got = {}
    for mode in ('expert', 'guided'):
        session = chat_service.ChatSession()
        session.turn_ui_context = {'ui_mode': mode, 'panel': 'results', 'results_tab': 'loadflow'}
        chat_tools.set_chat_session(session)
        got[mode] = rv.list_result_views()
        got[mode + '_exclude_shown'] = rv.list_result_views(exclude_shown=True)
    chat_tools.set_chat_session(None)
    assert json.dumps(got, indent=1) == json.dumps(golden, indent=1)
