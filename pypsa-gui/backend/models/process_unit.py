"""
Grid-to-process custom network columns: the process-unit columns on Link and the tonne factor on a product Bus
(ticket 02); the product value on a product Bus and the role of a Generator on one (ticket 04).

Not PyPSA attributes and not Energy Hub tags, so they have their own registry beside `EH_CUSTOM_COLUMNS`. They ride
the same lazy-column machinery (`services/adequacy/eh_columns.py`): a project that never writes one never gets the
column. Kinds are those of `eh_columns`: "bool", "float_nonneg", "tier" (one of CRITICALITY_TIERS or ""),
"process_role" (one of PROCESS_GENERATOR_ROLES or "") and "text".
"""
from __future__ import annotations

# How a process unit's electrical supply is ranked when it must be cut (CONTEXT.md, Criticality tier). The empty
# string is "not set".
CRITICALITY_TIERS: tuple[str, ...] = ("safety", "process_critical", "process_flexible", "deferrable")

# What a Generator on a product bus stands for, so results never infer it from a name (gatekeeper, ticket 04):
# `buy_in` is a real purchase of the product (an expense), `shortfall` is product not delivered, priced at its
# lost-production value (lost revenue, never an expense). The empty string is "an ordinary generator".
PROCESS_GENERATOR_ROLES: tuple[str, ...] = ("buy_in", "shortfall")

PROCESS_CUSTOM_COLUMNS: dict[str, dict[str, str]] = {
    "Bus": {"mwh_per_t": "float_nonneg", "product_value": "float_nonneg"},
    "Generator": {"process_role": "process_role"},
    "Link": {"eh_process": "bool", "criticality_tier": "tier", "throughput_unit": "text",
             "specific_energy_kwh_per_t": "float_nonneg"},
}


def product_role_generators(n, role: str):
    """The Generators with this `process_role` (one of PROCESS_GENERATOR_ROLES), as a pandas Index.

    Keyed on the column only, never on a name: empty when the column is missing or no Generator has the role. The
    product is the Generator's bus. The one predicate tickets 03 (solve path) and 04 (results) share.
    """
    gens = n.generators
    if role not in PROCESS_GENERATOR_ROLES or "process_role" not in gens.columns:
        return gens.index[:0]
    return gens.index[gens["process_role"].astype(str).str.strip() == role]
