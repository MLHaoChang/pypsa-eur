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
    n.generators.at["genset", fuel_curve.SLOPE] = 2.5  # the fixture's 0.25 warns (02d)
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
    return [x for x in out["cost"]["notes"] if x.startswith("Fuel curve used here")]


@pytest.mark.live_solve
def test_the_dc_backup_prices_fuel_from_a_genset_curve():
    """02c: a curve on one backup genset of several prices the fleet's fuel MW-weighted with the heat rate; a curve
    outside the fleet changes nothing."""
    from services.adequacy import dc_backup

    plain, units = _dc_assess(_dc_network())
    assert len(units) > 1 and _curve_note(plain) == []
    assert "fuel_curve_priced" not in plain["cost"]
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
    assert _close(off["cost"], plain["cost"]) == []
    for col, v in CURVE.items():
        n.generators.loc[units[0], col] = v  # one backup genset of several
    curved, _ = _dc_assess(n)
    cost = curved["cost"]
    assert cost["fuel_curve_priced"] == [units[0]]
    assert cost["fuel_heat_rate_priced"] == units[1:]
    (note,) = _curve_note(curved)
    assert "MW-weighted (p_nom) with the backup heat rate and fuel price for " in note
    assert other not in note and all(u in note for u in units)
    w = {u: float(n.generators.at[u, "p_nom"]) for u in units}
    (heat,) = [i["value"] for i in plain["cost"]["inputs"] if i["key"] == "heat_rate"]
    fp = curved["cost"]["inputs"]
    (fuel_price,) = [i["value"] for i in fp if i["key"] == "fuel_price"]
    (cost_el,) = [i["value"] for i in fp if i["key"] == "fleet_fuel_cost_per_mwh_el"]
    expect = (
        w[units[0]] * (SLOPE + NO_LOAD) * PRICE
        + sum(w[u] for u in units[1:]) * heat * fuel_price
    ) / sum(w.values())
    assert cost_el == pytest.approx(expect, rel=1e-12)
    fuel = {ln["line"]: ln["npv"] for ln in cost["lines"] if ln["leg"] == "genset"}
    fuel0 = {
        ln["line"]: ln["npv"] for ln in plain["cost"]["lines"] if ln["leg"] == "genset"
    }
    assert fuel["fuel"] == pytest.approx(
        fuel0["fuel"] * expect / (heat * fuel_price), rel=1e-9
    )
    assert fuel["capex_and_repurchase"] == fuel0["capex_and_repurchase"]


def test_fleet_fuel_curve_weights_a_mixed_fleet_by_p_nom():
    from services.adequacy import dc_backup

    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "a", bus="b", p_nom=3.0, **CURVE)
    n.add("Generator", "b", bus="b", p_nom=1.0)
    n.add("Generator", "c", bus="b", p_nom=0.0)
    assert dc_backup.fleet_fuel_curve(n, ["b", "c"], 2.8, 50.0) is None
    all_curved = dc_backup.fleet_fuel_curve(n, ["a"], 2.8, 50.0)
    assert all_curved["heat_rate"] == pytest.approx(SLOPE + NO_LOAD)
    assert all_curved["cost_per_mwh_el"] == pytest.approx((SLOPE + NO_LOAD) * PRICE)
    assert (
        all_curved["heat_rate_gensets"] == []
        and "MW-weighted" not in all_curved["rule"]
    )
    mixed = dc_backup.fleet_fuel_curve(n, ["a", "b", "c"], 2.8, 50.0)
    assert mixed["curved"] == ["a"] and mixed["heat_rate_gensets"] == ["b", "c"]
    assert mixed["heat_rate"] == pytest.approx((3 * (SLOPE + NO_LOAD) + 2.8) / 4)
    assert mixed["cost_per_mwh_el"] == pytest.approx(
        (3 * (SLOPE + NO_LOAD) * PRICE + 2.8 * 50.0) / 4
    )
    n.generators.loc[["a", "b"], "p_nom"] = 0.0  # no rated MW anywhere: equal weights
    even = dc_backup.fleet_fuel_curve(n, ["a", "b"], 2.8, 50.0)
    assert even["heat_rate"] == pytest.approx((SLOPE + NO_LOAD + 2.8) / 2)


