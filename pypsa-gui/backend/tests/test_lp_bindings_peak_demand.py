"""
Peak-demand charges in the LP (Edge Investment Case P1 WP1.5a).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.5a
Spike: docs/superpowers/findings/2026-09-27-ic-p1-linopy-spike.md (GO).
Spec §5 table "Monthly peak demand charge", §5.2 (partial coverage).

A `demand` item adds one dash-less `ic_peak_import` variable per (item, period
window, billing month) present in the snapshots. Each snapshot's import is
bounded by the peak of the window it falls in (first-match periods, as in
the billing engine), and the objective adds `Σ w_obj · rate · peak`. There are
no snapshot weights: a demand charge is per month, not per interval. The solved
peaks are committed (`n.meta["ic_demand_peaks"]`) only after a successful
solve, and they reach `cost_breakdown` as `demand_charge` rows (gap 0) and
`last_commercial_terms`. A month inside the horizon with no snapshots gets no
variable and is reported as not established.
"""
from __future__ import annotations

import queue
import threading
from datetime import date

import numpy as np
import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial import lp_bindings as L
from services.commercial.tariff_engine import rate as engine_rate
from tests.fixtures.investment_case import edge_15min as F

RATE = 15.0  # €/kW-month


def _tariff(*items) -> Tariff:
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "US",
                                  "valid_from": "2030-01-01", "items": list(items)})


def _demand(item_id="demand", rate=RATE, **kw):
    return {"id": item_id, "kind": "demand", "unit": "per_kw_month",
            "periods": kw.pop("periods", [{"name": "all", "rate": rate}]),
            "measured_on": "import", **kw}


def _commercial(tariff):
    return {"poc_link": "import", "import_tariff": tariff.model_dump(mode="json")}


def _site(start=F.START):
    old = F.START
    F.START = start
    try:
        n = F.build_edge_15min()
    finally:
        F.START = old
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    return n


# ── apply (no solve) ───────────────────────────────────────────────────────


def test_one_peak_key_per_month_present():
    n = _site(start="2030-01-28 00:00")  # a week across January and February
    applied = L.materialise_poc_prices(n, _commercial(_tariff(_demand())))
    keys = getattr(n, L.DEMAND_SPEC_ATTR)["keys"]
    assert sorted(k["month"] for k in keys) == ["2030-01", "2030-02"]
    assert all(k["eur_per_mw"] == RATE * 1000 for k in keys)
    assert applied.facts["demand_items"] == ["demand"]
    applied.undo()
    assert not hasattr(n, L.DEMAND_SPEC_ATTR)


def test_a_month_without_snapshots_is_not_established():
    n = _site()
    jan = pd.date_range("2030-01-07", periods=96, freq="15min")
    mar = pd.date_range("2030-03-04", periods=96, freq="15min")
    n.set_snapshots(jan.append(mar))
    n.snapshot_weightings.loc[:, :] = 0.25
    n.loads_t.p_set = pd.DataFrame({"site_load": 30.0}, index=n.snapshots)
    n.generators_t.p_max_pu = pd.DataFrame({"pv": 0.0}, index=n.snapshots)
    applied = L.materialise_poc_prices(n, _commercial(_tariff(_demand())))
    assert applied.facts["demand_not_established_months"] == ["2030-02"]
    assert sorted(k["month"] for k in getattr(n, L.DEMAND_SPEC_ATTR)["keys"]) == [
        "2030-01", "2030-03"]


def test_a_ratcheted_item_is_a_demand_item_since_wp1_5b():
    n = _site()
    item = _demand(ratchet={"lookback_months": 11, "share": 0.8})
    applied = L.materialise_poc_prices(n, _commercial(_tariff(item)))
    assert applied.facts["not_in_lp"] == {}
    assert applied.facts["demand_items"] == ["demand"]
    assert getattr(n, L.DEMAND_SPEC_ATTR)["keys"]


def test_demand_with_windowed_dispatch_is_refused():
    n = _site()
    with pytest.raises(L.CommercialBindingError, match="rolling"):
        L.materialise_poc_prices(n, _commercial(_tariff(_demand())), solve_strategy="rolling")


