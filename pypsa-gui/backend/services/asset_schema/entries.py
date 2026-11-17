"""
From `FieldSpec` rows to Parameter catalogue entries (spec expert-catalogue §3.2, §3.4, §3.6).

`fields.py` keeps the per-class rows. The catalogue facts most rows share are
rules and overlay tables here, so one decision lives in one place:

* the nine plain groups and where each legacy group / column lands;
* plain names (RAW-NAMES-1: the rename table in the slice-01 PR body);
* depth, functions, aliases and one-line help.

Where the `FieldSpec` row carries the attribute (id, plain_name, short_help, depth,
aliases, options_plain), a value set on the row wins over a rule here. Scope,
default and functions are per class, and rows are shared between classes, so
those live here and in `catalogue.py` only.
Entry ids derive from scope + plain name and are pinned by
`catalogue.ids.txt`; a rename that would move an id must set `id=` on the row
to the old id (the pin test says so).
"""

from __future__ import annotations

import re
from typing import Any

from services.asset_schema.fields import FieldSpec
from services.asset_schema.functions import Function

#: The nine plain groups, in the fixed order every asset type shows them (§3.4).
GROUPS: tuple[tuple[str, str], ...] = (
    ("general", "General"),
    ("size_limits", "Size and limits"),
    ("electrical", "Electrical"),
    ("operation", "Operation"),
    ("running_cost", "Running cost"),
    ("investment", "Investment"),
    ("reliability", "Reliability"),
    ("replacement", "Replacement and life"),
    ("source", "Source"),
)
GROUP_IDS = tuple(g for g, _ in GROUPS)
GROUP_NAMES = dict(GROUPS)

GROUP_HELP = {
    "general": "What the asset is and where it connects.",
    "size_limits": "How big the asset is and the output range it may run in.",
    "electrical": "Voltages, impedances and controls the load-flow and fault studies read.",
    "operation": "How the asset behaves while running: efficiency, losses, start-ups.",
    "running_cost": "Costs that grow with every hour the asset runs.",
    "investment": "Building cost, fixed upkeep, life and whether the optimiser may size it.",
    "reliability": "How often the asset fails and how long a repair takes.",
    "replacement": "When the asset was built and when it is renewed.",
    "source": "Where a value came from. Read-only.",
}

#: Legacy group (still on `asset_schema/{component}` until slice 03) → plain group.
LEGACY_GROUP = {
    "identity": "general",
    "size": "size_limits",
    "investment": "investment",
    "fom": "investment",
    "variable": "running_cost",
    "performance": "operation",
    "replacement": "replacement",
    "provenance": "source",
}

_ELECTRICAL = frozenset(
    {
        "b",
        "g",
        "r",
        "phase_shift",
        "tap_ratio",
        "v_nom",
        "v_nom_0",
        "v_nom_1",
        "control",
        "q_set",
        "flicker_changes_per_min",
        "flicker_step_mva",
        "harmonic_source",
        "harmonic_spectrum",
        "i_start_pu",
        "rated_mva",
        "ride_through_curve",
        "single_phase_share",
        "spectrum_post_filter",
        "start_method",
        "motor_kw",
    }
)
_RELIABILITY = frozenset(
    {
        "mttr_hours",
        "outage_rate_basis",
        "outage_rate_value",
        "p_max_pu_includes_outages",
        "criticality_tier",
        "restart_time_h",
    }
)
_INVESTMENT_SIZING = re.compile(r"^(p|e|s)_nom_(extendable|min|max)$")
_REPLACEMENT = frozenset({"build_year", "lifetime_run_hours"})


def is_bus_coordinate(scope: str, spec: FieldSpec) -> bool:
    """A Bus's `x`/`y` are its longitude and latitude; on a branch `x` is a reactance."""
    return scope == "Bus" and spec.key in ("x", "y")


def group_of(scope: str, spec: FieldSpec) -> str:
    key = spec.key
    if key == "x" and not is_bus_coordinate(scope, spec) or key in _ELECTRICAL:
        return "electrical"
    if key in _RELIABILITY:
        return "reliability"
    if _INVESTMENT_SIZING.match(key):
        return "investment"
    if key in _REPLACEMENT:
        return "replacement"
    return LEGACY_GROUP[spec.group]


