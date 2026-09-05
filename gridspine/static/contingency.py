"""Stage 2: full AC N-1 screening of a snapshot-applied net.

Branch outages run through lightsim2grid's ``ContingencyAnalysisCPP`` on a
GridModel built from the pandapower net — one batched solve for every branch,
matching pandapower to 1e-10 in |V| on the connected cases (probed). Unit
outages run through pandapower on a copy, because a generator is not a branch
id and its lost MW has to land on the slack. Both paths report ONE loading
definition, ``branch_loading_pct``: from/HV-side current over rating, the
side lightsim2grid returns. pandapower's own ``loading_percent`` is the max
of both ends, so ours is never above it and typically a few percent below;
the test pins that band by measurement.

ISLANDED is a topology fact, not a solver failure. When an outage splits the
grid lightsim2grid reports ``is_grid_connected_after_contingency == 0`` and
all-zero voltages; it does not solve the surviving island. Such a row is
``islanded=True, converged=False`` with the sentinel severity — never a
missing row — and the flag exists because these outages are identical in
every hour (11 of case39's 46: nine radial generator connections and two that
cut off the BUS_19/BUS_20 pocket) and a ranking that could not tell them from
a divergence would carry them as a constant floor.

Severity is defined ONCE, here, and the report quotes it:

    severity = sum over branches of max(0, loading/100 - 1)
             + sum over buses of max(0, (V_MIN - V)/0.1, (V - V_MAX)/0.1)

Dimensionless, zero without violations, monotone in overload depth and in
voltage excursion. Non-convergence and islanding take ``NON_CONVERGED_SEVERITY``.

THE stage-order guard: the net must already carry the hour — loads, every gen
setpoint, every RES output matching the tables — or the screen refuses. It
checks; it does not apply. A screen of case39's native peak labelled as some
other hour is the increment-1 defect in a new file.

The caller's net is never mutated: everything happens on a deep copy.

Allowed to import pandapower and lightsim2grid (``static/``); never pypsa.
"""
import copy

import numpy as np
import pandapower as pp
import pandas as pd
from lightsim2grid.contingencyAnalysis import ContingencyAnalysisCPP
from lightsim2grid.gridmodel import init_from_pandapower

from gridspine.schema.contingency import (
    NON_CONVERGED_SEVERITY,
    validate_contingency_results,
    validate_contingency_set,
)
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.loadflow import branch_keys
from gridspine.static.lodf import dc_base, dc_loading_pct, n2_dc_flows

V_MIN_PU = 0.9
V_MAX_PU = 1.1
V_BAND_PU = 0.1          # one excursion unit = 0.1 pu beyond a limit
LOADING_MAX_PCT = 100.0

_LS2G_MAX_ITER = 20
_LS2G_TOL = 1e-8
_P_TOL_MW = 1e-3

N1_LEDGER = (
    "N-1 branch outages solved by lightsim2grid ContingencyAnalysisCPP on a "
    "GridModel from the pandapower net, initialised at the base-case voltages; "
    "unit outages solved by pandapower with the unit out of service and the "
    "lost MW picked up by the slack",
    "the GridModel re-applies gen.in_service and sgen.in_service after "
    "init_from_pandapower: lightsim2grid 0.10.1's ext_grid slack adder "
    "re-initialises the generator container and drops the flags, which left "
    "every decommitted unit as a live PV bus in the branch and N-2 solves "
    "(found on the v3 year; hour 1803 base case 0.14 pu off); status vectors "
    "are checked against the net on every build",
    "branch loading is FROM/HV-side current over rating (lines: i_from / "
    "(max_i_ka x df x parallel); transformers: i_hv x sqrt(3) x vn_hv / "
    "(sn_mva x df x parallel)) — lightsim2grid returns the from side only, so "
    "this is never above pandapower's two-sided loading_percent and is up to "
    "~6 percentage points below it on case39 (measured 6.08 at hour 0)",
    "an outage that splits the grid is ISLANDED: recorded converged=False with "
    "the sentinel severity, the surviving island is not solved; on case39 that "
    "is 11 of 46 branch outages in every hour",
    f"violations: branch loading > {LOADING_MAX_PCT:g} %, bus voltage outside "
    f"[{V_MIN_PU}, {V_MAX_PU}] pu (assumed limits for the 39-bus case)",
    "severity = sum max(0, loading/100 - 1) + sum max(0, (Vmin - V)/0.1, "
    "(V - Vmax)/0.1); non-convergence and islanding carry NON_CONVERGED_SEVERITY",
)