# ── through run_simulation ─────────────────────────────────────────────────


def _solve(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    sink: dict = {}
    cfg = SolverConfig(commercial=commercial)
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(),
                                       state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal"), (status, condition)
    return sink, cfg


def _evening_peak(n):
    p0 = n.links_t.p0["import"]
    hour = pd.DatetimeIndex(n.snapshots).hour
    return float(p0[(hour >= 17) & (hour < 21)].max())


@pytest.mark.live_solve
def test_the_demand_charge_shaves_the_evening_peak():
    plain = _site()
    _solve(plain, None)
    charged = _site()
    _solve(charged, _commercial(_tariff(_demand())))
    assert _evening_peak(charged) < 0.99 * _evening_peak(plain)


@pytest.mark.live_solve
def test_lp_demand_cost_equals_the_engine_on_the_same_dispatch():
    n = _site()
    _solve(n, _commercial(_tariff(_demand())))
    peaks = n.meta[L.META_DEMAND]
    lp_cost = sum(v["eur_per_mw"] * v["peak_mw"] for v in peaks.values())
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                            index=n.snapshots)
    billed = engine_rate(dispatch, _tariff(_demand()), step_hours=0.25, timezone=None)
    assert billed.per_item["demand"] == pytest.approx(lp_cost, rel=1e-6)


@pytest.mark.live_solve
def test_the_solved_peak_is_the_monthly_max_import():
    n = _site(start="2030-01-28 00:00")
    _solve(n, _commercial(_tariff(_demand())))
    peaks = n.meta[L.META_DEMAND]
    months = pd.DatetimeIndex(n.snapshots).strftime("%Y-%m")
    p0 = n.links_t.p0["import"].groupby(np.asarray(months)).max()
    for v in peaks.values():
        assert v["peak_mw"] == pytest.approx(p0[v["month"]], abs=1e-6)


@pytest.mark.live_solve
def test_gap_is_zero_and_the_demand_rows_are_reported():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _site()
    sink, cfg = _solve(n, _commercial(_tariff(_demand())))
    cb = compute_cost_breakdown(n, cfg)
    assert abs(compute_objective_decomposition(n, cb)["gap_pct"]) < 1e-6
    peaks = n.meta[L.META_DEMAND]
    assert cb["commercial"]["demand_charge"] == pytest.approx(
        sum(v["eur_per_mw"] * v["peak_mw"] for v in peaks.values()), rel=1e-9)
    assert sink["last_commercial_terms"]["demand_peaks"] == peaks


@pytest.mark.live_solve
def test_a_plain_solve_clears_the_commercial_terms():
    n = _site()
    _solve(n, _commercial(_tariff(_demand())))
    sink, _ = _solve(n, None)
    assert sink["last_commercial_terms"] is None
    assert L.META_DEMAND not in n.meta


@pytest.mark.live_solve
def test_demand_rows_survive_a_netcdf_round_trip(tmp_path):
    import pypsa

    from services.results.cost_breakdown import compute_cost_breakdown

    n = _site()
    _, cfg = _solve(n, _commercial(_tariff(_demand())))
    before = compute_cost_breakdown(n, cfg)["commercial"]["demand_charge"]
    path = tmp_path / "n.nc"
    n.export_to_netcdf(path)
    after = compute_cost_breakdown(pypsa.Network(path), cfg)["commercial"]["demand_charge"]
    assert after == pytest.approx(before, rel=1e-12)


# ── WP1.5a review round 1 ──────────────────────────────────────────────────


def _rep_weeks():
    """Four representative weeks (Jan, Apr, Jul, Oct) weighted to a full year."""
    n = _site()
    parts = [pd.date_range(f"2030-{m:02d}-07", periods=96 * 7, freq="15min") for m in (1, 4, 7, 10)]
    idx = parts[0].append(parts[1:])
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 8760.0 / len(idx)
    n.loads_t.p_set = pd.DataFrame({"site_load": 30.0}, index=idx)
    n.generators_t.p_max_pu = pd.DataFrame({"pv": 0.0}, index=idx)
    return n


