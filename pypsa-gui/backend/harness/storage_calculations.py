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


def source(ctx, entity, allow_cyclic=False, split=False):
    """Capture complete raw trajectory under the harness network lock.

    With `split`, a name that is no StorageUnit may be a split storage built by
    `migrate_storage_unit` (a tagged Store with its charger and discharger Links),
    read as the StorageUnit it replaced (see `split_view`). A StorageUnit of that
    name always takes the StorageUnit path."""
    from harness.calculations import MAX_SNAPSHOTS, _number, _series

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
    if split and entity not in component.static.index:
        return _split_source(ctx, entity, allow_cyclic)
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
    return _raw(ctx, entity, inputs, _number(
        row["p_nom_opt"] if row["p_nom_extendable"] else row["p_nom"]
    ), _number(row["max_hours"]), cyclic, _number(row["state_of_charge_initial"]))


def _raw(ctx, entity, inputs, capacity, max_hours, cyclic, initial_state):
    from harness.calculations import solve_matches_inputs

    n = ctx.network
    fingerprint = workflow_tools._network_input_fingerprint(ctx)
    state = ctx.solver_state
    return {
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in n.snapshots],
        "inputs": inputs,
        "capacity": capacity,
        "max_hours": max_hours,
        "cyclic": cyclic,
        "initial_state": initial_state,
        "input_fingerprint": fingerprint,
        "solved_fingerprint": n.meta.get("assistant_solve_input_fingerprint"),
        "status": state.get("status"),
        "condition": state.get("condition"),
        "valid_solve": solve_matches_inputs(ctx),
    }


#: Relative tolerance of the migrate layout's size ties (discharger P x eta_d = charger P; E = max_hours x P).
SPLIT_RTOL = 1e-6


def _close(a, b):
    return a is not None and b is not None and math.isclose(a, b, rel_tol=SPLIT_RTOL, abs_tol=SPLIT_RTOL)


def split_view(n, entity):
    """The (Store, charger, discharger) rows of the split storage `migrate_storage_unit` built as `entity`.

    An untagged Store, or no Store, is `unknown_storage_unit`. A tagged one whose layout is not exactly the
    one the migration builds (and so is not a StorageUnit in another form) is `unsupported_split_storage_layout`:
    its extra terms are never folded into, or dropped from, the StorageUnit view."""
    from harness.calculations import _number, _series

    stores, links = n.components["Store"], n.components["Link"]
    if entity not in stores.static.index or not stores.static.index.is_unique:
        raise ValueError("unknown_storage_unit")
    store = stores.static.loc[entity]
    names = tuple(store.get(c) for c in ("charger", "discharger"))
    if not all(isinstance(v, str) and v for v in names):
        raise ValueError("unknown_storage_unit")

    def refuse():
        raise ValueError("unsupported_split_storage_layout")

    charger_name, discharger_name = names
    if (charger_name == discharger_name or not links.static.index.is_unique
            or not {charger_name, discharger_name} <= set(links.static.index)):
        refuse()
    charger, discharger = links.static.loc[charger_name], links.static.loc[discharger_name]
    store_bus, ac_bus = store["bus"], charger["bus0"]
    if not (charger["bus1"] == store_bus == discharger["bus0"] and discharger["bus1"] == ac_bus != store_bus):
        refuse()
    ports = [c for c in links.static.columns if c.startswith("bus") and c[3:].isdigit() and int(c[3:]) > 1]
    if any(isinstance(row.get(c), str) and row.get(c) for row in (charger, discharger) for c in ports):
        refuse()
    # The store bus holds this Store and the two Links only; anything else there moves its energy.
    for component in n.components:
        static = component.static
        if component.name in ("Bus", "SubNetwork") or static.empty:
            continue
        columns = [c for c in static.columns if c == "bus" or (c.startswith("bus") and c[3:].isdigit())]
        on_bus = set(static.index[(static[columns] == store_bus).any(axis=1)]) if columns else set()
        expected = {entity} if component.name == "Store" else {charger_name, discharger_name} \
            if component.name == "Link" else set()
        if on_bus != expected:
            refuse()
    index = n.snapshots
    zero = (
        (stores, entity, ("marginal_cost", "marginal_cost_quadratic", "e_min_pu")),
        (links, charger_name, ("marginal_cost", "marginal_cost_quadratic", "p_min_pu")),
        (links, discharger_name, ("p_min_pu",)),
    )
    for component, name, attributes in zero:
        for attribute in attributes:
            if any(v != 0 for v in _series(component, attribute, name, index)):
                refuse()
    if any(v != 1 for v in _series(stores, "e_max_pu", entity, index)) or _number(store["sign"]) != 1:
        refuse()
    extendable = {bool(store["e_nom_extendable"]), bool(charger["p_nom_extendable"]),
                  bool(discharger["p_nom_extendable"])}
    if len(extendable) != 1:
        refuse()
    opt = extendable.pop()
    power = _number(charger["p_nom_opt" if opt else "p_nom"])
    drawn = _number(discharger["p_nom_opt" if opt else "p_nom"])
    energy = _number(store["e_nom_opt" if opt else "e_nom"])
    hours = tuple(_number(store.get(c)) for c in ("min_hours", "max_hours"))
    eta_d = _number(discharger["efficiency"])
    if (not _close(*hours) or eta_d is None or eta_d <= 0 or drawn is None
            or not _close(drawn * eta_d, power) or power is None or hours[1] is None
            or not _close(energy, hours[1] * power)):
        refuse()
    return store, charger, discharger, power, hours[1]


def _split_source(ctx, entity, allow_cyclic):
    """The StorageUnit inputs of a migrated split storage, all on the AC side: p_store = charger p0,
    p_dispatch = -discharger p1 (kept apart, never netted), state of charge = Store e."""
    from harness.calculations import _number, _series

    n, index = ctx.network, ctx.network.snapshots
    store, charger, discharger, capacity, max_hours = split_view(n, entity)
    cyclic = bool(store["e_cyclic"])
    if cyclic and not allow_cyclic:
        raise ValueError("unsupported_storage_boundary")
    weights = n.snapshot_weightings
    if not weights.index.equals(index) or not weights.index.is_unique:
        raise ValueError("misaligned_storage_weights")
    stores, links = n.components["Store"], n.components["Link"]

    def output(component, attribute, name):
        table = component.dynamic.get(attribute)
        if (table is None or not table.index.equals(index) or not table.index.is_unique
                or not table.columns.is_unique or name not in table.columns):
            raise ValueError("misaligned_storage_output")
        return [_number(v) for v in table[name]]

    p_store = output(links, "p0", charger.name)
    p_dispatch = [None if v is None else -v for v in output(links, "p1", discharger.name)]
    inputs = {
        "duration": [_number(v) for v in weights["stores"]],
        "p_store": p_store,
        "p_dispatch": p_dispatch,
        "p": [None if d is None or s is None else d - s for d, s in zip(p_dispatch, p_store, strict=True)],
        "state_of_charge": output(stores, "e", store.name),
        "p_min_pu": [None if v is None else -v for v in _series(links, "p_max_pu", charger.name, index)],
        "p_max_pu": _series(links, "p_max_pu", discharger.name, index),
        "efficiency_store": _series(links, "efficiency", charger.name, index),
        "efficiency_dispatch": _series(links, "efficiency", discharger.name, index),
        "standing_loss": _series(stores, "standing_loss", store.name, index),
        "inflow": [0.0] * len(index),
    }
    return _raw(ctx, entity, inputs, capacity, max_hours, cyclic, _number(store["e_initial"]))


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
