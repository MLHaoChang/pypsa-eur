"""
Solve path for committable process Links (grid-to-process ticket 03).

Design: `.scratch/grid-to-process/design.md` §2.3. A process unit is a Link with `eh_process=True`; when one is
committable, a planning solve runs PyPSA's linearised UC (`process_uc: linearised`, the default) and a mandatory
binary operational check re-solves one week per period with binary UC on the planned design. PyPSA 1.1.2 leaves
start-up and shut-down costs unweighted, so the template rules refuse them on a weighted network and the solver
warns on a user network. The MIP settings apply when only a Link is committable.
"""
from __future__ import annotations

import contextlib
import pathlib
import queue
import sys
import threading

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.process_template_rules import refusals
from services.solver import process_uc as PU

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"


def _process_net(*, load=3.0, store=True, p_nom=10.0, p_min_pu=0.4, min_down_time=3,
                 lost_cost=2000.0, weight=1.0) -> pypsa.Network:
    """An electrolyser-like unit (el → NH3) with price spikes that make it want to stop, a product tank, a
    delivery Load and the lost-production slack on the product bus."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    n.snapshot_weightings.loc[:, ["objective", "generators"]] = weight
    n.add("Carrier", ["AC", "NH3"])
    n.add("Bus", "el", carrier="AC")
    n.add("Bus", "nh3", carrier="NH3", unit="t")
    price = np.where((np.arange(24) % 12) < 4, 500.0, 20.0)
    n.add("Generator", "grid", bus="el", p_nom=100.0, marginal_cost=price, carrier="AC")
    n.add("Link", "hb", bus0="el", bus1="nh3", p_nom=p_nom, efficiency=0.5, committable=True,
          p_min_pu=p_min_pu, min_down_time=min_down_time, carrier="NH3")
    n.links["eh_process"] = True
    n.add("Load", "offtake", bus="nh3", p_set=load, carrier="NH3")
    if store:
        n.add("Store", "tank", bus="nh3", e_nom=20.0, e_cyclic=True, carrier="NH3")
    n.add("Generator", "__lost_NH3", bus="nh3", p_nom=1e3, marginal_cost=lost_cost, carrier="NH3")
    n.generators["process_role"] = ""
    n.generators.loc["__lost_NH3", "process_role"] = "shortfall"
    return n


def _run(n, **cfg_kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    import routers.simulation as sim_router
    from tests.conftest import install_network_into_backend

    install_network_into_backend(n)
    cfg = SolverConfig(**cfg_kw)
    sim_router._state["solver_config"] = cfg
    n = PyPSAService.get_network()
    log = queue.SimpleQueue()
    state: dict = {}
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(), log,
                                  state_update=lambda **k: state.update(k))
    lines = []
    while not log.empty():
        lines.append(str(log.get()))
    return n, (status, cond), state, lines


# ── T1: minimum load and minimum down time ─────────────────────────────────


@pytest.mark.live_solve
def test_a_linearised_plan_never_runs_below_min_load_times_u(reset_backend):
    n, (status, _), _, lines = _run(_process_net())
    assert status in ("ok", "optimal")
    p, u = n.links_t.p0["hb"], n.links_t.status["hb"]
    assert ((u >= -1e-6) & (u <= 1 + 1e-6)).all()
    assert (p >= 0.4 * 10.0 * u - 1e-6).all()
    assert any("linearised UC" in ln for ln in lines)


@pytest.mark.live_solve
def test_a_binary_plan_keeps_min_load_and_every_stop_lasts_min_down_time(reset_backend):
    n, (status, _), _, _ = _run(_process_net(), process_uc="binary")
    assert status in ("ok", "optimal")
    p, u = n.links_t.p0["hb"], n.links_t.status["hb"].round()
    assert set(u.unique()) <= {0.0, 1.0}
    assert 0.0 in set(u.unique())                                     # it does stop
    assert (p >= 0.4 * 10.0 * u - 1e-6).all()
    runs, cur = [], 0
    for v in u.to_numpy():
        if v == 0:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    # The last off-run may wrap around the horizon end; every closed one lasts min_down_time.
    assert runs and all(r >= 3 for r in runs[:-1] or runs)


# ── T2: weighted start-up costs ────────────────────────────────────────────


@pytest.mark.parametrize("attr", ["start_up_cost", "shut_down_cost"])
def test_the_template_rules_refuse_a_start_cost_on_a_weighted_network(attr):
    n = _process_net(weight=8760 / 168)
    n.links.loc["hb", attr] = 500.0
    out = refusals(n, {"voll": 0.0})
    hit = [r for r in out if "hb" in r and attr in r]
    assert hit, out
    assert "stand_by_cost" in hit[0] and "shortfall" in hit[0]


def test_a_period_weight_alone_makes_the_network_weighted():
    n = _process_net()
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], n.snapshots]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings["objective"] = [10.0, 10.0]
    assert PU.is_weighted(n)
    n.investment_period_weightings["objective"] = [1.0, 1.0]
    assert not PU.is_weighted(n)


def test_the_solver_warns_on_a_weighted_user_network_and_does_not_refuse():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    n = _process_net(weight=8760 / 168)
    n.links.loc["hb", "start_up_cost"] = 500.0
    issues = [i for i in validate_for_run(n, SolverConfig()) if i.code == "uc_start_cost_unweighted"]
    assert issues and all(i.severity == "warning" for i in issues)
    assert "hb" in issues[0].message or issues[0].name == "hb"


def test_an_unweighted_network_passes_silently():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    n = _process_net()
    n.links.loc["hb", "start_up_cost"] = 500.0
    assert not [i for i in validate_for_run(n, SolverConfig()) if i.code == "uc_start_cost_unweighted"]
    assert refusals(n, {"voll": 0.0}) == []


# ── T3: the other template rules ───────────────────────────────────────────


def test_the_template_rules_refuse_lost_priced_at_or_below_buy_in():
    n = _process_net(lost_cost=400.0)
    n.add("Generator", "__buy_NH3", bus="nh3", p_nom=10.0, marginal_cost=400.0, carrier="NH3")
    n.generators.loc["__buy_NH3", "process_role"] = "buy_in"
    out = refusals(n, {"voll": 0.0})
    assert any("__lost_NH3" in r and "__buy_NH3" in r for r in out), out
    n.generators.loc["__lost_NH3", "marginal_cost"] = 401.0
    assert refusals(n, {"voll": 0.0}) == []


def test_the_template_rules_refuse_a_missing_or_voll_lost_value():
    n = _process_net()
    n.remove("Generator", "__lost_NH3")
    assert any("nh3" in r and "lost" in r for r in refusals(n, {"voll": 0.0}))
    n = _process_net(lost_cost=5000.0)
    assert any("VoLL" in r for r in refusals(n, {"voll": 5000.0}))
    n.generators.loc["__lost_NH3", "marginal_cost"] = float("nan")
    assert any("__lost_NH3" in r for r in refusals(n, {"voll": 0.0}))


def test_the_template_rules_refuse_a_committable_extendable_process_unit():
    n = _process_net()
    n.links.loc["hb", "p_nom_extendable"] = True
    assert any("hb" in r and "extendable" in r for r in refusals(n, {"voll": 0.0}))


def _template_networks():
    sys.path.insert(0, str(TPL_DIR))
    import eh_site as S
    import eh_templates as T

    yield "eh_site", S.build_eh_site({"site_type": "campus"}), {"voll": 5000.0}
    for tid, build in T.BUILDERS.items():
        cfg = T.solver_config(tid) if tid in T.TEMPLATE_META else {}
        yield tid, build(), cfg


def test_every_bundled_template_passes_the_template_rules():
    seen = []
    for tid, n, cfg in _template_networks():
        assert refusals(n, cfg) == [], tid
        seen.append(tid)
    assert "eh_microgrid_staged" in seen


# ── T4: the binary operational check ───────────────────────────────────────


@pytest.mark.live_solve
def test_the_binary_check_flags_a_unit_below_its_minimum_load(reset_backend):
    # Delivery 1 t/h needs 2 MW_el: below the 4 MW minimum load and no tank to absorb a run at 4.
    n, (status, _), state, lines = _run(_process_net(load=1.0, store=False))
    assert status in ("ok", "optimal")
    check = state["last_process_uc_check"]
    (row,) = check["periods"]
    assert row["flagged"] is True
    assert row["extra_shortfall"]["nh3"]["value"] > 0.0
    assert check["flagged"] is True
    assert "linearised UC understates restarts" in check["message"]
    assert any("linearised UC understates restarts" in ln for ln in lines)


@pytest.mark.live_solve
def test_the_binary_check_stays_quiet_when_the_relaxation_is_tight(reset_backend):
    n, (status, _), state, _ = _run(_process_net(load=4.0, store=False, min_down_time=0,
                                                 p_min_pu=0.4))
    assert status in ("ok", "optimal")
    (row,) = state["last_process_uc_check"]["periods"]
    assert row["flagged"] is False
    assert abs(row["pct"]) <= 0.02


@pytest.mark.live_solve
def test_the_binary_check_reports_every_period(reset_backend):
    n = _process_net(load=1.0, store=False)
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], n.snapshots]))
    n.investment_periods = [2030, 2040]
    n.loads_t.p_set = pd.DataFrame({"offtake": 1.0}, index=n.snapshots)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"grid": np.tile(np.where((np.arange(24) % 12) < 4, 500.0, 20.0), 2)}, index=n.snapshots)
    n, (status, _), state, _ = _run(n, multi_investment_periods=True)
    assert status in ("ok", "optimal")
    rows = state["last_process_uc_check"]["periods"]
    assert [r["period"] for r in rows] == [2030, 2040]
    assert all(r["flagged"] for r in rows)


@pytest.mark.live_solve
def test_no_binary_check_without_a_committable_process_unit(reset_backend):
    n = _process_net()
    n.links["eh_process"] = False
    _, (status, _), state, _ = _run(n)
    assert status in ("ok", "optimal")
    assert state.get("last_process_uc_check") is None


# ── T5: MIP settings when only a Link is committable ───────────────────────


@pytest.mark.live_solve
def test_the_mip_settings_apply_when_only_a_link_is_committable(reset_backend, monkeypatch):
    import services.solver_service as SS

    seen = {}
    real = SS._resolve_mip_kwargs

    def spy(*a, **k):
        seen["called"] = True
        return real(*a, **k)

    monkeypatch.setattr(SS, "_resolve_mip_kwargs", spy)
    _, (status, _), _, lines = _run(_process_net(), process_uc="binary")
    assert status in ("ok", "optimal")
    assert seen.get("called")
    assert any("0 committable generator(s), 1 committable link(s)" in ln for ln in lines)


def test_committable_counts_name_both_components():
    n = _process_net()
    n.add("Generator", "genset", bus="el", p_nom=5.0, committable=True)
    assert PU.committable_counts(n) == (1, 1)


# ── T6: composition with the island's linearised UC ────────────────────────


def test_the_planning_kwargs_compose_with_the_island_kwargs():
    n = _process_net()
    from services.solver_service import SolverConfig

    proc = PU.planning_uc_kwargs(n, SolverConfig())
    assert proc == {"linearized_unit_commitment": True}
    assert PU.planning_uc_kwargs(n, SolverConfig(process_uc="binary")) == {}
    assert PU.merge_uc_kwargs({"linearized_unit_commitment": True}, proc) == \
        {"linearized_unit_commitment": True}
    assert PU.merge_uc_kwargs({}, proc) == proc
    assert PU.merge_uc_kwargs({"linearized_unit_commitment": True}, {}) == \
        {"linearized_unit_commitment": True}


@pytest.mark.live_solve
def test_the_solve_passes_linearised_uc_once_and_the_island_restores_once(reset_backend,
                                                                         monkeypatch):
    import services.solver_service as SS

    restores = []

    @contextlib.contextmanager
    def island_uc(network, config):
        try:
            yield {"linearized_unit_commitment": True}
        finally:
            restores.append(1)

    monkeypatch.setattr(SS, "_island_solve_uc", island_uc)
    calls = []
    real = pypsa.optimization.OptimizationAccessor.__call__

    def spy(self, *a, **k):
        calls.append(k.get("linearized_unit_commitment"))
        return real(self, *a, **k)

    monkeypatch.setattr(pypsa.optimization.OptimizationAccessor, "__call__", spy)
    _, (status, _), _, _ = _run(_process_net())
    assert status in ("ok", "optimal")
    assert restores == [1]
    assert calls[0] is True                    # the plan; the check's own solves follow


# ── process_uc auto / linearised with other committable units ──────────────


def test_auto_keeps_a_network_with_other_committable_units_binary():
    from services.solver_service import SolverConfig

    n = _process_net()
    n.add("Generator", "genset", bus="el", p_nom=5.0, committable=True, marginal_cost=100.0)
    assert PU.planning_uc_kwargs(n, SolverConfig()) == {}
    assert "binary" in PU.planning_uc_note(n, SolverConfig())
    explicit = SolverConfig(process_uc="linearised")
    assert PU.planning_uc_kwargs(n, explicit) == {"linearized_unit_commitment": True}
    assert "WARN: also relaxes 1 committable generator(s)" in PU.planning_uc_note(n, explicit)


def test_a_network_without_process_units_gets_no_uc_keyword():
    from services.solver_service import SolverConfig

    n = _process_net()
    n.links["eh_process"] = False
    for mode in ("auto", "linearised", "binary"):
        assert PU.planning_uc_kwargs(n, SolverConfig(process_uc=mode)) == {}
        assert PU.planning_uc_note(n, SolverConfig(process_uc=mode)) is None
    assert PU.sclopf_model_kwargs(n, {}) == {}


# ── unchanged kwargs on every branch without process units ─────────────────


def _two_period(n):
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], n.snapshots]))
    n.investment_periods = [2030, 2040]
    n.loads_t.p_set = pd.DataFrame({"offtake": 3.0}, index=n.snapshots)
    n.generators_t.marginal_cost = pd.DataFrame(
        {"grid": np.tile(np.where((np.arange(24) % 12) < 4, 500.0, 20.0), 2)}, index=n.snapshots)
    return n


def _meshed(n):
    """Three electrical buses in a ring, so SCLOPF has outages to take."""
    n.buses.loc["el", "v_nom"] = 20.0
    n.add("Bus", ["el2", "el3"], carrier="AC", v_nom=20.0)
    n.add("Line", "l12", bus0="el", bus1="el2", x=0.1, r=0.01, s_nom=200.0)
    n.add("Line", "l23", bus0="el2", bus1="el3", x=0.1, r=0.01, s_nom=200.0)
    n.add("Line", "l31", bus0="el3", bus1="el", x=0.1, r=0.01, s_nom=200.0)
    return n


def _spy_optimize(monkeypatch):
    calls = []
    acc = pypsa.optimization.OptimizationAccessor
    real_call, real_sc, real_rh = (acc.__call__, acc.optimize_security_constrained,
                                   acc.optimize_with_rolling_horizon)

    def call(self, *a, **k):
        calls.append(("optimize", k))
        return real_call(self, *a, **k)

    def sc(self, *a, **k):
        calls.append(("sclopf", k))
        return real_sc(self, *a, **k)

    def rh(self, *a, **k):
        calls.append(("rolling", k))
        return real_rh(self, *a, **k)

    monkeypatch.setattr(acc, "__call__", call)
    monkeypatch.setattr(acc, "optimize_security_constrained", sc)
    monkeypatch.setattr(acc, "optimize_with_rolling_horizon", rh)
    return calls


BRANCHES = {
    "full": dict(),
    "rolling": dict(solve_strategy="rolling", rolling_horizon=12, rolling_overlap=2),
    "sclopf": dict(sclopf=True, sclopf_include_all_lines=True),
    "myopic": dict(solve_strategy="myopic", multi_investment_periods=True),
}


@pytest.mark.live_solve
@pytest.mark.parametrize("branch", sorted(BRANCHES))
def test_no_process_unit_means_no_uc_keyword_on_any_branch(reset_backend, monkeypatch, branch):
    n = _meshed(_process_net())
    n.links["eh_process"] = False
    if branch == "myopic":
        n = _two_period(n)
    calls = _spy_optimize(monkeypatch)
    _, (status, cond), state, _ = _run(n, **BRANCHES[branch])
    assert status in ("ok", "optimal"), (status, cond)
    assert calls, branch
    for kind, k in calls:
        assert "linearized_unit_commitment" not in k, (branch, kind)
        assert "model_kwargs" not in k, (branch, kind)
    assert state.get("last_process_uc_check") is None


@pytest.mark.live_solve
@pytest.mark.parametrize("branch", sorted(BRANCHES))
def test_a_process_unit_reaches_every_branch_linearised(reset_backend, monkeypatch, branch):
    n = _meshed(_process_net())
    if branch == "myopic":
        n = _two_period(n)
    calls = _spy_optimize(monkeypatch)
    _, (status, cond), state, _ = _run(n, **BRANCHES[branch])
    assert status in ("ok", "optimal"), (status, cond)
    kind, k = calls[0]
    assert kind == {"sclopf": "sclopf", "rolling": "rolling"}.get(branch, "optimize")
    if kind == "sclopf":
        assert k["model_kwargs"]["linearized_unit_commitment"] is True
    else:
        assert k["linearized_unit_commitment"] is True
    assert state["last_process_uc_check"]["periods"]


# ── the check never fails the run or changes the plan ──────────────────────


@pytest.mark.live_solve
def test_a_failing_binary_check_leaves_the_run_ok_and_the_plan_unchanged(reset_backend,
                                                                       monkeypatch):
    n, (status, _), state, _ = _run(_process_net(load=1.0, store=False))
    assert status in ("ok", "optimal")
    objective, p0 = n.objective, n.links_t.p0.copy()
    assert state["last_process_uc_check"]["status"] == "ok"

    def boom(*a, **k):
        raise RuntimeError("solver crashed")

    monkeypatch.setattr(PU, "_solve_window", boom)
    n, (status, _), state, _ = _run(_process_net(load=1.0, store=False))
    assert status in ("ok", "optimal")
    assert n.objective == pytest.approx(objective)
    pd.testing.assert_frame_equal(n.links_t.p0, p0)
    check = state["last_process_uc_check"]
    assert check["status"] == "unverified" and check["flagged"] is False
    assert "solver crashed" in check["periods"][0]["reason"]
    assert check["wall_time_s"] >= 0


def test_an_infeasible_binary_window_is_unverified_not_a_number():
    from services.solver_service import SolverConfig

    n = _process_net()
    n.optimize(solver_name="highs", linearized_unit_commitment=True)
    check = PU.binary_check(n, SolverConfig(),
                            solve=lambda m, sns, multi, cfg, opts, kw: ("warning", "infeasible", 0.0))
    (row,) = check["periods"]
    assert row["status"] == "unverified" and row["pct"] is None and "infeasible" in row["reason"]
    assert any(r["item"] == "window: window" and "first 24 snapshots" in r["value"] for r in check["ledger"])
    assert any("extra_functionality not applied" in r["value"] for r in check["ledger"])
    assert any("capex is excluded" in r["value"] for r in check["ledger"])


# ── strict JSON ────────────────────────────────────────────────────────────


def test_the_check_endpoint_serves_strict_json(reset_backend):
    import json

    import routers.results as R
    from fastapi import Response

    assert isinstance(R.get_process_uc_check(), Response)          # nothing solved: 204
    R._state["last_process_uc_check"] = {
        "status": "ok", "flagged": True, "periods": [
            {"period": 2030, "pct": float("inf"), "cost_lin": 0.0,
             "extra_shortfall": {"nh3": {"value": np.float64("nan"), "unit": "t"}}}]}
    out = R.get_process_uc_check()
    json.dumps(out, allow_nan=False)
    assert out["periods"][0]["pct"] is None
    assert set(out["non_finite_paths"]) == {"periods[0].pct", "periods[0].extra_shortfall.nh3.value"}


# ── review follow-ups ──────────────────────────────────────────────────────


def test_an_intermediate_product_needs_no_shortfall_slack():
    n = _process_net()
    n.add("Bus", "h2", carrier="H2")
    n.add("Link", "electrolyser", bus0="el", bus1="h2", p_nom=5.0, efficiency=0.7)
    n.add("Link", "h2_to_nh3", bus0="h2", bus1="nh3", p_nom=5.0, efficiency=0.5)
    n.links.loc[["electrolyser", "h2_to_nh3"], "eh_process"] = True
    assert refusals(n, {"voll": 0.0}) == []          # h2 carries no Load: intermediate


def test_the_island_relaxing_a_binary_plan_is_said():
    from services.solver_service import SolverConfig

    n = _process_net()
    cfg = SolverConfig(process_uc="binary")
    assert PU.island_relaxes_binary_note(n, cfg, {}) is None
    assert "relaxes the 1 process link(s)" in PU.island_relaxes_binary_note(
        n, cfg, {"linearized_unit_commitment": True})
    assert PU.island_relaxes_binary_note(n, SolverConfig(), {"linearized_unit_commitment": True}) is None


def test_sclopf_takes_the_keyword_as_itself_without_passive_branches():
    n = _process_net()
    assert PU.sclopf_model_kwargs(n, {"linearized_unit_commitment": True}) == \
        {"linearized_unit_commitment": True}
    m = _meshed(_process_net())
    assert PU.sclopf_model_kwargs(m, {"linearized_unit_commitment": True})["model_kwargs"][
        "linearized_unit_commitment"] is True


@pytest.mark.live_solve
def test_the_ledger_carries_each_periods_result(reset_backend):
    _, (status, _), state, _ = _run(_process_net(load=1.0, store=False))
    assert status in ("ok", "optimal")
    check = state["last_process_uc_check"]
    results = [r["value"] for r in check["ledger"] if r["item"].endswith(": result")]
    assert len(results) == 1 and "binary − linearised" in results[0] and "FLAGGED" in results[0]
    assert check["cost_basis"].startswith("operating cost")
    (row,) = check["periods"]
    assert row["extra_shortfall"]["nh3"]["value_window"] == pytest.approx(24.0)


def test_an_unverified_payload_has_the_checks_shape():
    from services.solver_service import SolverConfig

    n = _process_net()
    n.optimize(solver_name="highs", linearized_unit_commitment=True)
    ok = PU.binary_check(n, SolverConfig())
    bad = PU.unverified_payload(n, "boom", 0.0)
    assert set(bad) == set(ok)
    assert bad["status"] == "unverified" and bad["flagged"] is False