def test_a_year_represented_by_weeks_lists_every_unsampled_month():
    """#2: the span is the represented calendar year, not first..last snapshot."""
    applied = L.materialise_poc_prices(_rep_weeks(), _commercial(_tariff(_demand())))
    assert applied.facts["demand_not_established_months"] == [
        "2030-02", "2030-03", "2030-05", "2030-06", "2030-08", "2030-09", "2030-11", "2030-12"]


def test_partial_months_are_disclosed():
    """#4: a full monthly charge against part of a month's energy is disclosed."""
    applied = L.materialise_poc_prices(_site(start="2030-01-28 00:00"),
                                       _commercial(_tariff(_demand())))
    assert applied.facts["demand_partial_months"] == ["2030-01", "2030-02"]


def test_demand_rows_flag_config_drift_and_missing_commits():
    """#3 ADR-0001: rows say when the config and the committed peaks disagree."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial(_tariff(_demand()))
    # The config names a demand item, but no solve committed peaks.
    terms = commercial_cost_terms(n, commercial)
    assert terms["block"]["demand_charge"] is None
    assert "demand_charge_not_established" in terms["flags"]
    # Peaks committed for a demand item the config no longer names.
    n.meta[L.META_DEMAND] = {"demand|0||2030-01": {
        "item": "demand", "period": "all", "month": "2030-01", "inv_period": None,
        "eur_per_mw": 15000.0, "peak_mw": 10.0, "billed_mw": 10.0}}
    n.meta[L.META_DEMAND_INFO] = {"items": ["demand"], "not_established": ["2030-02"],
                                  "partial_months": ["2030-01"]}
    energy_only = _commercial(_tariff({"id": "e", "kind": "energy", "unit": "per_kwh",
                                       "periods": [{"name": "all", "rate": 0.1}]}))
    terms = commercial_cost_terms(n, energy_only)
    assert "config_changed_since_solve" in terms["flags"]
    terms = commercial_cost_terms(n, commercial)
    assert "demand_months_not_established" in terms["flags"]
    assert "demand_partial_months" in terms["flags"]


@pytest.mark.live_solve
def test_terms_are_published_only_after_a_successful_solve():
    """#6: a failed solve must not replace the published terms."""
    n = _site()
    sink, _ = _solve(n, _commercial(_tariff(_demand())))
    good = sink["last_commercial_terms"]
    assert good["demand_peaks"]
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    n.loads_t.p_set["site_load"] = 10_000.0  # infeasible without slack
    sink2: dict = {"last_commercial_terms": good}
    status, _ = run_simulation(SolverConfig(commercial=_commercial(_tariff(_demand()))), n,
                               PyPSAService.get_lock(), threading.Event(), queue.SimpleQueue(),
                               state_update=lambda **kw: sink2.update(kw))
    assert status not in ("ok", "optimal")
    assert sink2["last_commercial_terms"] is good


def test_rolling_is_refused_only_when_it_would_run():
    """#7: rolling on a multi-period axis falls back to full; not refused."""
    n = _site()
    L.materialise_poc_prices(n, _commercial(_tariff(_demand())), solve_strategy="full")
    with pytest.raises(L.CommercialBindingError):
        L.materialise_poc_prices(n, _commercial(_tariff(_demand())), solve_strategy="rolling")


@pytest.mark.live_solve
def test_an_hourly_demand_interval_is_bounded_on_hourly_means_and_matches_the_bill():
    """#5 LP side: the bound is on the interval mean, as billed."""
    n = _site()
    item = _demand(settlement="h")
    _solve(n, _commercial(_tariff(item)))
    peaks = n.meta[L.META_DEMAND]
    lp = sum(v["eur_per_mw"] * v["billed_mw"] for v in peaks.values())
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                            index=n.snapshots)
    billed = engine_rate(dispatch, _tariff(item), step_hours=0.25, timezone=None)
    assert billed.per_item["demand"] == pytest.approx(lp, rel=1e-6)


