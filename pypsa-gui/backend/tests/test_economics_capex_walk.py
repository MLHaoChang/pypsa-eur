"""
The annuitised-CAPEX walk lives in the leaf economics module, not in a router.

`_compute_total_annuitised_capex` sat in `routers/compare.py`, so the Results
side could not reach it without importing a router. That is why a second
aggregation grew there (`get_cost_breakdown`, built on `n.statistics()`) and
why two parity suites exist to keep the two answers agreeing —
`test_compare_link_capex_parity.py` and `test_compare_store_capex_parity.py`,
each of which was written to close a measured divergence.

These tests pin the walk at its new address and pin the leaf contract that
makes the address worth having: `services/economics.py` imports only stdlib +
pandas + equally-leaf siblings, so anything can depend on it without a cycle.

NOT in scope, and deliberately so: collapsing `get_cost_breakdown`'s
`n.statistics()` aggregation into this walk. Those are two genuinely
different mechanisms and choosing between them is a behavioural decision
with real consequences at the edges — which is exactly what the parity
suites measure. This extraction removes the duplication that is safe to
remove and leaves those suites pinning the rest.
"""
from __future__ import annotations

import pandas as pd


def test_capex_walk_is_importable_from_the_leaf_module():
    """The walk answers the same question at its new address."""
    from services.economics import annuitised_capex_by_carrier

    gens = pd.DataFrame(
        {"p_nom_opt": [100.0], "carrier": ["solar"]},
        index=["s1"],
    )
    empty = pd.DataFrame()

    out = annuitised_capex_by_carrier(
        gens, empty, empty, empty,
        periods=[], is_multi=False, years_map={},
        capital_cost_of=lambda row, comp_attr: 50_000.0,
    )

    # 100 MW x 50 000 EUR/MW/a = 5 MEUR/a.
    assert out["solar"]["total"] == 5.0


def test_capex_walk_expands_across_investment_periods():
    """
    Multi-period: each period contributes `annuitised_per_yr x ipw.years[P]`
    and the horizon total is their sum. Pinned because this is the half of
    the walk that a naive move would silently flatten — a single-period test
    passes either way.
    """
    from services.economics import annuitised_capex_by_carrier

    gens = pd.DataFrame(
        {"p_nom_opt": [100.0], "carrier": ["solar"]},
        index=["s1"],
    )
    empty = pd.DataFrame()

    out = annuitised_capex_by_carrier(
        gens, empty, empty, empty,
        periods=[2030, 2040], is_multi=True, years_map={2030: 10.0, 2040: 5.0},
        capital_cost_of=lambda row, comp_attr: 50_000.0,
    )

    # 5 MEUR/a x 10 years + 5 MEUR/a x 5 years = 75 MEUR over the horizon.
    assert out["solar"]["by_period"]["2030"] == 50.0
    assert out["solar"]["by_period"]["2040"] == 25.0
    assert out["solar"]["total"] == 75.0


def test_capex_walk_counts_non_extendable_links():
    """
    A fixed link carrying a capital_cost must be counted.

    Restricting this walk to extendables made such a link show up in
    Economics (built from `n.statistics()`, which charges capital_cost x
    p_nom_opt for EVERY asset) and vanish from the Capacity tab — measured at
    a 25.154535 vs 25.320785 MEUR gap on the golden fixture. The behaviour
    travels with the walk, so it is pinned at the new address too.
    """
    from services.economics import annuitised_capex_by_carrier

    links = pd.DataFrame(
        {
            "p_nom_opt": [200.0],
            "p_nom_extendable": [False],
            "carrier": ["dc"],
        },
        index=["l1"],
    )
    empty = pd.DataFrame()

    out = annuitised_capex_by_carrier(
        empty, empty, empty, links,
        periods=[], is_multi=False, years_map={},
        capital_cost_of=lambda row, comp_attr: 10_000.0,
    )

    assert out["dc"]["total"] == 2.0, "a non-extendable link with capex counts"


def test_economics_module_stays_a_leaf():
    """
    The whole point of the move: a module Results can import without
    dragging in a router. An `import routers.…` here would recreate the
    coupling that made the second aggregation necessary.
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "services" / "economics.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    offenders = [m for m in imported if m.split(".")[0] == "routers"]
    assert not offenders, (
        f"services/economics.py must not import routers.* — found {offenders}. "
        "It is a leaf module; the whole reason the CAPEX walk moved here is "
        "so the Results side can reach it without a router import."
    )
