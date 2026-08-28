"""Stage 0 (minimal): reference networks with canonical IDs assigned.
Allowed to import pandapower (with static/ and handoff/)."""
import pandas as pd
import pandapower.networks as pn

from gridspine.schema.network import validate_canonical
from gridspine.schema.network import unit_registry as _unit_registry


def load_case39():
    net = pn.case39()
    net.bus["name"] = [f"BUS_{i + 1:02d}" for i in range(len(net.bus))]
    bus_name = net.bus["name"]
    net.gen["name"] = [f"G_{bus_name.at[b]}" for b in net.gen["bus"]]
    net.ext_grid["name"] = [f"SLK_{bus_name.at[b]}" for b in net.ext_grid["bus"]]
    unit_names = list(net.gen["name"]) + list(net.ext_grid["name"])
    validate_canonical(net.bus["name"], pd.Series(unit_names))
    return net


def registry_from_net(net):
    bus_name = net.bus["name"]
    return _unit_registry(
        gen_names=net.gen["name"],
        gen_buses=[bus_name.at[b] for b in net.gen["bus"]],
        ext_names=net.ext_grid["name"],
        ext_buses=[bus_name.at[b] for b in net.ext_grid["bus"]],
    )
