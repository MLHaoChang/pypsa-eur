"""Bounded optimised capacities for checked answers (checked-results S6).

The observed `*_nom_opt` of one component of a named class, its change from
`*_nom`, and whether it was built to its upper bound (a disclosed line, not a
number). Per-unit availability limits (s_max_pu, p_max_pu) are not applied.
"""

import math

from harness import profile_common as common

PROFILE = "capacity/1"
BOUND_TOLERANCE = 1e-6
REFUSALS = frozenset({"unknown_component", "component_class_required", "unsupported_snapshot_scope"})
# Class -> (static table attribute, nominal column, unit, what the number measures).
CLASSES = {
    "Generator": ("generators", "p_nom", "MW", "active power"),
    "Link": ("links", "p_nom", "MW", "power on the input side (p0)"),
    "StorageUnit": ("storage_units", "p_nom", "MW", "power (its energy is p_nom x max_hours)"),
    "Store": ("stores", "e_nom", "MWh", "energy"),
    "Line": ("lines", "s_nom", "MVA", "apparent power"),
    "Transformer": ("transformers", "s_nom", "MVA", "apparent power"),
}
METRICS = {
    "optimal_capacity": {
        "formula_id": "observed-nominal-capacity/1",
        "unit": "MW, MVA or MWh by class",
        "label": "Capacity after the solve",
    },
    "capacity_change": {
        "formula_id": "nominal-capacity-change/1",
        "unit": "MW, MVA or MWh by class",
        "label": "Change from the input capacity",
    },
}
DISCLOSURE = (
    "The solve's nominal capacity (p_nom, e_nom or s_nom, optimised when extendable). Availability "
    "limits (s_max_pu, p_max_pu) are not applied to the figure."
)


def source(ctx, entity, component_class):
    from harness.calculations import _number

    n = ctx.network
    index = common.single_period_index(n)
    if component_class not in CLASSES:
        raise ValueError("component_class_required")
    table, nominal, _unit, _side = CLASSES[component_class]
    static = getattr(n, table)
    if entity not in static.index:
        raise ValueError("unknown_component")
    row = static.loc[entity]
    extendable = bool(row.get(nominal + "_extendable", False))
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "component_class": component_class,
        "index": [v.isoformat() for v in index],
        "extendable": extendable,
        "nominal": _number(row[nominal]),
        "optimal": _number(row[nominal + "_opt"]) if extendable else _number(row[nominal]),
        "maximum": _number(row.get(nominal + "_max", math.inf)),
        "valid_solve": common.lp_outputs_live(ctx),
    }


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("capacity_source_identity")
    unit = CLASSES[raw["component_class"]][2]
    definitions = {m: {**d, "unit": unit} for m, d in METRICS.items()}
    available = {}
    base = "unverified_solve" if not raw["valid_solve"] else "invalid_or_missing_capacity"
    if raw["valid_solve"] and common.finite([raw["nominal"], raw["optimal"]]) and raw["optimal"] >= 0:
        available = {"optimal_capacity": raw["optimal"], "capacity_change": raw["optimal"] - raw["nominal"]}
    return common.facts_and_slots(scope, definitions, available, {m: base for m in METRICS}, 1)


def at_upper_bound(raw):
    """True when built to *_nom_max: relative 1e-6, absolute when the bound is 0, never when inf."""
    x, top = raw["optimal"], raw["maximum"]
    if not raw["extendable"] or x is None or top is None or not math.isfinite(top):
        return False
    return abs(x - top) <= (BOUND_TOLERANCE * top if top > 0 else BOUND_TOLERANCE)


def notes(raw):
    table, nominal, unit, side = CLASSES[raw["component_class"]]
    lines = [f"{raw['component_class']} {nominal} in {unit}: {side}."]
    if not raw["extendable"]:
        lines.append("Fixed capacity (not optimised).")
    elif raw["valid_solve"] and at_upper_bound(raw):
        lines.append(f"Built to its upper bound ({nominal}_max).")
    return lines
