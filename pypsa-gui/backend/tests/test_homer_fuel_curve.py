"""homer-parity 02a: a generator's fuel curve (no-load intercept + slope) in the dispatch and the cost breakdown."""

from __future__ import annotations

import math

import numpy as np

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
    kw = dict(
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        committable=committable,
        p_min_pu=0.2 if committable else 0.0,
    )
    if curve:
        kw.update(
            {
                fuel_curve.SLOPE: SLOPE,
                fuel_curve.NO_LOAD: NO_LOAD if committable else 0.0,
                fuel_curve.PRICE: PRICE,
            }
        )
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
    hand = HOURS * (NO_LOAD * 1.0 + SLOPE * LOAD_MW)  # 0.125 MWh/h → 3.0 MWh
    linear = HOURS * SLOPE * LOAD_MW  # 0.075 MWh/h → 1.8 MWh
    assert row["fuel_mwh"] == pytest.approx(hand)
    assert row["no_load_fuel_mwh"] == pytest.approx(HOURS * NO_LOAD)
    assert row["fuel_mwh"] > linear + 1.0
    assert row["fuel_cost_eur"] == pytest.approx(hand * PRICE)
    assert row["no_load_cost_eur"] == pytest.approx(HOURS * NO_LOAD * PRICE)


def test_cost_breakdown_opex_and_objective_carry_the_fuel_cost():
    n = _solve(_network(curve=True))
    cb = compute_cost_breakdown(n, CFG)
    hand = HOURS * (NO_LOAD + SLOPE * LOAD_MW) * PRICE  # 120 EUR
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
    n.generators_t.status.iloc[:6, 0] = 0.0  # off for 6 h: no no-load burn there
    n.generators_t.p.iloc[:6, 0] = 0.0
    row = _row(compute_cost_breakdown(n, CFG))
    on_h = HOURS - 6
    assert row["no_load_fuel_mwh"] == pytest.approx(2.0 * on_h * NO_LOAD)
    assert row["fuel_mwh"] == pytest.approx(2.0 * on_h * (NO_LOAD + SLOPE * LOAD_MW))


def test_apply_rederives_after_a_p_nom_edit():
    n = _network(curve=True)
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
        NO_LOAD * 1.0 * PRICE
    )
    n.generators.at["genset", "p_nom"] = 3.0
    assert fuel_curve.apply(n, ["genset", "missing"]) == ["genset"]
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
        NO_LOAD * 3.0 * PRICE
    )
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * PRICE)


@pytest.mark.parametrize(
    "row, msg",
    [
        (
            {fuel_curve.SLOPE: -0.1, fuel_curve.PRICE: 40.0},
            "Fuel curve slope: enter zero or more",
        ),
        (
            {fuel_curve.NO_LOAD: -0.1, fuel_curve.PRICE: 40.0},
            "No-load fuel: enter zero or more",
        ),
        ({fuel_curve.PRICE: -1.0}, "Fuel price: enter zero or more"),
        ({fuel_curve.SLOPE: 0.25}, "Fuel price: enter the price per MWh of fuel"),
        (
            {fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0},
            "No-load fuel: turn on Unit commitment",
        ),
        (
            {
                fuel_curve.NO_LOAD: 0.05,
                fuel_curve.PRICE: 40.0,
                "committable": True,
                "p_nom_extendable": True,
            },
            "No-load fuel: untick Extendable",
        ),
        (
            {fuel_curve.SLOPE: "abc", fuel_curve.PRICE: 40.0},
            "Fuel curve slope: enter a finite number",
        ),
        (
            {fuel_curve.SLOPE: math.inf, fuel_curve.PRICE: 40.0},
            "Fuel curve slope: enter a finite number",
        ),
        (
            {fuel_curve.NO_LOAD: 0.05, fuel_curve.PRICE: 40.0, "committable": math.nan},
            "turn on Unit commitment",
        ),
    ],
)
def test_check_refuses(row, msg):
    assert msg in (fuel_curve.check(row) or "")


@pytest.mark.parametrize(
    "row",
    [
        {},
        {fuel_curve.SLOPE: None, fuel_curve.NO_LOAD: math.nan, fuel_curve.PRICE: None},
        {fuel_curve.SLOPE: 0.25, fuel_curve.PRICE: 40.0},
        {fuel_curve.PRICE: 40.0},
        {fuel_curve.SLOPE: 0.0, fuel_curve.NO_LOAD: 0.0, fuel_curve.PRICE: 0.0},
        {
            fuel_curve.NO_LOAD: 0.05,
            fuel_curve.PRICE: 40.0,
            "committable": True,
            "p_nom_extendable": False,
        },
    ],
)
def test_check_accepts(row):
    assert fuel_curve.check(row) is None


