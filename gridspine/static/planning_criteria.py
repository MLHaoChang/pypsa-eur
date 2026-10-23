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

**N-M-1 and N-1-1** (``contingency_set.n_m_1_contingencies`` /
``n_1_1_contingencies``): the first branch goes out, the named controls walk
to re-adjust, then the second goes out and the two stages are judged as for
any outage. In this static model the two kinds share these mechanics; they
differ only in which branches may go out first. ``max_compound`` caps each
compound kind to its first rows in id order; the report counts what it
skipped, and a kind with skipped rows never summarises as ``pass``.

**Angle limit** (``AngleLimit``, an argument, not a grid-code key): the
largest voltage-angle difference across each named branch (its two
terminals, the outaged branch included: the standing angle across its open
breaker) or named bus pair, on the stage-1 state. No limit gives
``unknown``, never ``pass``.

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
    COMPOUND_SEPARATORS,
    PLANNING_RESULT_COLUMNS,
    validate_contingency_set,
    validate_planning_results,
)
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.campus_taps import TAP_ROUNDS, V_TOL, _better, excursion
from gridspine.static.contingency import (
    LOADING_MAX_PCT,
    V_MAX_PU,
    V_MIN_PU,
    BaseCaseNotConverged,
    branch_loading_pct,
)
from gridspine.static.contingency_set import branch_contingencies
from gridspine.static.loadflow import branch_keys, check_net_carries_hour

LINE_EMERGENCY = "max_i_ka_emergency"
TRAFO_EMERGENCY = "sn_mva_emergency"

NO_REDISPATCH = "no redispatch modelled"
NO_CONTROLS = "no remedial controls named; " + NO_REDISPATCH
NO_CONTROLS_ON = "no named remedial control in service; " + NO_REDISPATCH

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
    "stage 2 models no redispatch: a branch above "
    "its normal rating that taps and shunts cannot relieve fails stage 2",
    "a bus-section loss takes out the bus and everything on it; the load and "
    "generation lost (p_mw x scaling) are reported, and the lost generation is "
    "picked up by the slack",
    "an outage that leaves any other bus unsupplied is islanded: not solved, "
    "both stages not_solved",
    "N-M-1 and N-1-1 share mechanics in this static model: the first branch "
    "goes out, the named controls re-adjust, then the second goes out; they "
    "differ only in which branches may go out first (a maintenance list or any)",
    "max_compound keeps the first rows of each compound kind in contingency_id "
    "order; the rest are skipped, counted in the report, and the kind's summary "
    "is then incomplete, never pass",
    "the angle check is judged on the stage-1 state (taps and shunts barely "
    "move angles); a pair with a bus out of service is skipped and named in "
    "the note; no limit set gives unknown, never pass",
)

ANGLE_BASES = frozenset({"assumed", "sagc"})


@dataclasses.dataclass(frozen=True)
class Controls:
    """The remedial controls stage 2 may move: on-load tap changers by their
    branch id (``"{hv_bus}-{lv_bus}-{ckt}"``, as ``branch_contingencies``
    names them) and switched shunts by ``net.shunt`` name."""
    oltc: tuple = ()
    switched_shunts: tuple = ()


@dataclasses.dataclass(frozen=True)
class AngleLimit:
    """The voltage-angle limit across a branch or bus pair, in degrees.
    ``basis`` is ``"assumed"`` or ``"sagc"``; ``"sagc"`` names the public
    SAGC clause in ``reference``. ``pairs`` holds branch ids and (bus, bus)
    name pairs; empty means every branch on the net."""
    deg: float
    basis: str
    reference: str = ""
    pairs: tuple = ()

    def __post_init__(self):
        d = self.deg
        if isinstance(d, bool) or not isinstance(d, (int, float, np.integer, np.floating)) \
                or not math.isfinite(float(d)):
            raise ContractError(f"angle limit must be a finite number of degrees, got {d!r}")
        if float(d) <= 0.0:
            raise ContractError(f"angle limit must be > 0 degrees, got {d!r}")
        if self.basis not in ANGLE_BASES:
            raise ContractError(f"angle limit basis must be one of {sorted(ANGLE_BASES)}, got {self.basis!r}")
        if self.basis == "sagc" and not (isinstance(self.reference, str) and self.reference.strip()):
            raise ContractError("an angle limit on basis 'sagc' needs the SAGC clause in reference")
        for p in self.pairs:
            if not (isinstance(p, str) or (isinstance(p, tuple) and len(p) == 2
                                           and all(isinstance(b, str) for b in p))):
                raise ContractError(f"angle pair must be a branch id or a (bus, bus) tuple, got {p!r}")

    def ledger(self) -> dict:
        return {"deg": float(self.deg), "basis": self.basis, "reference": self.reference}


