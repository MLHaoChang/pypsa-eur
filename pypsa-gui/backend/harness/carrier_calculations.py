"""Bounded per-carrier energy over generators and StorageUnits (checked-results S2).

Membership comes from the static tables with an exact carrier match. Every
member passes its own unit profile's validity checks; one invalid member makes
the metric unavailable for the carrier, never a partial sum. Links and Stores
of the carrier are outside scope and always disclosed.
"""

import math

from harness import storage_calculations as storage

PROFILE = "carrier-energy/1"
# Refusals named to the user; every other capture failure stays generic.
REFUSALS = frozenset({
    "ambiguous_carrier", "carrier_outside_scope", "unknown_carrier",
    "unsupported_carrier_population",
})
MAX_GENERATORS = 32
MAX_STORAGE_UNITS = 16
METRICS = {
    "carrier_generator_energy": {
        "formula_id": "carrier-generator-energy/1",
        "unit": "MWh",
        "label": "Generator energy of the carrier",
    },
    "carrier_peak_generator_output": {
        "formula_id": "carrier-generator-peak/1",
        "unit": "MW",
        "label": "Peak combined generator output of the carrier",
    },
    "carrier_storage_discharged_energy": {
        "formula_id": "carrier-storage-discharge-energy/1",
        "unit": "MWh",
        "label": "StorageUnit discharged energy of the carrier (grid side)",
    },
    "carrier_storage_charged_energy": {
        "formula_id": "carrier-storage-charge-energy/1",
        "unit": "MWh",
        "label": "StorageUnit charging energy of the carrier (grid side)",
    },
}
DISCLOSURE = (
    "Scope: generators and StorageUnits of this exact carrier; Link outputs and Stores are excluded. "
    "Generator energy uses generator snapshot weights. StorageUnit energy reads discharging (p_dispatch) "
    "and charging (p_store) separately, with the StorageUnit profile's store weights and validity checks. "
    "An empty population is exactly zero; one invalid member makes the metric unavailable."
)


def _carriers(n):
    seen = set()
    for component in ("Generator", "StorageUnit", "Link", "Store"):
        static = n.components[component].static
        if "carrier" in static:
            seen.update(str(c) for c in static["carrier"])
    return seen


def source(ctx, carrier):
    """Called under the network lock; refuses rather than guesses membership."""
    from harness import calculations
    from services import period_utils

    n = ctx.network
    if any(c != carrier and c.casefold() == carrier.casefold() for c in _carriers(n)):
        raise ValueError("ambiguous_carrier")
    members = {}
    for component in ("Generator", "StorageUnit", "Link", "Store"):
        static = n.components[component].static
        names = static.index[static["carrier"] == carrier] if "carrier" in static else []
        members[component] = [str(name) for name in names]
    if not members["Generator"] and not members["StorageUnit"]:
        raise ValueError(
            "carrier_outside_scope" if members["Link"] or members["Store"] else "unknown_carrier"
        )
    if len(members["Generator"]) > MAX_GENERATORS or len(members["StorageUnit"]) > MAX_STORAGE_UNITS:
        raise ValueError("unsupported_carrier_population")
    weights = period_utils.snapshot_weights(n, "generators")
    if not weights.index.equals(n.snapshots):
        raise ValueError("misaligned_source")
    return {
        "version": 1,
        "profile": PROFILE,
        "carrier": carrier,
        "index": [v.isoformat() for v in n.snapshots],
        "energy_weight": [calculations._number(v) for v in weights],
        "generators": {name: calculations.source(ctx, name) for name in members["Generator"]},
        "storage_units": {name: storage.source(ctx, name) for name in members["StorageUnit"]},
        "excluded": {"Link": len(members["Link"]), "Store": len(members["Store"])},
    }


def _window(raw, scope):
    index = raw["index"]
    if "start" not in scope:
        return 0, len(index) - 1
    import pandas as pd

    a = index.index(pd.Timestamp(scope["start"]).isoformat())
    b = index.index(pd.Timestamp(scope["end"]).isoformat())
    if b < a:
        raise ValueError("reversed_window")
    return a, b


def _generator_values(raw, scope, a, b):
    """Energy and combined peak over valid generators; None when any member fails."""
    from harness import calculations

    weights = raw["energy_weight"][a : b + 1]
    if any(w is None or w <= 0 for w in weights):
        return None, None
    series = []
    for name, unit in raw["generators"].items():
        if unit["index"] != raw["index"]:
            raise ValueError("misaligned_population")
        windowed = calculations._window(unit, scope)
        _, slots = calculations.calculate(windowed, name)
        if any(s["disposition"] != "answered" for s in slots if s["metric"] in ("dispatch_energy", "peak_dispatch")):
            return None, None
        series.append(windowed["inputs"]["p"])
    if not series:
        return 0.0, 0.0
    try:
        energy = math.fsum(w * p[i] for p in series for i, w in enumerate(weights))
        peak = max(math.fsum(p[i] for p in series) for i in range(len(weights)))
    except (OverflowError, ValueError, TypeError):
        return None, None
    return energy, peak


def _storage_values(raw, scope):
    """Sums of each unit's checked charge/discharge; None when any member fails."""
    totals = {"charged_energy": [], "discharged_energy": []}
    unit_scope = {k: scope[k] for k in ("start", "end") if k in scope}
    for name, unit in raw["storage_units"].items():
        if unit["index"] != raw["index"]:
            raise ValueError("misaligned_population")
        facts, _ = storage.calculate(
            unit, {**unit_scope, "profile": storage.PROFILE, "entity_id": name, "metrics": list(totals)}
        )
        values = {f["metric"]: f["value"] for f in facts}
        if set(values) != set(totals):
            return None, None
        for metric in totals:
            totals[metric].append(values[metric])
    return math.fsum(totals["discharged_energy"]), math.fsum(totals["charged_energy"])


def calculate(raw, scope):
    from harness.calculations import requested_metrics

    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["carrier"] != scope["entity_id"]:
        raise ValueError("carrier_source_identity")
    a, b = _window(raw, scope)
    energy, peak = _generator_values(raw, scope, a, b)
    discharged, charged = _storage_values(raw, scope)
    available = {
        "carrier_generator_energy": energy,
        "carrier_peak_generator_output": peak,
        "carrier_storage_discharged_energy": discharged,
        "carrier_storage_charged_energy": charged,
    }
    reasons = {
        "carrier_generator_energy": "invalid_or_missing_generator_data",
        "carrier_peak_generator_output": "invalid_or_missing_generator_data",
        "carrier_storage_discharged_energy": "invalid_or_missing_storage_trajectory",
        "carrier_storage_charged_energy": "invalid_or_missing_storage_trajectory",
    }
    facts, slots = [], []
    for metric in requested_metrics(scope):
        value = available.get(metric)
        answered = metric in METRICS and value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if answered else "unavailable",
            "requested_snapshots": b - a + 1,
        }
        if answered:
            definition = METRICS[metric]
            facts.append({
                "path": "/" + metric,
                "entity_id": scope["entity_id"],
                "metric": metric,
                "value": value,
                "unit": definition["unit"],
                "formula_id": definition["formula_id"],
            })
        else:
            slot["reason"] = reasons.get(metric, "unsupported_metric")
        slots.append(slot)
    return facts, slots


def population(raw):
    """The always-shown count line: members in scope and those excluded."""
    excluded = raw["excluded"]
    return (
        f"{len(raw['generators'])} generators and {len(raw['storage_units'])} StorageUnits of this carrier; "
        f"{excluded['Link']} Links and {excluded['Store']} Stores of this carrier excluded."
    )
