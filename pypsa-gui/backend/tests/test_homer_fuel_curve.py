"""homer-parity 02a: a generator's fuel curve (no-load intercept + slope) in the dispatch and the cost breakdown."""
from __future__ import annotations

import math

import pandas as pd
import pypsa
import pytest

from services import fuel_curve
from services.results.cost_breakdown import compute_cost_breakdown
from services.solver_service import SolverConfig

HOURS = 24
CFG = SolverConfig()
LOAD_MW = 0.3
SLOPE, NO_LOAD, PRICE = 0.25, 0.05, 40.0


def _network(*, curve: bool, committable: bool = True) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.add("Bus", "el", carrier="AC")
    n.add("Load", "site", bus="el", p_set=LOAD_MW)
    kw = dict(bus="el", carrier="diesel", p_nom=1.0, committable=committable, p_min_pu=0.2 if committable else 0.0)
    if curve:
        kw.update({fuel_curve.SLOPE: SLOPE, fuel_curve.NO_LOAD: NO_LOAD if committable else 0.0,
                   fuel_curve.PRICE: PRICE})
    else:
        kw["marginal_cost"] = SLOPE * PRICE
    n.add("Generator", "genset", **kw)
    fuel_curve.apply(n)
    return n


def _solve(n):
    status, _ = n.optimize(solver_name="highs")
    assert status == "ok"
    return n


def _row(payload, name="genset"):
    (row,) = [r for r in payload["fuel"] if r["generator"] == name]
    return row


def test_no_load_burns_more_at_low_load_than_the_linear_model():
    n = _solve(_network(curve=True))
    row = _row(compute_cost_breakdown(n, CFG))
    hand = HOURS * (NO_LOAD * 1.0 + SLOPE * LOAD_MW)          # 0.125 MWh/h → 3.0 MWh
    linear = HOURS * SLOPE * LOAD_MW                          # 0.075 MWh/h → 1.8 MWh
    assert row["fuel_mwh"] == pytest.approx(hand)
    assert row["no_load_fuel_mwh"] == pytest.approx(HOURS * NO_LOAD)
    assert row["fuel_mwh"] > linear + 1.0
    assert row["fuel_cost_eur"] == pytest.approx(hand * PRICE)
    assert row["no_load_cost_eur"] == pytest.approx(HOURS * NO_LOAD * PRICE)


def test_cost_breakdown_opex_and_objective_carry_the_fuel_cost():
    n = _solve(_network(curve=True))
    cb = compute_cost_breakdown(n, CFG)
    hand = HOURS * (NO_LOAD + SLOPE * LOAD_MW) * PRICE        # 120 EUR
    assert cb["opex"] == pytest.approx(hand)
    assert n.objective == pytest.approx(hand)


def test_no_curve_leaves_costs_and_breakdown_unchanged():
    n = _network(curve=False)
    before = n.generators[["marginal_cost"]].copy()
    assert fuel_curve.apply(n) == []
    pd.testing.assert_frame_equal(n.generators[["marginal_cost"]], before)
    _solve(n)
    cb = compute_cost_breakdown(n, CFG)
    assert cb["fuel"] == []
    assert cb["opex"] == pytest.approx(HOURS * LOAD_MW * SLOPE * PRICE)


def test_slope_only_curve_matches_the_linear_marginal_cost():
    n = _solve(_network(curve=True, committable=False))
    row = _row(compute_cost_breakdown(n, CFG))
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * PRICE)
    assert row["no_load_fuel_mwh"] == 0.0
    assert row["fuel_mwh"] == pytest.approx(HOURS * SLOPE * LOAD_MW)


def test_fuel_use_follows_status_and_snapshot_weighting():
    n = _network(curve=True)
    n.snapshot_weightings.loc[:, :] = 2.0
    _solve(n)
    n.generators_t.status.iloc[:6, 0] = 0.0      # off for 6 h: no no-load burn there
    n.generators_t.p.iloc[:6, 0] = 0.0
    row = _row(compute_cost_breakdown(n, CFG))
    on_h = HOURS - 6
    assert row["no_load_fuel_mwh"] == pytest.approx(2.0 * on_h * NO_LOAD)
    assert row["fuel_mwh"] == pytest.approx(2.0 * on_h * (NO_LOAD + SLOPE * LOAD_MW))


def test_apply_rederives_after_a_p_nom_edit():
    n = _network(curve=True)
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(NO_LOAD * 1.0 * PRICE)
    n.generators.at["genset", "p_nom"] = 3.0
    assert fuel_curve.apply(n, ["genset", "missing"]) == ["genset"]
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(NO_LOAD * 3.0 * PRICE)
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * PRICE)


