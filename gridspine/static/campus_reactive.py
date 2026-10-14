"""Reactive power at the PCC, and the compensation a campus needs (plan C4b).

**The requirement.** The PCC reactive exchange must stay within
``+-q_limit`` Mvar, where ``q_limit = q_frac * P_ref``:

* ``q_frac`` is the grid-code profile's demand range. For the EU profile
  that is DCC Art. 15(1)(a): "not wider than 48 percent" of the larger of
  maximum import and export capacity, about power factor 0.9. This is the
  maximum a TSO may require; the actual range is the TSO's choice. A study
  can therefore set the power factor of its connection agreement instead,
  and ``q_frac = tan(acos(pf))`` is then tagged ``assumed``.
* ``P_ref`` is the connection capacity (see the driver).

**Correcting an hour.** At an hour outside the band, the campus must supply
the difference itself:

1. **The inverters first.** BESS, PV, wind and gensets supply up to their
   headroom, ``sqrt(S^2 - P^2)`` at that hour's P (P has priority), shared
   in proportion to it. A unit with status 0 offers nothing. Neither does a
   genset, PV or wind unit at 0 MW: a genset that is not producing is not
   running, and Q-at-night capability is not assumed. A battery offers its
   headroom at any P (``ALWAYS_ON``).
2. **Then the installed compensation** (the campus file's
   ``compensation``, plan C8), if any:

   a. **STATCOMs**, continuously within +-their rating, shared in
      proportion to it;
   b. **then capacitor banks** (a capacitive need) **or shunt reactors** (an
      inductive need), discretely. With the inverters and STATCOMs at their
      limit, the steps are switched on one at a time, banks in file order,
      and the **fewest steps that bring the PCC into the band** are kept.
      If none does, the count that leaves the PCC closest to the band is
      kept (the fewest on a tie). The continuous sources then back off,
      never past zero, to put the PCC back on the band edge if the steps
      overshoot it. Banks of the other direction stay off.
3. **Then the residual.** What is still missing is a virtual shunt at the
   main MV busbar (``compensation_bus``: the low-voltage side of the
   transformers at the PCC), ``q_comp_mvar``: the compensation still
   needed. Its sign: ``+`` is capacitive (supplying Q), ``-`` is inductive
   (absorbing Q). With ``residual=False`` there is no virtual shunt: the PCC
   is left where the real equipment puts it, which is how the investment
   study re-checks a chosen set (``static/campus_invest.py``).

Transformer and cable reactive losses move with the flow, so the Q target is
corrected by the residual and the load flow re-solved until the PCC sits on
the band edge (within ``tol``). Without installed compensation the steps
above reduce to part one's rule, and give the same numbers.

``ReactiveResult.dispatch`` reports every installed compensation entry:
its kind, its switched ``steps`` (None for a STATCOM) and the Q it supplies
in the solved hour (``+`` capacitive; a shunt's Q follows V^2).
``ReactiveResult.setpoints`` is the real equipment's dispatch in the form
``solve_cases`` takes, so the same hour can be re-solved with it. The
dispatch itself never moves a tap; when the study has set taps for the hour
(plan C15, ``campus_taps``), ``ReactiveResult.taps`` carries them and the
setpoints include them.

**Sign convention.** ``q0_mvar`` and ``q_final_mvar`` are the grid's
``ext_grid`` Q, which is positive when the grid supplies reactive power
INTO the campus.

Allowed to import pandapower (``static/``); never pypsa.
"""
import copy
import dataclasses
import math

import pandapower as pp
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static.campus_flow import SizingCriteria, apply_hour

_SUPPLIERS = frozenset({"bess", "pv", "wind", "genset"})
#: Units that offer reactive power at zero active power. A battery inverter
#: stays connected and can run as a STATCOM. A genset that is not producing
#: is not running. PV and wind at 0 MW are assumed to be disconnected:
#: Q-at-night capability is not universal (ledgered, assumed).
ALWAYS_ON = frozenset({"bess"})


@dataclasses.dataclass(frozen=True)
class ReactiveRequirement:
    q_limit_mvar: float
    clause: str
    source: str


