"""homer-parity 02b: a generator's O&M per running hour (in the LP and the cost breakdown) and its lifetime in
running hours (the finance case's replacement timing)."""

from __future__ import annotations

import math

import pandas as pd
import pypsa
import pytest

from services import fuel_curve
from services.finance import replacements
from services.results.cost_breakdown import compute_cost_breakdown
from services.results.finance_case import _assets
from services.solver_service import SolverConfig

HOURS = 24
CFG = SolverConfig()
OM = 3.0
OM_HOUR, LIFE_HOURS = fuel_curve.OM_HOUR, fuel_curve.LIFE_HOURS


def _network(*, om: float | None = OM, life_h: float | None = None, committable: bool = True,
             load=0.3, weight: float = 1.0) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = weight
    n.add("Bus", "el", carrier="AC")
    n.add("Load", "site", bus="el", p_set=load)
    kw = dict(bus="el", carrier="diesel", p_nom=1.0, committable=committable,
              p_min_pu=0.2 if committable else 0.0, marginal_cost=10.0, lifetime=20.0,
              overnight_cost=0.0, discount_rate=0.07)
    if om is not None:
        kw[OM_HOUR] = om
    if life_h is not None:
        kw[LIFE_HOURS] = life_h
    n.add("Generator", "genset", **kw)
    fuel_curve.apply(n)
    return n


def _solve(n):
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    return n


def _block(payload, name="genset"):
    (row,) = [r for r in payload["run_hours"] if r["generator"] == name]
    return row


def _opex_of(n, name="genset"):
    op = n.statistics.opex(groupby=False, comps="Generator")
    if isinstance(op, pd.DataFrame):            # multi-period: one column per period
        op = op.sum(axis=1)
    return float(op.xs(name, level="name").sum()) if isinstance(op.index, pd.MultiIndex) else float(op[name])


# ── derivation and refusals ─────────────────────────────────────────────────

def test_om_alone_derives_only_the_stand_by_cost():
    assert fuel_curve.derived_costs({OM_HOUR: 3.0, "committable": True, "marginal_cost": 7.0}) == {
        "stand_by_cost": 3.0}


def test_om_adds_to_the_no_load_fuel_cost():
    row = {fuel_curve.SLOPE: 0.25, fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0, OM_HOUR: 3.0,
           "p_nom": 2.0, "committable": True}
    got = fuel_curve.derived_costs(row)
    assert got["marginal_cost"] == pytest.approx(10.0)
    assert got["stand_by_cost"] == pytest.approx(0.05 * 2.0 * 40.0 + 3.0)


def test_a_lifetime_alone_derives_nothing():
    assert fuel_curve.derived_costs({LIFE_HOURS: 10_000.0}) == {}


@pytest.mark.parametrize(
    "row, msg",
    [
        ({OM_HOUR: -1.0, "committable": True}, "O&M per running hour: enter zero or more"),
        ({OM_HOUR: float("inf"), "committable": True}, "O&M per running hour: enter a finite number"),
        ({OM_HOUR: 3.0, "committable": False}, "O&M per running hour: turn on Unit commitment"),
        ({LIFE_HOURS: 0.0}, "Lifetime in running hours: enter more than zero"),
        ({LIFE_HOURS: -5.0}, "Lifetime in running hours: enter more than zero"),
        ({LIFE_HOURS: "many"}, "Lifetime in running hours: enter a finite number"),
    ],
)
def test_check_refuses(row, msg):
    assert (fuel_curve.check(row) or "").startswith(msg)


@pytest.mark.parametrize(
    "row",
    [
        {OM_HOUR: 0.0, "committable": False},
        {OM_HOUR: 3.0, "committable": True, "p_nom_extendable": True},
        {LIFE_HOURS: 10_000.0, "committable": False},
    ],
)
def test_check_accepts(row):
    assert fuel_curve.check(row) is None


def test_a_direct_stand_by_edit_refuses_while_om_derives_it():
    before = {OM_HOUR: 3.0, "committable": True, "stand_by_cost": 3.0}
    msg = fuel_curve.guard({**before, "stand_by_cost": 9.0}, before=before, submitted={"stand_by_cost": 9.0})
    assert msg and msg.startswith("Stand-by cost: set by")


