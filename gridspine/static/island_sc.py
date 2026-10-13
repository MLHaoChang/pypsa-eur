"""Short circuit by control mode, grid-connected and islanded (plan I5).

Plan: ``docs/superpowers/plans/2026-10-07-campus-island-operation.md``.

This module holds the parts the campus and island short-circuit checks share:

* ``current_source_fault(net, fault_bus, injections)``: a bolted fault fed
  only by current sources (converters) through the passive network. Every
  injected ampere ends in the fault (KCL; IEC 60909 neglects shunts), so
  Ik'' is the sum of the injections referred to the fault bus voltage, and
  each branch carries what the nodal solution gives it. This is the case
  pandapower cannot solve (an inverter-only island, plan spike 4), and the
  converter part of the converter-aware minimum (spike 3).
  - Converter currents are added **in phase**: every converter is assumed to
    inject reactive current during the fault, the usual grid-code behaviour.
    The report says so (``PHASE_RULE``).
  - Transformers are their series impedance at the nominal ratio; off-nominal
    windings and magnetising branches are neglected (ledgered).
* ``mu(x, t_min)``: the IEC 60909-0 decay factor of a synchronous machine's
  current, I_b = μ·Ik'', with x = Ik''G / IrG.
* ``lam(excitation, xd_sat, ik_sustained_pu)``: the steady-state current a
  time-delayed relay sees, Ik = λ·IrG.
  - Without field forcing (``none``, or a shunt-excited AVR that collapses
    with the terminal voltage), λ = 1/x_d,sat: constant no-load excitation
    behind the saturated synchronous reactance.
  - With PMG field forcing (``avr_pmg``), λ is the sustained current the
    datasheet states (``ik_sustained_pu``), else ``PMG_SUSTAINED_PU``,
    tagged assumed.
  - This is a first pass, not IEC 60909's λ curves, which are not reproduced
    here (ledgered).

Allowed to import pandapower (``static/``).
"""
import dataclasses
import math

import numpy as np
import pandas as pd

#: The phase assumption behind adding converter currents.
PHASE_RULE = ("converter fault currents are added in phase: every converter is "
              "assumed to inject reactive current during the fault (assumed)")
#: The sustained fault current of a genset with PMG field forcing, in pu of
#: its rated current, when its datasheet gives none. 3 pu is the usual
#: datasheet figure; tagged assumed.
PMG_SUSTAINED_PU = 3.0
#: IEC 60909-0 μ = a + b * exp(-c * Ik''G/IrG) by minimum time delay t_min
#: (s). The coefficients are as recalled from the standard and have not been
#: checked against its text, so they are ledgered as assumed, as campus_sc's
#: PEAK_FACTOR is.
MU_COEFFICIENTS = {0.02: (0.84, 0.26, 0.26), 0.05: (0.71, 0.51, 0.30),
                   0.10: (0.62, 0.72, 0.32), 0.25: (0.56, 0.94, 0.38)}

LEDGER = (
    "island_sc: " + PHASE_RULE,
    "island_sc: transformers are their series impedance at the nominal ratio in the "
    "current-source solve; off-nominal windings and magnetising branches are neglected",
    "island_sc: the IEC 60909-0 mu coefficients are as recalled and not checked against "
    "the text (assumed)",
    "island_sc: lambda is a first pass (1/x_d,sat without field forcing, the sustained "
    "current with PMG forcing), not the IEC 60909 lambda curves",
)


def mu(x, t_min):
    """IEC 60909-0 μ for a synchronous machine with Ik''G/IrG = ``x`` at the
    minimum time delay ``t_min`` (one of ``MU_COEFFICIENTS``). μ = 1 when the
    machine is far enough from the fault (x ≤ 2)."""
    if t_min not in MU_COEFFICIENTS:
        raise ValueError(f"no mu formula for t_min = {t_min} s; allowed {sorted(MU_COEFFICIENTS)}")
    if x <= 2.0:
        return 1.0
    a, b, c = MU_COEFFICIENTS[t_min]
    return a + b * math.exp(-c * x)          # continuous at x = 2: each formula gives about 1 there


def lam(excitation, xd_sat, ik_sustained_pu=None):
    """Ik / IrG at the operating time of a time-delayed relay (module
    docstring)."""
    if excitation == "avr_pmg":
        return PMG_SUSTAINED_PU if ik_sustained_pu is None else float(ik_sustained_pu)
    return 1.0 / float(xd_sat)