def branch_loading_pct(net, i_from_ka, i_hv_ka) -> np.ndarray:
    """From/HV-side loading in percent, lines then transformers, in table order."""
    line, trafo = net.line, net.trafo
    l_df = line["df"].values if "df" in line.columns else 1.0
    l_par = line["parallel"].values if "parallel" in line.columns else 1.0
    line_pct = np.asarray(i_from_ka, dtype=float) / (line["max_i_ka"].values * l_df * l_par) * 100.0
    t_df = trafo["df"].values if "df" in trafo.columns else 1.0
    t_par = trafo["parallel"].values if "parallel" in trafo.columns else 1.0
    i_rated_hv = trafo["sn_mva"].values * t_df * t_par / (np.sqrt(3.0) * trafo["vn_hv_kv"].values)
    trafo_pct = np.asarray(i_hv_ka, dtype=float) / i_rated_hv * 100.0
    return np.concatenate([line_pct, trafo_pct])


def severity(loading_pct, vm_pu) -> float:
    ld = np.asarray(loading_pct, dtype=float)
    vm = np.asarray(vm_pu, dtype=float)
    over = np.clip(ld / LOADING_MAX_PCT - 1.0, 0.0, None)
    low = np.clip((V_MIN_PU - vm) / V_BAND_PU, 0.0, None)
    high = np.clip((vm - V_MAX_PU) / V_BAND_PU, 0.0, None)
    return float(np.nansum(over) + np.nansum(low) + np.nansum(high))


def _n_violations(loading_pct, vm_pu) -> int:
    ld = np.asarray(loading_pct, dtype=float)
    vm = np.asarray(vm_pu, dtype=float)
    return int(np.nansum(ld > LOADING_MAX_PCT) + np.nansum((vm < V_MIN_PU) | (vm > V_MAX_PU)))


def _check_net_carries_hour(net, dispatch, loads, hour, registry) -> None:
    at_hour = dispatch[dispatch["hour"] == hour].set_index("unit_id")
    load_rows = loads[loads["hour"] == hour]
    if at_hour.empty or load_rows.empty:
        raise ContractError(f"dispatch/loads tables have no rows for hour {hour}")
    net_load, table_load = float(net.load["p_mw"].sum()), float(load_rows["p_mw"].sum())
    if abs(net_load - table_load) > _P_TOL_MW:
        raise ContractError(
            f"net does not carry hour {hour}: net.load total {net_load:.3f} MW vs "
            f"loads table {table_load:.3f} MW — apply_snapshot first"
        )
    gen_idx = {net.gen.at[i, "name"]: i for i in net.gen.index}
    sgen = getattr(net, "sgen", None)
    sgen_idx = {sgen.at[i, "name"]: i for i in sgen.index} if sgen is not None else {}
    for unit_id, rec in registry.iterrows():
        if rec["kind"] == "gen":
            table, idx = net.gen, gen_idx.get(unit_id)
        elif rec["kind"] == "res":
            table, idx = sgen, sgen_idx.get(unit_id)
        else:
            continue
        if idx is None or unit_id not in at_hour.index:
            raise ContractError(f"unit {unit_id} missing from the net or the dispatch at hour {hour}")
        want, have = float(at_hour.at[unit_id, "p_mw"]), float(table.at[idx, "p_mw"])
        if abs(want - have) > _P_TOL_MW:
            raise ContractError(
                f"net does not carry hour {hour}: {unit_id} p_mw {have:.3f} on the net vs "
                f"{want:.3f} in the dispatch — apply_snapshot first"
            )



