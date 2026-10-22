"""VS-2 (LC-11): "walk me through it", "show me something else", compare runs.

Tier A on the oj-grid fixture: list_result_views projects the result-view
registry on the active project (data status per view from its own read, no
values), remembers what this conversation already showed, and every step reads
before it opens. OJ-16's deltas come from compare_project_runs only; the
oracle is a hand calculation (tests/oj_grid.py). OJ-18's interim answer (Q5)
runs the input checks only. Only provider HTTP is mocked.
"""
import json
import math

import pytest

from services import chat_service, chat_tools
from tests.journey_wire import run_journey
from tests.oj_grid import LOAD_MW, SOLAR_MW, SOLAR_PU
from tests.test_oj01_latest_loadflow_journey import MESSAGE, _solve_once, _steps, oj_grid  # noqa: F401
from tests.test_workflow_provider_parity import PROVIDERS
from tests.test_workflow_tools import helpers_state  # noqa: F401

# Hand oracle: Gas covers what Solar does not; storage stays idle.
GAS_MWH = 24 * LOAD_MW - SOLAR_MW * sum(SOLAR_PU)
OBJECTIVE_50 = 50 * GAS_MWH


def _first_with_data(listing, exclude=()):
    return next(v for v in listing['views'] if v['data'] == 'available' and v['id'] not in exclude)


def _read_of(results, pick):
    """The picked view's own read: on its row (Summary, Asset Detail) or in its card's listing."""
    view = pick(results[0])
    if 'read' in view:
        return view['read'][0]
    return next(v for v in results[1]['views'] if v['id'] == view['id'])['read'][0]


def _walk_step(pick):
    """list -> the picked view's card listing (a repeat for a cardless one) -> its read -> open its tab."""
    return [lambda r: ('list_result_views', {'exclude_shown': True}),
            lambda r: ('list_result_views', {'exclude_shown': True, **({'card': pick(r[0])['card']} if 'card' in pick(r[0]) else {})}),
            lambda r: (_read_of(r, pick)['tool'], _read_of(r, pick)['args']),
            lambda r: ('ui_open_panel', {'panel_id': 'results', 'results_tab': pick(r[0])['id']}),
            lambda r: (None, {})]


def test_list_result_views_reports_data_and_no_values(oj_grid):
    session = chat_service.ChatSession()
    chat_tools.set_chat_session(session)
    listing = chat_tools.DISPATCHERS['list_result_views']()
    by_id = {v['id']: v for v in listing['views']}
    assert set(by_id) >= {'summary', 'loadflow', 'dispatch', 'prices', 'adequacy', 'fmea'}
    assert by_id['loadflow']['data'] == 'available'
    # The plain listing carries no reads for a card's views; list_result_views(card=…) does.
    assert 'read' not in by_id['loadflow'] and by_id['loadflow']['card'] == 'grid'
    card_listing = chat_tools.DISPATCHERS['list_result_views'](card='grid')
    assert next(v for v in card_listing['views'] if v['id'] == 'loadflow')['read'] == [{'tool': 'get_loadflow_summary', 'args': {}}]
    assert by_id['loadflow']['verification'] == 'displayed_data'
    assert by_id['overview']['data'] == 'not_applicable'   # single period
    assert by_id['asset']['data'] == 'needs_entity'
    assert all(not v['shown'] for v in listing['views'])
    # Statuses only: no result values reach the model from this listing.
    assert 'MW' not in json.dumps({k: v for k, v in listing.items() if k != 'views'})
    assert not any(isinstance(x, float) for v in listing['views'] for x in v.values())
    assert len(json.dumps(listing)) < 3600 and not listing.get('compact')


@pytest.mark.parametrize('kind,model,base_url,auth', PROVIDERS, ids=[row[0] for row in PROVIDERS])
def test_something_else_offers_only_unshown_sections_with_data(oj_grid, kind, model, base_url, auth):
    """OJ-03: after OJ-01, 'show me something else' never repeats Load flow."""
    _, _, _, session = run_journey(kind, model, base_url, auth, _steps(), MESSAGE)
    calls, results, frames, _ = run_journey(kind, model, base_url, auth,
                                            _walk_step(lambda listing: _first_with_data(listing)),
                                            'Show me something else.', session=session)
    listing, _, read, shown = results
    by_id = {v['id']: v for v in listing['views']}
    assert 'loadflow' not in by_id                    # exclude_shown drops it
    assert listing['shown'] == ['loadflow']
    assert all(v['data'] != 'available' or not v['shown'] for v in listing['views'])
    picked = _first_with_data(listing)
    assert calls[2] == _read_of(results, lambda listing: _first_with_data(listing))['tool']   # read before show
    assert [d['results_tab'] for e, d in frames if e == 'ui_event'] == [picked['id']]
    assert not (isinstance(read, dict) and read.get('status') in {'no_data', 'unavailable'})


