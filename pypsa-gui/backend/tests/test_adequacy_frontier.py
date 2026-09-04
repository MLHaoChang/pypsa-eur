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


# ── restore exception-safety (coupling-loop spec §1.3, plan [S8-b]) ───────

def _fake_report() -> dict:
    """The shape ``run_frontier_sweep`` reads out of a solved sink — enough of
    it to build a point, no HiGHS involved. These tests are about the CONTROL
    FLOW around the sweep, so a live solve would only make them slow and
    make the injected failure harder to place exactly on point 2."""
    return {
        "engine": "lp_proxy",
        "fidelity": "deterministic_scenario",
        "target": {"binding": "system_cap",
                   "system": {"cap_mwh": 10.0, "achieved_ens_mwh": 1.0,
                              "achieved_shed_hours": 1.0}},
        "cost": {"total_system_cost_eur": 42.0, "period_basis": "horizon"},
    }


def _patched_sweep(monkeypatch, *, fail_on=None, restore_raises=False):
    """Instrument both halves of the sweep: the per-point solve and the
    closing base re-solve. Returns the call-record dict."""
    from services.adequacy import sweep as _sweep
    from services import solver_service as _solver

    calls = {"solves": 0, "restores": 0}

    def fake_solve_once(cfg, network, lock, log_queue, sink):
        calls["solves"] += 1
        if fail_on is not None and calls["solves"] == fail_on:
            raise RuntimeError("solver process died mid-sweep")
        sink["_status"] = "ok"
        sink["adequacy_report"] = _fake_report()

    def fake_run_simulation(*args, **kwargs):
        calls["restores"] += 1
        if restore_raises:
            raise RuntimeError("the closing base re-solve failed too")
        # The real `run_simulation` returns `(status, condition)`, and since
        # the shipped-code review's finding 13 `_restore_base` reads the
        # status rather than throwing it away — so the fake has to return one.
        return "ok", None

    monkeypatch.setattr(_sweep, "_solve_once", fake_solve_once)
    monkeypatch.setattr(_solver, "run_simulation", fake_run_simulation)
    return calls


def test_an_exception_mid_sweep_still_attempts_the_base_restore(monkeypatch):
    """★ §1.3 (plan [S8-b]). Without a try/finally, an exception on point 2
    leaves the NETWORK on whichever ε was swept last while the foreground
    results still describe the pre-study solve — the study silently rewrites
    the user's plan and says nothing. The restore must be attempted on every
    path, and the record that travels with the failure must state truthfully
    whether it worked.

    BROKEN VARIANT (bite): drop the ``try``/``finally`` (put the closing
    re-solve back on the straight-line path after the loop) — ``restores``
    stays 0 and no record reaches the caller at all.
    """
    calls = _patched_sweep(monkeypatch, fail_on=2)
    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig(solver_name="highs", voll=VOLL)
    with pytest.raises(RuntimeError) as exc:
        run_frontier_sweep(n, PyPSAService.get_lock(), cfg, [200.0, 50.0, 20.0],
                           log_queue=queue.SimpleQueue())
    assert calls["solves"] == 2, calls          # stopped where it blew up
    assert calls["restores"] == 1, calls        # …and the restore still ran
    rec = getattr(exc.value, "frontier_result", None)
    assert rec is not None, "the failed record never reached the caller"
    assert rec["base_restored"] is True         # the restore itself succeeded
    assert len(rec["points"]) == 1              # point 1 survived the failure


def test_a_failed_restore_is_reported_rather_than_claimed(monkeypatch):
    """★ §1.3, second clause: ``base_restored`` is FALSE when the closing
    re-solve itself failed. Hardcoding ``True`` asserts the foreground is the
    user's own solve when it demonstrably is not.

    BROKEN VARIANT (bite): return the literal ``"base_restored": True`` —
    the assertion below reads True on a run whose restore raised.
    """
    calls = _patched_sweep(monkeypatch, restore_raises=True)
    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig(solver_name="highs", voll=VOLL)
    res = run_frontier_sweep(n, PyPSAService.get_lock(), cfg, [200.0, 50.0],
                             log_queue=queue.SimpleQueue())
    assert calls["restores"] == 1
    assert res["base_restored"] is False
    # The sweep's own answer is not thrown away because the restore failed.
    assert [p["status"] for p in res["points"]] == ["ok", "ok"]


def test_a_clean_sweep_still_reports_a_successful_restore(monkeypatch):
    """The truthful-on-all-paths clause in the ordinary direction."""
    calls = _patched_sweep(monkeypatch)
    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig(solver_name="highs", voll=VOLL)
    res = run_frontier_sweep(n, PyPSAService.get_lock(), cfg, [200.0, 50.0],
                             log_queue=queue.SimpleQueue())
    assert calls == {"solves": 2, "restores": 1}
    assert res["base_restored"] is True
    assert res["base_restore_status"] == "ok"


def test_F1i_a_frontier_restore_that_comes_back_infeasible_says_so(monkeypatch):
    """★ F1i (shipped-code review, finding 13). `_restore_base` called
    `run_simulation` and discarded its return value, reporting a bare `True`
    for "it did not raise". A closing re-solve that comes back `infeasible`
    does not raise and does not restore anything either: the network is left
    on the last swept target while the record says the base is back — the one
    reading the caller must never be given.

    The contingency sweep's `_restore_base_guarded` has carried the solver's
    word since Phase 12e; this brings the frontier to the same shape. Bite
    (verified): drop `base_restore_status` and return a bare bool again.
    """
    from services import solver_service as _solver
    from services.adequacy import sweep as _sweep

    calls = {"solves": 0, "restores": 0}

    def fake_solve_once(cfg, network, lock, log_queue, sink):
        calls["solves"] += 1
        sink["_status"] = "ok"
        sink["adequacy_report"] = _fake_report()

    def fake_run_simulation(*args, **kwargs):
        calls["restores"] += 1
        return "infeasible", None            # did not raise; did not restore

    monkeypatch.setattr(_sweep, "_solve_once", fake_solve_once)
    monkeypatch.setattr(_solver, "run_simulation", fake_run_simulation)

    n = _network()
    PyPSAService.set_network(n)
    cfg = SolverConfig(solver_name="highs", voll=VOLL)
    res = run_frontier_sweep(n, PyPSAService.get_lock(), cfg, [200.0, 50.0],
                             log_queue=queue.SimpleQueue())

    assert calls["restores"] == 1
    assert res["base_restore_status"] == "infeasible", res
    # The flag still says the re-solve RAN — that is what it means — so the
    # status is the only thing that can carry the bad news, and it does.
    assert res["base_restored"] is True


def test_a_refused_config_never_touches_the_network(monkeypatch):
    """Validation failures happen BEFORE any solve, so there is nothing to
    restore — the try/finally must not fire a re-solve the user did not ask
    for on a request that was rejected out of hand."""
    calls = _patched_sweep(monkeypatch)
    n = _network()
    PyPSAService.set_network(n)
    with pytest.raises(FrontierConfigError):
        run_frontier_sweep(n, PyPSAService.get_lock(),
                           SolverConfig(solver_name="highs", voll=VOLL), [],
                           log_queue=queue.SimpleQueue())
    with pytest.raises(FrontierConfigError):
        run_frontier_sweep(n, PyPSAService.get_lock(),
                           SolverConfig(solver_name="highs", voll=0.0), [50.0],
                           log_queue=queue.SimpleQueue())
    assert calls == {"solves": 0, "restores": 0}


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
