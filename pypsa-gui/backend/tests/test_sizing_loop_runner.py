"""
AS-06 (`.scratch/asset-sizing-loop/issues/06-sizing-loop-runner.md`): the sizing loop runner. It solves on a copy
through the AS LP chain, runs the forward checks on the fixed capacities and drives the controller.

The campus is the AS prototype's (`.scratch/datacenter-grid-readiness/research/prototype-as-loop/`): a generic
100 MW campus, round illustrative numbers, every limit "assumed" (SAGC-1).
"""
from __future__ import annotations

import dataclasses
import math
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import sizing_loop_runner as slr
from services.adequacy.sizing_loop_runner import SizingInputs, SizingLoopRefusal, run_sizing
from services.solver.envelope_floors import set_run_hours_cap, tag_store
from services.solver.pcs_statcom import tag_pcs, tag_pq_link
from services.solver.sizing_cuts import tag_gfm
from services.solver.split_storage import add_split_storage
from services.solver_service import SolverConfig

H = 168
P_CRIT, BRIDGE_H = 80.0, 0.25
TAN_LOAD = math.tan(math.acos(0.95))
ENVELOPE = {"phases": [{"swing_mw": 30.0, "oscillation": {"amplitude_mw": 10.0, "period_s": 2.0},
                        "checkpoint": {"depth_mw": 20.0, "duration_s": 30.0}}],
            "max": {"swing_mw": 30.0, "oscillation_amplitude_mw": 10.0, "oscillation_period_s": 2.0,
                    "tan_phi_swing": 0.1}}


def _load(weeks=1):
    rng = np.random.default_rng(7)
    hours = np.arange(H)
    train = (rng.random(H) > 0.2).astype(float)
    week = 70 + 30.0 * train + 5 * np.sin(2 * np.pi * (hours - 14) / 24)
    return np.concatenate([week * (1 + 0.02 * k) for k in range(weeks)])


def _weak_flat_d(weeks=1, *, storage=True, gfm=True):
    """Intent D on the weak grid with a flat tariff: energy and high-C BESS (GFM), STATCOM, a genset with a
    100 h/yr run-hour cap; the bridge reserve P_crit x 0.25 h sits on the energy BESS."""
    load = _load(weeks)
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(len(load), name="snapshot"))
    n.snapshot_weightings.loc[:, "objective"] = 8760 / len(load)
    n.add("Bus", "poc")
    n.add("Bus", "mv")
    n.add("Generator", "import", bus="poc", p_nom=1e4, marginal_cost=75.0)
    n.add("Generator", "export", bus="poc", p_nom=1e4, p_min_pu=-1, p_max_pu=0, marginal_cost=54.0)
    n.add("Link", "trafo", bus0="poc", bus1="mv", p_nom_extendable=True, p_nom_max=125.0, p_max_pu=0.98,
          p_min_pu=-0.98, capital_cost=10_000.0)
    tag_pq_link(n, "trafo", x_pu=0.12, pf=0.98, s_n_mva=125.0, p_cap_mw=122.5, pf_req=0.98,
                statcom_capital_cost=8_000.0)
    n.add("Load", "gpu", bus="mv", p_set=pd.Series(load, n.snapshots), q_set=pd.Series(load * TAN_LOAD, n.snapshots))
    if storage:
        for name, tech, c_p, c_e in (("energy", "energy_bess", 30_000.0, 15_000.0),
                                     ("highC", "high_c_bess", 30_000.0, 35_000.0)):
            add_split_storage(n, name, "mv", tech, power_capital_cost=c_p, energy_capital_cost=c_e,
                              efficiency_charge=0.95, efficiency_discharge=0.95)
            tag_pcs(n, name, r=0.2, capital_cost=6_000.0)
            tag_store(n, name, smoothing_tier=True, backup_reserve=name == "energy")
            if gfm:
                tag_gfm(n, name)
    n.add("Generator", "genset", bus="mv", p_nom_extendable=True, p_nom_min=P_CRIT, capital_cost=35_000.0,
          marginal_cost=250.0)
    set_run_hours_cap(n, "genset", 100.0)
    return n


