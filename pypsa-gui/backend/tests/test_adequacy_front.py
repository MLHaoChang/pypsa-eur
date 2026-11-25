"""
The multi-objective front: reliability lever × CO2 cap (DC review issue 01).

Two toys:

* ``_hand`` — one bus, constant 100 MW load, an extendable gas unit
  (50 000 EUR/MW-yr, 20 EUR/MWh, 0.2 t/MWh fuel at efficiency 0.4, so
  0.5 t/MWh_el) and an extendable clean firm unit (300 000 EUR/MW-yr, no
  fuel). Uncapped, gas serves everything. A cap of C t/yr lets gas make at
  most 2C MWh/yr, so the clean unit covers the rest; solved by hand below.
* ``_three_bus`` — gas, PV with a day profile and a battery on three buses.
  The reserve margin and the CO2 cap both bind somewhere in it, so the
  front has a real trade-off.
"""

from __future__ import annotations

import math
import queue
import threading
import types

import pandas as pd
import pypsa
import pytest

from services.adequacy import front as fr
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig

HOURS_PER_YEAR = 8760.0


def _hand(hours: int = 4) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "clean")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add(
        "Generator",
        "gas",
        bus="b",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=1000.0,
        capital_cost=50_000.0,
        marginal_cost=20.0,
        efficiency=0.4,
        outage_rate_value=0.05,
        outage_rate_basis="EFORd",
        mttr_hours=10.0,
    )
    n.add(
        "Generator",
        "clean",
        bus="b",
        carrier="clean",
        p_nom_extendable=True,
        p_nom_max=1000.0,
        capital_cost=300_000.0,
        marginal_cost=0.0,
        outage_rate_value=0.05,
        outage_rate_basis="EFORd",
        mttr_hours=10.0,
    )
    return n


def _three_bus(hours: int = 24) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=hours, freq="h"))
    n.add("Carrier", "gas", co2_emissions=0.2)
    n.add("Carrier", "solar")
    n.add("Carrier", "battery")
    for b in "abc":
        n.add("Bus", b, carrier="AC")
    n.add("Line", "ab", bus0="a", bus1="b", x=0.1, r=0.01, s_nom=1000.0)
    n.add("Line", "bc", bus0="b", bus1="c", x=0.1, r=0.01, s_nom=1000.0)
    n.add("Load", "l", bus="c", p_set=100.0)
    n.add(
        "Generator",
        "gas",
        bus="a",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=1000.0,
        capital_cost=50_000.0,
        marginal_cost=20.0,
        efficiency=0.4,
        outage_rate_value=0.05,
        outage_rate_basis="EFORd",
        mttr_hours=10.0,
    )
    n.add(
        "Generator",
        "pv",
        bus="b",
        carrier="solar",
        p_nom_extendable=True,
        p_nom_max=2000.0,
        capital_cost=40_000.0,
        marginal_cost=0.0,
    )
    prof = [0] * 6 + [0.3, 0.6, 0.8, 0.9, 1, 1, 1, 0.9, 0.8, 0.6, 0.3] + [0] * 7
    n.generators_t.p_max_pu = pd.DataFrame({"pv": prof[:hours]}, index=n.snapshots)
    n.add(
        "StorageUnit",
        "bat",
        bus="c",
        carrier="battery",
        p_nom_extendable=True,
        p_nom_max=500.0,
        max_hours=4.0,
        capital_cost=60_000.0,
        efficiency_store=0.95,
        efficiency_dispatch=0.95,
        cyclic_state_of_charge=True,
    )
    return n


def _cfg(**kw):
    return SolverConfig(**{"solver_name": "highs", "voll": 5000.0, **kw})


def _run(n, **kw):
    PyPSAService.set_network(n)
    kw.setdefault("mc_draws", 100)
    return fr.run_front_study(n, PyPSAService.get_lock(), kw.pop("cfg", _cfg()), **kw)


def _by_key(res):
    return {(p["level"], p["co2_cap_t_per_yr"]): p for p in res["points"]}


# --------------------------------------------------------------------------
# The hand-solved point (F3)
# --------------------------------------------------------------------------


def test_a_binding_co2_point_matches_the_hand_solution():
    # Cap 219 000 t/yr -> gas energy ≤ 438 000 MWh/yr = 50 MW average, so
    # gas 50 MW and clean 50 MW. capex = 50·50 000 + 50·300 000 = 17.5 M,
    # opex = 20 · 438 000 = 8.76 M. One more tonne lets gas make 2 MWh more,
    # displacing 2/8760 MW of clean: 300 000·2/8760 − 50 000·2/8760 − 20·2
    # = 17.0776 EUR/t saved, the shadow price.
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None, 219_000.0])
    p = _by_key(res)[(100.0, 219_000.0)]
    assert p["status"] == "ok", p
    pt = p["point"]
    assert pt["capacities"]["generators:gas"] == pytest.approx(50.0, abs=1e-3)
    assert pt["capacities"]["generators:clean"] == pytest.approx(50.0, abs=1e-3)
    # every size carries its unit, read off its bus (UX 30, DC 01 PR3)
    assert pt["capacity_units"] == {k: "MW" for k in pt["capacities"]}
    assert pt["capex_eur_per_yr"] == pytest.approx(17_500_000.0, rel=1e-6)
    assert pt["opex_eur_per_yr"] == pytest.approx(8_760_000.0, rel=1e-6)
    assert pt["total_eur_per_yr"] == pytest.approx(26_260_000.0, rel=1e-6)
    assert pt["co2_t_per_yr"] == pytest.approx(219_000.0, rel=1e-6)
    assert pt["co2_binding"] is True
    assert pt["co2_shadow_eur_per_t"] == pytest.approx(17.0776, abs=1e-3)
    assert pt["co2_shadow_eur_per_t"] > 0

    unc = _by_key(res)[(100.0, None)]["point"]
    assert unc["capacities"]["generators:gas"] == pytest.approx(100.0, abs=1e-3)
    assert unc["co2_t_per_yr"] == pytest.approx(438_000.0, rel=1e-6)
    assert unc["co2_shadow_eur_per_t"] is None and unc["co2_binding"] is None
    assert res["reference_co2_t_per_yr"] == pytest.approx(438_000.0, rel=1e-6)


def test_a_co2_limit_goal_in_the_config_leaves_the_fronts_cap_the_only_one():
    # DC review 02 / GK A C6: a goal limit far below both points' emissions
    # would refuse each point as a duplicate cap, or bind it, if it reached
    # the front's solves. Every point solves, and each emits what its own cap
    # (or none) allows, as in the hand solution.
    n = _hand()
    res = _run(
        n,
        cfg=_cfg(co2_limit_t_per_yr=1.0),
        lever="ens_cap",
        levels=[100.0],
        co2_caps_t=[None, 219_000.0],
    )
    by = _by_key(res)
    assert all(p["status"] == "ok" for p in res["points"]), res["points"]
    assert by[(100.0, 219_000.0)]["point"]["co2_t_per_yr"] == pytest.approx(
        219_000.0, rel=1e-6
    )
    assert by[(100.0, None)]["point"]["co2_t_per_yr"] == pytest.approx(
        438_000.0, rel=1e-6
    )
    assert n.global_constraints.empty


def test_a_front_point_with_the_co2_goal_on_solves_one_cap_row_per_period(monkeypatch):
    # DC review 02 C6, the PR that lands second adds it (#1989 is on master):
    # a DC 01 front per-point solve with the CO2 limit goal set carries exactly
    # one CO2 cap row per period, and it is the front's own `co2_cap`. The goal
    # is dropped from each point's config (`front._cfg_for`), so the goal adds
    # no `co2_cap_<p>` row; the front's swept cap wins.
    from services.solver import co2_cap

    seen = []
    real_apply = co2_cap.apply

    def spy(n, cfg, phase, captured):
        gc = n.global_constraints
        rows = gc[
            (gc.type == "primary_energy") & (gc.carrier_attribute == "co2_emissions")
        ]
        seen.append((cfg.co2_limit_t_per_yr, sorted(rows.index)))
        undo = real_apply(n, cfg, phase, captured)
        gc = n.global_constraints
        after = gc[
            (gc.type == "primary_energy") & (gc.carrier_attribute == "co2_emissions")
        ]
        seen[-1] += (sorted(after.index),)
        return undo

    monkeypatch.setattr(co2_cap, "apply", spy)
    res = _run(
        _hand(),
        cfg=_cfg(co2_limit_t_per_yr=1.0),
        lever="ens_cap",
        levels=[100.0],
        co2_caps_t=[219_000.0],
    )
    assert res["points"][0]["status"] == "ok", res["points"]
    assert seen, "the goal's apply never ran on a front point"
    # Two solves: the capped point, and the front's uncapped reference solve.
    # In neither does the goal add a row; the capped one carries the front's.
    assert [limit for limit, _, _ in seen] == [None] * len(seen)
    assert all(before == after for _, before, after in seen)
    assert sorted(after for _, _, after in seen) == [[], [fr.CO2_CAP_NAME]]
    assert res["points"][0]["point"]["co2_t_per_yr"] == pytest.approx(
        219_000.0, rel=1e-6
    )


