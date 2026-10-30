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
from gridspine.templates.grid_codes import q_band_at

_SUPPLIERS = frozenset({"bess", "pv", "wind", "fuel_cell", "genset"})
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
    #: The connection capacity the band was scaled by (None when not known).
    p_ref_mw: float | None = None


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
        return ReactiveRequirement(q["value"] * p_ref_mw, q["clause"], q["source"], p_ref_mw)
    if not 0 < pf <= 1:
        raise ContractError(f"pf must be in (0, 1], got {pf}")
    return ReactiveRequirement(
        p_ref_mw * math.tan(math.acos(pf)),
        f"study connection agreement: power factor {pf:g} at the connection capacity",
        "assumed",
        p_ref_mw,
    )


#: The inverter-based resources IEEE 2800's reactive capability applies to.
IBR_KINDS = ("bess", "pv", "wind", "fuel_cell")
IBR_LABEL = ("screening at unit terminals: sqrt(S^2 - P_n^2) per inverter against the profile's q_range_ibr x P_n "
             "at rated active power; the standard states it for the plant at its reference point, so collector "
             "and transformer reactive losses are not counted")
IBR_COLUMNS = ("unit", "kind", "p_n_mw", "s_mva", "required_mvar", "available_mvar", "shortfall_mvar", "status",
               "detail", "clause", "source", "basis")


def _s_mva(u) -> float:
    """A unit's inverter S in MVA; NaN when it has none (None, should a rating ever be stored that way, is
    no rating, as NaN is)."""
    s = u["s_mva"]
    return float(s) if s is not None and s == s else math.nan


def _circle_q(s: float, p: float) -> float:
    """The inverter circle: the Q a unit of apparent power ``s`` has left at active power ``p``
    (0 past P = S; NaN for an unknown ``s``)."""
    return math.sqrt(max(s ** 2 - p ** 2, 0.0))


def pcs_s_mva(p_mw: float, s_x_mva: float | None) -> float:
    """A battery PCS's apparent power: rated ``p_mw`` plus the oversize ``s_x_mva`` the sizing LP chose (AS-05;
    None or NaN is none). Set it as the unit's ``s_mva`` so the exact circle ``_circle_q`` checks what the LP's
    inner bound credited."""
    if s_x_mva is None or s_x_mva != s_x_mva:
        return float(p_mw)
    if s_x_mva < 0:
        raise ValueError(f"PCS oversize {s_x_mva!r} MVA must be >= 0")
    return float(p_mw) + float(s_x_mva)


def statcom_split(q_statcom_mvar: float, q_steady_need_mvar: float) -> tuple[float, float]:
    """``(steady, dynamic)``: a STATCOM of ``q_statcom_mvar`` serves the steady need first, and only what is left
    is free for flicker (AS-05). The two slices sum to the rating, so one Mvar is never counted twice."""
    steady = min(max(q_steady_need_mvar, 0.0), max(q_statcom_mvar, 0.0))
    return steady, max(q_statcom_mvar, 0.0) - steady


def _ibr_ratings(campus):
    """``(uid, kind, P_n, S)`` of every IBR unit, S by ``_s_mva``."""
    for uid, u in campus.units.iterrows():
        if u["kind"] in IBR_KINDS:
            yield uid, u["kind"], float(u["p_mw"]), _s_mva(u)


