# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-33 LC-D13: EH redundancy options, levers and DtC planning, through the chat tools.

Each runs in `run_eh_study` (weak_flexible pack) and is read back through
`get_adequacy_results` ('eh_redundancy', 'eh_levers', 'eh_dtc_planning').

Redundancy and levers use `certifiable_weak_network`: 8 h at weight 1095,
hub loads 90/100/110/120/110/100/90/80 MW, `base` 60 MW at 10 EUR/MWh,
`peaker` 40 MW at 300, and the import (pack cap 50 MW) from `grid_supply` at
5. Nothing is extendable, so at the 10 permyriad ENS cap nothing is shed and
each hour costs 5 x min(L, cap) + 10 x min(L - cap, 60) + 300 x the rest:

    base option (import 50):  1095 x 8900  =  9,745,500 EUR

`n1_generation` adds an extendable spare at 180 EUR/MWh priced from the
defaults pack (annualised capex + FOM per MW, read from the pack here). It
displaces the peaker in the one hour above 110 MW (10 MW), so it is built at
10 MW: 9,745,500 - 120 x 10 x 1095 + 10 x annualised. It is the cheapest
option meeting the target, so it is selected. The mutation trims that hour to
115 MW: the base option falls by 300 x 5 x 1095 and the spare to 5 MW.

The import-cap lever solves at 0, 25 and 50 MW: 0 MW cannot hold the ENS cap
(40 MW short in three hours against 876 MWh: infeasible), 25 MW costs
1095 x 43,250 = 47,358,750 and 50 MW the base 9,745,500. The mutation raises
the import price from 5 to 8 EUR/MWh: each option moves by 3 x cap x 8 x 1095.

DtC planning uses the P16 shared bus (LC-D13.eh_dtc) with an extendable local
unit (8760 EUR/MW/yr, 90 EUR/MWh) over 2 h at weight 1. The import is islanded,
and per_load keeps only the critical `hospital` (30 MW): the plan builds
30 - 10 = 20 MW, ENS 0, cost 2 x (10 x 80 + 20 x 90) + 20 x 8760 x 2/8760 =
5240. Under the bus aggregate the whole bus (80 MW) is retained: 70 MW built,
14,340. The mutation raises the hospital to 40 MW: 30 MW built, 7060.