# ---------------------------------------------------------------------------
# emergency ratings
# ---------------------------------------------------------------------------

def _branch_ids(net) -> list:
    """Branch ids in ``branch_keys`` order (lines, then transformers)."""
    keys = branch_keys(net)
    return [f"{f}-{t}-{c}" for f, t, c in zip(keys["from_bus"], keys["to_bus"], keys["ckt"])]


def _branches_on(net) -> np.ndarray:
    return np.concatenate([net.line["in_service"].to_numpy(dtype=bool),
                           net.trafo["in_service"].to_numpy(dtype=bool)])


def _emergency(net) -> np.ndarray:
    """Emergency ratings in ``branch_keys`` order, NaN where none is given."""
    def column(table, col):
        if col not in table.columns:
            return np.full(len(table), np.nan)
        return pd.to_numeric(table[col], errors="coerce").to_numpy(dtype=float)
    return np.concatenate([column(net.line, LINE_EMERGENCY), column(net.trafo, TRAFO_EMERGENCY)])


def validate_emergency_ratings(net) -> None:
    """Refuse an emergency rating below the normal one, at or below zero, or
    present but not finite; equal to the normal rating is allowed."""
    names = _branch_ids(net)
    nl = len(net.line)
    for table, col, normal, offset in ((net.line, LINE_EMERGENCY, "max_i_ka", 0),
                                       (net.trafo, TRAFO_EMERGENCY, "sn_mva", nl)):
        if col not in table.columns:
            continue
        norm = table[normal].to_numpy(dtype=float)
        for pos, v in enumerate(table[col].tolist()):
            if v is None or (not isinstance(v, str) and pd.isna(v) and not np.isinf(v)):
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
    emerg = _emergency(net)
    return normal * rated / np.where(np.isnan(emerg), rated, emerg)


def _rating_note(net) -> str:
    """The row's "no emergency rating given (k of n branches)", over the
    branches in service after its outage; empty when every one has one."""
    on = _branches_on(net)
    k = int((on & np.isnan(_emergency(net))).sum())
    return f"no emergency rating given ({k} of {int(on.sum())} branches)" if k else ""


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
    on = _branches_on(net)
    loading = np.where(on, branch_loading_pct(net, i_from, i_hv), np.nan)
    emergency = np.where(on, emergency_loading_pct(net, i_from, i_hv), np.nan)
    vm = {str(net.bus.at[b, "name"]): float(net.res_bus.at[b, "vm_pu"]) for b in judged}
    if any(not math.isfinite(v) or v <= 0.0 for v in vm.values()):
        return None
    return _State(loading, emergency, vm)


def _worst(values: np.ndarray, ids: list):
    if np.isnan(values).all():
        return 0.0, ""
    k = int(np.nanargmax(values))
    return float(values[k]), ids[k]


def _verdict(pct: float) -> str:
    return "pass" if pct <= LOADING_MAX_PCT else "fail"


# ---------------------------------------------------------------------------
# stage 2: the remedial walk
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _Knob:
    """One named control: ``net[table].at[index, column]`` moves in lo..hi."""
    name: str
    table: str
    index: int
    column: str
    lo: int
    hi: int

    def get(self, net) -> int:
        return int(net[self.table].at[self.index, self.column])

    def put(self, net, value) -> None:
        net[self.table].at[self.index, self.column] = value

    def in_service(self, net) -> bool:
        return bool(net[self.table].at[self.index, "in_service"])


def _knobs(work, controls: Controls, trafo_pos: dict) -> list:
    """The named controls as ``_Knob``s, in sorted name order."""
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
        knobs.append(_Knob(name, "trafo", i, "tap_pos", int(lo), int(hi)))
    shunt_idx = {str(work.shunt.at[i, "name"]): i for i in work.shunt.index}
    for name in sorted(controls.switched_shunts):
        if name not in shunt_idx:
            raise ContractError(f"switched shunt {name!r} is not on the net")
        i = shunt_idx[name]
        for col in ("step", "max_step"):
            v = work.shunt.at[i, col]
            if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)) \
                    or not math.isfinite(float(v)):
                raise ContractError(f"switched shunt {name!r} has no finite {col} (got {v!r})")
        knobs.append(_Knob(name, "shunt", i, "step", 0, int(work.shunt.at[i, "max_step"])))
    return knobs


