"""
Explanation: explain_investment: schemas and routes.

Moved out of `harness/catalogue.py` (RETRO-FRAG-1); spliced back at its old
place, so it declares no ORDER.
Dispatchers: `services/chat_dispatchers/explanation.py`.

A schema fragment imports only `harness.catalogue_base`, so the enums below
are copies of catalogue's; `tests/test_tool_fragments.py` pins each.
"""

from __future__ import annotations

from harness.catalogue_base import _DERIVED, _t

# Classes whose nominal capacity the optimiser can size — mirrors
# services/asset_results/compute._NOM_COL, the map explain_investment reads
# through `nom_col_for`. Bus and Load are absent: neither is an investment.
INVESTMENT_CLASS_ENUM = [
    "Generator",
    "StorageUnit",
    "Store",
    "Link",
    "Line",
    "Transformer",
]

TOOLS = [
    _t(
        "explain_investment",
        "Why is this asset the size it is? Fuses, in ONE call, the evidence an "
        "explanation needs: `sizing` (existing vs optimised capacity, the "
        "bounds, and `binding_constraint` — one of not_solved / "
        "not_extendable / at_upper_bound / at_lower_bound / not_built / "
        "interior — with the sentence that says what that means), "
        "`asset_kpis` (the registry's "
        "cross-tab headline: capacity factor, capture price, revenue, net "
        "profit, LCOE, CO2, read from the same source as the Asset Detail "
        "tab), `system_signals` (marginal price at the asset's buses, active "
        "CO2 caps with their shadow prices, and `congestion` — binding lines "
        "at those buses PLUS a `note` saying what an empty list means: "
        "nothing binds, no duals captured, or out of scope) and "
        "`reading_notes`. START HERE for any 'why did the model build / not "
        "build X' question: `binding_constraint` is usually the whole answer, "
        "and an asset at a bound was NOT sized by its economics. Evidence "
        "only — no verdict; narrate solely from fields present in the payload, "
        "and obey reading_notes (an interior extendable asset earns ~zero net "
        "profit by construction). Safety: read.",
        {
            "component_class": {"type": "string", "enum": INVESTMENT_CLASS_ENUM},
            "name": {"type": "string"},
        },
        ["component_class", "name"],
    ),
]

ROUTES = {
    "explain_investment": _DERIVED,
}
