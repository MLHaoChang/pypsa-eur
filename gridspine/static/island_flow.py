"""Island steady state by control mode (plan I4).

Plan: ``docs/superpowers/plans/2026-10-07-campus-island-operation.md``.

``island_flow(campus, rows, online=None, settings=...)`` takes one hour of the
campus hourly table, solves it grid-connected to get every unit's pre-event
output P0, then opens the PCC and solves the island the units form:

* **References.** A grid-forming converter (``gfm_droop``, ``gfm_vsm``) or a
  genset with island data becomes a pandapower ``gen`` on an auxiliary bus,
  behind a virtual reactance ``x_v = q_droop_pct / 100`` (pu on its S_n) to
  its terminal bus. The Q-V droop is then part of the network, so
  Newton-Raphson solves it directly; a naive V_set <- V0 - k_q * Q outer loop
  diverges on short campus ties (plan spike 1). The gen holds V0 = 1 pu at
  the auxiliary bus. x_v gives V = V0 - k_q * Q only to first order; the
  x * P^2 term biases it at high load (ledgered, plan N9).
* **Frequency.** The references share the lost import by distributed slack,
  weighted by K = P_n / (R * f0) with R = ``droop_pct`` / 100 on P_n, so
  Δf = -ΔP_i / K_i for every unsaturated reference. An unsaturated
  ``isochronous`` genset is the only slack while it lasts, so Δf = 0.
  Steady state cannot tell droop from VSM: both use the same K.
* **Limits** (outer loop). pandapower's distributed slack ignores
  ``max_p_mw`` (plan spike 1), so a reference beyond its P range is fixed at
  its limit, and a GFM beyond ``i_max_pu`` times its S_n becomes a PQ source
  at that current; the flow is re-solved. With no unsaturated reference
  left, the island is not viable.
* **Grid-following units** keep their law: ``gfl_pq`` holds its dispatch;
  ``gfl_qu`` follows its Q(U) curve at its terminal voltage; ``gfl_pf`` holds
  Q = P * tan phi (a fixed cos phi, over-excited) or follows its cos phi(P)
  curve (a negative cos phi absorbs); ``gfl_fw`` adds
  -K_fw * sign(Δf) * max(0, |Δf| - db), capped at ``fw_p_limit_mw``. The
  common Δf is a fixed point that includes that response.
* A converter or genset **without island data** stays a PQ injection at its
  dispatch, as on today's campus; it cannot form the island. A UPS keeps its
  battery dispatch and feeds no fault.

The verdict is a **steady-state screening** (``label``). It is not viable
when there is no reference, every reference is at its limit, the flow does
not converge (returned, never raised), a bus leaves its voltage band, or a
unit, transformer or cable is above 100 %.

``delta_p_ac_mw`` is the AC-measured change in the references' total P from
P0, losses included: the one ΔP definition I6 shares (plan N1, S7).
``check_gate(result)`` is the per-unit consistency gate of plan N10.

``campus.net`` is never changed. Allowed to import pandapower (``static/``).
"""
import copy
import dataclasses
import math

import pandapower as pp
import pandas as pd

from gridspine.schema.island import GFM_CONTROLS
from gridspine.static.campus_flow import apply_hour

LABEL = "steady-state screening"
#: The reference roles; "fixed" is a reference held at its P0 while an
#: isochronous genset sets the frequency.
_REF = ("reference", "fixed")


@dataclasses.dataclass(frozen=True)
class IslandSettings:
    """``v_band`` is the steady island voltage band at every bus (pu);
    ``v0_pu`` the references' internal set-point; ``tol_mw`` and
    ``max_iter`` bound the outer loops."""
    v_band: tuple = (0.9, 1.1)
    v0_pu: float = 1.0
    tol_mw: float = 1e-5
    max_iter: int = 60


