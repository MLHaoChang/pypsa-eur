"""homer-parity 02b: a generator's O&M per running hour (in the LP and the cost breakdown) and its lifetime in
running hours (the finance case's replacement timing)."""

from __future__ import annotations

import dataclasses
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


def _network(
    *,
    om: float | None = OM,
    life_h: float | None = None,
    committable: bool = True,
    load=0.3,
    weight: float = 1.0,
) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = weight
    n.add("Bus", "el", carrier="AC")
    n.add("Load", "site", bus="el", p_set=load)
    kw = dict(
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        committable=committable,
        p_min_pu=0.2 if committable else 0.0,
        marginal_cost=10.0,
        lifetime=20.0,
        overnight_cost=0.0,
        discount_rate=0.07,
    )
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
    if isinstance(op, pd.DataFrame):  # multi-period: one column per period
        op = op.sum(axis=1)
    return (
        float(op.xs(name, level="name").sum())
        if isinstance(op.index, pd.MultiIndex)
        else float(op[name])
    )


# ── derivation and refusals ─────────────────────────────────────────────────


def test_om_alone_derives_only_the_stand_by_cost():
    assert fuel_curve.derived_costs(
        {OM_HOUR: 3.0, "committable": True, "marginal_cost": 7.0}
    ) == {"stand_by_cost": 3.0}


def test_om_adds_to_the_no_load_fuel_cost():
    row = {
        fuel_curve.SLOPE: 0.25,
        fuel_curve.NO_LOAD: 0.05,
        fuel_curve.PRICE: 40.0,
        OM_HOUR: 3.0,
        "p_nom": 2.0,
        "committable": True,
    }
    got = fuel_curve.derived_costs(row)
    assert got["marginal_cost"] == pytest.approx(10.0)
    assert got["stand_by_cost"] == pytest.approx(0.05 * 2.0 * 40.0 + 3.0)


def test_a_lifetime_alone_derives_nothing():
    assert fuel_curve.derived_costs({LIFE_HOURS: 10_000.0}) == {}