def test_marginal_cost_stays_editable_beside_om_alone():
    before = {OM_HOUR: 3.0, "committable": True, "stand_by_cost": 3.0, "marginal_cost": 10.0}
    after = {**before, "marginal_cost": 12.0}
    assert fuel_curve.guard(after, before=before, submitted={"marginal_cost": 12.0}) is None


def test_om_beside_a_stand_by_series_refuses_but_a_marginal_series_does_not():
    row = {OM_HOUR: 3.0, "committable": True}
    assert (fuel_curve.guard(row, cost_series=["stand_by_cost"]) or "").startswith("Stand-by cost: remove")
    assert fuel_curve.guard(row, cost_series=["marginal_cost"]) is None


def test_lp_refusals_name_om_without_unit_commitment():
    n = _network(committable=False, om=None)
    n.generators.loc["genset", OM_HOUR] = 3.0
    (msg,) = fuel_curve.lp_refusals(n)
    assert msg.startswith("genset: O&M per running hour: turn on Unit commitment")


def test_apply_writes_om_into_stand_by_and_leaves_marginal_cost():
    n = _network()
    assert n.generators.at["genset", "stand_by_cost"] == OM
    assert n.generators.at["genset", "marginal_cost"] == 10.0


# ── the solve and the cost breakdown ────────────────────────────────────────

def test_om_cost_is_hours_run_times_the_rate():
    n = _solve(_network(weight=2.0))
    on = float(n.generators_t.status["genset"].sum())
    assert on == HOURS
    row = _block(compute_cost_breakdown(n, CFG))
    assert row["hours_run"] == pytest.approx(on * 2.0)
    assert row["om_cost_eur"] == pytest.approx(on * 2.0 * OM)
    energy = float((n.generators_t.p["genset"] * 2.0).sum())
    assert _opex_of(n) == pytest.approx(row["om_cost_eur"] + 10.0 * energy)


def test_om_counts_only_committed_hours():
    load = pd.Series([0.3] * 12 + [0.0] * 12, index=pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n = _network(load=load)
    _solve(n)
    on = float(n.generators_t.status["genset"].sum())
    assert 0 < on < HOURS
    assert _block(compute_cost_breakdown(n, CFG))["om_cost_eur"] == pytest.approx(on * OM)


def test_om_moves_the_dispatch_to_the_unit_without_it():
    def run(om):
        n = _network(om=om)
        n.add("Generator", "backup", bus="el", carrier="diesel", p_nom=1.0, committable=True,
              p_min_pu=0.2, marginal_cost=12.0)
        _solve(n)
        return float(n.generators_t.status["genset"].sum())

    assert run(None) == HOURS
    assert run(100.0) == 0


def test_an_unsolved_generator_reads_none():
    n = _network(life_h=10_000.0)
    row = _block(compute_cost_breakdown(n, CFG))
    assert row["hours_run"] is None and row["om_cost_eur"] is None


def test_lifetime_alone_counts_hours_on_a_non_committable_generator():
    load = pd.Series([0.3] * 6 + [0.0] * 18, index=pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n = _solve(_network(om=None, life_h=10_000.0, committable=False, load=load))
    row = _block(compute_cost_breakdown(n, CFG))
    assert row["hours_run"] == pytest.approx(6.0)
    assert row["om_cost_eur"] == 0.0


def test_no_input_leaves_the_breakdown_unchanged():
    n = _solve(_network(om=None))
    assert OM_HOUR not in n.generators.columns
    assert compute_cost_breakdown(n, CFG)["run_hours"] == []


def test_multi_period_hours_scale_by_the_period_years():
    n = pypsa.Network()
    snaps = pd.date_range("2030-01-01", periods=4, freq="h")
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], snaps]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = [10.0, 5.0]
    n.add("Bus", "el", carrier="AC")
    n.add("Load", "site", bus="el", p_set=0.3)
    n.add("Generator", "genset", bus="el", carrier="diesel", p_nom=1.0, committable=True,
          p_min_pu=0.2, marginal_cost=10.0, lifetime=40.0, build_year=2030, **{OM_HOUR: OM})
    fuel_curve.apply(n)
    status, _ = n.optimize(solver_name="highs", multi_investment_periods=True)
    assert status == "ok"
    row = _block(compute_cost_breakdown(n, CFG))
    assert row["hours_run"] == pytest.approx(4 * 10.0 + 4 * 5.0)
    assert row["om_cost_eur"] == pytest.approx(OM * (4 * 10.0 + 4 * 5.0))
    # PyPSA's per-period opex (one year of each period) × the period's years carries the same O&M.
    op = n.statistics.opex(groupby=False, comps="Generator").loc["genset"]
    years = n.investment_period_weightings["years"]
    energy = n.generators_t.p["genset"].groupby(level=0).sum()
    assert float((op * years).sum()) == pytest.approx(row["om_cost_eur"] + 10.0 * float((energy * years).sum()))


