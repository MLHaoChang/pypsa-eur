"""Unbalanced faults by sequence networks, grid-connected and islanded (plan I5b).

Plan: ``docs/superpowers/plans/2026-10-07-campus-island-operation.md``.

pandapower's ``fault="1ph"`` cannot be used for the island (spike 6): it adds
converter current where there is no zero-sequence path, and it gives every
``gen`` a fixed zero-sequence shunt, so genset earthing cannot be written.
This module builds the three sequence networks itself and solves each fault
as one linear system:

    Y1 dV1 + e_f I1 = I_conv        Y2 V2 + e_f I2 = 0        Y0 V0 + e_f I0 = 0

with ``V1 = c + dV1`` (IEC 60909's equivalent voltage source c Un / sqrt 3
at the fault, every machine passive behind its corrected impedance) and the
fault's own three equations:

* 3-phase: V1_f = 0, I2 = I0 = 0;
* 1-phase to earth (phase a): I1 = I2 = I0, V1_f + V2_f + V0_f = 0;
* 2-phase (b to c, clear of earth): I1 = -I2, I0 = 0, V1_f = V2_f.

So Ik1 = 3 c / |Z1 + Z2 + Z0| and Ik2 = sqrt 3 c / |Z1 + Z2| (in pu) with only
machines, and a converter adds its current through the network's
distribution factors. IEC 60909-0:2016 adds those contributions by
magnitude, and so does I5 (``ISC.PHASE_RULE``): each converter is injected
at the angle -arg Z1_fj of its transfer impedance to the fault, so its
part of V1_f is in phase with c. Without a machine (Z1 undefined) every
converter is at 0, in phase with the others. A fault the passive network cannot
carry (pure positive-sequence current sources with no negative-sequence
path) is reported as not determinable, never as a number.

**What each element is:**

* cable: series in all three; Z0 from ``r0``/``x0`` (else drafted, below);
  R at the end-of-fault temperature in the min case, as I5;
* transformer: series Zk in the positive and negative sequence; in the zero
  sequence by its windings (``Campus.windings``): earthed star to earthed
  star is series Zk + 3 Z_N each side; an earthed star against a delta is a
  shunt Zk + 3 Z_N on the star's bus; anything else is open. A network
  transformer takes K_T, a genset's unit transformer the genset's K_S/K_SO
  (uncorrected for a fault inside the unit, at the genset's own bus, as
  pandapower does); transformers keep their rated ratio (an off-nominal
  tap against the bus voltages);
* genset: a shunt K (R + j x''d) / (R2 + j x2) / (R0 + j x0) + 3 R_N, with K
  as in I5 (K_G, or K_S/K_SO behind its unit transformer for a fault
  outside the unit); unearthed: no zero-sequence shunt;
* converter: a positive-sequence current source at its mode's k (I5's rules,
  min case included); optionally a negative-sequence admittance
  ``neg_seq`` = k2 on its rating (i2 = k2 u2, the grid-code k-factor),
  with its peak current held at its limit, |I1| + |I2| <= k In: both are
  scaled down together until they fit (a fixed point, as I4's current
  limit); never a zero-sequence path;
* the grid (grid-connected only): Z1 = Z2 = c Un^2 / Sk'', Z0 from the
  operator's ``x0x`` and ``r0x0``;
* an earthing transformer: a zero-sequence shunt j x0 + 3 R_N.

Loads, line capacitance and magnetising branches are left out, as IEC 60909
does. The earth-fault current of an unearthed zone is not IEC 60909; it is
reported apart as the cables' capacitive current sqrt 3 omega C0 Un.
"""
import dataclasses
import math

import numpy as np
import pandas as pd

from gridspine.static import island_sc as ISC
from gridspine.static.shortcircuit import LINE_ENDTEMP_DEGREE