@dataclasses.dataclass
class IslandFlow:
    viable: bool
    reason: str | None
    df_hz: float
    delta_p_ac_mw: float
    units: pd.DataFrame     # unit_id, kind, control, role, p0_mw, p_mw, q_mvar, k_mw_per_hz, saturated, loading_pct
    bus: pd.DataFrame       # bus, vm_pu, in_band
    trafo: pd.DataFrame     # trafo, loading_pct
    line: pd.DataFrame      # cable, loading_pct
    sync: dict              # df_hz, dv_pu at the PCC: the static sync-check inputs
    label: str = LABEL


def _role(kind, block, online):
    if not online:
        return "offline"
    if kind == "load":
        return "load"
    if block is None or kind == "ups":
        return "pq"
    if kind == "genset" or block.get("control") in GFM_CONTROLS:
        return "reference"
    return "gfl"


def _plan(campus, online):
    """One row per unit: what it becomes in the island."""
    out = []
    for uid, u in campus.units.iterrows():
        block = campus.island["units"].get(uid)
        on = online is None or uid in online
        role = _role(u["kind"], block, on)
        v = block["values"] if block else {}
        control = None
        if block:
            control = block.get("control") or (f"genset_{block['governor']}" if u["kind"] == "genset" else None)
        droop = v.get("droop_pct")
        k = u["p_mw"] / (droop / 100.0) if role == "reference" and droop else math.nan
        out.append({"unit_id": uid, "kind": u["kind"], "control": control or "none", "role": role,
                    "p_n": float(u["p_mw"]), "s_n": float(u["s_mva"]) if pd.notna(u["s_mva"]) else math.nan,
                    "k_weight": k, "block": block,
                    "iso": bool(block and block.get("governor") == "isochronous")})
    return pd.DataFrame(out).set_index("unit_id")


def island_net(campus, rows, online=None, settings=IslandSettings()):
    """The island net for the hour: a copy of the campus net with the PCC
    open and every reference behind its virtual droop reactance."""
    net, _, _ = _build(campus, rows, online, settings)
    return net


def _build(campus, rows, online, settings, island=True):
    """The net for the hour. ``island`` opens the PCC; grid-connected
    (I4b), only GFM converters become voltage sources and gensets stay in
    power-factor control, as on today's campus."""
    hour = dataclasses.replace(campus, net=copy.deepcopy(campus.net))
    apply_hour(hour, rows)
    net = hour.net
    plan = _plan(campus, online)
    sgen_idx = dict(zip(net.sgen["name"], net.sgen.index))
    load_idx = dict(zip(net.load["name"], net.load.index))
    # pre-event: grid-connected, every unit at its dispatch
    p0 = {}
    for uid, r in plan.iterrows():
        if r["kind"] == "load":
            p0[uid] = -float(net.load.at[load_idx[uid], "p_mw"]) if net.load.at[load_idx[uid], "in_service"] else 0.0
        else:
            i = sgen_idx[uid]
            p0[uid] = float(net.sgen.at[i, "p_mw"]) if net.sgen.at[i, "in_service"] else 0.0
    plan["p0_mw"] = pd.Series(p0)
    if island:
        net.ext_grid["in_service"] = False
    for uid, r in plan.iterrows():
        if r["role"] == "offline":
            if r["kind"] == "load":
                net.load.at[load_idx[uid], "in_service"] = False
            else:
                net.sgen.at[sgen_idx[uid], "in_service"] = False
            continue
        if r["role"] != "reference" or (not island and r["kind"] == "genset"):
            continue
        i = sgen_idx[uid]
        term = int(net.sgen.at[i, "bus"])
        net.sgen.at[i, "in_service"] = False
        vn = float(net.bus.at[term, "vn_kv"])
        aux = pp.create_bus(net, vn_kv=vn, name=f"{uid}~v")
        kq = r["block"]["values"]["q_droop_pct"] / 100.0
        pp.create_impedance(net, aux, term, rft_pu=0.0, xft_pu=kq, sn_mva=r["s_n"], name=f"{uid}~xv")
        pp.create_gen(net, aux, p_mw=r["p0_mw"], vm_pu=settings.v0_pu, sn_mva=r["s_n"], name=uid,
                      slack=False, slack_weight=0.0)
    return net, plan, (sgen_idx, load_idx)


