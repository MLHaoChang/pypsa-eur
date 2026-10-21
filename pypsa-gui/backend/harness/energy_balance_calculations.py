"""Bounded energy-flow totals for checked answers (checked-results S7).

The Energy flow tab's Source -> Via -> Destination rows are its own
proportional attribution and are not independently checkable; they stay
withheld. Its totals are: generation (excluding VoLL slacks and process
shortfall units), served demand (load less the solve's shed and the
shortfall), the storage change (the tab's state-of-charge delta in generator
weights) and the remainder, which by the tab's own closure is its Export plus
Losses total. A per-bus, per-snapshot balance over every component gates them.
"""

import math

import numpy as np

from harness import profile_common as common

PROFILE = "energy-balance/1"
BUS_TOL_MW = 1e-5
SERVED_TOL = 1e-6
REFUSALS = frozenset({
    "network_scope_required", "non_energy_bus", "legacy_lost_load_capture", "unsupported_snapshot_scope",
    "misaligned_source", "shortfall_without_load",
})
METRICS = {
    "generation_energy": {
        "formula_id": "site-generation-energy/1",
        "unit": "MWh",
        "label": "Generation (excluding VoLL slacks and process shortfall)",
    },
    "served_energy": {
        "formula_id": "site-served-energy/1",
        "unit": "MWh",
        "label": "Demand served",
    },
    "unserved_energy": {
        "formula_id": "site-unserved-energy/1",
        "unit": "MWh",
        "label": "Demand not served (shed and process shortfall)",
    },
    "storage_change": {
        "formula_id": "site-storage-change/1",
        "unit": "MWh",
        "label": "Change in stored energy",
    },
    "export_losses_and_conversion": {
        "formula_id": "site-export-losses-conversion/1",
        "unit": "MWh",
        "label": "Export, branch and storage losses, and conversion to other carriers (not split)",
    },
}
DISCLOSURE = (
    "Totals of the Energy flow tab, rebuilt from the solve: generator weights for energy, the storage "
    "state change converted as the tab does. The mix by source and route is the tab's attribution and "
    "is not checked. A per-bus balance of every component must close for any figure to be given."
)