#: Plain names that differ from today's `FieldSpec.label` (RAW-NAMES-1, spec §3.6 table).
#: Keyed by model name, or (scope, model name) where one class differs. The
#: frontend keeps reading `label` until slice 03 switches it to `plain_name`.
RENAMES: dict[Any, str] = {
    "p_nom_extendable": "Let the optimiser size it",
    "e_nom_extendable": "Let the optimiser size it",
    "s_nom_extendable": "Let the optimiser size it",
    "p_nom_min": "Smallest size",
    "p_nom_max": "Largest size",
    "e_nom_min": "Smallest size",
    "e_nom_max": "Largest size",
    "s_nom_min": "Smallest size",
    "s_nom_max": "Largest size",
    "marginal_cost": "Running cost",
    "capital_cost": "Yearly capital cost",
    "bus": "Connected to",
    ("Line", "bus0"): "From",
    ("Line", "bus1"): "To",
    ("Transformer", "bus0"): "High-voltage side",
    ("Transformer", "bus1"): "Low-voltage side",
    ("Line", "s_nom"): "Rating",
    ("Transformer", "s_nom"): "Rating",
    "committable": "Must start and stop (unit commitment)",
}


def plain_name_of(scope: str, spec: FieldSpec) -> str:
    if spec.plain_name:
        return spec.plain_name
    return RENAMES.get((scope, spec.key)) or RENAMES.get(spec.key) or spec.label


#: Search synonyms. Never shown as labels.
ALIASES: dict[str, tuple[str, ...]] = {
    "p_nom": ("rating", "Pmax", "nameplate", "installed capacity", "size"),
    "s_nom": ("thermal rating", "MVA rating", "capacity"),
    "e_nom": ("energy capacity", "storage size"),
    "marginal_cost": (
        "VOM",
        "variable cost",
        "fuel cost",
        "OPEX",
        "marginal cost",
        "SRMC",
    ),
    "capital_cost": ("CAPEX", "annuity", "annualised cost"),
    "overnight_cost": ("CAPEX", "investment cost", "build cost"),
    "fom_cost": ("fixed O&M", "FOM", "OPEX"),
    "efficiency": ("eta", "conversion efficiency"),
    "p_max_pu": ("availability", "capacity factor", "profile"),
    "p_nom_extendable": ("extendable", "optimise size", "sizing"),
    "e_nom_extendable": ("extendable", "optimise size", "sizing"),
    "s_nom_extendable": ("extendable", "optimise size", "sizing"),
    "committable": ("unit commitment", "UC"),
    "x": ("reactance", "impedance"),
    "r": ("resistance",),
    "max_hours": ("duration", "hours of storage"),
    "standing_loss": ("self-discharge",),
    "lifetime": ("economic life", "service life"),
    "discount_rate": ("WACC", "interest rate"),
    "ramp_limit_up": ("ramp rate",),
    "mttr_hours": ("MTTR", "repair time"),
    "outage_rate_value": ("failure rate", "FOR", "forced outage rate"),
    "co2_emissions": ("emission factor", "CO2"),
}