def test_a_cap_above_the_emissions_has_no_shadow_price():
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[500_000.0])
    pt = res["points"][0]["point"]
    assert pt["co2_binding"] is False
    assert pt["co2_shadow_eur_per_t"] is None


def test_a_goal_cap_is_overridden_on_the_copy_only():
    n = _hand()
    n.add(
        "GlobalConstraint",
        "co2_cap",
        type="primary_energy",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=123.0,
    )
    res = _run(n, lever="ens_cap", levels=[100.0], co2_caps_t=[219_000.0])
    assert res["points"][0]["point"]["co2_t_per_yr"] == pytest.approx(
        219_000.0, rel=1e-6
    )
    assert n.global_constraints.at["co2_cap", "constant"] == 123.0
    assert list(n.global_constraints.index) == ["co2_cap"]


def test_apply_co2_cap_names_one_constraint_per_period_on_its_years():
    n = _hand()
    snaps = pd.MultiIndex.from_product(
        [[2030, 2040], n.snapshots[:2]], names=["period", "timestep"]
    )
    n.set_snapshots(snaps)
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = [10.0, 10.0]
    names = fr.apply_co2_cap(n, 8760.0)
    assert names == ["co2_cap_2030", "co2_cap_2040"]
    gc = n.global_constraints
    # 2 hours × 10 years each = 20/8760 yr; 8760 t/yr over that = 20 t.
    assert gc.loc["co2_cap_2030", "constant"] == pytest.approx(20.0)
    assert gc.loc["co2_cap_2040", "investment_period"] == 2040
    assert fr.apply_co2_cap(n, None) == []
    assert n.global_constraints.empty


def _two_periods() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(
        pd.MultiIndex.from_product(
            [[2030, 2040], pd.date_range("2030-01-01", periods=2, freq="h")]
        )
    )
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = [10.0, 10.0]
    n.add("Carrier", "gas", co2_emissions=0.5)
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add(
        "Generator",
        "g",
        bus="b",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=1000.0,
        capital_cost=1000.0,
        marginal_cost=10.0,
        build_year=2030,
        lifetime=50,
    )
    return n


def test_annual_costs_and_co2_hold_on_a_multi_period_network():
    # 100 MW in each of two 10-year periods: 1000 EUR/MW-yr, 10 EUR/MWh,
    # 0.5 t/MWh. Per year: capex 100 000, opex 100·10·8760 = 8.76 M, CO2
    # 100·8760·0.5, whatever the period years and modelled hours are.
    res = _run(
        _two_periods(),
        lever="ens_cap",
        levels=[100.0],
        cfg=_cfg(multi_investment_periods=True),
    )
    pt = res["points"][0]["point"]
    assert pt["capex_eur_per_yr"] == pytest.approx(100_000.0, rel=1e-6)
    assert pt["opex_eur_per_yr"] == pytest.approx(8_760_000.0, rel=1e-6)
    assert pt["co2_t_per_yr"] == pytest.approx(438_000.0, rel=1e-6)


# --------------------------------------------------------------------------
# Monotone along each axis, MC per point (F7)
# --------------------------------------------------------------------------


def test_the_front_is_monotone_along_each_axis_and_mc_follows_the_fleet():
    res = _run(_three_bus(), levels=[0.05, 0.4], co2_caps_t=[None, 150_000.0])
    assert res["lever"] == "reserve_margin"
    assert "no outages" in res["lever_reason"]
    assert res["reliability_basis"] == "mc_eue"
    pts = _by_key(res)
    tol = 1e-3
    for lv in (0.05, 0.4):  # tightening CO2 never makes it cheaper
        a, b = pts[(lv, None)]["point"], pts[(lv, 150_000.0)]["point"]
        assert b["total_eur_per_yr"] >= a["total_eur_per_yr"] - tol
        assert b["co2_t_per_yr"] <= a["co2_t_per_yr"] + tol
    for cap in (None, 150_000.0):  # a higher margin never makes it cheaper
        a, b = pts[(0.05, cap)]["point"], pts[(0.4, cap)]["point"]
        assert b["total_eur_per_yr"] >= a["total_eur_per_yr"] - tol
    # the margin binds at 40 % uncapped and the CO2 cap binds at 150 kt
    assert pts[(0.4, None)]["point"]["reliability_binding"] is True
    assert pts[(0.05, 150_000.0)]["point"]["co2_binding"] is True
    assert (
        pts[(0.4, None)]["point"]["total_eur_per_yr"]
        > pts[(0.05, None)]["point"]["total_eur_per_yr"] + 1.0
    )
    for p in res["points"]:
        mc = p["mc"]
        assert mc["n_samples"] == 100
        assert set(mc) >= {"lole_ci", "eue_ci", "resolution_floor_h", "below_floor"}
    assert res["mc_budget"]["draws_per_point"] == 100
    assert res["degenerate"]["all"] is False


def _units_plus_new(hours: int = 4) -> pypsa.Network:
    """Four fixed 30 MW gas units and one extendable new unit, as the DC
    template carries its gensets: the MC samples each row as one unit."""
    n = _hand(hours)
    n.remove("Generator", ["clean", "gas"])
    n.add(
        "Generator",
        "gas_new",
        bus="b",
        carrier="gas",
        p_nom_extendable=True,
        p_nom_max=1000.0,
        capital_cost=50_000.0,
        marginal_cost=20.0,
        efficiency=0.4,
        outage_rate_value=0.05,
        outage_rate_basis="EFORd",
        mttr_hours=10.0,
    )
    for i in range(4):
        n.add(
            "Generator",
            f"gas_{i}",
            bus="b",
            carrier="gas",
            p_nom=30.0,
            marginal_cost=20.0,
            efficiency=0.4,
            outage_rate_value=0.05,
            outage_rate_basis="EFORd",
            mttr_hours=10.0,
        )
    return n


def test_a_higher_margin_buys_sampled_reliability_with_money():
    # Existing firm credit 4·30·0.95 = 114 MW. A 5 % margin (105 MW) builds
    # nothing new; 50 % (150 MW) builds (150 − 114)/0.95 = 37.89 MW, one more
    # sampled unit, so at the same seed and draws LOLE and EUE fall.
    res = _run(_units_plus_new(), levels=[0.05, 0.5], mc_draws=200)
    lo, hi = res["points"]
    assert lo["point"]["capacities"]["generators:gas_new"] == pytest.approx(
        0.0, abs=1e-3
    )
    assert hi["point"]["capacities"]["generators:gas_new"] == pytest.approx(
        37.895, abs=1e-2
    )
    assert lo["point"]["reliability_binding"] is False
    assert hi["point"]["reliability_binding"] is True
    assert hi["mc"]["lole_hours"] < lo["mc"]["lole_hours"]
    assert hi["mc"]["eue_mwh"] < lo["mc"]["eue_mwh"]
    assert hi["point"]["total_eur_per_yr"] > lo["point"]["total_eur_per_yr"]
    assert [p["on_front"] for p in res["points"]] == [True, True]


def test_the_same_fleet_samples_the_same_reliability():
    # 5 % does not bind, so the two uncapped levels build the same fleet:
    # common random numbers must give the identical LOLE, not a nearby one.
    res = _run(_three_bus(), levels=[0.05, 0.2])
    a, b = res["points"]
    assert fr.same_design(a["point"], b["point"])
    assert a["mc"]["lole_hours"] == b["mc"]["lole_hours"]
    assert a["mc"]["eue_mwh"] == b["mc"]["eue_mwh"]


# --------------------------------------------------------------------------
# No foreground mutation (F1), order independence (F2)
# --------------------------------------------------------------------------


def _frames(n):
    out = {}
    for c in n.iterate_components():
        out[c.name] = c.df.copy()
        for attr, df in c.pnl.items():
            out[f"{c.name}_t.{attr}"] = df.copy()
    return out


def _assert_same_frames(before, after):
    assert before.keys() == after.keys()
    for k in before:
        pd.testing.assert_frame_equal(before[k], after[k], check_exact=True, obj=k)


