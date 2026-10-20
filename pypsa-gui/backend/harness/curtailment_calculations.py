"""Bounded curtailment of variable generators for checked answers (checked-results S5).

Only generators with a time-varying p_max_pu are variable here (a dispatchable
unit running below its rating is not curtailed). Available energy is
Σ w·p_max_pu(t)·capacity with generator snapshot weights; curtailed energy is
Σ w·max(0, available − p). A residual below −1e-6·capacity, a committable
unit or any missing value makes the generator unavailable, and the network
total is unavailable when any variable generator is.
"""

import math

from harness import profile_common as common

PROFILE = "curtailment/1"
MAX_GENERATORS = 64
RESIDUAL_TOLERANCE = 1e-6
REFUSALS = frozenset({
    "unknown_generator", "not_variable", "no_variable_generators", "unsupported_generator_population",
    "unsupported_snapshot_scope", "misaligned_source",
})
METRICS = {
    "available_energy": {
        "formula_id": "variable-available-energy/1",
        "unit": "MWh",
        "label": "Available variable energy",
    },
    "curtailed_energy": {
        "formula_id": "variable-curtailed-energy/1",
        "unit": "MWh",
        "label": "Curtailed energy",
    },
    "curtailment_share": {
        "formula_id": "curtailment-share-of-available/1",
        "unit": "%",
        "label": "Curtailed share of available energy",
    },
}
DISCLOSURE = (
    "Generators with a time-varying availability (p_max_pu) only; generator snapshot weights; capacity "
    "is p_nom_opt if extendable, else p_nom. The Curtailment tab also lists renewables with a constant "
    "availability, which this check does not."
)


def _unit(n, name, index):
    from harness.calculations import _number

    row = n.generators.loc[name]
    p = n.generators_t.p
    return {
        "capacity": _number(row["p_nom_opt"] if bool(row["p_nom_extendable"]) else row["p_nom"]),
        "committable": bool(row.get("committable", False)),
        "p_max_pu": [_number(v) for v in n.generators_t.p_max_pu[name]],
        "p": [_number(v) for v in p[name]] if name in p.columns else [None] * len(index),
    }


def source(ctx, entity):
    from harness.calculations import _number
    from services import period_utils

    n = ctx.network
    index = common.single_period_index(n)
    varying = n.generators_t.p_max_pu
    if not varying.empty and not varying.index.equals(index):
        raise ValueError("misaligned_source")
    variable = [str(g) for g in varying.columns if g in n.generators.index]
    if entity == "network":
        names = variable
        if not names:
            raise ValueError("no_variable_generators")
        if len(names) > MAX_GENERATORS:
            raise ValueError("unsupported_generator_population")
    elif entity not in n.generators.index:
        raise ValueError("unknown_generator")
    elif entity not in variable:
        raise ValueError("not_variable")
    else:
        names = [entity]
    if not n.generators_t.p.index.equals(index):
        raise ValueError("misaligned_source")
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "weight": [_number(v) for v in period_utils.snapshot_weights(n, "generators").reindex(index)],
        "generators": {name: _unit(n, name, index) for name in names},
        "valid_solve": common.lp_outputs_live(ctx),
    }


def _energy(unit, weight, a, b):
    capacity = unit["capacity"]
    if unit["committable"] or capacity is None or not math.isfinite(capacity) or capacity < 0:
        return None
    available, curtailed = [], []
    for i in range(a, b + 1):
        pu, p, w = unit["p_max_pu"][i], unit["p"][i], weight[i]
        if not common.finite([pu, p, w]) or w < 0:
            return None
        avail = pu * capacity
        residual = avail - p
        if residual < -RESIDUAL_TOLERANCE * capacity:
            return None
        available.append(w * avail)
        curtailed.append(w * max(residual, 0.0))
    return math.fsum(available), math.fsum(curtailed)


def invalid(raw, scope):
    """Generators whose own values are unavailable in the window, for the answer to name."""
    a, b = common.window(raw["index"], scope)
    return [g for g, u in raw["generators"].items() if _energy(u, raw["weight"], a, b) is None]


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("curtailment_source_identity")
    a, b = common.window(raw["index"], scope)
    base = "unverified_solve" if not raw["valid_solve"] else "invalid_variable_generator"
    available, reasons = {}, {}
    energies = [_energy(u, raw["weight"], a, b) for u in raw["generators"].values()]
    if raw["valid_solve"] and all(e is not None for e in energies):
        total = math.fsum(e[0] for e in energies)
        curtailed = math.fsum(e[1] for e in energies)
        available = {"available_energy": total, "curtailed_energy": curtailed}
        if total > 0:
            available["curtailment_share"] = 100 * curtailed / total
        else:
            reasons["curtailment_share"] = "no_available_energy"
    reasons = {m: reasons.get(m, base) for m in METRICS}
    return common.facts_and_slots(scope, METRICS, available, reasons, b - a + 1)


def notes(raw, scope, label):
    lines = []
    if raw["entity_id"] == "network":
        lines.append(f"{len(raw['generators'])} variable generators.")
    bad = invalid(raw, scope) if raw["valid_solve"] else []
    if bad:
        lines.append("Unavailable for: " + ", ".join(label(g) for g in bad[:5])
                     + (f" and {len(bad) - 5} more." if len(bad) > 5 else "."))
    return lines
