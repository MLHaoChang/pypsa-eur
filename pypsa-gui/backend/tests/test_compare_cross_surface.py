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

from tests import compare_local_networks as cln
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


# ── Task 9: Emissions tab vs. /results/emissions ────────────────────────────

def test_emissions_agree_with_the_results_emissions_endpoint(golden):
    """
    `GET /results/emissions` (`R.get_emissions()`) returns a dict with keys
    `{'total_tCO2', 'by_carrier', 'by_generator', 'cap', 'caps',
    'is_multi_period', 'by_period'}` — verified by calling `get_emissions()`
    directly against the solved golden network. NONE of the brief's guessed
    total-key candidates (`total_kt`, `total_co2_kt`, `total`) exist in the
    real response; the actual key is `total_tCO2`. It is also in a DIFFERENT
    UNIT than the Compare tab's `total_kt` — tonnes, not kilotonnes (1 kt =
    1000 t) — so a bare key rename would not have been enough.

    Measured on the golden fixture: live `total_tCO2` = 8537.142857142859 t;
    Compare `total_kt.total` = 8.53714285714286 kt;
    8537.142857142859 / 1000 == 8.53714285714286 (ratio 1.0 exactly).
    """
    import routers.results as R

    em = cs.summarise(golden)["emissions"]
    live = R.get_emissions()
    # Shape check first — a silently-renamed/-reshaped payload would
    # otherwise let the comparison below fail with a confusing KeyError
    # instead of a clear "the endpoint shape moved" message.
    assert live, "emissions endpoint returned nothing"
    assert "total_tCO2" in live, f"no total_tCO2 key in {sorted(live)}"
    assert em.total_kt.total == pytest.approx(live["total_tCO2"] / 1000.0, rel=1e-6)


# ── Task 10: Economics tab vs. /results/asset_economics (suspect S3) ────────

def test_compare_capex_agrees_with_asset_economics_per_carrier(golden):
    """
    `GET /results/asset_economics` (`R.get_asset_economics()`) returns a dict
    with keys `{'currency', 'is_multi_period', 'periods', 'generators',
    'storage_units', 'stores', 'links'}` — verified by calling
    `get_asset_economics()` directly against the solved golden network.
    Unlike Task 6's `carrier_kpis` guess, the brief's bucket names here
    (`generators`/`storage_units`/`stores`/`links`) and field name
    (`fixed_cost_eur`) are CORRECT; each row also carries `carrier`.
    `fixed_cost_eur` is already horizon-total (the 922eb4d0 fix:
    `fixed_cost = fixed_cost_annual * total_years_factor` — see
    `get_asset_economics`'s own docstring), matching Compare's `capex_meur`
    (also horizon-total, per the S3 identity test above).

    Measured: gas/solar/diesel/h2 all agree exactly (ratio 1.0 — e.g. gas
    376323.6185743867 == 376323.6185743867). The lone `storage_units` row
    (`bess`) carries `carrier=''` (StorageUnit has no carrier set in this
    fixture) so it never matches a Compare carrier bucket and is correctly
    excluded rather than silently miscounted.
    """
    import routers.results as R

    econ = cs.summarise(golden)["economics"]
    live = R.get_asset_economics()
    by_carrier = {}
    for bucket in ("generators", "storage_units", "stores", "links"):
        for row in live.get(bucket, []) or []:
            c = str(row.get("carrier", "") or "").lower()
            by_carrier[c] = by_carrier.get(c, 0.0) + float(row.get("fixed_cost_eur") or 0.0)
    compared = 0
    for carrier, e in econ.by_carrier.items():
        if carrier not in by_carrier:
            continue
        assert e.capex_meur.total * 1e6 == pytest.approx(by_carrier[carrier], rel=1e-6), carrier
        compared += 1
    assert compared >= 1, "no carrier compared — asset_economics keys have drifted"


# ── Task 11: Curtailment tab vs. /results/curtailment ───────────────────────
#
# The golden fixture never curtails at all (see test_compare_invariants.py's
# KNOWN_VACUOUS_TABS["curtailment"] and compare_local_networks.py's module
# docstring), so this reads live data from the SAME purpose-built local
# network `test_compare_invariants.py`'s Task 11 identity tests use, rather
# than `golden`.

def test_curtailment_agrees_with_results_curtailment():
    """
    `GET /results/curtailment` (`R.get_curtailment()`) returns `{index,
    columns, data}` — a raw PER-SNAPSHOT MW time series per curtailable
    generator (`(p_max - p).clip(lower=0)`, renewable-carrier-filtered), NOT
    a pre-aggregated GWh total. Verified by calling `get_curtailment()`
    directly against `compare_local_networks.solve_curtailment_network()`
    (there is no `"gwh"`/`"total"` key at all — the brief's guess of a
    pre-aggregated payload does not hold here any more than Task 6's
    `carrier_kpis` guess held for `energy_gwh`).

    Recomputed here as the SAME weighted-sum-to-GWh
    `_compute_curtailment_summary` performs (`_build_snapshot_weights(n,
    "generators")`, then ÷1000 for GWh) and compared against the Compare
    tab's `total_gwh.total`.

    Measured on the local curtailment network: live `data` = `[[80.0],
    [40.0], [80.0], [40.0]]` under `columns=['solar']`, summing to 240.0 MWh;
    Compare's `total_gwh.total` = 0.24 GWh; 240.0 / 1000 == 0.24 (ratio 1.0
    exactly, 0 differing cells).
    """
    import numpy as np

    import routers.results as R
    from services import period_utils

    n = cln.solve_curtailment_network()
    cln.install_network(n)

    cur = cs.summarise(n)["curtailment"]
    resp = R.get_curtailment()
    # Shape check first — a silently-renamed/-reshaped payload would
    # otherwise make the weighted-sum computation below silently compare
    # against an empty or malformed matrix and pass vacuously.
    assert "data" in resp and resp["data"], (
        "curtailment endpoint returned nothing for a solved, curtailing network")
    assert resp.get("columns"), f"no curtailable columns in response: {sorted(resp)}"

    data = np.asarray(resp["data"], dtype=float)
    assert data.shape == (len(n.snapshots), len(resp["columns"])), (
        f"unexpected /results/curtailment shape {data.shape} for "
        f"{len(n.snapshots)} snapshots x {len(resp['columns'])} curtailable "
        "generators — key names or payload shape have drifted")

    w = period_utils.snapshot_weights(n, "generators")
    w_full = np.broadcast_to(np.asarray(w.values).reshape(-1, 1), data.shape)
    compared = int(data.size)
    assert compared >= 1, "no curtailment cells returned — shape has drifted"
    want_gwh = float((data * w_full).sum()) / 1000.0
    assert cur.total_gwh.total == pytest.approx(want_gwh, rel=1e-6)