def test_derived_costs():
    assert fuel_curve.derived_costs({}) == {}
    assert fuel_curve.derived_costs(
        {
            fuel_curve.SLOPE: 0.25,
            fuel_curve.NO_LOAD: 0.05,
            fuel_curve.PRICE: 40.0,
            "p_nom": 2.0,
        }
    ) == pytest.approx({"marginal_cost": 10.0, "stand_by_cost": 4.0})


# ── the asset write path (network_crud) and the pre-run check ─────────────────────────────────────────────────

CURVE = {fuel_curve.SLOPE: SLOPE, fuel_curve.NO_LOAD: NO_LOAD, fuel_curve.PRICE: PRICE}


def _genset(**kw):
    return {
        "name": "genset",
        "bus": "B1",
        "carrier": "diesel",
        "p_nom": 2.0,
        "committable": True,
        **kw,
    }


def test_post_derives_the_costs_from_the_curve(client, install_network):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json=_genset(**CURVE))
    assert r.status_code == 201, r.text
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * PRICE)
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
        NO_LOAD * 2.0 * PRICE
    )
    r = client.put("/api/network/generators/genset", json=_genset(p_nom=5.0))
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
        NO_LOAD * 5.0 * PRICE
    )


def test_post_without_a_curve_writes_no_curve_column(client, install_network):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    r = client.post("/api/network/generators", json=_genset(marginal_cost=7.0))
    assert r.status_code == 201, r.text
    assert not set(fuel_curve.COLUMNS) & set(n.generators.columns)
    assert n.generators.at["genset", "marginal_cost"] == 7.0


@pytest.mark.parametrize(
    "body", [_genset(**CURVE, committable=False), _genset(**{fuel_curve.SLOPE: 0.25})]
)
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
    assert (issue.severity, issue.code, issue.name) == (
        "error",
        "fuel_curve_invalid",
        "genset",
    )
    assert "Unit commitment" in issue.message
    assert _check_fuel_curves(_network(curve=False)) == []


def test_the_generator_form_shows_the_curve_on_every_generator():
    """GK B Q1: an LP input never hides, and `reveal_when` cannot say "fuel carrier OR a curve is set", so the
    rows show on every generator and their help names them fuel-burning only."""
    from services.asset_schema import fields as F

    rows = {f.key: f for f in F.fields_for("Generator")}
    for key in fuel_curve.COLUMNS:
        assert rows[key].reveal_when is None, key
        assert rows[key].blank == "clear", key
        assert rows[key].help.startswith("Fuel-burning generators only."), key
    assert "Unit commitment" in rows[fuel_curve.NO_LOAD].help
    assert rows["stand_by_cost"].derived and rows["stand_by_cost"].reveal_when == {
        "committable": True
    }
    assert "minimum load ratio" in rows["p_min_pu"].help


# ── GK B H1: every writer runs the same guard; the solve staging is authoritative ──────────────────────────


def _installed(install_network):
    from tests.conftest import build_network

    n = build_network()
    install_network(n)
    return n


def test_put_refuses_a_direct_edit_of_a_derived_cost_while_a_curve_is_set(
    client, install_network
):
    n = _installed(install_network)
    assert (
        client.post("/api/network/generators", json=_genset(**CURVE)).status_code == 201
    )
    for col, val in (("marginal_cost", 99.0), ("stand_by_cost", 1.0)):
        r = client.put(
            "/api/network/generators/genset", json=_genset(**CURVE, **{col: val})
        )
        label = fuel_curve.LABELS[col]
        assert r.status_code == 422 and r.json()["detail"].startswith(
            f"{label}: set by the fuel curve"
        ), r.text
    # The card sends its whole form: the stored derived values unchanged are not an edit.
    row = {c: float(n.generators.at["genset", c]) for c in fuel_curve.DERIVED}
    r = client.put(
        "/api/network/generators/genset",
        json=_genset(**{**CURVE, fuel_curve.PRICE: 50.0}, **row),
    )
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * 50.0)
    # Clearing the curve frees the column again.
    cleared = {k: None for k in fuel_curve.COLUMNS}
    r = client.put(
        "/api/network/generators/genset", json=_genset(**cleared, marginal_cost=7.0)
    )
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "marginal_cost"] == 7.0


