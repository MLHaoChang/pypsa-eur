"""
AS-06 (`.scratch/asset-sizing-loop/issues/06-sizing-loop-runner.md`): the sizing loop's controller. It solves,
runs the forward checks on the fixed capacities, adds cuts and stops by the revised rule of the prototype
(`.scratch/datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md`, section 5). The controller is pure
control flow, so every test here hands it a fake solve and a fake forward pass.

The numbers are illustrative, marked "assumed" (SAGC-1).
"""
from __future__ import annotations

import threading

import pytest

from services.adequacy import sizing_loop as sl
from services.adequacy.sizing_loop import merge_cuts, run_sizing_loop


def _ok(cost, **sizes):
    return {"status": "ok", "condition": "optimal", "cost_eur": float(cost), "sizes": dict(sizes)}


def _check(name, ok, *, lever="cut", cut=None, **kw):
    return {"name": name, "ok": ok, "lever": lever, "cut": cut or {}, **kw}


class Script:
    """A fake solve and forward pass: round k reads `solves[k-1]` and `checks[k-1]`, and records the state."""

    def __init__(self, solves, checks):
        self.solves, self.checks, self.states = solves, checks, []

    def solve(self, state):
        self.states.append({"round": state["round"], "cuts": dict(state["cuts"]), "frozen": dict(state["frozen"])})
        return self.solves[min(state["round"], len(self.solves)) - 1]

    def forward(self, result, state):
        return self.checks[min(state["round"], len(self.checks)) - 1]


# ── cuts: monotone, never removed ───────────────────────────────────────────


def test_a_cut_only_tightens_in_its_own_direction():
    cuts = merge_cuts({}, {"import_cap_mw": 120.0, "pst_rhs_mva": 5.0, "p_floor_mw": 40.0, "e_floor_mwh": 2.0,
                           "gfm_mva_min": 30.0, "pq_margin_mvar": 1.0})[0]
    looser = {"import_cap_mw": 125.0, "pst_rhs_mva": 6.0, "p_floor_mw": 30.0, "e_floor_mwh": 1.0,
              "gfm_mva_min": 20.0, "pq_margin_mvar": 0.5}
    same, changed = merge_cuts(cuts, looser)
    assert same == cuts and changed == set()
    tighter = {"import_cap_mw": 110.0, "pst_rhs_mva": 4.0, "p_floor_mw": 45.0, "e_floor_mwh": 3.0,
               "gfm_mva_min": 35.0, "pq_margin_mvar": 2.0}
    merged, changed = merge_cuts(cuts, tighter)
    assert merged == tighter and changed == set(tighter)


def test_a_cut_is_never_removed():
    merged, _ = merge_cuts({"pst_rhs_mva": 5.0, "gfm_mva_min": 30.0}, {"p_floor_mw": 40.0})
    assert merged == {"pst_rhs_mva": 5.0, "gfm_mva_min": 30.0, "p_floor_mw": 40.0}


@pytest.mark.parametrize("bad", [{"unknown_cut": 1.0}, {"pst_rhs_mva": float("nan")},
                                 {"p_floor_mw": float("inf")}])
def test_an_unknown_or_non_finite_cut_is_refused(bad):
    with pytest.raises(ValueError):
        merge_cuts({}, bad)


def test_cost_is_non_decreasing_and_cuts_accumulate():
    # Each round fails one more check and adds its cut; the fake LP's cost rises with every cut it holds.
    def solve(state):
        return _ok(100.0 + 0.5 * len(state["cuts"]), p=40.0 + len(state["cuts"]))

    rounds = [[_check("ramp", False, cut={"p_floor_mw": 40.0}), _check("pst", True)],
              [_check("ramp", True), _check("pst", False, cut={"pst_rhs_mva": 5.0})],
              [_check("ramp", True), _check("pst", True)]]
    out = run_sizing_loop(solve, lambda r, s: rounds[s["round"] - 1])
    assert out["status"] == "converged"
    costs = [r["total_cost_eur"] for r in out["rounds"]]
    assert costs == sorted(costs) and len(costs) == 3
    held = [set(r["cuts"]) for r in out["rounds"]]
    assert held[0] <= held[1] <= held[2] and held[2] == {"p_floor_mw", "pst_rhs_mva"}
    assert out["warnings"] == []


