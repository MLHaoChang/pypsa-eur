"""Increment 9: connection capacity at a bus: how many MW can connect here,
and what binds at that number.

**Definition.** Capacity at bus B, for a connection of kind ``load`` or
``generation``, is the largest P >= 0 such that adding P at B creates NO NEW
violation and WORSENS NO EXISTING ONE, intact and under every N-1 outage in
the contingency set. A constraint satisfied at P = 0 must stay satisfied; one
already violated at P = 0 may not get worse by more than a small tolerance.
A base case that already carries an N-1 overload therefore neither zeroes
every answer nor lets a new site push that overload deeper.

**Criteria** are the N-1 screen's own, imported rather than restated: branch
loading <= ``LOADING_MAX_PCT`` (from/HV-side current over rating, the
screen's definition) and bus voltage within [``V_MIN_PU``, ``V_MAX_PU``].
Islanding outages are topology, not a limit a connection can move, so they
are skipped, exactly as the screen flags rather than scores them.

**Balancing (owner's decision, 2026-09-29).** The added MW are supplied (for a
load) or displaced (for generation) by the COMMITTED synchronous units in
proportion to their available headroom: upward (``max_p_mw - p_mw``) for a
load, downward (``p_mw - min_p_mw``) for generation. The slack takes only what
they cannot, plus losses. Slack-only balancing was rejected because on case39
it routes every MW toward BUS_31 and makes the answer depend on where the
slack sits.

**Two methods.**

* ``capacity_dc``: closed form over the PTDF/LODF already built for the N-2
  prune. For each branch and each outage the headroom over the sensitivity
  bounds P; the minimum is the estimate. It costs one matrix pass per hour,
  so it can cover every bus. It is an ESTIMATE: no voltage, no reactive power,
  no losses, branch outages only. The N-2 prune measured that DC
  underestimates loading on case39's critical branch.
* ``capacity_ac``: bisection on P. Each trial is a full AC load flow plus the
  lightsim2grid N-1 batch the screen runs (``contingency._branch_outcomes`` /
  ``_unit_outcomes``), compared element by element with P = 0.

The caller's net is never mutated. The net must already carry the hour being
studied (``loadflow.apply_snapshot``); this module does not apply dispatch.
"""
import copy
import dataclasses
import math

import numpy as np
import pandapower as pp
import pandas as pd

from gridspine.schema.capacity import BINDING_KINDS, KINDS, validate_capacity  # noqa: F401 (re-export)
from gridspine.schema.contingency import validate_contingency_set
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency import (
    LOADING_MAX_PCT,
    V_BAND_PU,
    V_MAX_PU,
    V_MIN_PU,
    _branch_outcomes,
    _unit_outcomes,
    branch_loading_pct,
    gridmodel_for,
)
from gridspine.static.loadflow import branch_keys
from gridspine.static.lodf import dc_base

PROBE_NAME = "CAPACITY_PROBE"

#: What every capacity number in a bundle rests on; the study ledger carries it.
CAPACITY_LEDGER = (
    "connection capacity: the most MW at a bus that creates no new violation and "
    "worsens no existing one (by more than 0.5 % loading / 0.002 pu), intact and "
    "under every N-1 outage, on the N-1 screen's criteria (100 % loading, "
    "0.9-1.1 pu)",
    "connection capacity balancing: added MW are supplied (load) or displaced "
    "(generation) by the committed synchronous units pro-rata to their headroom; "
    "the slack takes what they cannot (owner's decision, 2026-09-29)",
    "connection capacity: a new load at power factor 0.98 lagging, new generation "
    "at unity; the DC figure is an estimate (no voltage, reactive power or losses, "
    "branch outages only), the AC figure is a bisection to 1 MW",
)


@dataclasses.dataclass(frozen=True)
class CapacityCriteria:
    loading_max_pct: float = LOADING_MAX_PCT
    v_min_pu: float = V_MIN_PU
    v_max_pu: float = V_MAX_PU
    #: How much an ALREADY-violated constraint may move before it counts as
    #: worsened: numerical noise between two AC solves, not an allowance.
    worsen_tol_pct: float = 0.5
    worsen_tol_pu: float = 0.002
    tol_mw: float = 1.0
    cap_mw: float = 2000.0
    #: 0.98 lagging: typical of a data centre with power-factor correction.
    load_pf: float = 0.98


