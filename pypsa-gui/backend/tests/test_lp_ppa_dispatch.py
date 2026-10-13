"""
A `changes_dispatch` PPA in the LP — buyer case only (Edge Investment Case P2
WP2.2d).

Plan: docs/superpowers/plans/2026-09-27-edge-investment-case-p2.md WP2.2d.
v1 binds `changes_dispatch=True` only for fixed-price pay-as-produced PPAs on
ON-SITE Generators where the site is the buyer (`buyer == site_party`): the
site pays price_y for every MWh the asset produces, so the LP adds +price_y ×
p_gen as a transient `marginal_cost` adder, committed as
`generators_t["ic_ppa_price"]` with the contracts' hash. Exported surplus still
earns the export price. Refused with a reason: the seller case, other kinds,
market_plus_premium, a volume cap, a grid-side or non-Generator asset, a
generator in two such PPAs. Rows add `ppa_settlement`; the gap stays 0; the
settlement line equals the LP row on the same dispatch.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pytest

from models.commercial import CommercialConfig
from services.commercial import billing as B
from services.commercial import contracts as K
from services.commercial import lp_bindings as L
from tests.fixtures.investment_case.edge_15min import build_edge_15min

TOU = {"id": "energy", "kind": "energy", "unit": "per_kwh",
       "periods": [{"name": "all", "rate": 0.02}]}
PPA = {"type": "ppa", "id": "ppa1", "kind": "pay_as_produced", "price": 100.0,
       "tenor_years": 10, "seller": "Solar BV", "buyer": "site", "asset_ids": ["pv"],
       "changes_dispatch": True}


def _tariff():
    return {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2029-01-01",
            "items": [TOU]}


def _commercial(*contracts, **extra):
    return {"poc_link": "import", "import_tariff": _tariff(), "contracts": list(contracts),
            **extra}


def _site():
    n = build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    return n


def _solve(n, commercial, **kw):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial, **kw)
    status, cond = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                  queue.SimpleQueue(), state_update=lambda **k: None)
    assert status in ("ok", "optimal"), (status, cond)
    return cfg


def _rows(n, cfg):
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    cb = compute_cost_breakdown(n, cfg)
    return compute_objective_decomposition(n, cb)["gap_pct"], cb["commercial"]


# ── refusals ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad,match", [
    ({"buyer": "Offtaker", "seller": "site"}, "buyer"),
    ({"kind": "as_consumed_btm"}, "pay_as_produced"),
    ({"pricing": "market_plus_premium", "premium_eur_per_mwh": 1.0,
      "reference_price": {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}},
     "fixed"),
    ({"volume_cap_mwh_per_year": 100.0}, "volume"),
    ({"asset_ids": ["grid_supply"]}, "on-site"),
])
def test_unsupported_dispatch_ppas_are_refused_with_a_reason(bad, match):
    with pytest.raises(L.CommercialBindingError, match=match):
        L.materialise_poc_prices(_site(), _commercial({**PPA, **bad}))


def test_a_generator_in_two_dispatch_ppas_is_refused():
    with pytest.raises(L.CommercialBindingError, match="pv"):
        L.materialise_poc_prices(_site(), _commercial(PPA, {**PPA, "id": "ppa2"}))


def test_the_term_is_the_indexed_price_and_marginal_cost_is_untouched():
    """Review #1: an objective term (`add_ppa_terms`), never a marginal-cost
    adder that a CO2 price could clobber."""
    n = _site()
    ppa = {**PPA, "indexation_pct_per_year": 2.0, "base_year": 2028}
    applied = L.materialise_poc_prices(n, _commercial(ppa))
    assert np.allclose(getattr(n, L.PPA_SPEC_ATTR)["pv"], 100.0 * 1.02 ** 2)
    assert "pv" not in n.generators_t.marginal_cost.columns
    assert applied.facts["ppa_dispatch"] == {"ppa1": ["pv"]}
    applied.undo()
    assert not hasattr(n, L.PPA_SPEC_ATTR)


def test_duplicate_assets_and_a_non_datetime_axis_are_refused():
    """Review #2 (an asset listed twice would settle twice) and #3 (no year to
    index to)."""
    with pytest.raises(ValueError, match="more than once"):
        CommercialConfig.model_validate(_commercial({**PPA, "asset_ids": ["pv", "pv"]}))
    n = _site()
    n.set_snapshots(pd.RangeIndex(len(n.snapshots)))
    with pytest.raises(L.CommercialBindingError, match="not dates"):
        L.materialise_poc_prices(n, {"poc_link": "import", "contracts": [PPA]})


def test_periods_that_are_not_years_are_refused_for_an_indexed_price():
    """Round 2 #1."""
    n = _site()
    n.set_investment_periods([1, 2])
    ppa = {**PPA, "indexation_pct_per_year": 2.0, "base_year": 2030}
    with pytest.raises(L.CommercialBindingError, match="not years"):
        L.materialise_poc_prices(n, _commercial(ppa))
    applied = L.materialise_poc_prices(n, _commercial(PPA))    # no indexation: fine
    applied.undo()


