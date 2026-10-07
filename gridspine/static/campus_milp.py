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
``ts``         worst unit: S x (1 + margin) minus rating,   1 % of
               on C8's dispatch (below): intact; and the    rating
               survivors in the group's own N-1 class when
               N-1 is on
``cl``         every cable: current minus its capacity      1 % of it
               (runs x rating), every class
``ik``/``ip``  per period, every rated bus (or switchgear   1 % of
               need): Ik'' and ip against the rating and    rating
               ``PEAK_FACTOR`` x rating
=============  ==========================================  =========

**The sizing margin is judged on C8's dispatch** (owner, 2026-10-06: the
margin may not be met with dispatched inverter Q). For every point the
loop solves, the hours are also re-solved with the dispatch C8's re-check
would give the same assets (``reactive_need(residual=False)``: inverters,
then STATCOMs, then steps, only as far as the PCC band needs), and ``ts``
is read from those flows. In the MILP the continuous Q has no coefficient
in ``ts``: it enters at its reference value. The AC judge sizes the
transformers from the same flows (``size_transformers``), so a point
whose margin holds only with the MILP's extra Q is not feasible. The extra
Q may still serve the 100 % loading (``tl``), the PCC band and the
voltages. A candidate's effect on ``ts`` is a finite difference with that
dispatch re-run; a compensation candidate's only at the hours where C8's
dispatch reaches the compensation or still misses the band (elsewhere the
inverters alone hold the band and the compensation stays off).

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

1. Start from C8's result: its choice and its dispatch (inverter Q
   clipped into the polygon; C8 uses the whole circle).
2. Linearise there and solve the MILP, with each limit tightened by
   ``beta_c x |e_c|`` (``e_c`` the last linearisation error of that
   constraint) and every continuous Q within ``Delta`` of the current one
   while every need keeps its current candidate (see 6).
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

   The trust region binds **only on a trial that keeps every need's current
   candidate** (``FREE_SWITCH``; a binary that is 1 exactly then relaxes it
   otherwise). A rejected step keeps the current point and its
   linearisation, so a trial that changes a candidate must move the
   continuous Q all the way from the current point: once ``Delta`` has been
   halved below that move, the cheaper candidate can never be reached, and
   the loop falls back. On the test campus at 38 MW (``test_campus_milp``),
   a literal trust region did exactly that (C8's 88.8 k a year after 3
   iterations), where this one reaches the 68.3 k joint optimum in 5. A
   candidate change is still held by the capability polygon and the
   STATCOM ratings, and its linearisation error by the back-off ``beta``.
7. **Stop** when an accepted point passes the AC check and its cost moved
   less than ``COST_TOL``; when the MILP returns the current point (it
   predicts nothing better); when ``Delta`` falls below its floor; or at
   ``max_iter``. A caller's ``should_stop()`` (plan C12, the panel's
   cancel) ends it too, before an iteration or between two
   finite-difference solves: ``stop == "cancelled"``.

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
from gridspine.static.campus_flow import SizingCriteria, size_transformers, solve_cases
from gridspine.static.campus_invest import (
    _apply, _bays, _compliance, _dispatch_table, _failures, _investment, _pcc_table, open_candidates, select_assets,
    study_from, with_measures,
)
from gridspine.static.campus_reactive import _SUPPLIERS, ALWAYS_ON, ReactiveResult, compensation_bus, reactive_need
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
#: The trust region binds only while the discrete choice is kept.
FREE_SWITCH = True
#: Cost change (currency per year) below which the cost has converged.
COST_TOL = 1.0
#: Tie-break per Mvar of continuous Q moved, currency per year.
MOVE_EPS = 1e-3
HISTORY_COLUMNS = ["iteration", "cost", "feasible", "worst_violation", "worst_lin_error", "delta", "beta_mean",
                   "beta_max", "beta_up", "rho", "accepted", "point_cost", "slack", "slack_on", "choice", "fd_solves",
                   "note"]


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


# --------------------------------------------------------------------------
# the adaptive rules
# --------------------------------------------------------------------------

def update_beta(beta: float, violated: bool, ample: bool) -> float:
    """Step 5 of the loop (module docstring)."""
    if violated:
        return min(beta * BETA_GROW, BETA_MAX)
    if ample:
        return max(beta * BETA_DECAY, BETA_MIN)
    return beta


def gain_ratio(merit_now: float, merit_trial: float, merit_predicted: float) -> float:
    """rho: the actual improvement of the merit over the predicted one. A
    step predicted to gain nothing scores 1 if it loses nothing, else 0."""
    predicted, actual = merit_now - merit_predicted, merit_now - merit_trial
    tiny = 1e-9 * max(1.0, abs(merit_now))
    if predicted > tiny:
        return actual / predicted
    return 1.0 if actual >= -tiny else 0.0


def update_delta(rho: float, delta: float, cap: float):
    """Step 6 of the loop: ``(new delta, accepted)``."""
    if rho > RHO_GOOD:
        return min(delta * DELTA_GROW, cap), True
    if rho < RHO_BAD:
        return delta * DELTA_SHRINK, False
    return delta, True


# --------------------------------------------------------------------------
# the problem: needs, candidates, controls
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Option:
    need: int                    # index into the needs
    pos: int                     # index into that need's open candidates
    cand: object                 # the campus_invest.Candidate
    q_stat: float = 0.0          # STATCOM Mvar of its units
    cap_steps: int = 0           # switched capacitor steps
    cap_step_mvar: float = 0.0
    ind_steps: int = 0           # reactor units, one step each
    ind_step_mvar: float = 0.0
    rating: float = math.nan     # a switchgear option's rated Ik''


@dataclasses.dataclass
class Point:
    """A choice (one option index per need) and its dispatch: inverter Q per
    ``(hour, unit)``, STATCOM Q per hour, and ``(option, "cap"|"ind",
    hour)`` steps."""
    choice: tuple
    inv: dict
    stat: dict
    steps: dict

    def same_as(self, other, tol=1e-6):
        return (self.choice == other.choice
                and all(abs(self.inv.get(k, 0.0) - other.inv.get(k, 0.0)) <= tol for k in self.inv.keys() | other.inv.keys())
                and all(abs(self.stat.get(k, 0.0) - other.stat.get(k, 0.0)) <= tol
                        for k in self.stat.keys() | other.stat.keys())
                and all(self.steps.get(k, 0) == other.steps.get(k, 0) for k in self.steps.keys() | other.steps.keys()))


@dataclasses.dataclass
class _Eval:
    point: Point
    spec: dict
    added: dict
    study: object
    converged: bool
    g: dict
    sens: dict
    cost: float
    compliance: object
    feasible: bool
    fails: list
    ref_r: dict = dataclasses.field(default_factory=dict)      # C8's dispatch per hour, for the margin


