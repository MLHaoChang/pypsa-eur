# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-37 LC-D13: the margin-loop, frontier, MC/ELCC and campaign readers report an edit since the study, through the chat tools.

Each reader carries `edited_since_study`, on the EH reader's basis: the
network revision captured when the study started against the revision now
(`services/study_state.edited_since`). Per reader: False after the run, True
after `update_component` (the stored numbers are the study that ran, flagged,
not recomputed), cleared by a re-run whose number moves by the hand delta.

The fixtures and hand answers are LC-33's (`test_lc33_d13_margin_elcc`):

* margin loop: m_tight = 0.9 x 130 / load - 1, 0.17 at 100 MW and
  117 / 110 - 1 at 110 MW;
* frontier: the 1000-permyriad point costs 15,200 EUR; raising the dear
  plant's marginal cost by 10 EUR/MWh (220 -> 230 EUR per MWh it serves,
  the oracle's capex mutation) adds 10 x 10 MW x 4 h;
* ELCC of the perfect 30 MW unit: load - 80, so 20 MW at 100 and 15 at 95.

The campaign log flags each entry, and its top-level flag reads the latest
entry of each study: a re-run clears it, the superseded entry stays True.
"""
import time

import pytest

from harness import confirm as harness_confirm
from routers import results as results_router
from services import chat_service, chat_tools
from services.adequacy import campaign, elcc
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_margin_elcc import (
    DEAR_PER_MWH,
    FR_TARGETS,
    MW_TOL,
    EUR_TOL,
    ML_F_MW,
    ML_Q,
    call,
    elcc_network,
    frontier_network,
    frontier_oracle,
    run_loop,
    wait_for,
)
from tests.test_workflow_tools import helpers_state  # noqa: F401


def edited_since(kind: str) -> bool | None:
    return chat_tools.get_adequacy_results(kind)['edited_since_study']


def edit(component_class: str, name: str, **attrs) -> None:
    with pytest.MonkeyPatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'write', 'execution'}))
        frames = list(chat_service._dispatch_real_tool_call(
            chat_tools._CHAT_SESSION.get(),
            {'id': f'lc37-edit-{time.monotonic_ns()}', 'name': 'update_component',
             'input': {'component_class': component_class, 'name': name, 'attrs': attrs}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames


def m_tight(load: float) -> float:
    return (1 - ML_Q) * ML_F_MW / load - 1


def test_an_edit_flags_the_margin_loop_and_a_rerun_clears_it_with_the_hand_delta(seam):
    first = run_loop(seam, 100.0)
    assert first['base_restored'] is True          # read after the loop's own restore
    assert first['margin_tight'] == pytest.approx(m_tight(100.0), abs=MW_TOL)
    assert edited_since('margin_loop') is False

    edit('Load', 'l', p_set=110.0)
    assert edited_since('margin_loop') is True
    assert chat_tools.get_adequacy_results('margin_loop')['margin_tight'] == pytest.approx(m_tight(100.0), abs=MW_TOL)

    call('run_margin_loop', target_lole_h=1.92, draws=400, seed=7, max_solves=8)
    second = wait_for('margin_loop')
    assert edited_since('margin_loop') is False
    assert second['margin_tight'] == pytest.approx(m_tight(110.0), abs=MW_TOL)
    assert second['margin_tight'] - first['margin_tight'] == pytest.approx(117 / 110 - 117 / 100, abs=MW_TOL)


def last_point_cost(out: dict) -> float:
    p = out['points'][FR_TARGETS.index(1000)]
    assert p['status'] == 'ok', p
    return p['point']['total_system_cost_eur']


def test_an_edit_flags_the_frontier_and_the_campaign_and_a_rerun_clears_both(seam):
    voll = 100.0
    seam(frontier_network(), voll=voll)
    chat_tools.start_campaign('LC-37 stale frontier', 10)
    assert chat_tools.campaign_status()['edited_since_study'] is None
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    first = wait_for('frontier')
    assert first['base_restored'] is True          # read after the sweep's own restore
    assert last_point_cost(first) == pytest.approx(frontier_oracle(voll)[0][-1][1], abs=EUR_TOL)
    assert edited_since('frontier') is False
    status = chat_tools.campaign_status()
    assert status['edited_since_study'] is False
    assert [e['edited_since_study'] for e in status['entries']] == [False]

    edit('Generator', 'dear', marginal_cost=20.0 + 10)
    assert edited_since('frontier') is True
    assert last_point_cost(chat_tools.get_adequacy_results('frontier')) == pytest.approx(15200.0, abs=EUR_TOL)
    status = chat_tools.campaign_status()
    assert status['edited_since_study'] is True
    assert [e['edited_since_study'] for e in status['entries']] == [True]

    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    second = wait_for('frontier')
    assert edited_since('frontier') is False
    assert last_point_cost(second) == pytest.approx(frontier_oracle(voll, DEAR_PER_MWH + 10)[0][-1][1], abs=EUR_TOL)
    assert last_point_cost(second) - last_point_cost(first) == pytest.approx(10 * 10.0 * 4, abs=EUR_TOL)
    status = chat_tools.campaign_status()
    assert status['edited_since_study'] is False
    assert [(e['study'], e['edited_since_study']) for e in status['entries']] == [('frontier', True), ('frontier', False)]


def elcc_mw() -> float:
    out = wait_for('mc')
    row, = out['elcc']
    assert row['status'] == 'ok', row
    return row['elcc_mw']


def test_an_edit_flags_the_elcc_and_a_rerun_clears_it_with_the_hand_delta(seam):
    tol = elcc.default_tol_mw(30.0)
    seam(elcc_network(100.0))
    study = {'seed': 3, 'draws': 200, 'elcc_assets': [{'kind': 'generator', 'name': 'P'}]}
    call('run_mc_study', **study)
    first = elcc_mw()
    assert 20.0 <= first <= 20.0 + tol
    assert edited_since('mc') is False

    edit('Load', 'l', p_set=95.0)
    assert edited_since('mc') is True
    stored, = chat_tools.get_adequacy_results('mc')['result']['elcc']
    assert stored['elcc_mw'] == first

    call('run_mc_study', **study)
    second = elcc_mw()
    assert edited_since('mc') is False
    assert 15.0 <= second <= 15.0 + tol
    assert second - first == pytest.approx(-5.0, abs=tol)


@pytest.mark.parametrize('kind', ['frontier', 'mc', 'margin_loop'])
def test_a_record_without_a_captured_revision_reads_unknown(seam, kind):
    """A record from before LC-37 (restored or legacy) has no revision: None, never False."""
    from services import dirty_state
    seam(elcc_network(100.0))
    record = {'status': 'done', 'error': None, 'network_revision': dirty_state.revision()}
    results_router._state[kind] = record
    assert edited_since(kind) is False
    del record['network_revision']
    assert edited_since(kind) is None


def test_a_campaign_entry_without_a_captured_revision_reads_unknown(seam):
    seam(elcc_network(100.0))
    chat_tools.start_campaign('LC-37 legacy entry', 10)
    campaign.record('frontier', 4)                 # an entry from before LC-37
    status = chat_tools.campaign_status()
    assert [e['edited_since_study'] for e in status['entries']] == [None]
    assert status['edited_since_study'] is None
