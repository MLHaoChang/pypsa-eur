# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-33 LC-D13: the reserve margin, the margin loop, the frontier, ELCC and the campaign, through the chat tools.

Reserve margin coupling. `update_solver_config(reserve_margin=m)` + `run_simulation`
on a 2 h fixture: loads 100 then 150 MW at snapshot weights 3 then 1, so the
unweighted peak is 150 and the weighted one (load x weight) 300. The fixed
`base` unit (120 MW, EFORd 0.2) is firm for 96 MW; the extendable `peaker`
(EFORd 0.1) is firm for 0.9 MW per MW built. The LP must build
X = ((1 + m) x 150 - 96) / 0.9 MW: 93.33 at m 0.2, 110 at m 0.3 (the
mutation, +15 / 0.9 MW). The objective is capex x X x (4 h / 8760 h) plus the
dispatch: base serves 3 x 100 and 120 MW, the peaker the last 30 MW.

Margin loop (`run_margin_loop`). Flat 100 MW over 48 h at weight 1; a 130 MW
firm unit F and an extendable X, both EFORd 0.1 (MTTR 4 h), X capped at
150 MW. The probe solve needs no X, so m_tight = 0.9 x 130 / 100 - 1 = 0.17
and the start is m_tight x (1 + STEP_OVERSHOOT). Each iterate at margin m
builds X(m) = max(0, ((1 + m) x 100 - 117) / 0.9) MW and costs
480 EUR/MW x X + 48 h x 100 MW x 10 EUR/MWh. The two-unit COPT gives
LOLE = 48 x (0.01 + 0.09 x [X < 100]) h: 4.8 h below the step and 0.48 h from
X = 100 MW (m* = 1.07) up. Against a 1.92 h target the loop must raise past
not-met iterates, come down from met ones, and verify the cheapest met
margin, which is above m* (the cheapest verified margin is not the global
minimum). The mutation raises the load to 110 MW and moves m_tight and m*.

Frontier, under a campaign. Flat 100 MW over 4 h, 60 MW of fixed plant, a
`cheap` extendable (<= 20 MW, 50 EUR per MWh avoided) and a `dear` one
(220 EUR per MWh avoided), VoLL 100. Targets 5000 / 2500 / 1000 permyriad of
400 MWh cap ENS at 200 / 100 / 40 MWh. Cheap plant beats VoLL, so it is built
at every point and the shortfall left is 80 MWh: the first two caps do not
bind (cost 6400), the third forces 10 MW of dear plant (15,200). The knee is
point 1: the step to point 2 costs 8,800 for 40 MWh avoided, above
VoLL x 40. At VoLL 40 cheap plant no longer beats VoLL: costs 2400 / 5400 /
15,200 and the knee moves to 0. The campaign charges targets + 1 solves. The
identity total_system_cost = objective - VoLL x ENS is checked on its own,
on a plain solve at the 1000 permyriad cap, against the frontier's point.

ELCC (`run_mc_study(elcc_assets=...)`). A perfect 30 MW unit P beside an 80 MW
unit at q 0.1 against a flat 100 MW: with P the fleet fails only when the
80 MW unit is out. Replacing P by D MW of perfect capacity restores that
LOLE exactly when 80 + D >= 100, so ELCC = 20 MW (15 MW at 95 MW of load,
the mutation). The bisection returns the upper end of its last bracket, so
ELCC lies in [20, 20 + tol] with the engine's own tolerance.

Tolerances. The LPs have integer or 1/0.9 data and vertex optima: MW at
1e-6, EUR at 1e-6. The MC values are checked as LC-D12.standard_mc does:
the COPT value inside the reported 95 % CI on a fixed seed, and a met / not
met claim only where the COPT value clears the target by more than the
half-width.
"""
import time

import pandas as pd
import pypsa
import pytest

from services import chat_service, chat_tools
from services.adequacy import elcc
from services.adequacy.margin_lever import STEP_OVERSHOOT
from services.adequacy.margin_loop_runner import PROBE_MARGIN
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

MW_TOL = 1e-6
EUR_TOL = 1e-6


def call(name: str, **args) -> None:
    frames = list(chat_service._dispatch_real_tool_call(
        chat_tools._CHAT_SESSION.get(),
        {'id': f'lc33-d13-{time.monotonic_ns()}', 'name': name, 'input': args}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames


def wait_for(kind: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = chat_tools.get_adequacy_results(kind)
        if out.get('status') != 'running':
            assert out.get('status') in ('done', 'met') and not out.get('error'), out
            return out.get('result') or out
        time.sleep(0.2)
    raise AssertionError(f'{kind} did not finish')


def gas_unit(n: pypsa.Network, name: str, q: float, **attrs) -> None:
    n.add('Generator', name, bus='b', carrier='gas', outage_rate_value=q, outage_rate_basis='EFORd',
          mttr_hours=4.0, **attrs)


def one_bus(hours: int, load) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=hours, freq='h'))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add('Carrier', 'gas')
    n.add('Bus', 'b', carrier='AC')
    n.add('Load', 'l', bus='b', p_set=load)
    return n


def capex_per_mw(capital_cost: float, n: pypsa.Network) -> float:
    """The app charges the annual capex pro rata to the horizon's weighted hours."""
    return capital_cost * n.snapshot_weightings.objective.sum() / 8760.0


