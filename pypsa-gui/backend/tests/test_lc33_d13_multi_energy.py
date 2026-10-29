# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-33 LC-D13: H2 unmet energy attributed apart from the electrical ENS, through the chat tools.

`run_eh_study` (strong_grid pack, stages apply_pack + ens_solve) fills the
`multi_energy` section of `get_adequacy_results('eh_reference_design')`.

Dedicated bus. 2 h at weight 1: the AC hub carries a 50 MW Load and 80 MW of
gas; an electrolyser (50 MW, efficiency 0.6) feeds a 40 MW H2 Load on its own
H2 bus. Every slack is priced at the same VoLL, so serving the AC Load first
avoids more shed per MWh of gas than feeding the electrolyser: the AC Load is
served in full, the electrolyser takes the 30 MW left and delivers 18 MW of
H2. H2 unmet = 2 x (40 - 18) = 44 MWh, by carrier and by Load; electrical
unmet = 0, and the electrical ENS target sees none of it. The mutation raises
the H2 Load by 5 MW: H2 unmet +10 MWh, electrical unmet exactly 0 again.

Shared bus. The same two Loads both sit on the AC hub with the 80 MW of gas:
10 MW short, 20 MWh over 2 h. The per-Load slacks are equally priced on one
bus, so the LP is indifferent to which Load sheds: the total is pinned, both
by Load and by carrier, and the split is not.

Live solves always capture per-Load slacks, so both cases report
`per_load_slack`; the dedicated-bus roll-up path is not reached from the seam.
All data are integers or 0.6 multiples; the shed is an LP vertex: 1e-6 MWh.
"""
import time

import pandas as pd
import pypsa
import pytest

from services import chat_service, chat_tools
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

MWH_TOL = 1e-6
HOURS = 2
AC_MW, GAS_MW = 50.0, 80.0
ELY_MW, ELY_EFF = 50.0, 0.6


def hub(h2_mw: float, *, shared: bool) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=HOURS, freq='h'))
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ('AC', 'H2', 'gas'):
        n.add('Carrier', c)
    n.add('Bus', 'hub', carrier='AC')
    if not shared:
        n.add('Bus', 'h2', carrier='H2')
        n.add('Link', 'electrolyser', bus0='hub', bus1='h2', p_nom=ELY_MW, efficiency=ELY_EFF, carrier='H2')
    n.add('Load', 'l_elec', bus='hub', carrier='AC', p_set=AC_MW)
    n.add('Load', 'l_h2', bus='hub' if shared else 'h2', carrier='H2', p_set=h2_mw)
    n.add('Generator', 'gas', bus='hub', carrier='gas', p_nom=GAS_MW, marginal_cost=20.0)
    return n


def dedicated_h2_unmet(h2_mw: float) -> float:
    delivered = min(ELY_MW, GAS_MW - AC_MW) * ELY_EFF
    return HOURS * max(h2_mw - delivered, 0.0)


def sections(seam, n) -> dict:
    seam(n, voll=500.0)
    frames = list(chat_service._dispatch_real_tool_call(
        chat_tools._CHAT_SESSION.get(),
        {'id': f'lc33-me-{time.monotonic_ns()}', 'name': 'run_eh_study',
         'input': {'archetype': 'strong_grid', 'stages': ['apply_pack', 'ens_solve'],
                   'pack_overrides': {'ens_cap_permyriad': 5000}}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 300
    while chat_tools.get_adequacy_results('eh_study').get('status') == 'running':
        assert time.monotonic() < deadline, 'EH study did not finish'
        time.sleep(0.2)
    assert chat_tools.get_adequacy_results('eh_study')['status'] == 'done'
    report = chat_tools.get_adequacy_results('eh_reference_design')
    return (report.get('result') or report)['sections']


def multi_energy(sections: dict) -> dict:
    section = sections['multi_energy']
    assert section['status'] == 'ok', section
    payload = section['payload']
    assert payload['attribution'] == 'per_load_slack' and payload['violations'] == []
    return payload


def test_the_dedicated_oracle_by_hand():
    assert dedicated_h2_unmet(40.0) == 2 * (40 - 18) == 44.0
    assert dedicated_h2_unmet(45.0) - dedicated_h2_unmet(40.0) == 10.0


@pytest.mark.parametrize('h2_mw', [40.0, 45.0], ids=['h2_40', 'h2_45'])
def test_h2_unmet_on_its_own_bus_is_attributed_to_hydrogen_alone(seam, h2_mw):
    s = sections(seam, hub(h2_mw, shared=False))
    payload = multi_energy(s)
    hand = dedicated_h2_unmet(h2_mw)
    assert payload['ens_by_carrier_mwh']['hydrogen'] == pytest.approx(hand, abs=MWH_TOL)
    assert payload['ens_by_carrier_mwh']['electrical'] == 0.0
    assert payload['ens_by_load_mwh'] == pytest.approx({'l_h2': hand}, abs=MWH_TOL)
    # The electrical ENS target counts the AC bus only.
    assert s['target']['payload']['system']['achieved_ens_mwh'] == 0.0


def test_a_shared_bus_reports_the_shed_total_not_an_invented_split(seam):
    payload = multi_energy(sections(seam, hub(40.0, shared=True)))
    total = HOURS * (AC_MW + 40.0 - GAS_MW)
    assert total == 20.0
    by_carrier = payload['ens_by_carrier_mwh']
    assert set(by_carrier) == {'electrical', 'hydrogen'}
    assert sum(by_carrier.values()) == pytest.approx(total, abs=MWH_TOL)
    assert sum(payload['ens_by_load_mwh'].values()) == pytest.approx(total, abs=MWH_TOL)