def _config(**kw):
    floors = {"backup_mwh": P_CRIT * BRIDGE_H}
    return SolverConfig(envelope_floors=floors, **kw)


def _inputs(**kw):
    base = dict(link="trafo", envelope=ENVELOPE, ramp_mw_per_min=10.0, sk_mva=250.0, pst_limit=0.35,
                transformer_library_mva=(63.0, 80.0, 100.0, 125.0), xr=4.0, reduce=False)
    return SizingInputs(**{**base, **kw})


def _fingerprints(n, cfg):
    from types import SimpleNamespace

    from services import workflow_tools as wt

    ctx = SimpleNamespace(network=n, solver_state={"solver_config": cfg}, user_ts={})
    return wt._network_input_fingerprint(ctx), wt._network_input_fingerprint(ctx, exact=True)


# ── acceptance: the prototype's failing case passes in 2 rounds (note cuts needed 9) ──


@pytest.mark.slow
def test_weak_flat_intent_d_passes_in_two_rounds():
    out = run_sizing(_weak_flat_d(), _config(), _inputs())
    assert out["status"] == "converged", (out["verdict"], [r["failing"] for r in out["rounds"]])
    assert out["verdict"] == "converged (campus checks not run: campus_flow, campus_reactive, grid_readiness, " \
                             "gfm_tuning)"
    assert len(out["rounds"]) == 2
    assert "pst" in out["rounds"][0]["failing"]
    costs = [r["cost_eur"] for r in out["rounds"]]
    assert costs == sorted(costs)
    # The dynamic STATCOM slice the Pst cut buys: the prototype's revised run ends at 3.4 Mvar.
    assert out["final"]["sizes"]["q_dyn"] == pytest.approx(3.4, abs=0.2)
    assert out["sheet"] is not None


# ── wiring, fast ────────────────────────────────────────────────────────────


def _tiny(**kw):
    """Two hours of the weak-flat campus: the same rows, a sub-second LP."""
    n = _weak_flat_d(**kw)
    n.set_snapshots(n.snapshots[:2])
    n.snapshot_weightings.loc[:, "objective"] = 4380.0
    return n


def test_the_runner_wires_the_checks_and_reports_each_round():
    out = run_sizing(_tiny(), _config(), _inputs())
    assert out["status"] in ("converged", "not_converged"), out["verdict"]
    names = [c["name"] for c in out["rounds"][0]["checks"]]
    assert names == ["transformer", "pq", "escr", "smoothing", "pst", *slr.CAMPUS_CHECKS]
    assert all(r["info"]["basis"] == "full_year" for r in out["rounds"])
    assert out["library"]["trafo"] in (63.0, 80.0, 100.0, 125.0)


def test_the_runner_leaves_the_network_and_config_untouched():
    n, cfg = _tiny(), _config()
    before = _fingerprints(n, cfg)
    links, stores, cfg_before = n.links.copy(), n.stores.copy(), dataclasses.asdict(cfg)
    run_sizing(n, cfg, _inputs())
    assert _fingerprints(n, cfg) == before
    pd.testing.assert_frame_equal(n.links, links)
    pd.testing.assert_frame_equal(n.stores, stores)
    assert dataclasses.asdict(cfg) == cfg_before
    assert not hasattr(n, "model") or n.model is None or "Statcom-q_ss" not in n.model.variables


def test_loop_settings_never_enter_the_fingerprints():
    n, cfg = _tiny(), _config()
    before = _fingerprints(n, cfg)
    run_sizing(n, cfg, _inputs(), max_rounds=2, freeze_after=1)
    run_sizing(n, cfg, _inputs(), max_rounds=5, freeze_after=2)
    assert _fingerprints(n, cfg) == before


def test_a_check_with_no_lever_is_unresolved():
    # No GFM store and a weak grid: the ESCR is set by the grid alone.
    out = run_sizing(_tiny(gfm=False), _config(), _inputs(sk_mva=150.0))
    assert out["status"] == "unresolved"
    assert out["verdict"].startswith("unresolved, needs grid reinforcement or EMT study: ")
    assert "escr" in out["unresolved"]