# ── lifetime in running hours → the finance case ────────────────────────────

def _finance(n):
    (a,), _rates, flags = _assets(n, [("Generator", "genset")])
    return a, flags


def test_run_hour_lifetime_shortens_the_part_life():
    # 24 snapshots of 2000/24 h each, on every hour: 2000 run hours a year; 10 000 h → 5 years.
    n = _solve(_network(life_h=10_000.0, weight=2000.0 / HOURS))
    a, flags = _finance(n)
    assert a.lifetime_years == pytest.approx(5.0)
    assert [p.lifetime_years for p in a.parts] == [pytest.approx(5.0)]
    assert "run_hour_lifetime:genset" in flags
    assert replacements.replacement_year(2030, 1, a.parts[0].lifetime_years) == 2034


def test_a_calendar_lifetime_shorter_than_the_run_hour_one_wins():
    n = _solve(_network(life_h=1_000_000.0, weight=2000.0 / HOURS))
    a, flags = _finance(n)
    assert a.lifetime_years == 20.0
    assert [p.lifetime_years for p in a.parts] == [20.0]
    assert "run_hour_lifetime:genset" not in flags


def test_a_unit_that_never_runs_keeps_its_calendar_life():
    n = _solve(_network(life_h=100.0, load=0.0))
    a, flags = _finance(n)
    assert a.lifetime_years == 20.0
    assert not [f for f in flags if f.startswith("run_hour_lifetime")]


def test_an_unsolved_run_hour_lifetime_is_flagged_unknown():
    a, flags = _finance(_network(life_h=10_000.0))
    assert a.lifetime_years == 20.0
    assert "run_hour_lifetime_unknown:genset" in flags


def test_no_run_hour_lifetime_leaves_the_assets_unchanged():
    n = _solve(_network(om=None, weight=2000.0 / HOURS))
    a, flags = _finance(n)
    assert a.lifetime_years == 20.0 and flags == []


def test_multi_period_lifetime_reads_the_horizon_average_rate():
    n = pypsa.Network()
    snaps = pd.date_range("2030-01-01", periods=4, freq="h")
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], snaps]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = [10.0, 10.0]
    n.snapshot_weightings.loc[:, :] = 500.0
    load = pd.Series([0.3] * 4 + [0.3, 0.3, 0.0, 0.0], index=n.snapshots)
    n.add("Bus", "el", carrier="AC")
    n.add("Load", "site", bus="el", p_set=load)
    n.add("Generator", "genset", bus="el", carrier="diesel", p_nom=1.0, committable=True,
          p_min_pu=0.2, marginal_cost=10.0, lifetime=40.0, build_year=2030, overnight_cost=0.0, discount_rate=0.07,
          **{LIFE_HOURS: 4_500.0})
    status, _ = n.optimize(solver_name="highs", multi_investment_periods=True)
    assert status == "ok"
    a, flags = _finance(n)
    # 2000 h/yr in 2030, 1000 h/yr in 2040 → (20 000 + 10 000) / 20 years = 1500 h/yr → 3 years.
    assert a.lifetime_years == pytest.approx(3.0)
    assert {"run_hour_lifetime:genset", "run_hour_lifetime_averaged:genset"} <= set(flags)
    assert math.isfinite(a.parts[0].lifetime_years)


# ── the writers: one guard, one derivation, the notices ─────────────────────

def _installed(install_network):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    return n