def _key(state: _State, bands: dict, moved: int):
    """The walk's key: (voltage excursion pu, worst overload above the normal
    rating in percentage points, steps moved). ``_better`` takes a lower
    excursion or overload only when it drops by more than ``STEP_TOL``."""
    over = max(0.0, float(np.nanmax(state.loading, initial=0.0)) - LOADING_MAX_PCT)
    return (excursion(state.vm, bands), over, moved)


def remedial_walk(work, knobs: list, bands: dict, judged, state: _State):
    """Walk the in-service ``knobs`` on ``work`` (module docstring) from
    ``state``, the solved post-outage state. Returns ``(state, moves)``: the
    last converged state, ``work`` left at its positions, and ``{name: steps
    moved}``."""
    on = [k for k in knobs if k.in_service(work)]
    start = {k.name: k.get(work) for k in on}
    pos = dict(start)
    cap = TAP_ROUNDS * sum(k.hi - k.lo for k in on)
    now = _key(state, bands, 0)
    for _ in range(cap):
        if now[0] <= V_TOL and now[1] == 0.0:
            break
        best = None
        for k in on:
            for d in (+1, -1):
                p = pos[k.name] + d
                if not k.lo <= p <= k.hi:
                    continue
                k.put(work, p)
                trial = _solve(work, judged)
                k.put(work, pos[k.name])
                if trial is None:
                    continue
                moved = sum(abs(pos[n] - start[n]) for n in pos if n != k.name) + abs(p - start[k.name])
                key = _key(trial, bands, moved)
                if best is None or _better(key, best[0]):
                    best = (key, k, p, trial)
        if best is None or not _better(best[0], now):
            break
        now, k, p, state = best
        k.put(work, p)
        pos[k.name] = p
    return state, {n: pos[n] - start[n] for n in pos if pos[n] != start[n]}


# ---------------------------------------------------------------------------
# the screen
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _Handles:
    """Where each contingency element sits on the net, by name."""
    branch: dict    # N-1 branch id -> (element_type, element_index)
    gen: dict
    sgen: dict
    bus: dict

    @classmethod
    def of(cls, net):
        n1 = branch_contingencies(net)
        return cls(
            {c: (t, int(i)) for c, t, i in zip(n1["contingency_id"], n1["element_type"], n1["element_index"])},
            {str(net.gen.at[i, "name"]): i for i in net.gen.index},
            {str(net.sgen.at[i, "name"]): i for i in net.sgen.index},
            {str(net.bus.at[i, "name"]): i for i in net.bus.index},
        )


def _apply_branches(work, cid, elems, h: _Handles) -> None:
    for e in elems:
        if e not in h.branch:
            raise ContractError(f"contingency {cid} names a branch not on the net: {e}")
        etype, eidx = h.branch[e]
        work[etype].at[eidx, "in_service"] = False


def _apply_outage(work, r, h: _Handles) -> None:
    """Take out the row's elements; a compound row's SECOND branch only, since
    the screen takes out its first and re-adjusts before calling this."""
    if r.kind == "branch":
        _apply_branches(work, r.contingency_id, r.element_ids, h)
    elif r.kind in COMPOUND_SEPARATORS:
        _apply_branches(work, r.contingency_id, r.element_ids[1:], h)
    elif r.kind == "unit":
        uid = r.element_ids[0]
        if uid in h.gen:
            work.gen.at[h.gen[uid], "in_service"] = False
        elif uid in h.sgen:
            work.sgen.at[h.sgen[uid], "in_service"] = False
        else:
            raise ContractError(f"contingency {r.contingency_id} names a unit not on the net: {uid}")
    elif r.kind == "bus_section":
        bus = r.element_ids[0]
        if bus not in h.bus:
            raise ContractError(f"contingency {r.contingency_id} names a bus not on the net: {bus}")
        b = h.bus[bus]
        work.bus.at[b, "in_service"] = False
        for tbl, cols in (("line", ("from_bus", "to_bus")), ("trafo", ("hv_bus", "lv_bus")),
                          ("gen", ("bus",)), ("sgen", ("bus",)), ("load", ("bus",)), ("shunt", ("bus",))):
            t = work[tbl]
            hit = np.zeros(len(t), dtype=bool)
            for c in cols:
                hit |= (t[c] == b).to_numpy()
            t.loc[hit, "in_service"] = False
    else:
        raise ContractError(f"contingency {r.contingency_id}: kind {r.kind!r} is not judged here")


