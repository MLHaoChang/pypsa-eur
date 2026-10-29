# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-06 LC-D13: staged energization and the DtC stress, through the chat tools.

Both run in `run_eh_study` (weak_flexible pack) and are read back through
`get_adequacy_results('eh_reference_design')`.

Energization. The import Link carries a schedule in MW,
`links_t['eh_energization_mw']` = E(t), applied for the solve as
p_max_pu = min(own p_max_pu, E / p_nom). The pack's `import_p_nom_mw` rewrites
the Link's p_nom; the schedule must compose with each rewrite as min(E, cap)
in MW, never as a rescaled fraction. Oracle, per step:

    ENS = sum_h w_h x max(L_h - G - min(E_h, cap), 0)

(efficiency 1, one import Link, so no split). The weights differ by hour. The
cap steps go 80 -> 30 -> 80 MW. At 80 the schedule binds in every short hour.
At 30 the cap binds in hours 0, 1 and 4 (E = 50) and the schedule in hours
2 and 3 (E = 20), each with a shortfall. At each step a pass-through spy on
`run_simulation` reads the import Link's p_nom at the ens solve, so the edit is
shown to reach the solve. The mutation drops the schedule at cap 80, and ENS
falls by the hand delta. The ENS cap is lifted (10000 permyriad), so the LP
sheds exactly the shortfall; all inputs are integers, so ENS is compared at
abs 1e-6 MWh.

DtC. The P16 shared bus: a 30 MW critical Load and a 50 MW non-critical Load
on the hub, 10 MW of local generation, and the import islanded by the
contingency, over 2 h at weight 1. The bus aggregate (one VoLL for the bus)
counts the whole bus critical: 70 MW x 2 h = 140 MWh critical unserved.
per_load (critical Loads priced at VoLL x 1.05 in the stress re-dispatch)
sheds the non-critical Load first: max(0, 70 - 50) x 2 = 40 MWh, with
priority_exact on this loss-free network. The mutation raises local
generation to 40 MW: 80 MWh against 0.
"""
import time

import pandas as pd
import pypsa
import pytest

from services import chat_service, chat_tools
from tests.eh_stage_fixtures import certifiable_weak_network
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

VOLL = 10000.0
ENS_ABS = 1e-6
WEIGHTS = [1500.0, 1000.0, 1200.0, 900.0, 1100.0, 1000.0, 1060.0, 1000.0]
HUB_LOAD = [150.0, 120.0, 95.0, 90.0, 100.0, 60.0, 70.0, 60.0]
SCHEDULE = [50.0, 50.0, 20.0, 20.0, 50.0, 50.0, 20.0, 20.0]
OWN_MW = 60.0
CAP_HIGH, CAP_LOW = 80.0, 30.0


def run_study(seam, n, *, stages, pack_overrides, **extra) -> dict:
    seam(n, voll=VOLL)
    frames = list(chat_service._dispatch_real_tool_call(
        chat_tools._CHAT_SESSION.get(),
        {'id': f'lc06-d13-{time.monotonic_ns()}', 'name': 'run_eh_study',
         'input': {'archetype': 'weak_flexible', 'stages': stages,
                   'pack_overrides': {'ens_cap_permyriad': 10000, **pack_overrides}, **extra}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = chat_tools.get_adequacy_results('eh_study')
        if status.get('status') != 'running':
            assert status.get('status') == 'done', status
            break
        time.sleep(0.2)
    else:
        raise AssertionError('EH study did not finish')
    report = chat_tools.get_adequacy_results('eh_reference_design')
    return (report.get('result') or report)['sections']


# -- energization ------------------------------------------------------------

def energized_network(*, schedule=SCHEDULE) -> pypsa.Network:
    n = certifiable_weak_network()
    n.remove('Generator', 'peaker')
    n.generators.at['base', 'p_nom'] = OWN_MW
    for c in n.snapshot_weightings.columns:
        n.snapshot_weightings[c] = WEIGHTS
    n.loads_t.p_set['hub_load'] = HUB_LOAD
    if schedule is not None:
        n.links_t['eh_energization_mw'] = pd.DataFrame({'import_poc': schedule}, index=n.snapshots)
    return n


def ens_oracle(n: pypsa.Network, cap: float) -> float:
    store = n.links_t.get('eh_energization_mw')
    w = n.snapshot_weightings.generators
    own = n.generators.loc[n.generators.bus == 'hub', 'p_nom'].sum()
    ens = 0.0
    for i, h in enumerate(n.snapshots):
        imp = cap if store is None else min(store['import_poc'].iloc[i], cap)
        ens += w[h] * max(n.loads_t.p_set.at[h, 'hub_load'] - own - imp, 0.0)
    return ens


@pytest.fixture
def solve_p_nom(monkeypatch):
    """The import Link's p_nom as each solve starts (pass-through spy)."""
    from services import solver_service

    seen: list[float] = []
    run = solver_service.run_simulation

    def spy(cfg, network, *a, **k):
        seen.append(float(network.links.at['import_poc', 'p_nom']))
        return run(cfg, network, *a, **k)

    monkeypatch.setattr(solver_service, 'run_simulation', spy)
    return seen


def ens_at(seam, n, cap) -> float:
    sections = run_study(seam, n, stages=['apply_pack', 'ens_solve'],
                         pack_overrides={'import_p_nom_mw': cap})
    target = sections['target']
    assert target['status'] == 'ok', target
    return target['payload']['system']['achieved_ens_mwh']