def test_harmonics_report_a_filter_cost():
    out = run_sizing(_tiny(), _config(), _inputs(harmonics=lambda n, sizes: 25_000.0))
    row = out["rounds"][0]
    assert row["filter_cost_eur"] == 25_000.0 and row["total_cost_eur"] == row["cost_eur"] + 25_000.0
    assert "harmonics" not in row["failing"]


def test_a_campus_adapter_runs_and_drops_from_the_not_run_list():
    out = run_sizing(_tiny(), _config(), _inputs(campus={"gfm_tuning": lambda n, r: {"ok": True, "lever": "cut",
                                                                                      "cut": {}}}))
    assert "gfm_tuning" not in out["rounds"][0]["not_run"]
    assert "campus_flow" in out["rounds"][0]["not_run"]


def _always_failing_pst(result, state, inp):
    """The real forward pass with a Pst check that fails every round and tightens its cut."""
    checks = [c for c in _real_forward(result, state, inp) if c["name"] != "pst"]
    return checks + [slr._check("pst", False, cut={"pst_rhs_mva": 10.0 - state["round"]})]


_real_forward = slr.forward_checks


def test_abort_after_round_one(monkeypatch):
    monkeypatch.setattr(slr, "forward_checks", _always_failing_pst)
    stop, solves = threading.Event(), []

    def solve(*a, **kw):
        solves.append(1)
        return slr.solve_once(*a, **kw)

    out = run_sizing(_tiny(), _config(), _inputs(), stop_event=stop, on_iteration=lambda row: stop.set(),
                     solve=solve)
    assert out["status"] == "aborted" and len(out["rounds"]) == 1 and len(solves) == 1


