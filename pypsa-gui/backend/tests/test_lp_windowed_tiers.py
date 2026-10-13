"""
Windowed energy tiers and the predicted non-convex tier in the LP (Edge
Investment Case P2 WP2.1c-ii).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.1c.
Convex windowed tiers (rates rising in k for EVERY period) are variables
`q[period, k] ≥ 0` per (item, month): Σ_k q[p, k] = the period's volume,
Σ_p q[p, k] ≤ width_k, cost Σ tier_rates[p][k] · q[p, k]. The engine's URDB
proportional bill is feasible for that LP, so the LP optimum is ≤ the bill on
the same dispatch (WP2.3's `tier_allocation` ≥ 0). Any other shape prices each
period at a single tier: the tier the SAME month a year earlier landed in
(`meter_history_energy_kwh`), else the first tier, noted `nonconvex_tier`.
A first threshold above 0 leaves the volume below it free, as the engine bills.
"""
from __future__ import annotations

import copy
import queue
import threading

import numpy as np
import pandas as pd
import pytest

from models.commercial import CommercialConfig, Tariff
from services.commercial import lp_bindings as L
from services.commercial.tariff_engine import rate
from tests.fixtures.investment_case import edge_15min as F

THRESH = [{"threshold": 0, "rate": 0.0}, {"threshold": 300_000, "rate": 0.0}]


def _windowed(peak_rates, off_rates, thresh=THRESH, item_id="e"):
    return {"id": item_id, "kind": "energy", "unit": "per_kwh", "measured_on": "import",
            "tiers": copy.deepcopy(thresh),
            "periods": [{"name": "peak", "rate": 0.0, "start_hour": 17, "end_hour": 21,
                         "tier_rates": list(peak_rates)},
                        {"name": "off", "rate": 0.0, "tier_rates": list(off_rates)}]}


def _plain(tiers, item_id="plain"):
    return {"id": item_id, "kind": "energy", "unit": "per_kwh", "measured_on": "import",
            "periods": [{"name": "all", "rate": 0.0}], "tiers": copy.deepcopy(tiers)}


def _tariff(*items):
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "US",
                                  "valid_from": "2029-01-01", "items": list(items)})


def _site():
    n = F.build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
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


def _rows(n, cfg):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb)["gap_pct"], cb["commercial"]


def _bill(n, tariff):
    d = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                     index=n.snapshots)
    return rate(d, tariff, step_hours=0.25, timezone=None)


# ── spec ───────────────────────────────────────────────────────────────────


def test_convex_windowed_tiers_are_lp_terms_per_period():
    t = _tariff(_windowed([0.30, 0.40], [0.10, 0.15]))
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(t))
    assert applied.facts["not_in_lp"] == {}
    assert applied.facts["tiered_items"] == ["e"]
    (key,) = getattr(n, L.TIER_SPEC_ATTR)["keys"]
    assert [p["name"] for p in key["periods"]] == ["peak", "off"]
    assert [s["width_mwh"] for s in key["segments"]] == [300.0, float("inf")]
    assert "import" not in n.links_t.marginal_cost.columns       # nothing flat
    applied.undo()


def test_a_period_whose_rates_fall_makes_the_item_nonconvex_priced_at_its_first_tier():
    t = _tariff(_windowed([0.30, 0.40], [0.15, 0.10]))
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(t))
    assert applied.facts["nonconvex_tier_items"] == ["e"]
    assert "nonconvex_tier" in applied.facts["notes"]
    hour = n.snapshots.hour
    mc = n.links_t.marginal_cost["import"].to_numpy()
    assert np.allclose(mc[(hour >= 17) & (hour < 21)], 300.0)
    assert np.allclose(mc[(hour < 17) | (hour >= 21)], 150.0)
    applied.undo()


def test_the_predicted_tier_is_where_the_same_month_a_year_earlier_landed():
    falling = [{"threshold": 0, "rate": 0.30}, {"threshold": 100_000, "rate": 0.10}]
    t = _tariff(_plain(falling))
    n = _site()
    applied = L.materialise_poc_prices(
        n, _commercial(t, meter_history_energy_kwh={"2029-01": 150_000.0}))
    assert np.allclose(n.links_t.marginal_cost["import"], 100.0)   # tier 1 (≥ 100 MWh)
    assert applied.facts["nonconvex_tier_predicted"] == {"plain": {"2030-01": 1}}
    applied.undo()
    applied = L.materialise_poc_prices(n, _commercial(t))            # no history: first tier
    assert np.allclose(n.links_t.marginal_cost["import"], 300.0)
    assert applied.facts["nonconvex_tier_predicted"] == {}
    applied.undo()