@pytest.mark.parametrize("failure", ["status", "raise"])
def test_the_foreground_network_and_revision_are_untouched(monkeypatch, failure):
    from services.adequacy import sweep

    n = _hand()
    PyPSAService.set_network(n)
    ctx = PyPSAService.get_active_context()
    rev = ctx.network_revision
    before = _frames(n)
    real = sweep._solve_once
    calls = {"n": 0}

    solved, contexts = [], []

    def flaky(cfg, net, lock, lq, sink):
        calls["n"] += 1
        solved.append(net)
        contexts.append(getattr(PyPSAService._solving_ctx, "ctx", None))
        if calls["n"] == 2:
            if failure == "raise":
                raise RuntimeError("solver died")
            sink.update(_status="warning", _condition="infeasible")
            return
        real(cfg, net, lock, lq, sink)

    monkeypatch.setattr(sweep, "_solve_once", flaky)
    res = fr.run_front_study(
        n,
        PyPSAService.get_lock(),
        _cfg(),
        lever="ens_cap",
        levels=[100.0],
        co2_caps_t=[None, 219_000.0, 300_000.0],
        mc_draws=20,
    )
    # one broken point is kept as failed; the others still solve
    failed = [p for p in res["points"] if p["status"] != "ok"]
    assert [p["status"] for p in failed] == (
        ["error"] if failure == "raise" else ["infeasible"]
    )
    if failure == "raise":
        assert failed[0]["error"] == "solver died"
    assert failed[0]["point"] is None and failed[0]["mc"] is None
    assert failed[0]["on_front"] is None and failed[0]["dominated_by"] == []
    assert sum(p["status"] == "ok" for p in res["points"]) == 2
    _assert_same_frames(before, _frames(n))
    assert PyPSAService.get_active_context().network_revision == rev
    assert PyPSAService.get_active_context().network is n
    # every solve ran on its own fresh copy, never the foreground (kills a
    # shared `work = base`), and in a solving context bound to the study's
    # private base, never the foreground's context
    assert len({id(x) for x in solved}) == len(solved) == 3
    assert all(x is not n for x in solved)
    assert all(c is not None and c.network is not n for c in contexts)
    assert len({id(c.network) for c in contexts}) == 1
    assert all(c is not PyPSAService.get_active_context() for c in contexts)


def test_an_interrupt_is_not_swallowed_as_a_failed_point(monkeypatch):
    from services.adequacy import sweep

    def interrupted(cfg, net, lock, lq, sink):
        raise KeyboardInterrupt

    monkeypatch.setattr(sweep, "_solve_once", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])


def test_a_solved_foreground_with_a_live_solver_model_can_be_studied():
    # copying a network whose solver model is still attached raises; the
    # private copy must detach it under the lock first
    n = _hand()
    n.optimize(solver_name="highs")
    assert n.model.solver_model is not None
    before = n.generators_t.p.copy()
    base = fr._private_base(n, threading.RLock())
    assert base is not n
    pd.testing.assert_frame_equal(base.generators_t.p, before)
    pd.testing.assert_frame_equal(n.generators_t.p, before)  # solution kept


def test_the_result_does_not_depend_on_the_grid_order():
    levels, caps = [0.05, 0.4], [None, 150_000.0]
    fwd = _by_key(_run(_three_bus(), levels=levels, co2_caps_t=caps))
    rev = _by_key(_run(_three_bus(), levels=levels[::-1], co2_caps_t=caps[::-1]))
    assert fwd.keys() == rev.keys()
    for k in fwd:
        a, b = fwd[k], rev[k]
        assert a["status"] == b["status"]
        assert a["point"]["total_eur_per_yr"] == pytest.approx(
            b["point"]["total_eur_per_yr"], rel=1e-9
        )
        assert a["point"]["co2_t_per_yr"] == pytest.approx(
            b["point"]["co2_t_per_yr"], rel=1e-9, abs=1e-6
        )
        assert a["point"]["capacities"] == pytest.approx(
            b["point"]["capacities"], rel=1e-6, abs=1e-6
        )
        assert a["mc"]["lole_hours"] == b["mc"]["lole_hours"]
        assert a["on_front"] == b["on_front"]


# --------------------------------------------------------------------------
# Budget (F5)
# --------------------------------------------------------------------------


def test_the_budget_counts_the_reference_solve_and_sits_at_the_boundary():
    assert fr.solves_needed([1, 2, 3, 4], [None, 1, 2, 3]) == 16
    assert fr.solves_needed([1, 2, 3, 4], [1, 2, 3, 4]) == 17
    assert fr.check_budget([1, 2, 3, 4], [None, 1, 2, 3], 16) == 16
    with pytest.raises(
        fr.FrontBudgetError,
        match=r"4 × 4 points \+ 1 uncapped reference = 17 solves exceed the 16-solve budget",
    ):
        fr.check_budget([1, 2, 3, 4], [1, 2, 3, 4], 16)


def test_an_over_budget_grid_is_refused_before_any_solve(monkeypatch):
    from services.adequacy import sweep

    calls = {"n": 0}
    monkeypatch.setattr(
        sweep, "_solve_once", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
    )
    n = _hand()
    with pytest.raises(fr.FrontBudgetError, match="17 solves"):
        _run(
            n,
            lever="ens_cap",
            levels=[10.0, 20.0, 30.0, 40.0],
            co2_caps_t=[1.0, 2.0, 3.0, 4.0],
        )
    assert calls["n"] == 0


def test_a_grid_at_the_budget_runs_every_solve(monkeypatch):
    seen = []

    def fake_point(base, cfg, lock, lq, lever, level, cap):
        seen.append((level, cap))
        return {
            "lever": lever,
            "level": level,
            "co2_cap_t_per_yr": cap,
            "status": "infeasible",
            "point": None,
        }, None

    monkeypatch.setattr(fr, "_solve_point", fake_point)
    res = _run(
        _hand(),
        lever="ens_cap",
        levels=[10.0, 20.0, 30.0, 40.0],
        co2_caps_t=[None, 1.0, 2.0, 3.0],
    )
    assert len(seen) == 16 and res["solves"] == 16 == res["solves_planned"]
    seen.clear()
    res = _run(
        _hand(), lever="ens_cap", levels=[10.0, 20.0, 30.0], co2_caps_t=[1.0, 2.0, 3.0]
    )
    # 9 points + the uncapped reference at the loosest level (the largest ‱)
    assert len(seen) == 10 and res["solves"] == 10 == res["solves_planned"]
    assert seen[-1] == (30.0, None)


@pytest.mark.parametrize(
    "kw, match",
    [
        ({"levels": []}, "at least one"),
        ({"levels": [0.0]}, "positive"),
        ({"levels": [1.0, 1.0]}, "repeat"),
        ({"levels": [1.0], "co2_caps_t": [-1.0]}, "t/yr"),
        ({"levels": [1.0], "co2_caps_t": [5.0, 5.0]}, "repeat"),
        ({"levels": [1.0], "lever": "voll"}, "unknown"),
        ({"levels": [1.0], "mc_draws": 0}, "mc_draws"),
    ],
)
def test_bad_requests_are_refused(kw, match):
    with pytest.raises(fr.FrontConfigError, match=match):
        _run(_hand(), **kw)


def test_the_ens_lever_needs_voll():
    with pytest.raises(fr.FrontConfigError, match="VOLL"):
        _run(_hand(), cfg=_cfg(voll=0.0), lever="ens_cap", levels=[1.0])


# --------------------------------------------------------------------------
# Lever choice from the network (Q1)
# --------------------------------------------------------------------------


def test_the_outage_model_is_read_from_the_network_both_ways():
    n = _hand()
    assert fr.lp_has_outage_model(n) is False
    assert fr.choose_lever(n, None)[0] == "reserve_margin"
    assert fr.choose_lever(n, "auto")[0] == "reserve_margin"
    # a two-state availability series on a unit with outage data is an outage model
    n.generators_t.p_max_pu = pd.DataFrame(
        {"gas": [1.0, 0.0, 1.0, 1.0]}, index=n.snapshots
    )
    assert fr.lp_has_outage_model(n) is True
    lever, why = fr.choose_lever(n, None)
    assert lever == "ens_cap" and "outage series" in why
    assert fr.choose_lever(n, "reserve_margin") == (
        "reserve_margin",
        "chosen by the user",
    )


@pytest.mark.parametrize(
    "series, rate, expected",
    [
        ([1.0, 0.0, 1.0, 1.0], 0.05, True),
        ([0.9, 0.0, 0.9, 0.9], 0.05, True),  # derated unit, still two-state
        ([1.0, 0.0, 1.0, 1.0], 0.0, False),  # no outage data on the unit
        ([0.3, 0.0, 0.8, 1.0], 0.05, False),  # a weather profile
        ([1.0, 1.0, 1.0, 1.0], 0.05, False),  # never out
        ([0.0, 0.0, 0.0, 0.0], 0.05, False),  # never available
    ],
)
def test_only_a_two_state_series_counts_as_an_outage_model(series, rate, expected):
    n = _hand()
    n.generators.loc["gas", "outage_rate_value"] = rate
    n.generators_t.p_max_pu = pd.DataFrame({"gas": series}, index=n.snapshots)
    assert fr.lp_has_outage_model(n) is expected