class _Problem:
    """Everything that stays fixed through the loop."""

    def __init__(self, campus_spec, hourly, library, req, criteria, state):
        self.spec0, self.library, self.req, self.criteria = campus_spec, library, req, criteria
        self.st = state
        self.lib_by_id = state.lib_by_id
        self.hours = state.hours
        self.profile = state.profile
        base = build_campus(campus_spec)
        self.f_hz = int(round(float(base.net.f_hz)))
        self.comp_bus = compensation_bus(base)
        self.skip = state.skip_buses
        self.needs = [n for n in state.needs if n.kind in ("transformer", "reactive", "cable", "switchgear")]
        self.groups = {f"{n.target['hv_bus']}/{n.target['lv_bus']}" for n in self.needs if n.kind == "transformer"}
        # the inverters each hour may use, with their P (campus_reactive's rule)
        units = base.units
        self.unit_bus = {str(u): str(units.at[u, "bus"]) for u in units.index}
        self.avail = {}
        for key in self.hours:
            rows = state.rows_of[key].set_index("unit_id")
            for uid, u in units.iterrows():
                if u["kind"] not in _SUPPLIERS or int(rows.at[uid, "status"]) != 1:
                    continue
                p = float(rows.at[uid, "p_mw"])
                if u["kind"] not in ALWAYS_ON and abs(p) <= 1e-9:
                    continue
                self.avail[(key, str(uid))] = (p, float(u["s_mva"]))
        self.ctrl_buses = sorted({self.unit_bus[u] for (_, u) in self.avail} | {self.comp_bus})

    def build_options(self, c8_choice_items, upper_bound):
        """The open candidates of every need, pruned by the upper bound."""
        self.options, self.by_need, self.x0, self.c8_labels = [], [], [], []
        for j, need in enumerate(self.needs):
            cands = open_candidates(need, self.library, self.lib_by_id, self.criteria, self.f_hz)
            want = c8_choice_items[j]
            self.c8_labels.append(need.choice.label if want is not None else "as described")
            cur = next((i for i, c in enumerate(cands) if (c.items, c.existing) == want), None)
            if cur is None and want is not None:
                cands = [need.choice, *cands]
                cur = 0
            idx = []
            for i, c in enumerate(cands):
                if (i != cur and need.kind != "switchgear" and math.isfinite(upper_bound)
                        and c.annual > upper_bound + COST_TOL):
                    continue
                idx.append(len(self.options))
                self.options.append(self._option(j, i, c))
            if not idx:
                raise ContractError(f"{need.key}: no candidate in the library")
            self.by_need.append(idx)
            self.x0.append(next((k for k in idx if self.options[k].pos == cur), idx[0]))
            need_cands = cands
            self.needs[j] = dataclasses.replace(need, candidates=need_cands, pos=cur if cur is not None else 0,
                                                unresolved=None)

    def _option(self, j, i, cand):
        kw = {}
        for kind, lid, n in cand.items:
            e = self.lib_by_id[lid]
            if kind == "statcom":
                kw["q_stat"] = kw.get("q_stat", 0.0) + n * value(e["q_mvar"])
            elif kind == "capacitor_bank":
                steps = int(value(e["steps"]))
                kw.update(cap_steps=n * steps, cap_step_mvar=value(e["q_mvar"]) / steps)
            elif kind == "shunt_reactor":
                kw.update(ind_steps=n, ind_step_mvar=value(e["q_mvar"]))
            elif kind == "switchgear":
                kw["rating"] = value(e["ik_rated_ka"])
        return Option(j, i, cand, **kw)

    # ------------------------------------------------------------------
    # a point on the campus file, solved
    # ------------------------------------------------------------------

    def realise(self, point):
        needs = [dataclasses.replace(n, pos=self.options[k].pos) for n, k in zip(self.needs, point.choice)]
        spec, added = _apply(self.spec0, needs, self.lib_by_id)
        return needs, spec, added

    def setpoints(self, point, spec, added):
        comp = {str(x["name"]): x for x in spec["campus"].get("compensation") or []}
        rk = next((k for k in point.choice if self.needs[self.options[k].need].kind == "reactive"), None)
        names = added.get("reactive", [])
        out = {}
        for key in self.hours:
            sgen = {u: q for (h, u), q in point.inv.items() if h == key}
            shunt = {}
            stat = [n for n in names if comp[n]["kind"] == "statcom"]
            total = sum(value(comp[n]["q_mvar"]) for n in stat)
            for n in stat:
                sgen[n] = point.stat.get(key, 0.0) * value(comp[n]["q_mvar"]) / total
            for kind, tag in (("capacitor_bank", "cap"), ("shunt_reactor", "ind")):
                left = int(point.steps.get((rk, tag, key), 0)) if rk is not None else 0
                for n in (n for n in names if comp[n]["kind"] == kind):
                    k = min(left, int(comp[n].get("steps", 1)))
                    shunt[n] = k
                    left -= k
            out[key] = {"sgen_q": sgen, "shunt_step": shunt}
        return out

    def evaluate(self, point, linearise=False, flows_only=False):
        """Solve ``point`` in AC at every hour and case (module docstring)."""
        needs, spec, added = self.realise(point)
        campus = build_campus(spec)
        sp = self.setpoints(point, spec, added)
        sens, flows, reactive = {}, {}, {}
        rows_of = self.st.rows_of
        for key in self.hours:
            def inspect(case, net, key=key):
                if linearise:
                    sens[(key, case)] = sensitivities(net, self.ctrl_buses)
            flows[key] = solve_cases(campus, rows_of[key], setpoints=sp[key], inspect=inspect)
            reactive[key] = self._reactive(campus, flows[key]["intact"], sp[key], point, key)
        study = study_from(campus, reactive, flows, self.st.installed, self.req, self.criteria, recheck=True)
        ref, ref_ok = self.reference(campus)
        # the sizing margin is judged on C8's own dispatch (owner, 2026-10-06)
        study.sizing = size_transformers(ref, campus, self.criteria)
        converged = bool(study.converged) and ref_ok
        g = self._constraints(campus, study, sens if linearise else None, ref) if converged else {}
        ref_r = self._ref_r
        if flows_only:
            return _Eval(point, spec, added, study, converged, g, {}, math.nan, None, False, [], ref_r)
        compliance = _compliance(study, self.req, self.profile) if converged else None
        fails = ([f for f in _failures(study, compliance, needs, self.req, self.criteria)
                  if f[1] != "switchgear" or f[0].split(" ", 1)[1] not in self.skip] if converged
                 else [(None, "load_flow", "the re-solve did not converge", 0)])
        return _Eval(point, spec, added, study, converged, g, self._sens if linearise else {},
                     self.cost(point, spec), compliance, converged and not fails, fails, ref_r)

    def reference(self, campus, hours=None):
        """C8's dispatch for these assets (``reactive_need(residual=False)``:
        inverters, then STATCOMs, then steps, only as far as the PCC band
        needs) and every case solved with it: ``({hour: solve_cases},
        converged)``. The sizing margin is judged on these flows."""
        flows, rs, ok = {}, {}, True
        for key in hours or self.hours:
            rows = self.st.rows_of[key]
            r = reactive_need(campus, rows, self.req, residual=False)
            flows[key], rs[key] = solve_cases(campus, rows, setpoints=r.setpoints), r
            ok &= r.converged and all(f.converged for f in flows[key].values())
        self._ref_r = rs
        return flows, ok

    def _reactive(self, campus, intact, sp, point, key):
        vm = dict(zip(intact.bus["bus"], intact.bus["vm_pu"])) if intact.converged else {}
        dispatch = {}
        for n, c in campus.compensation.iterrows():
            n = str(n)
            if c["kind"] == "statcom":
                dispatch[n] = {"kind": "statcom", "steps": None, "q_mvar": float(sp["sgen_q"].get(n, 0.0))}
            else:
                k = int(sp["shunt_step"].get(n, 0))
                q = k * float(c["q_mvar"]) / int(c["steps"] if c["kind"] == "capacitor_bank" else 1) \
                    * vm.get(str(c["bus"]), 1.0) ** 2
                dispatch[n] = {"kind": str(c["kind"]), "steps": k,
                               "q_mvar": q if c["kind"] == "capacitor_bank" else -q}
        unit_q = {u: q for (h, u), q in point.inv.items() if h == key}
        qf = float(intact.pcc_q_mvar) if intact.converged else math.nan
        return ReactiveResult(bool(intact.converged), qf, qf, sum(unit_q.values()), 0.0, unit_q, False, dispatch,
                              sum(d["q_mvar"] for d in dispatch.values()))

    def cost(self, point, spec):
        total = 0.0
        c = spec["campus"]
        for k in point.choice:
            o = self.options[k]
            need = self.needs[o.need]
            if need.kind == "switchgear":
                total += o.cand.annual * _bays(c, need.target["bus"])
            else:
                total += o.cand.annual
        return total

    # ------------------------------------------------------------------
    # the constraints of a solved point
    # ------------------------------------------------------------------

    def _constraints(self, campus, study, sens, ref):
        """``{key: g}`` with ``self._sens[key] = (hour, {bus: dg/dQ}, V^2 at
        the compensation bus)`` when ``sens`` is given. ``ref`` are the
        flows with C8's dispatch, on which the sizing margin (``ts``) is
        judged: there the MILP's own Q has no coefficient."""
        net = campus.net
        name_bus = dict(zip(net.bus.index, net.bus["name"].astype(str)))
        group_of, rating = {}, {}
        for i in net.trafo.index:
            gk = f"{name_bus[int(net.trafo.at[i, 'hv_bus'])]}/{name_bus[int(net.trafo.at[i, 'lv_bus'])]}"
            group_of[str(net.trafo.at[i, "name"])] = gk
            rating[str(net.trafo.at[i, "name"])] = float(net.trafo.at[i, "sn_mva"])
        cap = {str(net.line.at[i, "name"]): float(net.line.at[i, "max_i_ka"]) * float(net.line.at[i, "df"])
               * float(net.line.at[i, "parallel"]) for i in net.line.index}
        pcc_bus = name_bus[int(net.ext_grid["bus"].iloc[0])]
        kv = dict(zip(net.bus["name"].astype(str), net.bus["vn_kv"].astype(float)))
        own = self.profile.get("campus_voltage")
        band = {b: band_for(self.profile, kv[b]) if b == pcc_bus or not own else own for b in kv}
        m = self.criteria.margin
        g, self._sens, self.limit = {}, {}, getattr(self, "limit", {})
        n1 = self.criteria.n_minus_1
        vm2 = {}

        def vm_cb(hour, case):
            if (hour, case) not in vm2:
                b = study.flows[hour][case].bus
                vm2[(hour, case)] = float(b.loc[b["bus"] == self.comp_bus, "vm_pu"].iloc[0]) ** 2
            return vm2[(hour, case)]

        def put(key, val, lim, hour=None, case=None, d=None, scale=None):
            if key in g and val <= g[key]:
                return
            g[key] = val
            self.limit.setdefault(key, (lim, scale))
            if sens is not None and case is not None:
                s = sens.get((hour, case), {})
                coef = {b: (sum(w * s.get(q, {}).get(b, 0.0) for q, w in d)) for b in self.ctrl_buses}
                self._sens[key] = (hour, coef, vm_cb(hour, case))

        groups_cases = {}
        for key, cases in study.flows.items():
            for case, f in cases.items():
                cls = "intact" if case == "intact" else f"N-1:{group_of.get(case[4:], '?')}"
                groups_cases.setdefault(key, {}).setdefault(cls, []).append(case)
        classes = ["intact", *(f"N-1:{gk}" for gk in sorted(self.groups))]
        for key, cases in study.flows.items():
            for cls in classes:
                members = groups_cases[key].get(cls) or ["intact"]
                for case in members:
                    f = cases[case]
                    for b, vmv in zip(f.bus["bus"], f.bus["vm_pu"]):
                        put(("v", key, cls, b, 1), float(vmv), band[b]["v_max"], key, case, [(("vm", b), 1.0)], 0.01)
                        put(("v", key, cls, b, -1), -float(vmv), -band[b]["v_min"], key, case, [(("vm", b), -1.0)],
                            0.01)
                    if cls == "intact":
                        q = float(f.pcc_q_mvar)
                        sc_q = max(0.01 * self.req.q_limit_mvar, 0.01)
                        put(("q", key, cls, "PCC", 1), q, self.req.q_limit_mvar, key, case, [(("pcc_q",), 1.0)], sc_q)
                        put(("q", key, cls, "PCC", -1), -q, self.req.q_limit_mvar, key, case, [(("pcc_q",), -1.0)],
                            sc_q)
                    for t, load in zip(f.trafo["trafo"], f.trafo["loading_pct"]):
                        gk, r = group_of[t], rating[t]
                        put(("tl", key, cls, gk, 1), (float(load) / 100.0 - 1.0) * r, 0.0, key, case,
                            [(("trafo_load", t), 1.0)], 0.01 * r)
                    for cb, ika in zip(f.line["cable"], f.line["i_ka"]):
                        put(("cl", key, cls, cb, 1), float(ika) - cap[cb], 0.0, key, case, [(("line_i", cb), 1.0)],
                            0.01 * cap[cb])
        g.update(self.ts_values(campus, ref))
        pf = PEAK_FACTOR[self.f_hz]
        sw_buses = {n.target["bus"] for n in self.needs if n.kind == "switchgear"}
        for r in study.short_circuit.itertuples(index=False):
            if str(r.bus) in self.skip or (pd.isna(r.rated_ka) and str(r.bus) not in sw_buses):
                continue
            rated = float(r.rated_ka)
            key = (int(r.period), None)
            put(("ik", key, "sc", str(r.bus), 1), float(r.ikss_max_ka) - rated, 0.0, scale=0.01 * rated)
            put(("ip", key, "sc", str(r.bus), 1), float(r.ip_max_ka) - pf * rated, 0.0, scale=0.01 * pf * rated)
        return g

    def ts_values(self, campus, ref):
        """The sizing-margin constraints (``ts``) on the flows ``ref`` with
        C8's dispatch: worst unit, S x (1 + margin) minus its rating, in the
        intact class and (N-1 on) in its own group's outage class."""
        net = campus.net
        name_bus = dict(zip(net.bus.index, net.bus["name"].astype(str)))
        group_of, rating = {}, {}
        for i in net.trafo.index:
            group_of[str(net.trafo.at[i, "name"])] = \
                f"{name_bus[int(net.trafo.at[i, 'hv_bus'])]}/{name_bus[int(net.trafo.at[i, 'lv_bus'])]}"
            rating[str(net.trafo.at[i, "name"])] = float(net.trafo.at[i, "sn_mva"])
        m, n1 = self.criteria.margin, self.criteria.n_minus_1
        out = {}
        for key, cases in ref.items():
            by_cls = {}
            for case in cases:
                cls = "intact" if case == "intact" else f"N-1:{group_of.get(case[4:], '?')}"
                by_cls.setdefault(cls, []).append(case)
            for cls in ["intact", *(f"N-1:{gk}" for gk in sorted(self.groups))]:
                for case in by_cls.get(cls) or ["intact"]:
                    f = cases[case]
                    for t, s_mva in zip(f.trafo["trafo"], f.trafo["s_mva"]):
                        gk, r = group_of[t], rating[t]
                        if not (cls == "intact" or (n1 and cls[4:] == gk)):
                            continue
                        k = ("ts", key, cls, gk, 1)
                        val = float(s_mva) * (1 + m) - r
                        if k not in out or val > out[k]:
                            out[k] = val
                        self.limit.setdefault(k, (0.0, 0.01 * r))
        return out

    def fault_only(self, point):
        """``{key: g}`` of the fault-level constraints alone (one IEC 60909
        run per period), cached by what feeds a fault: the other needs'
        choices and the STATCOM units."""
        stat_items = tuple(sorted(it for k in point.choice for it in self.options[k].cand.items if it[0] == "statcom"))
        key = (tuple(k for k in point.choice if self.needs[self.options[k].need].kind != "reactive"), stat_items)
        cache = self.__dict__.setdefault("_fault_cache", {})
        if key not in cache:
            cache[key] = self._fault_only(point)
        return cache[key]

    def _fault_only(self, point):
        _, spec, _ = self.realise(point)
        campus = build_campus(spec)
        statcoms = {str(n) for n, c in campus.compensation.iterrows() if c["kind"] == "statcom"}
        pf = PEAK_FACTOR[self.f_hz]
        out = {}
        for p in sorted(self.st.installed):
            sc = campus_fault_levels(campus, self.st.installed[p] | statcoms)
            for r in sc.itertuples(index=False):
                for chk, val, lim in (("ik", r.ikss_max_ka, r.rated_ka), ("ip", r.ip_max_ka, pf * r.rated_ka)):
                    key = (chk, (int(p), None), "sc", str(r.bus), 1)
                    if pd.notna(lim):
                        out[key] = float(val) - float(lim)
        return out