def test_meter_history_energy_is_validated_and_registered_for_hash_recipe_1():
    from services.commercial import hashing as H

    with pytest.raises(ValueError, match="meter_history_energy_kwh"):
        CommercialConfig.model_validate({"poc_link": "import",
                                         "meter_history_energy_kwh": {"2029-13": 1.0}})
    with pytest.raises(ValueError, match="meter_history_energy_kwh"):
        CommercialConfig.model_validate({"poc_link": "import",
                                         "meter_history_energy_kwh": {"2029-01": -1.0}})
    assert H.FIELDS_AFTER_V1[("CommercialConfig", "meter_history_energy_kwh")] == {}


def test_energy_history_enters_the_energy_hash_only_where_it_prices_something():
    falling = [{"threshold": 0, "rate": 0.30}, {"threshold": 100_000, "rate": 0.10}]
    n = _site()
    tou = {"id": "tou", "kind": "energy", "unit": "per_kwh",
           "periods": [{"name": "all", "rate": 0.2}]}
    plain = CommercialConfig.model_validate(_commercial(_tariff(tou)))
    with_hist = CommercialConfig.model_validate(
        _commercial(_tariff(tou), meter_history_energy_kwh={"2029-01": 1.0}))
    assert L.energy_hash(n, plain) == L.energy_hash(n, with_hist)   # nothing non-convex
    nc = CommercialConfig.model_validate(_commercial(_tariff(_plain(falling))))
    nc_hist = CommercialConfig.model_validate(
        _commercial(_tariff(_plain(falling)), meter_history_energy_kwh={"2029-01": 1.0}))
    assert L.energy_hash(n, nc) != L.energy_hash(n, nc_hist)


# ── LP ─────────────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_convex_windowed_tiers_reconcile_and_the_lp_optimum_is_at_most_the_bill():
    t = _tariff(_windowed([0.30, 0.40], [0.10, 0.15]))
    n = _site()
    cfg = _solve(n, _commercial(t))
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    recs = n.meta[L.META_TIERS]
    lp = sum(v["rate_eur_per_mwh"] * v["q_mwh"] for v in recs.values())
    assert rows["energy_tiers"] == pytest.approx(lp, rel=1e-9)
    assert {v["period"] for v in recs.values()} == {"peak", "off"}
    bill = _bill(n, t).per_item["e"]
    assert bill >= lp - 1e-6                                         # tier_allocation ≥ 0
    # The LP gives the cheap first tier to the expensive peak period first.
    first = {v["period"]: v["q_mwh"] for v in recs.values() if v["tier"] == 0}
    assert first["peak"] > 0


@pytest.mark.live_solve
def test_one_period_windowed_tiers_equal_the_bill():
    """With every snapshot in one period the proportional split is the LP's."""
    item = _windowed([0.30, 0.40], [0.10, 0.15])
    item["periods"] = [{"name": "all", "rate": 0.0, "tier_rates": [0.10, 0.15]}]
    t = _tariff(item)
    n = _site()
    cfg = _solve(n, _commercial(t))
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    assert rows["energy_tiers"] == pytest.approx(_bill(n, t).per_item["e"], rel=1e-6)


@pytest.mark.live_solve
def test_a_first_threshold_above_zero_leaves_the_volume_below_it_free():
    """P1 carried the volume below a first threshold > 0 at the first rate; the
    engine bills it free (fixed in WP2.1c-ii)."""
    tiers = [{"threshold": 200_000, "rate": 0.10}, {"threshold": 400_000, "rate": 0.20}]
    t = _tariff(_plain(tiers))
    n = _site()
    cfg = _solve(n, _commercial(t))
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    assert rows["energy_tiers"] == pytest.approx(_bill(n, t).per_item["plain"], rel=1e-6)


@pytest.mark.live_solve
def test_a_predicted_nonconvex_tier_prices_the_rows():
    falling = [{"threshold": 0, "rate": 0.30}, {"threshold": 100_000, "rate": 0.10}]
    t = _tariff(_plain(falling))
    n = _site()
    cfg = _solve(n, _commercial(t, meter_history_energy_kwh={"2029-01": 150_000.0}))
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    w = n.snapshot_weightings.objective
    assert rows["energy_import"] == pytest.approx(
        float((w * n.links_t.p0["import"]).sum()) * 100.0, rel=1e-9)