# ── reserve margin coupling ──────────────────────────────────────────────

RM_PEAK = 150.0
RM_BASE_FIRM = 0.8 * 120.0
RM_PEAKER_DERATE = 0.9
RM_CAPEX = 1000.0


def margin_network() -> pypsa.Network:
    n = one_bus(2, [100.0, RM_PEAK])
    n.snapshot_weightings.loc[n.snapshots[0], :] = 3.0
    gas_unit(n, 'base', 0.2, p_nom=120.0, marginal_cost=10.0)
    gas_unit(n, 'peaker', 0.1, p_nom=0.0, p_nom_extendable=True, p_nom_max=500.0,
             capital_cost=RM_CAPEX, marginal_cost=500.0)
    return n


def margin_oracle(m: float) -> tuple[float, float]:
    """(peaker MW, objective EUR) at margin ``m``."""
    n = margin_network()
    built = ((1 + m) * RM_PEAK - RM_BASE_FIRM) / RM_PEAKER_DERATE
    dispatch = 3 * 100.0 * 10.0 + (120.0 * 10.0 + 30.0 * 500.0)
    return built, capex_per_mw(RM_CAPEX, n) * built + dispatch


def test_the_fixture_peaks_differ_by_weighting():
    n = margin_network()
    load = n.loads_t.p_set['l']
    assert load.max() == RM_PEAK
    assert (load * n.snapshot_weightings.generators).max() == 300.0


@pytest.mark.parametrize('m', [0.2, 0.3], ids=['m_0.2', 'm_0.3'])
def test_the_margin_builds_the_derated_shortfall_through_the_lp(seam, m):
    built, objective = margin_oracle(m)
    assert built == pytest.approx({0.2: 280.0 / 3, 0.3: 110.0}[m], abs=1e-9)
    state = seam(margin_network(), reserve_margin=m)()
    assert state['status'] == 'completed', state
    assert state['objective'] == pytest.approx(objective, abs=EUR_TOL)
    rm = chat_tools.get_adequacy_results('reserve_margin')
    period, = rm['by_period']
    assert period['peak_mw'] == RM_PEAK
    assert period['required_mw'] == pytest.approx((1 + m) * RM_PEAK, abs=MW_TOL)
    assert period['firm_mw'] == pytest.approx((1 + m) * RM_PEAK, abs=MW_TOL)
    assert period['met'] and period['binding']
    assets = {a['name']: a for a in rm['assets']}
    assert assets['base']['firm_mw'] == pytest.approx(RM_BASE_FIRM, abs=MW_TOL)
    assert assets['peaker']['derate'] == pytest.approx(RM_PEAKER_DERATE)
    assert assets['peaker']['capacity_mw'] == pytest.approx(built, abs=MW_TOL)


def test_raising_the_margin_moves_the_build_and_the_objective_by_the_hand_delta(seam):
    """The mutation: 0.2 -> 0.3 adds 0.1 x 150 / 0.9 MW and its capex, nothing else."""
    low = seam(margin_network(), reserve_margin=0.2)()['objective']
    high = seam(margin_network(), reserve_margin=0.3)()['objective']
    delta_mw = 0.1 * RM_PEAK / RM_PEAKER_DERATE
    assert high - low == pytest.approx(capex_per_mw(RM_CAPEX, margin_network()) * delta_mw, abs=EUR_TOL)


# ── margin loop ──────────────────────────────────────────────────────────

ML_HOURS = 48
ML_F_MW, ML_Q = 130.0, 0.1
ML_CAPEX = 87600.0
ML_TARGET_H = 1.92
ML_DRAWS, ML_SEED = 400, 7


def loop_network(load: float) -> pypsa.Network:
    n = one_bus(ML_HOURS, load)
    gas_unit(n, 'F', ML_Q, p_nom=ML_F_MW, marginal_cost=10.0)
    gas_unit(n, 'X', ML_Q, p_nom=0.0, p_nom_extendable=True, p_nom_max=150.0,
             capital_cost=ML_CAPEX, marginal_cost=50.0)
    return n


def built_at(m: float, load: float) -> float:
    return max(0.0, ((1 + m) * load - (1 - ML_Q) * ML_F_MW) / (1 - ML_Q))