#: The operator a = e^(j 120 deg).
A = complex(-0.5, math.sqrt(3) / 2)
#: IEC 60909-0 resistance temperature coefficient for the min case.
ALPHA_SC = 0.004
#: A drafted cable zero sequence: R0 = R + 3 R_screen with a 25 mm2 copper
#: screen (0.727 ohm/km at 20 degC), X0 = X, C0 = C (each core screened).
R_SCREEN_OHM_PER_KM = 0.727
#: A drafted genset zero-sequence reactance, as a share of x''d.
GENSET_X0_RATIO = 0.4
FAULTS = ("1ph", "2ph", "3ph")
#: The grid's zero-sequence shunt in ``SeqNet.earth``.
GRID = "GRID"
METHOD = "sequence networks (IEC 60909 equivalent source; converters as in I5)"
NO_EARTH = "no earth reference"
NO_NEG = ("not determinable: no negative-sequence path, so the fault current is set by the converters' "
          "unbalanced-fault control (neg_seq), not by the network")
BLIND_E = "earth-fault protection blind in island"
SEES_E = "sees the earth fault"

LEDGER = (
    "island_sc_seq: transformer phase shifts are not modelled; fault-bus currents and residual currents do not "
    "depend on them in a network without phase-shift loops",
    "island_sc_seq: a transformer's zero-sequence impedance is its Zk (vk0 = vk, assumed); magnetising branches "
    "are open (a three-limb core's zero-sequence magnetising path is neglected)",
    "island_sc_seq: a cable without zero-sequence data is drafted R0 = R + 3 x 0.727 ohm/km, X0 = X, C0 = C "
    "(assumed: a 25 mm2 copper screen bonded both ends)",
    "island_sc_seq: a genset without x2_pu / x0_pu takes x2 = x''d and x0 = 0.4 x''d (assumed); one without "
    "island data is unearthed (assumed)",
    "island_sc_seq: the grid's zero sequence without x0x / r0x0 is X0 = X1, R0/X0 = R/X (assumed, earthed)",
    "island_sc_seq: a converter with neg_seq keeps |I1| + |I2| within its limit by scaling both together "
    "(first pass; real controls set their own priority between the sequences)",
    "island_sc_seq: the earth-fault current is its initial value; a genset's decrement is not applied",
    "island_sc_seq: a STATCOM feeds the max case as in I5 and is left out of the min case",
    "island_sc_seq: an unearthed zone's earth-fault current is the cables' capacitive current at Un, "
    "not IEC 60909",
)


def c_factor(vn_kv, case):
    """IEC 60909 voltage factor, as pandapower (LV tolerance 10 %)."""
    if case == "max":
        return 1.1
    return 1.0 if vn_kv > 1.0 else 0.95


@dataclasses.dataclass
class Branch:
    name: str
    kind: str                    # line or trafo
    i: int
    j: int
    y1: complex                  # series, positive and negative sequence (on the j side's base)
    y0: complex | None = None    # series, zero sequence
    shunt0: tuple | None = None  # (bus, y): a transformer's zero-sequence shunt on its earthed star side
    t: float = 1.0               # off-nominal ratio at the i side (a transformer's rated against its buses')

    def current(self, y, v):
        """The branch current at its i side, pu, for series admittance ``y``."""
        return y / self.t ** 2 * v[self.i] - y / self.t * v[self.j]


@dataclasses.dataclass
class SeqNet:
    buses: list                  # bus names, in net order
    vn: np.ndarray               # kV
    y1: np.ndarray               # pu on sbase
    y2: np.ndarray
    y0: np.ndarray
    inj: np.ndarray              # fixed positive-sequence current sources (STATCOMs), pu, in phase
    conv: list                   # converters: (unit, bus, i_pu, y2_pu): current source and neg_seq admittance
    branches: list
    earth: dict                  # bus -> [(element, assumed)] zero-sequence shunts to earth
    c0_f: dict                   # line name -> its zero-sequence capacitance, farad
    case: str
    sbase: float
    f_hz: float


@dataclasses.dataclass
class Fault:
    fault: str
    bus: str
    ik_ka: float                 # the fault current (1ph: 3 I0; 2ph: phase b; 3ph: I1); NaN if not determinable
    earth_ka: float              # 3 I0 at the fault
    determinable: bool
    earth_path: bool
    note: str
    branch: pd.DataFrame         # branch, kind, i_e_ka (residual 3 I0, where it flows)


def _p(campus, element, param):
    p = campus.params
    row = p[(p["element"] == element) & (p["param"] == param)]
    return float(row["value"].iloc[0]) if len(row) else None


