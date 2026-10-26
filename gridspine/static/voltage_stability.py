"""P–V and Q–V voltage-stability margins at the point of connection (screening).

**P–V.** The PoC load is stepped at a set power factor (lagging) on a copy of
the net, each step balanced exactly as a connection-capacity trial is
(``capacity.apply_connection``: committed units pro-rata to headroom, the
slack takes the rest). The last converged step and the first failing one
bracket the nose; bisection between them to ``tol_mw`` makes the answer
independent of the step size. The nose is reported in MW with the PoC
voltage there, and the margin from the planned load in MW and as a % of it.
A nose not reached by ``cap_mw`` is reported at the cap as a lower bound
(``nose_is_lower_bound``); a nose below the planned load gives a negative
margin and ``nose_below_planned``.

Every load flow enforces generator Q limits (``enforce_q_lims=True``): the
nose depends heavily on them. Non-convergence can be the solver rather than
collapse, so every failing point is retried once from a flat start with more
iterations before it counts as past the nose. A P–V point the retry rescues
must sit on the upper branch: re-solved from its own voltages at a slightly
lower load, its PoC voltage must rise; a low-voltage-branch solution falls,
and is rejected as past the nose. The row records, per sweep, how many points
were retried and how many the retry rescued, and how many rescues were
rejected.

**Q–V.** With the planned load in place, a fictitious PV source at the PoC
(no P, Q limits of +/- ``QV_SOURCE_Q_LIMIT_MVAR`` so it never clips) holds
its voltage set-point while the set-point is swept, and the source's Q is
read. ``q_margin_mvar`` is minus the curve's minimum Q: positive means
margin, the reactive support the PoC can lose before collapse; negative
means the PoC already needs that much. When the minimum sits on the edge of
the converged sweep (``qmin_at_sweep_edge``) the margin is a bound, not a
found minimum. Sweep points that do not converge are skipped and counted.
dQ/dV is the slope at the operating point, by a central difference; positive
is the stable side.

**Cases.** Intact, plus the worst N-1 outages of the contingency set (read
only; branch and unit outages, the N-1 screen's kinds; other order-1 kinds
are skipped and named in the summary). Worst means the
lowest PoC voltage with the planned load connected, read from the screen's
own batched lightsim2grid solve (``contingency._branch_outcomes`` /
``_unit_outcomes``). At most ``max_cases`` of these converged outages are
studied. An outage that islands or does not converge with the planned load
connected is the most severe case, not one to drop: it is always listed, as
a ``not_solved`` row with no nose or margin numbers.

Every row is labelled ``SCREENING_BASIS``: this is a static screen and the
binding answer is the RMS study, which stays outside this tool.

The caller's net and contingency set are never mutated.

Allowed to import pandapower and lightsim2grid (``static/``); never pypsa.
"""
import copy
import dataclasses
import math
import numbers

import numpy as np
import pandapower as pp
import pandas as pd

from gridspine.schema.contingency import validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.capacity import (
    DEFAULT_CRITERIA,
    PROBE_NAME,
    _bus_index,
    apply_connection,
)
from gridspine.static.contingency import _branch_outcomes, _unit_outcomes, gridmodel_for
from gridspine.static.planning_criteria import _apply_outage, _Handles

SCREENING_BASIS = "screening; binding answer is the RMS study"
QV_SOURCE_NAME = "QV_PROBE"
QV_SOURCE_Q_LIMIT_MVAR = 1e9
_RANKED_KINDS = ("branch", "unit")

SOLVED = "solved"
NOT_SOLVED = "not_solved"
NOTE_COLLAPSE = "collapses at or below the planned load"
NOTE_ISLANDED = "islanded"
NOTE_NO_BASE = "does not converge with no load added"

