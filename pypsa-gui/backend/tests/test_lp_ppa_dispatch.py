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


def test_the_adder_is_the_indexed_price_and_undo_restores_the_cost():
    n = _site()
    ppa = {**PPA, "indexation_pct_per_year": 2.0, "base_year": 2028}
    applied = L.materialise_poc_prices(n, _commercial(ppa))
    assert np.allclose(n.generators_t.marginal_cost["pv"], 100.0 * 1.02 ** 2)
    assert applied.facts["ppa_dispatch"] == {"ppa1": ["pv"]}
    applied.undo()
    assert "pv" not in n.generators_t.marginal_cost.columns


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