@pytest.mark.parametrize(
    "row, msg",
    [
        (
            {OM_HOUR: -1.0, "committable": True},
            "O&M per running hour: enter zero or more",
        ),
        (
            {OM_HOUR: float("inf"), "committable": True},
            "O&M per running hour: enter a finite number",
        ),
        (
            {OM_HOUR: 3.0, "committable": False},
            "O&M per running hour: turn on Unit commitment",
        ),
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
    msg = fuel_curve.guard(
        {**before, "stand_by_cost": 9.0},
        before=before,
        submitted={"stand_by_cost": 9.0},
    )
    assert msg and msg.startswith("Stand-by cost: set by")


def test_marginal_cost_stays_editable_beside_om_alone():
    before = {
        OM_HOUR: 3.0,
        "committable": True,
        "stand_by_cost": 3.0,
        "marginal_cost": 10.0,
    }
    after = {**before, "marginal_cost": 12.0}
    assert (
        fuel_curve.guard(after, before=before, submitted={"marginal_cost": 12.0})
        is None
    )


def test_om_beside_a_stand_by_series_refuses_but_a_marginal_series_does_not():
    row = {OM_HOUR: 3.0, "committable": True}
    assert (fuel_curve.guard(row, cost_series=["stand_by_cost"]) or "").startswith(
        "Stand-by cost: remove"
    )
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
    load = pd.Series(
        [0.3] * 12 + [0.0] * 12,
        index=pd.date_range("2030-01-01", periods=HOURS, freq="h"),
    )
    n = _network(load=load)
    _solve(n)
    on = float(n.generators_t.status["genset"].sum())
    assert 0 < on < HOURS
    assert _block(compute_cost_breakdown(n, CFG))["om_cost_eur"] == pytest.approx(
        on * OM
    )


def test_om_moves_the_dispatch_to_the_unit_without_it():
    def run(om):
        n = _network(om=om)
        n.add(
            "Generator",
            "backup",
            bus="el",
            carrier="diesel",
            p_nom=1.0,
            committable=True,
            p_min_pu=0.2,
            marginal_cost=12.0,
        )
        _solve(n)
        return float(n.generators_t.status["genset"].sum())

    assert run(None) == HOURS
    assert run(100.0) == 0


def test_an_unsolved_generator_reads_none():
    n = _network(life_h=10_000.0)
    row = _block(compute_cost_breakdown(n, CFG))
    assert row["hours_run"] is None and row["om_cost_eur"] is None


def test_lifetime_alone_counts_hours_on_a_non_committable_generator():
    load = pd.Series(
        [0.3] * 6 + [0.0] * 18,
        index=pd.date_range("2030-01-01", periods=HOURS, freq="h"),
    )
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
    n.add(
        "Generator",
        "genset",
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        committable=True,
        p_min_pu=0.2,
        marginal_cost=10.0,
        lifetime=40.0,
        build_year=2030,
        **{OM_HOUR: OM},
    )
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
    assert float((op * years).sum()) == pytest.approx(
        row["om_cost_eur"] + 10.0 * float((energy * years).sum())
    )


# ── lifetime in running hours → the finance case ────────────────────────────


def _finance(n):
    (a,), _rates, flags = _assets(n, [("Generator", "genset")])
    return a, flags


def _sixth(load=0.3):
    """On 6 of 24 hourly snapshots, each weighted 365 h: a whole year sampled, 2190 run hours a year."""
    return pd.Series(
        [load] * 6 + [0.0] * 18,
        index=pd.date_range("2030-01-01", periods=HOURS, freq="h"),
    )


def test_run_hour_lifetime_shortens_the_part_life():
    n = _solve(_network(life_h=10_950.0, weight=365.0, load=_sixth()))
    a, flags = _finance(n)
    assert a.lifetime_years == pytest.approx(5.0)
    assert [p.lifetime_years for p in a.parts] == [pytest.approx(5.0)]
    assert flags == ["run_hour_lifetime:genset"]
    assert replacements.replacement_year(2030, 1, a.parts[0].lifetime_years) == 2034


def test_the_rate_is_per_modelled_year_not_per_8760_snapshots():
    # GK B H3: 4380 snapshots at weight 2 = one year; on in half of them → 4380 run hours a year.
    idx = pd.date_range("2030-01-01", periods=4380, freq="2h")
    n = _network(life_h=8_760.0, weight=2.0, load=0.0)
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 2.0
    n.generators_t.status = pd.DataFrame({"genset": [1.0, 0.0] * 2190}, index=idx)
    years, flags = fuel_curve.run_hour_lifetime(n, "genset")
    assert years == pytest.approx(2.0) and flags == []
    # A day sampled at weight 1, on throughout: 8760 h a year, not 24.
    n = _network(life_h=8_760.0)
    n.generators_t.status = pd.DataFrame({"genset": [1.0] * HOURS}, index=n.snapshots)
    assert fuel_curve.run_hour_lifetime(n, "genset")[0] == pytest.approx(1.0)


def test_a_calendar_lifetime_shorter_than_the_run_hour_one_wins():
    n = _solve(_network(life_h=1_000_000.0, weight=365.0, load=_sixth()))
    a, flags = _finance(n)
    assert a.lifetime_years == 20.0
    assert [p.lifetime_years for p in a.parts] == [20.0]
    assert flags == []


def test_a_unit_that_never_runs_keeps_its_calendar_life():
    n = _solve(_network(life_h=100.0, load=0.0))
    assert float(n.generators_t.status["genset"].sum()) == 0
    a, flags = _finance(n)
    assert a.lifetime_years == 20.0 and flags == []


def test_an_unsolved_run_hour_lifetime_is_flagged_unknown():
    a, flags = _finance(_network(life_h=10_000.0))
    assert a.lifetime_years == 20.0
    assert flags == ["run_hour_lifetime_unknown:genset"]


def test_a_generator_added_after_the_solve_is_unknown_not_inferred():
    n = _solve(_network(om=None))
    n.add(
        "Generator",
        "late",
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        committable=True,
        lifetime=20.0,
        **{LIFE_HOURS: 100.0},
    )
    assert fuel_curve.run_hour_lifetime(n, "late") == (
        None,
        ["run_hour_lifetime_unknown:late"],
    )
    n.generators.loc["late", "committable"] = False
    assert fuel_curve.run_hour_lifetime(n, "late") == (
        None,
        ["run_hour_lifetime_unknown:late"],
    )


def test_a_tiny_rate_never_yields_an_infinite_lifetime(monkeypatch):
    import services.adequacy.metrics as M

    n = _solve(_network(life_h=1e300))
    monkeypatch.setattr(M, "horizon_years", lambda *_a, **_k: 1e300)
    assert fuel_curve.run_hour_lifetime(n, "genset") == (None, [])
    a, _flags = _finance(n)
    assert a.lifetime_years == 20.0


def test_no_run_hour_lifetime_leaves_the_assets_unchanged():
    n = _solve(_network(om=None, weight=365.0, load=_sixth()))
    a, flags = _finance(n)
    assert a.lifetime_years == 20.0 and flags == []


def test_only_a_single_part_generator_wears():
    from services.finance.case import AssetPart
    from services.results.finance_case import _run_hour_life

    n = _solve(_network(life_h=10_950.0, weight=365.0, load=_sixth()))
    one = (AssetPart("investment", 100.0, 20.0),)
    worn, life, flags = _run_hour_life(n, "genset", one, 20.0)
    assert [p.lifetime_years for p in worn] == [
        pytest.approx(5.0)
    ] and life == pytest.approx(5.0)
    two = (AssetPart("engine", 60.0, 20.0), AssetPart("civil", 40.0, 40.0))
    assert _run_hour_life(n, "genset", two, 40.0) == (
        two,
        40.0,
        ["run_hour_lifetime_multi_part:genset"],
    )


def test_an_infinite_calendar_lifetime_takes_the_run_hour_one():
    from services.finance.case import AssetPart
    from services.results.finance_case import _run_hour_life

    n = _solve(_network(life_h=10_950.0, weight=365.0, load=_sixth()))
    worn, life, flags = _run_hour_life(
        n, "genset", (AssetPart("investment", 1.0, math.inf),), None
    )
    assert worn[0].lifetime_years == pytest.approx(5.0) and life == pytest.approx(5.0)
    assert flags == ["run_hour_lifetime:genset"]


@pytest.mark.parametrize("above, counted", [(0.0, 0.0), (1e-9, 1.0)])
def test_a_non_committable_unit_counts_hours_above_a_millionth_of_capacity(
    above, counted
):
    # GK B H6: p > 1e-6 × p_nom (p_nom_opt when extendable), flagged as a dispatch count.
    n = _network(om=None, life_h=8_760.0, committable=False)
    n.generators.loc["genset", "p_nom"] = 2.0
    n.generators_t.p = pd.DataFrame(
        {"genset": [2e-6 + above] + [0.0] * (HOURS - 1)}, index=n.snapshots
    )
    if not counted:
        assert fuel_curve.run_hour_lifetime(n, "genset") == (None, [])
        return
    years, flags = fuel_curve.run_hour_lifetime(n, "genset")
    assert years == pytest.approx(HOURS) and flags == [
        "run_hour_lifetime_from_dispatch:genset"
    ]
    n.generators.loc["genset", ["p_nom_extendable", "p_nom_opt"]] = [True, 4.0]
    assert fuel_curve.run_hour_lifetime(n, "genset") == (None, [])


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
    n.add(
        "Generator",
        "genset",
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        committable=True,
        p_min_pu=0.2,
        marginal_cost=10.0,
        lifetime=40.0,
        build_year=2030,
        overnight_cost=0.0,
        discount_rate=0.07,
        **{LIFE_HOURS: 19_710.0},
    )
    status, _ = n.optimize(solver_name="highs", multi_investment_periods=True)
    assert status == "ok"
    a, flags = _finance(n)
    # 30 000 run hours over 40 000 / 8760 modelled years → 6570 h a year → 3 years.
    assert a.lifetime_years == pytest.approx(3.0)
    assert flags == ["run_hour_lifetime:genset", "run_hour_lifetime_averaged:genset"]
    assert math.isfinite(a.parts[0].lifetime_years)


# ── the writers: one guard, one derivation, the notices ─────────────────────


def _installed(install_network):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    return n


def _genset(**kw):
    return {
        "name": "genset",
        "bus": "B1",
        "carrier": "diesel",
        "p_nom": 2.0,
        "committable": True,
        **kw,
    }


def test_post_derives_stand_by_from_om_and_put_refuses_a_direct_edit(
    client, install_network
):
    n = _installed(install_network)
    r = client.post(
        "/api/network/generators", json=_genset(marginal_cost=12.0, **{OM_HOUR: OM})
    )
    assert r.status_code == 201, r.text
    assert n.generators.at["genset", "stand_by_cost"] == OM
    assert n.generators.at["genset", "marginal_cost"] == 12.0
    r = client.put("/api/network/generators/genset", json=_genset(stand_by_cost=9.0))
    assert (
        r.status_code == 422
        and "Stand-by cost: set by the O&M per running hour" in r.text
    )
    r = client.put("/api/network/generators/genset", json=_genset(marginal_cost=14.0))
    assert r.status_code == 200, r.text


def test_post_refuses_om_without_unit_commitment(client, install_network):
    _installed(install_network)
    r = client.post(
        "/api/network/generators", json=_genset(committable=False, **{OM_HOUR: OM})
    )
    assert (
        r.status_code == 422
        and "O&M per running hour: turn on Unit commitment" in r.text
    )


def test_post_without_run_hour_inputs_writes_no_column(client, install_network):
    n = _installed(install_network)
    assert client.post("/api/network/generators", json=_genset()).status_code == 201
    assert (
        OM_HOUR not in n.generators.columns and LIFE_HOURS not in n.generators.columns
    )


def test_om_over_a_stand_by_cost_says_what_it_replaces_and_clearing_resets_it(
    client, install_network
):
    n = _installed(install_network)
    assert (
        client.post(
            "/api/network/generators", json=_genset(stand_by_cost=5.0)
        ).status_code
        == 201
    )
    r = client.put("/api/network/generators/genset", json=_genset(**{OM_HOUR: OM}))
    assert r.status_code == 200, r.text
    assert (
        r.json()["notice"]
        == "The O&M per running hour replaces the stand-by cost of 5 €/h with 3 €/h."
    )
    r = client.put("/api/network/generators/genset", json=_genset(**{OM_HOUR: None}))
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == 0.0
    assert (
        r.json()["notice"]
        == "O&M per running hour cleared: stand-by cost reset to 0 €/h."
    )


def test_clearing_the_curve_keeps_the_om_in_stand_by():
    curve = {fuel_curve.SLOPE: 0.25, fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0}
    before = {
        **curve,
        OM_HOUR: OM,
        "p_nom": 2.0,
        "committable": True,
        "marginal_cost": 10.0,
    }
    after = {**before, **{k: None for k in fuel_curve.COLUMNS}}
    assert fuel_curve.cleared(after, before=before) == {}
    assert fuel_curve.clear_notice(after, before=before) == (
        "Fuel curve cleared: stand-by cost is now the O&M per running hour, 3 €/h; marginal cost keeps 10 €/MWh "
        "(was derived from the curve)."
    )
    assert fuel_curve.derived_costs(after) == {"stand_by_cost": OM}


def test_adding_om_beside_a_curve_names_nothing_new():
    curve = {
        fuel_curve.SLOPE: 0.25,
        fuel_curve.NO_LOAD: 0.05,
        fuel_curve.PRICE: 40.0,
        "p_nom": 2.0,
        "committable": True,
        "stand_by_cost": 4.0,
    }
    assert fuel_curve.replace_notice({**curve, OM_HOUR: OM}, before=curve) is None


def test_validation_flags_om_without_unit_commitment():
    from services.validation_service import _check_fuel_curves

    n = _network(committable=False, om=None)
    n.generators.loc["genset", OM_HOUR] = OM
    (issue,) = _check_fuel_curves(n)
    assert issue.message.startswith("O&M per running hour: turn on Unit commitment")


def test_om_enters_the_solve_fingerprint_and_the_lifetime_only_the_case_digest(
    session_ctx, client, install_network
):
    from services.results.finance_case import owner_assets_digest
    from services.workflow_tools import _network_input_fingerprint

    n = _installed(install_network)
    ctx = session_ctx(client)
    g = n.generators.index[0]
    commercial = {
        "value_flows": {"asset_owners": [{"component": "Generator", "asset_id": g}]}
    }

    def fp():
        return _network_input_fingerprint(ctx), _network_input_fingerprint(
            ctx, exact=True
        )

    before, ic_before = fp(), owner_assets_digest(n, commercial)
    n.generators[LIFE_HOURS] = float("nan")
    n.generators.at[g, LIFE_HOURS] = 10_000.0
    assert fp() == before  # the LP does not read it
    assert (
        owner_assets_digest(n, commercial) != ic_before
    )  # the investment case reads stale
    n.generators.drop(columns=LIFE_HOURS, inplace=True)
    assert owner_assets_digest(n, commercial) == ic_before
    n.generators[OM_HOUR] = float("nan")
    n.generators.at[g, OM_HOUR] = 3.0
    moved = fp()
    assert moved[0] != before[0] and moved[1] != before[1]
    n.generators.drop(columns=OM_HOUR, inplace=True)
    assert fp() == before


# ── GK B H1: the O&M is counted once; the fuel block keeps only the fuel ─────


def test_the_om_sits_once_in_opex_and_leaves_the_fuel_block_alone():
    curve = {fuel_curve.SLOPE: 0.25, fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0}

    def run(om):
        n = _network(om=om)
        n.generators.loc["genset", list(curve)] = list(curve.values())
        fuel_curve.apply(n)
        _solve(n)
        return n, compute_cost_breakdown(n, CFG)

    n0, cb0 = run(None)
    n1, cb1 = run(OM)
    assert (
        n0.generators_t.status["genset"].sum()
        == n1.generators_t.status["genset"].sum()
        == HOURS
    )
    (f0,), (f1,) = cb0["fuel"], cb1["fuel"]
    assert f1 == f0  # fuel_use × price only, exactly
    om = _block(cb1)["om_cost_eur"]
    assert om == pytest.approx(HOURS * OM)
    # The statistics opex the ledger's one opex line per asset reads (`value_flows._asset_costs`) = fuel + O&M.
    assert _opex_of(n1) == pytest.approx(f1["fuel_cost_eur"] + om)
    assert _opex_of(n0) == pytest.approx(f0["fuel_cost_eur"])
    assert cb1["opex"] == pytest.approx(cb0["opex"] + om)


# ── GK B H2: finite inputs on every path ────────────────────────────────────


@pytest.mark.parametrize("col", [OM_HOUR, LIFE_HOURS])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_the_write_model_refuses_non_finite_inputs(col, bad):
    from pydantic import ValidationError

    from models.schemas import GeneratorCreate

    with pytest.raises(ValidationError):
        GeneratorCreate(name="g", bus="b", **{col: bad})


@pytest.mark.parametrize("col", [OM_HOUR, LIFE_HOURS])
@pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
def test_an_imported_non_finite_input_is_a_pre_run_error(col, bad):
    from services.validation_service import _check_fuel_curves

    n = _network(om=None)
    n.generators[col] = bad
    (issue,) = _check_fuel_curves(n)
    assert issue.message.startswith(f"{fuel_curve.LABELS[col]}: enter a finite number")
    assert fuel_curve.lp_refusals(n)[0].startswith(
        f"genset: {fuel_curve.LABELS[col]}: enter a finite number"
    )


def _bulk(client, updates):
    return client.patch(
        "/api/network/_bulk",
        json={"component_class": "Generator", "names": ["genset"], "updates": updates},
    )


def test_a_zero_run_hour_lifetime_refuses_on_single_and_bulk_edits(
    client, install_network
):
    _installed(install_network)
    r = client.post("/api/network/generators", json=_genset(**{LIFE_HOURS: 0.0}))
    assert (
        r.status_code == 422
        and "Lifetime in running hours: enter more than zero" in r.text
    )
    assert (
        client.post(
            "/api/network/generators", json=_genset(**{LIFE_HOURS: 9_000.0})
        ).status_code
        == 201
    )
    r = _bulk(client, {LIFE_HOURS: 0.0})
    assert r.status_code == 422 and r.json()["detail"].startswith(
        "Lifetime in running hours: enter more than zero"
    )
    r = _bulk(client, {LIFE_HOURS: 12_000.0})
    assert r.status_code == 200, r.text


# ── GK B H8: the 02a guards hold for the O&M on every path ──────────────────


def test_bulk_runs_the_om_guards_and_derivation(client, install_network):
    n = _installed(install_network)
    assert (
        client.post(
            "/api/network/generators", json=_genset(**{OM_HOUR: OM})
        ).status_code
        == 201
    )
    r = _bulk(client, {"stand_by_cost": 9.0})
    assert r.status_code == 422 and r.json()["detail"].startswith(
        "Stand-by cost: set by the O&M per running hour"
    )
    r = _bulk(client, {"committable": False})
    assert r.status_code == 422 and r.json()["detail"].startswith(
        "O&M per running hour: turn on Unit commitment"
    )
    r = _bulk(client, {OM_HOUR: 5.0})
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == 5.0
    r = _bulk(client, {OM_HOUR: None})
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == 0.0


def test_om_beside_a_stand_by_series_refuses_on_every_path(client, install_network):
    from services.validation_service import _check_fuel_curves

    n = _installed(install_network)
    assert client.post("/api/network/generators", json=_genset()).status_code == 201
    # The bulk edit writes only existing columns: another generator's O&M creates the column.
    assert (
        client.post(
            "/api/network/generators", json=_genset(name="other", **{OM_HOUR: 1.0})
        ).status_code
        == 201
    )
    n.generators_t.stand_by_cost = pd.DataFrame({"genset": 1.0}, index=n.snapshots)
    want = "Stand-by cost: remove its profile, or clear the O&M per running hour"
    r = client.put("/api/network/generators/genset", json=_genset(**{OM_HOUR: OM}))
    assert r.status_code == 422 and want in r.text
    r = _bulk(client, {OM_HOUR: OM})
    assert r.status_code == 422 and r.json()["detail"].startswith(want)
    n.generators.loc["genset", OM_HOUR] = OM
    assert (
        f"genset: {want}. It sets this cost, so the profile would be ignored"
        in fuel_curve.lp_refusals(n)
    )
    assert [i.message for i in _check_fuel_curves(n) if i.name == "genset"][
        0
    ].startswith(want)


# ── mutant killers: the guard and notice edges ──────────────────────────────

CURVE = {fuel_curve.SLOPE: 0.25, fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0}


def test_a_curve_refusal_comes_before_the_run_hour_ones():
    assert fuel_curve.check({fuel_curve.SLOPE: -1.0, OM_HOUR: -1.0}).startswith(
        "Fuel curve slope:"
    )


def test_the_curve_keeps_its_own_series_and_edit_wording():
    row = {
        **CURVE,
        OM_HOUR: OM,
        "p_nom": 1.0,
        "committable": True,
        "stand_by_cost": 5.0,
    }
    assert fuel_curve.guard(row, cost_series=["stand_by_cost"]).startswith(
        "Stand-by cost: remove its profile, or clear the fuel curve"
    )
    msg = fuel_curve.guard(
        {**row, "stand_by_cost": 9.0}, before=row, submitted={"stand_by_cost": 9.0}
    )
    assert msg.startswith("Stand-by cost: set by the fuel curve")


def test_resending_the_derived_stand_by_cost_is_not_an_edit():
    before = {OM_HOUR: OM, "committable": True, "stand_by_cost": OM}
    assert (
        fuel_curve.guard(before, before=before, submitted={"stand_by_cost": OM}) is None
    )


def test_clearing_the_om_and_typing_a_stand_by_cost_is_not_refused():
    before = {OM_HOUR: OM, "committable": True, "stand_by_cost": OM}
    after = {**before, OM_HOUR: None, "stand_by_cost": 7.0}
    assert (
        fuel_curve.guard(
            after, before=before, submitted={OM_HOUR: None, "stand_by_cost": 7.0}
        )
        is None
    )


def test_cleared_and_its_notice_on_rows_without_run_hour_inputs():
    plain = {"stand_by_cost": 2.0, "committable": True}
    assert fuel_curve.cleared(plain, before=None) == {}
    assert fuel_curve.cleared(plain, before=plain) == {}
    assert fuel_curve.clear_notice(plain, before=None) is None
    assert fuel_curve.clear_notice(plain, before=plain) is None


def test_clearing_the_om_under_a_kept_curve_says_nothing():
    before = {**CURVE, OM_HOUR: OM, "p_nom": 1.0, "committable": True}
    after = {**before, OM_HOUR: None}
    assert fuel_curve.cleared(after, before=before) == {}
    assert fuel_curve.clear_notice(after, before=before) is None


def test_a_curve_cleared_under_om_with_no_marginal_cost_reads_zero():
    before = {**CURVE, OM_HOUR: OM, "committable": True}
    after = {**before, **{k: None for k in fuel_curve.COLUMNS}}
    assert "marginal cost keeps 0 €/MWh" in fuel_curve.clear_notice(
        after, before=before
    )


def test_om_with_a_curve_and_no_p_nom_derives_the_om_alone():
    assert fuel_curve.derived_costs({**CURVE, OM_HOUR: OM, "committable": True}) == {
        "marginal_cost": pytest.approx(10.0),
        "stand_by_cost": OM,
    }


# ── mutant killers: run hours and the run-hour lifetime ─────────────────────


def test_the_run_hours_row_has_exactly_its_fields():
    n = _solve(_network(weight=2.0))
    assert compute_cost_breakdown(n, CFG)["run_hours"] == [
        {
            "generator": "genset",
            "carrier": "diesel",
            "counted_from": "status",
            "hours_run": pytest.approx(HOURS * 2.0),
            "om_cost_eur": pytest.approx(HOURS * 2.0 * OM),
        }
    ]
    n = _solve(_network(om=None, life_h=1_000.0, committable=False))
    assert _block(compute_cost_breakdown(n, CFG))["counted_from"] == "dispatch"


def test_run_hour_lifetime_edges(monkeypatch):
    import services.adequacy.metrics as M

    n = _solve(_network(life_h=1_000.0))
    assert fuel_curve.run_hour_lifetime(n, "nope") == (None, [])
    for unset in (float("nan"), 0.0, -1.0):
        n.generators.loc["genset", LIFE_HOURS] = unset
        assert fuel_curve.run_hour_lifetime(n, "genset") == (None, [])
    n.generators.loc["genset", LIFE_HOURS] = 1_000.0
    monkeypatch.setattr(M, "horizon_years", lambda *_a, **_k: 0.0)
    assert fuel_curve.run_hour_lifetime(n, "genset") == (None, [])


def test_one_investment_period_is_not_averaged():
    n = pypsa.Network()
    n.set_snapshots(
        pd.MultiIndex.from_product(
            [[2030], pd.date_range("2030-01-01", periods=4, freq="h")]
        )
    )
    n.investment_periods = [2030]
    n.add("Bus", "el", carrier="AC")
    n.add(
        "Generator",
        "genset",
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        committable=True,
        **{LIFE_HOURS: 8_760.0},
    )
    n.generators_t.status = pd.DataFrame({"genset": [1.0] * 4}, index=n.snapshots)
    assert fuel_curve.run_hour_lifetime(n, "genset") == (pytest.approx(1.0), [])


def test_an_extendable_unit_with_no_solved_capacity_counts_any_output():
    n = _network(om=None, life_h=8_760.0, committable=False)
    n.generators.loc["genset", ["p_nom_extendable", "p_nom_opt"]] = [True, float("nan")]
    n.generators_t.p = pd.DataFrame(
        {"genset": [1e-12] + [0.0] * (HOURS - 1)}, index=n.snapshots
    )
    assert fuel_curve.run_hour_lifetime(n, "genset")[0] == pytest.approx(HOURS)


@pytest.mark.parametrize(
    "parts, life, want_parts, want_life, flagged",
    [
        ((5.0,), 5.0, [5.0], 5.0, False),  # equal: unchanged, unflagged
        (
            (3.0,),
            None,
            [3.0],
            5.0,
            True,
        ),  # an unknown calendar life takes the run-hour one
        ((20.0,), 4.0, [5.0], 4.0, True),  # a part longer than the run-hour life wears
        (
            (None,),
            3.0,
            [5.0],
            3.0,
            True,
        ),  # a part with no stated life takes the run-hour one
    ],
)
def test_which_lives_the_run_hour_life_shortens(
    parts, life, want_parts, want_life, flagged
):
    from services.finance.case import AssetPart
    from services.results.finance_case import _run_hour_life

    n = _solve(_network(life_h=10_950.0, weight=365.0, load=_sixth()))
    got, got_life, flags = _run_hour_life(
        n, "genset", tuple(AssetPart("investment", 1.0, v) for v in parts), life
    )
    assert [p.lifetime_years for p in got] == [pytest.approx(v) for v in want_parts]
    assert got_life == pytest.approx(want_life)
    assert flags == (["run_hour_lifetime:genset"] if flagged else [])


# ── the acceptance line end to end: the replacement year from the schedule ──


def test_the_part_lifetimes_schedule_rebuys_at_the_run_hour_life():
    from datetime import date

    from services.finance.case import FinanceCase, FinanceInputs, Template
    from services.finance.timeline import build_timeline

    n = _solve(_network(life_h=10_950.0, weight=365.0, load=_sixth()))
    (a,), _rates, _flags = _assets(n, [("Generator", "genset")])
    a = dataclasses.replace(
        a,
        parts=(dataclasses.replace(a.parts[0], overnight_cost=1_000.0),),
        overnight_cost=1_000.0,
    )
    cod = date(2031, 1, 1)
    fin = FinanceInputs(
        financial_close=date(2030, 6, 1),
        cod_by_asset={"genset": cod},
        analysis_years=12,
        replacement_rule="part_lifetimes",
    )
    case = FinanceCase(
        inputs=fin,
        owner="owner",
        base_year=2030,
        cod=cod,
        templates=(Template(first_year=2031, lines=()),),
        assets=(a,),
    )
    got = replacements.schedule(case, build_timeline(case))
    # 5-year life from 2031: re-bought in the last service year of each purchase, 2035 and 2040.
    assert [(r.year, r.asset) for r in got] == [(2035, "genset"), (2040, "genset")]
