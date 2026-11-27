# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
A reduced REPC_A (PSS/E REPCA1) plant-controller model, for tests only
(GC-04b, GK A C4): never imported by production code.

It checks one claim: N REPCA1s on one PoC branch, gain-shared by
``dyr_writer.share_repca1``, give the PoC the aggregated machine's step
response. It is not PSS/E. The blocks follow the WECC REPC_A block diagram
(WECC REMTF, "WECC Second Generation Wind Turbine Models", 2014, and WECC,
"Solar Photovoltaic Power Plant Modeling and Validation Guideline", 2019),
block by block as commented in ``_step``; every per-unit quantity is on the
controller's own MBASE (``dyr_writer.share_repca1`` cites the base).

Reductions, the same for the aggregate and for the N machines: the
converter (REGC/REEC) is one first-order lag ``tm`` from the REPCA1 output
(Pref, Qext) to the machine's P and Q; the grid is a lossless PoC branch,
P_poc = sum(P_i), Q_poc = sum(Q_i), with the PoC voltage
V = V0 + xg * Q_poc / sum(MBASE); the frequency is an input. Vfrz and the
REEC current limits are not modelled (the steps stay away from them).
Forward Euler, fixed step.
"""
import numpy as np


def _deadband(u, lo, hi):
    """WECC deadband, no offset jump: zero inside [lo, hi], u - edge outside."""
    return np.where(u > hi, u - hi, np.where(u < lo, u - lo, 0.0))


def _col(controllers, name):
    return np.array([float(c[name]) for c in controllers])


def simulate(controllers, mbase, *, p0, q0=None, t_step=1.0, dpref=0.0, dvref=0.0,
             dqref=0.0, df=0.0, t_end=20.0, dt=1e-3, tm=0.05, xg=0.1, v0=1.0):
    """
    PoC P and Q, in pu on sum(MBASE), sampled every step: ``(t, p, q)``.

    ``controllers`` are REPCA1 field dicts (``unit_params.REPCA1_*`` names),
    one per machine, ``mbase`` their MBASEs, ``p0``/``q0`` the machines'
    initial pu dispatch (scalar or one per machine). At ``t_step``: Plant_pref
    and Qref step by ``dpref``/``dqref`` (pu on sum(MBASE), so every
    controller's reference moves by the same MW), Vref by ``dvref`` (pu V), the
    frequency by ``df`` (pu).
    """
    m = np.asarray(mbase, dtype=float)
    mtot = m.sum()
    n = len(m)
    p0 = np.broadcast_to(np.asarray(p0, dtype=float), (n,)).copy()
    q0 = np.zeros(n) if q0 is None else np.broadcast_to(np.asarray(q0, dtype=float), (n,)).copy()
    c = {k: _col(controllers, k) for k in controllers[0]}
    vcflag, refflag, fflag = c["repc_vcflag"], c["repc_refflag"], c["repc_fflag"]

    def measure(xp, xq):
        p_poc, q_poc = (m * xp).sum(), (m * xq).sum()
        v = v0 + xg * q_poc / mtot
        pb, qb = p_poc / m, q_poc / m              # Pbranch, Qbranch in pu_i
        # VcFlag = 1: line drop compensation |V - (Rc + jXc) I|, I = conj(S)/V
        i = (pb - 1j * qb) / v
        v_ldc = np.abs(v - (c["repc_rc"] + 1j * c["repc_xc"]) * i)
        # VcFlag = 0: reactive droop V + Kc * Qbranch
        v_droop = v + c["repc_kc"] * qb
        vmeas = np.where(vcflag == 1, v_ldc, v_droop)
        # RefFlag = 1 filters the measured voltage, 0 the branch Q (Tfltr)
        qin = np.where(refflag == 1, vmeas, qb)
        return p_poc, q_poc, pb, qin

    # initial equilibrium: every error zero, every state at its output
    xp, xq = p0.copy(), q0.copy()
    _, _, pb0, qin0 = measure(xp, xq)
    pref_plant = pb0.copy()                        # Plant_pref, pu_i
    ref_q = qin0.copy()                            # Vref (RefFlag 1) or Qref (0), pu_i
    s_pf, s_pi, s_lag = pb0.copy(), xp.copy(), xp.copy()
    s_qf, s_qi, s_ll = qin0.copy(), xq.copy(), xq.copy()
    f = 0.0

    steps = int(round(t_end / dt))
    t_out, p_out, q_out = np.empty(steps + 1), np.empty(steps + 1), np.empty(steps + 1)
    stepped = False
    for k in range(steps + 1):
        t = k * dt
        if not stepped and t >= t_step:
            stepped = True
            pref_plant = pref_plant + dpref * mtot / m
            ref_q = ref_q + np.where(refflag == 1, dvref, dqref * mtot / m)
            f = df
        p_poc, q_poc, pb, qin = measure(xp, xq)
        t_out[k], p_out[k], q_out[k] = t, p_poc / mtot, q_poc / mtot

        # --- Q path ---
        # filter 1/(1 + s Tfltr) on Vmeas (RefFlag 1) or Qbranch (RefFlag 0)
        ds_qf = (qin - s_qf) / c["repc_tfltr"]
        # error Vref - V or Qref - Q, deadband dbd1/dbd2, limit emin/emax
        e = _deadband(ref_q - s_qf, c["repc_dbd1"], c["repc_dbd2"])
        e = np.clip(e, c["repc_emin"], c["repc_emax"])
        # PI Kp + Ki/s, output limit Qmin/Qmax (non-windup)
        qpi = np.clip(c["repc_kp"] * e + s_qi, c["repc_qmin"], c["repc_qmax"])
        ds_qi = c["repc_ki"] * e
        # lead-lag (1 + s Tft)/(1 + s Tfv) -> Qext
        ratio = c["repc_tft"] / c["repc_tfv"]
        qext = ratio * qpi + (1.0 - ratio) * s_ll
        ds_ll = (qpi - s_ll) / c["repc_tfv"]

        # --- P path (FFlag 1) ---
        # filter 1/(1 + s Tp) on Pbranch
        ds_pf = (pb - s_pf) / c["repc_tp"]
        # frequency error Freq_ref - Freq, deadband fdbd1/fdbd2, droop Ddn (< 0) / Dup (> 0)
        ferr = _deadband(np.full(n, -f), c["repc_fdbd1"], c["repc_fdbd2"])
        droop = c["repc_ddn"] * np.minimum(ferr, 0.0) + c["repc_dup"] * np.maximum(ferr, 0.0)
        # Plant_pref - Pbranch + droop, limit femin/femax
        ep = np.clip(pref_plant - s_pf + droop, c["repc_femin"], c["repc_femax"])
        # PI Kpg + Kig/s, output limit Pmin/Pmax (non-windup), lag 1/(1 + s Tlag) -> Pref
        ppi = np.clip(c["repc_kpg"] * ep + s_pi, c["repc_pmin"], c["repc_pmax"])
        ds_pi = c["repc_kig"] * ep
        pref = np.where(fflag == 1, s_lag, xp)
        ds_lag = (ppi - s_lag) / c["repc_tg"]

        # --- reduced converter: first-order lag to P and Q, pu_i ---
        dxp = np.where(fflag == 1, (pref - xp) / tm, 0.0)
        dxq = (qext - xq) / tm

        s_qf = s_qf + dt * ds_qf
        s_qi = np.clip(s_qi + dt * ds_qi, c["repc_qmin"], c["repc_qmax"])
        s_ll = s_ll + dt * ds_ll
        s_pf = s_pf + dt * ds_pf
        s_pi = np.clip(s_pi + dt * ds_pi, c["repc_pmin"], c["repc_pmax"])
        s_lag = s_lag + dt * ds_lag
        xp = xp + dt * dxp
        xq = xq + dt * dxq
    return t_out, p_out, q_out


def deviation(a, b):
    """Largest |P| or |Q| difference at the PoC between two runs, pu on sum(MBASE)."""
    return max(np.max(np.abs(a[1] - b[1])), np.max(np.abs(a[2] - b[2])))