def test_setting_a_curve_says_what_it_replaces(client, install_network):
    from services import change_log_service

    _installed(install_network)
    r = client.post(
        "/api/network/generators", json=_genset(**CURVE, marginal_cost=12.0)
    )
    assert r.status_code == 201
    assert (
        r.json()["notice"]
        == "The fuel curve replaces the marginal cost of 12 €/MWh with 10 €/MWh."
    )
    assert (
        client.post(
            "/api/network/generators", json={**_genset(marginal_cost=3.0), "name": "g2"}
        ).status_code
        == 201
    )
    r = client.put(
        "/api/network/generators/g2", json={**_genset(**CURVE), "name": "g2"}
    )
    assert (
        r.json()["notice"]
        == "The fuel curve replaces the marginal cost of 3 €/MWh with 10 €/MWh."
    )
    assert any(
        "marginal cost of 3 €/MWh with 10" in str(e)
        for e in change_log_service.get_all()
    )
    # A cost sent with the new curve is the one named: never overwritten unsaid.
    assert (
        client.post(
            "/api/network/generators", json={**_genset(), "name": "g3"}
        ).status_code
        == 201
    )
    r = client.put(
        "/api/network/generators/g3",
        json={**_genset(**CURVE, marginal_cost=8.0), "name": "g3"},
    )
    assert (
        r.json()["notice"]
        == "The fuel curve replaces the marginal cost of 8 €/MWh with 10 €/MWh."
    )
    # Changing an existing curve says nothing: there is nothing of the person's left to replace.
    r = client.put(
        "/api/network/generators/g2",
        json={**_genset(**{**CURVE, fuel_curve.PRICE: 41.0}), "name": "g2"},
    )
    assert "notice" not in r.json()


def test_a_curve_and_a_marginal_cost_series_refuse(client, install_network):
    n = _installed(install_network)
    assert client.post("/api/network/generators", json=_genset()).status_code == 201
    n.generators_t.marginal_cost["genset"] = 5.0
    r = client.put("/api/network/generators/genset", json=_genset(**CURVE))
    assert r.status_code == 422 and r.json()["detail"].startswith(
        "Marginal cost: remove its profile"
    )
    m = _network(curve=True)
    m.generators_t.marginal_cost["genset"] = 5.0
    from services.validation_service import _check_fuel_curves

    (issue,) = _check_fuel_curves(m)
    assert issue.message.startswith("Marginal cost: remove its profile")


def test_a_curve_edit_stamps_the_derived_costs_customised(client, install_network):
    from services.asset_schema import provenance

    n = _installed(install_network)
    assert (
        client.post("/api/network/generators", json=_genset(**CURVE)).status_code == 201
    )
    r = client.put(
        "/api/network/generators/genset",
        json=_genset(**{**CURVE, fuel_curve.PRICE: 60.0}),
    )
    assert r.status_code == 200
    stamped = provenance._root(n).get("Generator", {}).get("genset", {})
    assert {"marginal_cost", "stand_by_cost"} <= set(stamped), stamped


def test_bulk_runs_the_same_guard_and_derivation(client, install_network):
    n = _installed(install_network)
    assert (
        client.post("/api/network/generators", json=_genset(**CURVE)).status_code == 201
    )
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["genset"],
            "updates": {"marginal_cost": 99.0},
        },
    )
    assert r.status_code == 422 and r.json()["detail"].startswith(
        "Marginal cost: set by the fuel curve"
    )
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["genset"],
            "updates": {"committable": False},
        },
    )
    assert r.status_code == 422 and r.json()["detail"].startswith(
        "No-load fuel: turn on Unit commitment"
    )
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["genset"],
            "updates": {"p_nom": 4.0},
        },
    )
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
        NO_LOAD * 4.0 * PRICE
    )
    assert n.generators.at["genset", "committable"]


def test_the_solve_staging_rederives_stale_stored_costs():
    """H1: a writer that bypasses the derivation (here a direct frame edit) cannot reach the LP stale."""
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    n = _network(curve=True)
    n.generators.loc["genset", ["marginal_cost", "stand_by_cost"]] = [0.0, 0.0]
    PyPSAService.set_network(n)
    status, cond = run_simulation(
        CFG,
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        queue.SimpleQueue(),
        state_update=lambda **kw: None,
    )
    assert status in ("ok", "optimal"), (status, cond)
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(NO_LOAD * PRICE)
    assert n.objective == pytest.approx(HOURS * (NO_LOAD + SLOPE * LOAD_MW) * PRICE)