def _plain(value):
    """JSON-ready copy: evidence is hashed and stored as JSON."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _array(value):
    return None if value is None else np.asarray(value, dtype=float)


def _frame(table, names, index):
    """T x len(names) values, or None when any column, row or value is missing."""
    if not names:
        return np.zeros((len(index), 0))
    if table is None or not table.index.equals(index) or any(c not in table.columns for c in names):
        return None
    out = table[names].to_numpy(dtype=float)
    return out if np.isfinite(out).all() else None


def source(ctx, entity):
    """Called under the network lock: raw arrays only; arithmetic in calculate."""
    from models.process_unit import product_role_generators
    from services.adequacy.slack import slack_generator_mask
    from services.results.energy_flows import _is_energy_unit

    n = ctx.network
    index = common.single_period_index(n)
    if entity != "network":
        raise ValueError("network_scope_required")
    units = n.buses["unit"] if "unit" in n.buses.columns else None
    if units is not None and not all(_is_energy_unit(u) for u in units):
        raise ValueError("non_energy_bus")
    gens = n.generators
    all_gens = [str(g) for g in gens.index]
    slack = set(map(str, gens.index[slack_generator_mask(gens).to_numpy()])) if len(gens) else set()
    shortfall = [str(g) for g in product_role_generators(n, "shortfall") if str(g) in set(all_gens)]
    kept = [g for g in all_gens if g not in slack and g not in shortfall]
    loads = [str(x) for x in n.loads.index]
    capture = (ctx.solver_state.get("last_lost_load") or {}).get("lost_load_t")
    shed = None
    if capture is not None and not capture.empty:
        if any(str(c) not in loads for c in capture.columns) or not capture.index.equals(index):
            raise ValueError("legacy_lost_load_capture")
        shed = np.maximum(np.nan_to_num(capture.to_numpy(dtype=float)), 0.0).sum(axis=1)
    load_buses = {str(n.loads.at[x, "bus"]) for x in loads}
    if any(str(gens.at[g, "bus"]) not in load_buses for g in shortfall):
        raise ValueError("shortfall_without_load")
    branches = []
    for name in n.links.index:
        ports = [c[3:] for c in n.links.columns if c.startswith("bus") and c[3:].isdigit()
                 and str(n.links.at[name, c]).strip()]
        branches.append(("links_t", str(name), [(str(n.links.at[name, f"bus{k}"]), f"p{k}") for k in ports]))
    for component, accessor in (("lines", "lines_t"), ("transformers", "transformers_t")):
        df = getattr(n, component)
        for name in df.index:
            branches.append((accessor, str(name), [(str(df.at[name, "bus0"]), "p0"), (str(df.at[name, "bus1"]), "p1")]))
    flows = []
    for accessor, name, ports in branches:
        for bus, attr in ports:
            values = _frame(getattr(getattr(n, accessor), attr, None), [name], index)
            flows.append((bus, None if values is None else values[:, 0]))
    storage = []
    for name in n.storage_units.index:
        row = n.storage_units.loc[name]
        p = _frame(n.storage_units_t.p, [name], index)
        soc = _frame(n.storage_units_t.state_of_charge, [name], index)
        start = (None if soc is None else soc[-1, 0]) if bool(row["cyclic_state_of_charge"]) else max(
            float(row["state_of_charge_initial"]), 0.0)
        storage.append((str(row["bus"]), None if p is None else p[:, 0], None if soc is None else soc[:, 0], start))
    for name in n.stores.index:
        row = n.stores.loc[name]
        p = _frame(n.stores_t.p, [name], index)
        e = _frame(n.stores_t.e, [name], index)
        start = (None if e is None else e[-1, 0]) if bool(row["e_cyclic"]) else max(float(row["e_initial"]), 0.0)
        storage.append((str(row["bus"]), None if p is None else p[:, 0], None if e is None else e[:, 0], start))
    weights = n.snapshot_weightings
    return _plain({
        "version": 1,
        "profile": PROFILE,
        "entity_id": entity,
        "index": [v.isoformat() for v in index],
        "w_gen": weights["generators"].to_numpy(dtype=float).tolist(),
        "w_store": weights["stores"].to_numpy(dtype=float).tolist(),
        "gen_p": _frame(n.generators_t.p, all_gens, index),
        "gen_bus": [str(gens.at[g, "bus"]) for g in all_gens],
        "kept": [all_gens.index(g) for g in kept],
        "shortfall": [all_gens.index(g) for g in shortfall],
        "load_p": _frame(n.loads_t.p, loads, index),
        "load_bus": [str(n.loads.at[x, "bus"]) for x in loads],
        "load_names": loads,
        "bus_carrier": {str(b): (str(c) if isinstance(c, str) and c else "AC")
                        for b, c in n.buses["carrier"].items()},
        "shed_by_load": None if capture is None or capture.empty else
        {str(c): np.maximum(np.nan_to_num(capture[c].to_numpy(dtype=float)), 0.0) for c in capture.columns},
        "shed": shed,
        "flows": flows,
        "storage": storage,
        "valid_solve": common.lp_outputs_live(ctx),
    })


def _bus_balance_closes(raw):
    """Every bus nets to zero in every snapshot, within the tab's BUS_TOL_MW."""
    gen_p, load_p = _array(raw["gen_p"]), _array(raw["load_p"])
    if gen_p is None or load_p is None:
        return False
    T = len(raw["index"])
    net, scale = {}, {}

    def add(bus, values):
        net[bus] = net.get(bus, np.zeros(T)) + values
        scale[bus] = np.maximum(scale.get(bus, np.zeros(T)), np.abs(values))

    for j, bus in enumerate(raw["gen_bus"]):
        add(bus, gen_p.reshape(T, -1)[:, j])
    for j, bus in enumerate(raw["load_bus"]):
        add(bus, -load_p.reshape(T, -1)[:, j])
    for bus, p, _state, _start in raw["storage"]:
        if p is None:
            return False
        add(bus, _array(p))
    for bus, p in raw["flows"]:
        if p is None:
            return False
        add(bus, -_array(p))
    return all((np.abs(net[b]) <= BUS_TOL_MW * np.maximum(1.0, scale[b])).all() for b in net)


