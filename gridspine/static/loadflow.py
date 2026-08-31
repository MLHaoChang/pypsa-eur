"""Stage 2 (minimal): snapshot AC load flow. Dispatch arrives as the
validated table; results leave as plain frames keyed by canonical names."""
from dataclasses import dataclass, field

import pandapower as pp
import pandas as pd

from gridspine.schema.contracts import ContractError

#: Column contract of ``LFResult.branch_flow``. The first three are the join
#: key the PowerFactory read-back compares on; see ``_branch_flow`` for why
#: that triple is authored the way it is.
#:
#: Deliberately the same six names, in the same order, as
#: ``gridspine.readback.pf_compare.BRANCH_CSV_COLUMNS`` and as the export
#: header in tests/gridspine/fixtures/powerfactory/README.md — the operator's
#: CSV is meant to drop straight onto this frame. Change one, change all three.
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
    """Set the committable generators from the dispatch table.

    DEPRECATED for driver use — call ``apply_snapshot`` instead. This function
    sets generation and nothing else, so on its own it reproduces the
    increment-1 defect: `net.load` keeps whatever level the source case carried
    (case39's is the hour-19 peak) while the machines move to the requested
    hour, and the slack quietly imports the difference. Kept because it is the
    generator half of ``apply_snapshot`` and several tests drive it directly.
    """
    snap = table[table["hour"] == hour].set_index("unit_id")
    name_to_gen_idx = {net.gen.at[i, "name"]: i for i in net.gen.index}
    for unit_id, rec in registry.iterrows():
        if rec["kind"] != "gen":
            continue  # ext_grid is the slack; it absorbs the residual
        row = snap.loc[unit_id]
        i = name_to_gen_idx[unit_id]
        net.gen.at[i, "p_mw"] = float(row["p_mw"])
        net.gen.at[i, "in_service"] = bool(int(row["status"]))



def _apply_loads(net, loads, hour) -> None:
    """Set `net.load` p/q for `hour` from the validated loads table.

    Both directions of the bus correspondence are checked, and both are the
    same defect wearing different clothes: a `net.load` row the table does not
    cover keeps its NATIVE value, which is exactly the increment-1 residual the
    loads artifact exists to remove, and it would go unnoticed because the flow
    still converges. Failing closed is the point.

    The table is per BUS; `net.load` is per ROW, and a bus may in principle
    carry several load rows (case39 carries exactly one each). The bus total is
    split across its rows in proportion to their native P, so a multi-row bus
    keeps its internal composition. When a bus's native P is zero there is no
    proportion to preserve and the split is equal — an arbitrary but harmless
    choice, since the rows sum to the same bus total either way.
    """
    from gridspine.schema.dispatch import validate_loads

    tbl = validate_loads(loads)
    at_hour = tbl[tbl["hour"] == hour]
    if at_hour.empty:
        raise ContractError(
            f"loads table has no rows for hour {hour}; "
            f"it covers {sorted(tbl['hour'].unique().tolist())[:5]}..."
        )
    target = at_hour.set_index("bus")

    bus_name = net.bus["name"]
    rows_by_bus = {}
    for i in net.load.index:
        rows_by_bus.setdefault(bus_name.at[net.load.at[i, "bus"]], []).append(i)

    uncovered = sorted(set(rows_by_bus) - set(target.index))
    if uncovered:
        raise ContractError(
            f"loads table does not cover net.load buses {uncovered} at hour "
            f"{hour}; leaving them at their native level is the increment-1 "
            "silent-import defect"
        )
    unknown = sorted(set(target.index) - set(rows_by_bus))
    if unknown:
        raise ContractError(
            f"loads table names buses with no net.load row: {unknown}"
        )

    for bus, idxs in rows_by_bus.items():
        p_total = float(target.at[bus, "p_mw"])
        q_total = float(target.at[bus, "q_mvar"])
        native = [float(net.load.at[i, "p_mw"]) for i in idxs]
        denom = sum(native)
        weights = (
            [v / denom for v in native] if denom != 0.0
            else [1.0 / len(idxs)] * len(idxs)
        )
        for i, w in zip(idxs, weights):
            net.load.at[i, "p_mw"] = p_total * w
            net.load.at[i, "q_mvar"] = q_total * w