def test_the_modelled_year_is_on_the_site_clock():
    """Review #6: two days either side of New Year in Berlin — the objective-
    weighted majority year on the SITE clock, as `contracts.modelled_year`."""
    n = _site()
    idx = pd.date_range("2029-12-31 00:00", periods=len(n.snapshots), freq="15min")
    n.set_snapshots(idx[:192])
    n.snapshot_weightings.loc[:, :] = 0.25
    shifted = pd.date_range("2029-12-30 23:00", periods=192, freq="15min")   # UTC
    n.set_snapshots(shifted)
    n.snapshot_weightings.loc[:, :] = 0.25
    ppa = {**PPA, "indexation_pct_per_year": 10.0, "base_year": 2029}
    cfg = {**_commercial(ppa), "timezone": "Europe/Berlin"}
    spec = L._ppa_dispatch_spec(n, CommercialConfig.model_validate(cfg))
    local = L._local_clock(n.snapshots, "Europe/Berlin")
    year = pd.Series(0.25, index=local.year).groupby(level=0).sum().idxmax()
    assert spec["pv"][0] == pytest.approx(100.0 * 1.1 ** (year - 2029))


def test_the_hash_ignores_order_and_includes_the_site_party():
    """Review #5."""
    a = CommercialConfig.model_validate(_commercial(PPA, {**PPA, "id": "b", "asset_ids": ["x"]}))
    b = CommercialConfig.model_validate(_commercial({**PPA, "id": "b", "asset_ids": ["x"]}, PPA))
    c = CommercialConfig.model_validate({**_commercial(PPA), "site_party": "Hub"})
    assert L.ppa_dispatch_hash(a) == L.ppa_dispatch_hash(b)
    assert L.ppa_dispatch_hash(c) != L.ppa_dispatch_hash(
        CommercialConfig.model_validate(_commercial(PPA)))


# ── LP ─────────────────────────────────────────────────────────────────────


@pytest.mark.live_solve
def test_the_pv_curtails_when_the_ppa_price_exceeds_the_value_of_its_output():
    free, bound = _site(), _site()
    _solve(free, _commercial())
    _solve(bound, _commercial(PPA))
    w = free.snapshot_weightings.objective
    assert float((w * bound.generators_t.p["pv"]).sum()) < \
        float((w * free.generators_t.p["pv"]).sum()) - 1.0          # imports at 20 €/MWh instead
    assert "pv" not in bound.generators_t.marginal_cost.columns     # undone


@pytest.mark.live_solve
def test_rows_add_the_ppa_settlement_the_gap_stays_zero_and_the_line_equals_the_row():
    n = _site()
    cheap = {**PPA, "price": 10.0}                                   # PV still runs
    commercial = _commercial(cheap)
    cfg = _solve(n, commercial)
    gap, rows = _rows(n, cfg)
    assert abs(gap) < 1e-6
    w = n.snapshot_weightings.objective.to_numpy()
    expected = float((w * n.generators_t.p["pv"].to_numpy() * 10.0).sum())
    assert rows["ppa_settlement"] == pytest.approx(expected, rel=1e-9)
    c = CommercialConfig.model_validate(commercial).contracts[0]
    inp = K.SettlementInputs(period=None, index=n.snapshots, weights=w,
                             generators=n.generators_t.p)
    (line,) = K.settle(c, inp)
    assert line.amount == pytest.approx(rows["ppa_settlement"], rel=1e-9)


@pytest.mark.live_solve
def test_a_ppa_price_changed_after_the_solve_is_drift_in_the_rows_and_on_the_bill():
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    commercial = _commercial({**PPA, "price": 10.0})
    _solve(n, commercial)
    assert "config_changed_since_solve" not in commercial_cost_terms(n, commercial)["flags"]
    edited = _commercial({**PPA, "price": 12.0})
    assert "config_changed_since_solve" in commercial_cost_terms(n, edited)["flags"]
    assert "config_changed_since_solve" in B.bill_site(n, edited).flags
    # A solve before WP2.2d bound no dispatch PPA: a recipe change, not drift.
    n.meta.pop(L.META_PPA)
    n.meta[L.META_LINKS]["lp_recipe"] = 4
    flags = commercial_cost_terms(n, commercial)["flags"]
    assert {"ppa_settlement_not_established", "ppa_recipe_changed"} <= set(flags)
    assert "config_changed_since_solve" not in flags