def ibr_q_capability(campus, profile: dict):
    """IEEE 2800's reactive capability of the on-site IBR (``IBR_LABEL``):
    one row per BESS, PV or wind unit, then a plant ``TOTAL`` row whose
    status and shortfall are max(0, sum required - sum available) over the
    rated units (NaN when none is rated), or None when the profile states no
    ``q_range_ibr``. A unit without a rating is ``reported``, never ``pass``;
    a shortfall ``fail`` row says the inverter rating or the STATCOM that
    would cover it. Every row carries ``IBR_LABEL`` in its ``basis`` column."""
    if "q_range_ibr" not in profile:
        return None
    ibr = profile["q_range_ibr"]
    frac = float(ibr["value"])
    oversize = math.sqrt(1.0 + frac ** 2)
    rows = []
    for uid, kind, p_n, s in _ibr_ratings(campus):
        base = {"unit": str(uid), "kind": kind, "p_n_mw": p_n, "s_mva": s, "clause": ibr["clause"],
                "source": ibr["source"]}
        if not (p_n > 0 and math.isfinite(s) and s > 0):
            rows.append({**base, "required_mvar": frac * p_n if p_n > 0 else math.nan, "available_mvar": math.nan,
                         "shortfall_mvar": math.nan, "status": "reported",
                         "detail": f"{uid}: rating not given (P_n and the inverter S are needed)"})
            continue
        req = frac * p_n
        avail = _circle_q(s, p_n)
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
    # IEEE 2800 states the requirement for the plant at its reference point:
    # the TOTAL compares the sums over the rated units, and names each unit
    # that cannot carry its own share (its row keeps its own fail).
    rated = [r for r in rows if r["status"] != "reported"]
    unrated = len(rated) < len(rows)
    if rated:
        p_t = sum(r["p_n_mw"] for r in rated)
        req_t = sum(r["required_mvar"] for r in rated)
        avail_t = sum(r["available_mvar"] for r in rated)
        short_t = max(req_t - avail_t, 0.0)
        short_t = short_t if short_t > 1e-9 else 0.0
    else:
        p_t = req_t = avail_t = short_t = math.nan
    status = "reported" if unrated else "fail" if short_t > 0 else "pass"
    if rated:
        detail = (f"plant {'meets' if short_t == 0 else 'falls short'}: {avail_t:.2f} Mvar available against "
                  f"{req_t:.2f} Mvar required ({frac:g} x rating at rated P)"
                  + (f", {short_t:.2f} Mvar short" if short_t > 0 else ""))
        alone = [f"{r['unit']} alone falls {r['shortfall_mvar']:.2f} Mvar short" for r in rated
                 if r["status"] == "fail"]
        if alone:
            detail += "; " + "; ".join(alone)
    else:
        detail = "no unit has a rating (P_n and the inverter S are needed)"
    if unrated and rated:
        detail += "; a unit without a rating is not counted"
    rows.append({"unit": "TOTAL", "kind": "ibr", "p_n_mw": p_t, "s_mva": math.nan, "required_mvar": req_t,
                 "available_mvar": avail_t, "shortfall_mvar": short_t, "status": status, "detail": detail,
                 "clause": ibr["clause"], "source": ibr["source"]})
    for r in rows:
        r["basis"] = IBR_LABEL
    return pd.DataFrame(rows, columns=list(IBR_COLUMNS))


#: The basis of every ``ibr_q_curve`` row.
IBR_CURVE_LABEL = ("Screening at unit terminals: √(S² − P²) per inverter against the grid code's per-unit "
                   "curve × rated P, at each checked active power; collector and transformer reactive losses are "
                   "not included.")
#: The columns of ``ibr_q_curve``'s table.
IBR_CURVE_COLUMNS = ("unit", "kind", "p_pu", "p_mw", "s_mva", "q_min_req_mvar", "q_max_req_mvar", "available_mvar",
                     "shortfall_mvar", "status", "detail", "clause", "source", "basis")


