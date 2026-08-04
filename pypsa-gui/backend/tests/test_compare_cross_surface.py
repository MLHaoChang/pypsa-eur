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