def _assumed(campus, element):
    return any(a.startswith(f"{element}:") for a in campus.assumptions)


def _unit_trafos(campus, sources):
    """``{transformer index: genset id}`` for the online gensets behind their
    own unit transformer (the rule of ``ISC.machine_net``)."""
    net, out = campus.net, {}
    for uid in sources:
        block = campus.island["units"].get(uid)
        if campus.units.at[uid, "kind"] != "genset" or not (block and block.get("unit_transformer")):
            continue
        bus = int(net.bus.index[net.bus["name"] == campus.units.at[uid, "bus"]][0])
        tr = net.trafo.index[(net.trafo["lv_bus"] == bus) & net.trafo["in_service"].astype(bool)]
        if len(tr) != 1:
            raise ValueError(f"genset {uid} has unit_transformer set, but its bus has {len(tr)} step-up "
                             "transformers; it needs exactly one")
        out[int(tr[0])] = uid
    return out


def _x_own(net, k):
    """A transformer's short-circuit reactance in pu of its own rating."""
    zk, rk = float(net.trafo.at[k, "vk_percent"]) / 100.0, float(net.trafo.at[k, "vkr_percent"]) / 100.0
    return math.sqrt(zk ** 2 - rk ** 2)


def genset_k(campus, uid, unit_trafo=None):
    """IEC 60909's correction for a genset: K_G, or K_S / K_SO behind its
    unit transformer (index ``unit_trafo``), as pandapower applies them."""
    net = campus.net
    block = campus.island["units"].get(uid)
    xd = ISC._param(campus, uid, "xd_pp")
    cos_phi = block["values"]["cos_phi_r"] if block else ISC.GENSET_COS_PHI
    sin_phi = math.sqrt(1.0 - cos_phi ** 2)
    if unit_trafo is None:
        return 1.1 / (1.0 + xd * sin_phi)
    t = net.trafo.loc[unit_trafo]
    u_nq = float(net.bus.at[int(t["hv_bus"]), "vn_kv"])
    u_rg = float(net.bus.at[int(t["lv_bus"]), "vn_kv"])
    ratio = (u_nq / u_rg) * (float(t["vn_lv_kv"]) / float(t["vn_hv_kv"]))
    if block.get("unit_transformer_oltc"):
        return ratio ** 2 * 1.1 / (1.0 + abs(xd - _x_own(net, unit_trafo)) * sin_phi)
    return ratio * 1.1 / (1.0 + xd * sin_phi)