#: What every margin in a bundle rests on; the study ledger carries it.
VOLTAGE_STABILITY_LEDGER = (
    "P–V nose: the PoC load is stepped at the set power factor (lagging), "
    "balanced as a connection-capacity trial (committed units pro-rata to "
    "headroom, the slack takes the rest); the last converged and first failing "
    "steps bracket the nose and bisection narrows it to tol_mw",
    "every P–V and Q–V load flow enforces generator Q limits",
    "a failing point is retried once from a flat start with more iterations "
    "before it counts as past the nose; a rescued P–V point whose PoC voltage "
    "does not rise at a slightly lower load is on the low-voltage branch and "
    "is rejected; the row records retries, rescues and rejections per sweep",
    "margin = nose MW - planned MW, and as a % of the planned MW; a nose not "
    "reached by cap_mw is reported at the cap as a lower bound, never "
    "extrapolated",
    "Q–V: a fictitious PV source at the PoC, Q never clipped, swept over its "
    "voltage set-point with the planned load connected; the Q margin is minus "
    "the curve's minimum Q (positive is margin), a bound when the minimum is "
    "on the sweep's edge; dQ/dV is a central difference at the operating point",
    "cases: intact plus the worst N-1 branch and unit outages of the "
    "contingency set by PoC voltage with the planned load connected, up to "
    "max_cases; an outage that islands or does not converge with the planned "
    "load is always listed, not_solved",
    f"every row is {SCREENING_BASIS!r}: static load flow only, no dynamics, "
    "controls or protection",
)


def _require_positive(name, v):
    if isinstance(v, bool) or not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
        raise ContractError(f"{name} must be a finite number > 0, got {v!r}")


def _require_count(name, v, low):
    if isinstance(v, bool) or not (isinstance(v, numbers.Integral) and v >= low):
        raise ContractError(f"{name} must be an integer >= {low}, got {v!r}")


@dataclasses.dataclass(frozen=True)
class VoltageStabilitySettings:
    #: power factor of the PoC load, lagging
    pf: float = DEFAULT_CRITERIA.load_pf
    #: worst converged N-1 outages studied besides the intact case
    max_cases: int = 5
    #: P–V step, % of the planned load
    step_pct: float = 20.0
    #: bisection tolerance on the nose
    tol_mw: float = 0.1
    #: highest load tried; None means 10 x the planned load
    cap_mw: float | None = None
    #: iterations allowed on the flat-start retry
    retry_max_iteration: int = 50
    qv_v_min_pu: float = 0.5
    qv_v_max_pu: float = 1.2
    qv_step_pu: float = 0.01
    #: half-width of the dQ/dV central difference
    dqdv_h_pu: float = 0.005

    def __post_init__(self):
        for name in ("step_pct", "tol_mw", "qv_step_pu", "dqdv_h_pu", "qv_v_min_pu", "qv_v_max_pu"):
            _require_positive(name, getattr(self, name))
        if self.cap_mw is not None:
            _require_positive("cap_mw", self.cap_mw)
        _require_count("max_cases", self.max_cases, 0)
        _require_count("retry_max_iteration", self.retry_max_iteration, 1)
        if isinstance(self.pf, bool) or not (isinstance(self.pf, (int, float)) and 0 < self.pf <= 1):
            raise ContractError(f"power factor must lie in (0, 1], got {self.pf!r}")
        if self.qv_v_min_pu >= self.qv_v_max_pu:
            raise ContractError(
                f"Q–V sweep needs qv_v_min_pu < qv_v_max_pu, got {self.qv_v_min_pu} >= {self.qv_v_max_pu}"
            )


class _Tally:
    def __init__(self):
        self.retries = 0
        self.rescues = 0
        self.low_branch = 0


def _run(work, st: VoltageStabilitySettings, tally: _Tally) -> bool:
    """Solve ``work`` in place, Q limits enforced; one flat-start retry on failure."""
    try:
        pp.runpp(work, enforce_q_lims=True)
        return True
    except pp.LoadflowNotConverged:
        tally.retries += 1
    try:
        pp.runpp(work, enforce_q_lims=True, init="flat", max_iteration=st.retry_max_iteration)
    except pp.LoadflowNotConverged:
        return False
    tally.rescues += 1
    return True


