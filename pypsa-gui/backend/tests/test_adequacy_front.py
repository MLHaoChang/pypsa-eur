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
import threading

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

    def flaky(cfg, net, lock, lq, sink):
        calls["n"] += 1
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


def test_an_interrupt_is_not_swallowed_as_a_failed_point(monkeypatch):
    from services.adequacy import sweep

    def interrupted(cfg, net, lock, lq, sink):
        raise KeyboardInterrupt

    monkeypatch.setattr(sweep, "_solve_once", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _run(_hand(), levels=[100.0], co2_caps_t=[None])


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
    assert "never bound at any of 2 points" in text
    assert "no outages" in text and "VOLL 5,000 EUR/MWh" in text
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
    pts = [_ok(1, 10.0, rel_b=None, co2_b=None), _ok(2, 10.0, rel_b=False, co2_b=False)]
    d = fr.degenerate_findings(
        pts, "ens_cap", voll=5000.0, outage_model=False, reference_co2=None
    )
    assert d["axes"]["ens_cap"]["unknown"] is True
    assert "could not be read at 1 of 2 points" in d["axes"]["ens_cap"]["text"]
    assert "never bound" not in d["axes"]["ens_cap"]["text"]
    assert d["axes"]["co2"]["unknown"] is True
    assert "never bound" not in d["axes"]["co2"]["text"]


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
        {"draws": 40, "max_draws": 40, "batch": 40, "seed": 3},
    )
    assert out["lole_hours"] == 0.25 and out["below_floor"] is True
    assert out["eue_ci"] is None and out["lole_ci"] == [0.25, 0.25]


def test_the_mc_without_a_pack_uses_the_single_area_engine(monkeypatch):
    import types

    from services.adequacy import eh_stages, mc

    def freeze(work, cfg, lock, **kw):
        assert kw == {}
        return types.SimpleNamespace(mc_inputs="MC", zonal_inputs=None, mc_error=None)

    monkeypatch.setattr(eh_stages, "freeze_fixed_plan", freeze)
    monkeypatch.setattr(mc, "mc_adequacy", lambda inputs, **kw: _mc_result(0.5))
    out, err = fr._mc_point("WORK", _cfg(), None, None, 40, 3)
    assert err is None and out["below_floor"] is False


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