# ── GK B H2: the fingerprint ───────────────────────────────────────────────────────────────────────────────


def test_the_curve_enters_the_fingerprint_only_when_present(
    session_ctx, client, install_network
):
    from services.workflow_tools import _network_input_fingerprint

    n = _installed(install_network)
    ctx = session_ctx(client)

    def fp():
        return _network_input_fingerprint(ctx), _network_input_fingerprint(
            ctx, exact=True
        )

    before = fp()
    n.generators["fuel_price"] = np.nan
    moved = fp()
    assert moved[0] != before[0] and moved[1] != before[1]
    n.generators.at[n.generators.index[0], "fuel_price"] = 40.0
    priced = fp()
    assert (
        priced[0] != moved[0] and priced[1] != moved[1]
    )  # a fuel_price edit alone moves it
    n.generators.drop(columns="fuel_price", inplace=True)
    assert fp() == before  # absent: byte-identical to before
    n.generators.at[n.generators.index[0], "stand_by_cost"] = 3.0
    assert fp()[1] != before[1]  # stand_by_cost is hashed as a PyPSA column


# ── GK B H6, H7: the fuel block reads solved values only; weightings; off hours ────────────────────────────


def _opex_of(n, name="genset"):
    """The generator's statistics opex, weighted like the LP objective (PyPSA's own number)."""
    op = n.statistics.opex(groupby=False, comps="Generator")
    return (
        float(op.xs(name, level="name").sum())
        if isinstance(op.index, pd.MultiIndex)
        else float(op[name])
    )


def test_a_generator_with_no_solved_values_reads_none():
    n = _solve(_network(curve=True))
    n.add(
        "Generator",
        "late",
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        committable=True,
        **{
            fuel_curve.SLOPE: SLOPE,
            fuel_curve.NO_LOAD: NO_LOAD,
            fuel_curve.PRICE: PRICE,
        },
    )
    n.add(
        "Generator",
        "late_lp",
        bus="el",
        carrier="diesel",
        p_nom=1.0,
        **{fuel_curve.SLOPE: SLOPE, fuel_curve.PRICE: PRICE},
    )
    n.generators_t.p = n.generators_t.p.drop(
        columns=["late", "late_lp"], errors="ignore"
    )
    n.generators_t.p_set["late"] = 0.5  # an input, never read as dispatch
    rows = {r["generator"]: r for r in compute_cost_breakdown(n, CFG)["fuel"]}
    for name in ("late", "late_lp"):
        assert rows[name]["fuel_mwh"] is None and rows[name]["fuel_cost_eur"] is None, (
            name
        )
        assert (
            rows[name]["no_load_fuel_mwh"] is None
            and rows[name]["no_load_cost_eur"] is None
        ), name
    # Dispatch solved but no status for a no-load unit: None, not the slope part alone.
    n.generators_t.status = n.generators_t.status.drop(columns="genset")
    row = {r["generator"]: r for r in compute_cost_breakdown(n, CFG)["fuel"]}["genset"]
    assert row["fuel_mwh"] is None


def test_uneven_weightings_match_the_hand_calculation_and_the_opex():
    n = _network(curve=True)
    weights = pd.Series([1.0, 3.0] * (HOURS // 2), index=n.snapshots)
    for col in ("objective", "generators", "stores"):
        n.snapshot_weightings[col] = weights
    _solve(n)
    cb = compute_cost_breakdown(n, CFG)
    row = _row(cb)
    hand = weights.sum() * (NO_LOAD + SLOPE * LOAD_MW)
    assert row["fuel_mwh"] == pytest.approx(hand)
    assert row["fuel_cost_eur"] == pytest.approx(hand * PRICE)
    assert row["fuel_cost_eur"] == pytest.approx(_opex_of(n))
    assert cb["opex"] == pytest.approx(
        row["fuel_cost_eur"]
    )  # the block splits opex out, adds nothing


def test_multi_period_scales_by_the_period_years():
    n = _network(curve=True)
    snaps = n.snapshots
    n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], snaps]))
    n.investment_periods = [2030, 2040]
    n.investment_period_weightings.loc[:, "years"] = [10.0, 5.0]
    n.investment_period_weightings.loc[:, "objective"] = [1.0, 1.0]
    n.loads_t.p_set = pd.DataFrame(LOAD_MW, index=n.snapshots, columns=["site"])
    n.optimize(solver_name="highs", multi_investment_periods=True)
    cb = compute_cost_breakdown(n, CFG)
    row = _row(cb)
    hand = (10.0 + 5.0) * HOURS * (NO_LOAD + SLOPE * LOAD_MW)
    assert row["fuel_mwh"] == pytest.approx(hand)
    assert row["fuel_cost_eur"] == pytest.approx(cb["opex"])


