"""The plant's voltage-control strategy in the load flow, and its reactive
capability at the point of connection (PoC) across voltage (TRGC 12; plan
GC-02).

**Control modes** (``PlantControl``), each a target for the plant power
controller (PPC) at the PoC, in the generator convention (+ the plant
delivers reactive power into the grid, over-excited; - it absorbs):

* ``q_fixed``: Q_PoC = ``setpoint`` Mvar.
* ``pf``: Q_PoC = sign(``setpoint``) x P_PoC x tan(acos(|``setpoint``|)),
  P_PoC the plant's export at the PoC. A positive ``setpoint`` delivers Q
  with exported P (lagging in the generator convention), a negative one
  absorbs it (leading).
* ``v_control``: V_PoC = ``setpoint`` pu.
* ``q_of_v``: the droop ``droop_q``: no Q inside the deadband
  ``|V - v_ref| <= d``; outside it Q = -(|V - v_ref| - d) x sign(V - v_ref)
  / s x Q_max, clamped to +-Q_max, with d = ``deadband_pct`` / 100 and
  s = ``slope_pct`` / 100: the voltage change, beyond the deadband, at
  which the plant gives its full Q_max. So at V = v_ref + d + D,
  Q = -D / s x Q_max. ``q_max_mvar`` is the plant-level reference the
  caller states (the voltage-control step uses the profile's
  ``q_range_ibr`` x the summed rated P of the IBR units, the requirement
  ``campus_reactive.ibr_q_capability`` checks).

**The PPC** (``control_case``) sends one command x in [-1, 1] to every
controllable unit: each in-service IBR unit (``campus_reactive.IBR_KINDS``)
and each STATCOM gets Q_i = x x avail_i, avail_i = sqrt(S_i^2 - P_i^2) at
its present P (a STATCOM: its rating). The plant's Q is so shared pro rata
to the available Q, and every unit reaches its limit together, at x = +-1.
A unit with P = S has no headroom (avail 0, Q 0); a unit with no S is
listed apart (``no_rating``) and gets Q 0, never an assumed rating.

The outer loop solves g(x) = 0 on the PoC: g = Q_PoC - target(V_PoC,
P_PoC) for the Q modes, V_PoC - setpoint for ``v_control``. g rises with x
for a sound plant (more Q raises V and Q_PoC; the droop's target falls with
V). That is checked, not assumed: g is evaluated at both bounds first.
g(-1) > g(+1) is refused (``ContractError``), since a root found there
would mean nothing. When both have the same sign the target is out of
reach: x stays at the nearer bound, ``at_limit`` says which (``upper`` or
``lower``) and ``shortfall`` by how much (Mvar, or pu for ``v_control``).
Otherwise Brent's method finds the root. A load flow that does not
converge at any evaluation makes the case non-converged: never at a limit,
never a pass.

**The grid** behind the PoC (``grid_equivalent``): the PoC of a campus is
its ``ext_grid`` bus, where a slack would hold V_PoC fixed. The loop moves
the source to a new bus behind the Thevenin impedance Z = c x Un^2 /
S_k'' (IEC 60909, ``island_sc_seq.c_factor``), so V_PoC answers the
plant's Q. ``"min"`` is the weak grid, S_k''min with c_max (1.1) and
``rx_min``: the largest Z, the most voltage per Mvar (the gatekeeper's
conservative pairing, GC-02 V1). ``"max"`` is the strong grid, S_k''max
with c_min (1.0, 0.95 at 1 kV and below) and ``rx_max``: the smallest Z. Run both: the loop gain and the limit hits
differ between them. A campus without the PCC's S_k'' is refused, never
solved against a stiff grid.

**The PoC P-Q chart** (``poc_pq_chart``): at each V_PoC in ``V_POINTS``
(held by the source at the PoC itself) and each P from 0 to P_n of every
IBR unit, the reactive range the plant reaches at the PoC with every
controllable unit at +-avail, each inverter's S scaled by min(1, V_PoC)
(a current-limited inverter gives less MVA below 1 pu; its terminal
voltage is not known before the flow, so the PoC's stands in), and PV
offering Q at P = 0 (Q at night): after the collector and transformer
reactive losses, loads out of service and switched banks at step 0
(their rated Mvar is listed in its own columns, not judged). STATCOMs
count: they are the plant's own reactive capability. Gensets are out:
their excitation is not the PPC's. Each row is judged against the
profile's ``q_capability_pq`` at P_PoC / p_ref, in its site convention
(+ the grid supplies the site): the plant must reach -hi .. -lo in the
export convention. A P_PoC within ``P_SNAP_PU`` of the band's P range
(the P = 0 row imports the losses) is judged at the range's end; a P the
band does not state fails, as ``campus_compliance``'s band row does, so a
band that stops short is never a pass. ``q_band_v_range_pu``, when the
profile states it, narrows the voltages judged; the rest read
``reported``.

Allowed to import pandapower (``static/``); never pypsa.
"""