@dataclasses.dataclass
class SourceFault:
    ik_ka: float                 # at the fault bus
    branch: pd.DataFrame         # branch, kind, i_ka (at the branch's from / HV side)


def _branches(net, sbase):
    """``(name, kind, i, j, y_pu, vn_i)`` for every in-service line and
    transformer, the series admittance on ``sbase`` at the nominal ratio."""
    out = []
    for k in net.line.index[net.line["in_service"].astype(bool)]:
        i, j = int(net.line.at[k, "from_bus"]), int(net.line.at[k, "to_bus"])
        vn = float(net.bus.at[i, "vn_kv"])
        length, par = float(net.line.at[k, "length_km"]), float(net.line.at[k, "parallel"])
        z_ohm = complex(net.line.at[k, "r_ohm_per_km"], net.line.at[k, "x_ohm_per_km"]) * length / par
        out.append((str(net.line.at[k, "name"]), "line", i, j, 1.0 / (z_ohm / (vn ** 2 / sbase)), vn))
    for k in net.trafo.index[net.trafo["in_service"].astype(bool)]:
        i, j = int(net.trafo.at[k, "hv_bus"]), int(net.trafo.at[k, "lv_bus"])
        sn = float(net.trafo.at[k, "sn_mva"]) * float(net.trafo.at[k, "parallel"])
        zk = float(net.trafo.at[k, "vk_percent"]) / 100.0 * sbase / sn
        rk = float(net.trafo.at[k, "vkr_percent"]) / 100.0 * sbase / sn
        out.append((str(net.trafo.at[k, "name"]), "trafo", i, j, 1.0 / complex(rk, math.sqrt(zk ** 2 - rk ** 2)),
                    float(net.bus.at[i, "vn_kv"])))
    return out


def current_source_fault(net, fault_bus, injections, sbase=1.0) -> SourceFault:
    """The fault at ``fault_bus`` (a net bus index) fed by ``injections``
    (``{bus index: kA at that bus's nominal voltage}``), every current in
    phase (``PHASE_RULE``)."""
    every = _branches(net, sbase)
    # only the part of the network that is connected to the fault takes part
    adj = {}
    for _, _, i, j, _, _ in every:
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    seen, todo = {int(fault_bus)}, [int(fault_bus)]
    while todo:
        for nxt in adj.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    buses = sorted(seen)
    pos = {b: n for n, b in enumerate(buses)}
    vn = {b: float(net.bus.at[b, "vn_kv"]) for b in buses}
    ibase = {b: sbase / (math.sqrt(3) * vn[b]) for b in buses}            # kA
    br = [b for b in every if b[2] in pos]
    injections = {int(b): ka for b, ka in injections.items() if int(b) in pos}
    y = np.zeros((len(buses), len(buses)), dtype=complex)
    for _, _, i, j, yb, _ in br:
        a, b = pos[i], pos[j]
        y[a, a] += yb
        y[b, b] += yb
        y[a, b] -= yb
        y[b, a] -= yb
    inj = np.zeros(len(buses), dtype=complex)
    for b, ka in injections.items():
        inj[pos[int(b)]] += ka / ibase[int(b)]                             # pu, real: one phase reference
    f = pos[int(fault_bus)]
    keep = [n for n in range(len(buses)) if n != f]
    v = np.zeros(len(buses), dtype=complex)
    if keep:
        sub = y[np.ix_(keep, keep)]
        v[keep] = np.linalg.solve(sub, inj[keep])
    ik_pu = inj.sum()                                                     # KCL: all of it reaches the fault
    rows = [{"branch": name, "kind": kind, "i_ka": 0.0} for name, kind, i, *_ in every if i not in pos]
    for name, kind, i, j, yb, vn_i in br:
        i_pu = yb * (v[pos[i]] - v[pos[j]])
        rows.append({"branch": name, "kind": kind, "i_ka": abs(i_pu) * sbase / (math.sqrt(3) * vn_i)})
    return SourceFault(ik_ka=abs(ik_pu) * ibase[int(fault_bus)], branch=pd.DataFrame(rows))


# ── the machine net: gensets as synchronous machines, converters by mode ──

#: Rated power factor of a genset without island data (producers/campus.py
#: drafts every genset at it).
GENSET_COS_PHI = 0.8
METHOD_MIN = "converter-aware (not IEC 60909)"


