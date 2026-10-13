"""
Tariff capacity items in the LP (Edge Investment Case P2 WP2.1c-iii).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.1c.
A `per_kw_year` capacity item on the CONTRACTED capacity is an explicit term on
the PoC Link's `p_nom` (extendable PoC), priced like the connection fee: €/MW-yr
× the operating years each active period represents (Σ w / 8760). A fixed PoC
makes it a constant: reported as `tariff_capacity_fixed` OUTSIDE the reconciled
total, as P1 does for a fixed connection fee. Measured on `peak_import` (the DE
Leistungspreis) it is an annual measured-peak variable per (period, local year)
over settlement-interval means, at € × the year's represented hours / 8760.
`per_kva_year` stays out of the LP. Rows equal the engine's bill on the solved
dispatch; the gap is 0.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial import billing as B
from services.commercial import lp_bindings as L
from tests.fixtures.investment_case import edge_15min as F

TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh",
       "periods": [{"name": "all", "rate": 0.10}]}
CAP = {"id": "cap", "kind": "capacity", "unit": "per_kw_year",
       "periods": [{"name": "all", "rate": 400.0}]}
LEISTUNG = {"id": "lp", "kind": "capacity", "unit": "per_kw_year", "measured_on": "peak_import",
            "periods": [{"name": "all", "rate": 400.0}]}
KVA = {"id": "kva", "kind": "capacity", "unit": "per_kva_year",
       "periods": [{"name": "all", "rate": 40.0}]}


def _tariff(*items):
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": [dict(i) for i in items]}


def _site(extendable=True):
    n = F.build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    if extendable:
        n.links.loc["import", ["p_nom_extendable", "p_nom_max", "capital_cost"]] = \
            [True, 120.0, 0.0]
    return n


def _commercial(*items, **kw):
    return {"poc_link": "import", "import_tariff": _tariff(*items), **kw}


def _solve(n, commercial, **cfg_kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, **cfg_kw)
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, cond)
    return cfg


def _rows(n, cfg):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb)["gap_pct"], cb["commercial"]


# ── spec ───────────────────────────────────────────────────────────────────


def test_capacity_items_are_lp_terms_except_per_kva():
    items = Tariff.model_validate(_tariff(CAP, LEISTUNG, KVA)).items
    assert [L._lp_reason(i) for i in items] == [None, None, "per_kva_year_not_in_lp"]


def test_capacity_items_are_neither_energy_prices_nor_demand_keys():
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(TOU, CAP, LEISTUNG))
    f = applied.facts
    assert f["energy_items"] == ["energy"] and f["demand_items"] == []
    assert sorted(f["capacity_items"]) == ["cap", "lp"]
    assert n.links_t.marginal_cost["import"].eq(100.0).all()     # the energy item only
    spec = getattr(n, L.CAPACITY_SPEC_ATTR)
    assert spec["contracted"] == [{"item": "cap", "eur_per_mw_year": 400_000.0}]
    assert {k["year"] for k in spec["peaks"]} == {"2030"}
    applied.undo()
    assert not hasattr(n, L.CAPACITY_SPEC_ATTR)


def test_adding_a_capacity_item_leaves_the_energy_hash_unchanged():
    from models.commercial import CommercialConfig

    n = _site()
    a = CommercialConfig.model_validate(_commercial(TOU))
    b = CommercialConfig.model_validate(_commercial(TOU, CAP))
    assert L.energy_hash(n, a) == L.energy_hash(n, b)


# ── LP == engine ───────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_contracted_capacity_on_an_extendable_poc_is_an_lp_term_and_equals_the_bill():
    n = _site()
    commercial = _commercial(TOU, CAP)
    cfg = _solve(n, commercial)
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    bill = B.bill_site(n, commercial).per_period[None]
    assert rows["tariff_capacity"] == pytest.approx(bill.per_item["cap"], rel=1e-9)
    size = float(n.links.at["import", "p_nom_opt"])
    assert size < 80.0                         # the capacity price makes the LP size it down
    assert rows["tariff_capacity"] == pytest.approx(
        400_000.0 * size * 7 * 24 / 8760.0, rel=1e-9)


@pytest.mark.live_solve
def test_contracted_capacity_on_a_fixed_poc_is_reported_outside_the_total():
    n = _site(extendable=False)
    cfg = _solve(n, _commercial(TOU, CAP))
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    fixed = rows["tariff_capacity_fixed"]
    assert fixed["included_in_total"] is False
    assert fixed["eur"] == pytest.approx(400_000.0 * 80.0 * 7 * 24 / 8760.0, rel=1e-9)
    assert "network_capacity_fixed" in fixed["flags"]
    assert rows.get("tariff_capacity") is None


@pytest.mark.live_solve
def test_a_measured_peak_capacity_item_is_the_annual_peak_and_equals_the_bill():
    n = _site(extendable=False)
    commercial = _commercial(TOU, LEISTUNG)
    cfg = _solve(n, commercial)
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    bill = B.bill_site(n, commercial).per_period[None]
    assert rows["tariff_capacity"] == pytest.approx(bill.per_item["lp"], rel=1e-9)
    (rec,) = n.meta[L.META_CAPACITY]["peaks"].values()
    assert rec["peak_mw"] < 45.0                 # the evening peak is shaved by the BESS


@pytest.mark.live_solve
def test_capacity_follows_the_active_periods_of_a_two_period_poc():
    n = _site()
    n.set_investment_periods([2030, 2040])
    n.investment_period_weightings["years"] = 10.0
    n.investment_period_weightings["objective"] = 10.0     # undiscounted, consistent
    n.links.loc["import", ["build_year", "lifetime"]] = [2030, 5]     # retired before 2040
    n.add("Carrier", "diesel")
    n.add("Generator", "backup", bus="site", carrier="diesel", p_nom=100.0,
          marginal_cost=500.0)
    commercial = _commercial(TOU, CAP)
    cfg = _solve(n, commercial, multi_investment_periods=True)
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    rec = n.meta[L.META_CAPACITY]["contracted"]["cap"]
    assert set(rec["eur_per_mw_by_period"]) == {"2030"}
    bill = B.bill_site(n, commercial).per_period                    # the bill agrees
    size = float(n.links.at["import", "p_nom_opt"])
    assert bill[2030].per_item["cap"] == pytest.approx(
        rec["eur_per_mw_by_period"]["2030"] * size, rel=1e-9)
    assert bill[2040].per_item["cap"] == 0.0


# ── drift ──────────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_a_capacity_rate_changed_after_the_solve_is_drift_and_an_old_recipe_is_not():
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial(TOU, CAP)
    _solve(n, commercial)
    assert "config_changed_since_solve" not in commercial_cost_terms(n, commercial)["flags"]
    edited = _commercial(TOU, {**CAP, "periods": [{"name": "all", "rate": 500.0}]})
    assert "config_changed_since_solve" in commercial_cost_terms(n, edited)["flags"]
    assert "config_changed_since_solve" in B.bill_site(n, edited).flags
    # A solve before WP2.1c-iii bound no capacity item: a recipe change.
    n.meta.pop(L.META_CAPACITY)
    n.meta[L.META_LINKS]["lp_recipe"] = 3
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert {"tariff_capacity_not_established", "capacity_recipe_changed"} <= set(flags)
    assert "config_changed_since_solve" not in flags
    bill = B.bill_site(n, commercial).flags
    assert "capacity_recipe_changed" in bill and "config_changed_since_solve" not in bill


def test_a_capacity_priced_poc_needs_no_capital_cost_but_other_links_still_do():
    from services.solver_service import SolverConfig
    from services.validation_service import validate_for_run

    def codes(commercial):
        n = _site()
        cfg = SolverConfig(commercial=commercial)
        return {(i.code, i.name) for i in validate_for_run(n, cfg) if i.severity == "error"}

    assert ("link_no_capital_cost", "import") not in codes(_commercial(TOU, CAP))
    assert ("link_no_capital_cost", "import") in codes(_commercial(TOU))
    assert ("link_no_capital_cost", "import") in codes(_commercial(TOU, LEISTUNG))


# ── WP2.1c-iii review round 1 ──────────────────────────────────────────────


def _backed_up(n):
    n.add("Carrier", "diesel")
    n.add("Generator", "backup", bus="site", carrier="diesel", p_nom=100.0, marginal_cost=500.0)
    return n


@pytest.mark.live_solve
def test_the_bill_charges_the_periods_the_solve_charged_when_the_connection_opens_late():
    """#1: available_from moves the PoC's build_year for the solve only; the
    bill reads the charged periods from the record, not the restored build_year."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _backed_up(_site())
    n.set_investment_periods([2030, 2040])
    n.investment_period_weightings["years"] = 10.0
    n.investment_period_weightings["objective"] = 10.0
    commercial = _commercial(TOU, CAP, connection={
        "kind": "firm", "import_cap_mw": 120.0, "available_from": "2035-01-01"})
    cfg = _solve(n, commercial, multi_investment_periods=True)
    gap, _rows_ = _rows(n, cfg)
    assert abs(gap) < 1e-6
    rows = {p: a + b for lab, p, a, b in commercial_cost_terms(n, commercial)["items"]
            if lab == "tariff_capacity"}
    bill = B.bill_site(n, commercial).per_period
    assert set(rows) == {2040}
    assert bill[2030].per_item["cap"] == 0.0
    assert bill[2040].per_item["cap"] == pytest.approx(rows[2040], rel=1e-9)