def test_no_load_fuel_counts_only_committed_hours():
    """H7: p_min_pu > 0 and a load that drops to zero, so the solver itself switches the unit off."""
    n = _network(curve=True)
    load = pd.Series(LOAD_MW, index=n.snapshots)
    load.iloc[:6] = 0.0
    n.loads_t.p_set = pd.DataFrame({"site": load})
    _solve(n)
    on = n.generators_t.status["genset"]
    assert on.iloc[:6].sum() == 0 and on.iloc[6:].min() == 1
    row = _row(compute_cost_breakdown(n, CFG))
    on_h = HOURS - 6
    assert row["no_load_fuel_mwh"] == pytest.approx(on_h * NO_LOAD)
    assert row["fuel_mwh"] == pytest.approx(on_h * (NO_LOAD + SLOPE * LOAD_MW))
    assert row["fuel_cost_eur"] == pytest.approx(_opex_of(n))


def test_a_curve_generator_leaves_its_neighbours_costs_alone():
    n = _network(curve=True)
    n.add("Generator", "grid", bus="el", carrier="AC", p_nom=1.0, marginal_cost=33.0)
    fuel_curve.apply(n)
    assert n.generators.at["grid", "marginal_cost"] == 33.0
    assert [r["generator"] for r in fuel_curve.fuel_use(n)] == ["genset"]


# ── review fixes: clearing a curve, blank derived rows, bulk notices ───────────────────────────────────────

CLEARED = "Fuel curve cleared: stand-by cost reset to 0 €/h; marginal cost keeps 10 €/MWh (was derived from the curve)."


def test_clearing_a_curve_resets_the_read_only_stand_by_cost(client, install_network):
    n = _installed(install_network)
    assert (
        client.post("/api/network/generators", json=_genset(**CURVE)).status_code == 201
    )
    assert n.generators.at["genset", "stand_by_cost"] > 0
    r = client.put(
        "/api/network/generators/genset",
        json=_genset(**{k: None for k in fuel_curve.COLUMNS}),
    )
    assert r.status_code == 200, r.text
    assert n.generators.at["genset", "stand_by_cost"] == 0.0
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(
        SLOPE * PRICE
    )  # editable: it stays
    assert r.json()["notice"] == CLEARED


def test_bulk_clearing_resets_stand_by_and_a_new_curve_says_what_it_replaces(
    client, install_network
):
    n = _installed(install_network)
    assert (
        client.post(
            "/api/network/generators", json=_genset(marginal_cost=6.0)
        ).status_code
        == 201
    )
    # Bulk edits existing columns only, as for every lazy generator column: another curve generator adds them.
    assert (
        client.post(
            "/api/network/generators", json={**_genset(**CURVE), "name": "g0"}
        ).status_code
        == 201
    )
    r = client.patch(
        "/api/network/_bulk",
        json={"component_class": "Generator", "names": ["genset"], "updates": CURVE},
    )
    assert r.status_code == 200, r.text
    assert r.json()["notices"] == [
        "genset: The fuel curve replaces the marginal cost of 6 €/MWh with 10 €/MWh."
    ]
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["genset"],
            "updates": {k: None for k in fuel_curve.COLUMNS},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["notices"] == [f"genset: {CLEARED}"]
    assert n.generators.at["genset", "stand_by_cost"] == 0.0


def test_a_blank_derived_row_is_not_an_edit():
    before = {
        **CURVE,
        "committable": True,
        "p_nom": 1.0,
        "marginal_cost": 10.0,
        "stand_by_cost": 2.0,
    }
    assert (
        fuel_curve.guard(
            before,
            before=before,
            submitted={"stand_by_cost": None, "marginal_cost": math.nan},
        )
        is None
    )
    assert (
        fuel_curve.guard(
            before, before=before, submitted={"marginal_cost": 10.0 + 1e-12}
        )
        is None
    )
    assert fuel_curve.guard(
        before, before=before, submitted={"marginal_cost": 10.5}
    ).startswith("Marginal cost:")