def converter_k(campus, uid, case="max", override=None):
    """The fault current of a converter, in pu of its rated current, by its
    control mode: a GFM feeds its current limit (``i_max_pu`` in the max
    case, ``k_gfm_min`` in the min case, or ``override``); a GFL unit, or one
    without island data, feeds ``k_sc``. A UPS feeds nothing."""
    u = campus.units.loc[uid]
    if u["kind"] == "ups":
        return 0.0
    block = campus.island["units"].get(uid)
    if block and block.get("control") in ("gfm_droop", "gfm_vsm"):
        if override is not None:
            return float(override)
        return block["values"]["i_max_pu" if case == "max" else "k_gfm_min"]
    p = campus.params
    row = p[(p["element"] == uid) & (p["param"] == "k_sc")]
    return float(row["value"].iloc[0])


def _param(campus, uid, name):
    p = campus.params
    row = p[(p["element"] == uid) & (p["param"] == name)]
    return float(row["value"].iloc[0])


def machine_net(campus, installed, island=False, case="max", gfm_override=None):
    """A copy of the campus net for IEC 60909: every installed genset a
    synchronous machine behind x''d (K_G, or K_S behind its own unit
    transformer), every installed converter a current source at its mode's
    k, the rest out of service. ``island`` takes the grid out."""
    import pandapower as pp

    net = __import__("copy").deepcopy(campus.net)
    installed = set(installed)
    if island:
        net.ext_grid["in_service"] = False
    for i in net.sgen.index:
        uid = str(net.sgen.at[i, "name"])
        if uid not in campus.units.index:            # a STATCOM: as built, in service
            continue
        u = campus.units.loc[uid]
        on = uid in installed
        net.sgen.at[i, "in_service"] = on
        if not on:
            continue
        if u["kind"] == "genset":
            block = campus.island["units"].get(uid)
            vn = float(net.bus.at[int(net.sgen.at[i, "bus"]), "vn_kv"])
            xd = _param(campus, uid, "xd_pp")
            sn = float(u["s_mva"])
            cos_phi = block["values"]["cos_phi_r"] if block else GENSET_COS_PHI
            net.sgen.at[i, "in_service"] = False
            g = pp.create_gen(net, int(net.sgen.at[i, "bus"]), p_mw=0.0, vm_pu=1.0, sn_mva=sn, vn_kv=vn,
                              xdss_pu=xd, rdss_ohm=_param(campus, uid, "rx_sc") * xd * vn ** 2 / sn,
                              cos_phi=cos_phi, name=uid)
            if block and block.get("unit_transformer"):
                bus = int(net.sgen.at[i, "bus"])
                trafos = net.trafo.index[(net.trafo["lv_bus"] == bus) & net.trafo["in_service"].astype(bool)]
                if len(trafos) != 1:
                    raise ValueError(f"genset {uid} has unit_transformer set, but its bus has {len(trafos)} "
                                     "step-up transformers; it needs exactly one")
                # IEC 60909 power station unit: K_S with an on-load tap changer,
                # K_SO without (the usual genset step-up); the transformer then
                # takes no network K_T of its own
                net.gen.at[g, "power_station_trafo"] = int(trafos[0])
                net.trafo.at[trafos[0], "power_station_unit"] = True
                net.trafo.at[trafos[0], "oltc"] = bool(block.get("unit_transformer_oltc"))
        else:
            net.sgen.at[i, "k"] = converter_k(campus, uid, case, gfm_override)
    return net


def _sources(campus, online):
    """Every online genset and converter, with its rated MVA."""
    out = {}
    for uid, u in campus.units.iterrows():
        if u["kind"] in ("load", "ups") or (online is not None and uid not in online):
            continue
        out[uid] = float(u["s_mva"])
    return out


def _counted_in_min(campus, uid, gfl_min_case):
    if campus.units.at[uid, "kind"] == "genset":
        return False
    block = campus.island["units"].get(uid)
    if block and block.get("control") in ("gfm_droop", "gfm_vsm"):
        return True
    return gfl_min_case == "keep"


