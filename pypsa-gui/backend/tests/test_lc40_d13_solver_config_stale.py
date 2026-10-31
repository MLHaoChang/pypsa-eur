# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-40 LC-D13: a solver-config change marks a finished adequacy study stale.

Owner decision LC40-COUNTER-1: `update_solver_config` bumps the same edit counter
as a network edit. A frontier, then `update_solver_config(voll=...)`: the reader
says the study is stale; a re-run clears it with the hand-moved knee.
"""
import dataclasses

import pytest

from services import chat_tools, dirty_state
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_margin_elcc import (
    FR_TARGETS,
    ML_DRAWS,
    ML_SEED,
    ML_TARGET_H,
    call,
    frontier_network,
    frontier_oracle,
    loop_network,
    wait_for,
)
from tests.test_lc37_d13_stale_readers import EUR_TOL, edited_since
from tests.test_workflow_tools import helpers_state  # noqa: F401


def costs(out: dict) -> list[float]:
    return [p['point']['total_system_cost_eur'] for p in out['points']]


def check(out: dict, voll: float) -> None:
    points, knee = frontier_oracle(voll)
    assert costs(out) == pytest.approx([c for _, c in points], abs=EUR_TOL)
    assert out['knee'] == knee


def test_a_voll_change_flags_the_frontier_and_a_rerun_clears_it(seam):
    seam(frontier_network(), voll=100.0)
    chat_tools.start_campaign('LC-40 stale frontier', 10)
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    check(wait_for('frontier'), 100.0)
    assert edited_since('frontier') is False

    chat_tools.update_solver_config({'voll': 40.0})
    assert edited_since('frontier') is True
    check(chat_tools.get_adequacy_results('frontier'), 100.0)     # the stored numbers are the old config's
    assert chat_tools.campaign_status()['edited_since_study'] is True

    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    check(wait_for('frontier'), 40.0)                              # the hand-moved knee: 1 -> 0
    assert edited_since('frontier') is False
    assert chat_tools.campaign_status()['edited_since_study'] is False


def test_a_value_flows_change_flags_the_frontier(seam, client):
    seam(frontier_network(), voll=100.0)
    from routers.simulation import _state
    _state['solver_config'] = dataclasses.replace(_state['solver_config'], commercial={'poc_link': 'import'})
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    wait_for('frontier')
    assert edited_since('frontier') is False
    r = client.put('/api/simulation/commercial/value_flows', json={'value_flows': None})
    assert r.status_code == 200, r.text
    assert edited_since('frontier') is True


def test_a_finance_change_does_not_flag_the_frontier(seam, client):
    seam(frontier_network(), voll=100.0)
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    wait_for('frontier')
    r = client.put('/api/simulation/finance', json={'finance': None})
    assert r.status_code in (200, 422), r.text
    assert edited_since('frontier') is False


def test_a_margin_hold_moves_the_counter_but_not_its_own_loop_stale(seam):
    seam(loop_network(100.0))()
    before = dirty_state.revision()
    call('run_margin_loop', target_lole_h=ML_TARGET_H, draws=ML_DRAWS, seed=ML_SEED,
         max_solves=8, restore='final')
    out = wait_for('margin_loop')
    assert out['status'] == 'met', out
    assert dirty_state.revision() > before            # the hold wrote reserve_margin
    assert edited_since('margin_loop') is False       # its verdict describes the held margin