def _apply_res(net, snap, registry) -> None:
    """Set `net.sgen` p_mw and in_service for the registry's `kind == 'res'` rows.

    A curtailed RES row arrives as status 0 / p_mw 0 (the producer zeroes both
    together), and it is set OUT OF SERVICE here. That is correct FOR LOAD
    FLOW: a zero-injection PQ element and an absent one are the same node
    equation, and taking it out keeps the RAW writer's STAT field agreeing with
    the snapshot being studied.

    INCREMENT-3 WARNING — do not reuse this mapping for the short-circuit
    stage. Curtailment is a control state, not a disconnection: a curtailed
    inverter is still energised, still synchronised, and still contributes
    fault current. `in_service=False` deletes it from the fault calculation
    entirely, which would understate the contribution at exactly the buses the
    study is about. Increment 3 needs its own status -> element mapping.
    """
    sgen = getattr(net, "sgen", None)
    if sgen is None or len(sgen) == 0:
        return
    idx_of = {sgen.at[i, "name"]: i for i in sgen.index}
    for unit_id, rec in registry.iterrows():
        if rec["kind"] != "res":
            continue
        if unit_id not in idx_of:
            raise ContractError(f"registry names a res unit with no sgen row: {unit_id}")
        row = snap.loc[unit_id]
        i = idx_of[unit_id]
        net.sgen.at[i, "p_mw"] = float(row["p_mw"])
        net.sgen.at[i, "in_service"] = bool(int(row["status"]))


def apply_snapshot(net, dispatch, loads, hour, registry) -> None:
    """Put the whole snapshot on the net: demand, commitment and RES output.

    This is the function the driver calls. `apply_dispatch` moved the machines
    and left the demand behind, so every non-peak hour converged by importing
    the residual through the slack — which is why increment 1 refused them.
    With the loads table applied alongside, any hour is load-consistent and the
    slack carries losses only; `tests/gridspine/test_loads_artifact.py::
    test_driver_slack_no_longer_imports_the_load_residual` is the assertion
    that holds that claim up.

    Order is deliberate: loads first, so that a table that fails its contract
    aborts before any generator has been touched and the net is left as it was
    found rather than half-updated.
    """
    _apply_loads(net, loads, hour)
    apply_dispatch(net, dispatch, hour, registry)
    _apply_res(net, dispatch[dispatch["hour"] == hour].set_index("unit_id"), registry)


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
    # must still get a row here or the key sets stop matching.
    #
    # An earlier version of this comment claimed pandapower DROPS them from
    # res_*. It does not. Probed on pandapower 3.1.2 with one line and one
    # transformer forced out of service: res_line/res_trafo keep full-length
    # indexes equal to net.line/net.trafo, and the out-of-service rows carry
    # p/q == 0.0 — NOT NaN, and not absent. (Asymmetry worth knowing: the
    # dead line's loading_percent is 0.0 while the dead transformer's is
    # NaN.) The reindex below is therefore a NO-OP on this version; it is
    # kept only as a cheap alignment assertion so that a future pandapower
    # which does drop rows degrades to NaN flows rather than silently
    # shortening the frame and breaking the key contract.
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
    """Solve the snapshot and package the results.

    Non-convergence is a RESULT, not an error: it comes back as
    ``LFResult(converged=False)`` with empty frames. The one case that does
    raise is ``ContractError``, from ``_branch_flow``, when a line and a
    transformer share a bus pair and therefore collide on
    (from_bus, to_bus, ckt) — see ``_branch_flow`` for why that is ambiguous
    rather than merely unusual.
    """
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
