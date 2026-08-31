"""Stage 2 (minimal): snapshot AC load flow. Dispatch arrives as the
validated table; results leave as plain frames keyed by canonical names."""
from dataclasses import dataclass, field

import pandapower as pp
import pandas as pd

from gridspine.schema.contracts import ContractError

#: Column contract of ``LFResult.branch_flow``. The first three are the join
#: key the PowerFactory read-back compares on; see ``_branch_flow`` for why
#: that triple is authored the way it is.
BRANCH_FLOW_COLUMNS = (
    "from_bus", "to_bus", "ckt", "p_from_mw", "q_from_mvar", "loading_percent",
)


def _empty_branch_flow():
    return pd.DataFrame(columns=list(BRANCH_FLOW_COLUMNS))


@dataclass
class LFResult:
    converged: bool
    bus: pd.DataFrame = field(default_factory=pd.DataFrame)
    branch_loading: pd.DataFrame = field(default_factory=pd.DataFrame)
    slack_p_mw: float = float("nan")
    #: Per-branch FROM-end flow keyed by (from_bus, to_bus, ckt) — the same
    #: triple the RAW writer stamps on each branch record, so a PowerFactory
    #: branch export taken off the .raw joins onto this without a translation
    #: table. Appended last so existing positional construction is unaffected.
    branch_flow: pd.DataFrame = field(default_factory=_empty_branch_flow)


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


def _bus_numbers(net):
    """Bus numbers exactly as the RAW writer assigns them: bus-table order,
    1-based. Kept as a local reproduction rather than an import so `static/`
    does not reach into `handoff/`; the two are locked together by
    tests/gridspine/test_loadflow.py::
    test_branch_flow_keys_map_1to1_onto_the_raw_branch_records.
    """
    return {net.bus.at[i, "name"]: k + 1 for k, i in enumerate(net.bus.index)}


def _branch_flow(net) -> pd.DataFrame:
    """FROM-end flows keyed the way ``raw_writer.write_raw`` keys its records.

    The CKT convention is the whole point of this function, and it is not
    obvious: the writer runs a SEPARATE ``_IdCounter`` for the branch section
    and the transformer section, and each counter is keyed on the SORTED
    bus-number pair — so two circuits between the same pair get '1' and '2'
    even when one of them is written to_bus-first, and a transformer's CKT
    sequence restarts independently of the lines'. Bus names (not numbers)
    are carried here because that is what the .raw NAME field and every other
    gridspine frame use; the numbers only ever appear inside the counter key.
    """
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    # Out-of-service branches are still written to the .raw (STAT=0), so they
    # must still get a row here or the key sets stop matching. pandapower
    # drops them from res_*, hence the reindex to NaN rather than a skip.
    res_line = net.res_line.reindex(net.line.index)
    res_trafo = net.res_trafo.reindex(net.trafo.index)

    def make_next(counter):
        def nxt(a, b):
            key = (min(a, b), max(a, b))
            counter[key] = counter.get(key, 0) + 1
            return str(counter[key])
        return nxt

    next_line_ckt, next_trafo_ckt = make_next({}), make_next({})
    rows = []
    for i in net.line.index:
        f_bus, t_bus = net.line.at[i, "from_bus"], net.line.at[i, "to_bus"]
        f_name, t_name = name_of.at[f_bus], name_of.at[t_bus]
        rows.append({
            "from_bus": f_name,
            "to_bus": t_name,
            "ckt": next_line_ckt(nums[f_name], nums[t_name]),
            "p_from_mw": float(res_line.at[i, "p_from_mw"]),
            "q_from_mvar": float(res_line.at[i, "q_from_mvar"]),
            "loading_percent": float(res_line.at[i, "loading_percent"]),
        })
    for i in net.trafo.index:
        hv, lv = net.trafo.at[i, "hv_bus"], net.trafo.at[i, "lv_bus"]
        hv_name, lv_name = name_of.at[hv], name_of.at[lv]
        rows.append({
            # The writer emits transformers HV-first, so "from" is the HV side.
            "from_bus": hv_name,
            "to_bus": lv_name,
            "ckt": next_trafo_ckt(nums[hv_name], nums[lv_name]),
            "p_from_mw": float(res_trafo.at[i, "p_hv_mw"]),
            "q_from_mvar": float(res_trafo.at[i, "q_hv_mvar"]),
            "loading_percent": float(res_trafo.at[i, "loading_percent"]),
        })
    out = pd.DataFrame(rows, columns=list(BRANCH_FLOW_COLUMNS))
    dupes = out.duplicated(subset=["from_bus", "to_bus", "ckt"], keep=False)
    if dupes.any():
        # Reachable only when a line and a transformer share a bus pair: PSS/E
        # keeps those in different record sections so the .raw stays legal,
        # but this frame has one flat key space and the comparison would join
        # the wrong rows. Fail loudly rather than silently fan out.
        raise ContractError(
            "duplicate branch keys (from_bus, to_bus, ckt): "
            f"{sorted(set(map(tuple, out.loc[dupes, ['from_bus', 'to_bus', 'ckt']].values)))}"
        )
    return out


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
        branch_flow=_branch_flow(net),
    )