def test_the_backup_npv_of_a_curved_fleet_matches_the_hand_calculation():
    """02c: fuel = MW × run hours × (slope + no-load) × fuel price a year, discounted end of year."""
    from tests.test_dc_backup import _PRICES, _delta

    fleet = {
        "heat_rate": SLOPE + NO_LOAD,
        "cost_per_mwh_el": (SLOPE + NO_LOAD) * PRICE,
        "curved": ["g"],
        "heat_rate_gensets": [],
        "rule": "rule",
    }
    kw = dict(genset_mw=4.0, run_hours_per_year=50.0, years=3)
    out = _delta(fuel_curve_fleet=fleet, **kw)
    r = _PRICES["discount_rate"]
    per_year = 4.0 * 50.0 * (SLOPE + NO_LOAD) * PRICE
    (fuel,) = [
        ln["npv"]
        for ln in out["lines"]
        if ln["leg"] == "genset" and ln["line"] == "fuel"
    ]
    assert fuel == pytest.approx(
        sum(per_year / (1 + r) ** y for y in (1, 2, 3)), rel=1e-12
    )
    assert out["fuel_mwh_th_avoided_per_year"] == pytest.approx(
        4.0 * 50.0 * (SLOPE + NO_LOAD)
    )
    keys = [i["key"] for i in out["inputs"]]
    assert {"fleet_fuel_mwh_th_per_mwh_el", "fleet_fuel_cost_per_mwh_el"} <= set(keys)
    plain = _delta(**kw)
    assert _close(plain, _delta(fuel_curve_fleet=None, **kw)) == []
    assert "fleet_fuel_cost_per_mwh_el" not in [i["key"] for i in plain["inputs"]]
    with pytest.raises(Exception, match="fuel curve fleet"):
        _delta(fuel_curve_fleet={**fleet, "cost_per_mwh_el": float("nan")}, **kw)


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
    # Hand calculation: fuel slope × price, plus price_CO2 × 0.25 t/MWh_th × the slope's MWh_th per MWh (GK B H1:
    # the curve, not the 0.4 efficiency, sets the fuel and so the CO2).
    if periods:
        expect = sum(
            12 * LOAD_MW * (SLOPE * PRICE + p * CO2_T_PER_MWH_TH * SLOPE)
            for p in prices.values()
        )
    else:
        expect = HOURS * LOAD_MW * (SLOPE * PRICE + 100.0 * CO2_T_PER_MWH_TH * SLOPE)
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
        # UX: the condition opens with the generator, never the exception class.
        assert cond.startswith("genset: ") and "Error" not in cond, cond
        assert out["rounds"][0]["kind"] == "fuel_curve_invalid"


def test_any_other_sizing_solve_error_keeps_its_type_name_and_no_kind(monkeypatch):
    """GK B D2: only the fuel curve refusal drops its class name; a plain ValueError keeps it."""
    from services.adequacy import sizing_loop_runner as slr

    from tests.test_sizing_loop_runner import _config, _inputs, _tiny

    def boom(n, cfg, undo):
        raise ValueError("boom")

    monkeypatch.setattr(slr, "_prepare", boom)
    out = slr.run_sizing(_tiny(), _config(), _inputs())
    assert out["rounds"][0]["condition"] == "ValueError: boom"
    assert "kind" not in out["rounds"][0]


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


# ── H8: tests that kill the fuel_curve.py mutant survivors ───────────────────────────────────────────────────


def test_a_price_is_needed_only_by_a_non_zero_term():
    assert fuel_curve.check({fuel_curve.SLOPE: 0.0}) is None
    assert fuel_curve.check({fuel_curve.NO_LOAD: 0.0}) is None
    for col in (fuel_curve.SLOPE, fuel_curve.NO_LOAD):
        msg = fuel_curve.check({col: 0.05, "committable": True})
        assert msg is not None and msg.startswith("Fuel price:"), col


def test_same_treats_only_two_unset_values_as_equal():
    s = fuel_curve._same
    assert s(None, None) and s(np.nan, None) and s(5.0, 5.0 + 1e-12)
    assert not s(None, 5.0) and not s(5.0, None)
    assert not s("x", None) and not s(None, "x")