#: Plain names for enum options, parallel to `FieldSpec.options`. Display only: the stored
#: value stays the raw option (GK B T3).
OPTIONS_PLAIN: dict[str, dict[str, str]] = {
    "control": {
        "PQ": "Fixed power (PQ)",
        "PV": "Fixed voltage (PV)",
        "Slack": "Reference bus (slack)",
    },
    "process_role": {"buy_in": "Bought in", "shortfall": "Shortfall"},
    "outage_rate_basis": {
        "FOR": "Forced outage rate (FOR)",
        "EFORd": "Demand-weighted forced outage rate (EFORd)",
    },
    "type": {
        "primary_energy": "Primary energy cap (CO₂)",
        "tech_capacity_expansion_limit": "New capacity limit",
        "operational_limit": "Dispatch limit",
        "transmission_volume_expansion_limit": "New transmission volume limit",
        "transmission_expansion_cost_limit": "New transmission cost limit",
    },
    "sense": {"<=": "At most", "==": "Exactly", ">=": "At least"},
    "criticality_tier": {
        "safety": "Safety",
        "process_critical": "Process critical",
        "process_flexible": "Process flexible",
        "deferrable": "Deferrable",
    },
    "harmonic_source": {
        "linear": "Linear",
        "6-pulse": "6-pulse rectifier",
        "12-pulse": "12-pulse rectifier",
        "18-pulse": "18-pulse rectifier",
        "active-front-end": "Active front end",
        "phase-angle": "Phase-angle control",
        "burst": "Burst-fire control",
        "thyristor-stack": "Thyristor stack",
    },
    "start_method": {
        "dol": "Direct on line",
        "soft": "Soft starter",
        "vfd": "Variable-speed drive",
    },
}


def options_plain_of(spec: FieldSpec) -> tuple[str, ...]:
    """One plain name per option; an option with none reads as itself (never blank)."""
    if spec.options_plain:
        return spec.options_plain
    names = OPTIONS_PLAIN.get(spec.key, {})
    return tuple(names.get(o, o) for o in spec.options)


#: Numeric entries with no unit: ratios, counts, a sign and a period label.
UNITLESS = frozenset(
    {
        ("Generator", "dc_ac_ratio"),
        ("GlobalConstraint", "constant"),
        ("GlobalConstraint", "investment_period"),
        ("Link", "efficiency2"),
        ("Link", "efficiency3"),
        ("Link", "efficiency4"),
        ("Load", "sign"),
        ("Transformer", "tap_ratio"),
    }
)


#: Depth by plain group when the row does not say (essentials is always set on the row).
_DEPTH_BY_GROUP = {
    "general": "basic",
    "size_limits": "basic",
    "running_cost": "basic",
    "investment": "basic",
    "operation": "advanced",
    "electrical": "advanced",
    "reliability": "advanced",
    "replacement": "advanced",
    "source": "detail",
}
DEPTHS = ("essentials", "basic", "advanced", "detail")


def depth_of(scope: str, spec: FieldSpec, group: str) -> str:
    if spec.depth:
        return spec.depth
    if spec.derived:
        return "detail"
    return _DEPTH_BY_GROUP[group]


_F = Function
#: What each plain group feeds, by default ("needs" only where a missing value blocks the run).
_FUNCTIONS_BY_GROUP: dict[str, dict[str, str]] = {
    "general": {_F.DISPATCH: "uses"},
    "size_limits": {
        _F.DISPATCH: "uses",
        _F.EXPANSION: "uses",
        _F.LOAD_FLOW: "uses",
        _F.ADEQUACY: "uses",
        _F.FINANCE: "uses",
    },
    "electrical": {
        _F.LOAD_FLOW: "uses",
        _F.SHORT_CIRCUIT: "uses",
        _F.GRID_COMPLIANCE: "uses",
    },
    "operation": {_F.DISPATCH: "uses", _F.ADEQUACY: "uses"},
    "running_cost": {_F.DISPATCH: "uses", _F.EXPANSION: "uses", _F.FINANCE: "uses"},
    "investment": {_F.EXPANSION: "uses", _F.FINANCE: "uses"},
    "reliability": {_F.ADEQUACY: "needs"},
    "replacement": {_F.FINANCE: "uses"},
    "source": {},
}
_PORTS = frozenset({"bus", "bus0", "bus1"})
_NEEDS: dict[str, dict[str, str]] = {
    **{
        k: {
            _F.DISPATCH: "needs",
            _F.LOAD_FLOW: "needs",
            _F.SHORT_CIRCUIT: "needs",
            _F.ADEQUACY: "needs",
        }
        for k in _PORTS
    },
    **{
        k: {
            _F.DISPATCH: "needs",
            _F.LOAD_FLOW: "uses",
            _F.ADEQUACY: "uses",
            _F.FINANCE: "uses",
        }
        for k in ("p_nom", "e_nom", "s_nom")
    },
    **{
        k: {_F.EXPANSION: "needs", _F.FINANCE: "uses"}
        for k in ("p_nom_extendable", "e_nom_extendable", "s_nom_extendable")
    },
    "x": {_F.LOAD_FLOW: "needs", _F.SHORT_CIRCUIT: "needs", _F.GRID_COMPLIANCE: "uses"},
    "r": {_F.LOAD_FLOW: "needs", _F.SHORT_CIRCUIT: "uses"},
    "v_nom": {
        _F.LOAD_FLOW: "needs",
        _F.SHORT_CIRCUIT: "needs",
        _F.GRID_COMPLIANCE: "uses",
    },
    "harmonic_spectrum": {_F.GRID_COMPLIANCE: "needs"},
    "flicker_step_mva": {_F.GRID_COMPLIANCE: "needs"},
}