class _Probe:
    """One private copy of a case on which the PoC load is set again for each
    trial, so a sweep does not copy the net per point. Every trial starts from
    the case's own dispatch, so the balancing never accumulates."""

    def __init__(self, case, bus, b, st, tally):
        self.work = copy.deepcopy(case)
        self.gen_p = self.work.gen["p_mw"].copy()
        self.bus, self.b, self.st, self.tally = bus, b, st, tally

    def _set_load(self, p_mw):
        w = self.work
        w.load = w.load[w.load["name"] != PROBE_NAME]
        w.gen["p_mw"] = self.gen_p
        apply_connection(w, self.bus, p_mw, "load", self.st.pf)

    def solve(self, p_mw):
        """The solved net carrying ``p_mw`` at the PoC, or None."""
        self._set_load(p_mw)
        return self.work if _run(self.work, self.st, self.tally) else None

    def v_poc(self, p_mw):
        """The PoC voltage at ``p_mw`` on the upper branch, or None.

        A flat-start rescue is checked: re-solved from its own voltages at a
        slightly lower load, an upper-branch point's PoC voltage rises and a
        low-voltage-branch point's falls."""
        rescues = self.tally.rescues
        w = self.solve(p_mw)
        if w is None:
            return None
        v = float(w.res_bus.at[self.b, "vm_pu"])
        if self.tally.rescues == rescues or p_mw <= 0:
            return v
        self._set_load(p_mw - min(self.st.tol_mw, 0.5 * p_mw))
        try:
            pp.runpp(w, enforce_q_lims=True, init="results")
            upper = float(w.res_bus.at[self.b, "vm_pu"]) > v
        except pp.LoadflowNotConverged:
            upper = False
        if not upper:
            self.tally.low_branch += 1
            return None
        return v


def _cap(planned_mw, st) -> float:
    return 10.0 * planned_mw if st.cap_mw is None else float(st.cap_mw)


def _nose(probe: _Probe, planned_mw) -> dict | None:
    """The bracketed and bisected nose, or None when nothing converges."""
    st = probe.st
    cap = _cap(planned_mw, st)
    step = planned_mw * st.step_pct / 100.0
    v_at = probe.v_poc

    v_lo = v_at(0.0)
    if v_lo is None:
        return None
    lo, hi = 0.0, None
    k = 1
    while hi is None and lo < cap:
        p = min(k * step, cap)
        v = v_at(p)
        if v is None:
            hi = p
        else:
            lo, v_lo = p, v
        k += 1
    if hi is not None:
        while hi - lo > st.tol_mw:
            mid = 0.5 * (lo + hi)
            v = v_at(mid)
            if v is None:
                hi = mid
            else:
                lo, v_lo = mid, v
    return {"nose_mw": lo, "v_nose_pu": v_lo, "nose_is_lower_bound": hi is None}


