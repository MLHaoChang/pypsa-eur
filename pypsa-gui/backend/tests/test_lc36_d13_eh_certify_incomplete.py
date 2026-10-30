# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-36 LC-D13: MC certification covers the ENS plan, not the finalist; an EH study cut short by its budget.

Fixture: `islanded_certify_network` (eh_stage_fixtures.py). One bus, a flat
100 MW Load, `base` 60 MW (EFORd 0.05) and `peaker` 45 MW (EFORd 0.10);
weights sum to one year, so `horizon_years` = 1 and a target in h/yr is the
target over the horizon. `run_eh_study` (weak_flexible, through the chat
tools) runs apply_pack, ens_solve, redundancy and mc_certify.

ENS plan versus finalist. The ENS plan keeps the two units. Losing either
sheds, so the two-unit COPT LOLE is 8760 x (1 - 0.95 x 0.90) = 1270.2 h/yr.
The redundancy compare selects `n1_generation`: a gas spare at 180 EUR/MWh
displaces the 300 EUR/MWh peaker's 40 MW of energy, and the option's cost
pins its size at 40 MW (base cost - 120 x 40 x 8760 + 40 x annualised). With
that spare, only losing `base` sheds unless both peaker and spare are out:
8760 x (0.05 + 0.95 x 0.10 x 0.05) = 479.6 h/yr. The certification CI holds
the ENS plan's 1270.2 and excludes the finalist's 479.6, and the redundancy
table says so in words (`finalists_mc_certified` False): finalist MC
certification is not built (`LC-D13.finalist_certification` is
`_unavailable`).

Verdict by hand (`certification_verdict`, eh_study.py): pass iff the CI's
upper bound <= target x horizon_years, fail iff its lower bound > target,
inconclusive otherwise; `certified` is the pass. Mutation: the target moves
through the three bands: 3 h/yr fails, the hand COPT LOLE 1270.2 (inside the
CI) is inconclusive, 8760 h/yr passes.

Budget cut. ens_solve charges one solve, so redundancy gets
`max_solves` = budget_solves - 1 (`_stage_redundancy`: `st.remaining()`). Its
loop runs the scenarios in order base, n1_generation, n1_conversion,
parallel_storage@1, @2, and stops before a solve once `solves_attempted`
reaches `max_solves`. At budget_solves 2 it solves `base` alone, stops at
`n1_generation`, flags `budget_exhausted` and selects nothing. The mutation
lifts the budget to 10: every scenario runs and `n1_generation` is selected.
"""
import pytest

from services import chat_tools
from services.adequacy.eh_study import certification_verdict
from tests.eh_stage_fixtures import islanded_certify_network
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_lc06_d13_energization_dtc import run_study
from tests.test_workflow_tools import helpers_state  # noqa: F401

HOURS_PER_YEAR = 8760.0
Q_BASE, Q_PEAKER, Q_SPARE = 0.05, 0.10, 0.05
PEAKER_MC, SPARE_MC = 300.0, 180.0
LOAD_MW, BASE_MW = 100.0, 60.0
STAGES = ['apply_pack', 'ens_solve', 'redundancy', 'mc_certify']
MC = {'draws': 200, 'seed': 7}


def ens_plan_lole() -> float:
    """Two units, each indispensable: shed unless both are up."""
    return HOURS_PER_YEAR * (1 - (1 - Q_BASE) * (1 - Q_PEAKER))


def finalist_lole() -> float:
    """Base 60 + peaker 45 + spare 40: base out sheds (85 MW); peaker out sheds only if the spare is out too."""
    return HOURS_PER_YEAR * (Q_BASE + (1 - Q_BASE) * Q_PEAKER * Q_SPARE)


def certify(seam, target_lole_h: float, **extra) -> dict:
    return run_study(seam, islanded_certify_network(), stages=STAGES,
                     pack_overrides={'ens_cap_permyriad': 2000, 'target_lole_h': target_lole_h},
                     mc=MC, **extra)


def test_the_certification_oracle_by_hand():
    assert ens_plan_lole() == pytest.approx(1270.2)
    assert finalist_lole() == pytest.approx(479.61)
    assert certification_verdict(lole_ci=(1155.0, 1414.0), target_h=3.0)[0] == 'fail'
    assert certification_verdict(lole_ci=(1155.0, 1414.0), target_h=ens_plan_lole())[0] == 'inconclusive'
    assert certification_verdict(lole_ci=(1155.0, 1414.0), target_h=HOURS_PER_YEAR)[0] == 'pass'


@pytest.mark.parametrize('target, verdict', [(3.0, 'fail'), (round(ens_plan_lole(), 1), 'inconclusive'),
                                             (HOURS_PER_YEAR, 'pass')],
                         ids=['fail_3h', 'inconclusive_copt', 'pass_8760h'])
def test_certification_covers_the_ens_plan_and_the_finalist_is_disclosed_uncertified(seam, target, verdict):
    sections = certify(seam, target)
    cert = sections['certification']
    assert cert['status'] == 'ok', cert
    p = cert['payload']
    assert p['horizon_years'] == 1.0
    assert p['target_lole_h_per_horizon'] == pytest.approx(target * p['horizon_years'])
    lo, hi = p['lole_ci']
    # The ENS plan's two-unit LOLE is inside the CI; the finalist's three-unit LOLE is not.
    assert lo <= ens_plan_lole() <= hi, p['lole_ci']
    assert not lo <= finalist_lole() <= hi, p['lole_ci']
    expected = 'pass' if hi <= target else 'fail' if lo > target else 'inconclusive'
    assert (p['verdict'], expected) == (verdict, verdict)
    report = chat_tools.get_adequacy_results('eh_reference_design')
    assert (report.get('result') or report)['certified'] is (verdict == 'pass')

    table = chat_tools.get_adequacy_results('eh_redundancy')
    assert table['finalists_mc_certified'] is False
    assert 'not each finalist' in table['finalists_mc_note']
    selected = table['selection']['selected']
    assert table['selection']['selected_id'] == 'n1_generation'
    [spare] = selected['applied']
    assert spare['name'] == 'eh_spare_gen' and spare['marginal_cost'] == SPARE_MC
    base_cost = next(o['cost_at_target_eur'] for o in table['options'] if o['scenario_id'] == 'base')
    spare_mw = LOAD_MW - BASE_MW
    assert selected['cost_at_target_eur'] == pytest.approx(
        base_cost - (PEAKER_MC - SPARE_MC) * spare_mw * HOURS_PER_YEAR
        + spare_mw * spare['annualised_cost_eur_per_mw_yr'], rel=1e-9)


@pytest.mark.parametrize('budget', [2, 10], ids=['budget_2', 'budget_10'])
def test_a_redundancy_compare_cut_by_the_budget_selects_nothing(seam, budget):
    certify(seam, 3.0, budget_solves=budget)
    table = chat_tools.get_adequacy_results('eh_redundancy')
    if budget == 2:
        assert table['solves_attempted'] == budget - 1
        assert table['budget_exhausted'] is True
        assert [o['scenario_id'] for o in table['options']] == ['base']
        assert table['selection'] is None
        assert table['selection_error'] == 'budget_solves exhausted before every option was solved'
    else:
        assert table['budget_exhausted'] is False
        assert table['selection']['selected_id'] == 'n1_generation'