def sequence_net(campus, sources, island=True, case="max", gfl_min_case="drop", gfm_override=None,
                 inside=None) -> SeqNet:
    """The three sequence networks of the campus with ``sources`` online
    (``{unit: MVA}``, as ``ISC._sources``), islanded or grid-connected.
    ``inside`` is a fault bus inside a power station unit (a genset's own
    bus behind its unit transformer): that unit then takes K_G and its
    transformer no correction."""
    net = campus.net
    sbase = float(net.sn_mva)
    names = [str(b) for b in net.bus["name"]]
    at = {int(b): n for n, b in enumerate(net.bus.index)}
    by_name = {nm: n for n, nm in enumerate(names)}
    vn = np.array([float(v) for v in net.bus["vn_kv"]])
    zb = vn ** 2 / sbase
    n = len(names)
    y1, y2, y0 = (np.zeros((n, n), dtype=complex) for _ in range(3))
    inj = np.zeros(n, dtype=complex)
    earth = {k: [] for k in range(n)}
    branches, c0_f, conv = [], {}, []

    def series(y, a, b, yb, t=1.0):
        y[a, a] += yb / t ** 2
        y[b, b] += yb
        y[a, b] -= yb / t
        y[b, a] -= yb / t

    f_t = 1.0 + ALPHA_SC * (LINE_ENDTEMP_DEGREE - 20.0) if case == "min" else 1.0
    for k in net.line.index[net.line["in_service"].astype(bool)]:
        name = str(net.line.at[k, "name"])
        a, b = at[int(net.line.at[k, "from_bus"])], at[int(net.line.at[k, "to_bus"])]
        length, par = float(net.line.at[k, "length_km"]), float(net.line.at[k, "parallel"])
        r, x = float(net.line.at[k, "r_ohm_per_km"]), float(net.line.at[k, "x_ohm_per_km"])
        r0 = _p(campus, name, "r0_ohm_per_km")
        x0 = _p(campus, name, "x0_ohm_per_km")
        c0 = _p(campus, name, "c0_nf_per_km")
        r0 = r + 3.0 * R_SCREEN_OHM_PER_KM if r0 is None else r0
        x0 = x if x0 is None else x0
        c0 = float(net.line.at[k, "c_nf_per_km"]) if c0 is None else c0
        yb1 = 1.0 / (complex(r * f_t, x) * length / par / zb[a])
        yb0 = 1.0 / (complex(r0 * f_t, x0) * length / par / zb[a])
        series(y1, a, b, yb1)
        series(y2, a, b, yb1)
        series(y0, a, b, yb0)
        branches.append(Branch(name, "line", a, b, yb1, y0=yb0))
        c0_f[name] = c0 * 1e-9 * length * par

    units = _unit_trafos(campus, sources)
    for k in net.trafo.index[net.trafo["in_service"].astype(bool)]:
        name = str(net.trafo.at[k, "name"])
        a, b = at[int(net.trafo.at[k, "hv_bus"])], at[int(net.trafo.at[k, "lv_bus"])]
        sn = float(net.trafo.at[k, "sn_mva"]) * float(net.trafo.at[k, "parallel"])
        hv_t, lv_t = float(net.trafo.at[k, "vn_hv_kv"]), float(net.trafo.at[k, "vn_lv_kv"])
        # Zk on the LV bus's base at the rated LV voltage; the rated ratio is an off-nominal tap at HV
        zk = float(net.trafo.at[k, "vk_percent"]) / 100.0 * sbase / sn * (lv_t / vn[b]) ** 2
        rk = float(net.trafo.at[k, "vkr_percent"]) / 100.0 * sbase / sn * (lv_t / vn[b]) ** 2
        t = (hv_t / vn[a]) / (lv_t / vn[b])
        if k in units:
            corr = 1.0 if campus.units.at[units[k], "bus"] == inside else genset_k(campus, units[k], k)
        else:
            corr = 0.95 * 1.1 / (1.0 + 0.6 * _x_own(net, k))           # K_T, network transformer
        z = corr * complex(rk, math.sqrt(zk ** 2 - rk ** 2))
        series(y1, a, b, 1.0 / z, t)
        series(y2, a, b, 1.0 / z, t)
        br = Branch(name, "trafo", a, b, 1.0 / z, t=t)
        w = campus.windings.get(name, {"hv": "D", "hv_earth": None, "lv": "Y", "lv_earth": "solid"})
        zn = {}
        for side, bus in (("hv", a), ("lv", b)):
            e = w[f"{side}_earth"]
            if e in ("solid", "resistance"):
                rn = _p(campus, name, f"r_n_{side}_ohm") if e == "resistance" else 0.0
                zn[side] = 3.0 * rn / zb[bus]
        assumed = _assumed(campus, name)
        if "hv" in zn and "lv" in zn:
            br.y0 = 1.0 / (z + zn["hv"] / t ** 2 + zn["lv"])
            series(y0, a, b, br.y0, t)
        elif "hv" in zn and w["lv"] == "D":
            br.shunt0 = (a, 1.0 / (t ** 2 * z + zn["hv"]))
        elif "lv" in zn and w["hv"] == "D":
            br.shunt0 = (b, 1.0 / (z + zn["lv"]))
        if br.shunt0 is not None:
            y0[br.shunt0[0], br.shunt0[0]] += br.shunt0[1]
            earth[br.shunt0[0]].append((name, assumed))
        branches.append(br)

    if not island:
        for k in net.ext_grid.index[net.ext_grid["in_service"].astype(bool)]:
            bus = at[int(net.ext_grid.at[k, "bus"])]
            s_sc = float(net.ext_grid.at[k, f"s_sc_{case}_mva"])
            rx = float(net.ext_grid.at[k, f"rx_{case}"])
            zabs = c_factor(vn[bus], case) * sbase / s_sc
            x1 = zabs / math.sqrt(1.0 + rx ** 2)
            y1[bus, bus] += 1.0 / complex(rx * x1, x1)
            y2[bus, bus] += 1.0 / complex(rx * x1, x1)
            pcc = names[bus]
            x0x, r0x0 = _p(campus, pcc, f"x0x_{case}"), _p(campus, pcc, f"r0x0_{case}")
            x0 = (1.0 if x0x is None else x0x) * x1
            y0[bus, bus] += 1.0 / complex((rx if r0x0 is None else r0x0) * x0, x0)
            earth[bus].append((GRID, x0x is None))

    for uid, s_mva in sources.items():
        u = campus.units.loc[uid]
        bus = by_name[str(u["bus"])]
        block = campus.island["units"].get(uid)
        if u["kind"] == "genset":
            xd, rx = ISC._param(campus, uid, "xd_pp"), ISC._param(campus, uid, "rx_sc")
            unit_trafo = next((k for k, g in units.items() if g == uid), None)
            kg = genset_k(campus, uid, None if u["bus"] == inside else unit_trafo)
            vals = block["values"] if block else {}
            x2 = vals.get("x2_pu", xd)
            x0 = vals.get("x0_pu", GENSET_X0_RATIO * xd)
            y1[bus, bus] += 1.0 / (kg * complex(rx * xd, xd) * sbase / s_mva)
            y2[bus, bus] += 1.0 / (kg * complex(rx * x2, x2) * sbase / s_mva)
            earthing = block["neutral_earthing"] if block else "isolated"
            if earthing != "isolated":
                rn = vals["r_n_ohm"] if earthing == "resistance" else 0.0
                y0[bus, bus] += 1.0 / (kg * complex(rx * x0, x0) * sbase / s_mva + 3.0 * rn / zb[bus])
                earth[bus].append((uid, False))
            continue
        if case == "min" and not ISC._counted_in_min(campus, uid, gfl_min_case):
            continue
        k2 = (block or {}).get("values", {}).get("neg_seq", 0.0)
        conv.append((uid, bus, ISC.converter_k(campus, uid, case, gfm_override) * s_mva / sbase,
                     -1j * k2 * s_mva / sbase))

    if case == "max":
        for i in net.sgen.index[net.sgen["in_service"].astype(bool)]:
            if str(net.sgen.at[i, "name"]) in campus.units.index:
                continue
            inj[at[int(net.sgen.at[i, "bus"])]] += float(net.sgen.at[i, "k"]) * float(net.sgen.at[i, "sn_mva"]) / sbase

    for name, row in campus.earthing_transformers.iterrows():
        bus = by_name[row["bus"]]
        rn = 0.0 if row["r_n_ohm"] != row["r_n_ohm"] else float(row["r_n_ohm"])
        y0[bus, bus] += 1.0 / (complex(3.0 * rn, float(row["x0_ohm"])) / zb[bus])
        earth[bus].append((name, False))

    return SeqNet(names, vn, y1, y2, y0, inj, conv, branches, earth, c0_f, case, sbase, float(net.f_hz))