def _p_range(r):
    if r["kind"] == "bess":
        return -r["p_n"], r["p_n"]
    return 0.0, r["p_n"]


def _interp(points, x):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _q_law(r, p, v):
    block = r["block"]
    values = block["values"]
    control = block["control"]
    if control == "gfl_qu":
        return r["s_n"] * _interp(values["qu_points"], v)
    if control == "gfl_pf":
        if "cos_phi" in values:
            return p * math.tan(math.acos(values["cos_phi"]))
        c = _interp(values["cosphi_p_points"], p / r["p_n"] if r["p_n"] else 0.0)
        return math.copysign(abs(p) * math.tan(math.acos(min(abs(c), 1.0))), c)
    return 0.0


def _fw_delta(values, df):
    raw = -values["fw_k_mw_per_hz"] * math.copysign(1.0, df) * max(0.0, abs(df) - values["fw_deadband_hz"])
    lim = values["fw_p_limit_mw"]
    return max(-lim, min(lim, raw))


def island_flow(campus, rows, online=None, settings=IslandSettings()) -> IslandFlow:
    """The island the units form at this hour (see the module docstring).
    ``online`` is the set of unit ids in the island; None means every unit."""
    net, plan, (sgen_idx, load_idx) = _build(campus, rows, online, settings)
    f0 = float(net.f_hz)
    gen_idx = dict(zip(net.gen["name"], net.gen.index))
    refs = plan[plan["role"] == "reference"]
    saturated = {uid: "none" for uid in refs.index}
    if refs.empty:
        return _result(net, plan, settings, False, "no reference: no grid-forming converter or genset can form the island",
                       math.nan, math.nan, saturated, converged=False)
    fixed_pq = {}            # uid -> (p, q) for references held at a limit as PQ sources
    limited = {}             # uid -> (sgen index, terminal bus, P/S, Q/S, i_max * S_n)
    df = 0.0
    gfl = plan[plan["role"] == "gfl"]

    for _ in range(settings.max_iter):
        active = [u for u in refs.index if saturated[u] == "none"]
        if not active:
            return _result(net, plan, settings, False,
                           "every grid-forming source is at its limit: the island cannot carry the hour",
                           df, math.nan, saturated, converged=False)
        iso = [u for u in active if refs.at[u, "iso"]]
        slack = iso or active
        for uid in refs.index:
            gi = gen_idx[uid]
            on_slack = uid in slack
            net.gen.at[gi, "slack"] = on_slack
            # off the slack, a gen holds its p_mw: P0, or its limit once saturated
            net.gen.at[gi, "slack_weight"] = (refs.at[uid, "p_n"] if iso else refs.at[uid, "k_weight"]) if on_slack else 0.0
        try:
            pp.runpp(net, distributed_slack=True)
        except pp.LoadflowNotConverged:
            return _result(net, plan, settings, False, "the island load flow did not converge",
                           math.nan, math.nan, saturated, converged=False)

        changed = False
        # frequency from the slack references
        if iso:
            df = 0.0
        else:
            u0 = slack[0]
            dp = float(net.res_gen.at[gen_idx[u0], "p_mw"]) - refs.at[u0, "p0_mw"]
            df = -dp / (refs.at[u0, "k_weight"] / f0)
        # limits on the slack references
        for uid in slack:
            gi = gen_idx[uid]
            p, q = float(net.res_gen.at[gi, "p_mw"]), float(net.res_gen.at[gi, "q_mvar"])
            lo, hi = _p_range(refs.loc[uid])
            if p > hi + settings.tol_mw or p < lo - settings.tol_mw:
                net.gen.at[gi, "p_mw"] = hi if p > hi else lo
                net.gen.at[gi, "slack"] = False
                net.gen.at[gi, "slack_weight"] = 0.0
                saturated[uid] = "p_limit"
                changed = True
                continue
            block = refs.at[uid, "block"]
            if refs.at[uid, "kind"] in ("bess", "pv", "wind") and "i_max_pu" in block["values"]:
                # the converter's current is S / V at its TERMINAL, not behind x_v
                term = int(net.sgen.at[sgen_idx[uid], "bus"])
                vt = float(net.res_bus.at[term, "vm_pu"])
                s_lim = block["values"]["i_max_pu"] * refs.at[uid, "s_n"] * vt
                s = math.hypot(p, q)
                if s > s_lim * (1 + 1e-9):
                    scale = s_lim / s
                    fixed_pq[uid] = (p * scale, q * scale)
                    net.gen.at[gi, "in_service"] = False
                    limited[uid] = (pp.create_sgen(net, term, p_mw=p * scale, q_mvar=q * scale, name=f"{uid}~limit"),
                                    term, p / s, q / s, block["values"]["i_max_pu"] * refs.at[uid, "s_n"])
                    saturated[uid] = "current_limit"
                    changed = True
        if changed:
            continue
        # a current-limited source carries i_max * S_n at the voltage it ends up at
        for uid, (si, term, cp, cq, s_cap) in limited.items():
            s_now = s_cap * float(net.res_bus.at[term, "vm_pu"])
            if abs(s_now - math.hypot(net.sgen.at[si, "p_mw"], net.sgen.at[si, "q_mvar"])) > settings.tol_mw:
                net.sgen.at[si, "p_mw"], net.sgen.at[si, "q_mvar"] = cp * s_now, cq * s_now
                fixed_pq[uid] = (cp * s_now, cq * s_now)
                changed = True
        # grid-following laws at this frequency and voltage
        for uid, r in gfl.iterrows():
            i = sgen_idx[uid]
            values = r["block"]["values"]
            p = r["p0_mw"]
            if r["block"]["control"] == "gfl_fw":
                p = r["p0_mw"] + _fw_delta(values, df)
            vt = float(net.res_bus.at[int(net.sgen.at[i, "bus"]), "vm_pu"])
            q = _q_law(r, p, vt)
            if abs(p - net.sgen.at[i, "p_mw"]) > settings.tol_mw or abs(q - net.sgen.at[i, "q_mvar"]) > settings.tol_mw:
                net.sgen.at[i, "p_mw"], net.sgen.at[i, "q_mvar"] = p, q
                changed = True
        if not changed:
            break
    else:
        return _result(net, plan, settings, False, "the outer loops did not settle", df, math.nan, saturated,
                       converged=False)

    dp_ac = 0.0
    for uid in refs.index:
        gi = gen_idx[uid]
        p = fixed_pq[uid][0] if uid in fixed_pq else float(net.res_gen.at[gi, "p_mw"])
        dp_ac += p - refs.at[uid, "p0_mw"]
    return _result(net, plan, settings, True, None, df, dp_ac, saturated, converged=True, fixed_pq=fixed_pq)