from __future__ import annotations

import copy
import dataclasses
import math

import pandapower as pp
import pandas as pd
from scipy.optimize import brentq

from gridspine.schema.contracts import ContractError
from gridspine.static.island_sc_seq import c_factor
from gridspine.static.campus_reactive import IBR_KINDS, _circle_q
from gridspine.templates.grid_codes import q_band_at

#: The control modes a plant's PPC runs.
MODES = ("q_fixed", "pf", "v_control", "q_of_v")
#: The PoC voltages of the P-Q chart, pu.
V_POINTS = (0.90, 0.95, 1.00, 1.05, 1.10)
#: The active power steps of the P-Q chart, per unit of each IBR unit's P_n.
P_POINTS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
#: How far, per unit of p_ref, a chart point's P may sit outside the band's P range and still be judged at its end.
P_SNAP_PU = 0.01
#: The tolerance on a v_control target, pu: a shortfall at most this big is not a limit.
V_TOL = 1e-6
#: The name of the source bus the grid equivalent adds.
SOURCE_BUS = "GRID_SOURCE"
#: The base of the equivalent's per-unit impedance, MVA.
Z_BASE_MVA = 100.0
#: Brent's tolerance on the command x.
X_TOL = 1e-12
#: A shortfall at most this many Mvar passes (the solver's own tolerance).
Q_TOL = 1e-3
#: The tolerance of "g(-1) > g(+1)": a plant with no headroom has g flat.
G_FLAT = 1e-9
#: The columns of ``poc_pq_chart``'s table.
CHART_COLUMNS = ("v_poc_pu", "p_frac", "p_poc_mw", "q_max_mvar", "q_min_mvar", "req_q_max_mvar", "req_q_min_mvar",
                 "shortfall_mvar", "status", "banks_cap_mvar", "banks_ind_mvar", "detail", "clause", "source", "basis")
CHART_BASIS = ("PoC reactive range of the IBR units and STATCOMs at +-sqrt(S'^2 - P^2), S' = S x min(1, V_PoC) "
               "(a STATCOM: its rating x min(1, V_PoC)); PV offers Q at P = 0; after collector and transformer "
               "reactive losses, with the PoC held at V; loads and gensets out, switched banks at step 0 and listed "
               "apart (rated Mvar, not judged)")


@dataclasses.dataclass(frozen=True)
class PlantControl:
    """A plant's control mode and its settings (module docstring)."""

    mode: str
    setpoint: float = 0.0
    v_ref_pu: float = 1.0
    deadband_pct: float = 0.0
    slope_pct: float = math.nan
    q_max_mvar: float = math.nan
    grid: str = "min"

    def __post_init__(self):
        if self.mode not in MODES:
            raise ContractError(f"unknown control mode {self.mode!r}; allowed {list(MODES)}")
        if self.grid not in ("min", "max"):
            raise ContractError(f"the grid equivalent is 'min' or 'max', got {self.grid!r}")
        if not _finite(self.setpoint):
            raise ContractError(f"the {self.mode} setpoint must be a finite number, got {self.setpoint!r}")
        if self.mode == "pf" and not 0 < abs(self.setpoint) <= 1:
            raise ContractError(f"a pf setpoint must be in [-1, 0) or (0, 1], got {self.setpoint!r}")
        if self.mode == "v_control" and not self.setpoint > 0:
            raise ContractError(f"a v_control setpoint must be a voltage > 0 pu, got {self.setpoint!r}")
        if self.mode == "q_of_v":
            for name in ("slope_pct", "q_max_mvar", "v_ref_pu"):
                v = getattr(self, name)
                if not (_finite(v) and v > 0):
                    raise ContractError(f"q_of_v needs {name} > 0, got {v!r}")
            if not (_finite(self.deadband_pct) and self.deadband_pct >= 0):
                raise ContractError(f"q_of_v needs deadband_pct >= 0, got {self.deadband_pct!r}")


