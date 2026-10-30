# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
PROTOTYPE, throwaway. Numerical test of the AS sizing loop (research note
ai-load-smoothing-sizing-loop.md, sections 4.2-4.4; owner decision AS-PROTO-1).

Question: do the cuts of section 4.4 converge under the note's rule?

A tiny PyPSA campus (one week, hourly) is sized for intents A, B and D; a
forward pass (transformer, PQ circle, ESCR, a 0.1 s ramp/flicker simulation)
checks the result and adds each failure back as a cut. Two cut sets run side
by side: "note" (4.4 as written) and "revised" (the fixes this prototype
suggests). All numbers are illustrative round values for a generic 100 MW
campus; limits are generic engineering defaults, not any client's data.

The prototype skill's logic branch suggests an HTML walkthrough; the question
here is numerical (does an LP loop converge), so this is a script instead.

Run:  python as_loop_prototype.py   (about 2.5 min; prints the tables to stdout
      and writes summary.csv; neither output is committed)
"""

import logging
import math
import sys
import time
import warnings

import numpy as np
import pandas as pd
import pypsa

logging.disable(logging.WARNING)
warnings.filterwarnings("ignore")

# --------------------------------------------------------------------------
# Parameters (illustrative)
# --------------------------------------------------------------------------
H = 168  # one representative week
W = 8760 / H  # snapshot weight to annualise operating cost
P_CRIT = 80.0  # MW critical load
AUTONOMY_H = 4.0  # AS-AUTONOMY-1: intent A backup = critical load x 4 h
BRIDGE_H = 0.25  # intent D bridge reserve until the genset carries the load

# GPU fluctuation envelope (one DcPhase)
S = 30.0  # MW swing (training start/stop step)
A = 10.0  # MW oscillation amplitude
T = 2.0  # s oscillation period
D_CK, d_CK = 20.0, 30.0  # checkpoint dip depth MW and duration s
TANQ = 0.10  # reactive swing of the load per MW of active swing (tan phi)

# Grid-code style limits (generic, "assumed")
R_MIN = 10.0  # MW/min ramp at the PoC
DP_LIM = 10.0  # MW per 5 s window
PST_LIM = 0.35  # allocated Pst emission limit at the PoC
F_SHAPE = 1.0  # flicker shape factor (conservative)
SCR_MIN = 3.0
I_MAX = 1.2  # GFM fault-current credit, pu of converter MVA
PF_REQ = 0.98  # PoC power factor (lagging) the campus must meet
PF_LOAD = 0.95  # campus load power factor before compensation
X_TR = 0.12  # transformer reactance pu
TR_LIB = [63, 80, 100, 125]  # MVA library; 125 is the largest available
TR_PF = 0.98

GRIDS = {  # tr_max: largest library transformer MVA; tanq: load Q swing per MW
    "strong": dict(S_sc=2000.0, XR=10.0, tr_max=125, tanq=0.10),
    "weak": dict(S_sc=250.0, XR=4.0, tr_max=125, tanq=0.10),
    "strong-tr100": dict(S_sc=2000.0, XR=10.0, tr_max=100, tanq=0.10),
    "weak-tanq0": dict(S_sc=250.0, XR=4.0, tr_max=125, tanq=0.0),
    "weak-flat": dict(S_sc=250.0, XR=4.0, tr_max=125, tanq=0.10, flat=True),
}
TR_ALL = TR_LIB

# Annualised costs EUR/unit/yr (illustrative)
C_TR = 10_000  # per MW of transformer
C_STATCOM = 8_000  # per Mvar
C_PCS = 6_000  # per MVA of PCS above rated P
C_GEN, MC_GEN = 35_000, 250.0
NET_CHARGE = 15.0  # EUR/MWh on import
TECH = {  # E/P hours bounds, power and energy cost, GFM capable, latency s
    "energy": dict(h=(2, 8), cP=30_000, cE=15_000, gfm=True, lat=0.05),
    "highC": dict(h=(0.25, 1), cP=30_000, cE=35_000, gfm=True, lat=0.05),
    "supercap": dict(
        h=(5 / 3600, 60 / 3600), cP=25_000, cE=1_500_000, gfm=False, lat=0.01
    ),
}
EFF = 0.95

INTENTS = {
    # techs offered, genset, backup energy (MWh), power headroom for backup
    "A": dict(
        techs=["energy", "highC", "supercap"],
        genset=False,
        e_backup=P_CRIT * AUTONOMY_H,
        p_backup=P_CRIT,
    ),
    "B": dict(techs=["highC"], genset=True, e_backup=0.0, p_backup=0.0),
    "D": dict(
        techs=["energy", "highC"],
        genset=True,
        e_backup=P_CRIT * BRIDGE_H,
        p_backup=P_CRIT,
    ),
}

rng = np.random.default_rng(7)
hours = np.arange(H)
TRAIN = (rng.random(H) > 0.2).astype(float)  # training in ~80 % of hours
LOAD = 70 + S * TRAIN + 5 * np.sin(2 * np.pi * (hours - 14) / 24)
P_N = LOAD.max()
PRICE_SHAPED = 60 + 40 * np.sin(2 * np.pi * (hours - 8) / 24) + rng.normal(0, 8, H)
PRICE = PRICE_SHAPED


def flicker_factor(r_per_min):
    """
    Pst per % of d for r rectangular changes per minute (analytic method,
    IEC 61000-3-3 annex: t_f = 2.3 (F d)^3.2, Pst = (sum t_f / 600)^(1/3.2)).
    """
    return F_SHAPE * (23.0 * r_per_min / 600.0) ** (1 / 3.2)


R_OSC = 120.0 / T  # two changes per oscillation period
D_LIM = PST_LIM / flicker_factor(R_OSC) / 100  # pu voltage change allowed


# --------------------------------------------------------------------------
# Sub-hourly forward simulation (0.1 s, 30 min): ramp limiter + storage + STATCOM
# --------------------------------------------------------------------------
DT = 0.1
t = np.arange(0, 1800, DT)


def gpu_trace():
    p = np.full_like(t, 70.0)
    train = (t >= 60) & (t < 1500)
    ck = ((t >= 600) & (t < 600 + d_CK)) | ((t >= 1200) & (t < 1200 + d_CK))
    p += S * train
    p += A * np.sin(2 * np.pi * t / T) * (train & ~ck)
    p -= D_CK * ck
    return p


P_GPU = gpu_trace()
RATE = min(R_MIN / 60.0, DP_LIM / 5.0)  # MW/s the PoC may follow


def rate_limit(p):
    g = np.empty_like(p)
    g[0] = p[0]
    step = RATE * DT
    for i in range(1, len(p)):
        g[i] = g[i - 1] + min(max(p[i] - g[i - 1], -step), step)
    return g


P_IDEAL = rate_limit(P_GPU)
REQ = P_GPU - P_IDEAL  # storage power wanted (+ = discharge)
X_REQ = np.cumsum(REQ) * DT / 3600  # MWh discharged so far
P_NEED = np.abs(REQ).max()
E_NEED = 2 * np.abs(X_REQ).max()  # band +-E/2 around the set point


def simulate(p_sm, e_sm, q_dyn, grid):
    """
    Storage clipped to +-p_sm and an SOC band +-e_sm/2; residual goes to the
    grid. STATCOM cancels the load's fast Q swing up to q_dyn.
    """
    sto = np.empty_like(REQ)
    x = 0.0
    half = e_sm / 2
    for i, r in enumerate(REQ):
        s = min(max(r, -p_sm), p_sm)
        xn = x + s * DT / 3600
        if xn > half:
            s = (half - x) * 3600 / DT
        elif xn < -half:
            s = (-half - x) * 3600 / DT
        x += s * DT / 3600
        sto[i] = s
    pg = P_GPU - sto
    q_fast = TANQ * (P_GPU - P_IDEAL)
    qg = TANQ * P_GPU - np.clip(q_fast, -q_dyn, q_dyn)
    n60, n5, n1 = int(60 / DT), int(5 / DT), int(1 / DT)
    ramp60 = np.abs(pg[n60:] - pg[:-n60]).max()
    var5 = np.abs(pg[n5:] - pg[:-n5]).max()
    psi = math.atan(grid["XR"])
    d = (math.cos(psi) * pg + math.sin(psi) * qg) / grid["S_sc"]
    win = np.lib.stride_tricks.sliding_window_view(d, n1 + 1)
    d_fast = (win.max(axis=1) - win.min(axis=1)).max()
    return dict(
        ramp60=ramp60, var5=var5, d=d_fast, pst=100 * d_fast * flicker_factor(R_OSC)
    )


# --------------------------------------------------------------------------
# Capacity expansion LP
# --------------------------------------------------------------------------
def build(intent, state):
    cfg = INTENTS[intent]
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(H, name="snapshot"))
    n.snapshot_weightings["objective"] = W  # stores and generators keep 1 h
    n.add("Bus", "poc")
    n.add("Bus", "mv")
    n.add("Generator", "import", bus="poc", p_nom=1e4, marginal_cost=PRICE + NET_CHARGE)
    n.add(
        "Generator",
        "export",
        bus="poc",
        p_nom=1e4,
        p_min_pu=-1,
        p_max_pu=0,
        marginal_cost=0.9 * PRICE,
    )
    fr = state["tr_frozen"]
    n.add(
        "Link",
        "trafo",
        bus0="poc",
        bus1="mv",
        p_min_pu=-1,
        p_nom=fr if fr else 0,
        p_nom_extendable=not fr,
        capital_cost=C_TR,
    )
    n.add("Load", "gpu", bus="mv", p_set=LOAD)
    for k in cfg["techs"]:
        c = TECH[k]
        n.add("Bus", k)
        n.add(
            "Store",
            k,
            bus=k,
            e_nom_extendable=True,
            e_cyclic=True,
            capital_cost=c["cE"],
        )
        n.add(
            "Link",
            k + " ch",
            bus0="mv",
            bus1=k,
            efficiency=EFF,
            p_nom_extendable=True,
            capital_cost=c["cP"],
        )
        n.add(
            "Link", k + " dis", bus0=k, bus1="mv", efficiency=EFF, p_nom_extendable=True
        )
    if cfg["genset"]:
        n.add(
            "Generator",
            "genset",
            bus="mv",
            p_nom_extendable=True,
            p_nom_min=P_CRIT,
            capital_cost=C_GEN,
            marginal_cost=MC_GEN,
        )
    return n


def sel(var, name):
    return var.sel(name=name)


def extra(intent, state, variant):
    cfg, cuts = INTENTS[intent], state["cuts"]

    def f(n, sns):
        m = n.model
        techs = cfg["techs"]
        Pn, Ep, Lp = m["Link-p_nom"], m["Store-e_nom"], m["Link-p"]
        Es = m["Store-e"]
        idx = pd.Index(techs, name="tech")
        Psm = m.add_variables(lower=0, coords=[idx], name="Psm")
        Esm = m.add_variables(lower=0, coords=[idx], name="Esm")
        Sx = m.add_variables(lower=0, coords=[idx], name="Sx")
        Qss = m.add_variables(lower=0, name="Qss")
        Qdyn = m.add_variables(lower=0, name="Qdyn")
        cost = C_STATCOM * (Qss + Qdyn)
        P, E = {}, {}
        for k in techs:
            P[k], E[k] = sel(Pn, k + " dis"), sel(Ep, k)
            m.add_constraints(sel(Pn, k + " ch") == P[k], name=f"tie {k}")
            lo, hi = TECH[k]["h"]
            m.add_constraints(E[k] >= lo * P[k], name=f"hmin {k}")
            m.add_constraints(E[k] <= hi * P[k], name=f"hmax {k}")
            ps, es = Psm.sel(tech=k), Esm.sel(tech=k)
            # 4.2.4 headroom: the smoothing slice is never sold to arbitrage
            m.add_constraints(sel(Lp, k + " dis") + ps <= P[k], name=f"hd {k}")
            m.add_constraints(sel(Lp, k + " ch") + ps <= P[k], name=f"hc {k}")
            m.add_constraints(sel(Es, k) - 0.5 * es <= E[k], name=f"eh {k}")
            m.add_constraints(es <= E[k], name=f"ecap {k}")
            cost = cost + C_PCS * Sx.sel(tech=k)
            if variant == "revised":  # PQ inner bound valid only up to r*P
                m.add_constraints(Sx.sel(tech=k) <= 0.2 * P[k], name=f"sxcap {k}")
        # 4.2.4 SOC lower band: backup slice + half the smoothing band
        m.add_constraints(
            sum(sel(Es, k) - 0.5 * Esm.sel(tech=k) for k in techs) >= cfg["e_backup"],
            name="soc low",
        )
        if cfg["p_backup"]:
            m.add_constraints(
                sum(P[k] - Psm.sel(tech=k) - sel(Lp, k + " dis") for k in techs)
                >= cfg["p_backup"],
                name="backup power",
            )
        if cfg["genset"]:
            g = m["Generator-p"].sel(name="genset")
            m.add_constraints(
                (g * W).sum() <= 100 * m["Generator-p_nom"].sel(name="genset"),
                name="run hours",
            )
        # 4.2.3 floors (note form), then any cut that raised them
        Psum, Esum = Psm.sum(), Esm.sum()
        p_floor = max(S - DP_LIM, A - DP_LIM / 2) + cuts.get("p_floor", 0)
        e_floor = 2 * max(
            S**2 / (2 * R_MIN * 60), D_CK * d_CK / 3600, A * T / math.pi / 3600
        ) + cuts.get("e_floor", 0)
        m.add_constraints(Psum >= p_floor, name="p floor")
        m.add_constraints(Esum >= e_floor, name="e floor")
        # 4.4 cuts
        if "import_cap" in cuts:
            m.add_constraints(sel(Lp, "trafo") <= cuts["import_cap"], name="imp cap")
            m.add_constraints(sel(Lp, "trafo") >= -cuts["import_cap"], name="exp cap")
        if "q_need" in cuts:
            q_st = Qss if variant == "revised" else Qss + Qdyn
            credit = math.sqrt(1 + 2 / 0.2) if variant == "revised" else 1.0
            if variant == "note":  # constant Q_need of the failing iterate
                m.add_constraints(
                    q_st + credit * Sx.sum() >= cuts["q_need"], name="pq cut"
                )
            else:  # per hour, linear in the transformer flow (chord bound on x*S^2)
                p_cap = cuts.get("import_cap", TR_LIB[-1] * TR_PF)
                k = X_TR * p_cap / (TR_PF**2 * TR_LIB[-1]) - math.tan(math.acos(PF_REQ))
                q_load = pd.Series(LOAD * math.tan(math.acos(PF_LOAD)), index=sns)
                m.add_constraints(
                    q_st + credit * Sx.sum() - k * sel(Lp, "trafo")
                    >= q_load + cuts["q_need"],
                    name="pq cut",
                )
        if "gfm" in cuts:
            gfm = [k for k in techs if TECH[k]["gfm"]]
            m.add_constraints(
                sum(I_MAX * (P[k] + Sx.sel(tech=k)) for k in gfm) >= cuts["gfm"],
                name="escr cut",
            )
        if "pst" in cuts:
            g = state["grid"]
            psi = math.atan(g["XR"])
            if variant == "note":  # P^smooth + kappa Q >= S - d_lim S_sc (+ raises)
                kappa = g["XR"] / (1 + g["XR"])
                m.add_constraints(
                    Psum + kappa * (Qss + Qdyn) >= cuts["pst"], name="pst cut"
                )
            else:  # residual P and Q swings, each floored at zero, true sensitivities
                Pres = m.add_variables(lower=0, name="Pres")
                Qres = m.add_variables(lower=0, name="Qres")
                m.add_constraints(Pres >= S + A - Psum, name="pres")
                m.add_constraints(Qres >= TANQ * (S + A) - Qdyn, name="qres")
                m.add_constraints(
                    math.cos(psi) * 2 * Pres + math.sin(psi) * Qres <= cuts["pst"],
                    name="pst cut",
                )
        m.add_objective(m.objective.expression + cost, overwrite=True)

    return f


def solve(intent, state, variant):
    n = build(intent, state)
    t0 = time.time()
    status, _ = n.optimize(
        solver_name="highs",
        extra_functionality=extra(intent, state, variant),
        solver_options={"output_flag": False},
        include_objective_constant=False,
    )
    dt = time.time() - t0
    if status != "ok":
        return None, None, dict(secs=dt), None
    m = n.model
    sol = lambda v: m[v].solution  # noqa: E731
    techs = INTENTS[intent]["techs"]
    caps = {"trafo": float(n.links.p_nom_opt["trafo"])}
    for k in techs:
        caps[f"P_{k}"] = float(n.links.p_nom_opt[k + " dis"])
        caps[f"E_{k}"] = float(n.stores.e_nom_opt[k])
    caps["Psm"] = float(sol("Psm").sum())
    caps["Esm"] = float(sol("Esm").sum())
    caps["Sx"] = float(sol("Sx").sum())
    caps["Qss"], caps["Qdyn"] = float(sol("Qss")), float(sol("Qdyn"))
    if INTENTS[intent]["genset"]:
        caps["genset"] = float(n.generators.p_nom_opt["genset"])
    for k in techs:
        caps[f"Sx_{k}"] = float(sol("Sx").sel(tech=k))
    capex = (
        C_TR * caps["trafo"]
        + C_STATCOM * (caps["Qss"] + caps["Qdyn"])
        + C_PCS * caps["Sx"]
    )
    capex += sum(
        TECH[k]["cP"] * caps[f"P_{k}"] + TECH[k]["cE"] * caps[f"E_{k}"] for k in techs
    )
    capex += C_GEN * caps.get("genset", 0.0)
    gp = n.generators_t.p
    opex = W * float(
        (gp["import"] * (PRICE + NET_CHARGE)).sum() + (gp["export"] * 0.9 * PRICE).sum()
    )
    if "genset" in gp:
        opex += W * MC_GEN * float(gp["genset"].sum())
    total = capex + opex
    flow = n.links_t.p0["trafo"].values
    return n, caps, dict(total=total, capex=capex, secs=dt), flow


# --------------------------------------------------------------------------
# Forward pass
# --------------------------------------------------------------------------
def forward(intent, caps, flow, state, variant):
    grid = state["grid"]
    fails = {}
    # transformer: library round-up, N-0
    s_need = np.abs(flow).max() / TR_PF
    lib = next((s for s in TR_LIB if s >= s_need - 1e-6), None)
    tr_mva = state["tr_frozen"] / TR_PF if state["tr_frozen"] else lib
    if lib is None:
        fails["trafo"] = s_need - TR_LIB[-1]
    # reactive need at the PoC (worst hour), PQ circle exact
    tr_s = tr_mva or TR_LIB[-1]
    q_load = LOAD * math.tan(math.acos(PF_LOAD))
    q_tr = X_TR * (flow / TR_PF) ** 2 / tr_s
    q_need = float(
        (q_load + q_tr - math.tan(math.acos(PF_REQ)) * np.maximum(flow, 0)).max()
    )
    techs = INTENTS[intent]["techs"]
    q_pcs = 0.0
    for k in techs:  # exact PQ circle on each PCS at its rated P
        p, sx = caps[f"P_{k}"], caps[f"Sx_{k}"]
        q_pcs += math.sqrt(max(0.0, (p + sx) ** 2 - p**2))
    q_st = caps["Qss"] + caps["Qdyn"]
    q_steady = max(0.0, q_need - q_pcs)
    if q_pcs + q_st < q_need - 1e-3:
        fails["pq"] = q_need - q_pcs - q_st
    q_dyn_avail = max(0.0, q_st - q_steady)
    # ESCR with GFM credit
    gfm = sum(caps[f"P_{k}"] for k in techs if TECH[k]["gfm"]) + caps["Sx"]
    escr = (grid["S_sc"] + I_MAX * gfm) / P_N
    if escr < SCR_MIN - 1e-6:
        fails["escr"] = SCR_MIN - escr
    # ramp, variation and flicker from the 0.1 s simulation
    sim = simulate(caps["Psm"], caps["Esm"], q_dyn_avail, grid)
    if sim["ramp60"] > R_MIN * 1.01 or sim["var5"] > DP_LIM * 1.01:
        fails["ramp"] = max(0.0, P_NEED - caps["Psm"]), max(0.0, E_NEED - caps["Esm"])
    if sim["pst"] > PST_LIM * 1.001:
        fails["pst"] = sim["pst"]
    info = dict(
        tr_lib=lib,
        s_need=s_need,
        q_need=q_need,
        q_pcs=q_pcs,
        escr=escr,
        q_dyn_avail=q_dyn_avail,
        **sim,
    )
    return fails, info


def add_cuts(fails, info, caps, state, variant):
    cuts, grid = state["cuts"], state["grid"]
    no_lever = []
    if "trafo" in fails:
        cuts["import_cap"] = TR_LIB[-1] * TR_PF
    if "pq" in fails:
        # constant Q_need from this iterate, raised by the shortfall if repeated
        if variant == "note":
            cuts["q_need"] = max(info["q_need"], cuts.get("q_need", 0) + fails["pq"])
        else:  # per-hour cut; on a repeat failure raise its margin by the shortfall
            cuts["q_need"] = cuts["q_need"] + fails["pq"] if "q_need" in cuts else 0.0
    if "escr" in fails:
        need = SCR_MIN * P_N - grid["S_sc"]
        cuts["gfm"] = max(need, cuts.get("gfm", 0) + fails["escr"] * P_N)
        if not any(TECH[k]["gfm"] for k in INTENTS[state["intent"]]["techs"]):
            no_lever.append("escr")
    if "ramp" in fails:
        dp, de = fails["ramp"]
        cuts["p_floor"] = cuts.get("p_floor", 0) + dp
        cuts["e_floor"] = cuts.get("e_floor", 0) + de
    if "pst" in fails:
        short = (info["d"] - D_LIM) * grid["S_sc"]  # voltage shortfall in MW-equivalent
        if variant == "note":
            kappa = grid["XR"] / (1 + grid["XR"])
            now = caps["Psm"] + kappa * (caps["Qss"] + caps["Qdyn"])
            base = S - D_LIM * grid["S_sc"]
            cuts["pst"] = max(cuts.get("pst", base), now + short)
        else:
            psi = math.atan(grid["XR"])
            now = math.cos(psi) * 2 * max(0.0, S + A - caps["Psm"]) + math.sin(
                psi
            ) * max(0.0, TANQ * (S + A) - caps["Qdyn"])
            base = D_LIM * grid["S_sc"]
            cuts["pst"] = min(cuts.get("pst", base), now - short)
    return no_lever


def capacity_move(a, b):
    worst = 0.0
    for k, v in b.items():
        if k == "trafo":
            continue
        u = a.get(k, 0.0)
        worst = max(worst, abs(v - u) / max(abs(u), abs(v), 1.0))
    return worst


def run(intent, grid_name, variant, max_iter=10, verbose=False):
    global TR_LIB, TANQ, PRICE
    PRICE = (
        np.full(H, PRICE_SHAPED.mean())
        if GRIDS[grid_name].get("flat")
        else PRICE_SHAPED
    )
    TR_LIB = [s for s in TR_ALL if s <= GRIDS[grid_name]["tr_max"]]
    TANQ = GRIDS[grid_name]["tanq"]
    state = dict(intent=intent, grid=GRIDS[grid_name], cuts={}, tr_frozen=None)
    hist = []
    for it in range(1, max_iter + 1):
        n, caps, cost, flow = solve(intent, state, variant)
        if n is None:
            hist.append(dict(it=it, lp_s=cost["secs"], fails="LP infeasible"))
            break
        f0 = time.time()
        fails, info = forward(intent, caps, flow, state, variant)
        cost["fwd_secs"] = time.time() - f0
        row = dict(
            it=it,
            total=cost["total"],
            capex=cost["capex"],
            lp_s=cost["secs"],
            fwd_s=cost["fwd_secs"],
            fails=",".join(fails) or "-",
            pst=info["pst"],
            escr=info["escr"],
            q_need=info["q_need"],
            q_pcs=info["q_pcs"],
            tr_lib=info["tr_lib"],
            **caps,
        )
        hist.append(row)
        if verbose:
            print(intent, grid_name, variant, row)
        if not fails:
            break
        no_lever = add_cuts(fails, info, caps, state, variant)
        if no_lever:
            row["fails"] += " (no lever)"
            break
        if it == 2 and not state["tr_frozen"]:  # rule 5: round up and freeze
            lib = info["tr_lib"] or TR_LIB[-1]
            state["tr_frozen"] = lib * TR_PF
    return pd.DataFrame(hist)


def verdict(df):
    """Apply the note's rule (4.4 steps 3, 6, 7) to a history, capped at 5."""
    for i, r in df.iterrows():
        if r["it"] > 5:
            break
        if r["fails"] == "-":
            return "converged (all pass)", int(r["it"])
        if "no lever" in r["fails"] or "infeasible" in r["fails"]:
            return "stopped: no lever", int(r["it"])
        if i >= 1:
            prev = df.iloc[i - 1]
            dc = abs(r["total"] - prev["total"]) / abs(prev["total"])
            caps = {
                k: r[k]
                for k in df.columns
                if k[:2] in ("P_", "E_")
                or k in ("Psm", "Esm", "Sx", "Qss", "Qdyn", "genset")
            }
            pcap = {k: prev[k] for k in caps}
            if dc < 0.005 and capacity_move(pcap, caps) < 0.02:
                return f"tolerance stop, still failing: {r['fails']}", int(r["it"])
    return "not converged in 5", 5


