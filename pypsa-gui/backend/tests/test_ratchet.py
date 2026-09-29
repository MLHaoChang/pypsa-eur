"""
Demand ratchets (Edge Investment Case P1 WP1.5b).

Plan: docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md WP1.5b
Spec §5 table "Ratchet (share ρ of max over lookback L)".

The billed demand for month m is `max(actual_m, ρ · max(actual_k for k in the
L months before m))`. A lookback month inside the horizon uses the modelled
actual peak; one before it uses `meter_history_peaks_kw`; one in neither is
unknown, and is flagged `ratchet_seed_missing` (no constraint, disclosed). The
ratchet is on ACTUAL peaks, never on billed ones, so a ratcheted month does not
itself seed the next ratchet at ρ² (a plan deviation, recorded there). The
tariff engine bills the same rule on the same dispatch, so LP demand cost equals
the bill.
"""
from __future__ import annotations

import queue
import threading

import numpy as np
import pandas as pd
import pytest

from models.commercial import Tariff
from services.commercial import lp_bindings as L
from services.commercial.tariff_engine import rate
from tests.fixtures.investment_case import edge_15min as F

RATE = 15.0
SHARE = 0.9


def _tariff(lookback=11, share=SHARE):
    return Tariff.model_validate({"id": "t", "name": "t", "jurisdiction": "US",
                                  "valid_from": "2029-01-01", "items": [
        {"id": "demand", "kind": "demand", "unit": "per_kw_month",
         "periods": [{"name": "all", "rate": RATE}], "measured_on": "import",
         "ratchet": {"lookback_months": lookback, "share": share}}]})


# ── engine ─────────────────────────────────────────────────────────────────


def _dispatch(peaks_by_month: dict[str, float]):
    rows = []
    for month, peak in peaks_by_month.items():
        idx = pd.date_range(f"{month}-01", periods=3, freq="h")
        rows.append(pd.DataFrame({"import_mw": [1.0, peak, 1.0], "export_mw": 0.0}, index=idx))
    return pd.concat(rows)


def test_engine_bills_the_ratcheted_demand_on_actual_peaks():
    # Jan 10 MW, Feb 2 MW, Mar 3 MW: billed Jan 10, Feb 9 (0.9×10), Mar 9 (not 8.1).
    res = rate(_dispatch({"2030-01": 10.0, "2030-02": 2.0, "2030-03": 3.0}), _tariff(),
               step_hours=1.0, timezone=None,
               meter_history={f"2029-{m:02d}": 0.0 for m in range(2, 13)})
    dl = res.demand_lines.set_index("month")
    assert dl.loc["2030-02", "billed_kw"] == pytest.approx(9000.0)
    assert dl.loc["2030-03", "billed_kw"] == pytest.approx(9000.0)
    assert res.per_item["demand"] == pytest.approx(RATE * (10000 + 9000 + 9000))


def test_engine_seeds_from_meter_history_and_discloses_a_missing_seed():
    hist = {"2029-12": 20_000.0}  # 20 MW metered in December
    res = rate(_dispatch({"2030-01": 5.0}), _tariff(), step_hours=1.0, timezone=None,
               meter_history=hist)
    assert res.demand_lines["billed_kw"].item() == pytest.approx(18_000.0)
    assert "ratchet_seed_missing" in res.notes["demand"]  # Feb–Nov 2029 unknown
    full = {f"2029-{m:02d}": 0.0 for m in range(2, 13)} | hist
    res2 = rate(_dispatch({"2030-01": 5.0}), _tariff(), step_hours=1.0, timezone=None,
                meter_history=full)
    assert "ratchet_seed_missing" not in res2.notes.get("demand", [])


# ── LP ─────────────────────────────────────────────────────────────────────


def _site(start="2030-01-28 00:00"):
    old = F.START
    F.START = start
    try:
        n = F.build_edge_15min()
    finally:
        F.START = old
    n.generators.loc["grid_supply", "marginal_cost"] = 50.0
    return n


def _commercial(history=None, tariff=None, **kw):
    out = {"poc_link": "import", "import_tariff": (tariff or _tariff()).model_dump(mode="json")}
    if history is not None:
        out["meter_history_peaks_kw"] = history
    out.update(kw)
    return out


def test_ratchet_keys_and_missing_seed_are_reported():
    n = _site()
    applied = L.materialise_poc_prices(n, _commercial(history={"2029-12": 1000.0}))
    spec = getattr(n, L.DEMAND_SPEC_ATTR)
    assert spec["ratchets"], "the ratchet rows are part of the spec"
    assert "ratchet_seed_missing" in applied.facts["notes"]
    applied.undo()


def _solve(n, commercial):
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation

    PyPSAService.set_network(n)
    cfg = SolverConfig(commercial=commercial)
    status, condition = run_simulation(cfg, n, PyPSAService.get_lock(), threading.Event(),
                                       queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, condition)
    return cfg


@pytest.mark.live_solve
def test_a_metered_history_peak_ratchets_the_billed_demand():
    n = _site()
    history = {f"2029-{m:02d}": 0.0 for m in range(2, 13)} | {"2029-12": 100_000.0}
    _solve(n, _commercial(history=history))
    peaks = n.meta[L.META_DEMAND]
    for v in peaks.values():
        assert v["billed_mw"] == pytest.approx(max(v["peak_mw"], SHARE * 100.0), abs=1e-6)


@pytest.mark.live_solve
def test_lp_billed_demand_equals_the_engine_bill_and_gap_is_zero():
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.results.objective_decomposition import compute_objective_decomposition

    n = _site()
    history = {f"2029-{m:02d}": 30_000.0 for m in range(2, 13)}
    cfg = _solve(n, _commercial(history=history))
    peaks = n.meta[L.META_DEMAND]
    lp_cost = sum(v["eur_per_mw"] * v["billed_mw"] for v in peaks.values())
    dispatch = pd.DataFrame({"import_mw": n.links_t.p0["import"].to_numpy(), "export_mw": 0.0},
                            index=n.snapshots)
    billed = rate(dispatch, _tariff(), step_hours=0.25, timezone=None, meter_history=history)
    assert billed.per_item["demand"] == pytest.approx(lp_cost, rel=1e-6)
    cb = compute_cost_breakdown(n, cfg)
    assert abs(compute_objective_decomposition(n, cb)["gap_pct"]) < 1e-6
    assert cb["commercial"]["demand_charge"] == pytest.approx(lp_cost, rel=1e-9)


@pytest.mark.live_solve
def test_an_initial_peak_lower_bound_is_honoured():
    n = _site()
    _solve(n, _commercial(history={f"2029-{m:02d}": 0.0 for m in range(2, 13)},
                          initial_peak_lower_bound={"2030-01": 60.0}))
    jan = [v for v in n.meta[L.META_DEMAND].values() if v["month"] == "2030-01"]
    assert jan and jan[0]["peak_mw"] >= 60.0 - 1e-6