# --------------------------------------------------------------------------
# linearisation and the MILP
# --------------------------------------------------------------------------

@dataclasses.dataclass
class _Linear:
    keys: list
    g0: np.ndarray
    D: np.ndarray                # constraints x options
    disabled: set
    sens: dict
    bays: dict                   # switchgear bus -> (bays now, {option: change})
    fd_solves: int


class _Cancelled(Exception):
    """``should_stop`` answered True between two finite-difference solves."""

    def __init__(self, solves):
        super().__init__("cancelled")
        self.solves = solves


def _linearise(prob, ev):
    keys = sorted(ev.g, key=str)
    stop = getattr(prob, "should_stop", None)
    row = {k: i for i, k in enumerate(keys)}
    g0 = np.array([ev.g[k] for k in keys])
    D = np.zeros((len(keys), len(prob.options)))
    disabled, solves = set(), 0
    cur = ev.point.choice
    cur_stat = any(prob.options[k].q_stat > 0 for k in cur)
    # hours where C8's dispatch reaches the compensation (or still misses the
    # band): only there can a compensation choice move the margin's flow
    lim = prob.req.q_limit_mvar
    reach = [h for h, r in ev.ref_r.items()
             if abs(r.q_final_mvar) > lim + Q_TOL or abs(r.q_installed_mvar) > 1e-9]
    n_cases = sum(len(c) for c in ev.study.flows.values())
    for j, idx in enumerate(prob.by_need):
        kind = prob.needs[j].kind
        for k in idx:
            if k == cur[j]:
                continue
            if stop is not None and kind != "switchgear" and stop():
                raise _Cancelled(solves)
            o = prob.options[k]
            trial = dataclasses.replace(ev.point, choice=tuple(k if i == j else c for i, c in enumerate(cur)))
            if kind in ("transformer", "cable"):
                e = prob.evaluate(trial, flows_only=True)
                solves += n_cases
                if not e.converged:
                    disabled.add(k)
                    continue
                for key, val in e.g.items():
                    if key in row:
                        D[row[key], k] = val - ev.g[key]
            elif kind == "reactive":
                if o.q_stat > 0 or cur_stat:
                    for key, val in prob.fault_only(trial).items():
                        if key in row:
                            D[row[key], k] = val - ev.g[key]
                if reach:
                    _, spec, _ = prob.realise(trial)
                    campus = build_campus(spec)
                    flows, ok = prob.reference(campus, reach)
                    solves += sum(len(c) for c in flows.values())
                    if ok:
                        for key, val in prob.ts_values(campus, flows).items():
                            if key in row:
                                D[row[key], k] = val - ev.g[key]
            else:                                              # a switchgear rating: exact
                now = prob.options[cur[j]]
                bus = prob.needs[j].target["bus"]
                pf = PEAK_FACTOR[prob.f_hz]
                for key in keys:
                    if key[0] in ("ik", "ip") and key[3] == bus:
                        D[row[key], k] = -(o.rating - now.rating) * (pf if key[0] == "ip" else 1.0)
    bays = {}
    for j, need in enumerate(prob.needs):
        if need.kind != "switchgear":
            continue
        bus = need.target["bus"]
        now = _bays(ev.spec["campus"], bus)
        change = {}
        for jj, idx in enumerate(prob.by_need):
            if prob.needs[jj].kind == "switchgear":
                continue
            for k in idx:
                if k == cur[jj]:
                    continue
                trial = dataclasses.replace(ev.point, choice=tuple(k if i == jj else c for i, c in enumerate(cur)))
                _, spec, _ = prob.realise(trial)
                d = _bays(spec["campus"], bus) - now
                if d:
                    change[k] = d
        bays[bus] = (now, change)
    return _Linear(keys, g0, D, disabled, ev.sens, bays, solves)