def test_derivation_without_p_nom_and_the_clear_notice_without_marginal_cost():
    assert fuel_curve.derived_costs(CURVE) == {
        "marginal_cost": SLOPE * PRICE,
        "stand_by_cost": 0.0,
    }
    assert "marginal cost keeps 0 €/MWh" in fuel_curve.clear_notice({}, before=CURVE)
    assert fuel_curve.clear_notice(CURVE, before=CURVE) is None


def test_a_cost_already_equal_to_the_derived_one_is_not_named_as_replaced():
    assert fuel_curve.replace_notice({**CURVE, "marginal_cost": SLOPE * PRICE}) is None


def test_apply_leaves_other_generators_and_stamps_only_when_asked():
    from services.asset_schema import provenance

    n = _network(curve=False)
    n.generators.at["genset", fuel_curve.SLOPE] = np.nan
    n.add("Generator", "other", bus="el", p_nom=1.0, stand_by_cost=3.0)
    n.add("Generator", "curve", bus="el", p_nom=2.0, committable=True, **CURVE)
    assert fuel_curve.apply(n, stamp=False) == ["curve"]
    assert n.generators.at["other", "stand_by_cost"] == 3.0
    assert not provenance.get(n, "Generator", "curve")
    n.generators.at["curve", "p_nom"] = 4.0
    assert fuel_curve.apply(n) == ["curve"]
    assert "stand_by_cost" in provenance.get(n, "Generator", "curve")


def test_the_wrap_logs_its_refusal():
    import queue

    n = _network(curve=True)
    n.generators.at["genset", fuel_curve.PRICE] = np.nan
    log = queue.SimpleQueue()
    fuel_curve.wrap_lp(n, None, log)
    assert log.get_nowait().startswith(
        "[FUEL CURVE] ERROR: Fuel curve: genset: Fuel price:"
    )
    fuel_curve.wrap_lp(n, None, None)  # no queue: nothing to log, nothing raised


def test_fuel_use_weights_a_multi_period_run_and_reads_a_non_committable_curve():
    n = _co2_network(periods=True)
    n.snapshot_weightings.loc[:, "objective"] = 2.0
    status, _ = n.optimize(solver_name="highs", multi_investment_periods=True)
    assert status == "ok"
    (row,) = fuel_curve.fuel_use(n, {2030: 10.0, 2035: 5.0})
    # 12 snapshots × weight 2 × (10 + 5) years × 0.3 MW × slope 0.25.
    assert row["fuel_mwh"] == pytest.approx(12 * 2 * 15 * LOAD_MW * SLOPE)
    assert row["no_load_fuel_mwh"] == 0.0
    single = _solve(_network(curve=True, committable=False))
    assert fuel_curve.fuel_use(single, {2030: 10.0}) == fuel_curve.fuel_use(single)


def test_a_curve_with_no_no_load_term_needs_no_solved_status():
    n = _network(curve=True)
    n.generators.at["genset", fuel_curve.NO_LOAD] = 0.0
    fuel_curve.apply(n)
    _solve(n)
    n.generators_t.status = n.generators_t.status.drop(
        columns="genset"
    )  # e.g. a file saved without it
    (row,) = fuel_curve.fuel_use(n)
    assert (
        row["fuel_mwh"] == pytest.approx(HOURS * LOAD_MW * SLOPE)
        and row["no_load_fuel_mwh"] == 0.0
    )


