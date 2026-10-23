"""Planning-criteria contingencies: emergency ratings and the two-stage check.

Planning criteria of the SAGC type go past plain N-1 on normal ratings: right
after an outage a branch may run up to its EMERGENCY rating, and once the
automatic controls have acted it must be back within its NORMAL rating. This
module judges each contingency of a set (branch, unit, bus section) in those
two stages, on one hour already on the net.

**Emergency ratings** are optional per element, as net columns beside the
normal ones: ``line.max_i_ka_emergency`` (kA) and ``trafo.sn_mva_emergency``
(MVA). Absent or NaN means none is given, and stage 1 then uses the normal
rating, which is conservative; the row says how many branches had none.
``validate_emergency_ratings`` refuses a figure below the normal one, at or
below zero, or present but not finite. Loading is ``branch_loading_pct``'s
from/HV-side definition with the emergency figure in place of the normal one
(``emergency_loading_pct``), ``df`` and ``parallel`` kept.

**Stage 1**, right after the outage: taps and shunts as they were, thermal
only, every in-service branch against its emergency rating.

**Stage 2**, after the remedial controls the caller names (``Controls``):
on-load tap changers and switched shunts on automatic control. They walk
(``remedial_walk``) one step at a time from where they were: each pass tries
+-1 step on every named control in sorted order and keeps the best move by
``campus_taps._better`` on ``(voltage excursion, worst overload above normal,
steps moved)``; a move that does not converge is rejected, and the walk
stops when no move improves or after ``TAP_ROUNDS`` x the total step range.
The net is then judged on NORMAL ratings and the voltage band. No redispatch
is modelled (owner decision DC13-SCOPE), and every stage-2 row says so.

**Bus sections** (``contingency_set.bus_section_contingencies``): the bus and
everything on it go out. The load and generation lost are reported, on the
section and on any bus left unsupplied, so a bus loss cannot read benign
because its load vanished; the lost generation is picked up by the slack.

An outage that leaves any other bus unsupplied is ISLANDED, as in
``static/contingency.py``: not solved, both stages ``not_solved``, and the
note names the buses cut off.

THE stage-order guard is ``check_net_carries_hour``, as in ``screen_n1``. The
caller's net is never mutated: everything happens on deep copies.

Allowed to import pandapower (``static/``); never pypsa.
"""
import copy
import dataclasses
import math
import time

import numpy as np
import pandapower as pp
import pandas as pd
from pandapower.topology import unsupplied_buses

from gridspine.schema.contingency import (
    PLANNING_RESULT_COLUMNS,
    validate_contingency_set,
    validate_planning_results,
)
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.campus_taps import STEP_TOL, TAP_ROUNDS, V_TOL, _better, excursion
from gridspine.static.contingency import (
    LOADING_MAX_PCT,
    V_MAX_PU,
    V_MIN_PU,
    branch_loading_pct,
)
from gridspine.static.contingency_set import branch_contingencies
from gridspine.static.loadflow import branch_keys, check_net_carries_hour

LINE_EMERGENCY = "max_i_ka_emergency"
TRAFO_EMERGENCY = "sn_mva_emergency"

NO_REDISPATCH = "no redispatch modelled"
NO_CONTROLS = "no remedial controls named; " + NO_REDISPATCH

PLANNING_LEDGER = (
    "stage 1 judges thermal loading only, right after the outage with taps and "
    "shunts as they were, against each branch's emergency rating; where none "
    "is given, against its normal rating (conservative)",
    "loading is the from/HV-side definition of static/contingency.py; the "
    "emergency figure replaces the normal one, df and parallel kept",
    f"stage 2 judges loading against the normal rating and bus voltages against "
    f"[{V_MIN_PU}, {V_MAX_PU}] pu (the assumed band of static/contingency.py), "
    "after the named on-load tap changers and switched shunts walk one step at "
    "a time to lower (voltage excursion, worst overload, steps moved)",
    "stage 2 models no redispatch (owner decision DC13-SCOPE): a branch above "
    "its normal rating that taps and shunts cannot relieve fails stage 2",
    "a bus-section loss takes out the bus and everything on it; the load and "
    "generation lost are reported, and the lost generation is picked up by "
    "the slack",
    "an outage that leaves any other bus unsupplied is islanded: not solved, "
    "both stages not_solved",
)