def test_a_falling_cost_is_flagged_since_cuts_only_shrink_the_feasible_set():
    script = Script([_ok(100.0), _ok(99.0)],
                    [[_check("pst", False, cut={"pst_rhs_mva": 5.0})], [_check("pst", True)]])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "converged"
    assert any("fell" in w for w in out["warnings"])


# ── the revised stopping rule ───────────────────────────────────────────────


def test_all_checks_passing_converges_in_one_round():
    script = Script([_ok(100.0)], [[_check("pst", True), _check("escr", True)]])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "converged" and out["verdict"] == "converged" and len(out["rounds"]) == 1


def test_a_tolerance_stop_with_pst_6x_over_is_not_converged():
    # The prototype's creep: cost moves 0.02 % a round, capacities barely move, Pst stays 6x the limit; the
    # LP keeps getting a slightly tighter Pst cut. A cost-only rule would call this converged.
    script = Script([_ok(70.46e6 * (1 + 2e-4) ** k, s_x=20.0, q=4.0 + 0.01 * k) for k in range(5)],
                    [[_check("pst", False, value=2.03, limit=0.35, cut={"pst_rhs_mva": 5.0 - 0.1 * k})]
                     for k in range(5)])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "not_converged"
    assert out["failing"] == ["pst"]
    assert "tolerance" in out["verdict"] and "pst" in out["verdict"]
    assert len(out["rounds"]) == 2


def test_capacities_still_moving_run_to_the_five_round_cap_and_are_not_converged():
    script = Script([_ok(70.46e6 * (1 + 2e-4) ** k, s_x=24.0 - 4 * k, q=4.0 * k) for k in range(5)],
                    [[_check("pst", False, value=2.03, limit=0.35, cut={"pst_rhs_mva": 5.0 - 0.1 * k})]
                     for k in range(5)])
    out = run_sizing_loop(script.solve, script.forward, max_rounds=10)
    assert out["status"] == "not_converged" and len(out["rounds"]) == sl.MAX_ROUNDS == 5
    assert "5 rounds" in out["verdict"] and "pst" in out["verdict"]


def test_a_failing_check_that_yields_no_tighter_cut_is_not_converged():
    # The second round proposes the same Pst cut: the LP would return the same plan, so stop and say so.
    script = Script([_ok(100.0), _ok(101.0)], [[_check("pst", False, cut={"pst_rhs_mva": 5.0})]] * 2)
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "not_converged" and len(out["rounds"]) == 2
    assert "no tighter cut" in out["verdict"] and "pst" in out["verdict"]
    assert out["failing"] == ["pst"]


def test_a_check_with_no_lever_is_unresolved():
    # SCR set by the grid alone: no GFM technology is offered, so no cut can lift it.
    script = Script([_ok(100.0)], [[_check("escr", False, lever="none", value=2.1, limit=3.0),
                                    _check("pst", False, cut={"pst_rhs_mva": 5.0})]])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "unresolved" and len(out["rounds"]) == 1
    assert out["verdict"] == "unresolved, needs grid reinforcement or EMT study: escr"
    assert out["unresolved"] == ["escr"]


def test_harmonics_report_a_filter_cost_not_a_cut():
    script = Script([_ok(100.0)], [[_check("harmonics", False, lever="filter_cost", filter_cost_eur=12.0,
                                           cut={"pst_rhs_mva": 1.0}),
                                    _check("pst", True)]])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "converged"
    assert out["cuts"] == {}
    row = out["rounds"][0]
    assert row["filter_cost_eur"] == 12.0 and row["total_cost_eur"] == 112.0
    assert out["final"]["total_cost_eur"] == 112.0


def test_the_tolerance_stop_compares_the_cost_including_the_filter_cost():
    # LP cost flat; the filter cost doubles, so the total moves far more than 0.5 % and the loop goes on.
    script = Script([_ok(100.0, p=1.0)] * 3,
                    [[_check("harmonics", False, lever="filter_cost", filter_cost_eur=10.0 * k),
                      _check("pst", False, cut={"pst_rhs_mva": 5.0 - k})] for k in (1, 2, 3)])
    out = run_sizing_loop(script.solve, script.forward, max_rounds=3)
    assert out["status"] == "not_converged" and len(out["rounds"]) == 3
    assert "tolerance" not in out["verdict"]


