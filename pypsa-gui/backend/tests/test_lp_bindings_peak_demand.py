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


def test_a_ratchet_is_left_to_wp1_5b():
    n = _site()
    item = _demand(ratchet={"lookback_months": 11, "share": 0.8})
    applied = L.materialise_poc_prices(n, _commercial(_tariff(item)))
    assert applied.facts["not_in_lp"] == {"demand": "ratchet_WP1.5b"}
    assert not hasattr(n, L.DEMAND_SPEC_ATTR)


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