def test_an_unpriceable_asset_refuses_the_margin_lever_before_any_solve(monkeypatch):
    from services.adequacy import sweep

    calls = {"n": 0}
    monkeypatch.setattr(
        sweep, "_solve_once", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
    )
    n = _hand()
    n.add("Generator", "grid_supply", bus="b", carrier="clean", p_nom=50.0)
    with pytest.raises(
        fr.FrontConfigError, match=r"cannot price 1 asset.*grid_supply.*has no outages"
    ):
        _run(n, levels=[0.1])
    assert calls["n"] == 0


def test_the_ens_lever_runs_where_the_margin_cannot_price_an_asset():
    n = _hand()
    n.add("Generator", "grid_supply", bus="b", carrier="clean", p_nom=50.0)
    res = _run(n, lever="ens_cap", levels=[100.0])
    assert res["points"][0]["status"] == "ok"


def test_no_solved_point_is_said_plainly():
    d = fr.degenerate_findings(
        [
            {"status": "infeasible", "point": None},
            {"status": "time_limit", "point": None},
        ],
        "ens_cap",
        voll=1.0,
        outage_model=False,
        reference_co2=None,
    )
    assert (
        d["text"]
        == "No point solved (infeasible, time_limit), so there is no front to read."
    )
    assert (
        fr.degenerate_findings(
            [], "ens_cap", voll=1.0, outage_model=False, reference_co2=None
        )["text"]
        is None
    )


# --------------------------------------------------------------------------
# Degenerate, said plainly (F6)
# --------------------------------------------------------------------------


def test_an_ens_cap_that_never_binds_says_so_and_names_the_levers():
    res = _run(_hand(), lever="ens_cap", levels=[50.0, 100.0])
    d = res["degenerate"]
    assert set(d["axes"]) == {"ens_cap"}
    text = d["axes"]["ens_cap"]["text"]
    assert "never limited the design at any of 2 points" in text
    assert "no outages" in text and "value of lost load of 5,000 €/MWh" in text
    assert d["axes"]["ens_cap"]["levers"] == [
        "reserve_margin",
        "lower_voll",
        "outage_sampling",
    ]
    assert d["all"] is True
    assert "same design" in d["text"] and "one point" in d["text"]


def test_a_reserve_margin_that_never_binds_says_so():
    res = _run(_three_bus(), levels=[0.05, 0.2])
    d = res["degenerate"]
    assert set(d["axes"]) == {"reserve_margin"}
    assert "tightest margin swept (20%)" in d["axes"]["reserve_margin"]["text"]
    assert d["all"] is True


def test_a_co2_cap_that_never_binds_says_so_with_the_reference():
    res = _run(
        _hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[500_000.0, 600_000.0]
    )
    assert res["solves"] == 3  # 2 points + reference
    co2 = res["degenerate"]["axes"]["co2"]["text"]
    assert "emits 438,000 t/yr" in co2 and "lowest 500,000 t/yr" in co2


def test_a_real_trade_off_is_not_degenerate():
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None, 219_000.0])
    d = res["degenerate"]
    assert "co2" not in d["axes"] and d["all"] is False and d["text"] is None


def _pt(caps, total):
    return {"capacities": caps, "total_eur_per_yr": total}


def test_same_design_compares_every_capacity_and_the_cost():
    a = _pt({"g": 100.0, "s": 5.0}, 1000.0)
    assert fr.same_design(a, _pt({"g": 100.0, "s": 5.0}, 1000.0))
    assert not fr.same_design(a, _pt({"g": 100.0, "s": 5.1}, 1000.0))  # one capacity
    assert not fr.same_design(a, _pt({"g": 100.0}, 1000.0))  # asset set
    assert not fr.same_design(a, _pt({"g": 100.0, "s": 5.0}, 1001.0))  # cost
    assert fr.same_design(a, _pt({"g": 100.0 + 1e-9, "s": 5.0}, 1000.0 + 1e-6))


def test_an_unread_size_or_total_is_never_the_same_design():
    a = _pt({"g": 100.0}, 1000.0)
    assert not fr.same_design(a, _pt({"g": None}, 1000.0))
    assert not fr.same_design(_pt({"g": None}, 1000.0), _pt({"g": None}, 1000.0))
    assert not fr.same_design(a, _pt({"g": 100.0}, None))
    # Mirrored: the unread value on the `a` side (each operand of `close`).
    assert not fr.same_design(_pt({"g": None}, 1000.0), a)
    assert not fr.same_design(_pt({"g": 100.0}, None), a)


def test_a_front_with_an_unread_capacity_still_finishes(monkeypatch):
    real = fr._capacities

    def one_unread(n):
        out = real(n)
        return {**out, "generators:gas": None}

    monkeypatch.setattr(fr, "_capacities", one_unread)
    res = _run(
        _hand(), lever="ens_cap", levels=[100.0, 200.0], co2_caps_t=[None], mc_draws=10
    )
    assert [p["status"] for p in res["points"]] == ["ok", "ok"]
    assert not res.get("aborted")


# --------------------------------------------------------------------------
# Dominance (F7)
# --------------------------------------------------------------------------


def _p(cost, ens, co2, status="ok", eue=None):
    return {
        "status": status,
        "point": None
        if status != "ok"
        else {"total_eur_per_yr": cost, "lp_ens_mwh": ens, "co2_t_per_yr": co2},
        "mc": None if eue is None else {"eue_mwh": eue},
    }


def test_dominated_points_are_marked_not_dropped():
    pts = [
        _p(10, 5, 100),  # 0 front
        _p(12, 5, 100),  # 1 dominated by 0 (cost)
        _p(10, 5, 100),  # 2 tie with 0: neither dominates
        _p(20, 1, 50),  # 3 front
        _p(9, float("nan"), 100),  # 4 not comparable
        _p(0, 0, 0, status="infeasible"),  # 5 failed
        _p(25, 1, 50),  # 6 dominated by 3
    ]
    fr.mark_dominance(pts, "lp_ens")
    assert len(pts) == 7
    assert [p["on_front"] for p in pts] == [True, False, True, True, None, None, False]
    assert pts[1]["dominated_by"] == [0, 2]
    assert pts[6]["dominated_by"] == [3]
    assert (
        pts[0]["dominated_by"] == []
        and pts[4]["dominated_by"] == []
        and pts[5]["dominated_by"] == []
    )


def test_dominance_reads_the_mc_basis_for_the_whole_front():
    pts = [_p(10, 0, 100, eue=5.0), _p(12, 0, 100, eue=1.0)]
    fr.mark_dominance(pts, "lp_ens")
    assert [p["on_front"] for p in pts] == [True, False]  # LP ENS ties, cost decides
    fr.mark_dominance(pts, "mc_eue")
    assert [p["on_front"] for p in pts] == [True, True]  # EUE separates them


def test_one_missing_mc_point_ranks_the_whole_front_on_lp_ens(monkeypatch):
    real = fr._mc_point
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return (None, "MC inputs unavailable") if calls["n"] == 1 else real(*a, **k)

    monkeypatch.setattr(fr, "_mc_point", flaky)
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None, 219_000.0])
    assert res["reliability_basis"] == "lp_ens"
    assert (
        "whole front is ranked on the dispatch model's ENS"
        in res["reliability_basis_note"]
    )
    assert res["points"][0]["mc"] is None
    assert res["points"][0]["mc_error"] == "MC inputs unavailable"
    assert res["points"][1]["mc"] is not None


def test_overlapping_lole_intervals_are_flagged():
    pts = [
        {"status": "ok", "mc": {"lole_ci": [1.0, 2.0]}},
        {"status": "ok", "mc": {"lole_ci": [1.5, 3.0]}},
        {"status": "ok", "mc": {"lole_ci": [5.0, 6.0]}},
    ]
    fr._flag_indistinct(pts, "mc_eue")
    assert [p["reliability_indistinct"] for p in pts] == [True, True, False]
    fr._flag_indistinct(pts, "lp_ens")
    assert not any(p["reliability_indistinct"] for p in pts)


def test_an_aborted_study_keeps_the_points_swept():
    import threading

    ev = threading.Event()
    ev.set()
    res = _run(
        _hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[1.0], stop_event=ev
    )
    assert res["aborted"] is True and res["points"] == [] and res["solves"] == 0


