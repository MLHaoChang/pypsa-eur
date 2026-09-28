"""
Site billing adapter: the solved network → the dispatch the billing engine
rates (Edge Investment Case P2 WP2.1b).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.1b.
`bill_site(n, commercial)` rates the PoC meter the LP charged (Σ import Links
of a group, the export Link) per investment period, on the site clock, with
representative weeks standing for their calendar year, capacity items on the
PoC's `p_nom_opt` in the periods the PoC is active, and meter history seeding
the first period only. Its bill equals `tariff_engine.rate` on the same frames.
The compact billing frames (wide float32, naive UTC, flat keys) survive the
restricted results unpickler under a size bound.
"""
from __future__ import annotations

import pickle
import queue
import threading

import numpy as np
import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial import billing as B
from services.commercial.tariff_engine import rate
from tests.fixtures.investment_case.edge_15min import build_edge_15min

TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh", "periods": [
    {"name": "night", "rate": 0.05, "start_hour": 0, "end_hour": 6},
    {"name": "day", "rate": 0.20}]}
DEMAND = {"id": "demand", "kind": "demand", "unit": "per_kw_month",
          "periods": [{"name": "all", "rate": 12.0}]}
CAP = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
       "periods": [{"name": "all", "rate": 40.0}]}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": list(items)}


def _solve(n, commercial, **cfg_kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, **cfg_kw)
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, cond)
    return cfg


def test_an_unsolved_network_is_not_billed():
    bill = B.bill_site(build_edge_15min(), {"poc_link": "import",
                                            "import_tariff": _tariff(TOU)})
    assert bill.per_period == {} and bill.flags == ["not_solved"]


@pytest.mark.live_solve
def test_the_site_bill_is_the_engine_on_the_same_frames():
    n = build_edge_15min()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, DEMAND)}
    _solve(n, commercial)
    bill = B.bill_site(n, commercial)
    (res,) = bill.per_period.values()
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                            index=n.snapshots)
    ref = rate(dispatch, Tariff.model_validate(_tariff(TOU, DEMAND)), step_hours=0.25,
               timezone=None)
    assert res.per_item == pytest.approx(ref.per_item)
    assert bill.provenance["tariff_hash"]


@pytest.mark.live_solve
def test_a_group_site_is_billed_on_its_members_combined_import():
    from tests.test_group_contract import _two_members

    n = _two_members()
    commercial = {"poc_link": "import", "group_contract": "hub",
                  "group_members": ["import", "import_b"], "group_cap_mw": 60.0,
                  "import_tariff": _tariff(TOU, DEMAND)}
    _solve(n, commercial)
    (res,) = B.bill_site(n, commercial).per_period.values()
    p0 = n.links_t.p0
    ref = rate(pd.DataFrame({"import_mw": (p0["import"] + p0["import_b"]).to_numpy(),
                             "export_mw": 0.0}, index=n.snapshots),
               Tariff.model_validate(_tariff(TOU, DEMAND)), step_hours=0.25, timezone=None)
    assert res.per_item == pytest.approx(ref.per_item)


@pytest.mark.live_solve
def test_two_periods_are_billed_separately_with_capacity_where_the_poc_is_active():
    """WP2.1a-i review #12: capacity follows the PoC's activity per period."""
    n = build_edge_15min()
    n.set_investment_periods([2030, 2040])
    n.investment_period_weightings["years"] = 10.0
    n.links.loc["import", ["build_year", "lifetime"]] = [2030, 5]    # retired before 2040
    n.add("Carrier", "diesel")
    n.add("Generator", "backup", bus="site", carrier="diesel", p_nom=100.0,
          marginal_cost=500.0)                                        # serves 2040 off-grid
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, CAP)}
    _solve(n, commercial, multi_investment_periods=True)
    bill = B.bill_site(n, commercial)
    assert set(bill.per_period) == {2030, 2040}
    assert bill.per_period[2030].per_item["cap"] > 0
    assert bill.per_period[2040].per_item["cap"] == 0.0
    assert bill.provenance["period_years"] == {2030: 10.0, 2040: 10.0}


@pytest.mark.live_solve
def test_representative_weeks_bill_their_calendar_year_with_sampled_sums():
    from tests.test_commercial_objective_reconciliation import _rep_weeks

    n = _rep_weeks()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, DEMAND)}
    _solve(n, commercial)
    (res,) = B.bill_site(n, commercial).per_period.values()
    assert len(res.monthly.index) == 12 or res.flags["demand"]
    assert res.per_item["demand"] is None                   # ten months not established
    assert res.per_item_sampled["demand"] > 0               # the sampled months' sum
    assert any(f.startswith("demand_month_not_established:") for f in res.flags["demand"])


def test_meter_history_seeds_the_first_period_only(monkeypatch):
    seen = []
    real = B.rate

    def spy(*a, **k):
        seen.append(k.get("meter_history"))
        return real(*a, **k)

    monkeypatch.setattr(B, "rate", spy)
    n = build_edge_15min()
    n.set_investment_periods([2030, 2040])
    n.links_t.p0 = pd.DataFrame({"import": np.full(len(n.snapshots), 5.0)}, index=n.snapshots)
    ratchet = {**DEMAND, "ratchet": {"lookback_months": 2, "share": 0.9}}
    B.bill_site(n, {"poc_link": "import", "import_tariff": _tariff(ratchet),
                    "meter_history_peaks_kw": {"2029-12": 1000.0}})
    assert seen == [{"2029-12": 1000.0}, None]


def test_per_item_sampled_sums_the_rated_months():
    idx = pd.date_range("2030-01-07", periods=24, freq="h").append(
        pd.date_range("2030-03-07", periods=24, freq="h"))
    res = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx),
               Tariff.model_validate(_tariff(DEMAND)), step_hours=1.0, timezone=None)
    assert res.per_item["demand"] is None                   # February not established
    assert res.per_item_sampled["demand"] == pytest.approx(2 * 12.0 * 1000.0)


def test_compact_frames_survive_the_restricted_unpickler_under_the_size_bound():
    from routers.projects import _RESULTS_STATE_SCHEMA, _safe_unpickle_results
    from services.finance.report import load_billing_frames, store_billing_frames

    idx = pd.date_range("2030-01-01", "2030-12-31 23:45", freq="15min", tz="Europe/Berlin")
    rng = np.random.default_rng(3)
    items = [{**TOU, "id": f"e{i}"} for i in range(7)] + [DEMAND]
    res = rate(pd.DataFrame({"import_mw": rng.uniform(0, 20, len(idx)), "export_mw": 0.0},
                            index=idx), Tariff.model_validate(_tariff(*items)),
               step_hours=0.25, timezone="Europe/Berlin")
    frames = B.compact_frames(B.SiteBill(per_period={None: res}, flags=[], provenance={}))
    assert set(frames) >= {"_:lines", "_:quantities", "_:monthly", "_:demand_lines"}
    assert frames["_:lines"].dtypes.eq(np.float32).all()
    store: dict = {}
    store_billing_frames(store, frames)
    blob = pickle.dumps({"__schema__": _RESULTS_STATE_SCHEMA, "data": store})
    assert len(blob) < 3 * 1024 * 1024
    back = load_billing_frames(_safe_unpickle_results(blob)["data"])
    pd.testing.assert_frame_equal(back["_:lines"], frames["_:lines"])
    assert back["_:lines"].index.tz is not None                # the zone is restored