def test_cleared_and_cost_series():
    curve = {**CURVE, "p_nom": 1.0}
    assert fuel_curve.cleared({}, before=curve) == {"stand_by_cost": 0.0}
    assert fuel_curve.cleared(curve, before=curve) == {}
    assert fuel_curve.cleared({}, before={}) == {}
    assert fuel_curve.cleared({}, before=None) == {}
    n = _network(curve=True)
    assert fuel_curve.cost_series(n, "genset") == []
    n.generators_t.stand_by_cost["genset"] = 1.0
    assert fuel_curve.cost_series(n, "genset") == ["stand_by_cost"]


# ── DC backup: stated precedence (GK B ruling 3) ─────────────────────────────────────────────────────────────


def _dc_assess(n):
    import queue
    import threading

    from project_templates import eh_templates as t
    from services.adequacy import dc_backup

    from tests.test_dc_backup import _dc

    out = dc_backup.assess(
        n,
        t.island_config("eh_datacenter"),
        _dc("bess"),
        years=20,
        prices=dc_backup.pack_prices(t.TEMPLATE_PACK_VERSION),
        cfg=SolverConfig(voll=500.0),
        lock=threading.Lock(),
        stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
    )
    return out, dc_backup.genset_units(n, t.island_config("eh_datacenter"))


def _dc_network():
    from project_templates import eh_templates as t

    from tests.test_dc_backup import _dc

    spec = _dc("bess")
    return t.build_eh_datacenter(
        load_spec=spec.model_copy(
            update={"backup": spec.backup.model_copy(update={"mode": "genset"})}
        )
    )


def _curve_note(out):
    return [x for x in out["cost"]["notes"] if x.startswith("fuel_curve_ignored")]


@pytest.mark.live_solve
def test_the_dc_backup_prices_fuel_from_its_heat_rate_not_a_genset_curve():
    from services.adequacy import dc_backup

    plain, units = _dc_assess(_dc_network())
    assert len(units) > 1 and _curve_note(plain) == []
    assert plain["cost"]["notes"][: len(dc_backup.COST_NOTES)] == list(
        dc_backup.COST_NOTES
    )
    n = _dc_network()
    other = next(g for g in n.generators.index if g not in units)
    for col, v in CURVE.items():
        n.generators.loc[other, col] = (
            v  # a curve on a generator outside the backup fleet
        )
    off, _ = _dc_assess(n)
    assert off["cost"]["notes"] == plain["cost"]["notes"]
    for col, v in CURVE.items():
        n.generators.loc[units[0], col] = v  # one backup genset of several
    curved, _ = _dc_assess(n)
    for key in (
        "npv_genset",
        "npv_chosen",
        "delta",
        "fuel_mwh_th_avoided_per_year",
        "lines",
        "inputs",
    ):
        assert curved["cost"][key] == plain["cost"][key], key
    (note,) = _curve_note(curved)
    assert note.startswith(
        "fuel_curve_ignored: fuel is priced from the backup heat rate ("
    )
    assert "slope and no-load fuel" in note and "is not read here" in note
    assert (
        units[0] in note and not any(u in note for u in units[1:]) and other not in note
    )


@pytest.mark.live_solve
def test_a_dc_backup_assessment_leaves_the_cost_breakdown_unchanged():
    from project_templates import eh_templates as t
    from services.adequacy import dc_backup

    n = _dc_network()
    units = dc_backup.genset_units(n, t.island_config("eh_datacenter"))
    assert units
    for g in units:
        for col, v in CURVE.items():
            n.generators.loc[g, col] = v
    fuel_curve.apply(n, stamp=False)
    n.optimize(solver_name="highs")
    n.model.solver_model = None  # the assessment copies the network
    cfg = SolverConfig()
    before = compute_cost_breakdown(n, cfg)
    _dc_assess(n)
    after = compute_cost_breakdown(n, cfg)
    for key in ("capex", "opex", "total"):
        assert after[key] == pytest.approx(before[key]), key