def test_the_front_writes_no_fingerprint_field():
    res = _run(_hand(), lever="ens_cap", levels=[100.0])
    assert not math.isnan(res["points"][0]["point"]["total_eur_per_yr"])
    assert not {"fingerprint", "digest", "network_fingerprint"} & set(res)


# --------------------------------------------------------------------------
# Weightings, discounting and scale
# --------------------------------------------------------------------------


def _two_periods_with_clean(objective) -> pypsa.Network:
    n = _two_periods()
    n.add("Carrier", "clean", co2_emissions=0.0)
    n.add(
        "Generator",
        "c",
        bus="b",
        carrier="clean",
        p_nom_extendable=True,
        p_nom_max=1000.0,
        capital_cost=1000.0,
        marginal_cost=50.0,
        build_year=2030,
        lifetime=50,
    )
    n.investment_period_weightings.loc[:, "objective"] = objective
    return n


def test_a_discounted_multi_period_shadow_reads_in_undiscounted_eur_per_t():
    # Cap 219 000 t/yr = half the gas energy: gas 50 MW, clean 50 MW in each
    # period. One more tonne lets gas make 2 MWh more instead of clean:
    # (50 − 10)·2 = 80 EUR/t, in every period, whatever the discounting.
    res = _run(
        _two_periods_with_clean([1.0, 0.5]),
        lever="ens_cap",
        levels=[100.0],
        co2_caps_t=[219_000.0],
        cfg=_cfg(multi_investment_periods=True),
    )
    pt = res["points"][0]["point"]
    assert pt["co2_binding"] is True
    assert pt["co2_shadow_eur_per_t"] == pytest.approx(80.0, rel=1e-6)
    assert pt["co2_t_per_yr"] == pytest.approx(219_000.0, rel=1e-6)
    assert pt["capex_eur_per_yr"] == pytest.approx(100_000.0, rel=1e-6)
    assert pt["opex_eur_per_yr"] == pytest.approx(
        50 * 10 * 8760 + 50 * 50 * 8760, rel=1e-6
    )


def test_snapshot_weightings_and_a_user_objective_scale_leave_the_hand_point():
    n = _hand()
    n.snapshot_weightings.loc[:, :] = 2.0  # each modelled hour counts twice
    res = _run(
        n,
        lever="ens_cap",
        levels=[100.0],
        co2_caps_t=[219_000.0],
        cfg=_cfg(user_objective_scale=1e-3),
    )
    pt = res["points"][0]["point"]
    assert pt["capex_eur_per_yr"] == pytest.approx(17_500_000.0, rel=1e-6)
    assert pt["opex_eur_per_yr"] == pytest.approx(8_760_000.0, rel=1e-6)
    assert pt["co2_t_per_yr"] == pytest.approx(219_000.0, rel=1e-6)
    assert pt["co2_shadow_eur_per_t"] == pytest.approx(17.0776, abs=1e-3)


# --------------------------------------------------------------------------
# Unknown is not "never bound" (missing report numbers)
# --------------------------------------------------------------------------


def _ok(level, cap, *, rel_b, co2_b):
    return {
        "status": "ok",
        "level": level,
        "co2_cap_t_per_yr": cap,
        "point": {
            "reliability_binding": rel_b,
            "co2_binding": co2_b,
            "capacities": {"g": float(level)},
            "total_eur_per_yr": float(level),
        },
    }


def test_an_unread_binding_is_said_as_unknown_not_never_bound():
    pts = [
        _ok(1, 10.0, rel_b=None, co2_b=None),
        _ok(2, 10.0, rel_b=None, co2_b=None),
        _ok(3, 10.0, rel_b=False, co2_b=False),
    ]
    d = fr.degenerate_findings(
        pts, "ens_cap", voll=5000.0, outage_model=False, reference_co2=None
    )
    assert d["axes"]["ens_cap"]["unknown"] is True
    assert (
        "Whether the ENS target bound could not be read at 2 of 3 points"
        in (d["axes"]["ens_cap"]["text"])
    )
    assert "could not be read at 2 of 3 capped points" in d["axes"]["co2"]["text"]
    rm = fr.degenerate_findings(
        pts, "reserve_margin", voll=0.0, outage_model=False, reference_co2=None
    )
    assert "Whether the reserve margin bound" in rm["axes"]["reserve_margin"]["text"]
    assert "never limited" not in d["axes"]["ens_cap"]["text"]
    assert d["axes"]["co2"]["unknown"] is True
    assert "never reached" not in d["axes"]["co2"]["text"]


def test_a_known_binding_beats_an_unread_one():
    pts = [_ok(1, 10.0, rel_b=None, co2_b=None), _ok(2, 10.0, rel_b=True, co2_b=True)]
    d = fr.degenerate_findings(
        pts, "reserve_margin", voll=0.0, outage_model=False, reference_co2=None
    )
    assert d["axes"] == {}


def test_missing_report_metrics_leave_lp_ens_unknown(monkeypatch):
    from services.adequacy import sweep

    real = sweep._solve_once

    def no_metrics(cfg, net, lock, lq, sink):
        real(cfg, net, lock, lq, sink)
        sink["adequacy_report"] = {**sink["adequacy_report"], "metrics": {}}

    monkeypatch.setattr(sweep, "_solve_once", no_metrics)
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None], mc_draws=10)
    p = res["points"][0]
    assert p["point"]["lp_ens_mwh"] is None
    assert fr._objectives(p, "lp_ens") is None


# --------------------------------------------------------------------------
# Tolerance boundaries
# --------------------------------------------------------------------------


def test_close_and_same_design_sit_on_their_tolerances():
    assert fr._close(0.0, 0.9e-6, fr._ABS_TOL_MW)
    assert not fr._close(0.0, 1.1e-6, fr._ABS_TOL_MW)
    assert fr._close(1e9, 1e9 * (1 + 0.9e-6), 0.0)
    assert not fr._close(1e9, 1e9 * (1 + 1.1e-6), 0.0)
    a = _pt({"g": 0.0}, 0.0)
    assert fr.same_design(a, _pt({"g": 0.9e-6}, 0.0009))
    assert not fr.same_design(a, _pt({"g": 1.1e-6}, 0.0))
    assert not fr.same_design(a, _pt({"g": 0.0}, 0.0011))


