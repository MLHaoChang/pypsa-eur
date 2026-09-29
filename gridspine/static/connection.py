"""Increment 10: the connection-point assessment of one facility.

A **facility** is a load plus a separate on-site unit (BESS or generator) at
one connection point. That is the owner's decision (2026-09-29): a data
centre's load can drop while its BESS stays connected, and the trip checks
have to see that.

Each check runs on a net that already carries the hour being studied, with
the facility placed by the same pro-rata balancing as increment 9, and is
held to a grid-code profile's tagged limits (``templates/grid_codes.py``).
The voltage band applied at each bus is the profile's band for that bus's
nominal voltage.

``connection``
    Facility in, net import balanced pro-rata: no new or worsened violation,
    intact and under every N-1 outage (increment 9's rule, with the profile's
    voltage band in place of the screen's generic 0.9-1.1 pu).
``energisation``
    Facility off to on with **no re-dispatch** (the slack takes it: an
    instantaneous step). The rapid voltage change at the POC.
``load_trip``
    From the connected state, the load drops, the on-site unit STAYS, and
    nothing re-dispatches. RVC at the POC, plus no new or worsened band or
    thermal violation.
``facility_trip``
    As ``load_trip``, with the on-site unit dropping too.
``q_lead`` / ``q_lag``
    The facility injecting / absorbing the DCC Art. 15(1)(a) reactive range
    (the profile's ``q_range_demand`` x the larger of max import or export)
    at the POC. No new or worsened band violation at either end.
``scr_onsite`` / ``scr_load``
    Minimum Sk'' at the POC over the on-site converter's MVA and the load's
    MVA. REPORTED against ``static/strength.py``'s bands, not gated, as that
    module rules for every SCR.

These are steady-state screening checks, not a compliance certificate: fault
ride-through and dynamic support need RMS/EMT, which the handoff bundle's
REGCA1/REECA1 records are for. The caller's net is never mutated.
"""
import copy
import dataclasses
import math

import numpy as np
import pandapower as pp
import pandas as pd

from gridspine.schema.connection import CHECKS, validate_connection  # noqa: F401 (re-export)
from gridspine.schema.contingency import validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.capacity import (
    DEFAULT_CRITERIA,
    _branch_ids,
    _breaches,
    _bus_index,
    _compare,
    _evaluate,
    _headroom,
)
from gridspine.static.contingency import branch_loading_pct
from gridspine.static.strength import band as scr_band
from gridspine.templates.grid_codes import band_for

PROBE_LOAD = "FACILITY_LOAD"
PROBE_ONSITE = "FACILITY_ONSITE"
SCR_CLAUSE = (
    "reported against the SCR bands of static/strength.py (<2 very weak, 2-3 weak, "
    "3-5 moderate, >=5 strong); not gated"
)
CONNECTION_CLAUSE = (
    "no new or worsened violation, intact and N-1 (increment 9's rule), with the "
    "profile's steady-state voltage band at every bus"
)


@dataclasses.dataclass(frozen=True)
class Facility:
    bus: str
    load_mw: float
    load_pf: float = 0.98
    onsite_mw: float = 0.0
    onsite_converter: bool = True


def _check_facility(net, fac: Facility) -> None:
    _bus_index(net, fac.bus)
    for name in ("load_mw", "onsite_mw"):
        v = getattr(fac, name)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ContractError(f"facility {name} must be finite and >= 0, got {v!r}")
    if not 0 < fac.load_pf <= 1:
        raise ContractError(f"facility load_pf must lie in (0, 1], got {fac.load_pf}")
    if fac.load_mw == 0 and fac.onsite_mw == 0:
        raise ContractError("facility is empty: both load_mw and onsite_mw are 0")