@pytest.mark.live_solve
@pytest.mark.parametrize("co2", ["uniform", "per_period"])
def test_a_co2_price_and_the_ppa_both_reach_the_lp(co2):
    """Review #1 (HIGH): on an emitting on-site generator the LP sees BOTH the
    CO2 price and the PPA price. The merit order proves it: the CHP costs
    30 + 0.2/0.4 × 100 = 80 €/MWh with CO2, 90 with the PPA as well; an 85 €/MWh
    backup beats it only when both are in the objective (the CO2 price alone
    leaves the CHP at 80, the PPA alone at 40)."""
    def build():
        n = _site()
        n.add("Carrier", "gas", co2_emissions=0.2)
        n.add("Generator", "chp", bus="site", carrier="gas", p_nom=20.0, marginal_cost=30.0,
              efficiency=0.4)
        n.add("Generator", "backup", bus="site", carrier="grid", p_nom=200.0,
              marginal_cost=85.0)
        n.generators.loc["grid_supply", "marginal_cost"] = 300.0
        kw = {"co2_price": 100.0}
        if co2 == "per_period":
            n.set_investment_periods([2030, 2040])
            n.investment_period_weightings["years"] = 10.0
            n.investment_period_weightings["objective"] = 10.0
            kw = {"co2_price_per_period": {2030: 100.0, 2040: 100.0},
                  "multi_investment_periods": True}
        return n, kw

    ppa = {**PPA, "id": "chp_ppa", "price": 10.0, "asset_ids": ["chp"]}
    control, kw = build()
    _solve(control, _commercial(), **kw)
    bound, kw = build()
    _solve(bound, _commercial(ppa), **kw)
    w = control.snapshot_weightings.objective
    assert float((w * control.generators_t.p["chp"]).sum()) > 1.0     # 80 < 85: it runs
    assert float((w * bound.generators_t.p["chp"]).sum()) == pytest.approx(0.0, abs=1e-6)
    assert float((w * bound.generators_t.p["backup"]).sum()) > 1.0
    assert "chp" not in bound.generators_t.marginal_cost.columns or \
        not np.allclose(bound.generators_t.marginal_cost["chp"], 90.0)


@pytest.mark.live_solve
def test_nan_output_makes_the_row_not_established():
    """Review #4."""
    n = _site()
    cfg = _solve(n, _commercial({**PPA, "price": 10.0}))
    n.generators_t.p.iloc[:20, n.generators_t.p.columns.get_loc("pv")] = np.nan
    _, rows = _rows(n, cfg)
    assert rows["ppa_settlement"] is None
    assert "ppa_settlement_not_established" in rows["flags"]


@pytest.mark.live_solve
def test_a_ppa_added_after_a_current_recipe_solve_is_drift_in_rows_and_bill():
    """Review #7: the rows say what the bill says."""
    from services.commercial.cost_rows import commercial_cost_terms

    n = _site()
    _solve(n, _commercial())
    later = _commercial({**PPA, "price": 10.0})
    assert "config_changed_since_solve" in commercial_cost_terms(n, later)["flags"]
    assert "config_changed_since_solve" in B.bill_site(n, later).flags


@pytest.mark.live_solve
def test_two_periods_index_the_price_per_period():
    n = _site()
    n.set_investment_periods([2030, 2040])
    n.investment_period_weightings["years"] = 10.0
    n.investment_period_weightings["objective"] = 10.0
    ppa = {**PPA, "price": 10.0, "indexation_pct_per_year": 1.0, "base_year": 2030}
    cfg = _solve(n, _commercial(ppa), multi_investment_periods=True)
    gap, _ = _rows(n, cfg)
    assert abs(gap) < 1e-6
    prices = n.generators_t["ic_ppa_price"]["pv"]
    assert prices.loc[2030].iloc[0] == pytest.approx(10.0)
    assert prices.loc[2040].iloc[0] == pytest.approx(10.0 * 1.01 ** 10)



def test_preflight_and_binding_refuse_an_unbindable_dispatch_ppa():
    from services.commercial.preflight import commercial_findings

    bad = _commercial({**PPA, "kind": "as_consumed_btm"})
    codes = {c for _, c, *_ in commercial_findings(_site(), bad)}
    assert codes == {"commercial.binding_invalid"}
    with pytest.raises(L.CommercialBindingError, match="pay_as_produced"):
        L.validate_for_network(_site(), bad)



def test_the_buyer_matches_the_site_party_trimmed_and_in_any_case():
    """WP2.2c round 1 #3 applies to the buyer check too."""
    applied = L.materialise_poc_prices(_site(), _commercial({**PPA, "buyer": " SITE "}))
    assert applied.facts["ppa_dispatch"] == {"ppa1": ["pv"]}
    applied.undo()