def _p_mw(table) -> pd.Series:
    scaling = table["scaling"] if "scaling" in table.columns else 1.0
    return table["p_mw"] * scaling


def _lost(base, work, gone) -> tuple:
    """(load MW, generation MW) on ``gone`` buses plus units the outage took off."""
    on = base.load["in_service"].astype(bool) & base.load["bus"].isin(gone)
    load = float(_p_mw(base.load)[on].sum())
    gen = 0.0
    for tbl in ("gen", "sgen"):
        b, w = base[tbl], work[tbl]
        off = b["in_service"].astype(bool) & (~w["in_service"].astype(bool) | b["bus"].isin(gone))
        gen += float(_p_mw(b)[off].sum())
    return load, gen


def _row(r, hour, **kw):
    row = {
        "contingency_id": r.contingency_id, "kind": r.kind, "hour": hour,
        "converged": False, "islanded": False, "load_lost_mw": 0.0, "gen_lost_mw": 0.0,
        "stage1_loading_pct": np.nan, "stage1_branch": "", "stage1_verdict": "not_solved",
        "stage2_loading_pct": np.nan, "stage2_branch": "", "stage2_min_vm_pu": np.nan,
        "stage2_max_vm_pu": np.nan, "stage2_verdict": "not_solved", "remedial": "",
        "angle_deg": np.nan, "angle_pair": "", "angle_verdict": "not_solved", "note": "",
    }
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# the angle check
# ---------------------------------------------------------------------------

def _angle_pairs(net, angle_limit: AngleLimit | None, keys, ids, h: _Handles) -> list:
    """``[(label, bus index, bus index)]`` to measure: the limit's pairs, or
    every branch when it names none (or no limit is set)."""
    ends = {}
    for bid, t, i in zip(ids, keys["element_type"], keys["element_index"]):
        a, b = ("from_bus", "to_bus") if t == "line" else ("hv_bus", "lv_bus")
        table = net.line if t == "line" else net.trafo
        ends[bid] = (int(table.at[i, a]), int(table.at[i, b]))
    named = angle_limit.pairs if angle_limit is not None and angle_limit.pairs else tuple(ids)
    out = []
    for p in named:
        if isinstance(p, str):
            if p not in ends:
                raise ContractError(f"angle pair {p!r} is not a branch on the net")
            out.append((p, *ends[p]))
        else:
            unknown = [b for b in p if b not in h.bus]
            if unknown:
                raise ContractError(f"angle pair {p!r} names bus(es) not on the net: {unknown}")
            out.append((f"{p[0]}:{p[1]}", h.bus[p[0]], h.bus[p[1]]))
    return out


def _angle(work, pairs, live: set):
    """``(largest |angle difference| in degrees, its label, skipped labels)``
    over ``pairs`` whose two buses are both in ``live``; NaN when none is."""
    va = work.res_bus["va_degree"]
    worst, label, skipped = np.nan, "", []
    for name, a, b in pairs:
        if a not in live or b not in live:
            skipped.append(name)
            continue
        d = abs((float(va.at[a]) - float(va.at[b]) + 180.0) % 360.0 - 180.0)
        if not d <= worst:   # NaN worst takes the first
            worst, label = d, name
    return worst, label, skipped


def _angle_fields(work, pairs, live, angle_limit: AngleLimit | None, notes: list) -> dict:
    deg, pair, skipped = _angle(work, pairs, live)
    if skipped:
        notes.append(f"angle pairs skipped, a bus out of service: {', '.join(skipped)}")
    if angle_limit is None:
        verdict = "unknown"
    elif math.isnan(deg):
        verdict = "unknown"
        notes.append("no angle pair measurable")
    else:
        verdict = "pass" if deg <= float(angle_limit.deg) else "fail"
    return {"angle_deg": deg, "angle_pair": pair, "angle_verdict": verdict}


# ---------------------------------------------------------------------------
# the screen
# ---------------------------------------------------------------------------