def _controls(prob, lin, keys):
    """The per-constraint coefficients of the controls: inverter Q per
    ``(hour, unit)``, and per hour the STATCOM Q, the capacitive and the
    inductive nominal Mvar switched in."""
    inv = sorted(prob.avail, key=str)
    hours = list(prob.hours)
    A_inv = np.zeros((len(keys), len(inv)))
    A_h = np.zeros((3, len(keys), len(hours)))
    inv_col = {k: i for i, k in enumerate(inv)}
    h_col = {h: i for i, h in enumerate(hours)}
    for r, key in enumerate(keys):
        entry = lin.sens.get(key)
        if entry is None:
            continue
        hour, coef, vm2 = entry
        for (h, u), c in inv_col.items():
            if h == hour:
                A_inv[r, c] = coef[prob.unit_bus[u]]
        cb = coef[prob.comp_bus]
        A_h[0, r, h_col[hour]] = cb
        A_h[1, r, h_col[hour]] = cb * vm2
        A_h[2, r, h_col[hour]] = -cb * vm2
    return inv, hours, A_inv, A_h


def _solve_milp(prob, lin, point, backoff, delta, penalty, cuts=(), free_switch=None):
    free_switch = FREE_SWITCH if free_switch is None else free_switch
    keys = lin.keys
    inv, hours, A_inv, A_h = _controls(prob, lin, keys)
    K = len(prob.options)
    m = linopy.Model()
    x = m.add_variables(binary=True, coords=[np.arange(K)], dims=["k"], name="x")
    member = np.zeros((len(prob.by_need), K))
    for j, idx in enumerate(prob.by_need):
        member[j, idx] = 1.0
    m.add_constraints((xr.DataArray(member, dims=["n", "k"]) * x).sum("k") == 1)
    for k in lin.disabled:
        m.add_constraints(x.loc[[k]].sum() == 0)
    for choice in cuts:                         # a choice whose campus did not converge
        m.add_constraints(x.loc[list(choice)].sum() <= len(choice) - 1)
    u0 = np.array([point.inv.get(v, 0.0) for v in inv])
    stat0 = np.array([point.stat.get(h, 0.0) for h in hours])
    q_ref = prob.q_ref
    terms = []
    obj = []
    if free_switch:
        # z = 1 when every need keeps its current candidate: only then does
        # the trust region bind (module docstring)
        z = m.add_variables(binary=True, name="z")
        for k in point.choice:
            m.add_constraints(z - x.loc[k] <= 0)
        m.add_constraints(z - sum(x.loc[k] for k in point.choice) >= 1 - len(point.choice))
    tr_delta = math.inf if free_switch else delta

    def trust(var, ref, big, dim):
        if not free_switch:
            return
        r = xr.DataArray(ref, dims=[dim])
        m.add_constraints(var - r + big * z <= delta + big)
        m.add_constraints(r - var + big * z <= delta + big)

    if inv:
        # bounds: the circle (and a literal trust region); the polygon below is the capability
        circle = np.array([math.sqrt(max(sv * sv - pv * pv, 0.0)) for pv, sv in (prob.avail[v] for v in inv)])
        lo = np.maximum(-circle, u0 - tr_delta)
        hi = np.minimum(circle, u0 + tr_delta)
        lo = np.minimum(lo, hi)
        u = m.add_variables(lower=lo, upper=hi, coords=[np.arange(len(inv))], dims=["v"], name="u")
        trust(u, u0, 4 * q_ref, "v")
        normals, apothem = polygon()
        p = np.array([prob.avail[v][0] for v in inv])
        s = np.array([prob.avail[v][1] for v in inv])
        for a, b in normals:
            m.add_constraints(float(b) * u <= xr.DataArray(apothem * s - a * p, dims=["v"]))
        mv = m.add_variables(lower=0, coords=[np.arange(len(inv))], dims=["v"], name="mv")
        u0d = xr.DataArray(u0, dims=["v"])
        m.add_constraints(mv - u >= -u0d)
        m.add_constraints(mv + u >= u0d)
        obj.append(MOVE_EPS * mv.sum())
        terms.append((xr.DataArray(A_inv, dims=["c", "v"]) * u).sum("v"))
    H = len(hours)
    stat_opts = [k for k, o in enumerate(prob.options) if o.q_stat > 0]
    cap_opts = [k for k, o in enumerate(prob.options) if o.cap_steps > 0]
    ind_opts = [k for k, o in enumerate(prob.options) if o.ind_steps > 0]
    if stat_opts:
        qs = m.add_variables(lower=np.maximum(-q_ref * 4, stat0 - tr_delta),
                             upper=np.minimum(q_ref * 4, stat0 + tr_delta), coords=[np.arange(H)], dims=["h"], name="qs")
        trust(qs, stat0, 8 * q_ref, "h")
        rating = np.zeros(K)
        rating[stat_opts] = [prob.options[k].q_stat for k in stat_opts]
        capx = (xr.DataArray(rating, dims=["k"]) * x).sum("k")
        m.add_constraints(qs - capx <= 0)
        m.add_constraints(-qs - capx <= 0)
        ms = m.add_variables(lower=0, coords=[np.arange(H)], dims=["h"], name="ms")
        s0d = xr.DataArray(stat0, dims=["h"])
        m.add_constraints(ms - qs >= -s0d)
        m.add_constraints(ms + qs >= s0d)
        obj.append(MOVE_EPS * ms.sum())
        terms.append((xr.DataArray(A_h[0], dims=["c", "h"]) * qs).sum("h"))
    step_vars = {}
    for tag, opts, plane in (("cap", cap_opts, 1), ("ind", ind_opts, 2)):
        if not opts:
            continue
        n_max = np.array([[getattr(prob.options[k], f"{tag}_steps")] * H for k in opts])
        st = m.add_variables(lower=0, upper=n_max, integer=True, coords=[np.arange(len(opts)), np.arange(H)],
                             dims=[f"o{tag}", "h"], name=f"s{tag}")
        pick = np.zeros((len(opts), K))
        pick[np.arange(len(opts)), opts] = n_max[:, 0]
        m.add_constraints(st - (xr.DataArray(pick, dims=[f"o{tag}", "k"]) * x).sum("k") <= 0)
        mvar = xr.DataArray([getattr(prob.options[k], f"{tag}_step_mvar") for k in opts], dims=[f"o{tag}"])
        nominal = (mvar * st).sum(f"o{tag}")                    # Mvar switched per hour
        terms.append((xr.DataArray(A_h[plane], dims=["c", "h"]) * nominal).sum("h"))
        step_vars[tag] = (st, opts)
    # the linearised constraints
    lim = np.array([prob.limit[k][0] for k in keys])
    scale = np.array([prob.limit[k][1] for k in keys])
    sig = m.add_variables(lower=0, coords=[np.arange(len(keys))], dims=["c"], name="sigma")
    x0 = np.zeros(K)
    x0[list(point.choice)] = 1.0
    steps0 = {tag: np.array([[point.steps.get((k, tag, hh), 0) * getattr(prob.options[k], f"{tag}_step_mvar")
                              for hh in hours] for k in opts]).sum(axis=0) if opts else np.zeros(H)
              for tag, opts in (("cap", cap_opts), ("ind", ind_opts))}
    const = A_inv @ u0 + A_h[0] @ stat0 + A_h[1] @ steps0["cap"] + A_h[2] @ steps0["ind"]
    rhs = lim - backoff - lin.g0 + lin.D @ x0 + const
    lhs = (xr.DataArray(lin.D, dims=["c", "k"]) * x).sum("k") - xr.DataArray(scale, dims=["c"]) * sig
    for t in terms:
        lhs = lhs + t
    m.add_constraints(lhs <= xr.DataArray(rhs, dims=["c"]), name="lin")
    # the cost: options, and switchgear per bay (exact products of binaries)
    sw = set()
    cost = np.zeros(K)
    for k, o in enumerate(prob.options):
        if prob.needs[o.need].kind == "switchgear":
            sw.add(k)
        else:
            cost[k] = o.cand.annual
    obj.append((xr.DataArray(cost, dims=["k"]) * x).sum())
    for j, need in enumerate(prob.needs):
        if need.kind != "switchgear":
            continue
        now, change = lin.bays[need.target["bus"]]
        rs = prob.by_need[j]
        a = np.zeros(K)
        a[rs] = [prob.options[r].cand.annual for r in rs]
        obj.append((xr.DataArray(a * now, dims=["k"]) * x).sum())
        if not change:
            continue
        ks = sorted(change)
        # w[r, k] = x_r x_k exactly (binaries): w >= x_r + x_k - 1, w <= x_r, w <= x_k
        w = m.add_variables(lower=0, upper=1, coords=[np.arange(len(rs)), np.arange(len(ks))], dims=["r", "q"],
                            name=f"w{j}")
        sel_r = np.zeros((len(rs), K))
        sel_r[np.arange(len(rs)), rs] = 1.0
        sel_k = np.zeros((len(ks), K))
        sel_k[np.arange(len(ks)), ks] = 1.0
        xr_ = (xr.DataArray(sel_r, dims=["r", "k"]) * x).sum("k")
        xk_ = (xr.DataArray(sel_k, dims=["q", "k"]) * x).sum("k")
        m.add_constraints(w - xr_ - xk_ >= -1)
        m.add_constraints(w - xr_ <= 0)
        m.add_constraints(w - xk_ <= 0)
        coef = np.outer([prob.options[r].cand.annual for r in rs], [change[k] for k in ks])
        obj.append((xr.DataArray(coef, dims=["r", "q"]) * w).sum())
    obj.append(penalty * sig.sum())
    m.add_objective(sum(obj[1:], obj[0]))
    status, cond = m.solve(solver_name="highs", io_api="direct", output_flag=False, mip_rel_gap=1e-9,
                           mip_abs_gap=1e-6)
    if status != "ok":
        raise ContractError(f"the MILP did not solve: {status}, {cond}")
    xs = np.round(x.solution.values).astype(int)
    choice = tuple(int(next(k for k in idx if xs[k] == 1)) for idx in prob.by_need)
    new = Point(choice,
                {v: float(val) for v, val in zip(inv, m.variables["u"].solution.values)} if inv else {},
                {h: float(val) for h, val in zip(hours, m.variables["qs"].solution.values)} if stat_opts else {},
                {})
    for tag, (st, opts) in step_vars.items():
        vals = np.round(st.solution.values).astype(int)
        for i, k in enumerate(opts):
            for hh, h in enumerate(hours):
                if vals[i, hh]:
                    new.steps[(k, tag, h)] = int(vals[i, hh])
    sigma = sig.solution.values
    # the linear prediction at the new point
    x1 = np.zeros(K)
    x1[list(choice)] = 1.0
    u1 = np.array([new.inv.get(v, 0.0) for v in inv])
    stat1 = np.array([new.stat.get(h, 0.0) for h in hours])
    steps1 = {tag: np.array([[new.steps.get((k, tag, hh), 0) * getattr(prob.options[k], f"{tag}_step_mvar")
                              for hh in hours] for k in opts]).sum(axis=0) if opts else np.zeros(H)
              for tag, opts in (("cap", cap_opts), ("ind", ind_opts))}
    pred = (lin.g0 + lin.D @ (x1 - x0) + A_inv @ (u1 - u0) + A_h[0] @ (stat1 - stat0)
            + A_h[1] @ (steps1["cap"] - steps0["cap"]) + A_h[2] @ (steps1["ind"] - steps0["ind"]))
    return new, dict(zip(keys, pred)), dict(zip(keys, sigma))


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