def _component(sn, start, zero=False):
    """The buses reached from ``start`` through series branches (the zero
    sequence's series branches only, with ``zero``)."""
    adj = {}
    for br in sn.branches:
        if zero and br.y0 is None:
            continue
        adj.setdefault(br.i, set()).add(br.j)
        adj.setdefault(br.j, set()).add(br.i)
    seen, todo = {start}, [start]
    while todo:
        for nxt in adj.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return sorted(seen)


def earth_zone(sn, bus):
    """``(earthed, rests_on_assumed, zone)``: whether the zero-sequence zone
    of ``bus`` has a path to earth, and whether every such path is assumed."""
    zone = _component(sn, bus, zero=True)
    shunts = [s for b in zone for s in sn.earth[b]]
    return bool(shunts), bool(shunts) and all(a for _, a in shunts), zone


def capacitive_earth_ka(sn, zone):
    """The earth-fault current of an unearthed zone: sqrt 3 omega C0 Un,
    C0 the zone's cable capacitance to earth per phase (not IEC 60909)."""
    zone = set(zone)
    c0 = sum(sn.c0_f[br.name] for br in sn.branches if br.kind == "line" and br.i in zone)
    un_kv = sn.vn[min(zone)]
    return math.sqrt(3.0) * 2.0 * math.pi * sn.f_hz * c0 * un_kv           # kV * S = kA


