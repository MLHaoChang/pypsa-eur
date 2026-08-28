"""Stage 2 (minimal): snapshot AC load flow. Dispatch arrives as the
validated table; results leave as plain frames keyed by canonical names."""
from dataclasses import dataclass, field

import pandapower as pp
import pandas as pd


@dataclass
class LFResult:
    converged: bool
    bus: pd.DataFrame = field(default_factory=pd.DataFrame)
    branch_loading: pd.DataFrame = field(default_factory=pd.DataFrame)
    slack_p_mw: float = float("nan")


def apply_dispatch(net, table, hour, registry) -> None:
    snap = table[table["hour"] == hour].set_index("unit_id")
    name_to_gen_idx = {net.gen.at[i, "name"]: i for i in net.gen.index}
    for unit_id, rec in registry.iterrows():
        if rec["kind"] != "gen":
            continue  # ext_grid is the slack; it absorbs the residual
        row = snap.loc[unit_id]
        i = name_to_gen_idx[unit_id]
        net.gen.at[i, "p_mw"] = float(row["p_mw"])
        net.gen.at[i, "in_service"] = bool(int(row["status"]))


def run_lf(net) -> LFResult:
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        return LFResult(converged=False)
    bus = pd.DataFrame(
        {"vm_pu": net.res_bus["vm_pu"].values, "va_degree": net.res_bus["va_degree"].values},
        index=pd.Index(net.bus["name"].values, name="bus"),
    )
    line_loading = pd.DataFrame(
        {"loading_percent": net.res_line["loading_percent"].values},
        index=pd.Index([f"L_{i:02d}" for i in net.line.index], name="branch"),
    )
    trafo_loading = pd.DataFrame(
        {"loading_percent": net.res_trafo["loading_percent"].values},
        index=pd.Index([f"T_{i:02d}" for i in net.trafo.index], name="branch"),
    )
    return LFResult(
        converged=bool(net.converged),
        bus=bus,
        branch_loading=pd.concat([line_loading, trafo_loading]),
        slack_p_mw=float(net.res_ext_grid["p_mw"].sum()),
    )
