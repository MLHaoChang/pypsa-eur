"""
Tiered energy rates (Edge Investment Case P1 WP1.5c).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.5c
Spec §5 table "Tiered rates", §5.3 (non-convex tiers).

Tiers apply to the cumulative MONTHLY volume (kWh). Rising marginal rates are
convex: the LP fills stacked volumes `ic_tier_q[k] ≤ width_k` whose sum is the
month's import energy, at `Σ rate_k · q_k`, and the bill on the same dispatch
matches exactly. Falling rates (volume discounts) are non-convex in a
minimisation. They are flagged `nonconvex_tier`, the LP prices them at the
first tier's rate (no volume history in P1, disclosed), and the engine bills
them exactly.
"""
from __future__ import annotations

import queue
import threading

import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial import lp_bindings as L
from services.commercial.tariff_engine import rate
from tests.fixtures.investment_case import edge_15min as F


def _tiered(tiers, periods=None, item_id="tiered"):
    return {"id": item_id, "kind": "energy", "unit": "per_kwh", "measured_on": "import",
            "periods": periods or [{"name": "all", "rate": 0.0}], "tiers": tiers}


def _tariff(*items):
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "NL",
                                  "valid_from": "2030-01-01", "items": list(items)})


RISING = [{"threshold": 0, "rate": 0.10}, {"threshold": 100_000, "rate": 0.30}]
FALLING = [{"threshold": 0, "rate": 0.30}, {"threshold": 100_000, "rate": 0.10}]


# ── engine ─────────────────────────────────────────────────────────────────


def test_engine_bills_cumulative_monthly_volume_by_tier():
    idx = pd.date_range("2030-01-31 22:00", periods=4, freq="h")  # 2 h Jan, 2 h Feb
    df = pd.DataFrame({"import_mw": [0.075, 0.075, 0.05, 0.05], "export_mw": 0.0}, index=idx)
    t = _tariff(_tiered([{"threshold": 0, "rate": 0.1}, {"threshold": 100, "rate": 0.2}]))
    res = rate(df, t, step_hours=1.0, timezone=None)
    # Jan: 150 kWh → 100×0.1 + 50×0.2 = 20; Feb: 100 kWh → 100×0.1 = 10.
    assert res.per_item["tiered"] == pytest.approx(30.0)
    assert res.monthly.loc["2030-01", "tiered"] == pytest.approx(20.0)


def test_engine_refuses_tiers_inside_time_windows():
    item = _tiered(RISING, periods=[{"name": "peak", "rate": 0.0, "start_hour": 8, "end_hour": 20},
                                    {"name": "rest", "rate": 0.0}])
    idx = pd.date_range("2030-01-07", periods=2, freq="h")
    res = rate(pd.DataFrame({"import_mw": 1.0, "export_mw": 0.0}, index=idx), _tariff(item),
               step_hours=1.0, timezone=None)
    assert res.flags["tiered"] == ["unsupported:tiers_with_windows"]


# ── LP ─────────────────────────────────────────────────────────────────────


def _site():
    n = F.build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    return n


def _solve(n, tariff):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    commercial = {"poc_link": "import", "import_tariff": tariff.model_dump(mode="json")}
    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial)
    sink: dict = {}
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(),
                                       state_update=lambda **kw: sink.update(kw))
    assert status in ("ok", "optimal"), (status, condition)
    return cfg, sink


def test_rising_tiers_are_lp_terms_not_flat_prices():
    n = _site()
    applied = L.materialise_poc_prices(
        n, {"poc_link": "import", "import_tariff": _tariff(_tiered(RISING)).model_dump(mode="json")})
    assert applied.facts["tiered_items"] == ["tiered"]
    assert "import" not in n.links_t.marginal_cost.columns  # nothing flat to add
    assert getattr(n, L.TIER_SPEC_ATTR)["keys"]
    applied.undo()
    assert not hasattr(n, L.TIER_SPEC_ATTR)


@pytest.mark.live_solve
def test_lp_tier_cost_equals_the_engine_and_gap_is_zero():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _site()
    t = _tariff(_tiered(RISING))
    cfg, _ = _solve(n, t)
    vols = n.meta[L.META_TIERS]
    lp_cost = sum(v["rate_eur_per_mwh"] * v["q_mwh"] for v in vols.values())
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                            index=n.snapshots)
    billed = rate(dispatch, t, step_hours=0.25, timezone=None)
    assert billed.per_item["tiered"] == pytest.approx(lp_cost, rel=1e-6)
    cb = compute_cost_breakdown(n, cfg)
    assert abs(compute_objective_decomposition(n, cb)["gap_pct"]) < 1e-6
    assert cb["commercial"]["energy_tiers"] == pytest.approx(lp_cost, rel=1e-9)


@pytest.mark.live_solve
def test_falling_tiers_are_flagged_and_priced_at_the_first_tier():
    n = _site()
    t = _tariff(_tiered(FALLING))
    _, sink = _solve(n, t)
    terms = sink["last_commercial_terms"]
    assert "nonconvex_tier" in terms["notes"]
    assert terms["nonconvex_tier_items"] == ["tiered"]
    # The LP price is the first tier's rate, the flat adder on the import Link.
    assert (n.links_t[L.ENERGY_PRICE_ATTR]["import"] == 300.0).all()
    # The engine bills the real (cheaper) volume discount on the same dispatch.
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                            index=n.snapshots)
    billed = rate(dispatch, t, step_hours=0.25, timezone=None).per_item["tiered"]
    lp = float((n.snapshot_weightings.objective * n.links_t.p0["import"] * 300.0).sum())
    assert billed < lp


def test_windowed_tiers_are_left_out_of_the_lp_with_a_reason():
    n = _site()
    item = _tiered(RISING, periods=[{"name": "peak", "rate": 0.0, "start_hour": 8, "end_hour": 20},
                                    {"name": "rest", "rate": 0.0}])
    applied = L.materialise_poc_prices(
        n, {"poc_link": "import", "import_tariff": _tariff(item).model_dump(mode="json")})
    assert applied.facts["not_in_lp"] == {"tiered": "tiers_with_windows"}