if __name__ == "__main__":
    print(
        f"P_NEED={P_NEED:.1f} MW  E_NEED={E_NEED:.2f} MWh  D_LIM={D_LIM * 100:.3f} %  "
        f"note floors P={max(S - DP_LIM, A - DP_LIM / 2):.1f} MW"
    )
    out = []
    for g in GRIDS:
        for variant in ("note", "revised"):
            for intent in INTENTS:
                df = run(intent, g, variant, verbose="-v" in sys.argv)
                v, k = verdict(df)
                print(
                    f"\n### intent {intent} | grid {g} | cuts {variant} -> {v} (round {k})"
                )
                cols = [
                    "it",
                    "total",
                    "capex",
                    "lp_s",
                    "fwd_s",
                    "fails",
                    "pst",
                    "escr",
                    "q_need",
                    "q_pcs",
                    "trafo",
                    "tr_lib",
                    "Psm",
                    "Esm",
                    "Sx",
                    "Qss",
                    "Qdyn",
                ]
                cols += [
                    c for c in df.columns if c[:2] in ("P_", "E_") or c == "genset"
                ]
                fmt = df.reindex(columns=cols).copy()
                fmt["total"] = (fmt["total"] / 1e6).round(3)
                fmt["capex"] = (fmt["capex"] / 1e6).round(3)
                print(fmt.round(2).to_string(index=False))
                last = df.iloc[-1]
                out.append(
                    dict(
                        intent=intent,
                        case=g,
                        cuts=variant,
                        verdict=v,
                        rounds=k,
                        iters_to_pass=int(last["it"]) if last["fails"] == "-" else None,
                        bound=" > ".join(df["fails"].iloc[:-1]) or "-",
                        cost1=round(df["total"].iloc[0] / 1e6, 2),
                        cost_end=round(last["total"] / 1e6, 2),
                        capex_end=round(last["capex"] / 1e6, 2),
                        Psm=round(last["Psm"], 1),
                        Sx=round(last["Sx"], 1),
                        Qst=round(last["Qss"] + last["Qdyn"], 1),
                        lp_s=round(df["lp_s"].mean(), 1),
                    )
                )
    print("\n### Summary")
    summary = pd.DataFrame(out)
    print(summary.to_string(index=False))
    summary.to_csv("summary.csv", index=False)
