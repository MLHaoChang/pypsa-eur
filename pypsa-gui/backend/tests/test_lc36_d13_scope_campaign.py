# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-36 LC-D13: unsupported scope and incomplete campaigns, through the chat tools.

Unsupported scope. Two refusals the adequacy tools raise before any solve:
the frontier with VoLL 0 ('requires a VOLL > 0') and the margin loop under the
'rolling' solve strategy (the reserve margin would bind per window, not per
period). Each refusal reaches the chat seam as a `tool_error` frame with the
generic kind 'tool_error' (no typed kind exists for scope refusals; a follow-up
ticket names that gap), so each test pins the kind exactly, the guard's own
text (it occurs only at the runner's raise site), no solve charged to the campaign, and the reader's prior
output byte-identical. The mutation is the in-scope run: LC-33's frontier
fixture at VoLL 100 gives its hand points (`frontier_oracle`) and costs
targets + 1 = 4 solves; the margin loop under 'full' (the default) is met from LC-33's hand
starting margin.

Incomplete campaign. A campaign has no "incomplete" state of its own: a
study that would overrun the budget is refused before it starts
(`campaign.check` in `_campaign_gated`), typed 'campaign_budget_exhausted',
and charges nothing. A frontier costs targets + 1 solves (each target plus the
closing restore). Budget 3: three targets (4 solves) are refused with spent 0
and no entry; two targets (3 solves) run to their hand points and spend the
budget exactly; `end_campaign` closes it with spent 3. The mutation is budget
4, which lets the three-target frontier through and spends 4.
"""
import json
import time

import pytest

from services import chat_service, chat_tools
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_margin_elcc import (
    FR_TARGETS,
    MW_TOL,
    call,
    frontier_network,
    frontier_oracle,
    loop_network,
    loop_oracle,
    run_loop,
    wait_for,
)
from tests.test_workflow_tools import helpers_state  # noqa: F401

VOLL = 100.0


def tool_errors(tool: str, /, **args) -> list[dict]:
    frames = list(chat_service._dispatch_real_tool_call(
        chat_tools._CHAT_SESSION.get(),
        {'id': f'lc36-d13-{time.monotonic_ns()}', 'name': tool, 'input': args}, []))
    return [d for e, d in frames if e == 'tool_error']


def snapshot(kind: str) -> str:
    return json.dumps(chat_tools.get_adequacy_results(kind), sort_keys=True, default=str)


def assert_frontier_points(out: dict, targets: list[int]) -> None:
    points, _ = frontier_oracle(VOLL)
    assert [p['target_permyriad'] for p in out['points']] == targets
    for p, (ens, cost) in zip(out['points'], points):
        assert p['status'] == 'ok', p
        assert p['point']['achieved_ens_mwh'] == pytest.approx(ens, abs=MW_TOL)
        assert p['point']['total_system_cost_eur'] == pytest.approx(cost, abs=MW_TOL)


# ── unsupported scope ────────────────────────────────────────────────────

def test_a_frontier_at_voll_zero_is_refused_without_a_solve_and_keeps_the_prior_result(seam):
    seam(frontier_network(), voll=VOLL)
    chat_tools.start_campaign('LC-36 scope', 10)
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    assert_frontier_points(wait_for('frontier'), FR_TARGETS)
    spent = len(FR_TARGETS) + 1
    assert chat_tools.campaign_status()['spent_solves'] == spent
    before = snapshot('frontier')

    chat_tools.update_solver_config({'voll': 0.0})
    [err] = tool_errors('run_frontier_study', targets_permyriad=FR_TARGETS)
    assert err['error_kind'] == 'tool_error'
    # The runner guard's own text (frontier_loop_runner.py), not the engine's later check.
    assert err['message'] == 'the frontier requires a VOLL > 0 in solver settings'
    status = chat_tools.campaign_status()
    assert status['spent_solves'] == spent
    assert [(e['study'], e['solves_charged']) for e in status['entries']] == [('frontier', spent)]
    assert snapshot('frontier') == before


@pytest.mark.parametrize('strategy', ['rolling', 'full'])
def test_the_margin_loop_refuses_the_rolling_strategy_before_any_solve(seam, strategy):
    if strategy == 'full':
        out = run_loop(seam, 100.0)
        assert out['status'] == 'met', out
        assert out['margin0'] == pytest.approx(loop_oracle(100.0)['m_start'], abs=MW_TOL / 100.0)
        return
    seam(loop_network(100.0), solve_strategy=strategy)
    before = snapshot('margin_loop')
    assert json.loads(before)['status'] == 'no_data'
    [err] = tool_errors('run_margin_loop', target_lole_h=1.92, draws=200, seed=1, max_solves=8)
    assert err['error_kind'] == 'tool_error'
    # The runner guard's own text (margin_loop_runner.py); validation_service's capitalised twin differs.
    assert err['message'].startswith("the reserve margin is not supported with the 'rolling' solve strategy")
    assert snapshot('margin_loop') == before


# ── incomplete campaign ──────────────────────────────────────────────────

@pytest.mark.parametrize('budget', [3, 4], ids=['budget_3', 'budget_4'])
def test_a_study_that_would_overrun_the_campaign_is_refused_and_charges_nothing(seam, budget):
    seam(frontier_network(), voll=VOLL)
    chat_tools.start_campaign('LC-36 campaign', budget)
    three_cost = len(FR_TARGETS) + 1
    errors = tool_errors('run_frontier_study', targets_permyriad=FR_TARGETS)
    if budget < three_cost:
        [err] = errors
        assert err['error_kind'] == 'campaign_budget_exhausted'
        status = chat_tools.campaign_status()
        assert (status['spent_solves'], status['remaining_solves'], status['entries']) == (0, budget, [])
        targets = FR_TARGETS[:2]
        call('run_frontier_study', targets_permyriad=targets)
    else:
        assert errors == []
        targets = FR_TARGETS
    assert_frontier_points(wait_for('frontier'), targets)
    spent = len(targets) + 1
    assert spent == budget
    status = chat_tools.campaign_status()
    assert (status['spent_solves'], status['remaining_solves']) == (spent, 0)
    assert [(e['study'], e['solves_charged']) for e in status['entries']] == [('frontier', spent)]
    closed = chat_tools.end_campaign('LC-36')
    assert (closed['active'], closed['spent_solves'], closed['budget_solves']) == (False, spent, budget)