@dataclasses.dataclass
class ControlOutcome:
    """What the PPC reached in one case (module docstring). ``target`` is in
    Mvar for the Q modes and pu for ``v_control``; ``shortfall`` likewise."""

    mode: str
    converged: bool
    v_poc_pu: float = math.nan
    p_poc_mw: float = math.nan
    q_poc_mvar: float = math.nan
    target: float = math.nan
    x: float = math.nan
    at_limit: str = ""
    shortfall: float = 0.0
    unit_q: dict = dataclasses.field(default_factory=dict)
    units_at_limit: list = dataclasses.field(default_factory=list)
    no_headroom: list = dataclasses.field(default_factory=list)
    no_rating: list = dataclasses.field(default_factory=list)
    grid: str = "min"
    z_ohm: float = math.nan
    c: float = math.nan
    note: str = ""


def _number(v) -> float:
    """``v`` as a float; NaN for None or anything that is not a number."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def droop_q(v_pu: float, v_ref_pu: float, deadband_pct: float, slope_pct: float, q_max_mvar: float) -> float:
    """The Q(V) droop's reactive power, Mvar (module docstring)."""
    d, s = deadband_pct / 100.0, slope_pct / 100.0
    dv = v_pu - v_ref_pu
    if abs(dv) <= d:
        return 0.0
    q = -math.copysign(abs(dv) - d, dv) / s * q_max_mvar
    return max(-q_max_mvar, min(q_max_mvar, q))


def _ext_grid(net):
    eg = net.ext_grid[net.ext_grid["in_service"].astype(bool)]
    if len(eg) != 1:
        raise ContractError(f"voltage control needs exactly one grid connection in service, found {len(eg)}")
    return eg.index[0]


def grid_equivalent(net, case: str = "min") -> tuple[int, float, float]:
    """Move the grid source behind its Thevenin impedance (module docstring;
    mutates ``net``). Returns ``(impedance index, Z ohm, c)``."""
    if case not in ("min", "max"):
        raise ContractError(f"the grid equivalent is 'min' or 'max', got {case!r}")
    i = _ext_grid(net)
    sk, rx = _number(net.ext_grid.at[i, f"s_sc_{case}_mva"]), _number(net.ext_grid.at[i, f"rx_{case}"])
    if not (math.isfinite(sk) and sk > 0):
        raise ContractError(f"the PCC has no S_k''{case} (got {sk!r}): voltage control needs the grid's strength")
    if not (math.isfinite(rx) and rx >= 0):
        raise ContractError(f"the PCC's rx_{case} must be a number >= 0, got {rx!r}")
    pcc = int(net.ext_grid.at[i, "bus"])
    vn = float(net.bus.at[pcc, "vn_kv"])
    c = c_factor(vn, "max" if case == "min" else "min")
    z_ohm = c * vn ** 2 / sk
    z_pu = c * Z_BASE_MVA / sk
    x_pu = z_pu / math.sqrt(1.0 + rx ** 2)
    src = pp.create_bus(net, vn_kv=vn, name=SOURCE_BUS)
    net.ext_grid.at[i, "bus"] = src
    imp = pp.create_impedance(net, src, pcc, rft_pu=rx * x_pu, xft_pu=x_pu, sn_mva=Z_BASE_MVA,
                              name="grid equivalent")
    return imp, z_ohm, c


