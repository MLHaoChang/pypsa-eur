"""Bounded StorageUnit trajectory checks, independent of model/tool aggregates."""

import math

import pandas as pd

from services import workflow_tools

PROFILE = "storage-unit-dispatch/1"
ABS_TOLERANCE = 1e-7
REL_TOLERANCE = 1e-8
METRICS = {
    "charged_energy": {
        "formula_id": "storage-charge-energy/1",
        "unit": "MWh",
        "label": "Charging energy (grid side)",
    },
    "discharged_energy": {
        "formula_id": "storage-discharge-energy/1",
        "unit": "MWh",
        "label": "Discharged energy (grid side)",
    },
    "net_storage_energy": {
        "formula_id": "storage-net-grid-energy/1",
        "unit": "MWh",
        "label": "Net discharged grid energy",
    },
    "peak_charging_power": {
        "formula_id": "storage-charge-peak/1",
        "unit": "MW",
        "label": "Peak charging power",
    },
    "peak_discharging_power": {
        "formula_id": "storage-discharge-peak/1",
        "unit": "MW",
        "label": "Peak discharging power",
    },
    "final_state_of_charge": {
        "formula_id": "storage-window-final-state/1",
        "unit": "MWh",
        "label": "Stored energy at window end",
    },
}
DISCLOSURE = (
    "Storage-operation energy uses snapshot_weightings.stores as durations, not generator or objective weights. "
    "Net energy is discharged minus charged grid-side energy, not the change in stored energy. "
    "Charging and discharging are read separately; simultaneous operation is not excluded. "
    "The chronological energy balance includes charging/discharging efficiency and standing loss. "
    "Only noncyclic StorageUnits with zero inflow/spill are supported; no storage cost, "
    "round-trip efficiency estimate, degradation or engineering certification is established."
)


def source(ctx, entity, allow_cyclic=False):
    """Capture complete raw trajectory under the harness network lock."""
    from harness.calculations import MAX_SNAPSHOTS, _number, _series, solve_matches_inputs

    n, index = ctx.network, ctx.network.snapshots
    if (
        not isinstance(index, pd.DatetimeIndex)
        or not index.is_unique
        or not index.is_monotonic_increasing
        or not 1 <= len(index) <= MAX_SNAPSHOTS
        or len(n.investment_periods)
    ):
        raise ValueError("unsupported_storage_snapshot_scope")
    component = n.components["StorageUnit"]
    if entity not in component.static.index or not component.static.index.is_unique:
        raise ValueError("unknown_storage_unit")
    row = component.static.loc[entity]
    cyclic = bool(row["cyclic_state_of_charge"])
    if _number(row["sign"]) != 1 or (cyclic and not allow_cyclic):
        raise ValueError("unsupported_storage_boundary")
    weights = n.snapshot_weightings
    if not weights.index.equals(index) or not weights.index.is_unique:
        raise ValueError("misaligned_storage_weights")
    inputs = {"duration": [_number(v) for v in weights["stores"]]}
    for attribute in ("p_store", "p_dispatch", "p", "state_of_charge"):
        table = component.dynamic.get(attribute)
        if (
            table is None
            or not table.index.equals(index)
            or not table.index.is_unique
            or not table.columns.is_unique
            or set(table.columns) != set(component.static.index)
        ):
            raise ValueError("misaligned_storage_output")
        inputs[attribute] = [_number(v) for v in table[entity]]
    for attribute in (
        "p_min_pu",
        "p_max_pu",
        "efficiency_store",
        "efficiency_dispatch",
        "standing_loss",
        "inflow",
    ):
        inputs[attribute] = _series(component, attribute, entity, index)
    if any(v != 0 for v in inputs["inflow"]):
        raise ValueError("unsupported_storage_inflow")
    spill = component.dynamic.get("spill")
    # PyPSA does not create spill variables for zero-inflow units. This is a
    # bounded model structural zero, never a replacement for missing dispatch.
    if spill is not None and entity in spill.columns:
        if not spill.index.equals(index) or not spill.columns.is_unique:
            raise ValueError("misaligned_storage_spill")
        if any(_number(v) != 0 for v in spill[entity]):
            raise ValueError("unsupported_storage_spill")
    fingerprint = workflow_tools._network_input_fingerprint(ctx)
    state = ctx.solver_state
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "inputs": inputs,
        "capacity": _number(
            row["p_nom_opt"] if row["p_nom_extendable"] else row["p_nom"]
        ),
        "max_hours": _number(row["max_hours"]),
        "cyclic": cyclic,
        "initial_state": _number(row["state_of_charge_initial"]),
        "input_fingerprint": fingerprint,
        "solved_fingerprint": n.meta.get("assistant_solve_input_fingerprint"),
        "status": state.get("status"),
        "condition": state.get("condition"),
        "valid_solve": solve_matches_inputs(ctx),
    }


SNAPSHOT_KEYS = (
    "duration", "p_store", "p_dispatch", "p", "state_of_charge", "p_min_pu", "p_max_pu",
    "efficiency_store", "efficiency_dispatch", "standing_loss", "inflow",
)


