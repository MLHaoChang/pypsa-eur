"""The hourly chronological year as a staged-template time base (grid-to-process 09, design §2.4 and §4.2, D4).

`eh_site.stage_network(time_base="year")` and the inventory-cadence validator rule, on a small two-period network
with a 21-day delivery cycle, plus the week-assuming code paths the issue's audit names, each run on the year base.
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_site as S  # noqa: E402

YEARS = [2030, 2035]
CYCLE_H = 504                       # one vessel every 21 days
SHIP_H = 24                         # the load-out lasts a day, at the cycle's end
MILL_MW = 10.0
SHIPMENT = MILL_MW * CYCLE_H * 0.9  # 90 % of what the mill can make in a cycle


def _flat(snapshots, delivery, interval_h: float | None = CYCLE_H) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(snapshots)
    n.add("Bus", "elec", carrier="AC")
    n.add("Bus", "conc", carrier="concentrate")
    n.buses["eh_poc"] = n.buses.index == "elec"     # the PCC, for the campus study
    n.add("Generator", "grid", bus="elec", carrier="grid", p_nom=50.0, marginal_cost=50.0)
    n.add("Link", "mill", bus0="elec", bus1="conc", p_nom=MILL_MW, efficiency=1.0)
    n.add("Load", "shipments", bus="conc", p_set=pd.Series(delivery, index=n.snapshots))
    n.add("Store", "shed", bus="conc", e_nom_extendable=True, capital_cost=1.0)
    if interval_h is not None:
        n.stores["delivery_interval_h"] = float(interval_h)
    return n


def _stage(n, time_base):
    return S.stage_network(n, YEARS, load_scale={y: {"shipments": 1.0} for y in YEARS}, energization={},
                           weights={2030: 5, 2035: 5}, existing=(), candidates=lambda net, y: None,
                           time_base=time_base)


def _shipping_year(interval_h=CYCLE_H) -> pypsa.Network:
    delivery = np.zeros(8760)
    for start in range(CYCLE_H - SHIP_H, 8760 - SHIP_H + 1, CYCLE_H):
        delivery[start:start + SHIP_H] = SHIPMENT / SHIP_H
    return _stage(_flat(pd.date_range("2030-01-01", periods=8760, freq="h"), delivery, interval_h), "year")


def _week(interval_h=CYCLE_H) -> pypsa.Network:
    return _stage(_flat(S.SNAPSHOTS, np.full(S.HOURS, SHIPMENT / CYCLE_H), interval_h), "week")


@pytest.fixture(scope="module")
def year():
    return _shipping_year()


# ── stage_network on the year base ──────────────────────────────────────────

def test_the_year_base_is_8760_hours_per_period_every_weighting_one(year):
    assert len(year.snapshots) == 2 * 8760
    for y in YEARS:
        assert len(year.snapshots[year.snapshots.get_level_values(0) == y]) == 8760
    assert (year.snapshot_weightings[["objective", "generators", "stores"]] == 1.0).all().all()
    assert year.investment_period_weightings["years"].tolist() == [5.0, 5.0]
    assert year.investment_period_weightings["objective"].tolist() == [5.0, 5.0]
    assert bool(year.stores.at["shed", "e_cyclic_per_period"]) and bool(year.stores.at["shed", "e_cyclic"])
    assert float(year.nyears.loc[2030]) == pytest.approx(1.0)


def test_the_week_base_is_unchanged_by_default():
    n = _week()
    assert len(n.snapshots) == 2 * S.HOURS
    assert (n.snapshot_weightings["objective"] == S.WEIGHT).all()
    assert (n.snapshot_weightings["stores"] == 1.0).all()


@pytest.mark.parametrize(("snaps", "match"), [
    (pd.date_range("2028-01-01", periods=8784, freq="h"), "the year 2028 has 8,784 hourly snapshots"),
    (pd.date_range("2030-01-01", periods=8760, freq="30min"), "consecutive hourly snapshots"),
    (pd.date_range("2027-07-01", periods=8760, freq="h"), "include 29 February"),
    (pd.date_range("2030-01-01", periods=168, freq="h"), "needs 8,760 hourly snapshots, the network has 168"),
])
def test_the_year_base_refuses_a_leap_year_and_any_step_but_an_hour(snaps, match):
    with pytest.raises(ValueError, match=match):
        _stage(_flat(snaps, np.zeros(len(snaps))), "year")


def test_an_unknown_time_base_is_refused():
    with pytest.raises(ValueError, match="time_base: 'month'"):
        _stage(_flat(S.SNAPSHOTS, np.zeros(S.HOURS)), "month")


# ── Acceptance: the 21-day shed carries across shipments ─────────────────────

@pytest.fixture(scope="module")
def solved(year):
    n = year.copy()
    n.snapshots.name = "snapshot"   # as the app's solve path (PyPSA's MultiIndex level names clash otherwise)
    status, cond = n.optimize(solver_name="highs", multi_investment_periods=True)
    assert (status, cond) == ("ok", "optimal")
    return n


@pytest.mark.live_solve
def test_a_21_day_store_fills_between_shipments_and_drains_at_each(solved):
    n = solved
    for y in YEARS:
        e = n.stores_t.e["shed"].loc[y].to_numpy()
        ships = range(CYCLE_H - SHIP_H, 8760 - SHIP_H + 1, CYCLE_H)
        for start in ships:
            # before each vessel the shed holds what the load-out day's milling cannot cover
            assert e[start - 1] >= SHIPMENT - MILL_MW * SHIP_H - 1e-6
            assert e[start + SHIP_H - 1] <= e[start - 1] - (SHIPMENT - MILL_MW * SHIP_H) + 1e-6
        for a, b in zip(list(ships)[:-1], list(ships)[1:]):
            # the level carries from one shipment to the next: it rebuilds over the 20 days between
            assert e[b - 1] - e[a + SHIP_H - 1] >= SHIPMENT - MILL_MW * SHIP_H - 1e-6
        assert n.stores.at["shed", "e_nom_opt"] >= SHIPMENT - MILL_MW * SHIP_H - 1e-6


@pytest.mark.live_solve
def test_the_year_mass_balance_closes_per_period(solved):
    n = solved
    for y in YEARS:
        made = float(n.links_t.p0["mill"].loc[y].sum())
        shipped = float(n.loads_t.p["shipments"].loc[y].sum())
        assert made - shipped == pytest.approx(0.0, abs=1e-6)   # cyclic over the year: Δ shed = 0


# ── Acceptance: the validator refuses the same store on the week base ────────

def _cadence(n):
    from services.validation_service import _check_inventory_cadence

    return _check_inventory_cadence(n)


def test_the_week_base_refuses_a_21_day_store_and_names_it():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    (issue,) = _cadence(_week())
    assert (issue.code, issue.component_class, issue.name) == ("inventory_cadence_needs_year", "Store", "shed")
    assert "Inventory shed" in issue.message
    assert "504 h" in issue.message and "168 hours in a row" in issue.message
    assert "chronological year time base" in issue.message
    assert issue.severity == "error"
    codes = [(i.code, i.name) for i in validate_for_run(_week(), SolverConfig(multi_investment_periods=True))]
    assert ("inventory_cadence_needs_year", "shed") in codes


@pytest.mark.parametrize("interval_h", [0.0, 24.0, 168.0, None])
def test_the_week_base_holds_a_continuous_or_at_most_weekly_store(interval_h):
    assert _cadence(_week(interval_h)) == []


def test_the_year_base_never_fires(year):
    assert _cadence(year) == []


def test_a_network_weighted_one_per_hour_never_fires():
    n = _flat(pd.date_range("2030-01-01", periods=24, freq="h"), np.zeros(24), interval_h=CYCLE_H)
    assert _cadence(n) == []


def test_a_project_without_the_column_is_unaffected():
    n = _week(None)
    assert "delivery_interval_h" not in n.stores.columns
    assert _cadence(n) == []


def test_the_column_is_a_registered_process_column():
    from services.adequacy.eh_columns import coerce_eh_value, eh_columns_for

    assert eh_columns_for("Store")["delivery_interval_h"] == "float_nonneg"
    assert coerce_eh_value("Store", "delivery_interval_h", "504") == 504.0
    with pytest.raises(ValueError):
        coerce_eh_value("Store", "delivery_interval_h", -1)


# ── The audit (issue item 3): each week-assuming path on the year base ───────

def _lf_cfg(**kw):
    return SimpleNamespace(**{"lf_aggregate_future": True, "lf_k_periods": 8, "lf_period_length_h": 168,
                              "lf_cluster_method": "hierarchical", "lf_include_extreme": True, **kw})


def test_rolling_is_refused_under_the_year_plan(year):
    from services.solver_service import SolverConfig
    from services.validation_service import _check_rolling_horizon

    cfg = SolverConfig(multi_investment_periods=True, solve_strategy="rolling")
    assert "rolling_with_multi_period" in [i.code for i in _check_rolling_horizon(year, cfg)]


def test_the_nyears_check_passes_on_the_year_base(year):
    from services.validation_service import _check_snapshot_weights_nyears

    assert _check_snapshot_weights_nyears(year) == []


def test_limited_foresight_clusters_a_year_period_into_weeks_that_keep_its_hours(year):
    pytest.importorskip("tsam")
    from services.time_aggregation_service import aggregate_period_snapshots

    r = aggregate_period_snapshots(year, 2035, _lf_cfg())
    assert len(r.snapshots) < 8760 and len(r.snapshots) % 168 == 0
    assert set(r.snapshots.get_level_values(0)) == {2035}
    assert float(r.weights.sum()) == pytest.approx(8760.0, abs=1e-6)


def test_a_myopic_iteration_keeps_the_current_year_whole(year):
    pytest.importorskip("tsam")
    from services.solver.myopic import _build_iteration_snapshots

    snaps, _over = _build_iteration_snapshots(year, 2030, YEARS, _lf_cfg())
    assert (snaps.get_level_values(0) == 2030).sum() == 8760
    assert 0 < (snaps.get_level_values(0) == 2035).sum() < 8760


def test_profile_shapes_template_spans_the_year(year):
    from services.profile_shapes import _template_snapshots

    idx = _template_snapshots(year)[0]
    assert len(idx) == 8760 and pd.Timestamp(idx[0]) == pd.Timestamp("2030-01-01")


def test_a_new_period_on_the_year_axis_keeps_its_hours_and_weights(client, install_network, session_ctx):
    install_network(_shipping_year())
    r = client.post("/api/network/investment_periods", json={"periods": [*YEARS, 2040]})
    assert r.status_code == 200, r.text
    n = session_ctx(client).network
    assert list(n.investment_periods) == [*YEARS, 2040]
    assert (n.snapshots.get_level_values(0) == 2040).sum() == 8760
    assert (n.snapshot_weightings[["objective", "generators", "stores"]] == 1.0).all().all()
    snaps = client.get("/api/network/snapshots").json()
    assert snaps["freq"] == "h"


@pytest.mark.live_solve
def test_the_campus_tables_and_ranking_take_a_year_per_period(solved):
    from gridspine.producers.campus import campus_hourly, draft_campus
    from gridspine.ranking.campus import campus_metrics, select_campus_hours

    spec = draft_campus(solved).spec
    hourly, pcc = campus_hourly(solved, spec)
    assert len(pcc) == len(YEARS) * 8760 and int(hourly["hour"].max()) == 8759
    metrics = campus_metrics(hourly, pcc, spec)
    assert metrics.index.is_unique
    picked = select_campus_hours(metrics, k=3)
    assert picked.groupby("period").size().le(3 * len(metrics.columns)).all()
    assert set(picked["period"]) <= set(YEARS)
