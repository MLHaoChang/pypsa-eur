"""Bounded, raw-source generator calculations; never a total-system-cost alias."""

from __future__ import annotations

import math
from numbers import Real

import pandas as pd

from services import workflow_tools

PROFILE = "generator-dispatch/1"
MAX_SNAPSHOTS = 512
METRICS = {
    "dispatch_energy": {
        "formula_id": "generator-energy/1",
        "unit": "MWh",
        "label": "Generator energy",
        "inputs": ("p", "energy_weight"),
    },
    "marginal_expenditure": {
        "formula_id": "generator-marginal-expenditure/1",
        "unit": "EUR",
        "label": "Marginal dispatch expenditure",
        "inputs": ("p", "marginal_cost", "objective_weight"),
    },
    "peak_dispatch": {
        "formula_id": "generator-peak/1",
        "unit": "MW",
        "label": "Peak generator output",
        "inputs": ("p",),
    },
}
DISCLOSURE = (
    "Energy uses generator snapshot weights; expenditure uses objective snapshot weights. "
    "Expenditure excludes fixed, startup, shutdown and quadratic costs; it is not total system cost. "
    "Currency follows the model's EUR convention; no price year is inferred."
)


def _number(value):
    try:
        if isinstance(value, bool) or not isinstance(value, Real):
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _series(component, attribute, entity, index):
    dynamic = component.dynamic.get(attribute)
    if dynamic is not None and entity in dynamic.columns:
        if not dynamic.index.equals(index) or not dynamic.index.is_unique:
            raise ValueError("misaligned_source")
        return [_number(v) for v in dynamic[entity]]
    return [_number(component.static.at[entity, attribute])] * len(index)


def source(ctx, entity):
    """Called under the network lock. Preserve missing values rather than fill."""
    n = ctx.network
    index = n.snapshots
    if (
        isinstance(index, pd.MultiIndex)
        or not isinstance(index, pd.DatetimeIndex)
        or not index.is_unique
        or not index.is_monotonic_increasing
        or not 1 <= len(index) <= MAX_SNAPSHOTS
        or len(n.investment_periods)
    ):
        raise ValueError("unsupported_snapshot_scope")
    generators = n.components["Generator"]
    if entity not in generators.static.index:
        raise ValueError("unknown_generator")
    weights = n.snapshot_weightings
    dispatch = generators.dynamic.get("p")
    if (
        not weights.index.equals(index)
        or not weights.index.is_unique
        or dispatch is None
        or not dispatch.index.equals(index)
        or not dispatch.index.is_unique
        or not dispatch.columns.is_unique
        or set(dispatch.columns) != set(generators.static.index)
    ):
        raise ValueError("misaligned_source")
    if entity not in dispatch.columns:
        raise ValueError("missing_dispatch")
    configured = generators.static.loc[entity]
    if _number(configured["sign"]) != 1:
        raise ValueError("unsupported_generator_sign")
    capacity = _number(
        configured["p_nom_opt"]
        if configured["p_nom_extendable"]
        else configured["p_nom"]
    )
    inputs = {
        "p": [_number(v) for v in dispatch[entity]],
        "energy_weight": [_number(v) for v in weights["generators"]],
        "objective_weight": [_number(v) for v in weights["objective"]],
        "marginal_cost": _series(generators, "marginal_cost", entity, index),
        "p_min": _series(generators, "p_min_pu", entity, index),
        "p_max": _series(generators, "p_max_pu", entity, index),
    }
    fingerprint = workflow_tools._network_input_fingerprint(ctx)
    state = ctx.solver_state
    valid_solve = (
        state.get("status") == "completed"
        and state.get("condition") == "optimal"
        and n.meta.get("assistant_solve_input_fingerprint") == fingerprint
    )
    return {
        "index": [v.isoformat() for v in index],
        "inputs": inputs,
        "capacity": capacity,
        "input_fingerprint": fingerprint,
        "solved_fingerprint": n.meta.get("assistant_solve_input_fingerprint"),
        "status": state.get("status"),
        "condition": state.get("condition"),
        "valid_solve": valid_solve,
        "profile": PROFILE,
    }


def calculate(raw, entity):
    """Formula-owned masks, complete populations, and independently testable sums."""
    inputs = raw["inputs"]
    count = len(raw["index"])
    capacity = raw["capacity"]
    facts, slots = [], []
    for metric, definition in METRICS.items():
        valid = []
        for i in range(count):
            p, lo, hi = (inputs[key][i] for key in ("p", "p_min", "p_max"))
            ok = (
                raw["valid_solve"]
                and capacity is not None
                and capacity >= 0
                and p is not None
                and lo is not None
                and hi is not None
                and 0 <= lo <= hi
                and capacity * lo - 1e-8 <= p <= capacity * hi + 1e-8
            )
            ok = ok and all(inputs[key][i] is not None for key in definition["inputs"])
            for weight in ("energy_weight", "objective_weight"):
                if weight in definition["inputs"]:
                    ok = ok and inputs[weight][i] is not None and inputs[weight][i] > 0
            if ok:
                valid.append(i)

        def aggregate(indices):
            if metric == "peak_dispatch":
                return max(inputs["p"][i] for i in indices)
            return math.fsum(
                math.prod(inputs[key][i] for key in definition["inputs"])
                for i in indices
            )

        complete = len(valid) == count
        try:
            value = aggregate(valid) if valid else None
        except (OverflowError, ValueError):
            value, complete = None, False
        if value is not None and not math.isfinite(value):
            value, complete = None, False
        slot = {
            "metric": metric,
            "disposition": "answered" if complete else "unavailable",
            "valid_snapshots": len(valid),
            "requested_snapshots": count,
            "excluded_indices": [
                raw["index"][i] for i in range(count) if i not in valid
            ],
        }
        if complete:
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": entity,
                    "metric": metric,
                    "value": value,
                    "unit": definition["unit"],
                    "formula_id": definition["formula_id"],
                }
            )
        else:
            slot["reason"] = (
                "unverified_solve"
                if not raw["valid_solve"]
                else "invalid_or_missing_raw_data"
            )
            # A subset is explicitly labelled, never available as a full-horizon fact.
            slot["partial_value"] = value
        slots.append(slot)
    return facts, slots