def gridmodel_for(work):
    """The one place a lightsim2grid GridModel is built from a solved pandapower net.

    lightsim2grid 0.10.1's `init_from_pandapower` applies `gen.in_service`, then
    — when the slack comes from `ext_grid` — its slack adder calls
    `init_generators` again over every pandapower gen plus the slack and never
    re-applies the flags: every out-of-service generator comes back as a live PV
    bus holding its setpoint. `sgen` is not re-initialised and keeps its flags.
    Re-apply both here and refuse a model whose status vectors disagree with the
    net, so the ranking, the screen and the prune measurement all solve the grid
    pandapower solved.
    """
    gm = init_from_pandapower(work)
    for i, on in enumerate(work.gen["in_service"].values):
        if not bool(on):
            gm.deactivate_gen(i)
    sgen = getattr(work, "sgen", None)
    if sgen is not None:
        for i, on in enumerate(sgen["in_service"].values):
            if not bool(on):
                gm.deactivate_sgen(i)
    n_gen = len(work.gen)
    gen_status = np.asarray(gm.get_gen_status(), dtype=bool)
    want = work.gen["in_service"].to_numpy(dtype=bool)
    if len(gen_status) < n_gen or not np.array_equal(gen_status[:n_gen], want) or not gen_status[n_gen:].all():
        raise ContractError(
            "lightsim2grid GridModel generator status does not follow pandapower gen.in_service "
            f"(model {gen_status.tolist()}, net {want.tolist()} + slack)"
        )
    if sgen is not None:
        sgen_status = np.asarray(gm.get_sgens_status(), dtype=bool)
        if not np.array_equal(sgen_status, sgen["in_service"].to_numpy(dtype=bool)):
            raise ContractError("lightsim2grid GridModel sgen status does not follow pandapower sgen.in_service")
    return gm

def _row(cid, hour, *, converged, islanded, loading=None, vm=None):
    if not converged:
        return {
            "contingency_id": cid, "hour": hour, "converged": False, "islanded": bool(islanded),
            "max_branch_loading_pct": np.nan, "min_vm_pu": np.nan, "max_vm_pu": np.nan,
            "n_violations": 0, "severity": NON_CONVERGED_SEVERITY,
        }
    return {
        "contingency_id": cid, "hour": hour, "converged": True, "islanded": False,
        "max_branch_loading_pct": float(np.nanmax(loading)),
        "min_vm_pu": float(np.nanmin(vm)), "max_vm_pu": float(np.nanmax(vm)),
        "n_violations": _n_violations(loading, vm), "severity": severity(loading, vm),
    }


def _screen_branches(work, branch_rows, hour, nl) -> dict:
    """One batched lightsim2grid solve; rows keyed by contingency_id."""
    keys = branch_keys(work)
    # branch_keys is lines then trafos in table order — exactly lightsim2grid's ids.
    ls2g_id = {(k.from_bus, k.to_bus, k.ckt): pos for pos, k in enumerate(keys.itertuples(index=False))}
    wanted = {}
    for r in branch_rows.itertuples(index=False):
        key = (r.from_bus, r.to_bus, str(r.ckt))
        if key not in ls2g_id:
            raise ContractError(
                f"contingency {r.contingency_id} names a branch not on the net: {key}"
            )
        wanted[r.contingency_id] = ls2g_id[key]
    if not wanted:
        return {}

    gm = gridmodel_for(work)
    ca = ContingencyAnalysisCPP(gm)
    ca.add_all_n1()   # row k of every result is branch id k; no insertion-order assumption
    ca.compute(work._ppc["internal"]["V"], _LS2G_MAX_ITER, _LS2G_TOL)
    ca.compute_flows()
    amps = np.asarray(ca.get_flows())
    volts = np.asarray(ca.get_voltages())
    connected = np.asarray(ca.is_grid_connected_after_contingency()).astype(bool)

    out = {}
    for cid, k in wanted.items():
        if not connected[k]:
            out[cid] = _row(cid, hour, converged=False, islanded=True)
            continue
        if abs(amps[k, k]) > 1e-6:
            raise ContractError(
                f"lightsim2grid N-1 row {k} carries current on branch {k}: result rows "
                "are not in branch-id order"
            )
        vm = np.abs(volts[k])
        if vm.size == 0 or np.nanmin(vm) <= 0.0:
            out[cid] = _row(cid, hour, converged=False, islanded=False)
            continue
        loading = branch_loading_pct(work, amps[k, :nl], amps[k, nl:])
        out[cid] = _row(cid, hour, converged=True, islanded=False, loading=loading, vm=vm)
    return out