Tolerances: integer data, vertex optima; EUR at 1e-6 relative (the costs
reach 5e7), MW at 1e-6.
"""
import pytest

from services import chat_tools
from services.adequacy.redundancy import REDUNDANCY_PACK_VERSION
from services.library.defaults_pack.pricing import price_from_pack
from tests.eh_stage_fixtures import certifiable_weak_network
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc06_d13_energization_dtc import run_study, shared_bus
from tests.test_workflow_tools import helpers_state  # noqa: F401

REL = 1e-6
MW_TOL = 1e-6
ENS_CAP = 10                                   # permyriad of 87,600 MWh
WEIGHT = 1095.0
PACK_IMPORT_MW = 50.0
SPARE_MC, PEAKER_MC = 180.0, 300.0


def hub_cost(loads, cap: float, import_price: float = 5.0) -> float:
    """Merit order import -> base (60 MW) -> peaker, no shedding."""
    total = 0.0
    for load in loads:
        imp = min(load, cap)
        base = min(load - imp, 60.0)
        peak = load - imp - base
        assert peak <= 40.0
        total += import_price * imp + 10.0 * base + PEAKER_MC * peak
    return WEIGHT * total


def hub_network(*, peak_hour_mw: float = 120.0, import_price: float = 5.0):
    n = certifiable_weak_network()
    n.loads_t.p_set.iloc[3, 0] = peak_hour_mw
    n.generators.at['grid_supply', 'marginal_cost'] = import_price
    return n


def spare_annualised() -> float:
    """The pack's annualised capex + FOM per MW for the spare's technology."""
    n = certifiable_weak_network()
    n.add('Generator', 'eh_spare_gen', bus='hub', carrier='gas', p_nom_extendable=True)
    return price_from_pack(n, 'generators', 'eh_spare_gen', 'gas-genset',
                           version=REDUNDANCY_PACK_VERSION)['annualised_cost_eur_per_mw_yr']


def test_the_hub_oracle_by_hand():
    loads = certifiable_weak_network().loads_t.p_set['hub_load'].tolist()
    assert hub_cost(loads, PACK_IMPORT_MW) == 1095 * 8900 == 9_745_500
    assert hub_cost(loads, 25.0) == 1095 * 43_250 == 47_358_750


# ── redundancy ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('peak_hour_mw', [120.0, 115.0], ids=['peak_120', 'peak_115'])
def test_the_n1_spare_is_priced_built_and_selected_by_hand(seam, peak_hour_mw):
    n = hub_network(peak_hour_mw=peak_hour_mw)
    loads = n.loads_t.p_set['hub_load'].tolist()
    base = hub_cost(loads, PACK_IMPORT_MW)
    spare_mw = max(loads) - 110.0                    # above import 50 + base 60
    annualised = spare_annualised()
    assert (PEAKER_MC - SPARE_MC) * WEIGHT > annualised   # worth building
    n1 = base - (PEAKER_MC - SPARE_MC) * spare_mw * WEIGHT + spare_mw * annualised

    run_study(seam, n, stages=['apply_pack', 'ens_solve', 'redundancy'],
              pack_overrides={'ens_cap_permyriad': ENS_CAP})
    table = chat_tools.get_adequacy_results('eh_redundancy')
    options = {o['scenario_id']: o for o in table['options']}
    assert options['base']['cost_at_target_eur'] == pytest.approx(base, rel=REL)
    assert options['base']['achieved_ens_mwh'] == pytest.approx(0.0, abs=MW_TOL)
    spare = options['n1_generation']
    [applied] = spare['applied']
    assert applied['name'] == 'eh_spare_gen'
    assert applied['annualised_cost_eur_per_mw_yr'] == pytest.approx(annualised, rel=1e-12)
    assert spare['cost_at_target_eur'] == pytest.approx(n1, rel=REL)
    assert spare['meets_target'] is True
    feasible = [o for o in table['options'] if o.get('meets_target') is True and not o.get('not_applicable')]
    assert min(feasible, key=lambda o: o['cost_at_target_eur'])['scenario_id'] == 'n1_generation'
    assert table['selection']['selected_id'] == 'n1_generation'


# ── levers ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('import_price', [5.0, 8.0], ids=['price_5', 'price_8'])
def test_each_import_cap_costs_the_hand_merit_order(seam, import_price):
    n = hub_network(import_price=import_price)
    loads = n.loads_t.p_set['hub_load'].tolist()
    run_study(seam, n, stages=['apply_pack', 'ens_solve', 'levers'],
              pack_overrides={'ens_cap_permyriad': ENS_CAP, 'levers': {'import_cap': True}})
    table = chat_tools.get_adequacy_results('eh_levers')
    assert table['kind'] == 'import_cap'
    options = {o['value']: o for o in table['options']}
    assert set(options) == {0.0, 25.0, 50.0}
    assert options[0.0]['condition'] == 'infeasible' and options[0.0]['cost_at_target_eur'] is None
    for cap in (25.0, 50.0):
        assert options[cap]['status'] == 'ok', options[cap]
        assert options[cap]['cost_at_target_eur'] == pytest.approx(hub_cost(loads, cap, import_price), rel=REL)
        assert options[cap]['achieved_ens_mwh'] == pytest.approx(0.0, abs=MW_TOL)
    # The mutation's hand delta: 3 EUR/MWh on every imported MWh.
    for cap in (25.0, 50.0):
        assert hub_cost(loads, cap, 8.0) - hub_cost(loads, cap, 5.0) == pytest.approx(3 * cap * 8 * WEIGHT)


# ── DtC planning ─────────────────────────────────────────────────────────

LOCAL_MW = 10.0
NEW_CAPEX, NEW_MC = 8760.0, 90.0


def planning_network(hospital_mw: float):
    n = shared_bus(LOCAL_MW)
    n.loads.at['hospital', 'p_set'] = hospital_mw
    n.add('Generator', 'local_new', bus='hub', carrier='gas', p_nom_extendable=True, p_nom_max=200.0,
          capital_cost=NEW_CAPEX, marginal_cost=NEW_MC)
    return n


def planning_oracle(retained_mw: float) -> tuple[float, float]:
    built = retained_mw - LOCAL_MW
    return built, 2 * (LOCAL_MW * 80.0 + built * NEW_MC) + built * NEW_CAPEX * 2 / 8760


def planning_row(seam, hospital_mw: float, attribution: str) -> dict:
    sections = run_study(
        seam, planning_network(hospital_mw), stages=['apply_pack', 'ens_solve', 'dtc_planning'],
        pack_overrides={'import_p_nom_mw': 100.0, 'ens_cap_permyriad': 100},
        dtc_config={'critical_load_ids': ['hospital'], 'islanding_contingencies': ['import_poc'],
                    'attribution': attribution})
    assert sections['dtc']['status'] == 'ok', sections['dtc']
    table = chat_tools.get_adequacy_results('eh_dtc_planning')
    assert table['mode'] == 'planning' and table['attribution'] == attribution
    [row] = table['contingencies']
    assert row['contingency'] == 'import_poc' and row['status'] == 'ok', row
    return row


@pytest.mark.parametrize('hospital_mw, attribution, retained', [
    (30.0, 'per_load', 30.0),
    (30.0, 'bus_aggregate_not_per_load', 80.0),     # the whole bus is critical
    (40.0, 'per_load', 40.0),                       # mutation: +10 MW critical
], ids=['per_load', 'bus_aggregate', 'hospital_40'])
def test_the_islanded_plan_builds_the_retained_critical_shortfall(seam, hospital_mw, attribution, retained):
    built, cost = planning_oracle(retained)
    assert planning_oracle(30.0) == (20.0, 5240.0)
    row = planning_row(seam, hospital_mw, attribution)
    assert row['built_p_nom_mw'] == pytest.approx(built, abs=MW_TOL)
    assert row['cost_at_target_eur'] == pytest.approx(cost, rel=REL)
    assert row['achieved_ens_mwh'] == pytest.approx(0.0, abs=MW_TOL)
    kept = row['retained_critical']
    per_load = attribution == 'per_load'
    assert kept['critical_loads'] == (['hospital'] if per_load else ['hospital', 'offices'])
    assert kept['zeroed_load_ids'] == (['offices'] if per_load else [])