def test_a_bool_cell_in_a_curve_column_reads_as_unset():
    # H8: a bool column read back from a file is not a number; float(True) would make it a slope of 1.
    assert fuel_curve.curve_of({fuel_curve.SLOPE: True}) is None
    assert fuel_curve.curve_of({fuel_curve.SLOPE: False, fuel_curve.PRICE: 40.0}) == (
        0.0,
        0.0,
        40.0,
    )


# ── GK B ruling 1 (option A): the LP-chain guard on every solve path ─────────────────────────────────────────

CO2_T_PER_MWH_TH, EFF = 0.25, 0.4


def _co2_network(*, periods: bool) -> pypsa.Network:
    """The curve genset, non-committable, burning a CO2-priced fuel at efficiency 0.4."""
    n = _network(curve=True, committable=False)
    n.add("Carrier", "diesel", co2_emissions=CO2_T_PER_MWH_TH)
    n.generators.at["genset", "efficiency"] = EFF
    if periods:
        snaps = n.snapshots[:12]
        n.set_snapshots(snaps)
        n.set_investment_periods([2030, 2035])
        n.investment_period_weightings.loc[:, ["objective", "years"]] = 1.0
    return n


def _run_sim(n, cfg):
    import queue
    import threading

    from services.pypsa_service import PyPSAService
    from services.solver_service import run_simulation

    PyPSAService.set_network(n)
    log = queue.SimpleQueue()
    status, cond = run_simulation(
        cfg,
        n,
        PyPSAService.get_lock(),
        threading.Event(),
        log,
        state_update=lambda **kw: None,
    )
    lines = []
    while not log.empty():
        if (x := log.get()) is not None:
            lines.append(str(x))
    return status, cond, "\n".join(lines)


@pytest.mark.parametrize("periods", [False, True])
def test_a_co2_priced_curve_run_pays_the_adder_exactly_once(periods):
    n = _co2_network(periods=periods)
    prices = {2030: 100.0, 2035: 200.0}
    cfg = SolverConfig(co2_price=100.0, co2_price_per_period=prices if periods else {})
    # Hand calculation: fuel slope × price, plus price_CO2 × 0.25 t/MWh_th / 0.4 (= 62.5 €/MWh at 100 €/t).
    if periods:
        expect = sum(
            12 * LOAD_MW * (SLOPE * PRICE + p * CO2_T_PER_MWH_TH / EFF)
            for p in prices.values()
        )
    else:
        expect = HOURS * LOAD_MW * (SLOPE * PRICE + 100.0 * CO2_T_PER_MWH_TH / EFF)
    for _ in range(2):  # re-staging neither drops nor doubles the adder
        status, cond, log = _run_sim(n, cfg)
        assert status in ("ok", "optimal"), (cond, log[-800:])
        assert n.objective == pytest.approx(expect, rel=1e-9)
        assert n.generators.at["genset", "marginal_cost"] == pytest.approx(
            SLOPE * PRICE
        )
        assert "genset" not in n.generators_t.marginal_cost.columns


def test_the_chain_refuses_half_a_curve_and_a_user_cost_series_by_kind():
    from services.solver_service import build_lp_chain

    n = _network(curve=True)
    n.generators.at["genset", fuel_curve.PRICE] = np.nan  # half a curve
    chain = build_lp_chain(n, CFG, None)
    with pytest.raises(fuel_curve.FuelCurveError, match="genset"):
        chain(n, n.snapshots)
    n = _network(curve=True)
    n.generators_t.marginal_cost["genset"] = pd.Series(
        5.0, n.snapshots
    )  # a user cost series
    with pytest.raises(fuel_curve.FuelCurveError, match="remove its profile"):
        build_lp_chain(n, CFG, None)(n, n.snapshots)
    assert (
        fuel_curve.wrap_lp(_network(curve=False), None, None) is None
    )  # no curve: nothing chained
    assert fuel_curve.lp_refusals(_network(curve=True)) == []
    assert issubclass(fuel_curve.FuelCurveError, ValueError)


def _tiny_sizing(**over):
    from tests.test_sizing_loop_runner import _tiny

    n = _tiny()
    for col, v in {**CURVE, **over}.items():
        n.generators.at["genset", col] = v
    return n