MAX_LIMIT_ITER = 100


def _rotation(sn, comp, f):
    """``e^(-j arg Z1_fj)`` for every bus j of ``comp``, so that a current
    injected there adds to the fault in phase with the equivalent source;
    ones without a machine in the positive sequence."""
    y = sn.y1[np.ix_(comp, comp)]
    if np.linalg.cond(y) > 1e12:
        return np.ones(len(comp), dtype=complex)
    z = np.linalg.inv(y)[f]
    return np.exp(-1j * np.angle(z))


def _linear(sn, comp, f, fault, c, scale, rot):
    """The solution vector ``[dV1, V2, V0, I1, I2, I0]`` over ``comp`` with
    each converter scaled by ``scale``, or None when the passive network
    cannot carry the fault (the system is inconsistent). A consistent
    system has unique fault currents: a floating sequence network's KCL
    forces its current to zero, and otherwise (Z1 + Z2 + Z0) I = 0 (or its
    two-phase form) does; only node voltages of floating parts are free,
    and branch currents do not see them."""
    n = len(comp)
    ix = np.ix_(comp, comp)
    y2 = sn.y2.copy()
    inj = sn.inj.copy()
    for uid, b, i_pu, y2c in sn.conv:
        inj[b] += scale[uid] * i_pu
        y2[b, b] += scale[uid] * y2c
    m = np.zeros((3 * n + 3, 3 * n + 3), dtype=complex)
    rhs = np.zeros(3 * n + 3, dtype=complex)
    for s, y in enumerate((sn.y1, y2, sn.y0)):
        m[s * n:(s + 1) * n, s * n:(s + 1) * n] = y[ix]
        m[s * n + f, 3 * n + s] = 1.0
    rhs[:n] = [inj[b] * rot[k] for k, b in enumerate(comp)]
    i1, i2, i0 = 3 * n, 3 * n + 1, 3 * n + 2
    r = 3 * n
    if fault == "3ph":
        m[r, f], rhs[r] = 1.0, -c
        m[r + 1, i2] = 1.0
        m[r + 2, i0] = 1.0
    elif fault == "1ph":
        m[r, i1], m[r, i2] = 1.0, -1.0
        m[r + 1, i1], m[r + 1, i0] = 1.0, -1.0
        m[r + 2, f], m[r + 2, n + f], m[r + 2, 2 * n + f], rhs[r + 2] = 1.0, 1.0, 1.0, -c
    else:
        m[r, i1], m[r, i2] = 1.0, 1.0
        m[r + 1, i0] = 1.0
        m[r + 2, f], m[r + 2, n + f], rhs[r + 2] = 1.0, -1.0, -c
    x, *_ = np.linalg.lstsq(m, rhs, rcond=None)
    if np.linalg.norm(m @ x - rhs) > 1e-8 * (np.linalg.norm(m) * np.linalg.norm(x) + np.linalg.norm(rhs)):
        return None
    return x


