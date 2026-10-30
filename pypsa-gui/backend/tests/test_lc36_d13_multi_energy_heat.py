# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-36 LC-D13: heat unmet energy attributed apart from the electrical ENS, through the chat tools.

`run_eh_study` (strong_grid pack, stages apply_pack + ens_solve) fills the
`multi_energy` section of `get_adequacy_results('eh_reference_design')`; the
same driver as LC-33's H2 test (`test_lc33_d13_multi_energy.sections`).

Dedicated bus. 2 h at weight 1: the AC hub carries a 50 MW Load and 55 MW of
gas; a heat pump (10 MW electric, efficiency 3) feeds a 40 MW Load on its own
'urban central heat' bus. Every slack is priced at the same VoLL, and each MWh
into the heat pump avoids 3 MWh of heat shed against 1 MWh of electric shed,
so the heat pump saturates at 10 MW: it delivers 30 MW of heat and the AC
Load gets the 45 MW left. Heat unmet = 2 x (40 - 30) = 20 MWh, electrical
unmet = 2 x (50 - 45) = 10 MWh, by carrier and by Load; the electrical ENS
target counts the 10 MWh only. The mutation raises the heat Load by 5 MW:
heat unmet +10 MWh, electrical exactly 10 MWh again.

Shared bus. A 5 MW AC Load and the 40 MW heat Load both sit on the AC hub
with 30 MW of gas: 15 MW short, 30 MWh over 2 h. The shortfall exceeds the AC
Load, so at least 10 MW (20 MWh) must be heat. Pinned: the total, that heat
floor, and that the by-carrier and by-Load readers agree Load by Load (a
roll-up by the bus's carrier instead of the Load's would book heat as
electrical).

The Load carrier 'urban central heat' canonicalises to `heat` by substring
(`services/solver/assumptions.py` `_LOAD_HEAT_TOKENS`). All data are integers;
the shed is an LP vertex: 1e-6 MWh.
"""
import pandas as pd
import pypsa
import pytest

from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc33_d13_multi_energy import multi_energy, sections
from tests.test_workflow_tools import helpers_state  # noqa: F401

MWH_TOL = 1e-6
HOURS = 2
HEAT = 'urban central heat'
AC_MW, GAS_MW = 50.0, 55.0
HP_MW, HP_COP = 10.0, 3.0


def hub(heat_mw: float, *, shared: bool, ac_mw: float = AC_MW, gas_mw: float = GAS_MW) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=HOURS, freq='h'))
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ('AC', HEAT, 'gas'):
        n.add('Carrier', c)
    n.add('Bus', 'hub', carrier='AC')
    if not shared:
        n.add('Bus', 'heat', carrier=HEAT)
        n.add('Link', 'heat_pump', bus0='hub', bus1='heat', p_nom=HP_MW, efficiency=HP_COP, carrier=HEAT)
    n.add('Load', 'l_elec', bus='hub', carrier='AC', p_set=ac_mw)
    n.add('Load', 'l_heat', bus='hub' if shared else 'heat', carrier=HEAT, p_set=heat_mw)
    n.add('Generator', 'gas', bus='hub', carrier='gas', p_nom=gas_mw, marginal_cost=20.0)
    return n


def dedicated_unmet(heat_mw: float) -> tuple[float, float]:
    """(heat, electrical) unmet MWh: the heat pump runs first, at its rating."""
    hp_in = min(HP_MW, GAS_MW)
    heat = HOURS * max(heat_mw - hp_in * HP_COP, 0.0)
    elec = HOURS * max(AC_MW - (GAS_MW - hp_in), 0.0)
    return heat, elec


def test_the_dedicated_oracle_by_hand():
    assert dedicated_unmet(40.0) == (2 * (40 - 30), 2 * (50 - 45)) == (20.0, 10.0)
    assert dedicated_unmet(45.0)[0] - dedicated_unmet(40.0)[0] == 10.0
    assert dedicated_unmet(45.0)[1] == dedicated_unmet(40.0)[1]


@pytest.mark.parametrize('heat_mw', [40.0, 45.0], ids=['heat_40', 'heat_45'])
def test_heat_unmet_on_its_own_bus_is_attributed_to_heat_alone(seam, heat_mw):
    s = sections(seam, hub(heat_mw, shared=False))
    payload = multi_energy(s)
    heat, elec = dedicated_unmet(heat_mw)
    assert payload['ens_by_carrier_mwh'] == pytest.approx({'electrical': elec, 'heat': heat}, abs=MWH_TOL)
    assert payload['ens_by_load_mwh'] == pytest.approx({'l_elec': elec, 'l_heat': heat}, abs=MWH_TOL)
    # The electrical ENS target counts the AC Load only.
    assert s['target']['payload']['system']['achieved_ens_mwh'] == pytest.approx(elec, abs=MWH_TOL)


SHARED_AC_MW, SHARED_GAS_MW = 5.0, 30.0


def test_a_shared_bus_reports_the_forced_heat_shed_and_its_readers_agree(seam):
    payload = multi_energy(sections(seam, hub(40.0, shared=True, ac_mw=SHARED_AC_MW, gas_mw=SHARED_GAS_MW)))
    short = SHARED_AC_MW + 40.0 - SHARED_GAS_MW
    total, heat_floor = HOURS * short, HOURS * (short - SHARED_AC_MW)
    assert (total, heat_floor) == (30.0, 20.0)
    by_carrier, by_load = payload['ens_by_carrier_mwh'], payload['ens_by_load_mwh']
    assert by_carrier['heat'] >= heat_floor - MWH_TOL
    assert by_carrier['heat'] == pytest.approx(by_load.get('l_heat', 0.0), abs=MWH_TOL)
    assert by_carrier.get('electrical', 0.0) == pytest.approx(by_load.get('l_elec', 0.0), abs=MWH_TOL)
    assert sum(by_carrier.values()) == pytest.approx(total, abs=MWH_TOL)
    assert sum(by_load.values()) == pytest.approx(total, abs=MWH_TOL)