@dataclasses.dataclass
class ReactiveResult:
    converged: bool
    q0_mvar: float                 # PCC Q with every inverter at Q = 0
    q_final_mvar: float            # PCC Q after correction
    q_inverters_mvar: float        # total supplied by the inverters (signed)
    q_comp_mvar: float             # compensation (+ capacitive, - inductive)
    unit_q: dict                   # unit_id -> Q dispatched
    compliant_without: bool        # inside the band before any correction
    dispatch: dict = dataclasses.field(default_factory=dict)    # compensation name -> kind, steps, q_mvar
    q_installed_mvar: float = 0.0  # supplied by the installed compensation (+ capacitive)
    #: The tap positions the study set in the intact case (plan C15,
    #: ``campus_taps``): the moved units only; empty when none moved.
    taps: dict = dataclasses.field(default_factory=dict)

    @property
    def setpoints(self) -> dict:
        """The real equipment's dispatch, for ``solve_cases(setpoints=...)``,
        with the moved taps as ``trafo_tap`` when there are any."""
        sgen = dict(self.unit_q)
        sgen.update({k: d["q_mvar"] for k, d in self.dispatch.items() if d["kind"] == "statcom"})
        out = {"sgen_q": sgen,
               "shunt_step": {k: d["steps"] for k, d in self.dispatch.items() if d["kind"] != "statcom"}}
        if self.taps:
            out["trafo_tap"] = dict(self.taps)
        return out


def requirement_from(profile: dict, p_ref_mw: float, pf: float | None = None) -> ReactiveRequirement:
    """The PCC band from a grid-code profile (its ``q_range_demand``), or from
    a study power factor ``pf``, applied to the connection capacity."""
    if not p_ref_mw > 0:
        raise ContractError(f"the connection capacity must be positive, got {p_ref_mw}")
    if pf is None:
        q = profile["q_range_demand"]
        return ReactiveRequirement(q["value"] * p_ref_mw, q["clause"], q["source"])
    if not 0 < pf <= 1:
        raise ContractError(f"pf must be in (0, 1], got {pf}")
    return ReactiveRequirement(
        p_ref_mw * math.tan(math.acos(pf)),
        f"study connection agreement: power factor {pf:g} at the connection capacity",
        "assumed",
    )


#: The inverter-based resources IEEE 2800's reactive capability applies to.
IBR_KINDS = ("bess", "pv", "wind")
IBR_LABEL = ("screening at unit terminals: sqrt(S^2 - P_n^2) per inverter against the profile's q_range_ibr x P_n "
             "at rated active power; the standard states it for the plant at its reference point, so collector "
             "and transformer reactive losses are not counted")
IBR_COLUMNS = ("unit", "kind", "p_n_mw", "s_mva", "required_mvar", "available_mvar", "shortfall_mvar", "status",
               "detail", "clause", "source")


def ibr_q_capability(campus, profile: dict):
    """IEEE 2800's reactive capability of the on-site IBR (``IBR_LABEL``):
    one row per BESS, PV or wind unit, then a ``TOTAL`` row comparing the
    sums, or None when the profile states no ``q_range_ibr``. A unit without
    a rating is ``reported``, never ``pass``; a shortfall ``fail`` row says
    the inverter rating or the STATCOM that would cover it."""
    if "q_range_ibr" not in profile:
        return None
    ibr = profile["q_range_ibr"]
    frac = float(ibr["value"])
    oversize = math.sqrt(1.0 + frac ** 2)
    rows = []
    for uid, u in campus.units.iterrows():
        if u["kind"] not in IBR_KINDS:
            continue
        p_n, s = float(u["p_mw"]), float(u["s_mva"]) if u["s_mva"] == u["s_mva"] else math.nan
        base = {"unit": str(uid), "kind": u["kind"], "p_n_mw": p_n, "s_mva": s, "clause": ibr["clause"],
                "source": ibr["source"]}
        if not (p_n > 0 and math.isfinite(s) and s > 0):
            rows.append({**base, "required_mvar": frac * p_n if p_n > 0 else math.nan, "available_mvar": math.nan,
                         "shortfall_mvar": math.nan, "status": "reported",
                         "detail": f"{uid}: rating not given (P_n and the inverter S are needed)"})
            continue
        req = frac * p_n
        avail = math.sqrt(max(s ** 2 - p_n ** 2, 0.0))
        short = max(req - avail, 0.0)
        if short <= 1e-9:
            detail = f"{uid} supplies {avail:.2f} Mvar of the {req:.2f} Mvar required at rated P"
        else:
            detail = (f"{uid} can supply {avail:.2f} of the {req:.2f} Mvar required at rated P: oversize the "
                      f"inverter to S >= {oversize:.3f} x P_n = {oversize * p_n:.2f} MVA, or add "
                      f"{short:.2f} Mvar of STATCOM")
        rows.append({**base, "required_mvar": req, "available_mvar": avail, "shortfall_mvar": short if short > 1e-9
                     else 0.0, "status": "pass" if short <= 1e-9 else "fail", "detail": detail})
    if not rows:
        return None
    rated = [r for r in rows if r["status"] != "reported"]
    req_t = sum(r["required_mvar"] for r in rated)
    avail_t = sum(r["available_mvar"] for r in rated)
    status = ("reported" if any(r["status"] == "reported" for r in rows)
              else "fail" if any(r["status"] == "fail" for r in rows) else "pass")
    rows.append({"unit": "TOTAL", "kind": "ibr", "p_n_mw": sum(r["p_n_mw"] for r in rows), "s_mva": math.nan,
                 "required_mvar": req_t, "available_mvar": avail_t,
                 "shortfall_mvar": sum(r["shortfall_mvar"] for r in rated), "status": status,
                 "detail": (f"on-site IBR: {avail_t:.2f} Mvar available against {req_t:.2f} Mvar required "
                            f"({frac:g} x rating at rated P), screened at the unit terminals"
                            + ("; a unit without a rating is not counted" if status == "reported" else "")),
                 "clause": ibr["clause"], "source": ibr["source"]})
    return pd.DataFrame(rows, columns=list(IBR_COLUMNS))