def test_walk_next_skip_stop(oj_grid):
    """OJ-02/OJ-15: one section per turn; next never repeats; skip passes one
    over; stop opens nothing."""
    wire = PROVIDERS[1]
    _, _, _, session = run_journey(*wire, _walk_step(lambda listing: _first_with_data(listing)),
                                   'Walk me through all the results, with the numbers.')
    seen = list(list(session.shown_results.values())[0])
    assert len(seen) == 1
    # "Skip prices": the model passes the next one over and opens the one after.
    calls, results, frames, _ = run_journey(
        *wire, _walk_step(lambda listing: _first_with_data(listing, exclude={_first_with_data(listing)['id']})),
        'Skip that one. Next.', session=session)
    skipped = _first_with_data(results[0])['id']
    opened = [d['results_tab'] for e, d in frames if e == 'ui_event']
    assert opened and opened[0] not in seen and opened[0] != skipped
    assert list(session.shown_results.values())[0] == seen + opened
    calls, results, frames, _ = run_journey(*wire, [lambda r: (None, {})], 'Stop.', session=session)
    assert calls == [None] and not [e for e, _ in frames if e == 'ui_event']


def test_shown_sections_are_per_project(oj_grid):
    session = chat_service.ChatSession()
    session.shown_results = {'another-project': ['loadflow']}
    chat_tools.set_chat_session(session)
    listing = chat_tools.DISPATCHERS['list_result_views'](exclude_shown=True)
    assert 'loadflow' in {v['id'] for v in listing['views']}


def test_guided_lists_guided_tabs_first_with_their_labels(oj_grid):
    session = chat_service.ChatSession()
    session.turn_ui_context = {'ui_mode': 'guided'}
    chat_tools.set_chat_session(session)
    listing = chat_tools.DISPATCHERS['list_result_views']()
    ids = [v['id'] for v in listing['views']]
    # The Guided tabs in Results.tsx order; Energy flow and Load shape went live with their read tools.
    assert ids[:5] == ['summary', 'flows', 'loadshape', 'adequacy', 'fmea']
    lf = next(v for v in listing['views'] if v['id'] == 'loadflow')
    assert lf['label'] == 'Load Flow (advanced)'


def test_compare_with_the_previous_run(oj_grid, client, monkeypatch):
    """OJ-16: deltas come from compare_project_runs, never prose; a changed
    input is said and incomparable runs get reasons, no deltas."""
    import harness.confirm as harness_confirm

    response = client.put('/api/network/generators/Gas', json={'name': 'Gas', 'bus': 'B1', 'marginal_cost': 60})
    assert response.status_code == 200, response.text
    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
        session = chat_service.ChatSession()
        chat_tools.set_chat_session(session)
        newest = _solve_once(session)
    previous = oj_grid['latest']
    steps = [lambda r: ('list_project_runs', {'limit': 2, 'status': 'completed'}),
             lambda r: ('compare_project_runs', {'run_id': r[0]['items'][0]['run_id'],
                                                 'baseline_run_id': r[0]['items'][1]['run_id']}),
             lambda r: ('compare_project_runs', {'run_id': r[0]['items'][0]['run_id'],
                                                 'baseline_run_id': r[0]['items'][1]['run_id'],
                                                 'allow_input_changes': True}),
             lambda r: (None, {})]
    calls, results, frames, _ = run_journey(*PROVIDERS[0], steps,
                                            'How does this run compare to the one before?')
    runs, strict, allowed = results
    assert [r['run_id'] for r in runs['items']] == [newest['run_id'], previous['run_id']]
    assert strict['comparable'] is False and strict['deltas'] == {}
    assert strict['inputs_differ'] is True
    assert [r['reason'] for r in strict['incomparable_reasons']] == ['fingerprint_mismatch']
    objective = allowed['deltas']['objective']
    assert objective['baseline']['value'] == pytest.approx(OBJECTIVE_50, rel=1e-6)
    assert objective['delta']['value'] == pytest.approx(10 * GAS_MWH, rel=1e-6)
    assert objective['delta_pct'] == pytest.approx(20.0, rel=1e-6)
    assert objective['delta']['unit'] == 'EUR'
    assert math.isclose(sum(SOLAR_PU), 1 / math.tan(math.radians(7.5)), rel_tol=1e-5)


