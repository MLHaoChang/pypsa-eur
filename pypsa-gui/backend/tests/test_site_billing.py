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
    import copy

    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": copy.deepcopy(list(items))}   # tests mutate their copy, never TOU


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
    assert list(res.monthly.index) == ["2030-01", "2030-07"]  # the sampled months
    assert res.per_item["demand"] is None                   # ten months not established
    assert res.per_item_sampled["demand"] > 0               # the sampled months' sum
    missing = [f for f in res.flags["demand"] if f.startswith("demand_month_not_established:")]
    assert len(missing) == 10                               # the calendar year 2030


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


def test_per_item_sampled_is_unknown_when_a_present_month_is_unknown():
    """Review #4: only months ABSENT from the dispatch are left out."""
    idx = pd.date_range("2030-01-01", periods=48, freq="h")
    d = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    d.iloc[5, 0] = np.nan
    res = rate(d, Tariff.model_validate(_tariff(TOU)), step_hours=1.0, timezone=None)
    assert res.per_item["energy"] is None and res.per_item_sampled["energy"] is None


def test_compact_frames_keep_unknown_amounts_nan_through_the_store():
    """Review #1: an unrated or unknown cell is NaN, never a confident 0."""
    from routers.projects import _RESULTS_STATE_SCHEMA, _safe_unpickle_results
    from services.finance.report import load_billing_frames, store_billing_frames

    idx = pd.date_range("2030-01-07", periods=24, freq="h")
    d = pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx)
    d.iloc[3, 0] = np.nan
    evening = {"id": "eve", "kind": "energy", "unit": "per_kwh",
               "periods": [{"name": "eve", "rate": 0.3, "start_hour": 17, "end_hour": 21}]}
    res = rate(d, Tariff.model_validate(_tariff(TOU, evening)), step_hours=1.0, timezone=None)
    engine_nan = int(res.lines["amount"].isna().sum())
    assert engine_nan > 1
    frames = B.compact_frames(B.SiteBill(per_period={None: res}))
    assert int(frames["_:lines"].isna().sum().sum()) == engine_nan
    store: dict = {}
    store_billing_frames(store, frames)
    blob = pickle.dumps({"__schema__": _RESULTS_STATE_SCHEMA, "data": store})
    back = load_billing_frames(_safe_unpickle_results(blob)["data"])
    assert int(back["_:lines"].isna().sum().sum()) == engine_nan


def _dispatched(n, mw=5.0):
    n.links_t.p0 = pd.DataFrame({"import": np.full(len(n.snapshots), mw)}, index=n.snapshots)
    return n


def test_sampled_weeks_standing_for_half_a_year_are_flagged_not_raised():
    """Review #2: no calendar billing period can be stated — the period is None."""
    n = build_edge_15min()
    parts = [pd.date_range(f"2030-{m:02d}-07", periods=96 * 7, freq="15min") for m in (1, 7)]
    idx = parts[0].append(parts[1])
    n.set_snapshots(idx)
    n.snapshot_weightings.loc[:, :] = 4380.0 / len(idx)
    bill = B.bill_site(_dispatched(n), {"poc_link": "import", "import_tariff": _tariff(TOU)})
    assert bill.per_period == {None: None}
    assert "period_not_billed:_:billing_period_unknown" in bill.flags
    assert B.compact_frames(bill) == {}


def test_solver_round_off_below_zero_is_zero_and_a_real_negative_is_flagged():
    """Review #3: the engine refuses negatives; the adapter never crashes."""
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU)}
    n = _dispatched(build_edge_15min())
    n.links_t.p0.iloc[3, 0] = -1e-9
    (res,) = B.bill_site(n, commercial).per_period.values()
    assert res.per_item["energy"] is not None
    n.links_t.p0.iloc[3, 0] = -0.5
    bill = B.bill_site(n, commercial)
    assert "negative_flow:import:1" in bill.flags
    assert bill.per_period[None].per_item["energy"] is None       # unknown, not guessed


@pytest.mark.live_solve
def test_a_tariff_changed_since_the_solve_is_flagged_on_the_bill():
    """Review #5: the bill rates the solved dispatch with the CURRENT config."""
    n = build_edge_15min()
    commercial = {"poc_link": "import", "import_tariff": _tariff(TOU, DEMAND)}
    _solve(n, commercial)
    bill = B.bill_site(n, commercial)
    assert "config_changed_since_solve" not in bill.flags
    assert bill.provenance["energy_hash"] and bill.provenance["demand_hash"]
    for item_idx in (0, 1):                                      # energy, then demand
        changed = {"poc_link": "import", "import_tariff": _tariff(TOU, DEMAND)}
        changed["import_tariff"]["items"][item_idx]["periods"][-1]["rate"] += 1.0
        assert "config_changed_since_solve" in B.bill_site(n, changed).flags


@pytest.mark.live_solve
@pytest.mark.parametrize("added", ["demand", "tiers"])
def test_an_lp_term_added_after_the_solve_is_flagged_on_the_bill(added):
    """Round 2 #1: the dispatch never saw a demand charge or convex tiers added
    after it was optimised."""
    tiers = {"id": "tiered", "kind": "energy", "unit": "per_kwh", "measured_on": "import",
             "periods": [{"name": "all", "rate": 0.0}],
             "tiers": [{"threshold": 0, "rate": 0.02}, {"threshold": 500_000, "rate": 0.06}]}
    n = build_edge_15min()
    _solve(n, {"poc_link": "import", "import_tariff": _tariff(TOU)})
    later = {"poc_link": "import",
             "import_tariff": _tariff(TOU, DEMAND if added == "demand" else tiers)}
    assert "config_changed_since_solve" in B.bill_site(n, later).flags
    fixed = {"id": "standing", "kind": "fixed", "unit": "per_month",
             "periods": [{"name": "all", "rate": 50.0}]}
    same = {"poc_link": "import", "import_tariff": _tariff(TOU, fixed)}
    assert "config_changed_since_solve" not in B.bill_site(n, same).flags  # not LP-carried