def _qv(probe: _Probe, planned_mw, v_op, tally: _Tally) -> dict:
    """The Q–V sweep on the probe's net; solves count on ``tally``."""
    out = {"q_margin_mvar": np.nan, "v_at_qmin_pu": np.nan, "qmin_at_sweep_edge": False,
           "qv_points_skipped": 0, "dqdv_mvar_per_pu": np.nan}
    if not math.isfinite(v_op):
        return out
    st, b = probe.st, probe.b
    w = probe.work
    if probe.solve(planned_mw) is None:
        return out
    g = pp.create_gen(w, b, p_mw=0.0, vm_pu=v_op, name=QV_SOURCE_NAME,
                      min_q_mvar=-QV_SOURCE_Q_LIMIT_MVAR, max_q_mvar=QV_SOURCE_Q_LIMIT_MVAR)
    try:
        def q_at(v):
            w.gen.at[g, "vm_pu"] = v
            return float(w.res_gen.at[g, "q_mvar"]) if _run(w, st, tally) else np.nan

        n = int(round((st.qv_v_max_pu - st.qv_v_min_pu) / st.qv_step_pu)) + 1
        grid = np.linspace(st.qv_v_max_pu, st.qv_v_min_pu, n)
        q = np.array([q_at(v) for v in grid])
        ok = np.flatnonzero(~np.isnan(q))
        out["qv_points_skipped"] = int(n - ok.size)
        if ok.size:
            i = ok[np.argmin(q[ok])]
            out.update(q_margin_mvar=-float(q[i]), v_at_qmin_pu=float(grid[i]),
                       qmin_at_sweep_edge=bool(i in (ok[0], ok[-1])))
        h = st.dqdv_h_pu
        out["dqdv_mvar_per_pu"] = (q_at(v_op + h) - q_at(v_op - h)) / (2 * h)
    finally:
        w.gen = w.gen.drop(index=g)
    return out


_NUMBERS = {
    "nose_mw": np.nan, "v_nose_pu": np.nan, "nose_is_lower_bound": False,
    "margin_mw": np.nan, "margin_pct": np.nan, "nose_below_planned": False, "v_poc_pu": np.nan,
    "q_margin_mvar": np.nan, "v_at_qmin_pu": np.nan, "qmin_at_sweep_edge": False,
    "qv_points_skipped": 0, "dqdv_mvar_per_pu": np.nan,
}


def _row(label, cid, kind, verdict, note, numbers, pv: _Tally, qv: _Tally) -> dict:
    return {
        "case": label, "contingency_id": cid, "kind": kind, "verdict": verdict, "note": note,
        **_NUMBERS, **numbers,
        "pv_flat_start_retries": pv.retries, "pv_flat_start_rescues": pv.rescues,
        "pv_low_branch_rejected": pv.low_branch,
        "qv_flat_start_retries": qv.retries, "qv_flat_start_rescues": qv.rescues,
        "basis": SCREENING_BASIS,
    }


def _case_row(case, label, cid, kind, bus, b, planned_mw, st) -> dict:
    pv, qv = _Tally(), _Tally()
    probe = _Probe(case, bus, b, st, pv)
    nose = _nose(probe, planned_mw)
    if nose is None:
        return _row(label, cid, kind, NOT_SOLVED, NOTE_NO_BASE, {}, pv, qv)
    numbers = dict(nose)
    numbers["margin_mw"] = nose["nose_mw"] - planned_mw
    numbers["margin_pct"] = 100.0 * numbers["margin_mw"] / planned_mw
    numbers["nose_below_planned"] = bool(numbers["margin_mw"] < 0)
    note = ""
    if numbers["nose_below_planned"]:
        note = "nose below the planned load"
    elif nose["nose_is_lower_bound"]:
        note = "nose beyond cap_mw: nose and margin are lower bounds"
    v_op = np.nan
    if not numbers["nose_below_planned"]:
        v_op = probe.v_poc(planned_mw)
        if v_op is None:
            v_op = np.nan
            note = "; ".join(filter(None, (note, "planned-load point did not converge: no Q–V")))
    numbers["v_poc_pu"] = v_op
    numbers.update(_qv(probe, planned_mw, v_op, qv))
    return _row(label, cid, kind, SOLVED, note, numbers, pv, qv)


