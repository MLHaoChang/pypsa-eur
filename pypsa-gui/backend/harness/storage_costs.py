"""Declared-basis direct StorageUnit costs from independently checked trajectories."""

import math

import pandas as pd

from harness import storage_calculations as storage

PROFILE = "storage-unit-cost/1"
TOTAL_DEFINITION = {
    "formula_id": "storage-direct-total-cost/1",
    "unit": "EUR",
    "label": "Total direct StorageUnit cost on the declared basis",
    "dependencies": ("allocated_fixed_cost", "storage_operating_expenditure"),
}
METRICS = {
    "storage_discharge_expenditure": {
        "formula_id": "storage-discharge-expenditure/1",
        "unit": "EUR",
        "label": "StorageUnit discharge expenditure",
    },
    "storage_holding_expenditure": {
        "formula_id": "storage-state-holding-expenditure/1",
        "unit": "EUR",
        "label": "StorageUnit holding expenditure",
    },
    "storage_operating_expenditure": {
        "formula_id": "storage-linear-operation-cost/1",
        "unit": "EUR",
        "label": "Direct StorageUnit operating expenditure",
        "dependencies": (
            "storage_discharge_expenditure",
            "storage_holding_expenditure",
        ),
    },
}


def source(ctx, entity):
    from harness.calculations import _number, _series

    raw = storage.source(ctx, entity, split=True)
    n = ctx.network
    component = n.components["StorageUnit"]
    if entity not in component.static.index:
        return {**raw, "costs": _split_costs(n, entity)}
    row = component.static.loc[entity]
    costs = {
        "version": 1,
        "objective_weight": [_number(v) for v in n.snapshot_weightings["objective"]],
        "capital_cost": _number(row["capital_cost"]),
        # This contract assumes capital_cost is already an annual charge. Other
        # fixed-cost derivations cannot silently be dropped or inferred.
        "direct_capital_charge": bool(pd.isna(row["overnight_cost"]))
        and _number(row["fom_cost"]) == 0,
        "linear_model": bool(row["active"]) and _number(row["p_nom_mod"]) == 0,
    }
    for attribute in (
        "marginal_cost",
        "marginal_cost_storage",
        "marginal_cost_quadratic",
    ):
        costs[attribute] = _series(component, attribute, entity, n.snapshots)
    return {**raw, "costs": costs}


def _split_costs(n, entity):
    """A migrated unit's costs in StorageUnit terms: its capital_cost on the charger (per MW), its
    marginal_cost per MWh dispatched = the discharger's per MWh drawn / efficiency, its
    marginal_cost_storage on the Store (store-side MWh held, as the unit's state of charge)."""
    from harness.calculations import _number, _series

    store, charger, discharger, _, _ = storage.split_view(n, entity)
    stores, links, index = n.components["Store"], n.components["Link"], n.snapshots
    members = ((store, "e_nom_mod"), (charger, "p_nom_mod"), (discharger, "p_nom_mod"))
    drawn = _series(links, "marginal_cost", discharger.name, index)
    eta = _series(links, "efficiency", discharger.name, index)
    quadratic = _series(links, "marginal_cost_quadratic", discharger.name, index)

    def divide(v, e, power=1):
        return None if v is None or e is None or e <= 0 else v / e**power

    return {
        "version": 1,
        "objective_weight": [_number(v) for v in n.snapshot_weightings["objective"]],
        "capital_cost": _number(charger["capital_cost"]),
        # Only the charger's capital_cost is the unit's annual charge; any other fixed term is unsupported.
        "direct_capital_charge": all(
            bool(pd.isna(row["overnight_cost"])) and _number(row["fom_cost"]) == 0
            for row, _ in members
        )
        and _number(store["capital_cost"]) == 0
        and _number(discharger["capital_cost"]) == 0,
        "linear_model": all(
            bool(row["active"]) and _number(row[mod]) == 0 for row, mod in members
        ),
        "marginal_cost": [divide(v, e) for v, e in zip(drawn, eta, strict=True)],
        "marginal_cost_storage": _series(
            stores, "marginal_cost_storage", store.name, index
        ),
        "marginal_cost_quadratic": [
            divide(v, e, 2) for v, e in zip(quadratic, eta, strict=True)
        ],
    }


