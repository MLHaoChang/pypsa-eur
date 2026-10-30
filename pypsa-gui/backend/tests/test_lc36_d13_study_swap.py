# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-36 LC-D13: switching project while an adequacy study runs, through the chat tools.

Project A runs LC-33's frontier (`frontier_network`, VoLL 100, three targets);
project B is a saved project (created first; a network reset then gives A a
context of its own). While the frontier is live, `activate_project(B)`
is refused with the typed kind 'study_in_flight' and `load_project(B)` is
refused too (the study-swap guard, `project_context.study_swap_refusal`); the
frontier's record stays `running` on A. Once the run ends the switch goes
through and B's frontier reader is `no_data`: A's points never show on B.

Two endings: the run finishes to LC-33's hand points (`frontier_oracle`), or
it is aborted (`abort_adequacy_study`) and ends `aborted`. Either way the
guard lifts.

Holding the run live: the real `run_frontier_sweep` is wrapped so it starts
only after a `threading.Event` the test sets (GK A's condition, the pattern of
#936's `sweep._solve_once` wrapper). The real sweep runs after the release; the
guard under test is not touched. Every wait has a timeout, and the gate is
released in a `finally`, so a missed release fails the test instead of hanging.
"""
import threading

import pytest

from harness import confirm as harness_confirm
from services import chat_tools
from services.adequacy import frontier
from services.pypsa_service import PyPSAService
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_margin_elcc import FR_TARGETS, call, frontier_network, wait_for
from tests.test_lc36_d13_scope_campaign import VOLL, assert_frontier_points, tool_errors
from tests.test_workflow_tools import helpers_state  # noqa: F401

GATE_S = 60.0
OTHER = 'lc36-swap-b'
A_GENERATORS = ['base', 'cheap', 'dear']


def active_generators() -> list[str]:
    return list(PyPSAService.get_network().generators.index)


@pytest.mark.parametrize('ending', ['finish', 'abort'])
def test_a_live_frontier_blocks_the_switch_until_it_ends(seam, client, api_project, monkeypatch, ending):
    api_project(OTHER)
    assert client.post('/api/network/reset').status_code == 200    # A gets a context of its own
    gate, entered = threading.Event(), threading.Event()
    real = frontier.run_frontier_sweep

    def held(*args, **kwargs):
        entered.set()
        if not gate.wait(GATE_S):
            raise RuntimeError('the test never released the frontier')
        return real(*args, **kwargs)

    monkeypatch.setattr(frontier, 'run_frontier_sweep', held)
    seam(frontier_network(), voll=VOLL)
    try:
        call('run_frontier_study', targets_permyriad=FR_TARGETS)
        assert entered.wait(GATE_S), 'the frontier worker never started'
        assert chat_tools.get_adequacy_results('frontier')['status'] == 'running'

        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'write', 'destructive', 'execution'}))
            [activate] = tool_errors('activate_project', project_id=OTHER)
            [load] = tool_errors('load_project', name=OTHER)
            if ending == 'abort':
                assert tool_errors('abort_adequacy_study', study='frontier') == []
        assert activate['error_kind'] == 'study_in_flight'
        assert load['error_kind'] == 'tool_error'
        assert 'abort it' in load['message']
        assert chat_tools.get_adequacy_results('frontier')['status'] == 'running'
        assert active_generators() == A_GENERATORS            # still on A
    finally:
        gate.set()
    if ending == 'finish':
        assert_frontier_points(wait_for('frontier'), FR_TARGETS)
    else:
        assert wait_for('frontier', ends=('aborted',))['status'] == 'aborted'

    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'write', 'destructive', 'execution'}))
        assert tool_errors('activate_project', project_id=OTHER) == []
    assert active_generators() != A_GENERATORS            # B is active now
    assert chat_tools.get_adequacy_results('frontier')['status'] == 'no_data'
