"""The flicker screen at the point of connection (PoC)
(grid-to-process issue 17, flicker part).

A load that steps its power up and down (a burst-fired heater, a cycling
compressor) makes the PoC voltage step with it. This module turns each unit's
largest power step and its rate into the short-term flicker severity Pst it
causes at the PoC, by the analytic method of IEC 61000-3-7 (after
IEC 61000-3-3), and judges it against the grid code's Pst and Plt limits:

    d %      = 100 * step_mva / S_k                  (relative voltage change)
    t_f      = 2.3 * (F * d) ** 3.2                  (flicker impression time per change, s; F = 1)
    Pst,unit = (10 * r * t_f / 600) ** (1 / 3.2)      (r voltage changes per minute, 10 min window)
    Pst      = (sum_i Pst,i ** 3) ** (1 / 3)          (IEC 61000-3-7 general summation law, alpha 3)
    Plt      = Pst                                    (the pattern repeats steadily over 2 h)

``r`` counts voltage changes, not cycles: a heater switched on and off once a
minute makes two changes a minute (``changes_per_min``).

S_k is a caller input: the module reads no fault level itself (the scorecard
passes ``_poc_sk_mva``, the campus study its PCC's minimum case).

Flicker is decided by step and rate only, never by the unit's control type: a
step or a rate of 0 declares a steady load. A unit with either missing is "no
input": its row is kept with verdict ``unknown`` and it blocks a pass at its
PoC. A unit outside the analytic method's range (changes less than
``MIN_INTERVAL_S`` apart, or d above ``D_MAX_PCT``) is ``unknown`` too: it
needs a flicker-meter study, and it never passes.

Each row: verdict ``pass``/``fail``/``unknown``. A row is judged on whichever
of the Pst and Plt limits is set, and is ``unknown`` only when neither is.

Pure pandas; no engine import.
"""
from __future__ import annotations

import dataclasses
import math

import pandas as pd

from gridspine.schema.contracts import ContractError

TOLERANCE = 1e-9
#: The step's shape factor: 1 for a rectangular step (the conservative case).
SHAPE_FACTOR = 1.0
#: Pst's observation window, s.
T_P_S = 600.0
#: The summation exponent across units (IEC 61000-3-7 general summation law; assumed).
ALPHA = 3.0
#: The analytic method's range: changes at least 1 s apart (IEC 61000-3-3 analytic method, not
#: verified against the text here) and a relative change of at most 10 % (assumed; a larger step
#: is a voltage-dip case, as a motor start is).
MIN_INTERVAL_S = 1.0
D_MAX_PCT = 10.0

LABEL = "static screen; binding answer is a flicker-meter study (IEC 61000-4-15) of the unit's measured pattern"

LEDGER = (
    "d = step / S_k is an upper bound on (dP * R + dQ * X) / U^2, so ignoring the grid's X/R is "
    "conservative (assumed scope)",
    "every change is a rectangular step (shape factor F = 1), the conservative case (assumed)",
    "r counts voltage changes, switch-on and switch-off each, so one on/off cycle is two changes",
    "the step pattern repeats steadily over 2 h, so Plt equals Pst (assumed)",
    "units sum by the IEC 61000-3-7 general summation law with alpha 3 (assumed); units that step "
    "together (alpha 1) are not covered",
    f"the analytic method holds for changes at least {MIN_INTERVAL_S:g} s apart (IEC 61000-3-3 analytic "
    f"method, not verified against the text here) and d up to {D_MAX_PCT:g} % (assumed); outside it a "
    "unit is unknown and needs a flicker-meter study",
    "a planning level is compared whole, not the emission limit the operator allocates to this "
    "installation, so the margin is overstated",
)

UNIT_COLUMNS = (
    "poc", "unit", "step_mva", "changes_per_min", "sk_mva", "d_pct", "pst", "plt", "pst_limit",
    "plt_limit", "pst_margin", "plt_margin", "verdict", "note", "label",
)
POC_COLUMNS = (
    "poc", "units", "no_input_units", "outside_range_units", "law", "sk_mva", "pst", "plt",
    "pst_limit", "plt_limit", "pst_margin", "plt_margin", "verdict", "note", "label",
)