@dataclasses.dataclass(frozen=True)
class Controls:
    """The remedial controls stage 2 may move: on-load tap changers by their
    branch id (``"{hv_bus}-{lv_bus}-{ckt}"``, as ``branch_contingencies``
    names them) and switched shunts by ``net.shunt`` name."""
    oltc: tuple = ()
    switched_shunts: tuple = ()


# ---------------------------------------------------------------------------
# emergency ratings
# ---------------------------------------------------------------------------

def _column(table, col) -> np.ndarray:
    if col not in table.columns:
        return np.full(len(table), np.nan)
    return pd.to_numeric(table[col], errors="coerce").to_numpy(dtype=float)


def validate_emergency_ratings(net) -> None:
    """Refuse an emergency rating below the normal one, at or below zero, or
    present but not finite; equal to the normal rating is allowed."""
    ids = branch_keys(net)
    names = [f"{f}-{t}-{c}" for f, t, c in zip(ids["from_bus"], ids["to_bus"], ids["ckt"])]
    nl = len(net.line)
    for table, col, normal, offset in ((net.line, LINE_EMERGENCY, "max_i_ka", 0),
                                       (net.trafo, TRAFO_EMERGENCY, "sn_mva", nl)):
        if col not in table.columns:
            continue
        raw = table[col].tolist()
        norm = table[normal].to_numpy(dtype=float)
        for pos, v in enumerate(raw):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            what = f"{names[offset + pos]} ({col} = {v!r})"
            if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)) \
                    or not math.isfinite(float(v)):
                raise ContractError(f"emergency rating must be a finite number: {what}")
            if float(v) <= 0.0:
                raise ContractError(f"emergency rating must be > 0: {what}")
            if float(v) < norm[pos]:
                raise ContractError(f"emergency rating below the normal rating {norm[pos]:g}: {what}")


def emergency_loading_pct(net, i_from_ka, i_hv_ka) -> np.ndarray:
    """``branch_loading_pct`` against each branch's emergency rating, its
    normal rating where none is given: lines i_from / (max_i_ka_emergency x
    df x parallel), transformers i_hv x sqrt(3) x vn_hv / (sn_mva_emergency
    x df x parallel)."""
    normal = branch_loading_pct(net, i_from_ka, i_hv_ka)
    rated = np.concatenate([net.line["max_i_ka"].to_numpy(dtype=float),
                            net.trafo["sn_mva"].to_numpy(dtype=float)])
    emerg = np.concatenate([_column(net.line, LINE_EMERGENCY), _column(net.trafo, TRAFO_EMERGENCY)])
    return normal * rated / np.where(np.isnan(emerg), rated, emerg)


def _missing_emergency(net) -> tuple:
    """(branches in service without an emergency rating, branches in service)."""
    on = np.concatenate([net.line["in_service"].to_numpy(dtype=bool),
                         net.trafo["in_service"].to_numpy(dtype=bool)])
    emerg = np.concatenate([_column(net.line, LINE_EMERGENCY), _column(net.trafo, TRAFO_EMERGENCY)])
    return int((on & np.isnan(emerg)).sum()), int(on.sum())


# ---------------------------------------------------------------------------
# solving one state
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _State:
    loading: np.ndarray        # normal-rating loading, %, per branch (NaN out of service)
    emergency: np.ndarray      # emergency-rating loading, %
    vm: dict                   # {bus name: vm_pu} of the buses judged


def _solve(net, judged) -> _State | None:
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        return None
    i_from = net.res_line["i_from_ka"].to_numpy(dtype=float)
    i_hv = net.res_trafo["i_hv_ka"].to_numpy(dtype=float)
    on = np.concatenate([net.line["in_service"].to_numpy(dtype=bool),
                         net.trafo["in_service"].to_numpy(dtype=bool)])
    loading = np.where(on, branch_loading_pct(net, i_from, i_hv), np.nan)
    emergency = np.where(on, emergency_loading_pct(net, i_from, i_hv), np.nan)
    vm = {net.bus.at[b, "name"]: float(net.res_bus.at[b, "vm_pu"]) for b in judged}
    if any(not math.isfinite(v) or v <= 0.0 for v in vm.values()):
        return None
    return _State(loading, emergency, vm)