def _genset(**kw):
    return {"name": "genset", "bus": "B1", "carrier": "diesel", "p_nom": 2.0, "committable": True, **kw}


def test_post_derives_stand_by_from_om_and_put_refuses_a_direct_edit(client, install_network):
    n = _installed(install_network)
    r = client.post("/api/network/generators", json=_genset(marginal_cost=12.0, **{OM_HOUR: OM}))
    assert r.status_code == 201, r.text
    assert n.generators.at["genset", "stand_by_cost"] == OM
    assert n.generators.at["genset", "marginal_cost"] == 12.0
    r = client.put("/api/network/generators/genset", json=_genset(stand_by_cost=9.0))
    assert r.status_code == 422 and "Stand-by cost: set by the O&M per running hour" in r.text
    r = client.put("/api/network/generators/genset", json=_genset(marginal_cost=14.0))
    assert r.status_code == 200, r.text


def test_post_refuses_om_without_unit_commitment(client, install_network):
    _installed(install_network)
    r = client.post("/api/network/generators", json=_genset(committable=False, **{OM_HOUR: OM}))
    assert r.status_code == 422 and "O&M per running hour: turn on Unit commitment" in r.text


def test_post_without_run_hour_inputs_writes_no_column(client, install_network):
    n = _installed(install_network)
    assert client.post("/api/network/generators", json=_genset()).status_code == 201
    assert OM_HOUR not in n.generators.columns and LIFE_HOURS not in n.generators.columns


def test_om_over_a_stand_by_cost_says_what_it_replaces_and_clearing_resets_it(client, install_network):
    n = _installed(install_network)
    assert client.post("/api/network/generators", json=_genset(stand_by_cost=5.0)).status_code == 201
    r = client.put("/api/network/generators/genset", json=_genset(**{OM_HOUR: OM}))
    assert r.status_code == 200, r.text
    assert r.json()["notice"] == "The O&M per running hour replaces the stand-by cost of 5 €/h with 3 €/h."
    r = client.put("/api/network/generators/genset", json=_genset(**{OM_HOUR: None}))
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == 0.0
    assert r.json()["notice"] == "O&M per running hour cleared: stand-by cost reset to 0 €/h."


def test_clearing_the_curve_keeps_the_om_in_stand_by():
    curve = {fuel_curve.SLOPE: 0.25, fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0}
    before = {**curve, OM_HOUR: OM, "p_nom": 2.0, "committable": True, "marginal_cost": 10.0}
    after = {**before, **{k: None for k in fuel_curve.COLUMNS}}
    assert fuel_curve.cleared(after, before=before) == {}
    assert fuel_curve.clear_notice(after, before=before) == (
        "Fuel curve cleared: stand-by cost is now the O&M per running hour, 3 €/h; marginal cost keeps 10 €/MWh "
        "(was derived from the curve).")
    assert fuel_curve.derived_costs(after) == {"stand_by_cost": OM}


def test_adding_om_beside_a_curve_names_nothing_new():
    curve = {fuel_curve.SLOPE: 0.25, fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0, "p_nom": 2.0,
             "committable": True, "stand_by_cost": 4.0}
    assert fuel_curve.replace_notice({**curve, OM_HOUR: OM}, before=curve) is None


def test_validation_flags_om_without_unit_commitment():
    from services.validation_service import _check_fuel_curves

    n = _network(committable=False, om=None)
    n.generators.loc["genset", OM_HOUR] = OM
    (issue,) = _check_fuel_curves(n)
    assert issue.message.startswith("O&M per running hour: turn on Unit commitment")


def test_the_run_hour_inputs_enter_the_fingerprint_only_when_present(session_ctx, client, install_network):
    from services.workflow_tools import _network_input_fingerprint

    n = _installed(install_network)
    ctx = session_ctx(client)

    def fp():
        return _network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True)

    before = fp()
    n.generators[LIFE_HOURS] = float("nan")
    n.generators.at[n.generators.index[0], LIFE_HOURS] = 10_000.0
    moved = fp()
    assert moved[0] != before[0] and moved[1] != before[1]
    n.generators.drop(columns=LIFE_HOURS, inplace=True)
    assert fp() == before
