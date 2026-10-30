# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-36 LC-D13: an EH study whose source network changes reports it, through the chat tools.

The EH reader (`get_adequacy_results('eh_study')`) carries
`edited_since_study`: the network revision when the study ran against the
revision now (`services/study_state.edited_since`). The fixture is LC-36's
dedicated heat hub (`test_lc36_d13_multi_energy_heat.hub`): 2 h, 50 MW AC Load,
55 MW gas, a 10 MW heat pump at efficiency 3 that the LP runs first, so the
AC Load gets 45 MW and electrical unmet is 2 x 5 = 10 MWh.

After the study the flag is False. `update_component` raises the AC Load to
55 MW through the chat tools: the flag turns True and the stored study still
reads 10 MWh (it is the study that ran, flagged, not recomputed). Re-running
clears the flag and moves electrical unmet by the hand delta: the heat pump
still takes 10 MW, the AC Load gets 45 of its 55 MW, 2 x 10 = 20 MWh (+10).

The margin-loop, frontier, MC/ELCC and campaign readers carry the same
marker since LC-37 (`test_lc37_d13_stale_readers`).
"""
import time

import pytest

from harness import confirm as harness_confirm
from services import chat_service, chat_tools
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_multi_energy import multi_energy, sections
from tests.test_lc36_d13_multi_energy_heat import HOURS, MWH_TOL, hub
from tests.test_workflow_tools import helpers_state  # noqa: F401

STUDY = {'archetype': 'strong_grid', 'stages': ['apply_pack', 'ens_solve'],
         'pack_overrides': {'ens_cap_permyriad': 5000}}


def edited_since() -> bool | None:
    return chat_tools.get_adequacy_results('eh_study')['edited_since_study']


def electrical_unmet(s: dict) -> float:
    return multi_energy(s)['ens_by_carrier_mwh']['electrical']


def rerun() -> dict:
    frames = list(chat_service._dispatch_real_tool_call(
        chat_tools._CHAT_SESSION.get(),
        {'id': f'lc36-stale-{time.monotonic_ns()}', 'name': 'run_eh_study', 'input': STUDY}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 300
    while chat_tools.get_adequacy_results('eh_study').get('status') == 'running':
        assert time.monotonic() < deadline, 'EH study did not finish'
        time.sleep(0.2)
    report = chat_tools.get_adequacy_results('eh_reference_design')
    return (report.get('result') or report)['sections']


def test_an_edit_flags_the_eh_study_and_a_rerun_clears_it_with_the_hand_delta(seam, monkeypatch):
    first = sections(seam, hub(40.0, shared=False))
    assert electrical_unmet(first) == pytest.approx(HOURS * (50 - 45), abs=MWH_TOL)
    assert edited_since() is False

    with monkeypatch.context() as m:
        m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'write', 'execution'}))
        frames = list(chat_service._dispatch_real_tool_call(
            chat_tools._CHAT_SESSION.get(),
            {'id': f'lc36-edit-{time.monotonic_ns()}', 'name': 'update_component',
             'input': {'component_class': 'Load', 'name': 'l_elec', 'attrs': {'p_set': 55.0}}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    assert edited_since() is True
    report = chat_tools.get_adequacy_results('eh_reference_design')
    stored = (report.get('result') or report)['sections']
    assert electrical_unmet(stored) == pytest.approx(HOURS * (50 - 45), abs=MWH_TOL)

    second = rerun()
    assert edited_since() is False
    assert electrical_unmet(second) == pytest.approx(HOURS * (55 - 45), abs=MWH_TOL)
    assert electrical_unmet(second) - electrical_unmet(first) == pytest.approx(10.0, abs=MWH_TOL)