def compensation_bus(campus) -> str:
    """Where compensation goes: the low-voltage side of the transformers
    connected to the PCC, else the PCC bus itself."""
    net = campus.net
    pcc = int(net.ext_grid["bus"].iloc[0])
    lv = [int(net.trafo.at[i, "lv_bus"]) for i in net.trafo.index if int(net.trafo.at[i, "hv_bus"]) == pcc]
    return str(net.bus.at[lv[0] if lv else pcc, "name"])


def _pcc_q(net):
    pp.runpp(net)
    return float(net.res_ext_grid["q_mvar"].sum())


def _clamp(x, cap):
    return max(-cap, min(cap, x))


def reactive_need(campus, rows: pd.DataFrame, req: ReactiveRequirement, tol: float = 0.01,
                  max_iter: int = 30, residual: bool = True) -> ReactiveResult:
    """Correct one hour into the band (see the module docstring). Works on a
    copy of the campus net."""
    work = copy.copy(campus)
    work.net = copy.deepcopy(campus.net)
    apply_hour(work, rows)
    net = work.net
    sgen_idx = dict(zip(net.sgen["name"].astype(str), net.sgen.index))
    shunt_idx = dict(zip(net.shunt["name"].astype(str), net.shunt.index))
    head = {}
    for uid, u in work.units.iterrows():
        if u["kind"] not in _SUPPLIERS:
            continue
        i = sgen_idx[uid]
        if not bool(net.sgen.at[i, "in_service"]):
            continue
        p = float(net.sgen.at[i, "p_mw"])
        if u["kind"] not in ALWAYS_ON and abs(p) <= 1e-9:
            continue                        # not running / no Q at night
        head[uid] = math.sqrt(max(float(u["s_mva"]) ** 2 - p ** 2, 0.0))
    total_head = sum(head.values())
    comp_df = getattr(work, "compensation", None)
    comp_df = comp_df if comp_df is not None else pd.DataFrame(columns=["kind", "steps"])
    statcom = {str(n): float(c["q_mvar"]) for n, c in comp_df.iterrows() if c["kind"] == "statcom"}
    total_stat = sum(statcom.values())
    bus_idx = dict(zip(net.bus["name"].astype(str), net.bus.index))
    comp = (pp.create_sgen(net, bus_idx[compensation_bus(work)], p_mw=0.0, q_mvar=0.0, name="_COMP")
            if residual else None)

    def report(steps):
        out = {}
        for n, c in comp_df.iterrows():
            n = str(n)
            if c["kind"] == "statcom":
                out[n] = {"kind": "statcom", "steps": None, "q_mvar": float(net.sgen.at[sgen_idx[n], "q_mvar"])}
            else:
                k = int(steps.get(n, 0))
                q = -float(net.res_shunt.at[shunt_idx[n], "q_mvar"]) if k else 0.0
                out[n] = {"kind": str(c["kind"]), "steps": k, "q_mvar": q}
        return out

    try:
        q0 = _pcc_q(net)
    except pp.LoadflowNotConverged:
        return ReactiveResult(False, math.nan, math.nan, 0.0, 0.0, {}, False)
    lim = req.q_limit_mvar
    if abs(q0) <= lim + tol:
        return ReactiveResult(True, q0, q0, 0.0, 0.0, {}, True, report({}), 0.0)

    edge = math.copysign(lim, q0)
    sign = 1.0 if q0 > 0 else -1.0                # +: the campus must inject (capacitive)
    cap = total_head + total_stat
    state = {"q_inv": 0.0, "q_comp": 0.0, "unit_q": {}}

    def allocate(target, with_residual, one_way=False):
        if one_way:                                # trim: back off, never past zero
            target = max(target, 0.0) if sign > 0 else min(target, 0.0)
        q_inv = _clamp(target, total_head)
        rest = target - q_inv
        q_stat = _clamp(rest, total_stat) if total_stat > 0 else 0.0
        q_comp = rest - q_stat if with_residual else 0.0
        share = q_inv / total_head if total_head > 0 else 0.0
        unit_q = {uid: share * h for uid, h in head.items()}
        for uid, q in unit_q.items():
            net.sgen.at[sgen_idx[uid], "q_mvar"] = q
        for n, r in statcom.items():
            net.sgen.at[sgen_idx[n], "q_mvar"] = q_stat * r / total_stat
        if comp is not None:
            net.sgen.at[comp, "q_mvar"] = q_comp
        state.update(q_inv=q_inv, q_comp=q_comp, unit_q=unit_q)
        return q_inv + q_stat

    def settle(target, with_residual, one_way=False):
        """Iterate the continuous sources onto the band edge; the PCC Q."""
        q_now = math.nan
        for _ in range(max_iter):
            supplied = allocate(target, with_residual, one_way)
            q_now = _pcc_q(net)
            err = q_now - edge
            if abs(err) <= tol:
                break
            if not with_residual and abs(supplied) >= cap - 1e-12 and err * sign > 0:
                break                              # every continuous source at its limit, still short
            target += err
        return q_now

    kind = "capacitor_bank" if sign > 0 else "shunt_reactor"
    ladder = [str(n) for n, c in comp_df.iterrows() if c["kind"] == kind for _ in range(int(c["steps"]))]
    steps = {}

    def switch(k):
        steps.clear()
        for n in ladder[:k]:
            steps[n] = steps.get(n, 0) + 1
        for n in set(ladder):
            net.shunt.at[shunt_idx[n], "step"] = steps.get(n, 0)

    try:
        if not ladder:
            q_now = settle(q0 - edge, residual)
        else:
            q_now = settle(q0 - edge, False)
            if abs(q_now - edge) > tol:
                full = allocate(sign * cap, False)                     # continuous at their limit
                outside = lambda q: max(abs(q) - lim - tol, 0.0)            # 0 in the band
                best_k, best_d = 0, outside(_pcc_q(net))
                for k in range(1, len(ladder) + 1):
                    switch(k)
                    d = outside(_pcc_q(net))
                    if d < best_d:
                        best_k, best_d = k, d
                    if d == 0.0 or d > best_d:
                        break                       # the fewest steps in the band, or past the best
                switch(best_k)
                allocate(full, False)
                q_now = settle(full + _pcc_q(net) - edge, residual, one_way=True)
    except pp.LoadflowNotConverged:
        return ReactiveResult(False, q0, math.nan, state["q_inv"], state["q_comp"], state["unit_q"], False)
    q_comp = state["q_comp"]
    if abs(q_comp) < 1e-9:
        q_comp = 0.0
    dispatch = report(steps)
    return ReactiveResult(True, q0, q_now, state["q_inv"], q_comp, state["unit_q"], False, dispatch,
                          sum(d["q_mvar"] for d in dispatch.values()))


def size_compensation(results: dict, criteria: SizingCriteria = SizingCriteria()) -> pd.DataFrame:
    """One row per direction (``capacitive``, ``inductive``). ``required_mvar``
    is the worst selected hour. ``recommended_mvar`` adds the sizing margin
    and rounds up to whole Mvar. A direction never needed is 0."""
    rows = []
    for direction, sign in (("capacitive", 1.0), ("inductive", -1.0)):
        worst, where = 0.0, (None, None)
        for (period, hour), r in results.items():
            if r.converged and sign * r.q_comp_mvar > worst:
                worst, where = sign * r.q_comp_mvar, (int(period), int(hour))
        rows.append({
            "direction": direction, "required_mvar": worst,
            "recommended_mvar": float(math.ceil(worst * (1 + criteria.margin) - 1e-9)) if worst > 0 else 0.0,
            "worst_period": where[0], "worst_hour": where[1], "margin": criteria.margin,
        })
    return pd.DataFrame(rows)
