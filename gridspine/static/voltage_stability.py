"""P–V and Q–V voltage-stability margins at the point of connection (screening).

**P–V.** The PoC load is stepped at a set power factor (lagging) on a copy of
the net, each step balanced exactly as a connection-capacity trial is
(``capacity.apply_connection``: committed units pro-rata to headroom, the
slack takes the rest). The last converged step and the first failing one
bracket the nose; bisection between them to ``tol_mw`` makes the answer
independent of the step size. The nose is reported in MW with the PoC
voltage there, and the margin from the planned load in MW and as a % of it.

Non-convergence can be the solver rather than collapse, so every failing
point is retried once from a flat start with more iterations before it
counts as past the nose. The row records how many points were retried and
how many the retry rescued.

**Q–V.** With the planned load in place, a fictitious PV source at the PoC
(no P, no Q limits) holds its voltage set-point while the set-point is swept,
and the source's Q is read. The Q margin is the distance from Q = 0 to the
curve's minimum (positive: the PoC can lose that much reactive support
before collapse). dQ/dV is the slope at the operating point, by a central
difference; positive is the stable side.

**Cases.** Intact, plus the worst N-1 outages of the contingency set (read
only; branch and unit outages, the N-1 screen's kinds). Worst means the
lowest PoC voltage with the planned load connected, from the screen's own
batched lightsim2grid solve; islanded and non-converged outages are not
ranked and are named in the summary. At most ``max_cases`` are studied.

Every row is labelled ``SCREENING_BASIS``: this is a static screen and the
binding answer is the RMS study, which stays outside this tool.

The caller's net and contingency set are never mutated.

Allowed to import pandapower and lightsim2grid (``static/``); never pypsa.
"""
import copy
import dataclasses
import math

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
_RANKED_KINDS = ("branch", "unit")

#: What every margin in a bundle rests on; the study ledger carries it.
VOLTAGE_STABILITY_LEDGER = (
    "P–V nose: the PoC load is stepped at the set power factor (lagging), "
    "balanced as a connection-capacity trial (committed units pro-rata to "
    "headroom, the slack takes the rest); the last converged and first failing "
    "steps bracket the nose and bisection narrows it to tol_mw",
    "a failing point is retried once from a flat start with more iterations "
    "before it counts as past the nose; the row records retries and rescues",
    "margin = nose MW - planned MW, and as a % of the planned MW; a nose not "
    "reached by cap_mw is reported at the cap and flagged, never extrapolated",
    "Q–V: a fictitious PV source at the PoC, no Q limits, swept over its "
    "voltage set-point with the planned load connected; the Q margin is minus "
    "the curve's minimum Q, dQ/dV is a central difference at the operating "
    "point; a minimum on the sweep's edge is flagged",
    "cases: intact plus the worst N-1 branch and unit outages of the "
    "contingency set by PoC voltage with the planned load connected, up to "
    "max_cases; islanded and non-converged outages are not ranked",
    f"every row is {SCREENING_BASIS!r}: static load flow only, no dynamics, "
    "controls or protection",
)


@dataclasses.dataclass(frozen=True)
class VoltageStabilitySettings:
    #: power factor of the PoC load, lagging
    pf: float = DEFAULT_CRITERIA.load_pf
    #: worst N-1 outages studied besides the intact case
    max_cases: int = 5
    #: P–V step, % of the planned load
    step_pct: float = 20.0
    #: bisection tolerance on the nose
    tol_mw: float = 1.0
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
        def positive(name):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ContractError(f"{name} must be a finite number > 0, got {v!r}")

        for name in ("step_pct", "tol_mw", "qv_step_pu", "dqdv_h_pu", "qv_v_min_pu", "qv_v_max_pu"):
            positive(name)
        if self.cap_mw is not None:
            positive("cap_mw")
        if not (isinstance(self.max_cases, int) and self.max_cases >= 0):
            raise ContractError(f"max_cases must be an integer >= 0, got {self.max_cases!r}")
        if not (isinstance(self.retry_max_iteration, int) and self.retry_max_iteration > 0):
            raise ContractError(f"retry_max_iteration must be an integer > 0, got {self.retry_max_iteration!r}")
        if not (isinstance(self.pf, (int, float)) and 0 < self.pf <= 1):
            raise ContractError(f"power factor must lie in (0, 1], got {self.pf!r}")
        if self.qv_v_min_pu >= self.qv_v_max_pu:
            raise ContractError(
                f"Q–V sweep needs qv_v_min_pu < qv_v_max_pu, got {self.qv_v_min_pu} >= {self.qv_v_max_pu}"
            )