@pytest.mark.live_solve
def test_a_solve_before_windowed_tiers_were_bound_is_a_recipe_change():
    """An `ic_poc_links` record without `lp_recipe` priced no windowed tier:
    the unchanged config now binds one — `energy_recipe_changed`, not a drift."""
    from services.commercial import billing as B
    from services.commercial.cost_rows import commercial_cost_terms

    tou = {"id": "tou", "kind": "energy", "unit": "per_kwh",
           "periods": [{"name": "all", "rate": 0.2}]}
    commercial = _commercial(_tariff(tou, _windowed([0.30, 0.40], [0.10, 0.15])))
    n = _site()
    _solve(n, commercial)
    assert "energy_recipe_changed" not in commercial_cost_terms(n, commercial)["flags"]
    # Rewrite the record as recipe 1 made it: no windowed item, no tier volumes.
    cfg = L._parse(commercial)
    rec = n.meta[L.META_LINKS]
    rec.pop("lp_recipe")
    rec["energy_hash"] = L.energy_hash(n, cfg, rec["hash_version"], recipe=1)
    n.meta.pop(L.META_TIERS)
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert "energy_recipe_changed" in flags and "config_changed_since_solve" not in flags
    bill = B.bill_site(n, commercial).flags
    assert "energy_recipe_changed" in bill and "config_changed_since_solve" not in bill
    commercial["import_tariff"]["items"][0]["periods"][0]["rate"] = 0.25   # a real edit
    assert "config_changed_since_solve" in commercial_cost_terms(n, commercial)["flags"]


def test_a_free_tier_never_takes_a_nonconvex_energy_item_out_of_the_lp():
    """Rates [0, 0.30, 0.10]: no history ⇒ the first CHARGED rate, not 0; a
    predicted free tier falls back the same way."""
    tiers = [{"threshold": 0, "rate": 0.0}, {"threshold": 50_000, "rate": 0.30},
             {"threshold": 100_000, "rate": 0.10}]
    t = _tariff(_plain(tiers))
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(t))
    assert np.allclose(n.links_t.marginal_cost["import"], 300.0)
    applied.undo()
    applied = L.materialise_poc_prices(
        n, _commercial(t, meter_history_energy_kwh={"2029-01": 10_000.0}))
    assert np.allclose(n.links_t.marginal_cost["import"], 300.0)
    assert applied.facts["nonconvex_tier_predicted"] == {"plain": {"2030-01": 0}}
    applied.undo()


@pytest.mark.parametrize("tiers,history", [
    ([{"threshold": 0, "rate": 0.0}, {"threshold": 50_000, "rate": 0.2},
      {"threshold": 100_000, "rate": 0.1}], None),                   # free first, falling
    ([{"threshold": 0, "rate": 0.3}, {"threshold": 100_000, "rate": 0.1}],
     {"2029-01": 150_000.0}),                                       # predicted from history
    ([{"threshold": 200_000, "rate": 0.10}, {"threshold": 400_000, "rate": 0.20}], None),
])
def test_a_tier_item_repriced_by_recipe_3_is_a_recipe_change_for_older_records(tiers, history):
    """Review #1, #2: the item set is unchanged but recipe 3 prices it
    differently (free-tier fallback, predicted tier, free volume below a first
    threshold above 0), so an older solve is flagged for a re-solve."""
    n = _site()
    extra = {"meter_history_energy_kwh": history} if history else {}
    cfg = CommercialConfig.model_validate(_commercial(_tariff(_plain(tiers)), **extra))
    rec = {"energy_hash": L.energy_hash(n, cfg, 2, recipe=2), "hash_version": 2,
           "lp_recipe": 2}
    assert L.energy_record_state(n, cfg, rec) == "recipe"
    rec3 = {"energy_hash": L.energy_hash(n, cfg, 2), "hash_version": 2, "lp_recipe": 3}
    assert L.energy_record_state(n, cfg, rec3) is None


def test_a_period_with_no_volume_in_a_month_is_priced_zero():
    """Review #3: a windowed key's period without snapshots in a month has no
    variables; one with snapshots but zero volume gets q = 0 at the optimum."""
    item = _windowed([0.30, 0.40], [0.10, 0.15])
    item["periods"][0].update(start_hour=23, end_hour=24)   # peak only 23:00–24:00
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(_tariff(item)))
    (key,) = getattr(n, L.TIER_SPEC_ATTR)["keys"]
    assert {p["name"] for p in key["periods"]} == {"peak", "off"}
    applied.undo()
    item["periods"][0].update(months=[7], start_hour=None, end_hour=None)   # never in January
    applied = L.materialise_poc_prices(n, _commercial(_tariff(item)))
    (key,) = getattr(n, L.TIER_SPEC_ATTR)["keys"]
    assert [p["name"] for p in key["periods"]] == ["off"]
    applied.undo()