def test_the_fixture_binds_each_way_at_the_low_cap():
    short = [L - OWN_MW - min(e, CAP_LOW) > 0 for L, e in zip(HUB_LOAD, SCHEDULE)]
    assert [h for h in range(8) if short[h] and SCHEDULE[h] > CAP_LOW] == [0, 1, 4]
    assert [h for h in range(8) if short[h] and SCHEDULE[h] < CAP_LOW] == [2, 3]
    assert all(e < CAP_HIGH for e in SCHEDULE)


def test_the_schedule_composes_as_min_with_each_capacity_edit(seam, solve_p_nom):
    n = energized_network()
    # Hand values: at 80, (40, 10, 15, 10) MW short in hours 0-3; at 30,
    # (60, 30, 15, 10, 10) MW short in hours 0-4.
    hand = {CAP_HIGH: 1500 * 40 + 1000 * 10 + 1200 * 15 + 900 * 10,
            CAP_LOW: 1500 * 60 + 1000 * 30 + 1200 * 15 + 900 * 10 + 1100 * 10}
    assert hand == {CAP_HIGH: 97000, CAP_LOW: 158000}
    got = []
    for step, cap in enumerate((CAP_HIGH, CAP_LOW, CAP_HIGH)):
        assert ens_oracle(n, cap) == pytest.approx(hand[cap], abs=ENS_ABS)
        ens = ens_at(seam, n, cap)
        assert solve_p_nom[-1] == cap, (step, solve_p_nom)
        assert ens == pytest.approx(hand[cap], abs=ENS_ABS), (step, cap, ens)
        got.append(ens)
    assert got[2] == got[0]


def test_dropping_the_schedule_moves_ens_by_the_hand_delta(seam, solve_p_nom):
    with_e = ens_at(seam, energized_network(), CAP_HIGH)
    n = energized_network(schedule=None)
    without = ens_at(seam, n, CAP_HIGH)
    assert solve_p_nom[-1] == CAP_HIGH
    # Without the schedule only hour 0 is short: 150 - 60 - 80 = 10 MW.
    assert ens_oracle(n, CAP_HIGH) == pytest.approx(1500 * 10, abs=ENS_ABS)
    assert without - with_e == pytest.approx(1500 * 10 - 97000, abs=ENS_ABS)


# -- DtC stress --------------------------------------------------------------

def shared_bus(local_mw: float) -> pypsa.Network:
    """The P16 shared bus, with the weak_flexible import tags."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=2, freq='h'))
    n.snapshot_weightings.loc[:, :] = 1.0
    for c in ('gas', 'AC'):
        n.add('Carrier', c)
    n.add('Bus', 'grid', carrier='AC')
    n.add('Bus', 'hub', carrier='AC')
    n.add('Load', 'hospital', bus='hub', p_set=30.0)
    n.add('Load', 'offices', bus='hub', p_set=50.0)
    n.add('Generator', 'local', bus='hub', carrier='gas', p_nom=local_mw, marginal_cost=80.0)
    n.add('Generator', 'grid_supply', bus='grid', carrier='gas', p_nom=500.0, marginal_cost=10.0)
    n.add('Link', 'import_poc', bus0='grid', bus1='hub', p_nom=100.0, p_nom_max=100.0,
          efficiency=1.0, carrier='AC')
    n.links['eh_role'] = ''
    n.links.at['import_poc', 'eh_role'] = 'grid_import'
    n.buses['eh_poc'] = False
    n.buses.at['grid', 'eh_poc'] = True
    n.buses['eh_critical'] = False      # the critical Load is named, not its bus
    return n


def dtc_row(seam, local_mw, attribution) -> tuple[dict, dict]:
    sections = run_study(
        seam, shared_bus(local_mw), stages=['apply_pack', 'ens_solve', 'dtc_stress'],
        pack_overrides={'import_p_nom_mw': 100.0},
        dtc_config={'critical_load_ids': ['hospital'], 'islanding_contingencies': ['import_poc'],
                    'attribution': attribution})
    dtc = sections['dtc']
    assert dtc['status'] == 'ok', dtc
    table = dtc['payload']
    assert table['attribution'] == attribution
    [row] = table['contingencies']
    assert row['status'] in ('ok', 'optimal'), row
    return table, row


@pytest.mark.parametrize('local_mw, bus_mwh, per_load_mwh', [
    (10.0, 70.0 * 2, 20.0 * 2),     # short 70: the bus counts it all; per_load sheds offices first
    (40.0, 40.0 * 2, 0.0),          # mutation: short 40 < offices 50, so the hospital is served
])
def test_bus_and_per_load_voll_each_set_the_critical_unserved(seam, local_mw, bus_mwh, per_load_mwh):
    _, bus = dtc_row(seam, local_mw, 'bus_aggregate_not_per_load')
    table, per_load = dtc_row(seam, local_mw, 'per_load')
    assert bus['critical_unserved_mwh'] == pytest.approx(bus_mwh, abs=ENS_ABS)
    assert bus['noncritical_unserved_mwh'] == pytest.approx(0.0, abs=ENS_ABS)
    assert per_load['critical_unserved_mwh'] == pytest.approx(per_load_mwh, abs=ENS_ABS)
    assert per_load['noncritical_unserved_mwh'] == pytest.approx(bus_mwh - per_load_mwh, abs=ENS_ABS)
    assert per_load['critical_unserved_by_load'] == pytest.approx({'hospital': per_load_mwh}, abs=ENS_ABS)
    assert bus['critical_unserved_mwh'] != pytest.approx(per_load['critical_unserved_mwh'], abs=1.0)
    assert table['priority_exact'] is True