def _result(net, plan, settings, viable, reason, df, dp_ac, saturated, converged, fixed_pq=None):
    fixed_pq = fixed_pq or {}
    f0 = float(net.f_hz)
    gen_idx = dict(zip(net.gen["name"], net.gen.index))
    sgen_idx = {n: i for n, i in zip(net.sgen["name"], net.sgen.index)}
    load_idx = dict(zip(net.load["name"], net.load.index))
    rows = []
    for uid, r in plan.iterrows():
        p = q = math.nan
        if converged:
            if r["role"] == "offline":
                p = q = 0.0
            elif r["role"] == "load":
                p, q = -float(net.res_load.at[load_idx[uid], "p_mw"]), -float(net.res_load.at[load_idx[uid], "q_mvar"])
            elif uid in fixed_pq:
                p, q = fixed_pq[uid]
            elif r["role"] == "reference":
                p, q = float(net.res_gen.at[gen_idx[uid], "p_mw"]), float(net.res_gen.at[gen_idx[uid], "q_mvar"])
            else:
                i = sgen_idx[uid]
                p, q = float(net.res_sgen.at[i, "p_mw"]), float(net.res_sgen.at[i, "q_mvar"])
        role = r["role"]
        if (role == "reference" and converged and saturated.get(uid) == "none"
                and not bool(net.gen.at[gen_idx[uid], "slack"])):
            role = "fixed"
        loading = math.hypot(p, q) / r["s_n"] * 100.0 if r["kind"] != "load" and r["s_n"] == r["s_n"] and r["s_n"] else math.nan
        lo, hi = _p_range(r) if r["role"] == "reference" else (math.nan, math.nan)
        fw = r["block"]["values"] if r["role"] == "gfl" and r["block"].get("control") == "gfl_fw" else {}
        rows.append({"unit_id": uid, "kind": r["kind"], "control": r["control"], "role": role,
                     "p0_mw": r["p0_mw"], "p_mw": p, "q_mvar": q,
                     "k_mw_per_hz": r["k_weight"] / f0 if r["k_weight"] == r["k_weight"] else math.nan,
                     "saturated": saturated.get(uid, "none"), "loading_pct": loading,
                     "p_min_mw": lo, "p_max_mw": hi,
                     "fw_k_mw_per_hz": fw.get("fw_k_mw_per_hz", math.nan),
                     "fw_deadband_hz": fw.get("fw_deadband_hz", math.nan),
                     "fw_p_limit_mw": fw.get("fw_p_limit_mw", math.nan)})
    units = pd.DataFrame(rows)
    camp_buses = ~net.bus["name"].astype(str).str.endswith("~v")
    if converged:
        vm = net.res_bus["vm_pu"]
        lo, hi = settings.v_band
        bus = pd.DataFrame({"bus": net.bus.loc[camp_buses, "name"].astype(str).to_numpy(),
                            "vm_pu": vm[camp_buses].to_numpy()})
        bus["in_band"] = (bus["vm_pu"] >= lo) & (bus["vm_pu"] <= hi)
        on_t = net.trafo["in_service"].astype(bool)
        trafo = pd.DataFrame({"trafo": net.trafo.loc[on_t, "name"].astype(str).to_numpy(),
                              "loading_pct": net.res_trafo.loc[on_t, "loading_percent"].to_numpy()})
        line = pd.DataFrame({"cable": net.line["name"].astype(str).to_numpy(),
                             "loading_pct": net.res_line["loading_percent"].to_numpy()})
        pcc_bus = net.ext_grid.at[net.ext_grid.index[0], "bus"]
        sync = {"df_hz": df, "dv_pu": float(vm.at[pcc_bus]) - float(net.ext_grid.at[net.ext_grid.index[0], "vm_pu"])}
    else:
        bus = pd.DataFrame(columns=["bus", "vm_pu", "in_band"])
        trafo = pd.DataFrame(columns=["trafo", "loading_pct"])
        line = pd.DataFrame(columns=["cable", "loading_pct"])
        sync = {"df_hz": df, "dv_pu": math.nan}
    if viable:
        out = bus[~bus["in_band"]]
        if not out.empty:
            viable, reason = False, f"voltage out of band at {', '.join(out['bus'])}"
        else:
            over = ([f"transformer {n}" for n in trafo.loc[trafo["loading_pct"] > 100.0, "trafo"]]
                    + [f"cable {n}" for n in line.loc[line["loading_pct"] > 100.0, "cable"]]
                    + [f"unit {n}" for n in units.loc[units["loading_pct"] > 100.0 + 1e-6, "unit_id"]])
            if over:
                viable, reason = False, f"overload: {', '.join(over)}"
    return IslandFlow(viable, reason, df, dp_ac, units, bus, trafo, line, sync)


