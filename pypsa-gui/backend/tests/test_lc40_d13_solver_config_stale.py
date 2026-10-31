# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-40 LC-D13: a solver-config change marks a finished adequacy study stale.

Owner decision LC40-COUNTER-1: `update_solver_config` bumps the same edit counter
as a network edit. A frontier, then `update_solver_config(voll=...)`: the reader
says the study is stale; a re-run clears it with the hand-moved knee.
"""
import pytest

from services import chat_tools
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_margin_elcc import FR_TARGETS, call, frontier_network, wait_for
from tests.test_lc37_d13_stale_readers import EUR_TOL, edited_since, frontier_oracle, last_point_cost
from tests.test_workflow_tools import helpers_state  # noqa: F401


def test_a_voll_change_flags_the_frontier_and_a_rerun_clears_it(seam):
    seam(frontier_network(), voll=100.0)
    chat_tools.start_campaign('LC-40 stale frontier', 10)
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    wait_for('frontier')
    assert edited_since('frontier') is False

    chat_tools.update_solver_config({'voll': 200.0})
    assert edited_since('frontier') is True
    assert last_point_cost(chat_tools.get_adequacy_results('frontier')) == pytest.approx(
        frontier_oracle(100.0)[0][-1][1], abs=EUR_TOL)
    assert chat_tools.campaign_status()['edited_since_study'] is True

    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    second = wait_for('frontier')
    assert edited_since('frontier') is False
    assert last_point_cost(second) == pytest.approx(frontier_oracle(200.0)[0][-1][1], abs=EUR_TOL)
    assert last_point_cost(second) != pytest.approx(last_point_cost(first), abs=EUR_TOL)
    assert chat_tools.campaign_status()['edited_since_study'] is False
