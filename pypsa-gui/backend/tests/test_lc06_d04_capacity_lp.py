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
the ordinary `run_simulation` chat tool, the answer through `get_results`,
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


def test_probe(lp):
    try:
        lp()
    except AssertionError:
        import json as j
        st = PyPSAService.get_solver_state()
        print('STATE', j.dumps({k: st.get(k) for k in ('status', 'error', 'message', 'error_kind')}, default=str)[:1500])
        print('LOG', str(chat_tools.get_simulation_log_history())[-1500:])
        raise
    import json
    print(json.dumps(chat_tools.get_results('economics_by_carrier'), default=str)[:1500])
    print(json.dumps(chat_tools.explain_investment('Generator', 'Cheap'), default=str)[:1500])
    print(json.dumps(chat_tools.get_vintage_results(), default=str)[:800])
    print(PyPSAService.get_network().objective)
    assert False