def _violation(prob, g):
    """``(total, worst)`` violation of ``g`` in scale units."""
    total, worst = 0.0, 0.0
    for key, val in g.items():
        lim, scale = prob.limit[key]
        tol = Q_TOL if key[0] == "q" else 1e-9
        v = max(0.0, val - lim - tol) / scale
        total += v
        worst = max(worst, v)
    return total, worst


def _penalty(prob, campus_spec):
    """Per scale unit of violation: ten times a bound on any set's cost, so
    a slack is used only where the linearisation has no feasible point.
    The bound counts a bus's switchgear at its bays now plus nine per other
    need (at most three units of each of three kinds)."""
    others = sum(1 for n in prob.needs if n.kind != "switchgear")
    bound = 0.0
    for j, idx in enumerate(prob.by_need):
        need, top = prob.needs[j], max(prob.options[k].cand.annual for k in idx)
        bound += top * (_bays(campus_spec["campus"], need.target["bus"]) + 9 * others
                        if need.kind == "switchgear" else 1.0)
    return 10.0 * bound + 1.0


def _warm_start(prob, state):
    """C8's final dispatch as a point on the MILP's options. C8 shares the
    inverters' Q within their circle; here it is clipped into the polygon,
    the MILP's capability."""
    choice = tuple(prob.x0)
    inv, stat, steps = {}, {}, {}
    rk = next((k for k in choice if prob.needs[prob.options[k].need].kind == "reactive"), None)
    for key, r in state.study.reactive.items():
        for u, q in r.unit_q.items():
            if (key, u) in prob.avail:
                lo, hi = q_range(*prob.avail[(key, u)])
                inv[(key, u)] = min(max(float(q), lo), hi)
        for name, d in r.dispatch.items():
            if d["kind"] == "statcom":
                stat[key] = stat.get(key, 0.0) + float(d["q_mvar"])
            elif d["steps"]:
                tag = "cap" if d["kind"] == "capacitor_bank" else "ind"
                steps[(rk, tag, key)] = steps.get((rk, tag, key), 0) + int(d["steps"])
    return Point(choice, inv, stat, steps)