def calculate(raw, scope):
    if raw.get("version") != 1 or raw.get("profile") != PROFILE or raw["entity_id"] != scope["entity_id"]:
        raise ValueError("energy_balance_source_identity")
    a, b = common.window(raw["index"], scope)
    base = "unverified_solve" if not raw["valid_solve"] else "balance_not_established"
    available = {}
    if raw["valid_solve"] and _bus_balance_closes(raw) and all(
        s is not None and start is not None for _bus, _p, s, start in raw["storage"]
    ):
        w = np.array(raw["w_gen"])
        dt = np.array(raw["w_store"])
        T = len(w)
        gp, lp = _array(raw["gen_p"]).reshape(T, -1), _array(raw["load_p"]).reshape(T, -1)
        kept = gp[:, raw["kept"]] if raw["kept"] else np.zeros((len(w), 0))
        short = np.maximum(gp[:, raw["shortfall"]], 0.0).sum(axis=1) if raw["shortfall"] else np.zeros(len(w))
        shed = _array(raw["shed"]) if raw["shed"] is not None else np.zeros(len(w))
        served_t = lp.sum(axis=1) - shed - short
        delta = np.zeros(len(w))
        for _bus, _p, state, start in raw["storage"]:
            delta += np.diff(np.concatenate(([start], _array(state))))
        if (dt > 0).all() and (served_t >= -SERVED_TOL * (1 + lp.sum(axis=1).max(initial=0.0))).all():
            rows = slice(a, b + 1)
            generation = math.fsum((np.maximum(kept, 0.0).sum(axis=1) * w)[rows])
            served = math.fsum((np.maximum(served_t, 0.0) * w)[rows])
            change = math.fsum((delta * w / dt)[rows])
            available = {
                "generation_energy": generation,
                "served_energy": served,
                "unserved_energy": math.fsum(((shed + short) * w)[rows]),
                "storage_change": change,
                "export_losses_and_conversion": generation - served - change,
            }
    return common.facts_and_slots(scope, METRICS, available, {m: base for m in METRICS}, b - a + 1)


def _by_carrier(raw, scope):
    """Generation, served and unserved MWh per bus carrier over the window."""
    T = len(raw["index"])
    a, b = common.window(raw["index"], scope)
    w = np.array(raw["w_gen"])[a : b + 1]
    gp = _array(raw["gen_p"]).reshape(T, -1)[a : b + 1]
    lp = _array(raw["load_p"]).reshape(T, -1)[a : b + 1]
    carrier = raw["bus_carrier"]
    out = {}

    def add(key, kind, values):
        row = out.setdefault(key, {"generation": 0.0, "served": 0.0, "unserved": 0.0})
        row[kind] += math.fsum(values * w)

    for j in raw["kept"]:
        add(carrier[raw["gen_bus"][j]], "generation", np.maximum(gp[:, j], 0.0))
    shed = raw.get("shed_by_load") or {}
    lost = {}
    for j in raw["shortfall"]:
        bus = raw["gen_bus"][j]
        lost[bus] = lost.get(bus, 0) + np.maximum(gp[:, j], 0.0)
    for j, bus in enumerate(raw["load_bus"]):
        add(carrier[bus], "served", lp[:, j])
    for bus, values in lost.items():
        add(carrier[bus], "unserved", values)
        add(carrier[bus], "served", -values)
    for load, values in shed.items():
        bus = raw["load_bus"][list(raw.get("load_names", [])).index(load)] if load in raw.get("load_names", []) else None
        if bus is not None:
            add(carrier[bus], "unserved", np.asarray(values)[a : b + 1])
            add(carrier[bus], "served", -np.asarray(values)[a : b + 1])
    return out


def notes(raw, scope, label):
    lines = [
        "Totals only: the Energy flow tab's split by source and route is its own attribution and is "
        "not checked.",
        "Export, losses and conversion is derived (generation minus served minus storage change), "
        "not separately measured.",
    ]
    if not raw["valid_solve"] or not _bus_balance_closes(raw):
        return lines
    split = _by_carrier(raw, scope)
    if len(split) > 1:
        def fmt(kind):
            return ", ".join(f"{label(c)} {repr(round(v[kind], 9)).removesuffix('.0')}"
                             for c, v in sorted(split.items()) if v[kind])
        lines.append("These totals add MWh of different carriers. Generation: " + (fmt("generation") or "none")
                     + "; served: " + (fmt("served") or "none") + "; unserved: " + (fmt("unserved") or "none") + ".")
    return lines
