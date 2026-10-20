"""Bounded equivalent storage cycles for checked answers (checked-results S6).

Discharged energy over the window divided by the energy capacity, as
"cycles over the window" (no per-year scaling). Storage energy uses store
weights, the elapsed hours of PyPSA's state balance. A StorageUnit is judged
snapshot by snapshot with the StorageUnit profile's own bounds helper, plus
its predecessor chain when non-cyclic; a Store likewise with its e balance.
A cyclic unit's trajectory is checked against its bounds only, disclosed.
"""

import math

from harness import profile_common as common
from harness import storage_calculations as storage

PROFILE = "storage-cycles/1"
ABS_TOLERANCE = 1e-7
REL_TOLERANCE = 1e-8
REFUSALS = frozenset({
    "unknown_storage", "component_class_required", "unsupported_storage_boundary",
    "unsupported_snapshot_scope", "misaligned_source",
})
METRICS = {
    "window_discharged_energy": {
        "formula_id": "storage-window-discharge/1",
        "unit": "MWh",
        "label": "Discharged energy over the window",
    },
    "energy_capacity": {
        "formula_id": "storage-energy-capacity/1",
        "unit": "MWh",
        "label": "Energy capacity",
    },
    "equivalent_cycles": {
        "formula_id": "storage-equivalent-cycles/1",
        "unit": "cycles",
        "label": "Equivalent full cycles over the window",
    },
}
DISCLOSURE = (
    "Cycles over the window: discharged energy divided by the energy capacity (p_nom x max_hours for a "
    "StorageUnit, e_nom for a Store), with store snapshot weights; not scaled to a year."
)


def _store(ctx, entity):
    from harness.calculations import _number, _series

    n = ctx.network
    index = n.snapshots
    component = n.components["Store"]
    row = component.static.loc[entity]
    for attribute in ("p", "e"):
        table = component.dynamic.get(attribute)
        if table is None or not table.index.equals(index) or entity not in table.columns:
            raise ValueError("misaligned_source")
    return {
        "duration": [_number(v) for v in n.snapshot_weightings["stores"].reindex(index)],
        "p": [_number(v) for v in component.dynamic["p"][entity]],
        "e": [_number(v) for v in component.dynamic["e"][entity]],
        "e_min_pu": _series(component, "e_min_pu", entity, index),
        "e_max_pu": _series(component, "e_max_pu", entity, index),
        "standing_loss": _series(component, "standing_loss", entity, index),
        "capacity": _number(row["e_nom_opt"] if bool(row["e_nom_extendable"]) else row["e_nom"]),
        "initial": _number(row["e_initial"]),
        "cyclic": bool(row["e_cyclic"]),
    }


def source(ctx, entity, component_class):
    n = ctx.network
    common.single_period_index(n)
    if component_class not in ("StorageUnit", "Store"):
        raise ValueError("component_class_required")
    if entity not in n.components[component_class].static.index:
        raise ValueError("unknown_storage")
    unit = (storage.source(ctx, entity, allow_cyclic=True) if component_class == "StorageUnit"
            else _store(ctx, entity))
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "component_class": component_class,
        "index": [v.isoformat() for v in n.snapshots],
        "unit": unit,
        "valid_solve": common.lp_outputs_live(ctx),
    }


def _storage_unit(unit, a, b):
    """(discharged, capacity) or None. Non-cyclic: the StorageUnit profile itself."""
    from harness.calculations import _number

    capacity, hours = _number(unit["capacity"]), _number(unit["max_hours"])
    if not common.finite([capacity, hours]) or capacity < 0 or hours < 0:
        return None
    if not unit["cyclic"]:
        scope = {"profile": storage.PROFILE, "entity_id": unit["entity_id"], "metrics": ["discharged_energy"]}
        if a != 0 or b != len(unit["index"]) - 1:
            scope.update(start=unit["index"][a], end=unit["index"][b])
        facts, _ = storage.calculate(unit, scope)
        return (facts[0]["value"], capacity * hours) if facts else None
    if unit["valid_solve"] is not True:
        return None
    inputs = unit["inputs"]
    for i in range(a, b + 1):
        values = tuple(_number(inputs[k][i]) for k in storage.SNAPSHOT_KEYS)
        if not storage.snapshot_within_bounds(values, capacity, hours):
            return None
    return math.fsum(inputs["duration"][i] * inputs["p_dispatch"][i] for i in range(a, b + 1)), capacity * hours


def _store_energy(unit, a, b):
    """(discharged, capacity) or None; p > 0 is energy leaving the store."""
    capacity = unit["capacity"]
    if not common.finite([capacity]) or capacity < 0:
        return None
    previous = unit["initial"]
    for i in range(len(unit["p"])):
        dt, p, e, lo, hi, loss = (unit[k][i] for k in ("duration", "p", "e", "e_min_pu", "e_max_pu", "standing_loss"))
        if not common.finite([dt, p, e, lo, hi, loss]) or dt <= 0:
            return None
        if not lo * capacity - ABS_TOLERANCE <= e <= hi * capacity + ABS_TOLERANCE:
            return None
        if not unit["cyclic"]:
            if previous is None:
                return None
            expected = (1 - loss) ** dt * previous - dt * p
            if not math.isclose(e, expected, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE):
                return None
        previous = e
    return math.fsum(unit["duration"][i] * max(unit["p"][i], 0.0) for i in range(a, b + 1)), capacity


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("storage_cycles_source_identity")
    a, b = common.window(raw["index"], scope)
    base = "unverified_solve" if not raw["valid_solve"] else "invalid_or_missing_storage_trajectory"
    available, reasons = {}, {}
    if raw["valid_solve"]:
        values = (_storage_unit(raw["unit"], a, b) if raw["component_class"] == "StorageUnit"
                  else _store_energy(raw["unit"], a, b))
        if values is not None:
            discharged, capacity = values
            available = {"window_discharged_energy": discharged, "energy_capacity": capacity}
            if capacity > 0:
                available["equivalent_cycles"] = discharged / capacity
            else:
                reasons["equivalent_cycles"] = "zero_energy_capacity"
    reasons = {m: reasons.get(m, base) for m in METRICS}
    return common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def notes(raw):
    if raw["unit"]["cyclic"]:
        return ["Cyclic unit: the state-of-charge trajectory is not verified, only its bounds."]
    return []
