"""
What `/results/asset_economics` reconciles with, measured rather than asserted.

Its docstring told the reader that `Σ fixed_cost_eur == Σ
economics_by_carrier.capex_meur × 1e6` holds EXACTLY, quoted a measured figure
for both sides, and said "that is the reconciliation to quote". The identity
holds only on a network with no branch capex. `services/compare/economics.py`
— the engine behind `economics_by_carrier` — deliberately walks `Line` and
`Transformer` capex, per the 2026-08-13 ruling that a line's capital cost is
part of what the system costs; `asset_economics` covers Generator,
StorageUnit, Store and Link and has no `lines` key at all.

So on any network with expandable or priced transmission the two differ by
exactly the branch capex, and the docstring invited the user to quote that
mismatch as a consistency proof. The third bullet of the same docstring
already flagged branch capex — against `cost_breakdown.capex` — which is
what made the first bullet read as settled.

These tests pin the identity WITH its scope, in both directions, so the
docstring cannot drift back.
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from routers.results import get_asset_economics, get_economics_by_carrier


def _two_bus_network(*, line_capital_cost: float) -> pypsa.Network:
    """One generator behind one line. The line carries carrier `AC`, which no
    generator uses, so the `ac` bucket in the per-carrier roll-up IS the branch
    capex and nothing else."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))
    n.add("Bus", "B0")
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=100.0)
    n.add("Generator", "gas", bus="B0", carrier="gas",
          p_nom=200.0, marginal_cost=50.0, capital_cost=100_000.0)
    n.add("Line", "LN", bus0="B0", bus1="B1", x=0.1, r=0.01, s_nom=200.0,
          carrier="AC", capital_cost=line_capital_cost)
    n.optimize(solver_name="highs")
    return n


def _total_fixed_cost(payload) -> float:
    total = 0.0
    for group in ("generators", "links", "storage_units", "stores"):
        for row in payload.get(group) or []:
            total += float(row.get("fixed_cost_eur") or 0.0)
    return total


def _total_capex_eur(payload, *, carriers=None) -> float:
    total = 0.0
    for carrier, vals in (payload.get("by_carrier") or {}).items():
        if carriers is not None and carrier not in carriers:
            continue
        capex = vals.get("capex_meur") or {}
        total += float(capex.get("total") or 0.0) * 1e6
    return total


def test_without_branch_capex_the_identity_holds_exactly(install_network):
    # The docstring's claim, correctly scoped: with no branch capex there is
    # nothing for the per-carrier roll-up to see that this endpoint does not.
    install_network(_two_bus_network(line_capital_cost=0.0))
    fixed = _total_fixed_cost(get_asset_economics())
    capex = _total_capex_eur(get_economics_by_carrier())
    assert fixed == pytest.approx(capex, rel=1e-12)
    assert fixed > 0.0, "the fixture stopped producing any capex at all"


def test_with_branch_capex_the_two_differ_by_exactly_the_branch_capex(install_network):
    install_network(_two_bus_network(line_capital_cost=7_000.0))
    by_carrier = get_economics_by_carrier()
    fixed = _total_fixed_cost(get_asset_economics())
    capex_all = _total_capex_eur(by_carrier)
    branch_capex = _total_capex_eur(by_carrier, carriers={"ac"})

    assert branch_capex > 0.0, (
        "the fixture priced a line but the per-carrier roll-up reports no "
        "branch capex — the premise of this test is gone"
    )
    # Not equal. This is the claim the docstring got wrong.
    assert fixed != pytest.approx(capex_all, rel=1e-9)
    # And the whole difference is the branch capex, nothing else.
    assert capex_all - fixed == pytest.approx(branch_capex, rel=1e-9)


def test_the_docstring_states_the_scope(install_network):
    # The finding was a DOCUMENTATION defect: the arithmetic was always right,
    # the sentence describing it was not, and a user was told to quote it.
    # Pin the correction so it cannot be edited back out silently.
    from services.results import asset_economics

    doc = asset_economics.compute_asset_economics.__doc__ or ""
    assert "branch" in doc.lower(), "the docstring no longer mentions branch capex"
    assert "EXACTLY — both 352,864,456.77" not in doc, (
        "the withdrawn single-network figure is back in the docstring"
    )