def test_a_negative_capacity_rate_is_refused():
    """#2: a credit would build the PoC to its max or leave the peak unbounded."""
    for item in (CAP, LEISTUNG):
        neg = {**item, "periods": [{"name": "all", "rate": -50.0}]}
        with pytest.raises(L.CommercialBindingError, match="negative"):
            L.materialise_poc_prices(_site(), _commercial(TOU, neg))


@pytest.mark.live_solve
def test_on_a_flat_axis_contracted_capacity_accrues_over_the_whole_horizon():
    """#3 (convention): the DSO bills the contracted capacity from the start;
    `available_from` gates the flow, not the charge (rows = bill)."""
    n = _site()
    commercial = _commercial(TOU, CAP, connection={
        "kind": "firm", "import_cap_mw": 120.0, "available_from": "2030-01-09"})
    n.add("Carrier", "diesel")
    n.add("Generator", "backup", bus="site", carrier="diesel", p_nom=100.0, marginal_cost=500.0)
    cfg = _solve(n, commercial)
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    size = float(n.links.at["import", "p_nom_opt"])
    assert rows["tariff_capacity"] == pytest.approx(400_000.0 * size * 168 / 8760.0, rel=1e-9)
    assert rows["tariff_capacity"] == pytest.approx(
        B.bill_site(n, commercial).per_period[None].per_item["cap"], rel=1e-9)