@dataclasses.dataclass(frozen=True)
class Limits:
    """Pst and Plt limits; None for not set."""
    pst: float | None = None
    plt: float | None = None


@dataclasses.dataclass(frozen=True)
class FlickerStudy:
    """``units``: one row per unit, its own contribution alone
    (``UNIT_COLUMNS``); ``pocs``: one row per PoC, summed (``POC_COLUMNS``)."""
    units: pd.DataFrame
    pocs: pd.DataFrame


def changes_per_min(cycles_per_min: float) -> float:
    """Voltage changes per minute of a load switched on and off
    ``cycles_per_min`` times a minute: each switching is one change."""
    return 2.0 * float(cycles_per_min)


def pst(d_pct: float, r_per_min: float) -> float:
    """Pst of a rectangular step of ``d_pct`` % repeated ``r_per_min`` times a minute."""
    t_f = 2.3 * (SHAPE_FACTOR * d_pct) ** 3.2
    return (10.0 * r_per_min * t_f / T_P_S) ** (1.0 / 3.2)


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _judge(p: float, limits: Limits) -> dict:
    plt = p
    pst_fail = limits.pst is not None and p > limits.pst + TOLERANCE
    plt_fail = limits.plt is not None and plt > limits.plt + TOLERANCE
    if limits.pst is None and limits.plt is None:
        verdict = "unknown"
    else:
        verdict = "fail" if pst_fail or plt_fail else "pass"
    notes = [f"Pst {p:.4g}, Plt {plt:.4g}"]
    if pst_fail:
        notes.append(f"Pst above {limits.pst:g}")
    if plt_fail:
        notes.append(f"Plt above {limits.plt:g}")
    if verdict == "unknown":
        notes.append("the grid code sets no flicker limit")
    return {
        "pst": p, "plt": plt, "pst_limit": limits.pst, "plt_limit": limits.plt,
        "pst_margin": None if limits.pst is None else limits.pst - p,
        "plt_margin": None if limits.plt is None else limits.plt - plt,
        "verdict": verdict, "notes": notes,
    }


def _empty_judgement(limits: Limits) -> dict:
    return {"pst": None, "plt": None, "pst_limit": limits.pst, "plt_limit": limits.plt,
            "pst_margin": None, "plt_margin": None, "verdict": "unknown"}


def _sk_of(sk_mva_by_poc, poc):
    sk = _num(sk_mva_by_poc.get(poc)) if sk_mva_by_poc is not None else None
    return sk if sk is not None and sk > 0 else None


NO_SK = "No short-circuit level at the point of connection"
STEP_LABEL, RATE_LABEL = "Largest power step", "Voltage changes per minute"


def _out_of_range(d: float, r: float) -> str | None:
    if r > 0 and 60.0 / r < MIN_INTERVAL_S - TOLERANCE:
        return (f"changes {60.0 / r:.3g} s apart, under the analytic method's {MIN_INTERVAL_S:g} s: "
                "a flicker-meter study is needed")
    if d > D_MAX_PCT + TOLERANCE:
        return (f"d {d:.4g} % is above the analytic method's {D_MAX_PCT:g} %: a flicker-meter study is needed")
    return None