def test_dominance_ignores_differences_inside_the_tolerance():
    assert not fr._dominates((0.0, 0.0, 0.0), (0.0009, 0.0, 0.0))
    assert fr._dominates((0.0, 0.0, 0.0), (0.0011, 0.0, 0.0))
    assert not fr._dominates((0.0011, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert not fr._dominates((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


# --------------------------------------------------------------------------
# MC branches: pack boundary and zonal inputs (mocked)
# --------------------------------------------------------------------------


def _mc_result(lole):
    return {
        "lole_hours": lole,
        "lole_ci": [lole, lole],
        "eue_mwh": 2 * lole,
        "eue_ci": None,
        "n_samples": 7,
        "resolution_floor_h": 0.5,
        "time_basis": "modelled",
    }


def test_the_mc_takes_the_pack_boundary_and_the_zonal_engine(monkeypatch):
    import types

    from services.adequacy import archetypes, eh_stages, mc, mc_zonal

    seen = {}

    def hub_boundary_copy(work, pack):
        seen["pack"] = pack
        return None, "BOUNDARY"

    def freeze(work, cfg, lock, overlay=None, boundary=None):
        seen["freeze"] = (overlay, boundary)
        return types.SimpleNamespace(mc_inputs="MC", zonal_inputs="Z", mc_error=None)

    def zonal(inputs, **kw):
        seen["zonal"] = (inputs, kw)
        return _mc_result(0.25)

    def single(*a, **kw):
        raise AssertionError("zonal inputs must use the zonal engine")

    monkeypatch.setattr(archetypes, "hub_boundary_copy", hub_boundary_copy)
    monkeypatch.setattr(eh_stages, "freeze_fixed_plan", freeze)
    monkeypatch.setattr(mc_zonal, "zonal_mc_adequacy", zonal)
    monkeypatch.setattr(mc, "mc_adequacy", single)
    pack = types.SimpleNamespace(import_overlay="OVERLAY")
    out, err = fr._mc_point("WORK", _cfg(), None, pack, 40, 3)
    assert err is None
    assert seen["pack"] is pack and seen["freeze"] == ("OVERLAY", "BOUNDARY")
    assert seen["zonal"] == (
        "Z",
        {"draws": 40, "max_draws": 40, "batch": 40, "seed": 3, "cov_target": 0.05},
    )
    assert out["cov_target"] == 0.05
    assert out["lole_hours"] == 0.25 and out["below_floor"] is True
    assert out["eue_ci"] is None and out["lole_ci"] == [0.25, 0.25]


def test_the_mc_without_a_pack_uses_the_single_area_engine(monkeypatch):
    import types

    from services.adequacy import eh_stages, mc

    def freeze(work, cfg, lock, **kw):
        assert kw == {}
        return types.SimpleNamespace(mc_inputs="MC", zonal_inputs=None, mc_error=None)

    monkeypatch.setattr(eh_stages, "freeze_fixed_plan", freeze)
    seen = {}

    def single(inputs, **kw):
        seen["single"] = (inputs, kw)
        return _mc_result(0.5)

    monkeypatch.setattr(mc, "mc_adequacy", single)
    out, err = fr._mc_point("WORK", _cfg(), None, None, 40, 3)
    assert err is None and out["below_floor"] is False
    assert seen["single"] == (
        "MC",
        {"draws": 40, "max_draws": 40, "batch": 40, "seed": 3, "cov_target": 0.05},
    )
    assert out["cov_target"] == 0.05


def test_an_mc_that_cannot_run_is_an_error_not_a_zero(monkeypatch):
    import types

    from services.adequacy import eh_stages

    monkeypatch.setattr(
        eh_stages,
        "freeze_fixed_plan",
        lambda *a, **k: types.SimpleNamespace(
            mc_inputs=None, zonal_inputs=None, mc_error="no outage data"
        ),
    )
    assert fr._mc_point("WORK", _cfg(), None, None, 40, 3) == (None, "no outage data")


# --------------------------------------------------------------------------
# GK A (36) asks on #1979
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word",
    ["time_limit", "iteration_limit", "suboptimal", "imprecise", "terminated_by_limit"],
)
def test_a_solve_stopped_on_a_limit_is_not_a_front_point(monkeypatch, word):
    from services.adequacy import sweep

    real = sweep._solve_once

    def limited(cfg, net, lock, lq, sink):
        real(cfg, net, lock, lq, sink)
        sink.update(_status="ok", _condition=word)  # linopy: status ok

    monkeypatch.setattr(sweep, "_solve_once", limited)
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    p = res["points"][0]
    assert p["status"] == word and p["condition"] == word
    assert p["point"] is None and p["mc"] is None
    assert p["on_front"] is None and p["dominated_by"] == []


def test_an_optimal_point_records_its_condition():
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    assert res["points"][0]["condition"].startswith("optimal")


def test_an_unreadable_cost_reads_none_and_the_front_serialises_strictly(monkeypatch):
    import json

    monkeypatch.setattr(fr, "_lp_capex_share", lambda n: None)
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None, 219_000.0])
    pt = res["points"][0]["point"]
    assert pt["capex_eur_per_yr"] is None and pt["opex_eur_per_yr"] is None
    # uncapped: gas 100 MW, 100·50 000 + 20·100·8760
    assert pt["total_eur_per_yr"] == pytest.approx(22_520_000.0, rel=1e-6)
    json.dumps(res, allow_nan=False)


def test_a_non_finite_mc_result_fails_that_point_only(monkeypatch):
    import types

    from services.adequacy import eh_stages, mc

    monkeypatch.setattr(
        eh_stages,
        "freeze_fixed_plan",
        lambda *a, **k: types.SimpleNamespace(
            mc_inputs="MC", zonal_inputs=None, mc_error=None
        ),
    )
    monkeypatch.setattr(
        mc,
        "mc_adequacy",
        lambda inputs, **kw: {**_mc_result(0.5), "eue_mwh": float("nan")},
    )
    out, err = fr._mc_point("WORK", _cfg(), None, None, 40, 3)
    assert out is None and "MC result unreadable" in err
    monkeypatch.setattr(mc, "mc_adequacy", lambda inputs, **kw: {"lole_hours": 1.0})
    out, err = fr._mc_point("WORK", _cfg(), None, None, 40, 3)
    assert out is None and "MC result unreadable" in err


@pytest.mark.parametrize(
    "kw, field",
    [
        ({"mc_draws": float("nan")}, "mc_draws"),
        ({"mc_draws": float("inf")}, "mc_draws"),
        ({"mc_draws": 0}, "mc_draws"),
        ({"mc_draws": 2001}, "mc_draws"),
        ({"mc_draws": 1.5}, "mc_draws"),
        ({"budget_solves": float("inf")}, "budget_solves"),
        ({"budget_solves": 17}, "budget_solves"),
        ({"budget_solves": 0}, "budget_solves"),
    ],
)
def test_out_of_range_arguments_are_refused_by_name(monkeypatch, kw, field):
    monkeypatch.setattr(fr, "_solve_point", lambda *a: pytest.fail("solved"))
    with pytest.raises(fr.FrontConfigError, match=field):
        _run(_hand(), lever="ens_cap", levels=[100.0], **kw)


def test_the_argument_bounds_are_inclusive(monkeypatch):
    stub = lambda base, cfg, lock, lq, lever, level, cap: (  # noqa: E731
        {
            "lever": lever,
            "level": level,
            "co2_cap_t_per_yr": cap,
            "status": "infeasible",
            "point": None,
        },
        None,
    )
    monkeypatch.setattr(fr, "_solve_point", stub)
    res = _run(
        _hand(), lever="ens_cap", levels=[100.0], mc_draws=2000, budget_solves=16
    )
    assert res["mc_budget"]["draws_per_point"] == 2000
    res = _run(_hand(), lever="ens_cap", levels=[100.0], mc_draws=1, budget_solves=1)
    assert res["solves"] == 1


@pytest.mark.parametrize(
    "lever, level, ok",
    [
        ("reserve_margin", 15.0, False),
        ("reserve_margin", 5.0, True),
        ("ens_cap", 10_001.0, False),
        ("ens_cap", 10_000.0, True),
    ],
)
def test_a_level_beyond_its_range_is_refused(lever, level, ok):
    if ok:
        assert fr._clean_levels(lever, [level]) == [level]
    else:
        with pytest.raises(fr.FrontConfigError, match="reliability level"):
            fr._clean_levels(lever, [level])


def _co2_by_level(monkeypatch, seen):
    def fake_point(base, cfg, lock, lq, lever, level, cap):
        seen.append((level, cap))
        return {
            "lever": lever,
            "level": level,
            "co2_cap_t_per_yr": cap,
            "status": "ok",
            "point": {
                "total_eur_per_yr": 1.0,
                "lp_ens_mwh": 0.0,
                "co2_t_per_yr": 1000.0 * level,
                "capacities": {"g": level},
                "reliability_binding": True,
                "co2_binding": True,
            },
        }, None

    monkeypatch.setattr(fr, "_solve_point", fake_point)
    monkeypatch.setattr(fr, "_mc_point", lambda *a, **k: (None, "off"))


@pytest.mark.parametrize("lever, loosest", [("ens_cap", 30.0), ("reserve_margin", 0.1)])
def test_the_reference_is_the_loosest_level_uncapped(monkeypatch, lever, loosest):
    seen = []
    _co2_by_level(monkeypatch, seen)
    monkeypatch.setattr(fr, "_margin_preflight", lambda *a: None)
    levels = [10.0, 30.0, 20.0] if lever == "ens_cap" else [0.2, 0.1, 0.3]
    # an uncapped column: the loosest level's uncapped point
    res = _run(_hand(), lever=lever, levels=levels, co2_caps_t=[None, 5.0])
    assert res["reference_co2_t_per_yr"] == 1000.0 * loosest
    # no uncapped column: one extra solve at the loosest level, uncapped
    seen.clear()
    res = _run(_hand(), lever=lever, levels=levels, co2_caps_t=[5.0])
    assert seen[-1] == (loosest, None)
    assert res["reference_co2_t_per_yr"] == 1000.0 * loosest


def test_one_solved_point_is_not_called_the_same_design():
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    assert res["degenerate"]["all"] is False and res["degenerate"]["text"] is None


def test_the_ens_text_with_an_outage_model_does_not_blame_missing_outages():
    pts = [_ok(1, None, rel_b=False, co2_b=None), _ok(2, None, rel_b=False, co2_b=None)]
    text = fr.degenerate_findings(
        pts, "ens_cap", voll=5000.0, outage_model=True, reference_co2=None
    )["axes"]["ens_cap"]["text"]
    assert "no outages" not in text
    assert (
        "never limited the design at any of 2 points: at a value of lost load of 5,000 €/MWh"
        in text
    )


def test_with_no_solved_point_the_basis_is_lp_ens(monkeypatch):
    monkeypatch.setattr(
        fr,
        "_solve_point",
        lambda base, cfg, lock, lq, lever, level, cap: (
            {
                "lever": lever,
                "level": level,
                "co2_cap_t_per_yr": cap,
                "status": "infeasible",
                "point": None,
            },
            None,
        ),
    )
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    assert res["reliability_basis"] == "lp_ens"


def _capped_two_periods():
    n = _two_periods()
    n.investment_period_weightings.loc[:, "objective"] = [10.0, 10.0]  # factor 1
    names = fr.apply_co2_cap(n, 219_000.0)
    return n, names


def test_the_shadow_is_the_dearest_period_and_an_unread_mu_is_unknown():
    n, names = _capped_two_periods()
    n.global_constraints["mu"] = [-10.0, -40.0]
    _t, shadow, binding = fr._co2(n, names)
    assert binding is True and shadow == pytest.approx(40.0)
    n.global_constraints["mu"] = [-10.0, float("nan")]
    _t, shadow, binding = fr._co2(n, names)
    assert binding is None and shadow is None


def test_a_non_finite_margin_input_is_refused_before_any_solve(monkeypatch):
    import types

    from services import validation_service

    monkeypatch.setattr(
        validation_service,
        "_check_reserve_margin",
        lambda n, cfg: [
            types.SimpleNamespace(code="nonfinite_p_nom", message="g has p_nom inf.")
        ],
    )
    monkeypatch.setattr(fr, "_solve_point", lambda *a: pytest.fail("solved"))
    with pytest.raises(
        fr.FrontConfigError, match=r"g has p_nom inf\..*reserve-margin lever"
    ):
        _run(_three_bus(), lever="reserve_margin", levels=[0.1])


# --------------------------------------------------------------------------
# Killing cases for the mutation survivors at e8854735
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values, expected",
    [
        ([float("nan"), 0.0, 1.0], True),  # NaN hours are skipped, not fatal
        ([float("nan"), float("nan")], False),  # nothing readable
        (["a", "b"], False),  # not numbers
    ],
)
def test_the_two_state_reader_skips_unreadable_hours(values, expected):
    assert fr._outage_series(pd.Series(values, dtype=object), True) is expected