def functions_of(scope: str, spec: FieldSpec, group: str) -> dict[str, str]:
    if is_bus_coordinate(scope, spec):
        rows = _FUNCTIONS_BY_GROUP["general"]
    else:
        rows = _NEEDS.get(spec.key) or _FUNCTIONS_BY_GROUP[group]
    order = {f: i for i, f in enumerate(Function)}
    return {
        Function(f).value: v
        for f, v in sorted(rows.items(), key=lambda kv: order[Function(kv[0])])
    }


#: One-line help where the row's first sentence is too long or names a model column.
SHORT_HELP: dict[Any, str] = {
    (
        "Generator",
        "stand_by_cost",
    ): "Derived: cost per hour while the unit is on at any output (no-load fuel and hourly O&M).",
    (
        "GlobalConstraint",
        "type",
    ): "What the constraint caps: primary energy (a CO₂ cap), new capacity, dispatch, or new transmission.",
    (
        "Link",
        "bus2",
    ): "Optional extra port 2: receives energy (CHP heat) or draws it (a process feed), by the sign of its efficiency.",
    (
        "Link",
        "bus3",
    ): "Optional extra port 3: receives energy or draws it, by the sign of its efficiency.",
    (
        "Link",
        "bus4",
    ): "Optional extra port 4: receives energy or draws it, by the sign of its efficiency.",
    (
        "Link",
        "eh_process",
    ): "Tick when this link is a production step: the input side is the main input and the output side the product.",
    (
        "Link",
        "criticality_tier",
    ): "How this unit's supply is ranked when it must be cut, from safety-critical to deferrable.",
    ("Link", "p_nom"): "Rating on the input side.",
    (
        "Link",
        "efficiency",
    ): "Output per unit of input (the COP for a heat pump, above 100 %).",
    ("Link", "efficiency2"): "Flow on port 2 per unit drawn on the input side.",
    ("Link", "efficiency3"): "Flow on port 3 per unit drawn on the input side.",
    ("Link", "efficiency4"): "Flow on port 4 per unit drawn on the input side.",
    (
        "Link",
        "start_method",
    ): "How the motor is started: direct on line (about 6 × rated current), soft starter (3 ×) or drive (1 ×).",
    (
        "Link",
        "ride_through_curve",
    ): "The voltage dips the unit rides through, as dip depth and duration pairs.",
    (
        "StorageUnit",
        "capital_cost",
    ): "Derived: each part's overnight cost annualised over its own life, plus fixed O&M.",
}


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


def short_help_of(scope: str, spec: FieldSpec) -> str:
    if spec.short_help:
        return spec.short_help
    return (
        SHORT_HELP.get((scope, spec.key))
        or SHORT_HELP.get(spec.key)
        or _first_sentence(spec.help)
    )


_SCOPE_SLUG = re.compile(r"(?<!^)(?=[A-Z])")


def scope_slug(scope: str) -> str:
    return _SCOPE_SLUG.sub("_", scope).lower()


def slug(text: str) -> str:
    text = text.replace("₂", "2").replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def entry_id_of(scope: str, spec: FieldSpec) -> str:
    return spec.id or f"{scope_slug(scope)}.{slug(plain_name_of(scope, spec))}"
