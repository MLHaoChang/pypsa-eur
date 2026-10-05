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
2. **Then compensation.** The remainder is a shunt at the main MV busbar
   (``compensation_bus``: the low-voltage side of the transformers at the
   PCC). Its sign: ``+`` is capacitive (supplying Q), ``-`` is inductive
   (absorbing Q).

Transformer and cable reactive losses move with the flow, so the Q target is
corrected by the residual and the load flow re-solved until the PCC sits on
the band edge (within ``tol``).

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


def reactive_need(campus, rows: pd.DataFrame, req: ReactiveRequirement, tol: float = 0.01,
                  max_iter: int = 30) -> ReactiveResult:
    """Correct one hour into the band (see the module docstring). Works on a
    copy of the campus net."""
    work = copy.copy(campus)
    work.net = copy.deepcopy(campus.net)
    apply_hour(work, rows)
    net = work.net
    sgen_idx = dict(zip(net.sgen["name"].astype(str), net.sgen.index))
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
    bus_idx = dict(zip(net.bus["name"].astype(str), net.bus.index))
    comp = pp.create_sgen(net, bus_idx[compensation_bus(work)], p_mw=0.0, q_mvar=0.0, name="_COMP")

    try:
        q0 = _pcc_q(net)
    except pp.LoadflowNotConverged:
        return ReactiveResult(False, math.nan, math.nan, 0.0, 0.0, {}, False)
    lim = req.q_limit_mvar
    if abs(q0) <= lim + tol:
        return ReactiveResult(True, q0, q0, 0.0, 0.0, {}, True)

    edge = math.copysign(lim, q0)
    target = q0 - edge                        # Q the campus must inject (+) or absorb (-)
    q_now, unit_q, q_inv, q_comp = q0, {}, 0.0, 0.0
    for _ in range(max_iter):
        q_inv = max(-total_head, min(total_head, target))
        q_comp = target - q_inv
        share = q_inv / total_head if total_head > 0 else 0.0
        unit_q = {uid: share * h for uid, h in head.items()}
        for uid, q in unit_q.items():
            net.sgen.at[sgen_idx[uid], "q_mvar"] = q
        net.sgen.at[comp, "q_mvar"] = q_comp
        try:
            q_now = _pcc_q(net)
        except pp.LoadflowNotConverged:
            return ReactiveResult(False, q0, math.nan, q_inv, q_comp, unit_q, False)
        err = q_now - edge
        if abs(err) <= tol:
            break
        target += err
    if abs(q_comp) < 1e-9:
        q_comp = 0.0
    return ReactiveResult(True, q0, q_now, q_inv, q_comp, unit_q, False)


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