def _label(prob, point):
    return "; ".join(f"{prob.needs[prob.options[k].need].key}: {prob.options[k].cand.label}" for k in point.choice)


def select_assets_milp(campus_spec, hourly, selection, library, req, profile,
                       criteria: SizingCriteria = SizingCriteria(), max_iter: int = MAX_ITER,
                       pcc_switchgear: bool = True, adaptive: bool = True, c8=None,
                       progress=None, should_stop=None) -> dict:
    """The joint asset choice (module docstring). ``adaptive=False`` holds
    ``beta`` at 1 and ``Delta`` at its start, for comparison. ``c8`` is
    C8's result if already computed.

    ``progress(iteration, max_iter, summary)``, when given, is called once
    per row of the history (the warm start is iteration 0), in order;
    ``summary`` is ``{"cost", "feasible", "accepted", "best_cost",
    "c8_cost", "delta", "choice"}``, ``best_cost`` the cheapest AC-feasible
    point so far (None while there is none). ``should_stop()``, when given,
    is asked before every iteration and between the finite-difference
    solves of a linearisation; once it answers True the loop ends with
    ``stop == "cancelled"`` and the result is built as at any other stop:
    the best AC-feasible point seen, or C8's result, flagged. Neither
    changes the loop otherwise.

    Returns C8's shapes (``investment``, ``cost``, ``compliance``,
    ``dispatch``, ``spec``, ``history`` (C8's escalations), ``unresolved``,
    ``pcc``, ``short_circuit``, ``scope``), plus ``milp_history`` (one row
    per iteration), ``sizing`` (the transformer sizing the margin was
    judged by, on C8's dispatch), ``lin_errors`` (per trial and constraint, AC minus the
    linear prediction, in scale units), ``comparison`` (per need, C8
    against the MILP), ``summary`` and ``fallback`` (None, or why C8's
    result is returned)."""
    if c8 is None:
        c8 = select_assets(campus_spec, hourly, selection, library, req, profile, criteria,
                           pcc_switchgear=pcc_switchgear)
    state = c8["state"]
    prob = _Problem(campus_spec, hourly, library, req, criteria, state)
    prob.should_stop = should_stop
    c8_items = [((n.choice.items, n.choice.existing) if n.choice is not None and n.pos >= 0 else None)
                for n in prob.needs]
    inv = c8["investment"]
    c8_cost = float(inv.loc[inv["status"].isin(["chosen", "kept"]), "annualised_eur_per_a"].sum())
    upper = c8_cost if state.feasible else math.inf
    prob.build_options(c8_items, upper)
    prob.q_ref = max([s for (_, s) in prob.avail.values()] + [o.q_stat for o in prob.options] + [1.0])
    penalty = _penalty(prob, campus_spec)
    point = _warm_start(prob, state)
    cur = prob.evaluate(point, linearise=True)
    if not cur.converged:
        return _fallback(c8, prob, [], "C8's point does not converge in the MILP's re-solve", c8_cost)
    delta, d_cap, d_floor = DELTA_START * prob.q_ref, DELTA_CAP * prob.q_ref, DELTA_FLOOR * prob.q_ref
    best = cur if cur.feasible else None
    stop, cuts, lin_rows = "max_iter", [], []
    try:
        lin = _linearise(prob, cur)
        fd0 = lin.fd_solves
    except _Cancelled as c:
        lin, fd0, stop = None, c.solves, "cancelled"
    history = []

    def report(row):
        history.append(row)
        if progress is not None:
            progress(int(row["iteration"]), max_iter,
                     {"cost": row["cost"], "feasible": bool(row["feasible"]), "accepted": bool(row["accepted"]),
                      "best_cost": None if best is None else best.cost, "c8_cost": c8_cost, "delta": row["delta"],
                      "choice": row["choice"]})

    v_tot, v_worst = _violation(prob, cur.g)
    report({"iteration": 0, "cost": cur.cost, "feasible": cur.feasible, "worst_violation": v_worst,
            "worst_lin_error": 0.0, "delta": delta, "beta_mean": 1.0, "beta_max": 1.0, "beta_up": "",
            "rho": math.nan, "accepted": True, "point_cost": cur.cost, "slack": 0.0, "slack_on": "",
            "choice": _label(prob, point), "fd_solves": fd0, "note": "C8's result, the warm start"})
    beta = {k: BETA0 for k in (lin.keys if lin is not None else [])}
    err = {k: 0.0 for k in beta}
    for it in range(1, max_iter + 1):
        if lin is None or (should_stop is not None and should_stop()):
            stop = "cancelled"
            break
        backoff = np.array([beta.setdefault(k, BETA0) * abs(err.get(k, 0.0)) for k in lin.keys])
        trial_pt, pred, sigma = _solve_milp(prob, lin, cur.point, backoff, delta, penalty, cuts)
        slack_on = sorted((k for k, s in sigma.items() if s > 1e-6), key=lambda k: -sigma[k])
        slack = float(sum(sigma.values()))
        row = {"iteration": it, "delta": delta, "slack": slack, "beta_up": "",
               "slack_on": "; ".join(_key_label(k) for k in slack_on[:5]), "fd_solves": 0}
        if trial_pt.same_as(cur.point):
            row.update(cost=cur.cost, feasible=cur.feasible, worst_violation=_violation(prob, cur.g)[1],
                       worst_lin_error=0.0, beta_mean=float(np.mean(list(beta.values()))),
                       beta_max=float(max(beta.values())), rho=math.nan, accepted=False, point_cost=cur.cost,
                       choice=_label(prob, cur.point), note="the MILP returns the current point")
            report(row)
            stop = "converged" if cur.feasible else "stalled"
            break
        trial = prob.evaluate(trial_pt, linearise=True)
        if not trial.converged:
            cuts.append(trial_pt.choice)
        merit_cur = cur.cost + penalty * _violation(prob, cur.g)[0]
        if trial.converged:
            t_tot, t_worst = _violation(prob, trial.g)
            merit_trial = trial.cost + penalty * t_tot
            errs = {k: trial.g[k] - pred[k] for k in lin.keys if k in trial.g}
        else:
            t_worst, merit_trial, errs = math.inf, math.inf, {}
        p_viol = sum(max(0.0, pred[k] - prob.limit[k][0] - (Q_TOL if k[0] == "q" else 1e-9)) / prob.limit[k][1]
                     for k in lin.keys)
        rho = gain_ratio(merit_cur, merit_trial, trial.cost + penalty * p_viol)
        grown = []
        if adaptive:
            for k in lin.keys:
                if k not in trial.g:
                    continue
                lim, scale = prob.limit[k]
                violated = trial.g[k] > lim + (Q_TOL if k[0] == "q" else 1e-9)
                before = beta[k]
                beta[k] = update_beta(beta[k], violated, lim - trial.g[k] > AMPLE * scale)
                if beta[k] > before:
                    grown.append(k)
            new_delta, accepted = update_delta(rho, delta, d_cap)
        else:
            new_delta, accepted = delta, rho >= RHO_BAD
        err.update(errs)
        worst_err = max((abs(e) / prob.limit[k][1] for k, e in errs.items()), default=math.nan)
        lin_rows += [{"iteration": it, "constraint": _key_label(k), "error": e / prob.limit[k][1]}
                     for k, e in errs.items() if abs(e) > 1e-9]
        if trial.feasible and (best is None or trial.cost < best.cost - 1e-9):
            best = trial
        row.update(cost=trial.cost, feasible=trial.feasible, worst_violation=t_worst, worst_lin_error=worst_err,
                   beta_mean=float(np.mean(list(beta.values()))), beta_max=float(max(beta.values())),
                   beta_up="; ".join(_key_label(k) for k in grown[:5]), rho=rho, accepted=accepted,
                   choice=_label(prob, trial_pt),
                   note="" if trial.converged else "the trial does not converge")
        delta = new_delta
        if accepted:
            row["point_cost"] = trial.cost
            moved = abs(trial.cost - cur.cost)
            cur = trial
            try:
                lin = _linearise(prob, cur)
            except _Cancelled as c:
                row["fd_solves"] = c.solves
                report(row)
                stop = "cancelled"
                break
            row["fd_solves"] = lin.fd_solves
            report(row)
            if cur.feasible and moved < COST_TOL:
                stop = "converged"
                break
        else:
            row["point_cost"] = cur.cost                       # the previous point is kept
            report(row)
        if delta < d_floor:
            stop = "delta_floor"
            break
    hist = pd.DataFrame(history, columns=HISTORY_COLUMNS)
    if best is None or (state.feasible and best.cost >= c8_cost - COST_TOL):
        why = ("no AC-feasible point was found" if best is None else
               f"no AC-feasible point is cheaper than C8's ({best.cost:,.0f} against {c8_cost:,.0f} per year)")
        if stop == "cancelled":
            why = f"cancelled after {len(history) - 1} iteration(s): {why}"
        out = _fallback(c8, prob, hist, why, c8_cost, stop)
    else:
        out = _result(c8, prob, best, hist, c8_cost, stop)
    out["lin_errors"] = pd.DataFrame(lin_rows, columns=["iteration", "constraint", "error"])
    out["sizing"] = (best.study if out["fallback"] is None else state.study).sizing
    return out