DEFAULT_CRITERIA = CapacityCriteria()


def _bus_index(net, bus):
    hit = net.bus.index[net.bus["name"] == bus]
    if len(hit) != 1:
        raise ContractError(f"unknown bus {bus!r}: not a bus name on this network")
    return hit[0]


def _check_kind(kind):
    if kind not in KINDS:
        raise ContractError(f"connection kind must be one of {list(KINDS)}, got {kind!r}")


def _headroom(net, kind) -> pd.Series:
    """Per committed gen (pandapower index): the MW it can move in the
    direction the connection needs. Decommitted units have none; a missing
    limit counts as none rather than as unlimited."""
    gen = net.gen
    on = gen["in_service"].astype(bool)

    def col(name):
        return gen[name].astype(float) if name in gen.columns else pd.Series(np.nan, index=gen.index)

    if kind == "load":
        room = col("max_p_mw") - col("p_mw")
    else:
        room = col("p_mw") - col("min_p_mw")
    return room.fillna(0.0).clip(lower=0.0).where(on, 0.0)


def apply_connection(net, bus, p_mw, kind, pf=None) -> None:
    """Add ``p_mw`` of ``kind`` at ``bus`` and re-dispatch the committed units
    pro-rata to headroom. MUTATES ``net``; the searches call it on copies.

    A load gets power factor ``pf`` (lagging); generation is at unity.
    """
    b = _bus_index(net, bus)
    _check_kind(kind)
    p_mw = float(p_mw)
    if not math.isfinite(p_mw) or p_mw < 0:
        raise ContractError(f"p_mw must be finite and >= 0, got {p_mw}")
    room = _headroom(net, kind)
    if kind == "load":
        pf = DEFAULT_CRITERIA.load_pf if pf is None else float(pf)
        if not 0 < pf <= 1:
            raise ContractError(f"power factor must lie in (0, 1], got {pf}")
        pp.create_load(net, bus=b, p_mw=p_mw, q_mvar=p_mw * math.tan(math.acos(pf)), name=PROBE_NAME)
        sign = 1.0
    else:
        pp.create_sgen(net, bus=b, p_mw=p_mw, q_mvar=0.0, name=PROBE_NAME)
        sign = -1.0
    total = float(room.sum())
    if total > 0 and p_mw > 0:
        share = room * min(1.0, p_mw / total)
        net.gen["p_mw"] = net.gen["p_mw"].astype(float) + sign * share


# ---------------------------------------------------------------------------
# AC
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _State:
    loading: np.ndarray        # per branch, branch_keys order
    vm: np.ndarray             # per bus, bus-table order
    n1: dict                   # contingency_id -> (converged, islanded, loading, vm)


def _evaluate(work, cset):
    try:
        pp.runpp(work)
    except pp.LoadflowNotConverged:
        return None
    nl = len(work.line)
    loading = branch_loading_pct(work, work.res_line["i_from_ka"].values, work.res_trafo["i_hv_ka"].values)
    vm = work.res_bus["vm_pu"].to_numpy(dtype=float)
    gm = gridmodel_for(work)
    v0 = work._ppc["internal"]["V"]
    n1 = {}
    n1.update(_branch_outcomes(work, gm, v0, cset[cset["kind"] == "branch"], nl))
    n1.update(_unit_outcomes(work, gm, v0, cset[cset["kind"] == "unit"], nl))
    return _State(loading=np.asarray(loading, dtype=float), vm=vm, n1=n1)


def _excess(loading, vm, crit):
    return (
        ("thermal", np.asarray(loading, dtype=float) - crit.loading_max_pct, crit.worsen_tol_pct, 100.0),
        ("v_low", crit.v_min_pu - np.asarray(vm, dtype=float), crit.worsen_tol_pu, V_BAND_PU),
        ("v_high", np.asarray(vm, dtype=float) - crit.v_max_pu, crit.worsen_tol_pu, V_BAND_PU),
    )