class _Tally:
    def __init__(self):
        self.retries = 0
        self.rescues = 0


def _run(work, st: VoltageStabilitySettings, tally: _Tally) -> bool:
    """Solve ``work`` in place; one flat-start retry on failure."""
    try:
        pp.runpp(work)
        return True
    except pp.LoadflowNotConverged:
        tally.retries += 1
    try:
        pp.runpp(work, init="flat", max_iteration=st.retry_max_iteration)
    except pp.LoadflowNotConverged:
        return False
    tally.rescues += 1
    return True


class _Probe:
    """One private copy of a case on which the PoC load is set again for each
    trial, so a sweep does not copy the net per point. Every trial starts from
    the case's own dispatch, so the balancing never accumulates."""

    def __init__(self, case, bus, st, tally):
        self.work = copy.deepcopy(case)
        self.gen_p = self.work.gen["p_mw"].copy()
        self.bus, self.st, self.tally = bus, st, tally

    def solve(self, p_mw):
        """The solved net carrying ``p_mw`` at the PoC, or None."""
        w = self.work
        w.load = w.load[w.load["name"] != PROBE_NAME]
        w.gen["p_mw"] = self.gen_p
        apply_connection(w, self.bus, p_mw, "load", self.st.pf)
        return w if _run(w, self.st, self.tally) else None


def _nose(probe: _Probe, b, planned_mw) -> dict:
    st = probe.st
    cap = 10.0 * planned_mw if st.cap_mw is None else float(st.cap_mw)
    step = planned_mw * st.step_pct / 100.0

    def v_at(p):
        w = probe.solve(p)
        return None if w is None else float(w.res_bus.at[b, "vm_pu"])

    v_lo = v_at(0.0)
    if v_lo is None:
        return {"converged": False, "nose_mw": np.nan, "v_nose_pu": np.nan, "nose_beyond_cap": False}
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
    return {"converged": True, "nose_mw": lo, "v_nose_pu": v_lo, "nose_beyond_cap": hi is None}


def _qv(probe: _Probe, b, planned_mw, v_op) -> dict:
    out = {"q_margin_mvar": np.nan, "v_at_qmin_pu": np.nan, "qmin_at_sweep_edge": False,
           "dqdv_mvar_per_pu": np.nan}
    if not math.isfinite(v_op):
        return out
    st = probe.st
    w = probe.work
    if probe.solve(planned_mw) is None:
        return out
    g = pp.create_gen(w, b, p_mw=0.0, vm_pu=v_op, name=QV_SOURCE_NAME)
    try:
        def q_at(v):
            w.gen.at[g, "vm_pu"] = v
            return float(w.res_gen.at[g, "q_mvar"]) if _run(w, st, probe.tally) else np.nan

        n = int(round((st.qv_v_max_pu - st.qv_v_min_pu) / st.qv_step_pu)) + 1
        grid = np.linspace(st.qv_v_max_pu, st.qv_v_min_pu, n)
        q = np.array([q_at(v) for v in grid])
        ok = np.flatnonzero(~np.isnan(q))
        if ok.size:
            i = ok[np.argmin(q[ok])]
            out.update(q_margin_mvar=-float(q[i]), v_at_qmin_pu=float(grid[i]),
                       qmin_at_sweep_edge=bool(i in (ok[0], ok[-1])))
        h = st.dqdv_h_pu
        out["dqdv_mvar_per_pu"] = (q_at(v_op + h) - q_at(v_op - h)) / (2 * h)
    finally:
        w.gen = w.gen.drop(index=g)
    return out