# ── WP1.5a review round 2 ──────────────────────────────────────────────────


def _utc_year(freq="h"):
    n = _site()
    idx = pd.date_range("2030-01-01", "2030-12-31 23:00", freq=freq)
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 1.0
    n.loads_t.p_set = pd.DataFrame({"site_load": 30.0}, index=idx)
    n.generators_t.p_max_pu = pd.DataFrame({"pv": 0.0}, index=idx)
    return n


@pytest.mark.parametrize("tz", ["America/New_York", "Europe/Berlin"])
def test_a_full_year_across_the_fall_back_hour_binds(tz):
    """#1: an hourly-settlement demand item on a full UTC year in a DST zone."""
    n = _utc_year()
    applied = L.materialise_poc_prices(n, {**_commercial(_tariff(_demand(settlement="h"))),
                                           "timezone": tz})
    assert applied.facts["demand_items"] == ["demand"]


@pytest.mark.parametrize("tz,edge", [("America/Bogota", "2029-12"), ("Asia/Tokyo", "2031-01")])
def test_a_full_utc_year_in_another_zone_bills_its_edges_as_partial(tz, edge):
    """#2: no bogus not-established months; the edge month is disclosed partial."""
    n = _utc_year()
    applied = L.materialise_poc_prices(n, {**_commercial(_tariff(_demand())), "timezone": tz})
    assert applied.facts["demand_not_established_months"] == []
    assert edge in applied.facts["demand_partial_months"]


def test_meter_history_seeds_only_the_first_investment_period():
    """#3: a 2029 meter reading must not floor the 2040 period."""
    n = _site()
    n.set_investment_periods([2030, 2040])
    item = _demand(ratchet={"lookback_months": 11, "share": 0.9})
    applied = L.materialise_poc_prices(n, {**_commercial(_tariff(item)),
                                           "meter_history_peaks_kw": {"2029-12": 60_000.0}})
    spec = getattr(n, L.DEMAND_SPEC_ATTR)
    seeded = {r["key"].split("|")[2] for r in spec["ratchets"] if "floor_mw" in r}
    assert seeded == {"2030"}
    assert "ratchet_seed_missing" in applied.facts["notes"]


def test_the_ratchet_seed_gap_reaches_the_rows():
    """#4: a lower-bound demand row says so, after a reload too."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    item = _demand(ratchet={"lookback_months": 11, "share": 0.9})
    commercial = _commercial(_tariff(item))
    n.meta[L.META_DEMAND] = {"k": {"item": "demand", "period": "all", "month": "2030-01",
                                   "inv_period": None, "eur_per_mw": 15000.0, "peak_mw": 1.0,
                                   "billed_mw": 1.0}}
    applied = L.materialise_poc_prices(n, commercial)
    n.meta[L.META_DEMAND_INFO] = getattr(n, L.DEMAND_SPEC_ATTR)["info"]
    applied.undo()
    assert "ratchet_seed_missing" in commercial_cost_terms(n, commercial)["flags"]


def test_a_rate_change_after_the_solve_is_config_drift():
    """#5: the drift check compares the demand items' content, not only ids."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(_tariff(_demand())))
    n.meta[L.META_DEMAND] = {"k": {"item": "demand", "period": "all", "month": "2030-01",
                                   "inv_period": None, "eur_per_mw": 15000.0, "peak_mw": 1.0,
                                   "billed_mw": 1.0}}
    n.meta[L.META_DEMAND_INFO] = getattr(n, L.DEMAND_SPEC_ATTR)["info"]
    applied.undo()
    assert "config_changed_since_solve" not in commercial_cost_terms(
        n, _commercial(_tariff(_demand())))["flags"]
    assert "config_changed_since_solve" in commercial_cost_terms(
        n, _commercial(_tariff(_demand(rate=40.0))))["flags"]


# ── WP1.5a review round 3 ──────────────────────────────────────────────────