def _worst(values: np.ndarray, ids: list):
    if np.isnan(values).all():
        return 0.0, ""
    k = int(np.nanargmax(values))
    return float(values[k]), ids[k]


# ---------------------------------------------------------------------------
# stage 2: the remedial walk
# ---------------------------------------------------------------------------

def _knobs(work, controls: Controls, trafo_pos: dict) -> list:
    """``[(name, table, index, column, lo, hi)]`` in sorted name order."""
    knobs = []
    for name in sorted(controls.oltc):
        if name not in trafo_pos:
            raise ContractError(f"OLTC {name!r} is not a transformer on the net")
        i = trafo_pos[name]
        lo, hi, pos = (work.trafo.at[i, c] for c in ("tap_min", "tap_max", "tap_pos"))
        if not all(isinstance(v, (int, float, np.integer, np.floating)) and math.isfinite(float(v))
                   for v in (lo, hi, pos)):
            raise ContractError(f"OLTC {name!r} has no tap range (tap_min, tap_max, tap_pos)")
        if work.trafo.get("tap_changer_type", pd.Series(dtype=object)).get(i) != "Ratio":
            # pandapower 3 leaves a tap without a changer type inert (raw_writer reads it the same way)
            raise ContractError(f"OLTC {name!r} has no tap_changer_type 'Ratio': its taps would not act")
        knobs.append((name, "trafo", i, "tap_pos", int(lo), int(hi)))
    shunt_idx = {str(work.shunt.at[i, "name"]): i for i in work.shunt.index}
    for name in sorted(controls.switched_shunts):
        if name not in shunt_idx:
            raise ContractError(f"switched shunt {name!r} is not on the net")
        i = shunt_idx[name]
        knobs.append((name, "shunt", i, "step", 0, int(work.shunt.at[i, "max_step"])))
    return knobs


def _key(state: _State, bands: dict, moved: int):
    over = max(0.0, float(np.nanmax(state.loading, initial=0.0)) - LOADING_MAX_PCT)
    return (excursion(state.vm, bands), over, moved)


def remedial_walk(work, knobs: list, bands: dict, judged, state: _State):
    """Walk the knobs on ``work`` (module docstring) from ``state``, the
    solved post-outage state. Returns ``(state, moves)``: the last converged
    state, ``work`` left at its positions, and ``{name: steps moved}``."""
    start = {k[0]: int(getattr(work, k[1]).at[k[2], k[3]]) for k in knobs}
    pos = dict(start)
    on = [k for k in knobs if bool(getattr(work, k[1]).at[k[2], "in_service"])]
    cap = TAP_ROUNDS * sum(k[5] - k[4] for k in on)
    now = _key(state, bands, 0)
    for _ in range(cap):
        if now[0] <= V_TOL and now[1] <= STEP_TOL:
            break
        best = None
        for name, tbl, i, col, lo, hi in on:
            for d in (+1, -1):
                p = pos[name] + d
                if not lo <= p <= hi:
                    continue
                getattr(work, tbl).at[i, col] = p
                trial = _solve(work, judged)
                getattr(work, tbl).at[i, col] = pos[name]
                if trial is None:
                    continue
                moved = sum(abs(pos[n] - start[n]) for n in pos if n != name) + abs(p - start[name])
                k = _key(trial, bands, moved)
                if best is None or _better(k, best[0]):
                    best = (k, name, tbl, i, col, p, trial)
        if best is None or not _better(best[0], now):
            break
        now, name, tbl, i, col, p, state = best
        getattr(work, tbl).at[i, col] = p
        pos[name] = p
    return state, {n: pos[n] - start[n] for n in pos if pos[n] != start[n]}


# ---------------------------------------------------------------------------
# the screen
# ---------------------------------------------------------------------------