def _capped(cset: pd.DataFrame, max_compound) -> tuple:
    """``(kept rows, {compound kind: rows skipped})``; ``max_compound`` None keeps all."""
    if max_compound is not None and (isinstance(max_compound, bool) or not isinstance(max_compound, (int, np.integer))
                                     or max_compound < 0):
        raise ContractError(f"max_compound must be a whole number >= 0 or None, got {max_compound!r}")
    skipped = {k: 0 for k in sorted(COMPOUND_SEPARATORS)}
    if max_compound is None:
        return cset, skipped
    keep = pd.Series(True, index=cset.index)
    for kind in skipped:
        ids = cset.loc[cset["kind"] == kind, "contingency_id"]
        dropped = ids.sort_values(kind="mergesort").iloc[int(max_compound):]
        keep[dropped.index] = False
        skipped[kind] = len(dropped)
    return cset[keep], skipped


def _summary(results: pd.DataFrame, kinds, skipped: dict) -> dict:
    """Per kind: the worst stage and angle verdicts; a kind with skipped rows
    is ``incomplete`` unless a judged row already fails."""
    out = {}
    for kind in sorted(kinds):
        rows = results[results["kind"] == kind]
        n_skip = skipped.get(kind, 0)
        stages = set(rows["stage1_verdict"]) | set(rows["stage2_verdict"])
        angles = set(rows["angle_verdict"])
        verdict = ("fail" if "fail" in stages else "incomplete" if n_skip
                   else "not_solved" if "not_solved" in stages else "pass")
        angle = ("fail" if "fail" in angles else "incomplete" if n_skip
                 else "unknown" if "unknown" in angles else "not_solved" if "not_solved" in angles else "pass")
        note = f"{n_skip} of {n_skip + len(rows)} {kind} rows skipped by max_compound" if n_skip else ""
        out[kind] = {"verdict": verdict, "angle_verdict": angle, "note": note}
    return out