def test_engine_nets_within_the_interval_before_clipping():
    """#1: a net interval meter reads the interval's net energy, so an export
    quarter offsets the importing quarters of the same hour."""
    idx = pd.date_range("2030-01-07 18:00", periods=4, freq="15min")
    df = pd.DataFrame({"import_mw": [20.0, 20.0, 20.0, 0.0],
                       "export_mw": [0.0, 0.0, 0.0, 20.0]}, index=idx)
    t = _tariff({**_demand(measured_on="net"), "settlement": "h"})
    res = engine_rate(df, t, step_hours=0.25, timezone=None)
    assert res.per_item["demand"] == pytest.approx(RATE * 10_000.0)  # mean net 10 MW


@pytest.mark.live_solve
def test_net_demand_on_hourly_settlement_matches_the_engine():
    """#1 end to end: 15-min steps, hourly net settlement, an export Link."""
    n = _site()
    n.generators.loc["pv", "p_nom"] = 90.0
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC", marginal_cost=0.5)
    n.generators.loc["grid_supply", "p_min_pu"] = -1.0
    item = {**_demand(measured_on="net"), "settlement": "h"}
    commercial = {**_commercial(_tariff(item)), "export_link": "export"}
    _solve(n, commercial)
    lp = sum(v["eur_per_mw"] * v["billed_mw"] for v in n.meta[L.META_DEMAND].values())
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(),
                             "export_mw": n.links_t.p0["export"].to_numpy()}, index=n.snapshots)
    billed = engine_rate(dispatch, _tariff(item), step_hours=0.25, timezone=None)
    assert lp == pytest.approx(billed.per_item["demand"], rel=1e-6)


def _seasonal():
    """June priced, a free catch-all window; a May-June axis."""
    return _demand(periods=[{"name": "summer", "rate": 10.0, "months": [6]},
                            {"name": "off", "rate": 0.0}],
                   ratchet={"lookback_months": 1, "share": 0.9})


def test_a_month_in_the_dispatch_is_modelled_whatever_its_window_rates():
    """#2: May is in the dispatch (its only window is free), so June's
    lookback does NOT reach for May's meter history, in the LP and the engine."""
    n = _site(start="2030-05-28 00:00")
    history = {"2030-05": 60_000.0, "2030-04": 60_000.0}
    applied = L.materialise_poc_prices(n, {**_commercial(_tariff(_seasonal())),
                                           "meter_history_peaks_kw": history})
    assert getattr(n, L.DEMAND_SPEC_ATTR)["ratchets"] == []
    assert "ratchet_seed_missing" not in applied.facts["notes"]
    dispatch = pd.DataFrame({"import_mw": np.full(len(n.snapshots), 30.0), "export_mw": 0.0},
                            index=n.snapshots)
    res = engine_rate(dispatch, _tariff(_seasonal()), step_hours=0.25, timezone=None,
                      meter_history=history)
    june = res.demand_lines[res.demand_lines["month"] == "2030-06"]
    assert (june["billed_kw"] == june["peak_kw"]).all()
    assert "ratchet_seed_missing" not in (res.notes.get("demand") or [])


def test_a_free_window_needs_no_ratchet_seed_in_the_engine():
    """#2: a zero-rate window bills nothing, so its unknown lookback is no gap."""
    n = _site(start="2030-05-28 00:00")
    dispatch = pd.DataFrame({"import_mw": np.full(len(n.snapshots), 30.0), "export_mw": 0.0},
                            index=n.snapshots)
    res = engine_rate(dispatch, _tariff(_seasonal()), step_hours=0.25, timezone=None)
    assert "ratchet_seed_missing" not in (res.notes.get("demand") or [])


@pytest.mark.live_solve
def test_the_reported_peak_is_the_actual_peak_when_a_ratchet_binds():
    """#3: `peak` is free between the actual peak and the billed demand when
    the ratchet binds; the report reads the dispatch, not the free variable."""
    n = _site()
    item = _demand(ratchet={"lookback_months": 1, "share": 1.0})
    _solve(n, {**_commercial(_tariff(item)), "meter_history_peaks_kw": {"2029-12": 200_000.0}})
    (v,) = n.meta[L.META_DEMAND].values()
    assert v["billed_mw"] == pytest.approx(200.0)
    assert v["peak_mw"] == pytest.approx(float(n.links_t.p0["import"].max()), abs=1e-6)


