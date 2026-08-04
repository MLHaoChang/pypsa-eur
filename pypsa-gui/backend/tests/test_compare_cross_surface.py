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