def _key_label(key):
    check, hour, cls, elem, sense = key
    where = f"{hour[0]}" if hour[1] is None else f"{hour[0]}/{hour[1]}"
    return f"{check}{'+' if sense > 0 else '-'} {elem} {cls} {where}"


def _comparison(c8, prob, best):
    inv = c8["investment"]
    rows = []
    for j, need in enumerate(prob.needs):
        c8_rows = inv[inv["need"] == need.key]
        c8_cost = float(c8_rows["annualised_eur_per_a"].sum())
        c8_label = prob.c8_labels[j]
        o = prob.options[best.point.choice[j]] if best is not None else None
        if o is None:
            m_label, m_cost = c8_label, c8_cost
        else:
            m_label = o.cand.label
            m_cost = o.cand.annual * (_bays(best.spec["campus"], need.target["bus"]) if need.kind == "switchgear" else 1)
        rows.append({"need": need.key, "c8_choice": c8_label, "milp_choice": m_label,
                     "c8_annualised_eur_per_a": c8_cost, "milp_annualised_eur_per_a": m_cost})
    return pd.DataFrame(rows)


def _fallback(c8, prob, hist, why, c8_cost, stop="fallback"):
    out = {k: v for k, v in c8.items() if k != "state"}
    hist = hist if isinstance(hist, pd.DataFrame) else pd.DataFrame(hist, columns=HISTORY_COLUMNS)
    out.update(milp_history=hist, comparison=_comparison(c8, prob, None), fallback=why,
               summary={"method": "milp", "fallback": True, "reason": why, "stop": stop, "c8_cost": c8_cost,
                        "milp_cost": c8_cost, "c8_feasible": bool(c8["state"].feasible),
                        "iterations": int(hist["iteration"].max()) if len(hist) else 0})
    return out


def _result(c8, prob, best, hist, c8_cost, stop):
    needs, spec, added = prob.realise(best.point)
    study = best.study
    investment, cost = _investment(needs, added, spec, study, prob.library, prob.lib_by_id, prob.st.periods)
    final = with_measures(prob.st.as_is, best.compliance)
    summary = {"method": "milp", "fallback": False, "reason": "", "stop": stop, "c8_cost": c8_cost,
               "milp_cost": best.cost, "c8_feasible": bool(prob.st.feasible),
               "iterations": int(hist["iteration"].max())}
    return {"investment": investment.drop(columns=["lifetime_a"]), "cost": cost, "compliance": final,
            "dispatch": _dispatch_table(study), "spec": spec, "history": c8["history"], "unresolved": [],
            "pcc": _pcc_table(study), "short_circuit": study.short_circuit, "scope": c8["scope"],
            "milp_history": hist, "comparison": _comparison(c8, prob, best), "fallback": None, "summary": summary}