def _apply_outage(work, r, handle, gen_idx, sgen_idx, bus_idx) -> None:
    if r.kind == "branch":
        for e in r.element_ids:
            if e not in handle:
                raise ContractError(f"contingency {r.contingency_id} names a branch not on the net: {e}")
            etype, eidx = handle[e]
            getattr(work, etype).at[eidx, "in_service"] = False
    elif r.kind == "unit":
        uid = r.element_ids[0]
        if uid in gen_idx:
            work.gen.at[gen_idx[uid], "in_service"] = False
        elif uid in sgen_idx:
            work.sgen.at[sgen_idx[uid], "in_service"] = False
        else:
            raise ContractError(f"contingency {r.contingency_id} names a unit not on the net: {uid}")
    elif r.kind == "bus_section":
        bus = r.element_ids[0]
        if bus not in bus_idx:
            raise ContractError(f"contingency {r.contingency_id} names a bus not on the net: {bus}")
        b = bus_idx[bus]
        work.bus.at[b, "in_service"] = False
        for tbl, cols in (("line", ("from_bus", "to_bus")), ("trafo", ("hv_bus", "lv_bus")),
                          ("gen", ("bus",)), ("sgen", ("bus",)), ("load", ("bus",)), ("shunt", ("bus",))):
            t = getattr(work, tbl)
            hit = np.zeros(len(t), dtype=bool)
            for c in cols:
                hit |= (t[c] == b).to_numpy()
            t.loc[hit, "in_service"] = False
    else:
        raise ContractError(f"contingency {r.contingency_id}: kind {r.kind!r} is not judged here")


def _p_on(table, buses) -> float:
    on = table["in_service"].astype(bool) & table["bus"].isin(buses)
    return float(table.loc[on, "p_mw"].sum())


def _lost(base, work, gone) -> tuple:
    """(load MW, generation MW) on ``gone`` buses plus units the outage took off."""
    load = _p_on(base.load, gone)
    gen = 0.0
    for tbl in ("gen", "sgen"):
        b, w = getattr(base, tbl), getattr(work, tbl)
        off = b["in_service"].astype(bool) & (~w["in_service"].astype(bool) | b["bus"].isin(gone))
        gen += float(b.loc[off, "p_mw"].sum())
    return load, gen


def _row(r, hour, **kw):
    row = {
        "contingency_id": r.contingency_id, "kind": r.kind, "hour": hour,
        "converged": False, "islanded": False, "load_lost_mw": 0.0, "gen_lost_mw": 0.0,
        "stage1_loading_pct": np.nan, "stage1_branch": "", "stage1_verdict": "not_solved",
        "stage2_loading_pct": np.nan, "stage2_branch": "", "stage2_min_vm_pu": np.nan,
        "stage2_max_vm_pu": np.nan, "stage2_verdict": "not_solved", "remedial": "", "note": "",
    }
    row.update(kw)
    return row