def calculate(raw, scope):
    from harness import calculations as calc

    trajectory = raw["storage_unit"]
    operation_scope = {
        **scope,
        "profile": storage.PROFILE,
        "metrics": list(storage.METRICS),
    }
    operation_facts, operation_slots = storage.calculate(trajectory, operation_scope)
    available = {f["metric"]: f["value"] for f in operation_facts}
    reasons = {s["metric"]: s.get("reason") for s in operation_slots}
    requested = operation_slots[0]["requested_snapshots"]
    valid = operation_slots[0]["valid_snapshots"]
    complete = all(s["disposition"] == "answered" for s in operation_slots)
    costs, count = trajectory["costs"], len(trajectory["index"])
    if type(costs["version"]) is not int or costs["version"] != 1:
        raise ValueError("unsupported_storage_cost_version")
    keys = (
        "objective_weight",
        "marginal_cost",
        "marginal_cost_storage",
        "marginal_cost_quadratic",
    )
    if any(len(costs[k]) != count for k in keys):
        raise ValueError("invalid_storage_cost_population")
    index = trajectory["index"]
    a, b = 0, count - 1
    if "start" in scope:
        a, b = (
            index.index(pd.Timestamp(scope[k]).isoformat()) for k in ("start", "end")
        )
    selected = range(a, b + 1)
    basis = scope.get("cost_basis")
    linear = costs["linear_model"] is True and all(
        v == 0 for v in costs["marginal_cost_quadratic"]
    )
    safe = complete and basis is not None and linear
    reason = (
        "invalid_or_missing_storage_trajectory"
        if not complete
        else "explicit_cost_basis_required"
        if basis is None
        else "unsupported_or_invalid_storage_cost_model"
        if not linear
        else "invalid_or_missing_storage_cost_dependency"
    )
    reasons.update({m: reason for m in (*METRICS, *calc.COST_METRICS)})

    def publish(metric, value):
        if math.isfinite(value):
            available[metric] = value

    if safe:
        for metric, quantity, price in (
            ("storage_discharge_expenditure", "p_dispatch", "marginal_cost"),
            ("storage_holding_expenditure", "state_of_charge", "marginal_cost_storage"),
        ):
            weights = [calc._number(costs["objective_weight"][i]) for i in selected]
            prices = [calc._number(costs[price][i]) for i in selected]
            if all(w is not None and w > 0 for w in weights) and all(
                p is not None for p in prices
            ):
                try:
                    publish(
                        metric,
                        math.fsum(
                            trajectory["inputs"][quantity][i] * p * w
                            for i, p, w in zip(selected, prices, weights, strict=True)
                        ),
                    )
                except (OverflowError, ValueError):
                    pass
        if all(
            m in available
            for m in METRICS["storage_operating_expenditure"]["dependencies"]
        ):
            try:
                publish(
                    "storage_operating_expenditure",
                    math.fsum(
                        available[m]
                        for m in METRICS["storage_operating_expenditure"][
                            "dependencies"
                        ]
                    ),
                )
            except (OverflowError, ValueError):
                pass
        capacity, capital = (
            calc._number(trajectory["capacity"]),
            calc._number(costs["capital_cost"]),
        )
        if (
            costs["direct_capital_charge"] is True
            and capital is not None
            and capital >= 0
        ):
            publish("annual_fixed_cost", capacity * capital)
            if "annual_fixed_cost" in available:
                publish(
                    "allocated_fixed_cost",
                    available["annual_fixed_cost"]
                    * basis["annual_fixed_cost_fraction"],
                )
        else:
            reasons.update(
                {
                    m: "unsupported_or_invalid_storage_fixed_cost"
                    for m in calc.COST_METRICS
                }
            )
        if (
            "allocated_fixed_cost" in available
            and "storage_operating_expenditure" in available
        ):
            try:
                publish(
                    "total_cost",
                    math.fsum(
                        [
                            available["allocated_fixed_cost"],
                            available["storage_operating_expenditure"],
                        ]
                    ),
                )
            except (OverflowError, ValueError):
                pass
    calc._comparison_values(raw, available, reasons)
    allowed = calc.PROFILES[PROFILE] + tuple(calc.COMPARISON_METRICS)
    facts, slots = [], []
    for metric in calc.requested_metrics(scope):
        value = available.get(metric) if metric in allowed else None
        answered = value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if answered else "unavailable",
            "requested_snapshots": requested,
        }
        # The physical trajectory count does not validate missing/unsupported
        # cost dependencies. Do not display it as complete cost-data coverage.
        if metric in storage.METRICS or answered:
            slot["valid_snapshots"] = valid
        if answered:
            d = calc.definition(metric, PROFILE)
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": scope["entity_id"],
                    "metric": metric,
                    "value": value,
                    "unit": d["unit"],
                    "formula_id": d["formula_id"],
                }
            )
        else:
            slot["reason"] = reasons.get(metric) or "unsupported_metric"
        slots.append(slot)
    return facts, slots


def disclosure(scope):
    from harness.calculations import _comparison_disclosure

    basis = scope.get("cost_basis")
    declared = (
        f"Declared model basis: {basis['currency']}, price year {basis['price_year']}; "
        f"annual fixed-cost fraction {basis['annual_fixed_cost_fraction']}. "
        if basis
        else "Storage cost accounting requires an explicit EUR price year and annual fixed-cost fraction. "
    )
    return (
        declared + storage.DISCLOSURE.replace("no storage cost, ", "no ") + " "
        "Direct StorageUnit costs use raw discharge power and end-of-snapshot stored energy "
        "with objective weights, not storage-duration or generator weights. Capital cost is "
        "treated as an annual charge on installed power capacity; its allocation is explicitly declared. "
        "The total excludes charging electricity purchases, discharge revenues and costs of other assets; "
        "it is not the solver objective, total network cost, profitability or a discounted NPV. "
        "The declared price year is an assumption, not a verified currency conversion. "
        "Only linear direct operation and capital_cost charges are supported; overnight-cost "
        "annuity, separate FOM, modular/inactive assets and quadratic costs are unsupported."
        + _comparison_disclosure(scope)
    )
