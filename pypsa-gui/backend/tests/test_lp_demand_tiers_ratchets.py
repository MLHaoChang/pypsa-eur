"""
Demand tiers and the WP2.1a-iii ratchet modes in the LP (Edge Investment Case
P2 WP2.1c-i).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.1c.
Rising demand tiers are stacked variables on the billed demand of each (item,
window, month) key, `ic_demand_tier_q ≤ width_k` summing to `ic_billed_demand`
at Σ w_obj · rate_k · q_k; falling tiers are priced at the first tier and noted
`nonconvex_tier`. Designated-month and cyclic ratchets are linear rows on
`ic_billed_demand`. Each construct's LP cost equals the engine's bill on the
same dispatch, and the objective decomposition gap is 0.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial import billing as B
from services.commercial import lp_bindings as L
from services.commercial.tariff_engine import rate
from tests.fixtures.investment_case import edge_15min as F

RISING = [{"threshold": 0, "rate": 5.0}, {"threshold": 30_000, "rate": 20.0}]
FALLING = [{"threshold": 0, "rate": 20.0}, {"threshold": 30_000, "rate": 5.0}]


def _demand(tiers=None, ratchet=None, periods=None, item_id="demand"):
    out = {"id": item_id, "kind": "demand", "unit": "per_kw_month", "measured_on": "import",
           "periods": periods or [{"name": "all", "rate": 0.0 if tiers else 12.0}]}
    if tiers is not None:
        out["tiers"] = tiers
    if ratchet is not None:
        out["ratchet"] = ratchet
    return out


def _tariff(*items):
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "US",
                                  "valid_from": "2029-01-01", "items": list(items)})


def _site(start="2030-01-28 00:00"):
    old = F.START
    F.START = start
    try:
        n = F.build_edge_15min()
    finally:
        F.START = old
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    return n


def _commercial(tariff, **kw):
    return {"poc_link": "import", "import_tariff": tariff.model_dump(mode="json"), **kw}


def _solve(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial)
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, cond)
    return cfg


def _engine(n, tariff, history=None):
    d = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                     index=n.snapshots)
    return rate(d, tariff, step_hours=0.25, timezone=None, meter_history=history)


def _gap_and_rows(n, cfg):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb)["gap_pct"], cb["commercial"]


# ── spec ───────────────────────────────────────────────────────────────────


def test_demand_tiers_and_new_ratchet_modes_are_in_the_lp():
    t = _tariff(_demand(RISING, {"months": [1], "share": 0.8}, item_id="a"),
                _demand(ratchet={"lookback_months": 2, "share": 0.9, "cyclic_year": True},
                        item_id="b"))
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(t))
    assert applied.facts["not_in_lp"] == {}
    assert sorted(applied.facts["demand_items"]) == ["a", "b"]
    keys = {k["key"]: k for k in getattr(n, L.DEMAND_SPEC_ATTR)["keys"]}
    tiered = keys["a|all||2030-01"]
    assert [s["width_mw"] for s in tiered["tiers"]] == [30.0, float("inf")]
    assert [s["eur_per_mw"] for s in tiered["tiers"]] == [5000.0, 20000.0]
    applied.undo()


def test_months_mode_reads_every_designated_month_of_the_rate_year():
    t = _tariff(_demand(ratchet={"months": [1, 7], "share": 0.8}))
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(t, meter_history_peaks_kw={"2030-07": 9e3}))
    rows = getattr(n, L.DEMAND_SPEC_ATTR)["ratchets"]
    feb = [r for r in rows if r["key"] == "demand|all||2030-02"]
    assert {r.get("of_key") for r in feb} == {"demand|all||2030-01", None}
    assert [r["floor_mw"] for r in feb if "floor_mw" in r] == [9.0]    # July's history
    assert "ratchet_seed_missing" not in applied.facts["notes"]
    applied.undo()


def test_cyclic_range_wraps_inside_the_rate_year_and_discloses_a_missing_seed():
    t = _tariff(_demand(ratchet={"lookback_months": 1, "share": 0.9, "cyclic_year": True}))
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(t))
    rows = getattr(n, L.DEMAND_SPEC_ATTR)["ratchets"]
    assert [r for r in rows if r["key"] == "demand|all||2030-02"][0]["of_key"] == \
        "demand|all||2030-01"
    assert not [r for r in rows if r["key"] == "demand|all||2030-01"]   # 2030-12 unknown
    assert "ratchet_seed_missing" in applied.facts["notes"]
    applied.undo()


# ── LP == engine ───────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_rising_demand_tiers_cost_the_engine_bill_with_zero_gap():
    t = _tariff(_demand(RISING))
    n = _site()
    cfg = _solve(n, _commercial(t))
    gap, rows = _gap_and_rows(n, cfg)
    assert abs(gap) < 1e-6
    assert rows["demand_charge"] == pytest.approx(_engine(n, t).per_item["demand"], rel=1e-6)
    rec = next(iter(n.meta[L.META_DEMAND].values()))
    assert rec["tiers"] and rec["billed_mw"] > 30.0                   # the second tier binds


@pytest.mark.live_solve
def test_falling_demand_tiers_are_priced_at_the_first_tier_and_noted():
    t = _tariff(_demand(FALLING))
    n = _site()
    cfg = _solve(n, _commercial(t))
    gap, rows = _gap_and_rows(n, cfg)
    assert abs(gap) < 1e-6
    lp = sum(20_000.0 * v["billed_mw"] for v in n.meta[L.META_DEMAND].values())
    assert rows["demand_charge"] == pytest.approx(lp, rel=1e-9)
    assert "nonconvex_tier" in n.meta[L.META_DEMAND_INFO]["notes"]
    assert _engine(n, t).per_item["demand"] < lp                      # the bill is exact


@pytest.mark.live_solve
def test_windowed_demand_tiers_price_each_window_with_its_own_rates():
    thresh = [{"threshold": 0, "rate": 0.0}, {"threshold": 30_000, "rate": 0.0}]
    t = _tariff(_demand(thresh, periods=[
        {"name": "eve", "rate": 0.0, "start_hour": 17, "end_hour": 21, "tier_rates": [8.0, 30.0]},
        {"name": "rest", "rate": 0.0, "tier_rates": [2.0, 4.0]}]))
    n = _site()
    cfg = _solve(n, _commercial(t))
    gap, rows = _gap_and_rows(n, cfg)
    assert abs(gap) < 1e-6
    assert rows["demand_charge"] == pytest.approx(_engine(n, t).per_item["demand"], rel=1e-6)


@pytest.mark.live_solve
@pytest.mark.parametrize("ratchet,history", [
    ({"months": [1], "share": 0.95}, None),
    ({"lookback_months": 1, "share": 0.95, "cyclic_year": True}, {"2030-12": 60_000.0}),
])
def test_new_ratchet_modes_bill_the_engine_with_zero_gap(ratchet, history):
    t = _tariff(_demand(ratchet=ratchet))
    n = _site()
    kw = {"meter_history_peaks_kw": history} if history else {}
    cfg = _solve(n, _commercial(t, **kw))
    gap, rows = _gap_and_rows(n, cfg)
    assert abs(gap) < 1e-6
    bill = _engine(n, t, history)
    assert rows["demand_charge"] == pytest.approx(bill.per_item["demand"], rel=1e-6)
    lines = bill.demand_lines.set_index("month")
    lp = {v["month"]: v["billed_mw"] * 1000.0 for v in n.meta[L.META_DEMAND].values()}
    assert lp == pytest.approx(lines["billed_kw"].to_dict(), rel=1e-6)


@pytest.mark.live_solve
def test_a_record_solved_before_tiers_were_bound_is_a_recipe_change_not_a_drift():
    """A solve before WP2.1c-i left the tiered demand item out of the LP: the
    unchanged config now binds it — `demand_recipe_changed`, not a config drift."""
    from services.commercial import hashing as H
    from services.commercial.cost_rows import commercial_cost_terms

    plain, tiered = _demand(item_id="plain"), _demand(RISING, item_id="tiered")
    commercial = _commercial(_tariff(plain, tiered))
    n = _site()
    _solve(n, commercial)
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert "demand_recipe_changed" not in flags and "config_changed_since_solve" not in flags
    # Rewrite the records as the old recipe made them.
    n.meta[L.META_DEMAND] = {k: v for k, v in n.meta[L.META_DEMAND].items()
                             if v["item"] == "plain"}
    cfg = L._parse(commercial)
    info = n.meta[L.META_DEMAND_INFO]
    info.pop("lp_recipe")
    info["items"] = ["plain"]
    info["items_hash"] = L.demand_hash(n, cfg, [cfg.import_tariff.items[0]], H.HASH_VERSION)
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert "demand_recipe_changed" in flags and "config_changed_since_solve" not in flags
    bill_flags = B.bill_site(n, commercial).flags                     # the bill agrees
    assert "demand_recipe_changed" in bill_flags and "config_changed_since_solve" not in bill_flags
    # A real rate change on the old item is still a drift.
    commercial["import_tariff"]["items"][0]["periods"][0]["rate"] = 13.0
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert "config_changed_since_solve" in flags
    assert "config_changed_since_solve" in B.bill_site(n, commercial).flags


FREE_THEN_FALLING = [{"threshold": 0, "rate": 0.0}, {"threshold": 30_000, "rate": 12.0},
                     {"threshold": 45_000, "rate": 5.0}]


def test_a_free_first_tier_does_not_take_a_nonconvex_charge_out_of_the_lp():
    """Review #1: rates [0, 12, 5] are priced at the first NON-ZERO rate, or at
    the tier the same month a year earlier landed in (metered history)."""
    t = _tariff(_demand(FREE_THEN_FALLING))
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(t))
    keys = getattr(n, L.DEMAND_SPEC_ATTR)["keys"]
    assert {k["eur_per_mw"] for k in keys} == {12_000.0}
    assert "nonconvex_tier:demand" in applied.facts["notes"]
    applied.undo()
    applied = L.materialise_poc_prices(
        n, _commercial(t, meter_history_peaks_kw={"2029-01": 50_000.0, "2029-02": 10_000.0}))
    by_month = {k["month"]: k["eur_per_mw"] for k in getattr(n, L.DEMAND_SPEC_ATTR)["keys"]}
    # Jan: tier 2; Feb's 10 MW lands in the FREE tier — the first charged rate.
    assert by_month == {"2030-01": 5_000.0, "2030-02": 12_000.0}
    applied.undo()


@pytest.mark.live_solve
@pytest.mark.parametrize("history", [None, {"2029-01": 20_000.0, "2029-02": 20_000.0}])
def test_a_free_first_tier_keeps_the_lp_shaving_the_peak(history):
    """Also when last year's peak sat inside the free tier (round 2 residue)."""
    t = _tariff(_demand(FREE_THEN_FALLING))
    n = _site()
    kw = {"meter_history_peaks_kw": history} if history else {}
    cfg = _solve(n, _commercial(t, **kw))
    gap, rows = _gap_and_rows(n, cfg)
    assert abs(gap) < 1e-6
    lp = sum(12_000.0 * v["billed_mw"] for v in n.meta[L.META_DEMAND].values())
    assert rows["demand_charge"] == pytest.approx(lp, rel=1e-9) and lp > 0


@pytest.mark.live_solve
def test_an_old_solve_whose_demand_items_are_all_newly_bound_is_a_recipe_change():
    """Review #2: the old recipe bound none of them, so it wrote no demand record."""
    from services.commercial.cost_rows import commercial_cost_terms

    commercial = _commercial(_tariff(_demand(RISING)))
    n = _site()
    _solve(n, commercial)
    n.meta.pop(L.META_DEMAND)
    n.meta.pop(L.META_DEMAND_INFO)
    n.meta[L.META_LINKS].pop("lp_recipe")
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert {"demand_charge_not_established", "demand_recipe_changed"} <= set(flags)
    assert "config_changed_since_solve" not in flags
    bill = B.bill_site(n, commercial).flags
    assert "demand_recipe_changed" in bill and "config_changed_since_solve" not in bill
    # A solve under this recipe with no demand record is a real change.
    n.meta[L.META_LINKS]["lp_recipe"] = L.LP_RECIPE
    assert "demand_recipe_changed" not in commercial_cost_terms(n, commercial)["flags"]
    assert "config_changed_since_solve" in B.bill_site(n, commercial).flags
