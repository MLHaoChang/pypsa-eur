"""
Phase 8 acceptance (spec §7) — does the lever actually move the metric?

The margin exists because Phase 7's loop kept reporting `unreachable`: on a
network whose firm capacity already covers demand the LP sheds nothing at any
cap, so no cap changes the plan and the MC's loss of load comes entirely from
outages the proxy never models. The margin is the lever that answers that. A
lever that moves nothing would be worse than none at all, so these three tests
are the phase's own falsification attempt.

**THE MARGIN IS COMPUTED, NEVER CHOSEN.** The adversarial review killed v1's
acceptance test by arithmetic: a hardcoded `m = 0.3` buys 20 MW on this
fixture, and the loss-of-load state is "one 100 MW unit out", where 120 MW
against a 150 MW load is STILL a shortfall hour. LOLE could not move until
m >= 0.49, so the test would have failed and the plan's own stop-rule would
have killed a sound phase. The general fact, which is why the calibration is
here rather than a constant:

    A reserve margin sized in MW moves EUE CONTINUOUSLY but moves LOLE in
    STEPS, and the step is set by the largest unit's capacity.

`_threshold_margin` derives that step from the fixture. A3 pins the other side
of it: below the threshold EUE falls while LOLE does not.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy.mc import mc_adequacy, snapshot_inputs
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, run_simulation

SNAPSHOTS = 48
LOAD_MW = 150.0
UNIT_MW = 100.0          # x2, and the largest single unit — the LOLE step
UNIT_EFORD = 0.12
PEAKER_EFORD = 0.05
DRAWS = 400
SEED = 11


def _network() -> pypsa.Network:
    """Two firm units that together cover the load, plus an expensive
    extendable peaker the LP has no economic reason to build.

    Sized so that the ONE-UNIT-OUT state is short: 100 MW against a 150 MW
    load. That is the state the MC's LOLE actually counts, and closing it is
    what a margin has to buy before LOLE can move at all.
    """
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=SNAPSHOTS, freq="h"))
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    for name in ("unit_1", "unit_2"):
        n.add("Generator", name, bus="b", carrier="gas", p_nom=UNIT_MW,
              marginal_cost=10.0, outage_rate_value=UNIT_EFORD,
              outage_rate_basis="EFORd", mttr_hours=24.0)
    n.add("Generator", "peaker", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=500.0, capital_cost=5_000_000.0,
          marginal_cost=500.0, outage_rate_value=PEAKER_EFORD,
          outage_rate_basis="EFORd", mttr_hours=12.0)
    return n


def _threshold_margin() -> float:
    """The smallest margin whose plan survives losing the largest unit.

    Derived, not chosen (spec §7): the peaker must cover
    `peak + largest_unit - total_fixed_nameplate`, and the margin that forces
    exactly that much is what the derated LHS demands of the peak.
    """
    firm_fixed = 2 * UNIT_MW * (1 - UNIT_EFORD)              # 176.0
    needed_peaker = LOAD_MW + UNIT_MW - 2 * UNIT_MW          # 50.0
    required = firm_fixed + needed_peaker * (1 - PEAKER_EFORD)
    return required / LOAD_MW - 1.0                          # ~0.49


def _solve(n: pypsa.Network, margin: float) -> tuple[str, float]:
    """Solve at a margin; return (condition, built peaker MW)."""
    PyPSAService.set_network(n)
    cfg = SolverConfig(solver_name="highs", reserve_margin=margin)
    _status, condition = run_simulation(
        cfg, n, PyPSAService.get_lock(), threading.Event(),
        queue.SimpleQueue(), state_update=lambda **kw: None)
    built = float(n.generators.at["peaker", "p_nom_opt"])
    return str(condition), built


def _evaluate(n: pypsa.Network) -> dict:
    """MC on the plan the solve produced.

    `keep_zero_capacity=True` is load-bearing HERE, not just in the wrapper:
    the m=0 plan has no peaker and the m* plan does, so without the superset
    fleet the two runs would sample different unit COUNTS and their RNG
    substreams would not line up. With it, the comparison is genuinely paired.
    """
    inputs = snapshot_inputs(n, keep_zero_capacity=True)
    return mc_adequacy(inputs, draws=DRAWS, seed=SEED, max_draws=DRAWS)


@pytest.mark.slow
def test_a1_the_margin_builds_capacity_a_zero_margin_solve_does_not():
    """★ A1 — the lever moves capacity."""
    m_star = _threshold_margin()
    cond0, built0 = _solve(_network(), 0.0)
    cond1, built1 = _solve(_network(), m_star)
    assert cond0 == "optimal" and cond1 == "optimal", (cond0, cond1)
    assert built0 == pytest.approx(0.0, abs=1e-6), (
        "the fixture is vacuous: the LP built the peaker on economics alone, "
        f"so the margin proves nothing (built {built0} MW at m=0)")
    assert built1 > built0 + 1.0, (
        f"m={m_star:.4f} must force capacity; built {built1} MW")


@pytest.mark.slow
def test_a2_that_capacity_lowers_mc_lole_with_separated_intervals():
    """★ A2 — the capacity moves the metric, at a COMPUTED margin.

    Intervals, not point estimates: the same seed drives both runs over the
    same fleet, so this is a paired comparison, and the no-margin lower bound
    must clear the with-margin upper bound. Overlapping blobs would prove
    nothing.
    """
    m_star = _threshold_margin()
    n0 = _network()
    _solve(n0, 0.0)
    base = _evaluate(n0)

    n1 = _network()
    _cond, built = _solve(n1, m_star)
    kept = _evaluate(n1)

    assert base["lole_hours"] > 0, (
        "vacuous fixture: nothing is ever short at m=0, so no margin could "
        "lower anything")
    lo_base, _hi_base = base["lole_ci"]
    _lo_kept, hi_kept = kept["lole_ci"]
    assert kept["lole_hours"] < base["lole_hours"], (
        f"LOLE did not fall: {base['lole_hours']} -> {kept['lole_hours']} "
        f"with {built} MW built at m={m_star:.4f}")
    assert lo_base > hi_kept, (
        "the intervals overlap, so the improvement is not established: "
        f"base CI {base['lole_ci']} vs margin CI {kept['lole_ci']}")


@pytest.mark.slow
def test_a3_below_the_threshold_eue_falls_but_lole_does_not():
    """★ A3 — the step behaviour the review's arithmetic predicted.

    A margin under the largest-unit threshold buys real megawatts, so the
    shortfall in each short hour shrinks (EUE falls) — but every one of those
    hours is still a shortfall hour, so LOLE does not move. This is why a
    hardcoded margin was the wrong acceptance test, and it is worth pinning in
    its own right: a user who raises the margin a little and sees LOLE unmoved
    is looking at arithmetic, not a broken engine.
    """
    m_small = 0.2
    assert m_small < _threshold_margin(), "the fixture no longer tests a step"

    n0 = _network()
    _solve(n0, 0.0)
    base = _evaluate(n0)

    n1 = _network()
    _cond, built = _solve(n1, m_small)
    small = _evaluate(n1)

    assert built > 1.0, f"m={m_small} should still buy megawatts; got {built}"
    assert small["eue_mwh"] < base["eue_mwh"], (
        f"EUE should fall continuously: {base['eue_mwh']} -> {small['eue_mwh']}")
    assert small["lole_hours"] == pytest.approx(base["lole_hours"], rel=1e-9), (
        "LOLE moved below the largest-unit threshold, so the step this test "
        f"documents is not where it was thought: {base['lole_hours']} -> "
        f"{small['lole_hours']} with {built} MW built")