@pytest.mark.parametrize("change", [{"timezone": "Europe/Berlin"},
                                    {"meter_history_peaks_kw": {"2029-12": 1.0}},
                                    {"initial_peak_lower_bound": {"2030-01": 5.0}}])
def test_a_metering_change_after_the_solve_is_config_drift(change):
    """#4: timezone, meter history and peak floors change what is billed."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    base = _commercial(_tariff(_demand()))
    applied = L.materialise_poc_prices(n, base)
    n.meta[L.META_DEMAND] = {"k": {"item": "demand", "period": "all", "month": "2030-01",
                                   "inv_period": None, "eur_per_mw": 15000.0, "peak_mw": 1.0,
                                   "billed_mw": 1.0}}
    n.meta[L.META_DEMAND_INFO] = getattr(n, L.DEMAND_SPEC_ATTR)["info"]
    applied.undo()
    assert "config_changed_since_solve" not in commercial_cost_terms(n, base)["flags"]
    assert "config_changed_since_solve" in commercial_cost_terms(n, {**base, **change})["flags"]


def test_a_negative_demand_rate_is_refused():
    """#5: it would make the LP unbounded."""
    with pytest.raises(L.CommercialBindingError, match="negative"):
        L.materialise_poc_prices(_site(), _commercial(_tariff(_demand(rate=-5.0))))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_rate_is_refused_by_the_model(bad):
    """Round 4: NaN < 0 is False, so the sign check alone let a NaN demand
    rate through and the row came out NaN (ADR-0001)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _tariff(_demand(rate=bad))
    with pytest.raises(ValidationError):
        _tariff({"id": "e", "kind": "energy", "unit": "per_kwh",
                 "periods": [{"name": "all", "rate": bad}]})


@pytest.mark.live_solve
def test_a_zero_weight_snapshot_does_not_zero_the_reported_peak():
    """WP1.6/1.7 round 3: an interval weighing nothing has no mean; it must
    not turn the reported actual peak into 0.0 (ADR-0001)."""
    n = _site()
    n.snapshot_weightings.iloc[5] = 0.0
    _solve(n, _commercial(_tariff(_demand())))
    (v,) = n.meta[L.META_DEMAND].values()
    p0 = n.links_t.p0["import"]
    expected = float(p0[n.snapshot_weightings.objective > 0].max())
    assert v["peak_mw"] == pytest.approx(expected, abs=1e-6)
    assert v["peak_mw"] > 0


# ── P2 WP2.1a-0: demand windows keyed by period NAME ───────────────────────
# A URDB demand period becomes one TariffPeriod per [start, end) fragment; the
# fragments of one name are ONE demand window, billed on one monthly peak.

def _split_peak(rate=RATE, *, evening_rate=None):
    return _demand(periods=[
        {"name": "peak", "rate": rate, "start_hour": 12, "end_hour": 14},
        {"name": "peak", "rate": rate if evening_rate is None else evening_rate,
         "start_hour": 17, "end_hour": 19},
        {"name": "off", "rate": 0.0}])


def test_engine_bills_one_peak_for_a_split_peak_window():
    idx = pd.date_range("2030-01-07", periods=24, freq="h")
    load = np.full(24, 5.0)
    load[13], load[18] = 20.0, 30.0            # both fragments of "peak"
    df = pd.DataFrame({"import_mw": load, "export_mw": 0.0}, index=idx)
    res = engine_rate(df, _tariff({**_split_peak(), "settlement": "h"}), step_hours=1.0,
                      timezone=None)
    lines = res.demand_lines[res.demand_lines["period"] == "peak"]
    assert len(lines) == 1                     # one window, not one per fragment
    assert lines["peak_kw"].iloc[0] == pytest.approx(30_000.0)
    assert res.per_item["demand"] == pytest.approx(RATE * 30_000.0)


def test_same_name_fragments_with_different_rates_in_one_month_are_refused():
    with pytest.raises(ValueError, match="peak"):
        _tariff(_split_peak(evening_rate=RATE * 2))


def test_same_name_windows_in_disjoint_months_may_differ_in_rate():
    """A summer "peak" and a winter "peak" at different rates stay valid."""
    item = _demand(periods=[
        {"name": "peak", "rate": 20.0, "months": [6, 7, 8], "start_hour": 17, "end_hour": 21},
        {"name": "peak", "rate": 12.0, "months": [1, 2, 12], "start_hour": 17, "end_hour": 21},
        {"name": "off", "rate": 0.0}])
    _tariff(item)  # validates


def test_lp_keys_one_window_per_name_and_uses_the_name():
    n = _site()
    L.materialise_poc_prices(n, _commercial(_tariff({**_split_peak(), "settlement": "h"})))
    keys = [k for k in getattr(n, L.DEMAND_SPEC_ATTR)["keys"] if k["period"] == "peak"]
    months = {k["month"] for k in keys}
    assert len(keys) == len(months)            # one key per month for the named window
    assert all("|peak|" in k["key"] for k in keys)


@pytest.mark.live_solve
def test_lp_split_peak_charge_equals_the_engine():
    n = _site()
    tariff = _tariff({**_split_peak(), "settlement": "h"})
    _solve(n, _commercial(tariff))
    lp = sum(v["eur_per_mw"] * v["billed_mw"] for v in n.meta[L.META_DEMAND].values())
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                            index=n.snapshots)
    assert lp == pytest.approx(engine_rate(dispatch, tariff, step_hours=0.25,
                                           timezone=None).per_item["demand"], rel=1e-6)


def test_renaming_a_fragment_is_demand_drift():
    """The windows follow the names, so the items hash covers them."""
    n = _site()
    a = L.demand_hash(n, L._parse(_commercial(_tariff(_split_peak()))),
                      _tariff(_split_peak()).items)
    renamed = _split_peak()
    renamed["periods"][1]["name"] = "evening"
    b = L.demand_hash(n, L._parse(_commercial(_tariff(renamed))), _tariff(renamed).items)
    assert a != b


def test_p1_position_keyed_records_still_produce_rows():
    """Upgrade path: rows read eur_per_mw and billed_mw, never the key format."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial(_tariff(_demand()))
    applied = L.materialise_poc_prices(n, commercial)
    n.meta[L.META_DEMAND] = {"demand|0||2030-01": {
        "item": "demand", "period": "all", "month": "2030-01", "inv_period": None,
        "eur_per_mw": 15000.0, "peak_mw": 2.0, "billed_mw": 2.0}}
    n.meta[L.META_DEMAND_INFO] = getattr(n, L.DEMAND_SPEC_ATTR)["info"]
    applied.undo()
    block = commercial_cost_terms(n, commercial)["block"]
    assert block["demand_charge"] == pytest.approx(30_000.0)


