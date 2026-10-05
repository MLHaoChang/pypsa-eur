"""The electrical asset choice as a MILP, solved in a loop of successive
linearisations with an adaptive convergence rate (plan C11).

C8 (``campus_invest.select_assets``) chooses need by need: the cheapest
candidate of each, escalated while an AC re-check fails. It cannot see that
a slightly dearer choice for one need removes another need altogether. This
module chooses every need **jointly**, with the dispatch of the continuous
and switched reactive sources, as one mixed-integer linear programme around
the current AC operating point, and walks that point until the AC re-check
agrees with the linear model. C8's result is the warm start and the upper
bound on cost.

**Variables.**

* One binary per candidate of every need, exactly one chosen per need. The
  candidates are C8's generators without C8's adequacy filters
  (``campus_invest.open_candidates``): the reactive need also gets "none",
  transformers every size in 1..3 units (a redundant group stays redundant),
  cables every section in 1..3 runs, switchgear every rating at the bus's
  voltage. Adequacy is decided by the linearised AC constraints instead.
  When C8's point is AC-feasible, a candidate dearer on its own than C8's
  whole set cannot be in a cheaper one and is left out (switchgear is not
  pruned: its cost depends on the bays).
* **Integer steps** per hour for every capacitor-bank and shunt-reactor
  candidate, up to its units x steps, and zero unless it is chosen.
* **Continuous inverter Q** per hour, for each inverter that C8's dispatch
  would also use (``campus_reactive``: a supplier, in service, and producing
  unless a battery). Its capability at the hour's P is a regular
  ``POLYGON_SIDES``-gon **inscribed** in the circle ``P^2 + Q^2 <= S^2``,
  with vertices at 0, 45, 90, ... degrees. Inscribed, because the capability
  is a hard equipment limit and the AC re-check cannot repair an inverter
  dispatched beyond its rating; the price is at most ``1 - cos(pi/8)`` = 7.6
  % of S, and nothing at P = 0 or at P = +-S, where the vertices sit.
* **Continuous STATCOM Q** per hour within +- the rating of the chosen
  candidate's units.
* One slack per constraint, ``>= 0``, at a penalty that outweighs any asset
  set (``penalty``), so a linearisation with no feasible point still returns
  a point to diagnose. Every non-zero slack is reported in the history.

The dispatch is **per hour, held in every case**, the same semantics as
C8's re-check (``campus_flow.solve_cases``): an N-1 case is the steady state
right after the outage, before any controller re-dispatches. A per-case
dispatch would assume an instant re-dispatch.

**Constraints**, per selected hour and per case class: ``intact``, and for
each transformer group ``N-1:<hv>/<lv>`` (the worst of its member outages;
the intact case when the group has a single unit). All are those C8's
re-check judges:

=============  ==========================================  =========
key            g <= limit                                  scale
=============  ==========================================  =========
``v``          every bus voltage within its band (the       0.01 pu
               code band at the PCC, the profile's
               ``campus_voltage`` band or the code band
               elsewhere), upper and lower
``q``          PCC Q within +- the band, intact only        1 % of it
``tl``         worst unit of each group: loading x rating   1 % of
               minus rating (MVA), every class              rating
``ts``         worst unit: S x (1 + margin) minus rating:   1 % of
               intact; and the survivors in the group's     rating
               own N-1 class when N-1 is on
``cl``         every cable: current minus its capacity      1 % of it
               (runs x rating), every class
``ik``/``ip``  per period, every rated bus (or switchgear   1 % of
               need): Ik'' and ip against the rating and    rating
               ``PEAK_FACTOR`` x rating
=============  ==========================================  =========

A rating is part of ``g``, so a candidate's rating enters exactly. A rated
bus that is not a need keeps its rating, and a cable that is not a need its
section (C8 makes them needs only once they fail; here they are held).

**Linearisation**, around the current AC point:

* **Continuous Q and switched steps: the load-flow Jacobian.** After each
  solved case, the Jacobian is rebuilt at the converged voltages from
  pandapower's internal ``Ybus`` (``dSbus_dV``; the ``J`` pandapower stores
  is from the last Newton iterate, a tolerance away). One sparse solve gives
  the complex voltage change per Mvar injected at each control bus, and the
  chain rule gives every monitored quantity: voltage magnitudes, the PCC's
  Q (``Ybus`` row of the slack), transformer S at the HV side and the
  current-based loading on its binding side, cable current on its larger
  end (``Yf``, ``Yt``). No extra load flow is needed. A test checks every
  sensitivity against a finite-difference AC re-solve. A capacitor or
  reactor step enters as ``q_step x V^2`` at the current voltage.
* **Discrete candidates: finite differences.** For each transformer and
  cable candidate other than the current one, the campus is rebuilt with it
  and every selected hour and case re-solved at the current dispatch, with
  the fault levels per period: one AC solve per candidate, hour and case,
  plus one IEC 60909 run per period. A compensation candidate at zero
  dispatch changes no load flow, so its effect enters through its dispatch
  variables; for the fault level, a candidate with a STATCOM (or replacing
  one) gets an IEC 60909 run per period. A switchgear rating enters its
  ``g`` exactly. A candidate whose campus does not converge at some hour is
  left out. The count is reported (``fd_solves`` in the history).
* The effects of candidates of different needs are added: exact for one
  change, an approximation for several, which the loop corrects.

**Objective**: the annualised cost (``annualised_cost`` x units, as C8),
switchgear per bay with the bays a linear function of the other choices
(exact products of binaries), plus the slack penalty, plus a tie-break of
``MOVE_EPS`` per Mvar moved so the dispatch stays where it is unless moving
it is needed.

**The loop.**

1. Start from C8's result: its choice and its dispatch.
2. Linearise there and solve the MILP, with each limit tightened by
   ``beta_c x |e_c|`` (``e_c`` the last linearisation error of that
   constraint) and every continuous Q within ``Delta`` of the current one.
3. Put the choice on a copy of the campus file exactly as C8 does
   (``campus_invest._apply``), the dispatch as setpoints, and re-solve AC
   at every hour and case: the trial point.
4. The linearisation error per constraint: AC minus the linear prediction.
5. **Back-off.** ``beta_c`` starts at 1. It grows x``BETA_GROW`` (1.5, at
   most ``BETA_MAX`` = 8) when the constraint is violated in AC at the
   trial, and decays x``BETA_DECAY`` (0.7, at least ``BETA_MIN`` = 0.25)
   when it is inside its limit by more than ``AMPLE`` (5) scale units.
6. **Trust region.** With ``merit = cost + penalty x violation`` (the
   violation in scale units, summed), ``rho`` = actual / predicted
   improvement of the merit. ``rho > 0.75``: accept, ``Delta x 2`` (at most
   ``4 x Q_ref``); ``rho < 0.25``: reject (the current point is kept) and
   ``Delta x 0.5``; between: accept, ``Delta`` kept. ``Q_ref`` is the
   largest continuous rating (an inverter's S or a STATCOM candidate's
   units); ``Delta`` starts at ``2 x Q_ref``, a full swing, so the first step
   is not restricted, and the floor is ``Delta_0 / 64``.
7. **Stop** when an accepted point passes the AC check and its cost moved
   less than ``COST_TOL``; when the MILP returns the current point (it
   predicts nothing better); when ``Delta`` falls below its floor; or at
   ``max_iter``.

The result is the best AC-feasible point seen, accepted or not (lowest
cost, the first on a tie), never a linear prediction. **Fallback:** when no
AC-feasible point is cheaper than C8's by more than ``COST_TOL``, C8's own
result is returned, flagged, with the reason.

**Feasible** means C8's own re-check finds nothing (``_failures``), with
the PCC's switchgear left out when the study gives it to the grid operator
(``pcc_switchgear=False``, as C8). Then the PCC has no switchgear
candidate and no ``ik``/``ip`` constraint either.

Steady state only: no dynamics, no harmonics, no tap-changer optimisation.
Allowed to import pandapower, linopy and highspy (``static/``); never pypsa.
"""
import copy
import dataclasses
import math