def test_a_network_with_no_generator_has_no_outage_model():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    assert fr.lp_has_outage_model(n) is False


@pytest.mark.parametrize(
    "lever, levels, match",
    [
        ("ens_cap", ["abc"], "reliability level abc is not a number"),
        ("ens_cap", [float("nan")], "‱ of demand"),
        ("reserve_margin", [float("nan")], "margin fraction"),
    ],
)
def test_an_unreadable_level_is_refused_in_its_lever_unit(lever, levels, match):
    with pytest.raises(fr.FrontConfigError, match=match):
        fr._clean_levels(lever, levels)


def test_no_levels_is_refused_by_name():
    with pytest.raises(fr.FrontConfigError, match="at least one reliability level"):
        fr._clean_levels("ens_cap", None)


def test_co2_caps_are_read_strictly_and_zero_is_a_cap():
    assert fr._clean_caps([0.0, None]) == [0.0, None]
    with pytest.raises(fr.FrontConfigError, match="CO2 cap abc is not a number"):
        fr._clean_caps(["abc"])
    with pytest.raises(fr.FrontConfigError, match="CO2 cap nan"):
        fr._clean_caps([float("nan")])


def test_an_unreadable_argument_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(fr, "_solve_point", lambda *a: pytest.fail("solved"))
    with pytest.raises(fr.FrontConfigError, match="mc_draws many is not a number"):
        _run(_hand(), lever="ens_cap", levels=[100.0], mc_draws="many")


def test_each_period_cap_takes_its_own_years():
    n = _two_periods()
    n.investment_period_weightings.loc[:, "years"] = [10.0, 5.0]
    fr.apply_co2_cap(n, 1000.0)
    c = n.global_constraints["constant"]
    assert c["co2_cap_2030"] == pytest.approx(2 * c["co2_cap_2040"])


def test_existing_capacity_of_an_extendable_is_not_charged_as_capex():
    # 20 MW of the gas plant already exist: PyPSA charges only the 80 MW
    # built on top (its objective_constant takes the 20 MW back out).
    n = _hand()
    n.generators.loc["gas", "p_nom"] = 20.0
    n.generators.loc["gas", "p_nom_min"] = 20.0
    res = _run(n, lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    pt = res["points"][0]["point"]
    assert pt["capacities"]["generators:gas"] == pytest.approx(100.0, abs=1e-3)
    assert pt["capex_eur_per_yr"] == pytest.approx(80 * 50_000.0, rel=1e-6)
    assert pt["opex_eur_per_yr"] == pytest.approx(20 * 100 * 8760.0, rel=1e-6)


def test_a_zero_cost_plan_has_no_capex_share():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", p_nom=100.0)  # fixed and free
    res = _run(n, lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    p = res["points"][0]
    assert p["status"] == "ok"
    assert p["point"]["capex_eur_per_yr"] is None
    assert p["point"]["total_eur_per_yr"] == pytest.approx(0.0, abs=1e-6)


def _fake_net(objective):
    return types.SimpleNamespace(objective=objective)


def test_annual_costs_read_none_on_every_unreadable_input(monkeypatch):
    monkeypatch.setattr(fr, "_objective_years", lambda n: 1.0)
    monkeypatch.setattr(fr, "_lp_capex_share", lambda n: 0.5)
    rep = {"cost": {"total_system_cost_eur": 10.0}}
    assert fr._annual_costs(_fake_net(4.0), rep) == (2.0, 8.0, 10.0)
    nan_total = {"cost": {"total_system_cost_eur": float("nan")}}
    assert fr._annual_costs(_fake_net(4.0), nan_total) == (None, None, None)
    assert fr._annual_costs(_fake_net(float("inf")), rep) == (None, None, 10.0)
    assert fr._annual_costs(_fake_net(4.0), {"cost": None}) == (None, None, None)
    assert fr._annual_costs(_fake_net(4.0), None) == (None, None, None)
    monkeypatch.setattr(fr, "_objective_years", lambda n: 1e-300)
    monkeypatch.setattr(fr, "_lp_capex_share", lambda n: 1.0)
    huge = {"cost": {"total_system_cost_eur": 1.0}}
    assert fr._annual_costs(_fake_net(1e300), huge) == (
        None,
        None,
        pytest.approx(1e300),
    )


def test_a_period_shadow_factor_with_no_objective_weight_is_one():
    n = _two_periods()
    fr.apply_co2_cap(n, 1000.0)
    gc = n.global_constraints
    n.investment_period_weightings.loc[:, "objective"] = [0.0, 1.0]
    n.investment_period_weightings.loc[:, "years"] = [10.0, float("inf")]
    assert fr._period_shadow_factor(n, gc, "co2_cap_2030") == 1.0
    assert fr._period_shadow_factor(n, gc, "co2_cap_2040") == 1.0


@pytest.mark.parametrize(
    "lever, rep",
    [
        ("ens_cap", None),
        ("ens_cap", {"target": None}),
        ("reserve_margin", {"reserve_margin": None}),
        ("reserve_margin", {"reserve_margin": {"by_period": None}}),
    ],
)
def test_a_missing_report_section_reads_binding_unknown(lever, rep):
    assert fr._reliability_binding(lever, rep) is None


def _sink_patch(monkeypatch, **sink):
    from services.adequacy import sweep

    real = sweep._solve_once

    def patched(cfg, net, lock, lq, s):
        real(cfg, net, lock, lq, s)
        s.update(sink)

    monkeypatch.setattr(sweep, "_solve_once", patched)


def test_a_missing_condition_falls_back_to_the_status(monkeypatch):
    _sink_patch(monkeypatch, _condition=None, _status="optimal")
    p = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["status"] == "ok" and p["condition"] == "optimal"


def test_no_word_at_all_is_a_failed_point(monkeypatch):
    _sink_patch(monkeypatch, _condition=None, _status=None)
    p = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["status"] == "failed" and p["point"] is None


def test_a_point_names_its_lever_and_its_cost_basis():
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    p = res["points"][0]
    assert p["lever"] == "ens_cap"
    assert p["point"]["excludes_shed_cost"] is True
    assert p["point"]["lp_ens_mwh"] == pytest.approx(0.0, abs=1e-6)


def test_report_metrics_set_to_none_leave_lp_ens_unknown(monkeypatch):
    from services.adequacy import sweep

    real = sweep._solve_once

    def none_metrics(cfg, net, lock, lq, s):
        real(cfg, net, lock, lq, s)
        s["adequacy_report"] = {**s["adequacy_report"], "metrics": None}

    monkeypatch.setattr(sweep, "_solve_once", none_metrics)
    p = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["point"]["lp_ens_mwh"] is None


def _frozen(**kw):
    return types.SimpleNamespace(
        **{"mc_inputs": "MC", "zonal_inputs": None, "mc_error": None, **kw}
    )


def test_mc_inputs_missing_with_no_reason_say_so(monkeypatch):
    from services.adequacy import eh_stages

    monkeypatch.setattr(
        eh_stages, "freeze_fixed_plan", lambda *a, **k: _frozen(mc_inputs=None)
    )
    assert fr._mc_point("W", _cfg(), None, None, 4, 0) == (
        None,
        "MC inputs unavailable",
    )


@pytest.mark.parametrize(
    "patch, ok",
    [
        ({"lole_hours": float("nan")}, False),
        ({"resolution_floor_h": None}, True),
    ],
)
def test_the_mc_record_reads_each_field(monkeypatch, patch, ok):
    from services.adequacy import eh_stages, mc

    monkeypatch.setattr(eh_stages, "freeze_fixed_plan", lambda *a, **k: _frozen())
    monkeypatch.setattr(
        mc, "mc_adequacy", lambda i, **kw: {**_mc_result(0.25), **patch}
    )
    out, err = fr._mc_point("W", _cfg(), None, None, 4, 0)
    if ok:
        assert err is None and out["below_floor"] is False
    else:
        assert out is None and "unreadable" in err


def test_numbers_of_ten_print_whole():
    assert fr._fmt(10.0) == "10"
    assert fr._fmt(9.5) == "9.5"


def test_an_unnamed_exception_is_named_by_its_type(monkeypatch):
    from services.adequacy import sweep

    def bare(cfg, net, lock, lq, s):
        raise RuntimeError()

    monkeypatch.setattr(sweep, "_solve_once", bare)
    p = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["status"] == "error" and p["error"] == "RuntimeError"


def _stub_points(monkeypatch):
    monkeypatch.setattr(
        fr,
        "_solve_point",
        lambda base, cfg, lock, lq, lever, level, cap: (
            {
                "lever": lever,
                "level": level,
                "co2_cap_t_per_yr": cap,
                "status": "infeasible",
                "point": None,
            },
            None,
        ),
    )


def test_voll_is_required_by_the_ens_lever_only(monkeypatch):
    _stub_points(monkeypatch)
    monkeypatch.setattr(fr, "_margin_preflight", lambda *a: None)
    res = _run(_three_bus(), lever="reserve_margin", levels=[0.1], cfg=_cfg(voll=0.0))
    assert res["solves"] == 1
    res = _run(_three_bus(), lever="reserve_margin", levels=[0.1], cfg=_cfg(voll=None))
    assert res["solves"] == 1
    with pytest.raises(fr.FrontConfigError, match="VOLL"):
        _run(_hand(), lever="ens_cap", levels=[100.0], cfg=_cfg(voll=None))


def test_the_callers_log_queue_gets_the_solver_log():
    lq = queue.SimpleQueue()
    _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None], log_queue=lq)
    assert not lq.empty()