def check_gate(res, tol_hz=1e-4) -> dict:
    """The per-unit consistency gate (plan N10): every unsaturated reference
    on the slack sits at the one Δf (-ΔP_i / K_i), every saturated one is at
    its limit, and every frequency-watt unit sits on its law at that Δf."""
    u = res.units
    problems = []
    for _, r in u[(u["role"] == "reference") & (u["saturated"] == "none")].iterrows():
        if r["k_mw_per_hz"] == r["k_mw_per_hz"] and res.df_hz != 0.0:
            own = -(r["p_mw"] - r["p0_mw"]) / r["k_mw_per_hz"]
            if abs(own - res.df_hz) > tol_hz:
                problems.append(f"{r['unit_id']}: own Δf {own:.6f} against {res.df_hz:.6f}")
    for _, r in u[u["saturated"] == "p_limit"].iterrows():
        if min(abs(r["p_mw"] - r["p_max_mw"]), abs(r["p_mw"] - r["p_min_mw"])) > 1e-6:
            problems.append(f"{r['unit_id']}: saturated but at {r['p_mw']:.6f}, not at a limit")
    for _, r in u[u["control"] == "gfl_fw"].iterrows():
        law = -r["fw_k_mw_per_hz"] * math.copysign(1.0, res.df_hz) * max(0.0, abs(res.df_hz) - r["fw_deadband_hz"])
        law = max(-r["fw_p_limit_mw"], min(r["fw_p_limit_mw"], law))
        if abs((r["p_mw"] - r["p0_mw"]) - law) > 1e-4:
            problems.append(f"{r['unit_id']}: frequency-watt off its law ({r['p_mw'] - r['p0_mw']:.6f} against {law:.6f})")
    return {"ok": not problems, "problems": problems}