import linopy
import numpy as np
import pandas as pd
import xarray as xr
from pandapower.pypower.dSbus_dV import dSbus_dV
from scipy.sparse import hstack, vstack
from scipy.sparse.linalg import spsolve

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_compliance import Q_TOL
from gridspine.static.campus_flow import SizingCriteria, solve_cases, trafo_groups
from gridspine.static.campus_invest import (
    _apply, _bays, _compliance, _dispatch_table, _failures, _investment, _pcc_table, open_candidates, select_assets,
    study_from, with_measures,
)
from gridspine.static.campus_reactive import _SUPPLIERS, ALWAYS_ON, ReactiveResult, compensation_bus
from gridspine.static.campus_sc import PEAK_FACTOR, campus_fault_levels
from gridspine.templates.campus_assets import value
from gridspine.templates.grid_codes import band_for

POLYGON_SIDES = 8
BETA0, BETA_GROW, BETA_DECAY, BETA_MAX, BETA_MIN = 1.0, 1.5, 0.7, 8.0, 0.25
#: Inside the limit by more than this many scale units is "ample slack".
AMPLE = 5.0
RHO_GOOD, RHO_BAD = 0.75, 0.25
DELTA_GROW, DELTA_SHRINK = 2.0, 0.5
DELTA_START, DELTA_CAP, DELTA_FLOOR = 2.0, 4.0, 2.0 / 64      # x Q_ref
MAX_ITER = 20
#: Cost change (currency per year) below which the cost has converged.
COST_TOL = 1.0
#: Tie-break per Mvar of continuous Q moved, currency per year.
MOVE_EPS = 1e-3
HISTORY_COLUMNS = ["iteration", "cost", "feasible", "worst_violation", "worst_lin_error", "delta", "beta_mean",
                   "beta_max", "rho", "accepted", "slack", "slack_on", "choice", "fd_solves", "note"]