def _case_row(case, label, cid, kind, bus, b, planned_mw, st) -> dict:
    tally = _Tally()
    probe = _Probe(case, bus, st, tally)
    row = {"case": label, "contingency_id": cid, "kind": kind}
    row.update(_nose(probe, b, planned_mw))
    op = probe.solve(planned_mw) if row["converged"] else None
    v_op = float(op.res_bus.at[b, "vm_pu"]) if op is not None else np.nan
    row["margin_mw"] = row["nose_mw"] - planned_mw
    row["margin_pct"] = 100.0 * row["margin_mw"] / planned_mw
    row["v_poc_pu"] = v_op
    row.update(_qv(probe, b, planned_mw, v_op))
    row["flat_start_retries"] = tally.retries
    row["flat_start_rescues"] = tally.rescues
    row["basis"] = SCREENING_BASIS
    return row


def _rank(net, bus, b, planned_mw, cset, st) -> tuple:
    """N-1 rows by PoC voltage with the planned load connected, lowest first;
    and the contingency ids that could not be ranked."""
    rows = cset[(cset["order"] == 1) & cset["kind"].isin(_RANKED_KINDS)]
    if rows.empty:
        return rows, []
    work = _Probe(net, bus, st, _Tally()).solve(planned_mw)
    if work is None:
        return rows.iloc[0:0], list(rows["contingency_id"])
    gm = gridmodel_for(work)
    v0 = work._ppc["internal"]["V"]
    nl = len(work.line)
    outcomes = {}
    outcomes.update(_branch_outcomes(work, gm, v0, rows[rows["kind"] == "branch"], nl))
    outcomes.update(_unit_outcomes(work, gm, v0, rows[rows["kind"] == "unit"], nl))
    pos = work.bus.index.get_loc(b)
    v = {cid: float(vm[pos]) for cid, (conv, isl, _ld, vm) in outcomes.items() if conv and not isl}
    not_ranked = [cid for cid in rows["contingency_id"] if cid not in v]
    ranked = rows[rows["contingency_id"].isin(v)].copy()
    ranked["_v"] = ranked["contingency_id"].map(v)
    ranked = ranked.sort_values(["_v", "contingency_id"], kind="stable").drop(columns="_v")
    return ranked, not_ranked


def voltage_stability(net, poc_bus, planned_mw, contingencies=None,
                      settings: VoltageStabilitySettings | None = None) -> tuple:
    """P–V and Q–V margins at ``poc_bus`` for a planned load of ``planned_mw``.

    Returns ``(rows, summary)``: one row per case (intact first, then the
    studied N-1 outages, worst first) and a summary of which N-1 outages were
    ranked, not ranked and studied, with the ledger.
    """
    st = VoltageStabilitySettings() if settings is None else settings
    b = _bus_index(net, poc_bus)
    planned_mw = float(planned_mw)
    if not math.isfinite(planned_mw) or planned_mw <= 0:
        raise ContractError(f"planned_mw must be finite and > 0, got {planned_mw}")

    results = [_case_row(net, "intact", None, None, poc_bus, b, planned_mw, st)]
    summary = {"n1_ranked": 0, "n1_not_ranked": [], "n1_studied": 0, "kinds_not_ranked": [],
               "ledger": VOLTAGE_STABILITY_LEDGER}
    if contingencies is not None:
        cset = validate_contingency_set(contingencies.copy(deep=True))
        summary["kinds_not_ranked"] = sorted(
            set(cset.loc[(cset["order"] != 1) | ~cset["kind"].isin(_RANKED_KINDS), "kind"])
        )
        ranked, not_ranked = _rank(net, poc_bus, b, planned_mw, cset, st)
        summary["n1_ranked"] = len(ranked)
        summary["n1_not_ranked"] = not_ranked
        handles = _Handles.of(net)
        for r in ranked.head(st.max_cases).itertuples(index=False):
            case = copy.deepcopy(net)
            _apply_outage(case, r, handles)
            results.append(_case_row(case, r.contingency_id, r.contingency_id, r.kind, poc_bus, b, planned_mw, st))
        summary["n1_studied"] = len(results) - 1
    return pd.DataFrame(results), summary