def test_skipped_campus_checks_qualify_a_converged_verdict():
    script = Script([_ok(100.0)], [[_check("pst", True), _check("campus_flow", True, skipped=True),
                                    _check("gfm_tuning", True, skipped=True)]])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "converged"
    assert out["verdict"] == "converged (campus checks not run: campus_flow, gfm_tuning)"
    assert out["not_run"] == ["campus_flow", "gfm_tuning"]


# ── library assets: round up, freeze after round 2 ──────────────────────────


def test_the_transformer_rounds_up_and_freezes_after_round_two():
    lib = [80.0, 100.0, 80.0, 63.0]
    checks = [[_check("transformer", True, library={"trafo": s}),
               _check("pst", k == 3, cut={"pst_rhs_mva": 5.0 - k})] for k, s in enumerate(lib)]
    script = Script([_ok(100.0 + k, trafo=s) for k, s in enumerate(lib)], checks)
    out = run_sizing_loop(script.solve, script.forward)
    assert [s["frozen"] for s in script.states] == [{}, {}, {"trafo": 100.0}, {"trafo": 100.0}]
    assert out["frozen"] == {"trafo": 100.0}
    assert out["status"] == "converged" and len(out["rounds"]) == 4


def test_a_frozen_size_cannot_flip_even_when_a_later_round_asks_for_more():
    checks = [[_check("transformer", True, library={"trafo": s}), _check("pst", False, cut={"pst_rhs_mva": 9 - k})]
              for k, s in enumerate([63.0, 80.0, 125.0, 125.0, 125.0])]
    script = Script([_ok(100.0 + k, s_x=10.0 * k) for k in range(5)], checks)
    out = run_sizing_loop(script.solve, script.forward)
    assert out["frozen"] == {"trafo": 80.0}
    assert {s["frozen"].get("trafo") for s in script.states[2:]} == {80.0}


def test_freeze_after_one_round():
    script = Script([_ok(100.0), _ok(101.0)], [[_check("transformer", True, library={"trafo": 80.0}),
                                               _check("pst", False, cut={"pst_rhs_mva": 5.0})],
                                              [_check("transformer", True, library={"trafo": 100.0}),
                                               _check("pst", True)]])
    run_sizing_loop(script.solve, script.forward, freeze_after=1)
    assert script.states[1]["frozen"] == {"trafo": 80.0}


# ── the full-year final solve ───────────────────────────────────────────────


def test_a_final_full_year_solve_confirms_the_reduced_year_verdict():
    script = Script([_ok(100.0)], [[_check("pst", True)]])
    seen = []

    def final_solve(state):
        seen.append(dict(state["cuts"]))
        return _ok(103.0)

    out = run_sizing_loop(script.solve, script.forward, final_solve=final_solve)
    assert out["status"] == "converged" and out["final"]["total_cost_eur"] == 103.0
    assert out["final"]["basis"] == "full_year" and len(seen) == 1


def test_reduced_year_rounds_passing_and_a_failing_full_year_is_not_converged():
    calls = []

    def forward(result, state):
        calls.append(state.get("final", False))
        return [_check("pst", not state.get("final", False), cut={"pst_rhs_mva": 4.0})]

    out = run_sizing_loop(lambda s: _ok(100.0), forward, final_solve=lambda s: _ok(104.0))
    assert calls == [False, True]
    assert out["status"] == "not_converged"
    assert out["verdict"] == "not converged: reduced-year rounds passed, full year failed: pst"
    assert out["failing"] == ["pst"]


def test_no_final_solve_after_a_verdict_other_than_converged():
    called = []
    script = Script([_ok(100.0)], [[_check("escr", False, lever="none")]])
    out = run_sizing_loop(script.solve, script.forward, final_solve=lambda s: called.append(1))
    assert out["status"] == "unresolved" and called == []


# ── failure, abort, budget, run time ────────────────────────────────────────