@pytest.mark.parametrize("row, msg", [
    ({fuel_curve.SLOPE: -0.1, fuel_curve.PRICE: 40.0}, "zero or more"),
    ({fuel_curve.PRICE: -1.0}, "zero or more"),
    ({fuel_curve.SLOPE: 0.25}, "fuel price"),
    ({fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0}, "Unit commitment"),
    ({fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0, "committable": True, "p_nom_extendable": True},
     "extendable"),
    ({fuel_curve.SLOPE: "abc", fuel_curve.PRICE: 40.0}, "finite number"),
    ({fuel_curve.SLOPE: math.inf, fuel_curve.PRICE: 40.0}, "finite number"),
])
def test_check_refuses(row, msg):
    assert msg in (fuel_curve.check(row) or "")


@pytest.mark.parametrize("row", [
    {},
    {fuel_curve.SLOPE: None, fuel_curve.NO_LOAD: math.nan, fuel_curve.PRICE: None},
    {fuel_curve.SLOPE: 0.25, fuel_curve.PRICE: 40.0},
    {fuel_curve.PRICE: 40.0},
    {fuel_curve.SLOPE: 0.0, fuel_curve.NO_LOAD: 0.0, fuel_curve.PRICE: 0.0},
    {fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0, "committable": True, "p_nom_extendable": False},
])
def test_check_accepts(row):
    assert fuel_curve.check(row) is None


def test_derived_costs():
    assert fuel_curve.derived_costs({}) == {}
    assert fuel_curve.derived_costs({fuel_curve.SLOPE: 0.25, fuel_curve.NO_LOAD: 0.05,
                                     fuel_curve.PRICE: 40.0, "p_nom": 2.0}) == pytest.approx(
        {"marginal_cost": 10.0, "stand_by_cost": 4.0})


# ── the asset write path (network_crud) and the pre-run check ─────────────────────────────────────────────────

CURVE = {fuel_curve.SLOPE: SLOPE, fuel_curve.NO_LOAD: NO_LOAD, fuel_curve.PRICE: PRICE}


def _genset(**kw):
    return {"name": "genset", "bus": "B1", "carrier": "diesel", "p_nom": 2.0, "committable": True, **kw}


def test_post_derives_the_costs_from_the_curve(client, install_network):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json=_genset(**CURVE))
    assert r.status_code == 201, r.text
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * PRICE)
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(NO_LOAD * 2.0 * PRICE)
    r = client.put("/api/network/generators/genset", json=_genset(p_nom=5.0))
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(NO_LOAD * 5.0 * PRICE)


def test_post_without_a_curve_writes_no_curve_column(client, install_network):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json=_genset(marginal_cost=7.0))
    assert r.status_code == 201, r.text
    assert not set(fuel_curve.COLUMNS) & set(n.generators.columns)
    assert n.generators.at["genset", "marginal_cost"] == 7.0


@pytest.mark.parametrize("body", [_genset(**CURVE, committable=False), _genset(**{fuel_curve.SLOPE: 0.25})])
def test_write_refuses_a_curve_it_cannot_price(client, install_network, body):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json=body)
    assert r.status_code == 422
    assert "genset" not in n.generators.index
    client.post("/api/network/generators", json=_genset())
    r = client.put("/api/network/generators/genset", json=body)
    assert r.status_code == 422
    assert not set(fuel_curve.COLUMNS) & set(n.generators.columns)


def test_validation_flags_a_curve_it_cannot_price():
    from services.validation_service import _check_fuel_curves

    n = _network(curve=True)
    assert _check_fuel_curves(n) == []
    n.generators.at["genset", "committable"] = False
    (issue,) = _check_fuel_curves(n)
    assert (issue.severity, issue.code, issue.name) == ("error", "fuel_curve_invalid", "genset")
    assert "Unit commitment" in issue.message
    assert _check_fuel_curves(_network(curve=False)) == []


def test_the_generator_form_shows_the_curve_for_fuel_carriers_only():
    from services.asset_schema import fields as F

    rows = {f.key: f for f in F.fields_for("Generator")}
    for key in fuel_curve.COLUMNS:
        assert rows[key].reveal_when == {"carrier": F.FUEL_CARRIERS}, key
        assert rows[key].blank == "clear", key
    assert {"diesel", "gas", "oil"} <= set(F.FUEL_CARRIERS)
    assert not {"solar", "onwind", "offwind", "ror"} & set(F.FUEL_CARRIERS)
    assert "Unit commitment" in rows[fuel_curve.NO_LOAD].help
    assert rows["stand_by_cost"].derived and rows["stand_by_cost"].reveal_when == {"committable": True}
    assert "minimum load ratio" in rows["p_min_pu"].help