def _screen_units(work, unit_rows, hour, registry) -> dict:
    """pandapower per unit outage on a fresh copy of the snapshot net."""
    gen_idx = {work.gen.at[i, "name"]: i for i in work.gen.index}
    sgen = getattr(work, "sgen", None)
    sgen_idx = {sgen.at[i, "name"]: i for i in sgen.index} if sgen is not None else {}
    out = {}
    for r in unit_rows.itertuples(index=False):
        uid = r.element_ids[0]
        if uid in gen_idx:
            table, idx = "gen", gen_idx[uid]
        elif uid in sgen_idx:
            table, idx = "sgen", sgen_idx[uid]
        else:
            raise ContractError(f"contingency {r.contingency_id} names a unit not on the net: {uid}")
        w = copy.deepcopy(work)
        getattr(w, table).at[idx, "in_service"] = False
        try:
            pp.runpp(w)
        except pp.LoadflowNotConverged:
            out[r.contingency_id] = _row(r.contingency_id, hour, converged=False, islanded=False)
            continue
        loading = branch_loading_pct(w, w.res_line["i_from_ka"].values, w.res_trafo["i_hv_ka"].values)
        out[r.contingency_id] = _row(
            r.contingency_id, hour, converged=True, islanded=False,
            loading=loading, vm=w.res_bus["vm_pu"].values,
        )
    return out


def screen_n1(net, contingencies, dispatch, loads, hour, registry) -> pd.DataFrame:
    """Validated results, one row per N-1 contingency, in the set's order."""
    cset = validate_contingency_set(contingencies)
    dispatch = validate_dispatch(dispatch)
    loads = validate_loads(loads)
    hour = int(hour)
    if (cset["order"] != 1).any():
        raise ContractError("screen_n1 takes an N-1 set; got rows with order != 1")
    _check_net_carries_hour(net, dispatch, loads, hour, registry)

    work = copy.deepcopy(net)
    try:
        pp.runpp(work)
    except pp.LoadflowNotConverged as exc:
        raise ContractError(f"base case at hour {hour} does not converge; nothing to screen") from exc
    nl = len(work.line)

    rows = {}
    rows.update(_screen_branches(work, cset[cset["kind"] == "branch"], hour, nl))
    rows.update(_screen_units(work, cset[cset["kind"] == "unit"], hour, registry))
    ordered = [rows[cid] for cid in cset["contingency_id"]]
    return validate_contingency_results(pd.DataFrame(ordered))


# ===========================================================================
# N-2: DC-LODF prune, AC verify the survivors
# ===========================================================================

def _branch_positions(state):
    """N-1 branch contingency id -> branch position, built (not parsed) from the keys."""
    keys = state.branch_keys
    return {
        f"{r.from_bus}-{r.to_bus}-{r.ckt}": i for i, r in enumerate(keys.itertuples(index=False))
    }