def snapshot_within_bounds(values, capacity, hours):
    """One snapshot's own limits, without the predecessor chain. Shared by this
    profile and storage-cycles/1 so both judge a snapshot the same way."""
    if any(v is None for v in values):
        return False
    dt, charge, discharge, _net, state, lo, hi, eta_in, eta_out, loss, inflow = values
    return (
        dt > 0
        and charge >= 0
        and discharge >= 0
        and lo <= 0 <= hi
        and 0 < eta_in <= 1
        and 0 < eta_out <= 1
        and 0 <= loss <= 1
        and inflow == 0
        and charge <= -lo * capacity + ABS_TOLERANCE
        and discharge <= hi * capacity + ABS_TOLERANCE
        and -ABS_TOLERANCE <= state <= capacity * hours + ABS_TOLERANCE
    )


def calculate(raw, scope):
    """Check the full predecessor chain before aggregating the exact window."""
    from harness.calculations import MAX_SNAPSHOTS, _number, requested_metrics

    if (
        type(raw["version"]) is not int
        or raw["version"] != 1
        or raw["profile"] != PROFILE
        or raw["entity_id"] != scope["entity_id"]
    ):
        raise ValueError("storage_source_identity")
    index = pd.DatetimeIndex(raw["index"])
    if (
        not index.is_unique
        or not index.is_monotonic_increasing
        or not 1 <= len(index) <= MAX_SNAPSHOTS
    ):
        raise ValueError("invalid_storage_index")
    count, inputs = len(index), raw["inputs"]
    keys = SNAPSHOT_KEYS
    if any(len(inputs[k]) != count for k in keys):
        raise ValueError("invalid_storage_population")
    a, b = 0, count - 1
    if "start" in scope:
        a, b = (
            index.get_loc(pd.Timestamp(scope["start"])),
            index.get_loc(pd.Timestamp(scope["end"])),
        )
        if b < a:
            raise ValueError("reversed_storage_window")
    capacity, hours, previous = (
        _number(raw[k]) for k in ("capacity", "max_hours", "initial_state")
    )
    prefix_valid = (
        raw["valid_solve"] is True
        and capacity is not None
        and capacity >= 0
        and hours is not None
        and hours >= 0
        and previous is not None
        and math.isfinite(capacity * hours)
        and 0 <= previous <= capacity * hours
    )
    mask = []
    for i in range(count):
        dt, charge, discharge, net, state, lo, hi, eta_in, eta_out, loss, inflow = (
            _number(inputs[k][i]) for k in keys
        )
        ok = prefix_valid and snapshot_within_bounds(
            (dt, charge, discharge, net, state, lo, hi, eta_in, eta_out, loss, inflow), capacity, hours
        )
        if ok:
            try:
                expected = (1 - loss) ** dt * previous + dt * (
                    eta_in * charge - discharge / eta_out
                )
                ok = (
                    math.isfinite(expected)
                    and math.isclose(
                        net,
                        discharge - charge,
                        rel_tol=REL_TOLERANCE,
                        abs_tol=ABS_TOLERANCE,
                    )
                    and math.isclose(
                        state, expected, rel_tol=REL_TOLERANCE, abs_tol=ABS_TOLERANCE
                    )
                )
            except (OverflowError, ValueError):
                ok = False
        prefix_valid = bool(ok)
        mask.append(prefix_valid)
        previous = state
    selected = list(range(a, b + 1))
    complete = all(mask[i] for i in selected)
    available = {}
    if complete:
        try:
            charged = math.fsum(
                inputs["duration"][i] * inputs["p_store"][i] for i in selected
            )
            discharged = math.fsum(
                inputs["duration"][i] * inputs["p_dispatch"][i] for i in selected
            )
            available = {
                "charged_energy": charged,
                "discharged_energy": discharged,
                "net_storage_energy": discharged - charged,
                "peak_charging_power": max(inputs["p_store"][i] for i in selected),
                "peak_discharging_power": max(
                    inputs["p_dispatch"][i] for i in selected
                ),
                "final_state_of_charge": inputs["state_of_charge"][b],
            }
        except (OverflowError, ValueError):
            available = {}
    facts, slots = [], []
    for metric in requested_metrics(scope):
        value = available.get(metric)
        answered = metric in METRICS and value is not None and math.isfinite(value)
        slot = {
            "metric": metric,
            "disposition": "answered" if answered else "unavailable",
            "requested_snapshots": len(selected),
            "valid_snapshots": sum(mask[i] for i in selected),
        }
        if answered:
            definition = METRICS[metric]
            facts.append(
                {
                    "path": "/" + metric,
                    "entity_id": scope["entity_id"],
                    "metric": metric,
                    "value": value,
                    "unit": definition["unit"],
                    "formula_id": definition["formula_id"],
                }
            )
        else:
            slot["reason"] = (
                "invalid_or_missing_storage_trajectory"
                if metric in METRICS
                else "unsupported_metric"
            )
        slots.append(slot)
    return facts, slots