def flicker_table(units: pd.DataFrame, sk_mva_by_poc: dict, limits: Limits) -> FlickerStudy:
    """Per-unit and per-PoC flicker severity.

    ``units``: ``unit``, ``poc``, ``step_mva`` (the largest power step, MVA)
    and ``changes_per_min`` (voltage changes per minute, switch-on and
    switch-off each counted), NaN/None for not given. ``sk_mva_by_poc``: S_k
    (MVA) by PoC; a PoC with none is ``unknown``."""
    for col in ("unit", "poc", "step_mva", "changes_per_min"):
        if col not in units.columns:
            raise ContractError(f"units needs a {col!r} column")
    if units["unit"].duplicated().any():
        raise ContractError(f"duplicate unit(s): {sorted(units.loc[units['unit'].duplicated(), 'unit'])}")
    limits = limits or Limits()

    unit_rows, by_poc = [], {}
    for rec in units.to_dict("records"):
        poc, name = rec["poc"], rec["unit"]
        step, rate = _num(rec["step_mva"]), _num(rec["changes_per_min"])
        for label, v in ((STEP_LABEL, step), (RATE_LABEL, rate)):
            if v is not None and v < 0:
                raise ContractError(f"{name}: {label} must be 0 or more, got {v:g}.")
        sk = _sk_of(sk_mva_by_poc, poc)
        row = {"poc": poc, "unit": name, "step_mva": step, "changes_per_min": rate, "sk_mva": sk,
               "d_pct": None, "label": LABEL}
        entry = by_poc.setdefault(poc, {"known": [], "missing": [], "outside": [], "names": []})
        entry["names"].append(name)
        missing = [label for label, v in ((STEP_LABEL, step), (RATE_LABEL, rate)) if v is None]
        if missing:
            row.update(_empty_judgement(limits), note=f"no input: {' and '.join(missing)} not set")
            entry["missing"].append(name)
        elif sk is None:
            row.update(_empty_judgement(limits), note=NO_SK)
        else:
            d = 100.0 * step / sk
            row["d_pct"] = d
            outside = _out_of_range(d, rate)
            if outside:
                row.update(_empty_judgement(limits), note=outside)
                entry["outside"].append(name)
            else:
                p = pst(d, rate)
                entry["known"].append(p)
                j = _judge(p, limits)
                notes = [f"d {d:.4g} % at {rate:g} changes/min"] + j.pop("notes")
                row.update(j, note="; ".join(notes))
        unit_rows.append(row)

    poc_rows = []
    for poc, entry in by_poc.items():
        sk = _sk_of(sk_mva_by_poc, poc)
        row = {"poc": poc, "units": entry["names"], "no_input_units": entry["missing"],
               "outside_range_units": entry["outside"], "law": f"alpha {ALPHA:g}", "sk_mva": sk,
               "label": LABEL}
        if sk is None:
            row.update(_empty_judgement(limits), note=NO_SK)
            poc_rows.append(row)
            continue
        if not entry["known"]:
            # Every unit lacks an input or is outside the method: there is no Pst to show, not a Pst of 0.
            row.update(_empty_judgement(limits), note="no unit with a Pst: " + ", ".join(
                f"{why} for " + ", ".join(map(str, entry[key]))
                for key, why in (("missing", "no input"), ("outside", "outside the analytic method")) if entry[key]))
            poc_rows.append(row)
            continue
        p = sum(v ** ALPHA for v in entry["known"]) ** (1.0 / ALPHA)
        j = _judge(p, limits)
        notes = j.pop("notes")
        for key, why in (("missing", "no input"), ("outside", "outside the analytic method")):
            if entry[key]:
                if j["verdict"] != "fail":
                    j["verdict"] = "unknown"
                notes.append(f"{why} for " + ", ".join(map(str, entry[key])) + ": it blocks a pass")
        row.update(j, note="; ".join(notes))
        poc_rows.append(row)

    return FlickerStudy(units=pd.DataFrame(unit_rows, columns=list(UNIT_COLUMNS)),
                        pocs=pd.DataFrame(poc_rows, columns=list(POC_COLUMNS)))


def limits_from_profile(profile: dict | None) -> tuple[Limits, list]:
    """The flicker limits of a grid-code profile and where they come from:
    ``(Limits, [{"key", "clause", "source"}])``. Either key may be absent;
    a profile with neither sets no limit (every verdict ``unknown``)."""
    profile = profile or {}
    found, cited = {}, []
    for key in ("pst_limit", "plt_limit"):
        lim = profile.get(key)
        if isinstance(lim, dict):
            found[key] = float(lim["value"])
            cited.append({"key": key, "clause": lim.get("clause"), "source": lim.get("source")})
    return Limits(pst=found.get("pst_limit"), plt=found.get("plt_limit")), cited