def _rebalance(net, net_import_mw: float) -> None:
    """Increment 9's pro-rata re-dispatch for a net import (positive) or export."""
    if net_import_mw == 0:
        return
    kind = "load" if net_import_mw > 0 else "generation"
    room = _headroom(net, kind)
    total = float(room.sum())
    if total > 0:
        sign = 1.0 if kind == "load" else -1.0
        share = room * min(1.0, abs(net_import_mw) / total)
        net.gen["p_mw"] = net.gen["p_mw"].astype(float) + sign * share


def _place(net, fac: Facility, *, load=True, onsite=True, q_mvar=None) -> None:
    b = _bus_index(net, fac.bus)
    if load and (fac.load_mw > 0 or q_mvar is not None):
        q = fac.load_mw * math.tan(math.acos(fac.load_pf)) if q_mvar is None else q_mvar
        pp.create_load(net, bus=b, p_mw=fac.load_mw, q_mvar=q, name=PROBE_LOAD)
    if onsite and fac.onsite_mw > 0:
        pp.create_sgen(net, bus=b, p_mw=fac.onsite_mw, q_mvar=0.0, name=PROBE_ONSITE)


def _drop(net, *, load=False, onsite=False) -> None:
    if load:
        net.load.loc[net.load["name"] == PROBE_LOAD, "in_service"] = False
    if onsite and len(net.sgen):
        net.sgen.loc[net.sgen["name"] == PROBE_ONSITE, "in_service"] = False


def _intact(work):
    """(loading, vm) of one AC solve, or None when it does not converge."""
    try:
        pp.runpp(work)
    except pp.LoadflowNotConverged:
        return None
    loading = branch_loading_pct(work, work.res_line["i_from_ka"].values, work.res_trafo["i_hv_ka"].values)
    return np.asarray(loading, dtype=float), work.res_bus["vm_pu"].to_numpy(dtype=float)


def _words(breach) -> str:
    kind, element, contingency, _score, pre = breach[:5]
    after = f" after losing {contingency}" if contingency else ""
    if kind.startswith("thermal"):
        text = f"overload of {element}{after}"
    elif kind.startswith("v_low"):
        text = f"low voltage at {element}{after}"
    elif kind.startswith("v_high"):
        text = f"high voltage at {element}{after}"
    else:
        text = f"no load-flow solution{after}"
    return text + (" (already violated, worsened)" if pre else "")


def _row(check, status, value, unit, limit, detail, clause, source):
    return {"check": check, "status": status, "value": value, "unit": unit, "limit": limit,
            "detail": detail, "clause": clause, "source": source}