def screen_planning(net, contingencies, dispatch, loads, hour, registry, *, controls: Controls | None = None,
                    max_compound: int | None = None, angle_limit: AngleLimit | None = None):
    """``(results, report)``: one ``validate_planning_results`` row per
    contingency judged, in the set's order, and ``{"counts": {kind: n judged},
    "skipped": {compound kind: n}, "summary": {kind: {...}}, "angle_limit":
    {...} or "unset", "seconds"}``. Kinds judged: branch (any order), unit,
    bus_section, n_m_1, n_1_1."""
    cset = validate_contingency_set(contingencies)
    dispatch = validate_dispatch(dispatch)
    loads = validate_loads(loads)
    hour = int(hour)
    controls = controls or Controls()
    if angle_limit is not None and not isinstance(angle_limit, AngleLimit):
        raise ContractError(f"angle_limit must be an AngleLimit or None, got {type(angle_limit).__name__}")
    check_net_carries_hour(net, dispatch, loads, hour, registry)
    validate_emergency_ratings(net)
    kinds = set(cset["kind"])
    cset, skipped = _capped(cset, max_compound)
    t0 = time.perf_counter()

    # the controls and angle pairs are checked before the base solve, so a bad one is named, not a divergence
    keys = branch_keys(net)
    ids = _branch_ids(net)
    trafo_pos = {c: int(i) for c, t, i in zip(ids, keys["element_type"], keys["element_index"]) if t == "trafo"}
    handles = _Handles.of(net)
    knobs = _knobs(net, controls, trafo_pos)
    pairs = _angle_pairs(net, angle_limit, keys, ids, handles)
    base = copy.deepcopy(net)
    try:
        pp.runpp(base)
    except pp.LoadflowNotConverged as exc:
        raise BaseCaseNotConverged(f"base case at hour {hour} does not converge; nothing to screen") from exc
    cut_before = set(unsupplied_buses(base))
    out_before = set(base.bus.index[~base.bus["in_service"].astype(bool)])

    def live(work):
        return [b for b in work.bus.index if bool(work.bus.at[b, "in_service"]) and b not in cut_before]

    def bands_of(work, judged):
        return {str(work.bus.at[b, "name"]): (V_MIN_PU, V_MAX_PU) for b in judged}

    def cut_off(work):
        out_bus = set(work.bus.index[~work.bus["in_service"].astype(bool)]) - out_before
        return out_bus, set(unsupplied_buses(work)) - cut_before - out_bus - out_before

    rows = []
    for r in cset.itertuples(index=False):
        work = copy.deepcopy(base)
        notes = []
        if r.kind in COMPOUND_SEPARATORS:
            first = r.element_ids[0]
            _apply_branches(work, r.contingency_id, [first], handles)
            _, cut = cut_off(work)
            if cut:
                names = ", ".join(sorted(str(base.bus.at[b, "name"]) for b in cut))
                rows.append(_row(r, hour, islanded=True, note=f"islanded at the first outage ({first}): "
                                 f"disconnects {names}"))
                continue
            judged = live(work)
            s0 = _solve(work, judged)
            if s0 is None:
                rows.append(_row(r, hour, note=f"load flow does not converge at the first outage ({first})"))
                continue
            _, pre = remedial_walk(work, knobs, bands_of(work, judged), judged, s0)
            notes.append(f"re-adjusted after the first outage ({first}): {_moved(pre)}")
        _apply_outage(work, r, handles)
        out_bus, cut = cut_off(work)
        load_lost, gen_lost = _lost(base, work, out_bus | cut)
        lost = {"load_lost_mw": load_lost, "gen_lost_mw": gen_lost}
        if r.kind == "bus_section":
            notes.append(f"bus section lost: {load_lost:.1f} MW load, {gen_lost:.1f} MW generation "
                         "(the generation picked up by the slack)")
        if cut:
            names = ", ".join(sorted(str(base.bus.at[b, "name"]) for b in cut))
            notes.append(f"islanded: disconnects {names}")
            rows.append(_row(r, hour, islanded=True, note="; ".join(notes), **lost))
            continue
        judged = live(work)
        bands = bands_of(work, judged)
        s1 = _solve(work, judged)
        if s1 is None:
            notes.append("post-outage load flow does not converge")
            rows.append(_row(r, hour, note="; ".join(notes), **lost))
            continue
        s1_pct, s1_branch = _worst(s1.emergency, ids)
        stage1 = {"stage1_loading_pct": s1_pct, "stage1_branch": s1_branch, "stage1_verdict": _verdict(s1_pct)}
        angle = _angle_fields(work, pairs, set(judged), angle_limit, notes)
        notes.append(_rating_note(work))
        notes.append(NO_CONTROLS if not knobs else
                     NO_REDISPATCH if any(k.in_service(work) for k in knobs) else NO_CONTROLS_ON)
        s2, moves = remedial_walk(work, knobs, bands, judged, s1)
        if moves:
            # the walk keeps converged states only; the chosen positions are re-solved as the result
            final = _solve(work, judged)
            if final is None:
                pct, branch = _worst(s2.loading, ids)
                notes.append(f"stage 2 does not converge at the chosen controls; last converged state "
                             f"{pct:.1f} % ({branch}), {min(s2.vm.values()):.3f}-{max(s2.vm.values()):.3f} pu")
                rows.append(_row(r, hour, converged=True, remedial=_moved(moves),
                                 note="; ".join(n for n in notes if n), **stage1, **angle, **lost))
                continue
            s2 = final
        s2_pct, s2_branch = _worst(s2.loading, ids)
        ok2 = s2_pct <= LOADING_MAX_PCT and excursion(s2.vm, bands) <= V_TOL
        rows.append(_row(
            r, hour, converged=True, **stage1,
            stage2_loading_pct=s2_pct, stage2_branch=s2_branch,
            stage2_min_vm_pu=min(s2.vm.values()), stage2_max_vm_pu=max(s2.vm.values()),
            stage2_verdict="pass" if ok2 else "fail",
            remedial=_moved(moves), note="; ".join(n for n in notes if n), **angle, **lost,
        ))
    results = validate_planning_results(pd.DataFrame(rows, columns=list(PLANNING_RESULT_COLUMNS)))
    report = {
        "counts": {k: int(n) for k, n in cset["kind"].value_counts().sort_index().items()},
        "skipped": skipped,
        "summary": _summary(results, kinds, skipped),
        "angle_limit": angle_limit.ledger() if angle_limit is not None else "unset",
        "seconds": round(time.perf_counter() - t0, 3),
    }
    return results, report


def _moved(moves: dict) -> str:
    """``"SH1 +3, T1 -2"``: each control's steps from where it was, or "none"."""
    return ", ".join(f"{n} {d:+d}" for n, d in sorted(moves.items())) if moves else "none"
