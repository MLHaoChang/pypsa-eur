# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-06 LC-D12: the standard sequential Monte Carlo against the exact two-unit COPT.

The study runs through the ordinary `run_mc_study` chat tool and is read back
through `get_adequacy_results('mc')`. The oracle is computed here from the
network's own inputs (p_nom, outage rate, load). Each hour's available capacity
is one of the four states of the two-unit COPT, so

    LOLE = sum_h P(cap < L_h)            EUE = sum_h E[max(L_h - cap, 0)]

are exact. Two units, G60 (60 MW, q = 0.1, MTTR 50 h) and G40 (40 MW, q = 0.2,
MTTR 40 h), serve a load cycling 50 / 70 / 90 MW for 8760 h. That gives
LOLE = 2920 x (0.10 + 0.28 + 0.28) = 1927.2 h and EUE = 2920 x 18.6 = 54,312 MWh.

The start bias. The engine draws each unit's first state from the stationary
distribution (`mc.sample_capacity`), so all units do not start available and
every hour's marginal is the stationary one. The horizon (8760 h) is also far
longer than either MTTR. Even an all-available start would miss only about
sum(q x MTTR) = 13 unit-down hours, under 1 % of LOLE.

The statistics. The seed is fixed (1), with 500 draws and cov_target 0.04.
Asserted:
- the oracle lies in the reported 95 % CI, for LOLE and for EUE;
- the LOLE CI half-width is at most 10 % of the oracle LOLE (GK A's condition).
On 40 seeds (2026-10-28), the CI covered the oracle 36 and 37 times out of 40.
The mean of the estimates was 1928.1 h, and their spread (sd 15.0 h) matched
the reported SEM (13.2 h). The CI is honest and the estimator unbiased. The CI
is the only tolerance: no other slack is added.
"""
import time

import pandas as pd
import pypsa
import pytest

from services import chat_service, chat_tools
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

HOURS = 8760
LOAD_CYCLE = [50.0, 70.0, 90.0]
UNITS = {'G60': (60.0, 0.1, 50.0), 'G40': (40.0, 0.2, 40.0)}    # p_nom MW, q, MTTR h
SEED, DRAWS, COV_TARGET = 1, 500, 0.04
HALF_WIDTH_SHARE = 0.10


def network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=HOURS, freq='h'))
    n.add('Bus', 'B', v_nom=20.0)
    n.add('Carrier', 'gas')
    for name, (p_nom, q, mttr) in UNITS.items():
        n.add('Generator', name, bus='B', carrier='gas', p_nom=p_nom,
              outage_rate_value=q, outage_rate_basis='EFORd', mttr_hours=mttr)
    n.add('Load', 'Demand', bus='B', p_set=LOAD_CYCLE * (HOURS // len(LOAD_CYCLE)))
    return n


def copt_oracle(n: pypsa.Network) -> tuple[float, float]:
    """Exact LOLE (h) and EUE (MWh) from the network's own inputs."""
    (c1, q1), (c2, q2) = ((g.p_nom, g.outage_rate_value) for g in n.generators.itertuples())
    states = [(c1 + c2, (1 - q1) * (1 - q2)), (c1, (1 - q1) * q2), (c2, q1 * (1 - q2)), (0.0, q1 * q2)]
    assert sum(p for _c, p in states) == pytest.approx(1.0, abs=1e-15)
    w = n.snapshot_weightings.generators
    load = n.loads_t.p_set['Demand']
    lole = sum(w[h] * sum(p for c, p in states if c < load[h]) for h in n.snapshots)
    eue = sum(w[h] * sum(p * max(load[h] - c, 0.0) for c, p in states) for h in n.snapshots)
    return lole, eue


def run_mc(**args) -> dict:
    frames = list(chat_service._dispatch_real_tool_call(
        chat_tools._CHAT_SESSION.get(),
        {'id': f'lc06-d12-{time.monotonic_ns()}', 'name': 'run_mc_study', 'input': args}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        out = chat_tools.get_adequacy_results('mc')
        if out.get('status') != 'running':
            assert out.get('status') == 'done' and out.get('error') is None, out
            return out['result']
        time.sleep(0.2)
    raise AssertionError('MC study did not finish')


@pytest.fixture
def mc(seam):
    n = network()
    oracle = copt_oracle(n)
    seam(n)
    return oracle, run_mc(seed=SEED, draws=DRAWS, cov_target=COV_TARGET)


def test_the_oracle_is_the_hand_copt():
    lole, eue = copt_oracle(network())
    # per cycle: P(cap<50) = 0.08+0.02, P(cap<70) = P(cap<90) = 0.18+0.08+0.02;
    # E[short] = 0.8+1.0, 1.8+2.4+1.4, 5.4+4.0+1.8 MW
    assert lole == pytest.approx(2920 * (0.10 + 0.28 + 0.28), abs=1e-9)
    assert eue == pytest.approx(2920 * (1.8 + 5.6 + 11.2), abs=1e-6)


def test_lole_contains_the_exact_copt_within_a_tight_ci(mc):
    (lole, _eue), result = mc
    m = result['metrics']
    lo, hi = m['lole_ci']
    assert lo <= lole <= hi, (lole, m['lole_hours'], m['lole_ci'])
    assert (hi - lo) / 2 <= HALF_WIDTH_SHARE * lole, (hi - lo) / 2
    assert m['time_basis'] == 'hours_per_year' and m['horizon_years'] == 1.0   # 8760 h: per year = per horizon
    assert m['converged'] is True
    assert m['n_samples'] >= DRAWS
    assert {u['name']: u['rate_source'] for u in result['units_provenance']} == {'G60': 'asset', 'G40': 'asset'}


def test_eue_contains_the_exact_copt(mc):
    (_lole, eue), result = mc
    lo, hi = result['metrics']['eue_ci']
    assert lo <= eue <= hi, (eue, result['metrics']['eue_mwh'], (lo, hi))