def _ac_pairs(work, pairs):
    """AC solve of the given (a, b) pairs, one analysis per pair on one GridModel.

    NOT batched on purpose. lightsim2grid deduplicates and reorders the
    contingencies it is given (probed: six requested pairs with one repeat came
    back as five rows, two of them not the pair at that position), so a batched
    result cannot be aligned to the request by insertion order. Solving one
    pair per analysis makes the alignment structural; the zero-current check on
    the outaged branches stays as the assertion that it holds.
    """
    n_branch = len(work.line) + len(work.trafo)
    n_bus = len(work.bus)
    amps = np.zeros((len(pairs), n_branch))
    volts = np.zeros((len(pairs), n_bus), dtype=complex)
    connected = np.zeros(len(pairs), dtype=bool)
    if not pairs:
        return amps, volts, connected
    gm = gridmodel_for(work)
    v0 = work._ppc["internal"]["V"]
    for i, (a, b) in enumerate(pairs):
        ca = ContingencyAnalysisCPP(gm)
        ca.add_nk([int(a), int(b)])
        ca.compute(v0, _LS2G_MAX_ITER, _LS2G_TOL)
        ca.compute_flows()
        amps[i] = np.asarray(ca.get_flows())[0]
        volts[i] = np.asarray(ca.get_voltages())[0]
        connected[i] = bool(np.asarray(ca.is_grid_connected_after_contingency())[0])
        if connected[i] and (abs(amps[i, a]) > 1e-6 or abs(amps[i, b]) > 1e-6):
            raise ContractError(
                f"lightsim2grid result for pair {(a, b)} carries current on an outaged "
                "branch; the solver did not apply the requested outage"
            )
    return amps, volts, connected


def _dc_estimate(state, base_over, a, b):
    """(islanded, dc_max_loading, dc_max_new_loading, dc_new_overloads)."""
    flows, islanded = n2_dc_flows(state, a, b)
    if islanded:
        return True, np.nan, np.nan, 0
    ld = dc_loading_pct(state, flows)
    fresh = ~base_over
    new_over = int(((ld > LOADING_MAX_PCT) & fresh).sum())
    max_new = float(ld[fresh].max()) if fresh.any() else 0.0
    return False, float(ld.max()), max_new, new_over


def _n2_prepare(net, candidates, dispatch, loads, hour, registry):
    cset = validate_contingency_set(candidates)
    dispatch = validate_dispatch(dispatch)
    loads = validate_loads(loads)
    hour = int(hour)
    if (cset["order"] != 2).any() or (cset["kind"] != "branch").any():
        raise ContractError("screen_n2 takes an N-2 branch set; got rows with order != 2 or kind != branch")
    _check_net_carries_hour(net, dispatch, loads, hour, registry)
    work = copy.deepcopy(net)
    try:
        pp.runpp(work)
    except pp.LoadflowNotConverged as exc:
        raise ContractError(f"base case at hour {hour} does not converge; nothing to screen") from exc
    state = dc_base(work)
    pos = _branch_positions(state)
    pairs = []
    for r in cset.itertuples(index=False):
        a, b = r.element_ids
        if a not in pos or b not in pos:
            raise ContractError(
                f"contingency {r.contingency_id} names a branch not on the net: "
                f"{[e for e in (a, b) if e not in pos]}"
            )
        pairs.append((pos[a], pos[b]))
    base_over = dc_loading_pct(state, state.flows_mw) > LOADING_MAX_PCT
    return cset, work, state, pairs, base_over, hour