def ppc_units(net, campus, s_scale: float = 1.0) -> tuple[dict, list]:
    """``({sgen index: avail Mvar}, [units with no rating])`` of the
    controllable units in service (module docstring), each S times
    ``s_scale`` (the chart's current limit below 1 pu)."""
    ibr = {str(u) for u, k in campus.units["kind"].items() if k in IBR_KINDS}
    comp = getattr(campus, "compensation", None)
    statcom = set() if comp is None or comp.empty else {str(n) for n, k in comp["kind"].items() if k == "statcom"}
    avail, no_rating = {}, []
    for i in net.sgen.index:
        name = str(net.sgen.at[i, "name"])
        if not bool(net.sgen.at[i, "in_service"]) or name not in ibr | statcom:
            continue
        s = _number(net.sgen.at[i, "sn_mva"])
        if not (math.isfinite(s) and s > 0):
            no_rating.append(name)
            avail[i] = 0.0
            continue
        s *= s_scale
        avail[i] = s if name in statcom else _circle_q(s, abs(float(net.sgen.at[i, "p_mw"])))
    return avail, no_rating


def share(net, avail: dict, x: float) -> None:
    """Put the PPC command on the units: Q_i = x x avail_i (mutates ``net``)."""
    for i, a in avail.items():
        net.sgen.at[i, "q_mvar"] = x * a


def _poc(net, imp, pcc) -> tuple[float, float, float]:
    """``(V, P export, Q export)`` at the PoC, after a solved load flow."""
    return (float(net.res_bus.at[pcc, "vm_pu"]), float(net.res_impedance.at[imp, "p_to_mw"]),
            float(net.res_impedance.at[imp, "q_to_mvar"]))


def _target(control: PlantControl, v: float, p: float) -> float:
    if control.mode == "q_fixed":
        return control.setpoint
    if control.mode == "pf":
        pf = control.setpoint
        return math.copysign(p * math.tan(math.acos(abs(pf))), pf)
    if control.mode == "v_control":
        return control.setpoint
    return droop_q(v, control.v_ref_pu, control.deadband_pct, control.slope_pct, control.q_max_mvar)


class _NotConverged(Exception):
    pass


def control_case(net, campus, imp: int, control: PlantControl, run=pp.runpp) -> ControlOutcome:
    """Run the PPC's outer loop on ``net``, already carrying the grid
    equivalent ``imp`` (module docstring; mutates ``net``, which is left
    solved at the outcome's x)."""
    pcc = int(net.impedance.at[imp, "to_bus"])
    avail, no_rating = ppc_units(net, campus)
    names = {i: str(net.sgen.at[i, "name"]) for i in avail}
    no_headroom = [names[i] for i, a in avail.items() if a <= 0 and names[i] not in no_rating]
    seen = {}

    def evaluate(x):
        share(net, avail, x)
        try:
            run(net)
        except pp.LoadflowNotConverged as e:
            raise _NotConverged from e
        v, p, q = _poc(net, imp, pcc)
        seen["v"], seen["p"], seen["q"] = v, p, q
        return v, p, q

    def g(x):
        v, p, q = evaluate(x)
        if control.mode == "v_control":
            return v - control.setpoint
        return q - _target(control, v, p)

    base = ControlOutcome(control.mode, False, no_headroom=no_headroom, no_rating=no_rating, grid=control.grid)
    try:
        g_lo, g_hi = g(-1.0), g(1.0)
        if g_lo > g_hi + G_FLAT:
            raise ContractError(f"{control.mode}: g(-1) = {g_lo:.6g} > g(+1) = {g_hi:.6g}; more Q from the plant "
                                "lowers the PoC's Q or voltage, so the control loop has no meaning (check the "
                                "units' and the grid's sign)")
        at_limit, shortfall = "", 0.0
        tol = V_TOL if control.mode == "v_control" else Q_TOL
        if g_lo > 0:                    # out of reach below; within the tolerance it is met, at the bound
            x = -1.0
            if g_lo > tol:
                at_limit, shortfall = "lower", g_lo
        elif g_hi < 0:
            x = 1.0
            if -g_hi > tol:
                at_limit, shortfall = "upper", -g_hi
        else:
            x = brentq(g, -1.0, 1.0, xtol=X_TOL)
        g(x)
    except _NotConverged:
        return dataclasses.replace(base, note="the load flow did not converge inside the control loop")
    v, p, q = seen["v"], seen["p"], seen["q"]
    unit_q = {names[i]: float(net.sgen.at[i, "q_mvar"]) for i in avail}
    limited = [names[i] for i, a in avail.items() if a > 0] if at_limit else []
    return dataclasses.replace(base, converged=True, v_poc_pu=v, p_poc_mw=p, q_poc_mvar=q,
                               target=_target(control, v, p), x=x, at_limit=at_limit, shortfall=shortfall,
                               unit_q=unit_q, units_at_limit=limited)