def test_a_failed_solve_stops_with_failed():
    script = Script([_ok(100.0), {"status": "warning", "condition": "infeasible", "cost_eur": None}],
                    [[_check("pst", False, cut={"pst_rhs_mva": 5.0})]] * 2)
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "failed" and "infeasible" in out["verdict"] and len(out["rounds"]) == 2


def test_abort_after_round_one():
    stop = threading.Event()
    script = Script([_ok(100.0)] * 5, [[_check("pst", False, cut={"pst_rhs_mva": 5.0 - k})] for k in range(5)])
    out = run_sizing_loop(script.solve, script.forward, stop_event=stop, on_iteration=lambda row: stop.set())
    assert out["status"] == "aborted" and len(out["rounds"]) == 1 and len(script.states) == 1


def test_the_round_budget_is_capped_at_five():
    assert sl._budget(10) == 5 and sl._budget(0) == 1 and sl._budget(3) == 3


def test_each_round_records_its_run_time():
    script = Script([_ok(100.0)], [[_check("pst", True)]])
    out = run_sizing_loop(script.solve, script.forward, final_solve=lambda s: _ok(101.0))
    row = out["rounds"][0]
    assert row["solve_s"] >= 0 and row["forward_s"] >= 0
    assert out["final"]["solve_s"] >= 0 and out["seconds"] >= row["solve_s"]


def test_on_iteration_sees_every_row():
    seen = []
    script = Script([_ok(100.0), _ok(101.0)], [[_check("pst", False, cut={"pst_rhs_mva": 5.0})],
                                              [_check("pst", True)]])
    run_sizing_loop(script.solve, script.forward, on_iteration=seen.append)
    assert [r["round"] for r in seen] == [1, 2]


# ── review fixes ────────────────────────────────────────────────────────────


def test_a_check_whose_key_another_check_tightened_further_is_not_stale():
    script = Script([_ok(100.0), _ok(102.0)],
                    [[_check("transformer", False, cut={"import_cap_mw": 50.0}),
                      _check("site_peak", False, cut={"import_cap_mw": 60.0})],
                     [_check("transformer", True), _check("site_peak", True)]])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["status"] == "converged" and script.states[1]["cuts"] == {"import_cap_mw": 50.0}


def test_two_checks_naming_one_asset_round_up_to_the_larger():
    checks = [[_check("transformer", True, library={"trafo": 80.0}), _check("site_peak", True, library={"trafo": 100.0}),
               _check("pst", False, cut={"pst_rhs_mva": 5.0 - k})] for k in range(3)]
    script = Script([_ok(100.0 + k, s=10.0 * k) for k in range(3)], checks)
    out = run_sizing_loop(script.solve, script.forward, max_rounds=3)
    assert out["frozen"] == {"trafo": 100.0}


def test_a_freeze_round_naming_no_library_size_freezes_the_last_named():
    checks = [[_check("transformer", True, library={"trafo": 80.0}), _check("pst", False, cut={"pst_rhs_mva": 5.0})],
              [_check("pst", False, cut={"pst_rhs_mva": 4.0})],
              [_check("pst", True)]]
    script = Script([_ok(100.0, s=1.0), _ok(102.0, s=2.0), _ok(104.0, s=3.0)], checks)
    out = run_sizing_loop(script.solve, script.forward)
    assert script.states[2]["frozen"] == {"trafo": 80.0} and out["frozen"] == {"trafo": 80.0}


def test_a_run_converged_before_the_freeze_still_reports_the_library_size():
    script = Script([_ok(100.0)], [[_check("transformer", True, library={"trafo": 80.0}), _check("pst", True)]])
    out = run_sizing_loop(script.solve, script.forward)
    assert out["frozen"] == {} and out["library"] == {"trafo": 80.0}


def test_a_falling_filter_cost_is_not_flagged():
    script = Script([_ok(100.0), _ok(100.0)],
                    [[_check("harmonics", False, lever="filter_cost", filter_cost_eur=20.0),
                      _check("pst", False, cut={"pst_rhs_mva": 5.0})],
                     [_check("harmonics", False, lever="filter_cost", filter_cost_eur=10.0), _check("pst", True)]])
    assert run_sizing_loop(script.solve, script.forward)["warnings"] == []