def screen_n2(net, candidates, dispatch, loads, hour, registry, prune_threshold_pct):
    """(results, prune_log). Results hold AC-verified and islanded pairs only;
    pruned pairs appear in the log with their DC estimate and nowhere else."""
    cset, work, state, pairs, base_over, hour = _n2_prepare(net, candidates, dispatch, loads, hour, registry)
    nl = len(work.line)

    log_rows, to_verify = [], []
    for cid, (a, b) in zip(cset["contingency_id"], pairs):
        islanded, mx, mx_new, new_over = _dc_estimate(state, base_over, a, b)
        if islanded:
            decision = "islanded"
        elif mx_new >= prune_threshold_pct:
            decision = "verified"
            to_verify.append((cid, a, b))
        else:
            decision = "pruned"
        log_rows.append({
            "contingency_id": cid, "decision": decision,
            "dc_max_loading_pct": mx, "dc_max_new_loading_pct": mx_new, "dc_new_overloads": new_over,
        })
    log = pd.DataFrame(log_rows)

    amps, volts, connected = _ac_pairs(work, [(a, b) for _c, a, b in to_verify])
    rows = {}
    for i, (cid, a, b) in enumerate(to_verify):
        if not connected[i]:
            rows[cid] = _row(cid, hour, converged=False, islanded=True)
            continue
        vm = np.abs(volts[i])
        if vm.size == 0 or np.nanmin(vm) <= 0.0:
            rows[cid] = _row(cid, hour, converged=False, islanded=False)
            continue
        loading = branch_loading_pct(work, amps[i, :nl], amps[i, nl:])
        rows[cid] = _row(cid, hour, converged=True, islanded=False, loading=loading, vm=vm)
    for cid, decision in zip(log["contingency_id"], log["decision"]):
        if decision == "islanded":
            rows[cid] = _row(cid, hour, converged=False, islanded=True)

    ordered = [rows[cid] for cid in cset["contingency_id"] if cid in rows]
    results = validate_contingency_results(pd.DataFrame(ordered))
    return results, log


def measure_prune_threshold(net, candidates, dispatch, loads, hour, registry):
    """The plan's Step 5 as code: full AC N-2 as ground truth, threshold = the
    largest value that keeps every pair creating a violation the base case did
    not already have. Returns (threshold, report)."""
    cset, work, state, pairs, base_over, hour = _n2_prepare(net, candidates, dispatch, loads, hour, registry)
    nl = len(work.line)
    base_ld = branch_loading_pct(work, work.res_line["i_from_ka"].values, work.res_trafo["i_hv_ka"].values)
    base_vm = work.res_bus["vm_pu"].values
    base_branch_viol = base_ld > LOADING_MAX_PCT
    base_bus_viol = (base_vm < V_MIN_PU) | (base_vm > V_MAX_PU)

    estimates = {cid: _dc_estimate(state, base_over, a, b) for cid, (a, b) in zip(cset["contingency_id"], pairs)}
    live = [(cid, a, b) for cid, (a, b) in zip(cset["contingency_id"], pairs) if not estimates[cid][0]]
    amps, volts, connected = _ac_pairs(work, [(a, b) for _c, a, b in live])

    flagged, by_voltage_only, n_connected = [], [], 0
    for i, (cid, a, b) in enumerate(live):
        if not connected[i]:
            continue
        vm = np.abs(volts[i])
        if vm.size == 0 or np.nanmin(vm) <= 0.0:
            continue
        n_connected += 1
        ld = branch_loading_pct(work, amps[i, :nl], amps[i, nl:])
        new_branch = ((ld > LOADING_MAX_PCT) & ~base_branch_viol).any()
        new_bus = (((vm < V_MIN_PU) | (vm > V_MAX_PU)) & ~base_bus_viol).any()
        if new_branch or new_bus:
            flagged.append(cid)
            if new_bus and not new_branch:
                by_voltage_only.append(cid)

    if flagged:
        threshold = float(min(estimates[cid][2] for cid in flagged))
    else:
        threshold = 0.0
    report = {
        "ac_pairs_connected": n_connected,
        "ac_pairs_with_new_violation": len(flagged),
        "new_violation_pairs": flagged,
        "flagged_by_voltage_only": by_voltage_only,
        "min_dc_loading_among_flagged": threshold,
        "base_case_overloaded_branches": int(base_branch_viol.sum()),
    }
    return threshold, report