@dataclasses.dataclass
class ControlLawFlow:
    """The grid-connected hour with every converter on its own control law
    (plan I4b)."""
    converged: bool
    pcc_p_mw: float
    pcc_q_mvar: float
    units: pd.DataFrame     # unit_id, control, p_mw, q_mvar
    bus: pd.DataFrame       # bus, vm_pu
    label: str = LABEL


def control_law_flow(campus, rows, settings=IslandSettings()) -> ControlLawFlow:
    """The hour grid-connected, with each unit's actual law in place of the
    MILP's free Q (plan I4b). A GFM converter sits behind its x_v at its
    dispatched P and V0; a grid-following unit follows its Q law; a genset
    stays in power-factor control; a unit without island data stays PQ, as on
    today's campus. The MILP assumes a plant controller that can dispatch any
    Q inside the capability; the difference to this flow is ledgered."""
    net, plan, (sgen_idx, _) = _build(campus, rows, None, settings, island=False)
    gen_idx = dict(zip(net.gen["name"], net.gen.index))
    gfl = plan[plan["role"] == "gfl"]
    for _ in range(settings.max_iter):
        try:
            pp.runpp(net)
        except pp.LoadflowNotConverged:
            return ControlLawFlow(False, math.nan, math.nan, pd.DataFrame(), pd.DataFrame())
        changed = False
        for uid, r in gfl.iterrows():
            i = sgen_idx[uid]
            p = r["p0_mw"]
            q = _q_law(r, p, float(net.res_bus.at[int(net.sgen.at[i, "bus"]), "vm_pu"]))
            if abs(q - net.sgen.at[i, "q_mvar"]) > settings.tol_mw:
                net.sgen.at[i, "q_mvar"] = q
                changed = True
        if not changed:
            break
    rows_out = []
    for uid, r in plan.iterrows():
        if r["kind"] == "load":
            continue
        if uid in gen_idx:
            p, q = float(net.res_gen.at[gen_idx[uid], "p_mw"]), float(net.res_gen.at[gen_idx[uid], "q_mvar"])
        else:
            i = sgen_idx[uid]
            p, q = float(net.res_sgen.at[i, "p_mw"]), float(net.res_sgen.at[i, "q_mvar"])
        rows_out.append({"unit_id": uid, "control": r["control"], "p_mw": p, "q_mvar": q})
    camp_buses = ~net.bus["name"].astype(str).str.endswith("~v")
    bus = pd.DataFrame({"bus": net.bus.loc[camp_buses, "name"].astype(str).to_numpy(),
                        "vm_pu": net.res_bus.loc[camp_buses, "vm_pu"].to_numpy()})
    eg = net.res_ext_grid.iloc[0]
    return ControlLawFlow(True, float(eg["p_mw"]), float(eg["q_mvar"]), pd.DataFrame(rows_out), bus)