def _band_v_range(profile: dict):
    r = profile.get("q_band_v_range_pu")
    return (float(r["min"]), float(r["max"])) if r else None


def poc_pq_chart(campus, profile: dict, p_ref_mw: float | None, v_points=V_POINTS, p_points=P_POINTS,
                 run=pp.runpp) -> pd.DataFrame:
    """The PoC P-Q chart across voltage (module docstring): one row per
    V_PoC and P step. ``status`` is ``pass``/``fail`` against the band,
    ``reported`` where the band, its P range, ``p_ref`` or the judged
    voltage range does not reach, ``not converged`` where a flow fails."""
    lim = profile.get("q_capability_pq")
    v_range = _band_v_range(profile)
    banks = getattr(campus, "compensation", None)
    cap = ind = 0.0
    if banks is not None and not banks.empty:
        cap = float(banks.loc[banks["kind"] == "capacitor_bank", "q_mvar"].sum())
        ind = float(banks.loc[banks["kind"] == "shunt_reactor", "q_mvar"].sum())
    ibr = {str(u): float(r["p_mw"]) for u, r in campus.units.iterrows() if r["kind"] in IBR_KINDS}
    base = copy.deepcopy(campus.net)
    base.load["in_service"] = False
    if len(base.shunt):
        base.shunt["step"] = 0
    eg = _ext_grid(base)
    for i in base.sgen.index:
        name = str(base.sgen.at[i, "name"])
        if name in ibr:
            base.sgen.at[i, "in_service"] = True
        elif name in campus.units.index:
            base.sgen.at[i, "in_service"] = False       # gensets and UPS: not the PPC's
    rows = []
    for v in v_points:
        for frac in p_points:
            net = copy.deepcopy(base)
            net.ext_grid.at[eg, "vm_pu"] = v
            for i in net.sgen.index:
                name = str(net.sgen.at[i, "name"])
                if name in ibr:
                    net.sgen.at[i, "p_mw"] = frac * ibr[name]
            avail, _ = ppc_units(net, campus, s_scale=min(1.0, v))
            q_ends, p_poc = [], math.nan
            try:
                for x in (1.0, -1.0):
                    share(net, avail, x)
                    run(net)
                    q_ends.append(-float(net.res_ext_grid.at[eg, "q_mvar"]))
                    p_poc = -float(net.res_ext_grid.at[eg, "p_mw"])
            except pp.LoadflowNotConverged:
                rows.append({"v_poc_pu": v, "p_frac": frac, "status": "not converged", "banks_cap_mvar": cap,
                             "banks_ind_mvar": ind, "detail": f"V {v:.2f} pu, P {frac:g} x P_n: the load flow "
                                                             "did not converge", "basis": CHART_BASIS})
                continue
            rows.append(_judge(lim, v_range, p_ref_mw, v, frac, p_poc, q_ends[0], q_ends[1], cap, ind))
    return pd.DataFrame(rows, columns=list(CHART_COLUMNS))