def test_do_these_results_make_sense_runs_the_input_checks_only(oj_grid):
    """OJ-18 interim (Q5 = A): the three input checks, and the prompt says
    result-plausibility rules do not exist yet."""
    from harness import compose

    steps = [lambda r: ('project_readiness', {}), lambda r: ('validate_network', {}),
             lambda r: ('diagnose_network', {}), lambda r: (None, {})]
    calls, results, frames, _ = run_journey(*PROVIDERS[1], steps, 'Do these results make sense?')
    assert calls == ['project_readiness', 'validate_network', 'diagnose_network', None]
    assert not [d for e, d in frames if e in {'error', 'tool_error'}]
    text = ' '.join(compose._RESULTS_WALK.split())
    assert 'project_readiness, validate_network and diagnose_network' in text
    assert "result-plausibility rules don't exist yet" in text


def _numbers(text):
    import re
    return {float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', text)}


def _leaf_numbers(value, out=None):
    """Two numeric facts from a read, with their keys: what a model may quote."""
    out = [] if out is None else out
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((k, v))
            else:
                _leaf_numbers(v, out)
    elif isinstance(value, list):
        for v in value[:20]:
            _leaf_numbers(v, out)
    return out


def test_a_tab_the_user_opened_counts_as_shown_and_ends_the_walk(oj_grid):
    """Manual navigation: the tab on screen is shown; the walk does not offer it again."""
    session = chat_service.ChatSession()
    calls, results, frames, _ = run_journey(
        *PROVIDERS[1], [lambda r: ('list_result_views', {'exclude_shown': True}), lambda r: (None, {})],
        'Next.', session=session, ui_context_extra={'panel': 'results', 'results_tab': 'prices'})
    listing = results[0]
    assert listing['shown'] == ['prices'] and listing['on_screen'] == 'prices'
    assert 'prices' not in {v['id'] for v in listing['views']}


def test_go_back_reopens_the_previous_section(oj_grid):
    wire = PROVIDERS[1]
    _, _, _, session = run_journey(*wire, _steps(), MESSAGE)
    _, _, _, session = run_journey(*wire, _walk_step(lambda listing: _first_with_data(listing)),
                                   'Next.', session=session)
    before = list(list(session.shown_results.values())[0])
    back = [lambda r: ('list_result_views', {}),
            lambda r: ('get_loadflow_summary', {}),
            lambda r: ('ui_open_panel', {'panel_id': 'results', 'results_tab': before[0]}),
            lambda r: (None, {})]
    calls, results, frames, _ = run_journey(*wire, back, 'Go back.', session=session)
    assert before[0] == 'loadflow'
    assert next(v for v in results[0]['views'] if v['id'] == 'loadflow')['shown'] is True
    assert [d['results_tab'] for e, d in frames if e == 'ui_event'] == ['loadflow']
    assert list(session.shown_results.values())[0] == before


def test_each_walk_step_quotes_only_its_own_read(oj_grid):
    """OJ-15: a step's numbers come from that step's read, never an earlier one."""
    wire = PROVIDERS[1]
    _, first, _, session = run_journey(*wire, _steps(), MESSAGE)

    def words(results):
        facts = _leaf_numbers(results[2])[:2]
        return 'Key points: ' + '; '.join(f'{k} {v}' for k, v in facts) + '. Next section?'

    calls, results, frames, _ = run_journey(*wire, _walk_step(lambda listing: _first_with_data(listing)),
                                            'Next.', session=session, answer=words)
    text = ''.join(d.get('delta', '') for e, d in frames if e == 'token')
    assert text.startswith('Key points: ') and _numbers(text)
    assert _numbers(text) <= _numbers(json.dumps(results[2]))   # this step's read
    shown = [d for e, d in frames if e == 'ui_event'][0]
    assert shown['results_tab'] != 'loadflow'


def test_statuses_are_the_declared_ones_and_rewind_forgets_shown(oj_grid):
    from services import result_views as rv

    _, _, _, session = run_journey(*PROVIDERS[1], _steps(), MESSAGE)
    listing = chat_tools.DISPATCHERS['list_result_views']()
    assert {v['data'] for v in listing['views']} <= set(rv.DATA_STATUSES)
    assert session.shown_results
    session.reset_history()
    assert session.shown_results == {}
