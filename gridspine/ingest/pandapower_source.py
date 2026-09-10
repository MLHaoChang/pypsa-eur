"""Stage 0 (minimal): reference networks with canonical IDs assigned.
Allowed to import pandapower (with static/ and handoff/)."""
import pandas as pd
import pandapower as pp
import pandapower.networks as pn

from gridspine.schema.contracts import ContractError
from gridspine.schema.network import validate_canonical
from gridspine.schema.network import unit_registry as _unit_registry

# LEDGER ASSUMPTION, not measured data. The IEEE 39-bus case carries no
# renewables, so `load_case39_res` invents them: three wind farms and two solar
# parks sited on the generator-adjacent buses of the New England 345 kV ring,
# at round capacities chosen to be a material but not overwhelming share of the
# ~6.25 GW system load. Nothing downstream may treat these as sourced figures —
# they exist so the RES-aware stages have something to chew on, and they are
# recorded here (rather than inline in the loader) so the invention is visible.
# `p_mw` is INSTALLED capacity; per-hour output is a separate concern.
RES_LEDGER = (
    {"name": "W_BUS_33", "bus": "BUS_33", "p_mw": 600.0, "tech": "wind"},
    {"name": "W_BUS_35", "bus": "BUS_35", "p_mw": 600.0, "tech": "wind"},
    {"name": "W_BUS_37", "bus": "BUS_37", "p_mw": 600.0, "tech": "wind"},
    {"name": "S_BUS_34", "bus": "BUS_34", "p_mw": 500.0, "tech": "solar"},
    {"name": "S_BUS_36", "bus": "BUS_36", "p_mw": 500.0, "tech": "solar"},
)


def load_case39():
    net = pn.case39()
    net.bus["name"] = [f"BUS_{i + 1:02d}" for i in range(len(net.bus))]
    bus_name = net.bus["name"]
    net.gen["name"] = [f"G_{bus_name.at[b]}" for b in net.gen["bus"]]
    net.ext_grid["name"] = [f"SLK_{bus_name.at[b]}" for b in net.ext_grid["bus"]]
    unit_names = list(net.gen["name"]) + list(net.ext_grid["name"])
    validate_canonical(net.bus["name"], pd.Series(unit_names))
    return net


def load_case39_res():
    """`load_case39()` plus the RES_LEDGER sites as pandapower **sgen** rows.

    sgen is the right component: these are PQ injections with no voltage
    setpoint and no slack duty, unlike `gen` (PV) or `ext_grid` (slack). Adding
    them as `gen` would hand each site voltage control it does not have.
    """
    net = load_case39()
    bus_idx = {name: idx for idx, name in net.bus["name"].items()}
    for entry in RES_LEDGER:
        pp.create_sgen(
            net,
            bus=bus_idx[entry["bus"]],
            p_mw=entry["p_mw"],
            q_mvar=0.0,
            name=entry["name"],
            in_service=True,
        )
    unit_names = (
        list(net.gen["name"]) + list(net.ext_grid["name"]) + list(net.sgen["name"])
    )
    validate_canonical(net.bus["name"], pd.Series(unit_names))
    return net


def registry_from_net(net):
    bus_name = net.bus["name"]
    reg = _unit_registry(
        gen_names=net.gen["name"],
        gen_buses=[bus_name.at[b] for b in net.gen["bus"]],
        ext_names=net.ext_grid["name"],
        ext_buses=[bus_name.at[b] for b in net.ext_grid["bus"]],
    )
    # Vanilla case39 has no sgen, so increment-1 callers must come out of here
    # with exactly the frame _unit_registry returned — same rows, same columns,
    # same index name. Concatenating an empty res frame would be a no-op in
    # theory and a dtype change in practice, so skip it outright.
    sgen = getattr(net, "sgen", None)
    if sgen is None or len(sgen) == 0:
        return reg
    res = pd.DataFrame(
        {
            "unit_id": list(sgen["name"]),
            "bus": [bus_name.at[b] for b in sgen["bus"]],
            "kind": "res",
        }
    ).set_index("unit_id")
    out = pd.concat([reg, res])
    if out.index.duplicated().any():
        raise ContractError(
            f"duplicate unit ids: {sorted(out.index[out.index.duplicated()])}"
        )
    return out