def test_the_sizing_loop_refuses_half_a_curve_and_a_user_cost_series():
    from services.adequacy.sizing_loop_runner import run_sizing

    from tests.test_sizing_loop_runner import _config, _inputs

    for n in (_tiny_sizing(**{fuel_curve.PRICE: np.nan}), _tiny_sizing()):
        if fuel_curve.curve_of(n.generators.loc["genset"])[
            2
        ]:  # the full curve gets a user cost series
            n.generators_t.marginal_cost["genset"] = pd.Series(5.0, n.snapshots)
        out = run_sizing(n, _config(), _inputs())
        cond = out["rounds"][0]["condition"]
        assert out["rounds"][0]["status"] == "error", out["verdict"]
        assert cond.startswith("FuelCurveError: genset: "), cond


def test_the_sizing_loop_stages_a_p_nom_changed_outside_the_form():
    from services.adequacy.sizing_loop_runner import _prepare, run_sizing

    from tests.test_sizing_loop_runner import _config, _inputs

    n = _tiny_sizing(p_nom_extendable=False, p_nom=50.0, committable=True)
    fuel_curve.apply(n, stamp=False)
    n.generators.at["genset", "p_nom"] = 60.0  # a writer that does not re-derive
    out = run_sizing(n, _config(), _inputs())
    assert out["status"] in ("converged", "not_converged"), out["verdict"]
    assert all(r["status"] == "ok" for r in out["rounds"])
    copy = n.copy()
    _prepare(copy, _config(), [])
    assert copy.generators.at["genset", "stand_by_cost"] == pytest.approx(
        NO_LOAD * 60.0 * PRICE
    )
    assert copy.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * PRICE)


def test_the_sizing_loop_never_reads_a_per_period_co2_column_as_a_user_series():
    from services.adequacy.sizing_loop_runner import _prepare

    n = _co2_network(periods=True)
    cfg = SolverConfig(co2_price_per_period={2030: 100.0, 2035: 200.0})
    undo: list = []
    kw = _prepare(n, cfg, undo)
    assert "genset" in n.generators_t.marginal_cost.columns  # the transform ran
    status, _ = n.optimize(solver_name="highs", multi_investment_periods=True, **kw)
    assert status == "ok"


def test_the_sizing_loop_result_is_unchanged_with_no_curve():
    """A2 pin: master's result on `_tiny()` (captured on the code before the `_prepare` reorder)."""
    from services.adequacy.sizing_loop_runner import run_sizing

    from tests.test_sizing_loop_runner import _config, _inputs, _tiny

    out = run_sizing(_tiny(), _config(), _inputs())
    assert (out["status"], len(out["rounds"])) == ("converged", 2)
    assert [r["cost_eur"] for r in out["rounds"]] == pytest.approx(
        [71379629.67760263, 72640406.59389393], rel=1e-7
    )
    assert out["final"]["sizes"] == pytest.approx(
        {
            "trafo": 104.59183673469389,
            "q_ss": 0.0,
            "q_dyn": 3.367966804973091,
            "p_sm": 40.0,
            "e_sm": 1.83510172158991,
            "store:energy": 21.052631578947366,
            "charger:energy": 10.526315789473683,
            "store:highC": 7.368421052631579,
            "charger:highC": 29.473684210526315,
            "s_x:energy": 2.0219298245614077,
            "s_x:highC": 5.894736842105264,
        },
        rel=1e-6,
        abs=1e-6,
    )


def test_a_tou_upload_onto_a_curve_generator_ends_in_the_pre_run_refusal(
    client, install_network, session_ctx
):
    n = _installed(install_network)
    assert (
        client.post("/api/network/generators", json=_genset(**CURVE)).status_code == 201
    )
    assert (
        client.post(
            "/api/projects/Fuel Site", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    gen = client.post(
        "/api/projects/Fuel Site/profiles/series/generate",
        json={
            "kind": "tou",
            "components": ["genset"],
            "params": {
                "component_type": "generators",
                "currency": "EUR",
                "price_basis": "nominal",
                "currency_year": 2025,
                "periods": [{"name": "flat", "rate": 50.0}],
            },
        },
    )
    assert gen.status_code == 200, gen.text
    applied = client.post(
        "/api/projects/Fuel Site/profiles/apply",
        json={
            "job_id": gen.json()["job_id"],
            "component": "genset",
            "time_basis": "UTC",
        },
    )
    assert applied.status_code == 200, applied.text
    n = session_ctx(client).network
    assert "genset" in n.generators_t.marginal_cost.columns
    status, cond, log = _run_sim(n, CFG)
    assert (status, cond) == ("error", "validation_failed")
    assert "fuel_curve_invalid" in log and "remove its profile" in log
