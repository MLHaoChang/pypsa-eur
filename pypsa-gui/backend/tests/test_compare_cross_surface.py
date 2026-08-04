"""
Cross-surface checks: does the Compare tab agree with the live `/results/*`
endpoints that compute the same quantities from the active network?

`install_golden(n)` makes the golden network the live singleton so the
`routers.results` / `routers.simulation` functions imported here read the
SAME network `compare_support.summarise()` reads. Every test below reads the
target function/endpoint's REAL response shape (verified by hand against a
running golden network before the assertion was written — see the
task-5-6-report.md for the raw probe output) rather than guessing key names,
and carries a `compared >= 1` (or equivalent) guard so a silent shape drift
fails loudly instead of iterating zero times and passing vacuously.
"""
from __future__ import annotations

import pytest

from tests import compare_support as cs
from tests.golden import fixture as gf


@pytest.fixture()
def golden(reset_backend):
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return n


# ── Task 5: Capacity tab — CAPEX vs. periodized_capital_costs ──────────────

def test_capacity_capex_agrees_with_periodized_capital_costs(golden):
    """
    Σ per-carrier capex_meur must equal Σ over assets of
    periodized_capital_costs × p_nom_opt × horizon years — the resolution
    asset_economics, cost_breakdown and asset_costs all share.
    """
    from services.solver_service import periodized_capital_costs
    from routers.simulation import _state

    cap = cs.summarise(golden)["capacity"]
    tab_total_eur = sum(c.total for c in cap.capex_meur_by_carrier.values()) * 1e6

    pcc = periodized_capital_costs(golden, _state.get("solver_config"))
    horizon_years = float(sum(gf.GOLDEN_YEARS))
    expected = 0.0
    for attr, nom in (("generators", "p_nom"), ("storage_units", "p_nom"),
                      ("stores", "e_nom")):
        df = getattr(golden, attr)
        for name in df.index:
            cc = pcc.get(attr, {}).get(name, {}).get("capital_cost", 0.0)
            opt = float(df.at[name, f"{nom}_opt"] if f"{nom}_opt" in df.columns
                        else df.at[name, nom])
            expected += cc * opt * horizon_years

    assert tab_total_eur == pytest.approx(expected, rel=1e-6)


# ── Task 6: Dispatch tab vs. /results/carrier_kpis ──────────────────────────

def test_dispatch_agrees_with_carrier_kpis(golden):
    """
    `GET /results/carrier_kpis` returns `{"rows": [...]}` — NOT `{"carriers":
    [...]}`. Each row is `{"component", "carrier", "capacity_mw",
    "energy_mwh", "capacity_factor_pct", "curtailment_mwh", "curtailment_pct",
    "market_value_eur_per_mwh", "revenue_eur"}` (verified by calling
    `get_carrier_kpis()` directly against the solved golden network — see
    task-5-6-report.md for the raw dump). There is no `energy_gwh` key: energy
    is `energy_mwh`, in MWh, so it must be divided by 1000 before comparing to
    the Compare tab's `dispatch_gwh_by_carrier` (GWh). Rows are per
    (component, carrier) — e.g. a Link's H2 energy and a Generator's gas
    energy are separate rows — so this only compares the component classes
    the dispatch tab itself aggregates (Generator, StorageUnit; see
    `_compute_dispatch_summary` in routers/compare.py, which walks
    `n.generators_t.p` and the clipped-non-negative half of
    `n.storage_units_t.p`, and does NOT include Links or Stores).
    """
    import routers.results as R

    disp = cs.summarise(golden)["dispatch"]
    kpis = R.get_carrier_kpis()
    # Shape check first — a silently-renamed top-level key would otherwise
    # make the loop below iterate zero times and pass vacuously.
    assert "rows" in kpis and kpis["rows"], (
        "carrier_kpis returned nothing for a solved network")
    compared = 0
    for row in kpis["rows"]:
        if row.get("component") not in ("Generator", "StorageUnit"):
            continue
        carrier = str(row.get("carrier", "")).lower()
        energy_mwh = row.get("energy_mwh")
        if carrier not in disp.dispatch_gwh_by_carrier or energy_mwh is None:
            continue
        want_gwh = energy_mwh / 1000.0
        assert disp.dispatch_gwh_by_carrier[carrier].total == pytest.approx(want_gwh, rel=1e-6)
        compared += 1
    assert compared >= 1, "no carrier compared — key names have drifted"


# ── Task 8: Prices tab vs. /results/prices ──────────────────────────────────

def test_prices_mean_agrees_with_results_prices(golden):
    """
    `GET /results/prices` returns `{"index", "columns", "data", "data_adjusted",
    "periods", "source", "negative_hours", "fallback_per_snapshot", "note"}`
    (verified by calling `get_prices()` directly against the solved golden
    network). On the golden fixture: `index` is 48 ISO timestamps, `columns`
    is the 4 bus names (`elec_a`, `elec_b`, `h2`, `iso_bus`), `data` and
    `data_adjusted` are both 48x4 matrices of EUR/MWh (in this fixture they
    happen to be numerically identical cell-for-cell — no subsidised
    renewable ever sets the dual — but `data_adjusted` is the semantically
    correct one to compare against: it's the merit-order-corrected series,
    matching what `_compute_prices_summary` reads via
    `corrected_marginal_prices`, per that function's own docstring). There is
    no top-level "mean"/"prices" key, so the tab's `mean_price.total` is
    recomputed here directly from the raw matrix as the snapshot-weighted
    mean, using the SAME weighting basis `_compute_prices_summary` uses:
    `_build_snapshot_weights(n)` with no explicit column defaults to
    `period_utils.snapshot_weights(n, "objective")`.

    Verified numerically before this assertion was written: weighted mean of
    `data_adjusted` == `prices.mean_price.total` exactly (ratio 1.0, 0
    differing cells) on the golden fixture — see task-5b-7-8-report.md.
    """
    import numpy as np

    import routers.results as R
    from services import period_utils

    prices = cs.summarise(golden)["prices"]
    resp = R.get_prices()
    # Shape check first — a silently-renamed/-reshaped payload would otherwise
    # make the weighted-mean computation below silently compare against an
    # empty or malformed matrix and pass vacuously.
    assert "data_adjusted" in resp and resp["data_adjusted"], (
        "get_prices returned nothing for a solved network")
    data_adj = np.asarray(resp["data_adjusted"], dtype=float)
    assert data_adj.shape == (len(golden.snapshots), len(golden.buses)), (
        f"unexpected /results/prices shape {data_adj.shape} for "
        f"{len(golden.snapshots)} snapshots x {len(golden.buses)} buses — "
        "key names or payload shape have drifted")

    w = period_utils.snapshot_weights(golden, "objective")
    w_full = np.broadcast_to(np.asarray(w.values).reshape(-1, 1), data_adj.shape)
    compared = int(data_adj.size)
    assert compared >= 1, "no price cells returned — shape has drifted"
    want = float((data_adj * w_full).sum() / w_full.sum())
    assert prices.mean_price.total == pytest.approx(want, rel=1e-6)