def test_a_raising_solve_is_a_failed_status_not_an_exception(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("solver crashed")

    monkeypatch.setattr(slr, "_prepare", boom)
    out = run_sizing(_tiny(), _config(), _inputs())
    assert out["status"] == "failed" and "RuntimeError: solver crashed" in out["verdict"]


def test_every_result_names_its_basis():
    out = run_sizing(_tiny(), _config(), _inputs())
    assert out["basis_note"] == slr.BASIS_NOTE


def _budget_activity(budget):
    """The loop's result and the capex-budget row's activity in each solved round."""
    seen = []

    def spy(*a, **kw):
        res = slr.solve_once(*a, **kw)
        if res.get("network") is not None:
            seen.append(float(res["network"].model.constraints["capex_budget_0"].lhs.solution.sum()))
        return res

    return run_sizing(_tiny(), _config(capex_budget_per_period={"0": budget}), _inputs(), solve=spy), seen


def test_the_project_capex_budget_binds_inside_the_loop():
    # A slack budget: the row is in every round's LP and the loop converges.
    out, slack = _budget_activity(1e12)
    assert out["status"] == "converged" and slack
    # Below what the converged sizes cost: the first round fits, a later one cannot, so the loop fails rather
    # than reporting sizes that break the project's budget.
    out, tight = _budget_activity(0.9 * slack[-1])
    assert tight and tight[0] <= 0.9 * slack[-1] + 1e-3
    assert out["status"] == "failed", out["verdict"]


@pytest.mark.parametrize("setting, value, named", [
    ("island_constraints", True, "island constraints are on"),
    ("commercial", {"tariff": {}}, "commercial bindings are set on this project and are not applied to the loop"),
    ("fixed_sizes", "nameplate", "Capacity > sizing is off"),
])
def test_a_pre_solve_setting_the_loop_cannot_reproduce_is_refused_by_name(setting, value, named):
    with pytest.raises(SizingLoopRefusal, match=named):
        run_sizing(_tiny(), _config(**{setting: value}), _inputs())


@pytest.mark.parametrize("mode", ["rolling", "myopic"])
def test_a_rolling_or_myopic_project_is_named_in_the_basis(mode):
    out = run_sizing(_tiny(), _config(solve_strategy=mode), _inputs())
    assert f"the project solves {mode}; the loop sizes one full-horizon LP" in out["basis_note"]


def test_a_network_without_pq_tags_is_refused():
    n = _tiny()
    for col in ("pq_x_pu", "pq_pf", "pq_s_n_mva", "pq_p_cap_mw", "pq_pf_req", "statcom_capital_cost"):
        n.links[col] = np.nan
    with pytest.raises(SizingLoopRefusal, match="PQ tags"):
        run_sizing(n, _config(), _inputs())


# ── the reduced year and the full-year final solve ──────────────────────────


def test_inner_rounds_solve_a_reduced_year_and_the_final_solve_the_full_year():
    seen = []

    def solve(base, config, inp, state, *, reduce, **kw):
        seen.append(reduce)
        return slr.solve_once(base, config, inp, state, reduce=reduce, **kw)

    n = _weak_flat_d(weeks=4)
    cfg = _config(lf_k_periods=1, lf_period_length_h=168)
    out = run_sizing(n, cfg, _inputs(reduce=True), solve=solve)
    assert seen[-1] is False and all(seen[:-1])
    inner = out["rounds"][0]["info"]
    assert inner["basis"] == "reduced_year" and inner["snapshots"] == 168 and inner["of"] == 4 * 168
    assert out["final"]["info"] == {"basis": "full_year", "snapshots": 4 * 168, "of": 4 * 168}
    assert all(r["solve_s"] >= 0 for r in out["rounds"])


def test_the_reduced_year_keeps_the_full_years_weight():
    n = _weak_flat_d(weeks=4)
    red, info = slr.reduce_year(n, _config(lf_k_periods=1, lf_period_length_h=168))
    assert info["snapshots"] == 168
    # Each column keeps its own year: objective 8760 h, stores and generators 4 x 168 h of 1 h steps.
    for col in n.snapshot_weightings.columns:
        assert red.snapshot_weightings[col].sum() == pytest.approx(n.snapshot_weightings[col].sum()), col
    assert red.snapshot_weightings["stores"].max() == pytest.approx(4.0)
    assert len(n.snapshots) == 4 * 168                     # the caller's network is not reduced


def test_the_clustering_cache_keeps_no_entry_for_the_runner():
    from services import time_aggregation_service as tas

    before = set(tas._cache)
    slr.reduce_year(_weak_flat_d(weeks=4), _config(lf_k_periods=1, lf_period_length_h=168))
    assert set(tas._cache) == before


def test_reduced_rounds_passing_and_a_failing_full_year_is_not_converged():
    def solve(base, config, inp, state, *, reduce, **kw):
        return slr.solve_once(base, config, inp, state, reduce=reduce, **kw)

    real = slr.forward_checks

    def forward(result, state, inp):
        checks = real(result, state, inp)
        if result["info"]["basis"] == "full_year":
            checks.append(slr._check("full_year_probe", False, cut={"pst_rhs_mva": 0.0}))
        else:
            checks = [{**c, "ok": True} for c in checks]
        return checks

    import unittest.mock as mock

    with mock.patch.object(slr, "forward_checks", forward):
        out = run_sizing(_weak_flat_d(weeks=4), _config(lf_k_periods=1, lf_period_length_h=168),
                         _inputs(reduce=True), solve=solve)
    assert out["status"] == "not_converged"
    assert out["verdict"].startswith("not converged: reduced-year rounds passed, full year failed")


# ── the control-settings sheet ──────────────────────────────────────────────


def test_a_converged_run_writes_the_control_settings_sheet(tmp_path):
    dyr = tmp_path / "campus.dyr"
    out = run_sizing(_tiny(), _config(), _inputs(dyr_path=str(dyr)))
    assert out["status"] == "converged", out["verdict"]
    assert out["sheet"] is not None
    assert sorted(p.split("/")[-1] for p in out["sheet_paths"]) == ["campus.control_settings.csv",
                                                                   "campus.control_settings.json"]