def _compare(base, trial, crit, scenario, contingency, branch_ids, bus_names):
    """Breaches of the no-new, no-worse rule in one scenario, as
    (binding_kind, element, contingency, score). ``score`` is the growth of
    the excess in the screen's severity units, so thermal and voltage compare."""
    out = []
    for (name, b, tol, unit), (_n, t, _t, _u) in zip(
        _excess(base[0], base[1], crit), _excess(trial[0], trial[1], crit)
    ):
        allowed = np.where(b > 0, b + tol, 0.0)
        with np.errstate(invalid="ignore"):
            breach = t > allowed + 1e-9
        breach &= ~np.isnan(t)
        labels = branch_ids if name == "thermal" else bus_names
        for i in np.flatnonzero(breach):
            growth = (t[i] - max(b[i], 0.0)) / unit
            out.append((f"{name}_{scenario}", labels[i], contingency, float(growth)))
    return out


def _breaches(base: _State, trial: _State, crit, branch_ids, bus_names):
    out = _compare((base.loading, base.vm), (trial.loading, trial.vm), crit,
                   "intact", None, branch_ids, bus_names)
    for cid, (conv, isl, ld, vm) in base.n1.items():
        if not conv or isl:
            continue          # islanding is topology; a base divergence has no level to compare
        t_conv, t_isl, t_ld, t_vm = trial.n1[cid]
        if not t_conv or t_isl:
            out.append(("n1_divergence", None, cid, math.inf))
            continue
        out += _compare((ld, vm), (t_ld, t_vm), crit, "n1", cid, branch_ids, bus_names)
    return out


def _branch_ids(net):
    return [f"{k.from_bus}-{k.to_bus}-{k.ckt}" for k in branch_keys(net).itertuples(index=False)]


def capacity_ac(net, bus, kind, contingencies, criteria=DEFAULT_CRITERIA, start_mw=None) -> dict:
    """AC capacity at ``bus`` by bisection to ``criteria.tol_mw``.

    Returns ``capacity_mw`` (the highest P found feasible, never above
    ``cap_mw``), the binding constraint at the first infeasible P, and the
    number of AC evaluations. ``start_mw`` (the DC estimate, typically) only
    seeds the bracket; it never decides the answer.
    """
    _bus_index(net, bus)
    _check_kind(kind)
    cset = validate_contingency_set(contingencies)
    if (cset["order"] != 1).any():
        raise ContractError("capacity_ac takes an N-1 set; got rows with order != 1")
    base = _evaluate(copy.deepcopy(net), cset)
    if base is None:
        raise ContractError("the base case does not converge; there is no capacity to measure against it")
    branch_ids, bus_names = _branch_ids(net), list(net.bus["name"])
    crit = criteria
    count = 0

    def trial(p):
        nonlocal count
        count += 1
        work = copy.deepcopy(net)
        apply_connection(work, bus, p, kind, pf=crit.load_pf)
        state = _evaluate(work, cset)
        if state is None:
            return [("ac_divergence", None, None, math.inf)]
        return _breaches(base, state, crit, branch_ids, bus_names)

    def result(cap, binding):
        kind_, element, contingency, _score = binding
        return {"capacity_mw": float(cap), "binding_kind": kind_, "binding_element": element,
                "binding_contingency": contingency, "evaluations": count}

    lo = 0.0
    hi = float(start_mw) if start_mw and start_mw > crit.tol_mw else 10.0 * crit.tol_mw
    hi = min(hi, crit.cap_mw)
    found = trial(hi)
    while not found:
        lo = hi
        if hi >= crit.cap_mw:
            return result(crit.cap_mw, ("none_up_to_cap", None, None, 0.0))
        hi = min(2.0 * hi, crit.cap_mw)
        found = trial(hi)
    while hi - lo > crit.tol_mw:
        mid = 0.5 * (lo + hi)
        b = trial(mid)
        if b:
            hi, found = mid, b
        else:
            lo = mid
    return result(lo, max(found, key=lambda x: x[3]))


