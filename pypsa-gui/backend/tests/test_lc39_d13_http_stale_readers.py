# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-39 LC-D13: the HTTP frontier, MC and margin-loop routes report an edit since the study.

The panels read `GET /api/results/{frontier,mc,margin_loop}`. Each carries
`edited_since_study` on the EH route's basis (`services/study_state.edited_since`):
False after the run, True after `update_component` with the stored numbers
unchanged, cleared by a re-run. The fixtures are LC-37's.
"""
import pytest

from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_margin_elcc import (
    FR_TARGETS,
    call,
    elcc_network,
    frontier_network,
    run_loop,
    wait_for,
)
from tests.test_lc37_d13_stale_readers import edit
from tests.test_workflow_tools import helpers_state  # noqa: F401


def flag(client, route: str) -> bool | None:
    r = client.get(f'/api/results/{route}')
    assert r.status_code == 200, r.text
    return r.json()['edited_since_study']


def test_the_margin_loop_route_flags_an_edit_and_a_rerun_clears_it(seam, client):
    run_loop(seam, 100.0)
    assert flag(client, 'margin_loop') is False
    edit('Load', 'l', p_set=110.0)
    assert flag(client, 'margin_loop') is True
    call('run_margin_loop', target_lole_h=1.92, draws=400, seed=7, max_solves=8)
    wait_for('margin_loop')
    assert flag(client, 'margin_loop') is False


def test_the_frontier_route_flags_an_edit_and_a_rerun_clears_it(seam, client):
    seam(frontier_network(), voll=100.0)
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    wait_for('frontier')
    assert flag(client, 'frontier') is False
    edit('Generator', 'dear', marginal_cost=30.0)
    assert flag(client, 'frontier') is True
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    wait_for('frontier')
    assert flag(client, 'frontier') is False


def test_the_mc_route_flags_an_edit_and_a_rerun_clears_it(seam, client):
    seam(elcc_network(100.0))
    study = {'seed': 3, 'draws': 200, 'elcc_assets': [{'kind': 'generator', 'name': 'P'}]}
    call('run_mc_study', **study)
    wait_for('mc')
    assert flag(client, 'mc') is False
    edit('Load', 'l', p_set=95.0)
    assert flag(client, 'mc') is True
    call('run_mc_study', **study)
    wait_for('mc')
    assert flag(client, 'mc') is False