# --------------------------------------------------------------------------
# the inverter capability polygon
# --------------------------------------------------------------------------

def polygon(sides: int = POLYGON_SIDES):
    """``(normals, apothem)`` of the regular ``sides``-gon inscribed in the
    unit circle with vertices at angles ``2 pi i / sides``: a point (P, Q) of
    an inverter of rating S is inside when ``normals @ (P, Q) <= apothem x S``
    for every side."""
    phi = (2 * np.arange(sides) + 1) * np.pi / sides
    return np.c_[np.cos(phi), np.sin(phi)], math.cos(math.pi / sides)


def q_range(p_mw: float, s_mva: float, sides: int = POLYGON_SIDES):
    """``(q_min, q_max)`` of the polygon at active power ``p_mw``."""
    normals, apothem = polygon(sides)
    lo, hi = -math.inf, math.inf
    for (a, b) in normals:
        rhs = apothem * s_mva - a * p_mw
        if b > 1e-12:
            hi = min(hi, rhs / b)
        elif b < -1e-12:
            lo = max(lo, rhs / b)
    return (lo, hi) if lo <= hi else (0.0, 0.0)


# --------------------------------------------------------------------------
# sensitivities from the load-flow Jacobian
# --------------------------------------------------------------------------

def sensitivities(net, buses) -> dict:
    """The change of every monitored quantity per Mvar injected at each of
    ``buses`` (names), from the Jacobian at ``net``'s solved point (module
    docstring). ``{("vm", bus): {b: pu/Mvar}, ("pcc_q",): {b: Mvar/Mvar},
    ("trafo_s", name): {b: MVA/Mvar}, ("trafo_load", name): {b: MVA/Mvar},
    ("line_i", name): {b: kA/Mvar}}``; ``trafo_load`` is the loading
    fraction times the rating, the MVA its current is worth."""
    ppci = net._ppc["internal"]
    ybus, v, base = ppci["Ybus"], ppci["V"], float(ppci["baseMVA"])
    ref, pv, pq = ppci["ref"], ppci["pv"], ppci["pq"]
    pvpq = np.r_[pv, pq]
    lk = net._pd2ppc_lookups["bus"]
    name_idx = dict(zip(net.bus["name"].astype(str), net.bus.index))
    pq_pos = {int(b): i for i, b in enumerate(pq)}
    cols = []
    rhs = np.zeros((len(pvpq) + len(pq), len(buses)))
    for j, b in enumerate(buses):
        i = int(lk[name_idx[b]])
        if i in pq_pos:
            rhs[len(pvpq) + pq_pos[i], j] = 1.0 / base
        elif i not in set(int(r) for r in ref):
            raise ContractError(f"bus {b} is a voltage-controlled bus; a Q injection there has no sensitivity")
        cols.append(i)
    nb = len(v)
    dva, dvm = np.zeros((nb, len(buses))), np.zeros((nb, len(buses)))
    if len(pq) and rhs.any():
        ds_dvm, ds_dva = dSbus_dV(ybus, v)
        jac = vstack([hstack([ds_dva[pvpq][:, pvpq].real, ds_dvm[pvpq][:, pq].real]),
                      hstack([ds_dva[pq][:, pvpq].imag, ds_dvm[pq][:, pq].imag])]).tocsc()
        dx = np.asarray(spsolve(jac, rhs)).reshape(len(pvpq) + len(pq), len(buses))
        dva[pvpq], dvm[pq] = dx[:len(pvpq)], dx[len(pvpq):]
    vm = np.abs(v)
    dv = v[:, None] * (dvm / vm[:, None] + 1j * dva)
    out = {}

    def put(key, arr):
        out[key] = {b: float(arr[j]) for j, b in enumerate(buses)}

    for name, i in name_idx.items():
        put(("vm", name), dvm[int(lk[i])])
    s = int(ref[0])
    dq = (v[s] * np.conj(ybus[s].dot(dv))).imag.ravel() * base
    dq = dq - np.array([1.0 if c == s else 0.0 for c in cols])     # injected at the slack: the grid supplies less
    put(("pcc_q",), dq)
    live = np.cumsum(ppci["branch_is"]) - 1
    yf, yt = ppci["Yf"], ppci["Yt"]
    i_f, i_t = yf.dot(v), yt.dot(v)
    di_f, di_t = yf.dot(dv), yt.dot(dv)
    fbus = np.real(ppci["branch"][:, 0]).astype(int)

    def d_abs(x, dx_):
        return (np.conj(x) * dx_).real / abs(x) if abs(x) > 1e-12 else np.zeros(len(buses))

    lookups = net._pd2ppc_lookups["branch"]
    if "trafo" in lookups:
        f0, _ = lookups["trafo"]
        for k, i in enumerate(net.trafo.index):
            r = f0 + k
            if not ppci["branch_is"][r]:
                continue
            q = live[r]
            fb = fbus[q]
            sf = v[fb] * np.conj(i_f[q])
            dsf = dv[fb] * np.conj(i_f[q]) + v[fb] * np.conj(di_f[q])
            name = str(net.trafo.at[i, "name"])
            put(("trafo_s", name), d_abs(sf, dsf) * base)
            rt = net.res_trafo.loc[i]
            hv = rt["i_hv_ka"] * net.trafo.at[i, "vn_hv_kv"] >= rt["i_lv_ka"] * net.trafo.at[i, "vn_lv_kv"]
            cur, dcur = (i_f[q], di_f[q]) if hv else (i_t[q], di_t[q])
            load = float(rt["loading_percent"]) / 100.0 * float(net.trafo.at[i, "sn_mva"])
            put(("trafo_load", name), load * d_abs(cur, dcur) / abs(cur) if abs(cur) > 1e-12 else d_abs(cur, dcur))
    if "line" in lookups:
        f0, _ = lookups["line"]
        for k, i in enumerate(net.line.index):
            r = f0 + k
            if not ppci["branch_is"][r]:
                continue
            q = live[r]
            rl = net.res_line.loc[i]
            frm = rl["i_from_ka"] >= rl["i_to_ka"]
            cur, dcur = (i_f[q], di_f[q]) if frm else (i_t[q], di_t[q])
            ika = float(rl["i_ka"])
            put(("line_i", str(net.line.at[i, "name"])),
                ika * d_abs(cur, dcur) / abs(cur) if abs(cur) > 1e-12 else np.zeros(len(buses)))
    return out
