"""
Grid-to-process custom network columns: the process-unit columns on Link and the tonne factor on a product Bus
(ticket 02); the product value on a product Bus and the role of a Generator on one (ticket 04); an inventory Store's delivery
interval (ticket 09: hours between shipments out of it, absent or 0 = continuous, read by the time-base validator rule).

Not PyPSA attributes and not Energy Hub tags, so they have their own registry beside `EH_CUSTOM_COLUMNS`. They ride
the same lazy-column machinery (`services/adequacy/eh_columns.py`): a project that never writes one never gets the
column. Kinds are those of `eh_columns`: "bool", "float_nonneg", "tier" (one of CRITICALITY_TIERS or ""),
"process_role" (one of PROCESS_GENERATOR_ROLES or ""), "text", "harmonic_source" (one of HARMONIC_SOURCES or "")
"harmonic_spectrum" (order:% pairs, checked by gridspine's harmonic screen), "fraction" (0..1) and "start_method"
(one of START_METHODS or "").
"""
from __future__ import annotations

# How a process unit's electrical supply is ranked when it must be cut (CONTEXT.md, Criticality tier). The empty
# string is "not set".
CRITICALITY_TIERS: tuple[str, ...] = ("safety", "process_critical", "process_flexible", "deferrable")

# What a Generator on a product bus stands for, so results never infer it from a name (gatekeeper, ticket 04):
# `buy_in` is a real purchase of the product (an expense), `shortfall` is product not delivered, priced at its
# lost-production value (lost revenue, never an O&M or purchase expense: the case books it as its own
# `lost_production` stream). The empty string is "an ordinary generator".
PROCESS_GENERATOR_ROLES: tuple[str, ...] = ("buy_in", "shortfall")

# A unit's drive topology or heater control for the harmonic screen (grid-to-process issue 17); `linear` declares a
# unit that emits none. The empty string is "not set" (no input). Equal to `gridspine.static.harmonics.TOPOLOGIES`.
HARMONIC_SOURCES: tuple[str, ...] = ("linear", "6-pulse", "12-pulse", "18-pulse", "active-front-end", "phase-angle",
                                     "burst", "thyristor-stack")

# How a unit's motor is started, for the motor-start screen (grid-to-process issue 18): direct on line, soft
# starter or variable-speed drive. The empty string is "not set" (no input). Equal to
# `gridspine.static.motor_start.START_METHODS`.
START_METHODS: tuple[str, ...] = ("dol", "soft", "vfd")

PROCESS_CUSTOM_COLUMNS: dict[str, dict[str, str]] = {
    "Bus": {"mwh_per_t": "float_nonneg", "product_value": "float_nonneg",
            "lost_production_value": "float_nonneg"},
    "Generator": {"process_role": "process_role"},
    "Store": {"delivery_interval_h": "float_nonneg"},
    "Link": {"eh_process": "bool", "criticality_tier": "tier", "throughput_unit": "text",
             "specific_energy_kwh_per_t": "float_nonneg", "rated_mva": "float_nonneg",
             "harmonic_source": "harmonic_source", "harmonic_spectrum": "harmonic_spectrum",
             "spectrum_post_filter": "bool", "flicker_step_mva": "float_nonneg",
             "flicker_changes_per_min": "float_nonneg",
             "single_phase_share": "fraction", "motor_kw": "float_nonneg", "start_method": "start_method",
             "i_start_pu": "float_nonneg"},
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


def product_roles(n) -> dict[str, str]:
    """{Generator: role} of every Generator with a `process_role`, from `product_role_generators` (column only).
    A product slack is an LP term, not an asset: the value flows never make it ownable (ticket 06)."""
    return {str(g): role for role in PROCESS_GENERATOR_ROLES for g in product_role_generators(n, role)}


def bus_values(n) -> dict[str, float]:
    """{bus: lost_production_value} for every bus with a finite value above 0 (ticket 08): the column alone."""
    import math

    import pandas as pd

    if "lost_production_value" not in n.buses.columns:
        return {}
    v = pd.to_numeric(n.buses["lost_production_value"], errors="coerce")
    return {str(b): float(x) for b, x in v.items() if math.isfinite(x) and x > 0}