# ── WP2.1a-0 review round 1: LP == engine on the named-window cases ────────

def _summer_winter(ratchet=None):
    return _demand(periods=[
        {"name": "peak", "rate": 20.0, "months": [6, 7, 8], "start_hour": 17, "end_hour": 21},
        {"name": "peak", "rate": 12.0, "start_hour": 17, "end_hour": 21},
        {"name": "off", "rate": 0.0}], **({"ratchet": ratchet} if ratchet else {}))


def _free_winter(ratchet=None):
    return _demand(periods=[
        {"name": "peak", "rate": 15.0, "months": [6, 7, 8], "start_hour": 17, "end_hour": 21},
        {"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 21},
        {"name": "off", "rate": 0.0}], **({"ratchet": ratchet} if ratchet else {}))


RATCHET_1 = {"lookback_months": 1, "share": 0.9}


@pytest.mark.live_solve
@pytest.mark.parametrize("case", ["split_ratchet", "summer_winter", "summer_winter_ratchet",
                                  "free_winter_ratchet", "net_hourly", "two_periods"])
def test_lp_equals_the_engine_on_named_windows(case):
    start = "2030-05-28 00:00" if "winter" in case else F.START
    n = _site(start=start)
    item = {"split_ratchet": {**_split_peak(), "ratchet": RATCHET_1},
            "summer_winter": _summer_winter(),
            "summer_winter_ratchet": _summer_winter(RATCHET_1),
            "free_winter_ratchet": _free_winter(RATCHET_1),
            "net_hourly": {**_split_peak(), "measured_on": "net", "settlement": "h"},
            "two_periods": _split_peak()}[case]
    commercial = _commercial(_tariff(item))
    history = {"2029-12": 0.0, "2030-04": 0.0, "2030-05": 0.0}
    if "ratchet" in case:
        commercial["meter_history_peaks_kw"] = history
    if case == "net_hourly":
        n.generators.loc["pv", "p_nom"] = 90.0
        n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC",
              marginal_cost=0.5)
        n.generators.loc["grid_supply", "p_min_pu"] = -1.0
        commercial["export_link"] = "export"
    if case == "two_periods":
        n.set_investment_periods([2030, 2040])
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, multi_investment_periods=case == "two_periods")
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, cond)
    peaks = n.meta[L.META_DEMAND]
    exp = n.links_t.p0["export"].to_numpy() if "export" in n.links_t.p0 else 0.0
    ts = n.snapshots.get_level_values(-1) if case == "two_periods" else n.snapshots
    total = 0.0
    periods = [None] if case != "two_periods" else [2030, 2040]
    for p in periods:
        sel = slice(None) if p is None else (n.snapshots.get_level_values(0) == p)
        idx = pd.DatetimeIndex(ts[sel])
        disp = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy()[sel],
                             "export_mw": (exp[sel] if not np.isscalar(exp) else exp)}, index=idx)
        billed = engine_rate(disp, _tariff(item), step_hours=0.25, timezone=None,
                             meter_history=history if "ratchet" in case else None)
        lp = sum(v["eur_per_mw"] * v["billed_mw"] for v in peaks.values()
                 if v.get("inv_period") == p)
        assert lp == pytest.approx(billed.per_item["demand"], rel=1e-6), (case, p)
        total += lp
    assert total > 0