def _judge(lim, v_range, p_ref, v, frac, p, q_max, q_min, cap, ind) -> dict:
    row = {"v_poc_pu": v, "p_frac": frac, "p_poc_mw": p, "q_max_mvar": q_max, "q_min_mvar": q_min,
           "req_q_max_mvar": math.nan, "req_q_min_mvar": math.nan, "shortfall_mvar": math.nan,
           "status": "reported", "banks_cap_mvar": cap, "banks_ind_mvar": ind,
           "clause": lim["clause"] if lim else "", "source": lim["source"] if lim else "", "basis": CHART_BASIS}
    where = f"V {v:.2f} pu, P {p:.2f} MW"
    reach = f"reaches {q_min:.2f} to {q_max:.2f} Mvar"
    if not lim:
        return {**row, "detail": f"{where} {reach}; the profile states no q_capability_pq"}
    if not (p_ref and p_ref > 0):
        return {**row, "detail": f"{where} {reach}; no connection capacity (p_ref) to scale the band"}
    p_pu = -p / p_ref                                  # the band's P is the site's import
    lo_p, hi_p = lim["points"][0][0], lim["points"][-1][0]
    if lo_p - P_SNAP_PU <= p_pu < lo_p or hi_p < p_pu <= hi_p + P_SNAP_PU:
        p_pu = min(max(p_pu, lo_p), hi_p)
    band = q_band_at(lim, p_pu)
    if band is None:
        return {**row, "status": "fail",
                "detail": f"{where} ({-p / p_ref:.3f} pu import) {reach}; the band does not state this P "
                          f"(it covers {lo_p:g} to {hi_p:g} pu)"}
    need_hi, need_lo = -band[0] * p_ref, -band[1] * p_ref   # export convention: -hi .. -lo
    row.update(req_q_max_mvar=need_hi, req_q_min_mvar=need_lo)
    if v_range is not None and not v_range[0] <= v <= v_range[1]:
        return {**row, "detail": f"{where} {reach}; outside the band's stated voltage range "
                                 f"{v_range[0]:g} to {v_range[1]:g} pu: reported, not judged"}
    short = max(need_hi - q_max, q_min - need_lo, 0.0)
    status = "fail" if short > Q_TOL else "pass"
    return {**row, "shortfall_mvar": short, "status": status,
            "detail": f"{where} {reach} for a required {need_lo:.2f} to {need_hi:.2f} Mvar"
                      + (f": {short:.2f} Mvar short" if status == "fail" else "")}


#: The columns of ``outcome_rows``' table (one row per hour, grid equivalent and case).
OUTCOME_COLUMNS = ("period", "hour", "grid", "case", "mode", "converged", "v_poc_pu", "p_poc_mw", "q_poc_mvar",
                   "target", "x", "at_limit", "shortfall", "units_at_limit", "no_headroom", "no_rating", "z_ohm",
                   "c", "note")


def outcome_rows(key: dict, grid: str, flows: dict) -> list:
    """One ``OUTCOME_COLUMNS`` row per case of ``solve_cases(..., control=...)``."""
    out = []
    for case, f in flows.items():
        o = f.control
        out.append({**key, "grid": grid, "case": case, "mode": o.mode, "converged": o.converged,
                    "v_poc_pu": o.v_poc_pu, "p_poc_mw": o.p_poc_mw, "q_poc_mvar": o.q_poc_mvar,
                    "target": o.target, "x": o.x, "at_limit": o.at_limit, "shortfall": o.shortfall,
                    "units_at_limit": ";".join(o.units_at_limit), "no_headroom": ";".join(o.no_headroom),
                    "no_rating": ";".join(o.no_rating), "z_ohm": o.z_ohm, "c": o.c, "note": o.note})
    return out


def droop_from(profile: dict, campus) -> tuple[PlantControl | None, str]:
    """The profile's Q(V) droop as a ``q_of_v`` control, or ``(None, why)``
    when a setting is not stated. Q_max is the plant-level reference
    ``q_range_ibr`` x the summed rated P of the IBR units (module
    docstring)."""
    missing = [k for k in ("q_v_slope_pct", "voltage_deadband_pct", "q_range_ibr") if k not in profile]
    if missing:
        return None, f"the profile states no {', '.join(missing)}"
    p_n = float(sum(float(r["p_mw"]) for _, r in campus.units.iterrows() if r["kind"] in IBR_KINDS))
    if not p_n > 0:
        return None, "the campus has no IBR unit to run the droop"
    q_max = float(profile["q_range_ibr"]["value"]) * p_n
    return PlantControl("q_of_v", deadband_pct=float(profile["voltage_deadband_pct"]["value"]),
                        slope_pct=float(profile["q_v_slope_pct"]["value"]), q_max_mvar=q_max), ""