@pytest.mark.live_solve
def test_a_partly_unknown_capacity_term_adds_nothing_to_the_totals():
    """#4 (ADR-0001)."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial(TOU, CAP, LEISTUNG)
    _solve(n, commercial)
    n.meta[L.META_CAPACITY]["contracted"]["cap"]["link"] = "ghost"
    out = commercial_cost_terms(n, commercial)
    assert out["block"]["tariff_capacity"] is None
    assert "tariff_capacity_not_established" in out["flags"]
    assert not [i for i in out["items"] if i[0] == "tariff_capacity"]


@pytest.mark.live_solve
def test_a_current_recipe_solve_without_a_record_is_not_established_on_the_bill():
    """#5: not a config change."""
    n = _site()
    commercial = _commercial(TOU, CAP)
    _solve(n, commercial)
    n.meta.pop(L.META_CAPACITY)
    flags = B.bill_site(n, commercial).flags
    assert "tariff_capacity_not_established" in flags
    assert "config_changed_since_solve" not in flags


def test_windowed_solves_refuse_only_capacity_terms_that_change_per_window():
    """#6: a contracted item on a fixed PoC is a constant."""
    from models.commercial import CommercialConfig

    fixed = _site(extendable=False)
    spec = L._capacity_spec(fixed, CommercialConfig.model_validate(_commercial(TOU, CAP)))
    L.refuse_windowed_terms(None, None, "rolling", False, spec)          # allowed
    ext = _site()
    spec = L._capacity_spec(ext, CommercialConfig.model_validate(_commercial(TOU, CAP)))
    with pytest.raises(L.CommercialBindingError):
        L.refuse_windowed_terms(None, None, "rolling", False, spec)
    spec = L._capacity_spec(fixed, CommercialConfig.model_validate(_commercial(TOU, LEISTUNG)))
    with pytest.raises(L.CommercialBindingError):
        L.refuse_windowed_terms(None, None, "rolling", False, spec)