def test_a_p1_split_peak_record_is_flagged_as_a_recipe_change():
    """#4: a P1 record (no hash_version) holding two peaks for one named window
    and month was billed on the P1 recipe; say so instead of showing it."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial(_tariff(_split_peak()))
    applied = L.materialise_poc_prices(n, commercial)
    info = {k: v for k, v in getattr(n, L.DEMAND_SPEC_ATTR)["info"].items()
            if k != "hash_version"}                       # as P1 wrote it
    applied.undo()
    row = {"item": "demand", "period": "peak", "month": "2030-01", "inv_period": None,
           "eur_per_mw": 15000.0}
    n.meta[L.META_DEMAND] = {"demand|0||2030-01": {**row, "peak_mw": 30.0, "billed_mw": 30.0},
                             "demand|1||2030-01": {**row, "peak_mw": 45.0, "billed_mw": 45.0}}
    n.meta[L.META_DEMAND_INFO] = info
    assert "demand_recipe_changed" in commercial_cost_terms(n, commercial)["flags"]


def test_a_p1_unique_name_record_is_not_a_recipe_change():
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial(_tariff(_demand()))
    applied = L.materialise_poc_prices(n, commercial)
    info = {k: v for k, v in getattr(n, L.DEMAND_SPEC_ATTR)["info"].items()
            if k != "hash_version"}
    # As P1 wrote it: no version, the items hashed with recipe 1.
    info["items_hash"] = L.demand_hash(n, L._parse(commercial), _tariff(_demand()).items, 1)
    applied.undo()
    n.meta[L.META_DEMAND] = {"demand|0||2030-01": {
        "item": "demand", "period": "all", "month": "2030-01", "inv_period": None,
        "eur_per_mw": 15000.0, "peak_mw": 2.0, "billed_mw": 2.0}}
    n.meta[L.META_DEMAND_INFO] = info
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert "demand_recipe_changed" not in flags and "config_changed_since_solve" not in flags