def scorecard_rows(profile: dict, chart: pd.DataFrame, outcomes: pd.DataFrame | None, droop_why: str,
                   grid_note: str = "") -> pd.DataFrame:
    """The voltage-control rows of the scorecard, in ``campus_compliance``'s
    row shape: the modes the code asks for (reported), the Q(V) droop in the
    load flow, and the PoC P-Q chart against the band. A row whose input the
    profile does not state is ``unknown``, never ``pass``."""
    from gridspine.static.campus_compliance import COLUMNS, _row, _unknown

    rows = []
    modes = profile.get("voltage_control_modes")
    if modes:
        rows.append(_row("voltage_control_modes", "reported", "reported", math.nan, math.nan, "", None,
                         modes["clause"], modes["source"],
                         f"the code asks for {', '.join(modes['value'])}; the plant controller runs "
                         f"{', '.join(MODES)}"))
    else:
        rows.append(_unknown("voltage_control_modes", "", "", "assumed",
                             "the profile states no voltage_control_modes"))
    slope = profile.get("q_v_slope_pct") or {}
    if outcomes is None or outcomes.empty:
        rows.append(_unknown("q_v_droop", "Mvar", slope.get("clause", ""), slope.get("source", "assumed"),
                             droop_why or "no hour was run"))
    else:
        bad = outcomes[~outcomes["converged"].astype(bool)]
        lim = outcomes[outcomes["at_limit"].fillna("").astype(str) != ""]
        if len(bad):
            r = bad.iloc[0]
            status, value, where = "fail", math.nan, r
            detail = f"{len(bad)} case(s) did not converge under the droop; first {r['case']} on the S_k''{r['grid']} grid"
        elif len(lim):
            r = lim.loc[lim["shortfall"].astype(float).idxmax()]
            status, value, where = "fail", float(r["shortfall"]), r
            detail = (f"the plant cannot follow its droop in {len(lim)} case(s); worst {r['case']} on the "
                      f"S_k''{r['grid']} grid at V {r['v_poc_pu']:.4f} pu: {r['shortfall']:.2f} Mvar short "
                      f"({r['at_limit']} limit)")
        else:
            r = outcomes.loc[outcomes["v_poc_pu"].astype(float).sub(1.0).abs().idxmax()]
            status, value, where = "pass", 0.0, r
            detail = (f"the droop holds in every case on both grid equivalents; widest V {r['v_poc_pu']:.4f} pu "
                      f"({r['case']}, S_k''{r['grid']}) with Q {r['q_poc_mvar']:.2f} Mvar")
        if grid_note:
            detail += f"; {grid_note}"
        rows.append(_row("q_v_droop", status, status, value, 0.0, "Mvar",
                         (int(where["period"]), int(where["hour"])), slope.get("clause", ""),
                         slope.get("source", "assumed"), detail, case=where["case"]))
    lim = profile.get("q_capability_pq") or {}
    judged = chart[chart["status"].isin(["pass", "fail", "not converged"])]
    if judged.empty:
        why = chart["detail"].iloc[0] if len(chart) else "the chart has no point"
        rows.append(_unknown("poc_pq_band", "Mvar", lim.get("clause", ""), lim.get("source", "assumed"),
                             f"no point judged: {why}"))
    else:
        failed = judged[judged["status"] != "pass"]
        if len(failed):
            # a point with no shortfall number (not converged, or a P the band does not state) is named first
            blind = failed[failed["shortfall_mvar"].isna()]
            r = blind.iloc[0] if len(blind) else failed.loc[failed["shortfall_mvar"].astype(float).idxmax()]
            status = "fail"
        else:
            margin = pd.concat([judged["q_max_mvar"] - judged["req_q_max_mvar"],
                                judged["req_q_min_mvar"] - judged["q_min_mvar"]], axis=1).min(axis=1)
            r, status = judged.loc[margin.idxmin()], "pass"
        detail = f"{len(judged)} of {len(chart)} points judged, {len(failed)} short; " + str(r["detail"])
        rows.append(_row("poc_pq_band", status, status, r["shortfall_mvar"], 0.0, "Mvar", None,
                         lim.get("clause", ""), lim.get("source", "assumed"), detail))
    return pd.DataFrame(rows, columns=COLUMNS)
