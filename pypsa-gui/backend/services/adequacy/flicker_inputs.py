# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""
What the GPU envelope leaves for the flicker screen once the smoothing battery has acted (AS-04).

The residual real swing is ``P_res = max(0, S + A - P_sm)`` and the residual reactive swing
``Q_res = max(0, tan φ_swing · (S + A) - Q_dyn)``. The step the grid sees as a voltage change is
``cos ψ · P_res + sin ψ · Q_res`` (ΔU ≈ R·ΔP + X·ΔQ over the PoC impedance, ψ = atan(X/R)),
in MVA to divide by S_sc. IEC TR 61000-3-7 gives the ΔU/U ≈ ΔS/S_sc approximation and the
changes-per-minute classes; the oscillation cycles 60/T times a minute and each cycle is two voltage changes, so r = 120/T
(``gridspine.static.flicker.changes_per_min``).
"""
from __future__ import annotations

import math

from gridspine.static.flicker import changes_per_min

#: Name of the lumped envelope row in the flicker screen's unit table.
ENVELOPE_UNIT = "gpu_envelope"
#: PoC X/R when the project enters none. Assumed: a typical MV/HV transmission-level figure.
DEFAULT_XR = 4.0
XR_ASSUMED = "assumed; typical grid X/R, replace with the network operator's value"


def residual_swing(s_mw: float, a_mw: float, p_sm_mw: float, tan_phi_swing: float = 0.0,
                   q_dyn_mvar: float = 0.0) -> tuple[float, float]:
    """``(P_res, Q_res)`` after smoothing: both floored at zero."""
    swing = float(s_mw) + float(a_mw)
    return (max(0.0, swing - float(p_sm_mw)),
            max(0.0, float(tan_phi_swing) * swing - float(q_dyn_mvar)))


def voltage_form_mva(p_res: float, q_res: float, xr: float = DEFAULT_XR) -> float:
    """``cos ψ · P_res + sin ψ · Q_res``, ψ = atan(X/R); the equivalent step for ``d = this / S_sc``."""
    psi = math.atan(float(xr))
    return math.cos(psi) * float(p_res) + math.sin(psi) * float(q_res)


def envelope_unit(envelope: dict, p_sm_mw: float, xr: float = DEFAULT_XR) -> dict:
    """The flicker-unit record of the envelope's worst phase: its voltage-form step and its voltage changes per minute (120/T)."""
    mx = envelope["max"]
    period = float(mx["oscillation_period_s"])
    p_res, q_res = residual_swing(mx["swing_mw"], mx["oscillation_amplitude_mw"], p_sm_mw,
                                  float(mx.get("tan_phi_swing") or 0.0))
    return {"unit": ENVELOPE_UNIT, "step_mva": voltage_form_mva(p_res, q_res, xr),
            "changes_per_min": changes_per_min(60.0 / period) if period > 0 else None,
            "p_res_mw": p_res, "q_res_mvar": q_res, "p_sm_mw": float(p_sm_mw), "xr": float(xr)}