def solve_fault(sn, bus, fault) -> Fault:
    """One bolted fault of kind ``fault`` at bus name ``bus``."""
    if fault not in FAULTS:
        raise ValueError(f"fault must be one of {FAULTS}, got {fault!r}")
    f_all = sn.buses.index(bus)
    comp = _component(sn, f_all)
    pos = {b: k for k, b in enumerate(comp)}
    n, f = len(comp), pos[f_all]
    ibase = sn.sbase / (math.sqrt(3.0) * sn.vn[f_all])
    earthed = earth_zone(sn, f_all)[0]
    zero_rows = [{"branch": br.name, "kind": br.kind, "i_e_ka": 0.0} for br in sn.branches]
    if fault == "1ph" and not earthed:
        return Fault(fault, bus, 0.0, 0.0, True, False, NO_EARTH, pd.DataFrame(zero_rows))
    c = c_factor(sn.vn[f_all], sn.case)
    scale = {uid: 1.0 for uid, *_ in sn.conv}
    rot = _rotation(sn, comp, f)
    for _ in range(MAX_LIMIT_ITER):
        x = _linear(sn, comp, f, fault, c, scale, rot)
        if x is None:
            nan = pd.DataFrame([{**row, "i_e_ka": math.nan} for row in zero_rows])
            return Fault(fault, bus, math.nan, math.nan, False, earthed, NO_NEG, nan)
        new = {}
        for uid, b, i_pu, y2c in sn.conv:
            if not i_pu or not y2c or b not in pos:
                new[uid] = 1.0
                continue
            peak = scale[uid] * (i_pu + abs(y2c * x[n + pos[b]]))
            new[uid] = scale[uid] * i_pu / peak
        if all(abs(new[u] - scale[u]) < 1e-10 for u in scale):
            break
        scale = new
    else:
        raise RuntimeError(f"the converter current limits did not settle for a {fault} fault at {bus}")
    i1, i2, i0 = 3 * n, 3 * n + 1, 3 * n + 2
    s1, s2, s0 = x[i1], x[i2], x[i0]
    phase = {"3ph": s1, "1ph": s1 + s2 + s0, "2ph": A ** 2 * s1 + A * s2 + s0}[fault]
    v0 = np.zeros(len(sn.buses), dtype=complex)
    v0[comp] = x[2 * n:3 * n]
    rows = []
    for br in sn.branches:
        if br.i not in pos:
            i_e = 0.0
        elif br.y0 is not None:
            i_e = 3.0 * abs(br.current(br.y0, v0)) * sn.sbase / (math.sqrt(3.0) * sn.vn[br.i])
        elif br.shunt0 is not None:
            b = br.shunt0[0]
            i_e = 3.0 * abs(br.shunt0[1] * v0[b]) * sn.sbase / (math.sqrt(3.0) * sn.vn[b])
        else:
            i_e = 0.0
        rows.append({"branch": br.name, "kind": br.kind, "i_e_ka": float(i_e)})
    return Fault(fault, bus, float(abs(phase) * ibase), float(abs(3.0 * s0) * ibase), True, earthed, "",
                 pd.DataFrame(rows))


# ── the studies ─────────────────────────────────────────────────────────────

def _unit_buses(campus, sources):
    """The own buses of the online gensets behind their unit transformers."""
    return {campus.units.at[g, "bus"] for g in _unit_trafos(campus, sources).values()}


def _net_at(campus, sources, island, case, gfl, bus, cache):
    """The sequence networks for a fault at ``bus`` (``inside`` a power
    station unit when it is one's own bus), memoised in ``cache``."""
    key = bus if bus in _unit_buses(campus, sources) else None
    if key not in cache:
        cache[key] = sequence_net(campus, sources, island, case, gfl, inside=key)
    return cache[key]


def _gfl_min_case(campus, gfl_min_case):
    req = campus.island.get("requirements")
    if gfl_min_case is None:
        gfl_min_case = req["gfl_min_case"] if req else "drop"
    return gfl_min_case


def unbalanced_fault_levels(campus, online=None, island=True, gfl_min_case=None, gfm_override=None) -> pd.DataFrame:
    """One row per bus: ``ik1_max_ka``, ``ik1_min_ka`` (1-phase to earth),
    ``ik2_max_ka``, ``ik2_min_ka`` (2-phase), ``ik3_max_ka``, ``ik3_min_ka``
    (3-phase, the same method), ``earth_path``, ``earth_assumed`` (every
    earth path in the zone is assumed), ``ic_e_ka`` (an unearthed zone's
    capacitive earth-fault current, else NaN), ``note`` and ``method``.
    ``online`` as in ``ISC.island_fault_levels``; ``island=False`` is the
    grid-connected campus with every unit installed in ``online``."""
    gfl = _gfl_min_case(campus, gfl_min_case)
    sources = ISC._sources(campus, online)
    nets = {case: sequence_net(campus, sources, island, case, gfl, gfm_override) for case in ("max", "min")}
    inner = _unit_buses(campus, sources)
    rows = []
    for bus in nets["max"].buses:
        row = {"bus": bus}
        notes = []
        for case in ("max", "min"):
            sn = (sequence_net(campus, sources, island, case, gfl, gfm_override, inside=bus) if bus in inner
                  else nets[case])
            for fault in FAULTS:
                res = solve_fault(sn, bus, fault)
                row[f"ik{fault[0]}_{case}_ka"] = res.ik_ka
                if res.note and res.note not in notes:
                    notes.append(res.note)
        earthed, assumed, zone = earth_zone(nets["min"], nets["min"].buses.index(bus))
        row.update(earth_path=earthed, earth_assumed=assumed,
                   ic_e_ka=math.nan if earthed else capacitive_earth_ka(nets["min"], zone),
                   note="; ".join(notes), method=METHOD)
        rows.append(row)
    return pd.DataFrame(rows)