def ibr_q_curve(campus, profile: dict):
    """Each IBR unit's inverter circle against the profile's per-unit
    reactive capability curve ``q_capability_unit`` (``IBR_CURVE_LABEL``),
    or None when the profile has no curve or the campus no IBR unit.

    One row per unit and checked active power ``p_pu``: the curve's
    breakpoints within 0..1 pu, and rated P (1 pu). The required band is
    ``[q_min_pu, q_max_pu] x P_n`` in the generator convention (+ the unit
    delivers, over-excited; - it absorbs), unlike ``q_capability_pq``,
    whose + is the grid supplying the site, per unit of ``p_ref``. The
    available Q at P = p_pu x P_n is sqrt(S^2 - P^2) either way, so each
    side is checked on its own. The requirement is linear
    between breakpoints and the circle concave in P, so their difference is
    concave on each segment and least at a segment end: the breakpoints and
    1 pu cover the whole curve. That needs P <= S on the curve: a unit with
    S < P_n has no Q above P = S (the clamp at 0 bends the circle there), so
    a shortfall between breakpoints can be missed; rated P then fails anyway
    whenever the curve asks for Q there. A curve that does not reach 1 pu gives a
    ``reported`` row at 1 pu, never a silent skip. Points below 0 pu
    (storage charging) are outside this first cut. A unit without P_n or S
    is one ``reported`` row, "capability unknown", never ``pass``.
    ``q_range_ibr`` stays its own table (``ibr_q_capability``)."""
    lim = profile.get("q_capability_unit")
    if not lim:
        return None
    breaks = [p for p, _, _ in lim["points"] if 0.0 <= p <= 1.0]
    p_check = sorted({*breaks, 1.0})
    rows = []
    for uid, kind, p_n, s in _ibr_ratings(campus):
        base = {"unit": str(uid), "kind": kind, "s_mva": s, "clause": lim["clause"], "source": lim["source"],
                "basis": IBR_CURVE_LABEL}
        unknown = {"q_min_req_mvar": math.nan, "q_max_req_mvar": math.nan, "available_mvar": math.nan,
                   "shortfall_mvar": math.nan, "status": "reported"}
        if not (p_n > 0 and math.isfinite(s) and s > 0):
            rows.append({**base, **unknown, "p_pu": math.nan, "p_mw": math.nan,
                         "detail": f"capability unknown: {uid} has no rating (P_n and the inverter S are needed)"})
            continue
        for p_pu in p_check:
            p = p_pu * p_n
            band = q_band_at(lim, p_pu)
            if band is None:
                rows.append({**base, **unknown, "p_pu": p_pu, "p_mw": p,
                             "detail": f"{uid} at P {p_pu:g} pu: the curve does not state rated P, so it is "
                                       "not checked there"})
                continue
            lo, hi = band[0] * p_n, band[1] * p_n
            avail = _circle_q(s, p)
            short_out = hi - avail if hi - avail > 1e-9 else 0.0
            short_in = -lo - avail if -lo - avail > 1e-9 else 0.0
            short = max(short_out, short_in)
            if not short:
                detail = (f"{uid} at P {p_pu:g} pu ({p:.2f} MW) covers {lo:.2f} to {hi:.2f} Mvar "
                          f"with \u00b1{avail:.2f} Mvar")
            else:
                sides = [f"{x:.2f} Mvar short to {what}" for x, what in
                         ((short_out, "deliver"), (short_in, "absorb")) if x]
                detail = (f"{uid} at P {p_pu:g} pu ({p:.2f} MW) has \u00b1{avail:.2f} Mvar for {lo:.2f} to "
                          f"{hi:.2f} Mvar: {' and '.join(sides)}")
            rows.append({**base, "p_pu": p_pu, "p_mw": p, "q_min_req_mvar": lo, "q_max_req_mvar": hi,
                         "available_mvar": avail, "shortfall_mvar": short, "status": "fail" if short else "pass",
                         "detail": detail})
    if not rows:
        return None
    return pd.DataFrame(rows, columns=list(IBR_CURVE_COLUMNS))


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
        head[uid] = _circle_q(_s_mva(u), p)
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


def size_compensation(results: dict, criteria: SizingCriteria = SizingCriteria(),
                      q_dyn_mvar: float = 0.0) -> pd.DataFrame:
    """One row per direction (``capacitive``, ``inductive``). ``required_mvar``
    is the worst selected hour. ``recommended_mvar`` adds the sizing margin
    and rounds up to whole Mvar. A direction never needed is 0. A
    ``q_dyn_mvar`` > 0 (the STATCOM's dynamic slice for flicker, AS-05) adds a
    ``dynamic`` row of its own, so it is never summed into the steady rows."""
    if not q_dyn_mvar >= 0:
        raise ValueError(f"q_dyn_mvar {q_dyn_mvar!r} must be >= 0")
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
    if q_dyn_mvar > 0:
        rows.append({"direction": "dynamic", "required_mvar": float(q_dyn_mvar),
                     "recommended_mvar": float(math.ceil(q_dyn_mvar * (1 + criteria.margin) - 1e-9)),
                     "worst_period": None, "worst_hour": None, "margin": criteria.margin})
    return pd.DataFrame(rows)
