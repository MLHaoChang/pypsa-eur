"""
The cost-vs-availability frontier (spec §5.6, Phase 5).

The ε-constraint study: sweep the reliability target and record what the
least-cost plan meeting it costs. This is the trade-off the feature is named
for — CapEx/OpEx against availability of energy — as a curve, where Phases
1–4 give a single point on it.

Fixture: 100 MW load, 60 MW cheap firm, and an EXPENSIVE extendable peaker.
Shedding is economic at a loose target, so tightening has to buy its way out
by building, and cost must rise monotonically as the target tightens. That
monotonicity is the property the whole study exists to show, so it is
asserted rather than eyeballed off a chart.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pypsa
import pytest

from services.adequacy.frontier import (
    DEFAULT_TARGETS_PERMYRIAD,
    FrontierBudgetError,
    FrontierConfigError,
    knee_index,
    non_convexity_warning,
    run_frontier_sweep,
)
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

WEIGHT = 3.0
N = 4
LOAD_MW = 100.0
CHEAP_MW = 60.0
VOLL = 3000.0


def _network(committable: bool = False) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=N, freq="h"))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=LOAD_MW)
    n.add("Generator", "cheap", bus="b", carrier="gas", p_nom=CHEAP_MW,
          marginal_cost=10.0, committable=committable)
    n.add("Generator", "peak", bus="b", carrier="gas", p_nom=0.0,
          p_nom_extendable=True, p_nom_max=200.0,
          capital_cost=400_000.0, marginal_cost=250.0)
    return n


def _run(n, targets, **cfg_kw):
    PyPSAService.set_network(n)
    cfg = SolverConfig(**{"solver_name": "highs", "voll": VOLL, **cfg_kw})
    return run_frontier_sweep(n, PyPSAService.get_lock(), cfg, targets,
                              log_queue=queue.SimpleQueue())


def _ok(res):
    return [p for p in res["points"] if p["status"] == "ok"]


def test_the_curve_is_monotone_in_cost_as_the_target_tightens():
    res = _run(_network(), [200.0, 100.0, 50.0, 20.0])
    ok = _ok(res)
    assert len(ok) >= 3, res["points"]
    # loosest-first, so cost must be non-decreasing and ENS non-increasing
    costs = [p["point"]["total_system_cost_eur"] for p in ok]
    ens = [p["point"]["achieved_ens_mwh"] for p in ok]
    assert costs == sorted(costs), costs
    assert ens == sorted(ens, reverse=True), ens
    # and the study must actually MOVE, or it is measuring nothing
    assert costs[-1] > costs[0], (
        "no cost gradient across the swept range — the fixture no longer "
        "makes reliability something you have to pay for")
    assert ens[0] > ens[-1]


def test_every_point_respects_its_own_cap_and_carries_provenance():
    res = _run(_network(), [200.0, 50.0, 20.0])
    for p in _ok(res):
        pt = p["point"]
        assert pt["achieved_ens_mwh"] <= pt["cap_mwh"] + 1e-6, p
        # Each point is an independent analysis and must say which engine
        # produced it — the frontier is not allowed to launder provenance.
        assert pt["engine"] == "lp_proxy"
        assert pt["fidelity"] == "deterministic_scenario"


def test_an_unreachable_target_is_reported_not_silently_dropped():
    """
    A standard no plan can meet is a real answer. Interpolating over the gap
    would draw a curve through a point that does not exist.
    """
    # Cap the peaker so a hard floor of unserved energy exists: 40 MW short
    # every hour, only 5 MW buildable, so ~420 MWh cannot be avoided at any
    # price. The loose end must clear that floor (else the test proves only
    # that everything fails); the tight end must not.
    n = _network()
    n.generators.loc["peak", "p_nom_max"] = 5.0
    res = _run(n, [5000.0, 0.0001])
    statuses = {p["target_permyriad"]: p["status"] for p in res["points"]}
    assert statuses[0.0001] != "ok", statuses
    assert res["points"][-1]["point"] is None
    # the reachable end still produced a usable point
    assert any(p["status"] == "ok" for p in res["points"])


def test_the_study_leaves_the_foreground_on_the_users_own_config():
    """
    The closing re-solve runs the ORIGINAL config, not whichever target was
    swept last — otherwise the study silently rewrites the user's results.
    """
    n = _network()
    sink: dict = {}
    PyPSAService.set_network(n)
    cfg = SolverConfig(solver_name="highs", voll=VOLL, ens_cap_permyriad=None)
    run_frontier_sweep(n, PyPSAService.get_lock(), cfg, [200.0, 50.0],
                       log_queue=queue.SimpleQueue(),
                       final_state_update=lambda **kw: sink.update(kw))
    # cfg carried no target, so the restored foreground must carry no report
    assert sink.get("adequacy_report") is None


def test_targets_are_validated_rather_than_coerced():
    n = _network()
    with pytest.raises(FrontierConfigError):
        _run(n, [])
    for bad in ([-1.0], [0.0], [float("nan")], ["x"]):
        with pytest.raises(FrontierConfigError):
            _run(n, bad)
    with pytest.raises(FrontierBudgetError):
        _run(n, [float(i + 1) for i in range(50)])


def test_a_frontier_without_voll_is_refused():
    """Without slack there is nothing for the cap to constrain; every point
    would collapse onto the same unconstrained plan."""
    with pytest.raises(FrontierConfigError):
        _run(_network(), [50.0], voll=0.0)


def test_duplicate_targets_collapse_and_order_is_loosest_first():
    res = _run(_network(), [50.0, 200.0, 50.0])
    swept = [p["target_permyriad"] for p in res["points"]]
    assert swept == [200.0, 50.0], swept


def test_non_convexity_is_warned_not_hidden():
    """
    Unit commitment makes C*(Ē) non-convex. The points stay valid, but a knee
    read off the curve may be a MIP-gap artefact — the spec requires that be
    surfaced.
    """
    plain = non_convexity_warning(_network(committable=False),
                                  SolverConfig(solver_name="highs", voll=VOLL))
    assert plain is None

    uc = non_convexity_warning(_network(committable=True),
                               SolverConfig(solver_name="highs", voll=VOLL))
    assert uc and "committable" in uc

    gap = non_convexity_warning(
        _network(committable=False),
        SolverConfig(solver_name="highs", voll=VOLL,
                     solver_options={"mip_rel_gap": 0.01}))
    assert gap and "MIP gap" in gap


def test_knee_is_the_crossing_and_absent_when_there_is_none():
    # cost per avoided MWh: 1 -> 2 costs 100/10 = 10; 2 -> 3 costs 900/10 = 90
    pts = [
        {"status": "ok", "point": {"total_system_cost_eur": 1000.0, "achieved_ens_mwh": 30.0}},
        {"status": "ok", "point": {"total_system_cost_eur": 1100.0, "achieved_ens_mwh": 20.0}},
        {"status": "ok", "point": {"total_system_cost_eur": 2000.0, "achieved_ens_mwh": 10.0}},
    ]
    # VoLL 50 €/MWh: the second step (90 €/MWh) is where cost overtakes value
    assert knee_index(pts, 50.0) == 1
    # VoLL 1000 €/MWh: every step is still worth buying, so there is no knee
    # inside the swept range — and inventing one at an endpoint would be worse
    assert knee_index(pts, 1000.0) is None
    assert knee_index(pts[:1], 50.0) is None
    assert knee_index(pts, 0.0) is None


def test_default_targets_are_ordered_and_within_the_budget():
    assert len(DEFAULT_TARGETS_PERMYRIAD) <= 12
    assert list(DEFAULT_TARGETS_PERMYRIAD) == sorted(DEFAULT_TARGETS_PERMYRIAD,
                                                     reverse=True)
    assert all(t > 0 for t in DEFAULT_TARGETS_PERMYRIAD)