def _rank(net, bus, b, planned_mw, rows, st) -> tuple:
    """The converged N-1 rows by PoC voltage with the planned load connected,
    lowest first; and ``{contingency_id: note}`` for the rest."""
    work = _Probe(net, bus, b, st, _Tally()).solve(planned_mw)
    if work is None:
        return rows.iloc[0:0], {cid: NOTE_COLLAPSE for cid in rows["contingency_id"]}
    gm = gridmodel_for(work)
    v0 = work._ppc["internal"]["V"]
    nl = len(work.line)
    outcomes = {}
    outcomes.update(_branch_outcomes(work, gm, v0, rows[rows["kind"] == "branch"], nl))
    outcomes.update(_unit_outcomes(work, gm, v0, rows[rows["kind"] == "unit"], nl))
    pos = work.bus.index.get_loc(b)
    v, failed = {}, {}
    for cid in rows["contingency_id"]:
        conv, isl, _ld, vm = outcomes[cid]
        if conv:
            v[cid] = float(vm[pos])
        else:
            failed[cid] = NOTE_ISLANDED if isl else NOTE_COLLAPSE
    ranked = rows[rows["contingency_id"].isin(v)].copy()
    ranked["_v"] = ranked["contingency_id"].map(v)
    ranked = ranked.sort_values(["_v", "contingency_id"], kind="stable").drop(columns="_v")
    return ranked, failed


def voltage_stability(net, poc_bus, planned_mw, contingencies=None,
                      settings: VoltageStabilitySettings | None = None) -> tuple:
    """P–V and Q–V margins at ``poc_bus`` for a planned load of ``planned_mw``.

    ``contingencies`` is an N-1 set, or None for the intact case only. Branch
    and unit outages (the N-1 screen's kinds) are ranked; other kinds are
    skipped and named in the summary.

    Returns ``(rows, summary)``: one row per case — intact first, then the
    N-1 outages that island or do not converge with the planned load
    (``not_solved``), then the studied converged ones, worst first — and a
    summary of how many were ranked, not solved, skipped and studied, with
    the ledger.
    """
    st = VoltageStabilitySettings() if settings is None else settings
    if not isinstance(st, VoltageStabilitySettings):
        raise ContractError(f"settings must be VoltageStabilitySettings, got {type(st).__name__}")
    b = _bus_index(net, poc_bus)
    if isinstance(planned_mw, bool) or not isinstance(planned_mw, numbers.Real):
        raise ContractError(f"planned_mw must be a number, got {planned_mw!r}")
    planned_mw = float(planned_mw)
    if not math.isfinite(planned_mw) or planned_mw <= 0:
        raise ContractError(f"planned_mw must be finite and > 0, got {planned_mw}")
    if _cap(planned_mw, st) <= planned_mw:
        raise ContractError(f"cap_mw must exceed the planned load ({planned_mw} MW), got {st.cap_mw}")
    cset = None
    if contingencies is not None:
        cset = validate_contingency_set(contingencies.copy(deep=True))
        if (cset["order"] != 1).any():
            raise ContractError("voltage_stability takes an N-1 set; got rows with order != 1")

    results = [_case_row(net, "intact", None, None, poc_bus, b, planned_mw, st)]
    summary = {"n1_ranked": 0, "n1_not_solved": [], "n1_skipped": [], "n1_studied": 0,
               "ledger": VOLTAGE_STABILITY_LEDGER}
    if cset is not None and not cset.empty:
        judged = cset["kind"].isin(_RANKED_KINDS)
        summary["n1_skipped"] = list(cset.loc[~judged, "contingency_id"])
        if judged.any():
            ranked, failed = _rank(net, poc_bus, b, planned_mw, cset[judged], st)
            kind_of = dict(zip(cset["contingency_id"], cset["kind"]))
            results += [_row(cid, cid, kind_of[cid], NOT_SOLVED, note, {}, _Tally(), _Tally())
                        for cid, note in failed.items()]
            handles = _Handles.of(net)
            studied = ranked.head(st.max_cases)
            for r in studied.itertuples(index=False):
                case = copy.deepcopy(net)
                _apply_outage(case, r, handles)
                results.append(_case_row(case, r.contingency_id, r.contingency_id, r.kind,
                                         poc_bus, b, planned_mw, st))
            summary.update(n1_ranked=len(ranked), n1_not_solved=list(failed), n1_studied=len(studied))
    return pd.DataFrame(results), summary