def _island_min(campus, sources, gfl_min_case, gfm_override):
    """``{bus name: kA}``: the gensets' IEC minimum plus every counted
    converter at its minimum, in phase."""
    import pandapower.shortcircuit as sc

    gensets = {u for u in sources if campus.units.at[u, "kind"] == "genset"}
    net = machine_net(campus, gensets, island=True, case="min")
    names = list(net.bus["name"].astype(str))
    out = dict.fromkeys(names, 0.0)
    if gensets:
        sc.calc_sc(net, case="min")
        for name, ik in zip(names, net.res_bus_sc["ikss_ka"]):
            out[name] += float(ik)
    inj = {}
    for uid in sources:
        if uid in gensets or not _counted_in_min(campus, uid, gfl_min_case):
            continue
        i = net.sgen.index[net.sgen["name"] == uid][0]
        bus = int(net.sgen.at[i, "bus"])
        k = converter_k(campus, uid, "min", gfm_override)
        inj[bus] = inj.get(bus, 0.0) + k * sources[uid] / (math.sqrt(3) * float(net.bus.at[bus, "vn_kv"]))
    if inj:
        for b, name in zip(net.bus.index, names):
            out[name] += current_source_fault(net, int(b), inj).ik_ka
    return out


def island_fault_levels(campus, online=None, gfl_min_case=None, gfm_override=None) -> pd.DataFrame:
    """One row per bus of the islanded campus: ``ikss_max_ka`` (IEC 60909,
    or the current-source solve when no synchronous machine is online),
    ``ikss_min_ka`` and ``ikss_min_n1_ka`` (converter-aware, with the largest
    source out: ``largest_out``), and ``method_min``. ``online`` is the set
    of units in the island (None: every unit). ``gfl_min_case`` defaults to
    the campus requirements', else ``drop``."""
    import pandapower.shortcircuit as sc

    req = campus.island.get("requirements")
    if gfl_min_case is None:
        gfl_min_case = req["gfl_min_case"] if req else "drop"
    sources = _sources(campus, online)
    net = machine_net(campus, sources, island=True, case="max", gfm_override=None)
    names = list(net.bus["name"].astype(str))
    if net.gen["in_service"].astype(bool).any():
        sc.calc_sc(net, case="max")
        ik_max = dict(zip(names, map(float, net.res_bus_sc["ikss_ka"])))
    else:
        inj = {}
        for uid in sources:
            i = net.sgen.index[net.sgen["name"] == uid][0]
            bus = int(net.sgen.at[i, "bus"])
            inj[bus] = inj.get(bus, 0.0) + float(net.sgen.at[i, "k"]) * sources[uid] / (
                math.sqrt(3) * float(net.bus.at[bus, "vn_kv"]))
        ik_max = {name: current_source_fault(net, int(b), inj).ik_ka for b, name in zip(net.bus.index, names)}
    ik_min = _island_min(campus, sources, gfl_min_case, gfm_override)
    largest = max(sorted(sources), key=lambda u: sources[u]) if sources else None
    rest = {u: s for u, s in sources.items() if u != largest}
    ik_n1 = _island_min(campus, rest, gfl_min_case, gfm_override) if largest else dict.fromkeys(names, 0.0)
    return pd.DataFrame([{"bus": n, "ikss_max_ka": ik_max[n], "ikss_min_ka": ik_min[n], "ikss_min_n1_ka": ik_n1[n],
                          "largest_out": largest, "method_min": METHOD_MIN} for n in names])


# ── relays ─────────────────────────────────────────────────────────────────

RELAY_S_DEFAULT = 1.5


def _branch_current(net, kind, name):
    if kind == "line":
        i = net.line.index[net.line["name"] == name][0]
        return float(net.res_line_sc.at[i, "ikss_ka"])
    i = net.trafo.index[net.trafo["name"] == name][0]
    return float(net.res_trafo_sc.at[i, "ikss_hv_ka"])