# ---------------------------------------------------------------------------
# DC
# ---------------------------------------------------------------------------

def _bound(F, S, R):
    """Largest P >= 0 with |F + P S| <= max(R, |F|), element-wise; inf where S = 0."""
    L = np.maximum(R, np.abs(F))
    with np.errstate(divide="ignore", invalid="ignore"):
        up = np.where(S > 1e-12, (L - F) / S, np.where(S < -1e-12, (L + F) / -S, np.inf))
    return np.clip(np.nan_to_num(up, nan=np.inf), 0.0, None)


def capacity_dc(net, buses, kind, criteria=DEFAULT_CRITERIA) -> pd.DataFrame:
    """Closed-form DC estimate for each bus in ``buses``, branch outages only.

    Index: bus name. Columns: ``dc_estimate_mw`` (capped at ``cap_mw``),
    ``dc_binding_element``, ``dc_binding_contingency`` (None when the intact
    case binds, and both None when nothing binds up to the cap).
    """
    _check_kind(kind)
    for b in buses:
        _bus_index(net, b)
    state = dc_base(net)
    n_bus = len(state.bus_names)
    pos = {name: i for i, name in enumerate(state.bus_names)}
    ids = [f"{k.from_bus}-{k.to_bus}-{k.ckt}" for k in state.branch_keys.itertuples(index=False)]

    room = _headroom(net, kind)
    bal = np.zeros(n_bus)
    if room.sum() > 0:
        bus_name = net.bus["name"]
        for i, r in room.items():
            if r > 0:
                bal[pos[bus_name.at[net.gen.at[i, "bus"]]]] += r / room.sum()
    else:
        bal[state.ref_bus] = 1.0          # the slack's column is zero: pure slack balancing
    sign = -1.0 if kind == "load" else 1.0  # a load withdraws at B

    F0, R = state.flows_mw, state.rating_mva
    live = np.flatnonzero(~state.islanding)
    Lk = state.lodf[:, live]
    Fk = F0[:, None] + Lk * F0[None, live]
    rows = {}
    for b in buses:
        inj = -sign * bal
        inj[pos[b]] += sign
        s = state.ptdf @ inj
        intact = _bound(F0, s, R)
        n1 = _bound(Fk, s[:, None] + Lk * s[None, live], R[:, None])
        for j, k in enumerate(live):
            n1[k, j] = np.inf            # the outaged branch carries nothing
        best_i = int(np.argmin(intact))
        best = (intact[best_i], ids[best_i], None)
        if n1.size:
            l, j = np.unravel_index(int(np.argmin(n1)), n1.shape)
            if n1[l, j] < best[0]:
                best = (n1[l, j], ids[l], ids[live[j]])
        if not math.isfinite(best[0]) or best[0] >= criteria.cap_mw:
            best = (criteria.cap_mw, None, None)
        rows[b] = {"dc_estimate_mw": float(best[0]), "dc_binding_element": best[1],
                   "dc_binding_contingency": best[2]}
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "bus"
    return out


def dc_rows(net, hour: int, criteria=DEFAULT_CRITERIA) -> pd.DataFrame:
    """The in-study screen: every bus, both kinds, DC only. ``net`` carries ``hour``."""
    buses = list(net.bus["name"])
    rows = []
    for kind in KINDS:
        dc = capacity_dc(net, buses, kind, criteria)
        for bus, r in dc.iterrows():
            capped = r["dc_binding_element"] is None
            rows.append({
                "bus": bus, "hour": int(hour), "kind": kind, "capacity_mw": float("nan"),
                "dc_estimate_mw": r["dc_estimate_mw"],
                "binding_kind": "none_up_to_cap" if capped else (
                    "thermal_n1" if r["dc_binding_contingency"] else "thermal_intact"),
                "binding_element": r["dc_binding_element"],
                "binding_contingency": r["dc_binding_contingency"], "method": "dc",
            })
    return validate_capacity(pd.DataFrame(rows))
