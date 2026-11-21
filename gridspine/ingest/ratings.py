"""
The rating an importer states when the source file leaves one unset or unbounded.

A solver needs a finite rating on every branch and source, but an unrated PSS/E
line (RATEA = 0) or a PowerFactory external grid (``ElmXnet``) gives none. The
readers then use a bound that never binds and stays in scale with the case:

    bound = ceil(max(10 × L, floor(kV)))  MVA

``L`` is the case's total load, Σ |S| of the in-service loads in MVA. ``floor`` is
a per-voltage-class floor taken at the highest of the given voltages:

    < 1 kV: 1 · ≤ 36 kV: 50 · ≤ 150 kV: 250 · ≤ 300 kV: 1000 · above: 3000 MVA

A voltage of 0 or a missing one falls to the lowest class. Never ``inf`` or a bare
big-M: preflight refuses ``inf``, and a 1e6-style value hurts LP conditioning.
Each reader lists every such value under ``report.assumed`` and marks the element.
"""

from __future__ import annotations

import math

LOAD_MULTIPLE = 10.0


def _class_floor(kv: float) -> float:
    if kv < 1.0:
        return 1.0
    if kv <= 36.0:
        return 50.0
    if kv <= 150.0:
        return 250.0
    if kv <= 300.0:
        return 1000.0
    return 3000.0


def assumed_rating_mva(total_load_mva: float, *kv: float) -> float:
    """The never-binding bound for a total load and the element's voltage(s)."""
    known = [float(v) for v in kv if v is not None and math.isfinite(float(v)) and v > 0]
    floor = _class_floor(max(known)) if known else _class_floor(0.0)
    return float(math.ceil(max(LOAD_MULTIPLE * float(total_load_mva), floor)))


def total_load_mva(loads) -> float:
    """Σ |S| of the in-service loads of a ``n.loads`` frame, in MVA."""
    if not len(loads):
        return 0.0
    on = loads[loads["active"].astype(bool)] if "active" in loads else loads
    return float(((on["p_set"] ** 2 + on["q_set"] ** 2) ** 0.5).sum())