def copt_lole(x_mw: float, load: float) -> float:
    """Exact LOLE (h per horizon) of the two-unit fleet F + X against a flat load."""
    q = ML_Q
    states = [(ML_F_MW + x_mw, (1 - q) ** 2), (ML_F_MW, (1 - q) * q), (x_mw, q * (1 - q)), (0.0, q * q)]
    return ML_HOURS * sum(p for cap, p in states if cap < load)


def loop_oracle(load: float) -> dict:
    m_tight = (1 - ML_Q) * ML_F_MW / load - 1
    return {'m_tight': m_tight,
            'm_start': max(m_tight, 0.0) * (1 + STEP_OVERSHOOT),
            'm_step': ((1 - ML_Q) * load + (1 - ML_Q) * ML_F_MW) / load - 1}   # X(m*) = load


def run_loop(seam, load: float) -> dict:
    seam(loop_network(load))()
    call('run_margin_loop', target_lole_h=ML_TARGET_H, draws=ML_DRAWS, seed=ML_SEED, max_solves=8)
    return wait_for('margin_loop')


@pytest.mark.parametrize('load', [100.0, 110.0], ids=['load_100', 'load_110'])
def test_the_margin_loop_steps_to_the_cheapest_verified_margin(seam, load):
    oracle = loop_oracle(load)
    assert copt_lole(load - 1e-9, load) == pytest.approx(4.8) and copt_lole(load, load) == pytest.approx(0.48)
    out = run_loop(seam, load)
    assert out['status'] == 'met', out
    assert out['margin_tight'] == pytest.approx(oracle['m_tight'], abs=MW_TOL / load)
    assert oracle['m_start'] > PROBE_MARGIN
    assert out['margin0'] == pytest.approx(oracle['m_start'], abs=MW_TOL / load)
    its = [it for it in out['iterations'] if it['solve_status'] == 'ok']
    assert its[0]['lever_value'] == pytest.approx(oracle['m_start'], abs=MW_TOL / load)
    capex = capex_per_mw(ML_CAPEX, loop_network(load))
    verdicts = []
    for it in its:
        m = it['lever_value']
        x = built_at(m, load)
        assert it['cost_eur'] == pytest.approx(capex * x + ML_HOURS * load * 10.0, abs=EUR_TOL)
        lole = copt_lole(x, load)
        lo, hi = it['mc']['lole_ci']
        assert lo <= lole <= hi, (m, x, lole, it['mc'])
        half = (hi - lo) / 2
        assert abs(lole - ML_TARGET_H) > half, (m, lole, half)
        met = lole <= ML_TARGET_H
        assert (it['mc']['lole_hours'] <= ML_TARGET_H) == met, it
        verdicts.append((m, met, it['cost_eur']))
    # Not met -> every later margin is higher; met -> every later margin is lower.
    for i, (m, met, _cost) in enumerate(verdicts):
        for later, _met, _c in verdicts[i + 1:]:
            assert (later < m) if met else (later > m), verdicts
    met_rows = [(cost, m) for m, met, cost in verdicts if met]
    assert met_rows and any(not met for _m, met, _c in verdicts)
    assert out['lever_star'] == pytest.approx(min(met_rows)[1], abs=1e-12)
    assert out['lever_star'] >= oracle['m_step'] - 1e-9


def test_the_hand_step_moves_with_the_load():
    assert loop_oracle(100.0)['m_tight'] == pytest.approx(0.17)
    assert loop_oracle(100.0)['m_step'] == pytest.approx(1.07)
    assert loop_oracle(110.0)['m_tight'] == pytest.approx(117.0 / 110 - 1)
    assert loop_oracle(110.0)['m_step'] == pytest.approx(216.0 / 110 - 1)


# ── frontier and campaign ────────────────────────────────────────────────

FR_TARGETS = [5000, 2500, 1000]


def frontier_network() -> pypsa.Network:
    n = one_bus(4, 100.0)
    n.add('Generator', 'base', bus='b', carrier='gas', p_nom=60.0, marginal_cost=10.0)
    # 8760 x 30: 30 EUR per MWh of a 4 h horizon on top of 20 EUR/MWh dispatch.
    n.add('Generator', 'cheap', bus='b', carrier='gas', p_nom_extendable=True, p_nom_max=20.0,
          capital_cost=8760.0 * 30, marginal_cost=20.0)
    n.add('Generator', 'dear', bus='b', carrier='gas', p_nom_extendable=True, p_nom_max=500.0,
          capital_cost=8760.0 * 200, marginal_cost=20.0)
    return n