def test_an_unset_stop_event_does_not_stop_the_study(monkeypatch):
    _stub_points(monkeypatch)
    res = _run(
        _hand(),
        lever="ens_cap",
        levels=[10.0, 20.0],
        co2_caps_t=[5.0],
        stop_event=threading.Event(),
    )
    assert res["aborted"] is False and res["solves"] == 3


def test_the_uncapped_reference_ignores_a_capped_point_at_the_same_level(monkeypatch):
    def fake_point(base, cfg, lock, lq, lever, level, cap):
        co2 = cap if cap is not None else 999.0
        return {
            "lever": lever,
            "level": level,
            "co2_cap_t_per_yr": cap,
            "status": "ok",
            "point": {
                "total_eur_per_yr": 1.0,
                "lp_ens_mwh": 0.0,
                "co2_t_per_yr": co2,
                "capacities": {"g": 1.0},
                "reliability_binding": True,
                "co2_binding": True,
            },
        }, None

    monkeypatch.setattr(fr, "_solve_point", fake_point)
    monkeypatch.setattr(fr, "_mc_point", lambda *a, **k: (None, "off"))
    res = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[5.0, None])
    assert res["reference_co2_t_per_yr"] == 999.0


@pytest.mark.parametrize(
    "a, b, overlap",
    [
        ([1.0, 2.0], [2.0, 3.0], True),  # touching ends overlap
        ([1.0, 2.0], [5.0, 6.0], False),  # disjoint, either order
        ([5.0, 6.0], [1.0, 2.0], False),
        ([1.0, 4.0], [2.0, 3.0], True),  # nested
    ],
)
def test_lole_interval_overlap_is_exact(a, b, overlap):
    pts = [
        {"status": "ok", "mc": {"lole_ci": a}},
        {"status": "ok", "mc": {"lole_ci": b}},
    ]
    fr._flag_indistinct(pts, "mc_eue")
    assert [p["reliability_indistinct"] for p in pts] == [overlap, overlap]


# --------------------------------------------------------------------------
# Mutation survivors: the guards that read None, never crash
# --------------------------------------------------------------------------


def test_an_optimal_solve_with_no_adequacy_report_reads_none(monkeypatch):
    from services.adequacy import sweep

    real = sweep._solve_once

    def no_report(cfg, net, lock, lq, sink):
        real(cfg, net, lock, lq, sink)
        sink.pop("adequacy_report", None)

    monkeypatch.setattr(sweep, "_solve_once", no_report)
    p = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["status"] == "ok", p
    pt = p["point"]
    assert pt["capex_eur_per_yr"] is None and pt["total_eur_per_yr"] is None
    assert pt["opex_eur_per_yr"] is None and pt["lp_ens_mwh"] is None
    assert pt["reliability_binding"] is None


def test_zero_objective_weightings_read_no_cost_and_still_solve():
    n = _hand()
    n.snapshot_weightings["objective"] = 0.0
    p = _run(n, lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["status"] == "ok" and p["condition"].startswith("optimal"), p
    pt = p["point"]
    assert pt["total_eur_per_yr"] is None
    assert pt["capex_eur_per_yr"] is None and pt["opex_eur_per_yr"] is None


def test_zero_generator_weightings_read_no_co2_and_still_solve():
    n = _hand()
    n.snapshot_weightings["generators"] = 0.0
    p = _run(n, lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["status"] == "ok" and p["condition"].startswith("optimal"), p
    assert p["point"]["co2_t_per_yr"] is None


def test_an_infinite_emissions_total_reads_no_co2(monkeypatch):
    from services.results import emissions

    real = emissions.compute_emissions

    def infinite(*a, **kw):
        return {**real(*a, **kw), "total_tCO2": float("inf")}

    monkeypatch.setattr(emissions, "compute_emissions", infinite)
    p = _run(_hand(), lever="ens_cap", levels=[100.0], co2_caps_t=[None])["points"][0]
    assert p["status"] == "ok", p
    assert p["point"]["co2_t_per_yr"] is None


def test_an_mc_result_with_nan_lole_and_no_floor_is_unreadable(monkeypatch):
    from services.adequacy import eh_stages, mc

    monkeypatch.setattr(eh_stages, "freeze_fixed_plan", lambda *a, **k: _frozen())
    monkeypatch.setattr(
        mc,
        "mc_adequacy",
        lambda i, **kw: {"lole_hours": float("nan"), "eue_mwh": 1.0, "n_samples": 5},
    )
    out, err = fr._mc_point("W", _cfg(), None, None, 4, 0)
    assert out is None and err.startswith("MC result unreadable")


def test_a_non_finite_confidence_bound_reads_no_interval():
    rec = fr._mc_record({**_mc_result(1.0), "lole_ci": [1.0, float("inf")]}, 0.1)
    assert rec["lole_ci"] is None
    assert rec["lole_hours"] == 1.0


def test_an_ac_power_flow_setting_does_not_fail_the_front():
    cfg = _cfg(run_ac_pf_after_lopf=True)
    assert fr._cfg_for(cfg, "ens_cap", 100.0).run_ac_pf_after_lopf is False
    assert fr._cfg_for(cfg, "reserve_margin", 0.1).run_ac_pf_after_lopf is False
    res = _run(_hand(), cfg=cfg, lever="ens_cap", levels=[100.0], co2_caps_t=[None])
    p = res["points"][0]
    assert p["status"] == "ok" and p["condition"] == "optimal", p


def test_the_budget_never_rises_above_the_maximum():
    levels = [1, 2, 3, 4, 5]
    with pytest.raises(fr.FrontBudgetError, match="20 solves exceed the 16-solve"):
        fr.check_budget(levels, [None, 1, 2, 3], 10**6)


@pytest.mark.parametrize("v", [10**400, True, False], ids=["huge", "true", "false"])
def test_an_integer_argument_refuses_overflow_and_bools(v):
    with pytest.raises(fr.FrontConfigError, match="mc_draws .* is not a number"):
        fr._int_arg("mc_draws", v, 0, 10)