def assess_connection(net, fac: Facility, contingencies, profile: dict, criteria=DEFAULT_CRITERIA,
                      sk_min_mva: float | None = None) -> pd.DataFrame:
    """Every check for ``fac`` on ``net`` (which carries the hour), held to
    ``profile``. ``sk_min_mva`` is the IEC 60909 minimum Sk'' at the POC; the
    SCR rows are omitted without it."""
    _check_facility(net, fac)
    cset = validate_contingency_set(contingencies)
    pos = _bus_index(net, fac.bus)
    poc = list(net.bus.index).index(pos)
    bands = [band_for(profile, float(kv)) for kv in net.bus["vn_kv"]]
    crit = dataclasses.replace(
        criteria,
        v_min_pu=np.array([b["v_min"] for b in bands]),
        v_max_pu=np.array([b["v_max"] for b in bands]),
    )
    poc_band = bands[poc]
    ids, names = _branch_ids(net), list(net.bus["name"])
    rvc = profile["rvc_limit_pct"]
    net_import = fac.load_mw - fac.onsite_mw
    rows = []

    # connection: hour state vs connected state, intact and N-1
    base = _evaluate(copy.deepcopy(net), cset)
    if base is None:
        raise ContractError("the hour's base case does not converge; nothing to assess against")
    connected = copy.deepcopy(net)
    _rebalance(connected, net_import)
    _place(connected, fac)
    trial = _evaluate(copy.deepcopy(connected), cset)
    found = [("ac_divergence", None, None, math.inf, False)] if trial is None else _breaches(base, trial, crit, ids, names)
    worst = max(found, key=lambda x: x[3]) if found else None
    rows.append(_row("connection", "fail" if found else "pass", float(len(found)), "violations", None,
                     _words(worst) if worst else "no new or worsened violation", CONNECTION_CLAUSE,
                     poc_band["source"]))

    # voltage steps: energisation (no re-dispatch) and the two trips
    def step(check, before_net, after_net):
        before, after = _intact(before_net), _intact(after_net)
        if before is None or after is None:
            rows.append(_row(check, "fail", None, "%", rvc["value"], "no load-flow solution",
                             rvc["clause"], rvc["source"]))
            return
        dv = (after[1][poc] - before[1][poc]) * 100.0
        breaches = _compare(before, after, crit, "intact", None, ids, names)
        problems = []
        if abs(dv) > rvc["value"]:
            problems.append(f"voltage step {dv:+.2f} % at {fac.bus} exceeds {rvc['value']:g} %")
        problems += [_words(b) for b in breaches]
        detail = "; ".join(problems) if problems else (
            f"voltage step {dv:+.2f} % at {fac.bus}; no new or worsened violation")
        rows.append(_row(check, "fail" if problems else "pass", dv, "%", rvc["value"], detail,
                         rvc["clause"], rvc["source"]))

    energised = copy.deepcopy(net)
    _place(energised, fac)
    step("energisation", copy.deepcopy(net), energised)
    after_load = copy.deepcopy(connected)
    _drop(after_load, load=True)
    step("load_trip", copy.deepcopy(connected), after_load)
    after_all = copy.deepcopy(connected)
    _drop(after_all, load=True, onsite=True)
    step("facility_trip", copy.deepcopy(connected), after_all)

    # reactive range at the POC
    q_frac = profile["q_range_demand"]
    p_ref = max(fac.load_mw, fac.onsite_mw)
    q = q_frac["value"] * p_ref
    reference = _intact(copy.deepcopy(connected))
    for check, q_mvar, word, limit in (("q_lead", -q, "injecting", poc_band["v_max"]),
                                       ("q_lag", q, "absorbing", poc_band["v_min"])):
        w = copy.deepcopy(net)
        _rebalance(w, net_import)
        _place(w, fac, q_mvar=q_mvar)
        state = _intact(w)
        what = (f"facility {word} {q:.1f} Mvar at {fac.bus} "
                f"({q_frac['value']:g} x {p_ref:.1f} MW)")
        if state is None or reference is None:
            rows.append(_row(check, "fail", None, "pu", limit, f"{what}: no load-flow solution",
                             q_frac["clause"], q_frac["source"]))
            continue
        breaches = _compare(reference, state, crit, "intact", None, ids, names)
        detail = what + ("; " + "; ".join(_words(b) for b in breaches) if breaches
                         else f"; POC at {state[1][poc]:.3f} pu, within {poc_band['v_min']:g}-{poc_band['v_max']:g} pu")
        rows.append(_row(check, "fail" if breaches else "pass", float(state[1][poc]), "pu", limit,
                         detail, q_frac["clause"], q_frac["source"]))

    # SCR, reported
    if sk_min_mva is not None:
        if fac.onsite_mw > 0 and fac.onsite_converter:
            v = sk_min_mva / fac.onsite_mw
            rows.append(_row("scr_onsite", "reported", v, "", None,
                             f"Sk''min {sk_min_mva:.0f} MVA / on-site converter {fac.onsite_mw:.0f} MVA: {scr_band(v)}",
                             SCR_CLAUSE, "assumed"))
        if fac.load_mw > 0:
            s_load = fac.load_mw / fac.load_pf
            v = sk_min_mva / s_load
            rows.append(_row("scr_load", "reported", v, "", None,
                             f"Sk''min {sk_min_mva:.0f} MVA / load {s_load:.0f} MVA: {scr_band(v)}",
                             SCR_CLAUSE, "assumed"))
    return validate_connection(pd.DataFrame(rows))