def earth_reference(campus, online=None) -> pd.DataFrame:
    """One row per bus: ``earthed_grid`` and ``earthed_island`` (a
    zero-sequence path to earth in the bus's zone), ``lost_on_islanding``,
    ``island_assumed`` (the island's earth paths are all assumed) and the
    ``finding``. Losing the earth reference on islanding is a headline
    finding for the island design."""
    sources = ISC._sources(campus, online)
    grid = sequence_net(campus, ISC._sources(campus, None), island=False)
    isl = sequence_net(campus, sources, island=True)
    rows = []
    for k, bus in enumerate(isl.buses):
        eg = earth_zone(grid, k)[0]
        ei, assumed, _ = earth_zone(isl, k)
        if not ei:
            finding = "no earth reference in island" + (" (earthed when grid-connected)" if eg else "")
        elif assumed:
            finding = "earth reference in island rests on assumed data"
        else:
            finding = "earthed in island"
        rows.append({"bus": bus, "earthed_grid": eg, "earthed_island": ei, "lost_on_islanding": eg and not ei,
                     "island_assumed": assumed, "finding": finding})
    return pd.DataFrame(rows)


def earth_fault_relay_check(campus, online=None, gfl_min_case=None, sensitivity=None) -> pd.DataFrame:
    """One row per relay with an earth-fault pickup: the residual current
    3 I0 through its branch for a 1-phase fault at its ``fault_bus`` in the
    island's min case (``relay_e_ka``), the fault's earth current there
    (``fault_e_ka``), the same residual grid-connected (``grid_e_ka``), and
    the verdict at s * ``i_pickup_e_ka`` (s: the requirements'
    ``relay_sensitivity``, else 1.5). A screening, not a protection study."""
    req = campus.island.get("requirements")
    gfl = _gfl_min_case(campus, gfl_min_case)
    s = sensitivity or (req["values"]["relay_sensitivity"] if req else ISC.RELAY_S_DEFAULT)
    sources, every = ISC._sources(campus, online), ISC._sources(campus, None)
    isl, grid = {}, {}
    rows = []
    for _, r in campus.relays.iterrows():
        pickup = r["i_pickup_e_ka"]
        if not pickup == pickup:
            continue
        fi = solve_fault(_net_at(campus, sources, True, "min", gfl, r["fault_bus"], isl), r["fault_bus"], "1ph")
        fg = solve_fault(_net_at(campus, every, False, "min", gfl, r["fault_bus"], grid), r["fault_bus"], "1ph")
        relay_ka = float(fi.branch.set_index("branch").at[r["branch"], "i_e_ka"])
        grid_ka = float(fg.branch.set_index("branch").at[r["branch"], "i_e_ka"])
        need = s * float(pickup)
        if not fi.determinable:
            verdict, detail = "not determinable", fi.note
        elif relay_ka >= need:
            verdict, detail = SEES_E, ""
        else:
            verdict = BLIND_E
            detail = NO_EARTH if not fi.earth_path else f"{relay_ka:.3f} kA < {s:g} x {float(pickup):.3f} kA"
        rows.append({"relay": r["relay"], "branch": r["branch"], "fault_bus": r["fault_bus"],
                     "i_pickup_e_ka": float(pickup), "relay_e_ka": relay_ka, "fault_e_ka": fi.earth_ka,
                     "grid_e_ka": grid_ka, "sensitivity": s, "verdict": verdict, "detail": detail})
    return pd.DataFrame(rows, columns=["relay", "branch", "fault_bus", "i_pickup_e_ka", "relay_e_ka", "fault_e_ka",
                                       "grid_e_ka", "sensitivity", "verdict", "detail"])