def screen_planning(net, contingencies, dispatch, loads, hour, registry, *, controls: Controls | None = None):
    """``(results, report)``: one ``validate_planning_results`` row per
    contingency in the set's order, and ``{"counts": {kind: n}, "seconds"}``.
    Kinds judged: branch (any order), unit, bus_section."""
    cset = validate_contingency_set(contingencies)
    dispatch = validate_dispatch(dispatch)
    loads = validate_loads(loads)
    hour = int(hour)
    controls = controls or Controls()
    check_net_carries_hour(net, dispatch, loads, hour, registry)
    validate_emergency_ratings(net)
    t0 = time.perf_counter()

    base = copy.deepcopy(net)
    try:
        pp.runpp(base)
    except pp.LoadflowNotConverged as exc:
        raise ContractError(f"base case at hour {hour} does not converge; nothing to screen") from exc
    keys = branch_keys(base)
    ids = [f"{f}-{t}-{c}" for f, t, c in zip(keys["from_bus"], keys["to_bus"], keys["ckt"])]
    n1 = branch_contingencies(base)
    handle = {c: (t, int(i)) for c, t, i in zip(n1["contingency_id"], n1["element_type"], n1["element_index"])}
    trafo_pos = {c: int(i) for c, t, i in zip(ids, keys["element_type"], keys["element_index"]) if t == "trafo"}
    gen_idx = {base.gen.at[i, "name"]: i for i in base.gen.index}
    sgen_idx = {base.sgen.at[i, "name"]: i for i in base.sgen.index}
    bus_idx = {base.bus.at[i, "name"]: i for i in base.bus.index}
    knobs = _knobs(base, controls, trafo_pos)
    k_missing, n_branches = _missing_emergency(base)
    rating_note = f"no emergency rating given ({k_missing} of {n_branches} branches)" if k_missing else ""
    stage2_note = NO_REDISPATCH if knobs else NO_CONTROLS
    cut_before = set(unsupplied_buses(base))
    out_before = set(base.bus.index[~base.bus["in_service"].astype(bool)])

    rows = []
    for r in cset.itertuples(index=False):
        work = copy.deepcopy(base)
        _apply_outage(work, r, handle, gen_idx, sgen_idx, bus_idx)
        out_bus = set(work.bus.index[~work.bus["in_service"].astype(bool)]) - out_before
        cut = set(unsupplied_buses(work)) - cut_before - out_bus - out_before
        load_lost, gen_lost = _lost(base, work, out_bus | cut)
        lost = {"load_lost_mw": load_lost, "gen_lost_mw": gen_lost}
        if cut:
            names = ", ".join(sorted(str(base.bus.at[b, "name"]) for b in cut))
            rows.append(_row(r, hour, islanded=True, note=f"islanded: disconnects {names}", **lost))
            continue
        judged = [b for b in work.bus.index if bool(work.bus.at[b, "in_service"]) and b not in cut_before]
        bands = {str(work.bus.at[b, "name"]): (V_MIN_PU, V_MAX_PU) for b in judged}
        s1 = _solve(work, judged)
        if s1 is None:
            rows.append(_row(r, hour, note="post-outage load flow does not converge", **lost))
            continue
        s1_pct, s1_branch = _worst(s1.emergency, ids)
        s2, moves = remedial_walk(work, knobs, bands, judged, s1)
        notes = [n for n in (rating_note, stage2_note) if n]
        if moves:
            # the walk keeps converged states only; re-solve the chosen positions as the result
            final = _solve(work, judged)
            if final is None:
                notes.append(f"stage 2 does not converge at the chosen controls; last converged state "
                             f"{_worst(s2.loading, ids)[0]:.1f} % ({_worst(s2.loading, ids)[1]}), "
                             f"{min(s2.vm.values()):.3f}-{max(s2.vm.values()):.3f} pu")
                rows.append(_row(r, hour, converged=True, stage1_loading_pct=s1_pct, stage1_branch=s1_branch,
                                 stage1_verdict="pass" if s1_pct <= LOADING_MAX_PCT else "fail",
                                 remedial=_said(moves), note="; ".join(notes), **lost))
                continue
            s2 = final
        s2_pct, s2_branch = _worst(s2.loading, ids)
        ok2 = s2_pct <= LOADING_MAX_PCT and excursion(s2.vm, bands) <= V_TOL
        rows.append(_row(
            r, hour, converged=True,
            stage1_loading_pct=s1_pct, stage1_branch=s1_branch,
            stage1_verdict="pass" if s1_pct <= LOADING_MAX_PCT else "fail",
            stage2_loading_pct=s2_pct, stage2_branch=s2_branch,
            stage2_min_vm_pu=min(s2.vm.values()), stage2_max_vm_pu=max(s2.vm.values()),
            stage2_verdict="pass" if ok2 else "fail",
            remedial=_said(moves), note="; ".join(notes), **lost,
        ))
    results = validate_planning_results(pd.DataFrame(rows, columns=list(PLANNING_RESULT_COLUMNS)))
    report = {
        "counts": {k: int(n) for k, n in cset["kind"].value_counts().sort_index().items()},
        "seconds": round(time.perf_counter() - t0, 3),
    }
    return results, report


def _said(moves: dict) -> str:
    return ", ".join(f"{n} {d:+d}" for n, d in sorted(moves.items())) if moves else "none"
