"""LC-06 LC-D04: a capacity expansion with a unique optimum, by hand.

One bus, 10 MW of demand in two snapshots weighted 4380 h each, so the horizon
is exactly one year and the app's periodised capital cost (capital_cost x
nyears, nyears = sum of weights / 8760, the validator's
snapshot_weights_nyears_off) is the annual figure itself. Cheap is extendable
to at most 6 MW at 60,000 EUR/MW/a plus 10 EUR/MWh; Backup to 100 MW at 20,000
EUR/MW/a plus 50 EUR/MWh (the validator refuses an unbounded extendable).
Run flat out for the year, one MW of Cheap costs 60,000 + 8760 x 10 = 147,600
EUR and one MW of Backup 20,000 + 8760 x 50 = 458,000, so the unique optimum
builds Cheap to its cap and Backup the remaining 4 MW. Raising the cap by 1 MW
moves one MW from Backup to Cheap and lowers the objective by the difference.

Every number below is computed here from those inputs. The run goes through
the ordinary `run_simulation` chat tool, the answer through `get_results` (objective_decomposition, economics_by_carrier),
`explain_investment` and `get_vintage_results`, and the cap edit through the
authenticated generator route.
"""
import pandas as pd
import pypsa
import pytest

from services import chat_service, chat_tools
from services.pypsa_service import PyPSAService
from tests.test_oj01_latest_loadflow_journey import _solve_once
from tests.test_workflow_tools import helpers_state  # noqa: F401

NAME = 'lc06-d04-lp'
DEMAND_MW, WEIGHTS = 10.0, (4380.0, 4380.0)
NYEARS = sum(WEIGHTS) / 8760
CHEAP = {'capital_cost': 60_000.0, 'marginal_cost': 10.0, 'p_nom_max': 6.0}
BACKUP = {'capital_cost': 20_000.0, 'marginal_cost': 50.0, 'p_nom_max': 100.0}


def per_mw(gen):
    return gen['capital_cost'] * NYEARS + sum(WEIGHTS) * gen['marginal_cost']


def objective(cap):
    return cap * per_mw(CHEAP) + (DEMAND_MW - cap) * per_mw(BACKUP)


def build() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=len(WEIGHTS), freq='h'))
    n.snapshot_weightings.loc[:, :] = pd.DataFrame({c: WEIGHTS for c in n.snapshot_weightings.columns},
                                                   index=n.snapshots)
    n.add('Bus', 'B', v_nom=20)
    n.add('Carrier', 'cheap')
    n.add('Carrier', 'backup')
    n.add('Generator', 'Cheap', bus='B', carrier='cheap', p_nom_extendable=True, **CHEAP)
    n.add('Generator', 'Backup', bus='B', carrier='backup', p_nom_extendable=True, **BACKUP)
    n.add('Load', 'Demand', bus='B', p_set=DEMAND_MW)
    return n


@pytest.fixture
def lp(client, session_ctx, install_network, monkeypatch):
    import harness.confirm as harness_confirm

    install_network(build())
    assert client.post(f'/api/projects/{NAME}').status_code == 200
    binding = PyPSAService.bind_request_context(session_ctx(client))
    cell = PyPSAService.bind_turn_cell()
    try:
        with monkeypatch.context() as m:
            m.setattr(harness_confirm, 'AUTO_APPROVE_TIERS', frozenset({'execution'}))
            session = chat_service.ChatSession()
            chat_tools.set_chat_session(session)
            yield lambda: _solve_once(session)
        chat_tools.set_chat_session(None)
    finally:
        PyPSAService._turn_cell.reset(cell)
        PyPSAService.reset_request_context(binding)


def carriers(result):
    assert result['results_current'], result
    return result['by_carrier']


def solved_objective():
    return chat_tools.get_results('objective_decomposition')['n_objective']


def sizing(name):
    return chat_tools.explain_investment('Generator', name)['sizing']


def test_the_unique_optimum_matches_the_hand_solution(lp):
    lp()
    assert solved_objective() == pytest.approx(objective(6.0))

    built = {'cheap': CHEAP['p_nom_max'], 'backup': DEMAND_MW - CHEAP['p_nom_max']}
    by_carrier = carriers(chat_tools.get_results('economics_by_carrier'))
    for carrier, gen in (('cheap', CHEAP), ('backup', BACKUP)):
        energy_mwh = built[carrier] * sum(WEIGHTS)
        row = by_carrier[carrier]
        assert row['capex_meur']['total'] == pytest.approx(built[carrier] * gen['capital_cost'] * NYEARS / 1e6)
        assert row['opex_meur']['total'] == pytest.approx(energy_mwh * gen['marginal_cost'] / 1e6)
        assert row['dispatch_gwh']['total'] == pytest.approx(energy_mwh / 1e3)

    cheap, backup = sizing('Cheap'), sizing('Backup')
    assert (cheap['existing'], cheap['optimised'], cheap['added']) == pytest.approx((0, 6, 6))
    assert cheap['binding_constraint'] == 'at_upper_bound'
    assert (backup['existing'], backup['optimised'], backup['added']) == pytest.approx((0, 4, 4))
    assert backup['binding_constraint'] not in ('at_upper_bound', 'at_lower_bound', 'not_built', 'not_solved')


def test_raising_the_cap_lowers_the_objective_by_the_cost_difference(lp, client):
    older = lp()
    response = client.put('/api/network/generators/Cheap', json={'name': 'Cheap', 'bus': 'B', 'p_nom_max': 7.0})
    assert response.status_code == 200, response.text
    lp()
    # One MW moves from Backup to Cheap: the delta is the per-MW cost gap, from the inputs.
    delta = per_mw(BACKUP) - per_mw(CHEAP)
    assert solved_objective() == pytest.approx(objective(6.0) - delta)
    assert (sizing('Cheap')['optimised'], sizing('Backup')['optimised']) == pytest.approx((7, 3))
    # The older run keeps its own capacity answer (LC-03 retained family).
    kept = chat_tools.get_results('economics_by_carrier', run_id=older['run_id'])
    assert (kept['status'], kept['scope']) == ('ok', 'historical'), kept
    assert kept['data']['by_carrier']['cheap']['capex_meur']['total'] == pytest.approx(6 * CHEAP['capital_cost'] / 1e6)


def test_a_single_period_network_reports_no_vintages(lp):
    lp()
    assert chat_tools.get_vintage_results() == {'results': {}}