def frontier_oracle(voll: float) -> tuple[list[tuple[float, float]], int]:
    """[(ENS MWh, total system cost EUR)] per target, and the knee index."""
    hours, short = 4, 40.0
    per_mwh = {'cheap': 30.0 + 20.0, 'dear': 200.0 + 20.0}
    caps = {'cheap': 20.0, 'dear': float('inf')}
    points = []
    for target in FR_TARGETS:
        cap = target / 10000 * 100.0 * hours
        built, ens = {}, short * hours
        for tech in ('cheap', 'dear'):           # cheapest first
            free = per_mwh[tech] < voll           # built without a cap
            need = max(ens - cap, 0.0) / hours if not free else ens / hours
            built[tech] = min(caps[tech], need)
            ens -= built[tech] * hours
        cost = 60.0 * hours * 10.0 + sum(per_mwh[t] * built[t] * hours for t in built)
        points.append((ens, cost))
    knee = next((i for i in range(len(points) - 1)
                 if points[i][0] > points[i + 1][0]
                 and points[i + 1][1] - points[i][1] > voll * (points[i][0] - points[i + 1][0])), None)
    return points, knee


def test_the_frontier_oracle_by_hand():
    assert frontier_oracle(100.0) == ([(80.0, 6400.0), (80.0, 6400.0), (40.0, 15200.0)], 1)
    assert frontier_oracle(40.0) == ([(160.0, 2400.0), (100.0, 5400.0), (40.0, 15200.0)], 0)


@pytest.mark.parametrize('voll', [100.0, 40.0], ids=['voll_100', 'voll_40'])
def test_the_frontier_prices_each_target_and_the_campaign_charges_it(seam, voll):
    points, knee = frontier_oracle(voll)
    seam(frontier_network(), voll=voll)
    started = chat_tools.start_campaign('LC-33 frontier', 10)
    assert started['spent_solves'] == 0
    call('run_frontier_study', targets_permyriad=FR_TARGETS)
    out = wait_for('frontier')
    assert out['voll_eur_per_mwh'] == voll
    assert [p['target_permyriad'] for p in out['points']] == FR_TARGETS
    for p, (ens, cost) in zip(out['points'], points):
        assert p['status'] == 'ok', p
        assert p['point']['cap_mwh'] == pytest.approx(p['target_permyriad'] / 10000 * 400.0)
        assert p['point']['achieved_ens_mwh'] == pytest.approx(ens, abs=MW_TOL)
        assert p['point']['total_system_cost_eur'] == pytest.approx(cost, abs=EUR_TOL)
    assert out['knee'] == knee
    assert out['base_restored'] is True
    status = chat_tools.campaign_status()
    assert status['spent_solves'] == len(FR_TARGETS) + 1
    assert [(e['study'], e['solves_charged']) for e in status['entries']] == [('frontier', len(FR_TARGETS) + 1)]


def test_total_system_cost_is_the_objective_less_the_shed_at_voll(seam):
    """The identity on its own: a plain solve at the 1000 permyriad cap, read
    through the status, the lost-load reader and the adequacy report."""
    voll = 100.0
    state = seam(frontier_network(), voll=voll, ens_cap_permyriad=1000)()
    assert state['status'] == 'completed', state
    lost = chat_tools.get_results('lost_load')
    assert lost['voll_eur_per_mwh'] == voll
    report = chat_tools.get_adequacy_results('adequacy')
    total = report['cost']['total_system_cost_eur']
    assert total == pytest.approx(state['objective'] - voll * lost['total_mwh'], abs=EUR_TOL)
    assert report['metrics']['ens_mwh'] == pytest.approx(lost['total_mwh'], abs=MW_TOL)


# ── ELCC ─────────────────────────────────────────────────────────────────

def elcc_network(load: float) -> pypsa.Network:
    n = one_bus(48, load)
    gas_unit(n, 'G1', 0.1, p_nom=80.0, marginal_cost=10.0)
    gas_unit(n, 'P', 0.0, p_nom=30.0, marginal_cost=10.0)
    return n


@pytest.mark.parametrize('load', [100.0, 95.0], ids=['load_100', 'load_95'])
def test_a_perfect_units_elcc_is_the_capacity_that_restores_the_lole(seam, load):
    hand = load - 80.0                       # 80 + D >= load while G1 is up
    seam(elcc_network(load))
    call('run_mc_study', seed=3, draws=200, elcc_assets=[{'kind': 'generator', 'name': 'P'}])
    out = wait_for('mc')
    row, = out['elcc']
    assert row['status'] == 'ok', row
    assert row['nameplate_mw'] == 30.0
    tol = elcc.default_tol_mw(30.0)
    assert hand <= row['elcc_mw'] <= hand + tol, (row, tol)
    lo, hi = row['baseline_lole_ci']
    assert lo <= 48 * 0.1 <= hi                  # baseline COPT: G1 out, 30 MW < load