def _relay_current(campus, relay, sources, gfl_min_case, override):
    """``(relay_ka, initial_ka, fault_total_ka)`` for one relay in the
    island's minimum case: every genset alone, decayed to the relay's
    operating time, plus the counted converters, in phase."""
    import pandapower.shortcircuit as sc

    base = machine_net(campus, set(), island=True)
    fb = int(base.bus.index[base.bus["name"] == relay["fault_bus"]][0])
    vn_f = float(base.bus.at[fb, "vn_kv"])
    relay_ka = initial_ka = total_ka = 0.0
    for g in sorted(u for u in sources if campus.units.at[u, "kind"] == "genset"):
        net = machine_net(campus, {g}, island=True, case="min")
        sc.calc_sc(net, bus=fb, case="min", branch_results=True)
        ik_g = float(net.res_bus_sc.at[fb, "ikss_ka"])
        through = _branch_current(net, relay["kind"], relay["branch"])
        gi = net.gen.index[net.gen["name"] == g][0]
        vn_g = float(net.gen.at[gi, "vn_kv"])
        irg = sources[g] / (math.sqrt(3) * vn_g)
        x = ik_g * vn_f / vn_g / irg
        block = campus.island["units"][g]["values"]
        if relay["timing"] == "instantaneous":
            d = mu(x, float(relay["t_min_s"]))
        else:
            excitation = campus.island["units"][g]["excitation"]
            d = min(1.0, lam(excitation, block["xd_sat"], block.get("ik_sustained_pu")) / x)
        relay_ka += d * through
        initial_ka += through
        total_ka += d * ik_g
    inj = {}
    for uid in sources:
        if campus.units.at[uid, "kind"] == "genset" or not _counted_in_min(campus, uid, gfl_min_case):
            continue
        i = base.sgen.index[base.sgen["name"] == uid][0]
        bus = int(base.sgen.at[i, "bus"])
        k = converter_k(campus, uid, "min", override)
        inj[bus] = inj.get(bus, 0.0) + k * sources[uid] / (math.sqrt(3) * float(base.bus.at[bus, "vn_kv"]))
    if inj:
        res = current_source_fault(base, fb, inj)
        through = float(res.branch.set_index("branch").at[relay["branch"], "i_ka"])
        relay_ka += through
        initial_ka += through
        total_ka += res.ik_ka
    return relay_ka, initial_ka, total_ka


def relay_check(campus, online=None, gfl_min_case=None, sensitivity=None) -> pd.DataFrame:
    """One row per relay with a phase pickup: the current it sees in the island's minimum case
    (``relay_ka``; ``initial_ka`` before decay), the fault's total there
    (``fault_total_ka``) and what bypasses the relay (``bypass_ka``), the
    verdict at s * pickup, the grid-connected Ik''max at the fault bus and
    its ratio to the island current (the setting-group question), the
    current with the largest source out (``relay_n1_ka``, reported only: a
    feeder whose only source is out carries no fault to clear), and the
    current at each GFM current limit in ``sensitivity`` (default: the
    requirements' ``i_max_sensitivity_pu``). A screening, not a protection
    study."""
    from gridspine.static.campus_sc import campus_fault_levels

    req = campus.island.get("requirements")
    if gfl_min_case is None:
        gfl_min_case = req["gfl_min_case"] if req else "drop"
    s = req["values"]["relay_sensitivity"] if req else RELAY_S_DEFAULT
    if sensitivity is None:
        sensitivity = req["values"]["i_max_sensitivity_pu"] if req else []
    sources = _sources(campus, online)
    largest = max(sorted(sources), key=lambda u: sources[u]) if sources else None
    rest = {u: v for u, v in sources.items() if u != largest}
    grid = campus_fault_levels(campus, set(campus.units.index)).set_index("bus")
    rows = []
    for _, r in campus.relays.iterrows():
        if not r["i_pickup_ka"] == r["i_pickup_ka"]:          # an earth-fault relay only (island_sc_seq)
            continue
        relay_ka, initial_ka, total_ka = _relay_current(campus, r, sources, gfl_min_case, None)
        need = s * float(r["i_pickup_ka"])
        if relay_ka >= need:
            verdict, detail = "sees the fault", f"{relay_ka:.3f} kA against {need:.3f} kA (s = {s:g} x pickup)"
        else:
            verdict = "protection blind in island"
            detail = (f"blinded by infeed that bypasses the relay: it sees {relay_ka:.3f} of {total_ka:.3f} kA, "
                      f"against {need:.3f} kA" if total_ka >= need else
                      f"the island drives only {total_ka:.3f} kA into the fault, against {need:.3f} kA")
        grid_ik = float(grid.at[r["fault_bus"], "ikss_max_ka"])
        out = {"relay": r["relay"], "branch": r["branch"], "fault_bus": r["fault_bus"], "timing": r["timing"],
               "relay_ka": relay_ka, "initial_ka": initial_ka, "fault_total_ka": total_ka,
               "bypass_ka": total_ka - relay_ka, "pickup_ka": float(r["i_pickup_ka"]), "s": s,
               "verdict": verdict, "detail": detail, "grid_ikss_max_ka": grid_ik,
               "grid_to_island": grid_ik / relay_ka if relay_ka > 0 else math.inf,
               "largest_out": largest,
               "relay_n1_ka": _relay_current(campus, r, rest, gfl_min_case, None)[0] if largest else 0.0}
        for v in sensitivity:
            out[f"relay_ka_at_{v:g}"] = _relay_current(campus, r, sources, gfl_min_case, v)[0]
        rows.append(out)
    return pd.DataFrame(rows)