def test_a_bulk_clear_resets_stand_by_only_on_the_curve_generators(
    client, install_network
):
    """GK B D1: the reset is per target; a no-curve generator in the batch keeps its stand-by cost."""
    n = _installed(install_network)
    assert (
        client.post("/api/network/generators", json=_genset(**CURVE)).status_code == 201
    )
    plain = {**_genset(stand_by_cost=3.0), "name": "plain"}
    assert client.post("/api/network/generators", json=plain).status_code == 201
    r = client.patch(
        "/api/network/_bulk",
        json={
            "component_class": "Generator",
            "names": ["genset", "plain"],
            "updates": {k: None for k in fuel_curve.COLUMNS},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["notices"] == [f"genset: {CLEARED}"]
    assert n.generators.at["genset", "stand_by_cost"] == 0.0
    assert n.generators.at["plain", "stand_by_cost"] == 3.0


def test_fuel_use_scales_no_load_fuel_by_p_nom_and_names_the_carrier():
    """GK B T1: p_nom 1.25 (its 20 % minimum, 0.25 MW, sits under the 0.3 MW load, so the unit runs every hour)."""
    n = _network(curve=True)
    n.generators.at["genset", "p_nom"] = 1.25
    fuel_curve.apply(n)
    _solve(n)
    (row,) = fuel_curve.fuel_use(n)
    assert row["carrier"] == "diesel"
    assert row["no_load_fuel_mwh"] == pytest.approx(NO_LOAD * 1.25 * HOURS)
    assert row["fuel_mwh"] == pytest.approx(
        NO_LOAD * 1.25 * HOURS + SLOPE * LOAD_MW * HOURS
    )
    assert row["fuel_cost_eur"] == pytest.approx(row["fuel_mwh"] * PRICE)


@pytest.mark.parametrize(
    "scale",
    [{"auto_objective_scale": True}, {"user_objective_scale": 10.0}],
    ids=["auto", "user"],
)
def test_every_sizing_round_reports_its_cost_in_eur_whatever_the_objective_scale(scale):
    """GK A A2-1: `cost_eur` is the objective in EUR, with any applied scale reversed (as run_simulation does)."""
    from services.adequacy.sizing_loop_runner import run_sizing

    from tests.test_sizing_loop_runner import _config, _inputs, _tiny

    base = run_sizing(_tiny(), _config(voll=10_000.0), _inputs())
    scaled = run_sizing(_tiny(), _config(voll=10_000.0, **scale), _inputs())
    assert [r["cost_eur"] for r in scaled["rounds"]] == pytest.approx(
        [r["cost_eur"] for r in base["rounds"]], rel=1e-6
    )


def _close(a, b, path="") -> list[str]:
    """Where ``a`` and ``b`` differ, every leaf compared with == (GK B F1: the values are bit-equal to master's)."""
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return [f"{path}: keys {sorted(set(a) ^ set(b))}"]
        return [d for k in a for d in _close(a[k], b[k], f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [
            d
            for i, (x, y) in enumerate(zip(a, b))
            for d in _close(x, y, f"{path}[{i}]")
        ]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


@pytest.mark.live_solve
def test_a_no_curve_dc_backup_assessment_matches_master():
    """GK B (55): the no-curve `assess` cost block, notes and verdict equal master's (a52e33ef9), captured by running
    master's dc_backup on this network into the fixture."""
    import json
    import pathlib

    out, _ = _dc_assess(_dc_network())
    got = json.loads(
        json.dumps(
            {"cost": out["cost"], "notes": out["notes"], "verdict": out["verdict"]},
            default=str,
        )
    )
    master = json.loads(
        (
            pathlib.Path(__file__).parent
            / "fixtures"
            / "homer_02a_dc_backup_no_curve_master.json"
        ).read_text()
    )
    assert _close(got, master) == []


def test_a_refusal_in_the_full_year_solve_carries_its_kind():
    """GK B F2: the full-year solve's failure row carries the result's `kind`, as a round's does."""
    from services.adequacy.sizing_loop import run_sizing_loop

    from tests.test_sizing_loop import _check, _ok

    refused = {
        "status": "error",
        "condition": "genset: Fuel price: …",
        "kind": "fuel_curve_invalid",
    }
    out = run_sizing_loop(
        lambda s: _ok(100.0),
        lambda r, s: [_check("pst", True)],
        final_solve=lambda s: refused,
    )
    final = out["final"]  # the full-year row is reported as `final`, not as a round
    assert out["status"] == "failed"
    assert (final["basis"], final["status"], final["kind"]) == (
        "full_year",
        "error",
        "fuel_curve_invalid",
    )


def test_a_generator_with_every_input_column_is_staged_and_read_by_the_lp_guard():
    """02g: every column of INPUT_COLUMNS on one generator. `apply` stamps the derived costs and `lp_refusals`
    reads the row (its marginal cost profile is refused), so neither gate closes when every input is present."""
    n = _network(curve=True)
    n.generators.at["genset", fuel_curve.OM_HOUR] = 3.0
    n.generators.at["genset", fuel_curve.LIFE_HOURS] = 20_000.0
    n.generators.loc["genset", ["marginal_cost", "stand_by_cost"]] = 0.0
    assert set(fuel_curve.INPUT_COLUMNS) <= set(n.generators.columns)
    assert fuel_curve.apply(n, stamp=False) == ["genset"]
    assert n.generators.at["genset", "marginal_cost"] == pytest.approx(SLOPE * PRICE)
    assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
        NO_LOAD * 1.0 * PRICE + 3.0
    )
    assert fuel_curve.lp_refusals(n) == []
    n.generators_t.marginal_cost["genset"] = pd.Series(1.0, index=n.snapshots)
    (refusal,) = fuel_curve.lp_refusals(n)
    assert refusal.startswith("genset: Marginal cost: remove its profile")


# ── 02d: a slope below 1 MWh fuel per MWh warns ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "slope, warns",
    [(0.8, True), (0.999, True), (1.0, False), (2.5, False), (0.0, False)],
)
def test_a_slope_below_one_warns_before_the_run(slope, warns):
    from services.issue_titles import issue_title
    from services.validation_service import _check_fuel_curves

    n = _network(curve=True)
    n.generators.at["genset", fuel_curve.SLOPE] = slope
    issues = _check_fuel_curves(n)
    if not warns:
        assert issues == []
        return
    (issue,) = issues
    assert (issue.severity, issue.code, issue.component_class, issue.name) == (
        "warning",
        "fuel_curve_slope_low",
        "Generator",
        "genset",
    )
    assert issue.message.startswith(
        f"Fuel curve slope: {slope:g} MWh of fuel per MWh generated means "
    )
    if slope == 0.8:
        assert "means 125 % efficiency." in issue.message
    assert issue_title(issue.code) != issue.code.replace("_", " ")


def test_no_slope_warning_without_a_curve_or_with_an_invalid_one():
    from services.validation_service import _check_fuel_curves

    assert _check_fuel_curves(_network(curve=False)) == []
    assert fuel_curve.slope_warning({"marginal_cost": 5.0}) is None
    # a curve with the slope unset (no-load fuel only) reads slope 0: no warning
    assert (
        fuel_curve.slope_warning({fuel_curve.NO_LOAD: 0.1, fuel_curve.PRICE: 40.0})
        is None
    )
    n = _network(curve=True)
    n.generators.at["genset", fuel_curve.SLOPE] = 0.5
    n.generators.at["genset", "committable"] = (
        False  # refused: the error, not also the warning
    )
    assert [i.code for i in _check_fuel_curves(n)] == ["fuel_curve_invalid"]


# ── 02e: the no-load fuel emits CO2 ───────────────────────────────────────────────────────────────────────────

OFF_HOURS = 6


def _no_load_co2_network(
    *, periods: bool = False, no_load: float = NO_LOAD
) -> pypsa.Network:
    """The committable curve genset on a CO2 fuel at efficiency 0.4; no load in the first hours, so it is off."""
    n = _network(curve=True)
    n.add("Carrier", "diesel", co2_emissions=CO2_T_PER_MWH_TH)
    n.generators.at["genset", "efficiency"] = EFF
    n.generators.at["genset", fuel_curve.NO_LOAD] = no_load
    fuel_curve.apply(n, stamp=False)
    load = pd.Series(LOAD_MW, index=n.snapshots)
    load.iloc[:OFF_HOURS] = 0.0
    n.loads_t.p_set["site"] = load
    if periods:
        n.set_investment_periods([2030, 2035])
        n.investment_period_weightings.loc[:, ["objective", "years"]] = 1.0
    return n


def _expect(co2_price, *, no_load=NO_LOAD, on_hours=HOURS - OFF_HOURS):
    per_on_hour = LOAD_MW * (
        SLOPE * PRICE + co2_price * CO2_T_PER_MWH_TH * SLOPE
    ) + no_load * 1.0 * (PRICE + co2_price * CO2_T_PER_MWH_TH)
    return on_hours * per_on_hour


@pytest.mark.parametrize("periods", [False, True])
def test_a_committed_hour_pays_the_no_load_co2_exactly_once(periods):
    n = _no_load_co2_network(periods=periods)
    if periods:
        prices = {2030: 100.0, 2035: 200.0}
        cfg = SolverConfig(co2_price=100.0, co2_price_per_period=prices)
    else:
        cfg = SolverConfig(co2_price=100.0)
    for _ in range(2):  # re-staging neither drops nor doubles the adder
        status, cond, log = _run_sim(n, cfg)
        assert status in ("ok", "optimal"), (cond, log[-800:])
        on = n.generators_t.status["genset"]
        for part in [g for _, g in on.groupby(level=0)] if periods else [on]:
            assert part.iloc[:OFF_HOURS].sum() == 0 and part.iloc[OFF_HOURS:].min() == 1
        if periods:
            per = on.groupby(level=0).sum()
            expect = sum(_expect(p, on_hours=float(per[y])) for y, p in prices.items())
        else:
            expect = _expect(100.0)
        assert n.objective == pytest.approx(expect, rel=1e-9)
        # the transform is transient
        assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
            NO_LOAD * PRICE
        )
        assert "genset" not in n.generators_t.stand_by_cost.columns
        assert "genset" not in n.generators_t.marginal_cost.columns


@pytest.mark.parametrize("co2_price, no_load", [(0.0, NO_LOAD), (100.0, 0.0)])
def test_no_co2_price_or_no_no_load_term_leaves_the_objective_as_before(
    co2_price, no_load
):
    n = _no_load_co2_network(no_load=no_load)
    status, cond, log = _run_sim(n, SolverConfig(co2_price=co2_price))
    assert status in ("ok", "optimal"), (cond, log[-800:])
    assert n.objective == pytest.approx(_expect(co2_price, no_load=no_load), rel=1e-9)
    assert "no-load fuel" not in log


def _result_df(n, comp, attr, src):
    return getattr(getattr(n, comp), attr)


W = 2.0  # a snapshot weighting other than 1


def test_the_emission_reports_count_the_no_load_co2_per_committed_hour():
    from services.asset_results.compute import build_ctx, gen_co2_total
    from services.compare.emissions import _compute_emissions_summary
    from services.results.emissions import compute_emissions

    n = _no_load_co2_network()
    n.snapshot_weightings.loc[:, :] = (
        W  # every report weights the no-load CO2 like the dispatch
    )
    status, cond, log = _run_sim(n, SolverConfig(co2_price=100.0))
    assert status in ("ok", "optimal"), (cond, log[-800:])
    on_hours = W * (HOURS - OFF_HOURS)
    output_t = (
        on_hours * LOAD_MW * SLOPE * CO2_T_PER_MWH_TH
    )  # the curve's fuel, not 1 / efficiency (H1)
    no_load_t = on_hours * NO_LOAD * 1.0 * CO2_T_PER_MWH_TH
    out = compute_emissions(n, "lopf", result_df=_result_df)
    (row,) = [r for r in out["by_generator"] if r["name"] == "genset"]
    assert row["tCO2"] == pytest.approx(output_t + no_load_t, rel=1e-9)
    assert row["no_load_tCO2"] == pytest.approx(no_load_t, rel=1e-9)
    assert out["total_tCO2"] == pytest.approx(output_t + no_load_t, rel=1e-9)
    cmp = _compute_emissions_summary(n, [], False, True)
    assert cmp.total_kt.total == pytest.approx(
        (output_t + no_load_t) / 1000.0, rel=1e-9
    )
    ctx = build_ctx(n, "Generator", "genset", source="test", sns=n.snapshots)
    assert gen_co2_total(ctx) == pytest.approx(output_t + no_load_t, rel=1e-9)
    # no no-load term: the reports read the output CO2 only, and no extra key
    n2 = _no_load_co2_network(no_load=0.0)
    n2.snapshot_weightings.loc[:, :] = W
    _run_sim(n2, SolverConfig())
    (row2,) = [
        r
        for r in compute_emissions(n2, "lopf", result_df=_result_df)["by_generator"]
        if r["name"] == "genset"
    ]
    assert "no_load_tCO2" not in row2
    assert row2["tCO2"] == pytest.approx(output_t, rel=1e-9)


def test_a_generator_without_a_curve_keeps_its_efficiency_co2_bit_for_bit():
    """GK B H1: only curve gensets move. A plain emitter beside the curve genset keeps co2 / efficiency."""
    from services.results.emissions import compute_emissions

    n = _no_load_co2_network()
    n.add(
        "Generator",
        "plain",
        bus="el",
        carrier="diesel",
        p_nom=0.1,
        efficiency=EFF,
        marginal_cost=-200.0,  # runs whenever there is load, CO2 price included
    )
    assert fuel_curve.fuel_per_mwh(n.generators.loc["plain"], 1.0 / EFF) == 1.0 / EFF
    status, cond, log = _run_sim(n, SolverConfig(co2_price=100.0))
    assert status in ("ok", "optimal"), (cond, log[-800:])
    p_plain = float(n.generators_t.p["plain"].sum())
    assert p_plain > 0
    (row,) = [
        r
        for r in compute_emissions(n, "lopf", result_df=_result_df)["by_generator"]
        if r["name"] == "plain"
    ]
    assert row["intensity_tCO2_per_MWh_out"] == CO2_T_PER_MWH_TH / EFF
    assert row["tCO2"] == pytest.approx(p_plain * CO2_T_PER_MWH_TH / EFF, rel=1e-12)
    assert "no_load_tCO2" not in row


# ── GK B H2: a CO2 cap counts p / efficiency, so a curve genset under one is refused ────────────────────────────


def _add_cap(n):
    n.add(
        "GlobalConstraint",
        "co2_cap",
        type="primary_energy",
        carrier_attribute="co2_emissions",
        sense="<=",
        constant=1e6,
    )
    return n


def test_a_co2_cap_refuses_a_curve_genset_on_an_emitting_carrier():
    from services.validation_service import _check_fuel_curves

    n = _add_cap(_no_load_co2_network())
    (refusal,) = fuel_curve.lp_refusals(n)
    assert refusal.startswith(
        "genset: Fuel curve: the CO2 cap co2_cap counts this generator's CO2 from its efficiency"
    )
    (issue,) = [i for i in _check_fuel_curves(n) if i.code == "fuel_curve_co2_cap"]
    assert (issue.severity, issue.name) == ("error", "genset")
    status, cond, log = _run_sim(n, SolverConfig())
    assert status not in ("ok", "optimal")
    assert "the CO2 cap co2_cap" in log


def test_no_co2_cap_refusal_off_an_emitting_carrier_or_without_a_curve():
    n = _add_cap(_no_load_co2_network())
    n.carriers.at["diesel", "co2_emissions"] = 0.0
    assert fuel_curve.co2_caps(n) == ["co2_cap"] and fuel_curve.lp_refusals(n) == []
    plain = _network(curve=False)
    plain.add("Carrier", "diesel", co2_emissions=CO2_T_PER_MWH_TH)
    assert fuel_curve.lp_refusals(_add_cap(plain)) == []
    assert fuel_curve.co2_caps(_no_load_co2_network()) == []


# ── GK B H3: the no-load CO2 transform is undone bit for bit, also when the apply raises ───────────────────────


def _frames(n):
    return (
        n.generators[["marginal_cost", "stand_by_cost"]].copy(),
        n.generators_t.stand_by_cost.copy(),
        n.generators_t.marginal_cost.copy(),
    )


def _same_frames(a, b):
    return all(x.equals(y) for x, y in zip(a, b))


def _co2_cfg(periods):
    return SolverConfig(
        co2_price=100.0,
        co2_price_per_period={2030: 100.0, 2035: 200.0} if periods else {},
        multi_investment_periods=periods,
    )


@pytest.mark.parametrize("periods", [False, True])
def test_the_no_load_co2_transform_restores_the_frames_bit_for_bit(periods):
    from services.project_context import ProjectContext
    from services.pypsa_service import PyPSAService
    from services.solver.assumptions import _apply_modelling_assumptions

    n = _no_load_co2_network(periods=periods)
    before = _frames(n)
    with PyPSAService.solving_context(ProjectContext(network=n, loaded_project="h3")):
        restore, _ = _apply_modelling_assumptions(
            n, _co2_cfg(periods), lambda msg: None
        )
        if periods:
            sb = n.generators_t.stand_by_cost["genset"]
            assert sb.loc[2035].iloc[0] == pytest.approx(
                NO_LOAD * (PRICE + 200.0 * CO2_T_PER_MWH_TH)
            )
        else:
            assert n.generators.at["genset", "stand_by_cost"] == pytest.approx(
                NO_LOAD * (PRICE + 100.0 * CO2_T_PER_MWH_TH)
            )
        restore()
    assert _same_frames(_frames(n), before)


class _Boom(RuntimeError):
    pass


@pytest.mark.parametrize("periods", [False, True])
def test_a_raise_after_the_no_load_co2_transform_restores_the_frames(periods):
    from services.project_context import ProjectContext
    from services.pypsa_service import PyPSAService
    from services.solver.assumptions import _apply_modelling_assumptions

    n = _no_load_co2_network(periods=periods)
    before = _frames(n)

    def phase(msg):
        if "no-load fuel" in msg:  # logged right after the transform and its undo entry
            raise _Boom(msg)

    with PyPSAService.solving_context(ProjectContext(network=n, loaded_project="h3")):
        with pytest.raises(_Boom):
            _apply_modelling_assumptions(n, _co2_cfg(periods), phase)
    assert _same_frames(_frames(n), before)


def test_the_emission_reports_split_the_no_load_co2_by_period():
    from services.compare.emissions import _compute_emissions_summary
    from services.results.emissions import compute_emissions

    n = _no_load_co2_network(periods=True)
    status, cond, log = _run_sim(n, _co2_cfg(True))
    assert status in ("ok", "optimal"), (cond, log[-800:])
    on = n.generators_t.status["genset"].groupby(level=0).sum()
    out = compute_emissions(n, "lopf", result_df=_result_df)
    cmp = _compute_emissions_summary(n, [2030, 2035], True, True)
    for period in (2030, 2035):
        expect = float(on[period]) * (LOAD_MW * SLOPE + NO_LOAD) * CO2_T_PER_MWH_TH
        (bucket,) = [b for b in out["by_period"] if int(b["period"]) == period]
        (row,) = [r for r in bucket["by_generator"] if r["name"] == "genset"]
        assert row["no_load_tCO2"] == pytest.approx(
            float(on[period]) * NO_LOAD * CO2_T_PER_MWH_TH, rel=1e-9
        )
        assert row["tCO2"] == pytest.approx(expect, rel=1e-9)
        assert cmp.total_kt.by_period[str(period)] == pytest.approx(
            expect / 1000.0, rel=1e-9
        )


def test_no_load_fuel_reads_the_committed_curve_and_its_p_nom():
    row = {**CURVE, "committable": True, "p_nom": 2.0}
    assert fuel_curve.no_load_fuel(row) == pytest.approx(NO_LOAD * 2.0)
    assert fuel_curve.no_load_fuel({**row, "committable": False}) == 0.0
    assert fuel_curve.no_load_fuel({**row, fuel_curve.NO_LOAD: 0.0}) == 0.0
    assert fuel_curve.no_load_fuel({**row, "p_nom": None}) == 0.0
    assert fuel_curve.no_load_fuel({"committable": True, "p_nom": 2.0}) == 0.0
    assert fuel_curve.fuel_per_mwh(row, 2.5) == SLOPE
    assert fuel_curve.fuel_per_mwh({"p_nom": 2.0}, 2.5) == 2.5
    n = _no_load_co2_network()
    assert fuel_curve.no_load_fuel_t(n).empty  # not solved: no status
    n.generators_t.status = pd.DataFrame({"other": 1.0}, index=n.snapshots)
    assert fuel_curve.no_load_fuel_t(
        n
    ).empty  # a status frame without the genset's column


def test_the_co2_cap_check_reads_only_an_emitting_curve_under_a_cap():
    from types import SimpleNamespace

    assert fuel_curve.co2_caps(SimpleNamespace(global_constraints=pd.DataFrame())) == []
    no_attr = pd.DataFrame({"type": ["primary_energy"]}, index=["cap"])
    assert fuel_curve.co2_caps(SimpleNamespace(global_constraints=no_attr)) == []
    n = _add_cap(_no_load_co2_network())
    n.add("Generator", "plain", bus="el", carrier="diesel", p_nom=0.1)
    assert fuel_curve.co2_cap_refusal(n, "plain") is None  # under the cap, but no curve
    n.generators.at["genset", "carrier"] = (
        "unlisted"  # a carrier missing from the table
    )
    assert fuel_curve.co2_cap_refusal(n, "genset") is None
    n.generators.at["genset", "carrier"] = "diesel"
    n.carriers.at["diesel", "co2_emissions"] = float("nan")
    assert fuel_curve.co2_cap_refusal(n, "genset") is None
