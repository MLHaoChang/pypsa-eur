"""The motor-start voltage-change screen at the point of connection (PoC)
(grid-to-process issue 18, design §4.3 item 1).

Starting a large motor draws several times its rated current at a low power
factor for a few seconds, so the PoC voltage dips. This module estimates that
relative voltage change from each process unit's motor and start method and
judges it against the grid code's rapid-voltage-change limit:

    S_rated  = P_motor / (eta * pf_rated)                     (rated input apparent power, MVA)
    S_start  = k * S_rated = sqrt(3) * U * I_start            (k: the start current multiple)
    S_sc     = S_k'' / c                                      (Z = c * Un^2 / S_k'', IEC 60909)
    dU %     = (S_start * sin(phi_start) - Q_gfm) / S_sc * 100

``k`` is the unit's own ``i_start_pu`` when set, else the start method's
default (``START_MULTIPLE``). ``phi_start`` is the start method's ledgered
starting power factor (``START_PF``). ``Q_gfm`` is the reactive power the
grid-forming storage at the PoC can give; when the caller cannot state it, it
is 0 (conservative) and the row says so. A Q_gfm above the start's reactive
draw gives 0, not a voltage rise.

The source is the PoC's minimum short-circuit case when the caller has one
(S_sc = S_k,min / c_min); else the entered S_k'' with c_max (the smaller
S_sc, conservative whichever case it is), and the row notes it.

A unit with no motor rating or no start method is "no input": verdict
``unknown``, never pass. Each row: verdict ``pass``/``fail``/``unknown``.

Pure pandas; the IEC 60909 c factor is ``island_sc_seq.c_factor``, as the harmonic scan's.
"""
from __future__ import annotations

import dataclasses
import math

import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static.flicker import NO_SK, _num, _sk_of
from gridspine.static.island_sc_seq import c_factor

TOLERANCE = 1e-9
#: Start methods: direct on line, soft starter, variable-speed drive. Equal to the GUI's
#: ``models.process_unit.START_METHODS``.
START_METHODS = ("dol", "soft", "vfd")
#: The default start current, as a multiple of rated current, per start method (design §4.3; demo, assumed).
START_MULTIPLE = {"dol": 6.0, "soft": 3.0, "vfd": 1.0}
#: The power factor the grid sees during the start, per start method (demo, assumed): a motor started direct on
#: line or on a soft starter draws mostly magnetising current; a VFD's rectifier draws near-unity displacement power.
START_PF = {"dol": 0.30, "soft": 0.35, "vfd": 0.95}
#: Rated efficiency times rated power factor, turning shaft kW into input kVA (demo, assumed).
ETA_PF_RATED = 0.85

METHOD_NAMES = {"dol": "direct on line", "soft": "soft starter", "vfd": "variable-speed drive"}

LABEL = "static screen; binding answer is a motor-start load-flow or RMS study of the unit's motor"

LEDGER = (
    "S_start = k * P_motor / (eta * pf_rated) with eta * pf_rated "
    f"{ETA_PF_RATED:g} (demo, assumed) and k the unit's start current multiple, else "
    + ", ".join(f"{m} {START_MULTIPLE[m]:g}" for m in START_METHODS) + " (demo, assumed)",
    "starting power factor per start method: " + ", ".join(f"{m} {START_PF[m]:g}" for m in START_METHODS)
    + " (demo, assumed)",
    "dU = (S_start * sin phi_start - Q_gfm) / S_sc is the reactive-power form of dU = (dP * R + dQ * X) / U^2 "
    "with R ignored, a screening estimate (assumed scope)",
    "S_sc = S_k''min / c_min on the PoC's minimum case, else the entered S_k'' / c_max (IEC 60909 voltage factor)",
    "Q_gfm is the grid-forming storage's available reactive power at the PoC, rating * tan(acos pf_rated) at rated "
    "power (assumed); when it cannot be stated it is 0 (conservative)",
    "one motor starts at a time, from a steady PoC voltage (assumed)",
)

#: Said of a limit the profile does not take from a code (a shipped demo value).
PLANNING_LEVEL = ("planning level, not an emission limit allocated to this installation; the project's grid code "
                  "governs")
MOTOR_LABEL, METHOD_LABEL, MULTIPLE_LABEL = "Motor rating", "Start method", "Start current multiple"

UNIT_COLUMNS = (
    "poc", "unit", "motor_kw", "start_method", "start_multiple", "start_pf", "s_start_mva", "q_start_mvar",
    "q_gfm_mvar", "sk_mva", "sk_case", "c", "s_sc_mva", "du_pct", "limit_pct", "margin_pct", "verdict", "note",
    "label",
)


@dataclasses.dataclass(frozen=True)
class Source:
    """A PoC's short-circuit inputs: ``sk_mva`` the entered S_k'' (MVA), ``sk_min_mva`` its minimum case when
    known, ``vn_kv`` its nominal voltage when known (sets c_min). None for not given."""
    sk_mva: float | None = None
    sk_min_mva: float | None = None
    vn_kv: float | None = None


def s_start_mva(motor_kw: float, multiple: float) -> float:
    """The starting apparent power, MVA, of a ``motor_kw`` motor started at ``multiple`` × rated current."""
    return multiple * motor_kw / 1000.0 / ETA_PF_RATED


def du_pct(s_start: float, pf_start: float, q_gfm: float, s_sc: float) -> float:
    """The relative voltage change, %, of a start of ``s_start`` MVA at ``pf_start`` on ``s_sc`` MVA with
    ``q_gfm`` Mvar of grid-forming support; 0 when the support covers the start's reactive draw."""
    q = s_start * math.sqrt(max(0.0, 1.0 - pf_start ** 2))
    return max(0.0, q - q_gfm) / s_sc * 100.0


def short_circuit_power(src: Source | None) -> tuple[float | None, float | None, str | None, float | None, str]:
    """``(S_sc, S_k, case, c, note)`` of a PoC: its minimum case divided by c_min when it has one, else the
    entered S_k'' divided by c_max. ``S_sc`` None when neither is given."""
    src = src or Source()
    sk_min = _sk_of({"": src.sk_min_mva}, "")
    if sk_min is not None:
        vn = _num(src.vn_kv)
        # c_min is 1.0 above 1 kV and 0.95 at LV; an unknown voltage takes 1.0, the smaller S_sc.
        c = c_factor(vn if vn is not None else 1e9, "min")
        return sk_min / c, sk_min, "min", c, f"minimum case S_k''min {sk_min:g} MVA / c_min {c:g}"
    sk = _sk_of({"": src.sk_mva}, "")
    if sk is None:
        return None, None, None, None, NO_SK
    c = c_factor(None, "max")
    return (sk / c, sk, "entered", c,
            f"the PoC has no minimum short-circuit case: the entered S_k'' {sk:g} MVA / c_max {c:g} is used")


def limit_from_profile(profile: dict | None) -> tuple[float | None, str | None]:
    """The rapid-voltage-change limit (``rvc_limit_pct``) of a grid-code profile and its clause; a limit the
    profile does not take from a code carries ``PLANNING_LEVEL``. ``(None, None)`` when the profile sets none."""
    lim = (profile or {}).get("rvc_limit_pct")
    if not isinstance(lim, dict):
        return None, None
    clause = str(lim.get("clause") or "")
    if lim.get("source") != "code":
        clause = f"{clause}; {PLANNING_LEVEL}" if clause else PLANNING_LEVEL
    return float(lim["value"]), clause


def _unknown(row: dict, note: str) -> dict:
    row.update(du_pct=None, margin_pct=None, verdict="unknown", note=note)
    return row


def motor_start_table(units: pd.DataFrame, sources: dict, q_gfm_mvar_by_poc: dict | None,
                      limit_pct: float | None) -> pd.DataFrame:
    """One row per unit (``UNIT_COLUMNS``): its start's voltage change at its PoC.

    ``units``: ``unit``, ``poc``, ``motor_kw`` (kW), ``start_method`` (one of ``START_METHODS``) and
    ``i_start_pu`` (start current multiple, optional), NaN/None/"" for not given. ``sources``: a ``Source`` by
    PoC. ``q_gfm_mvar_by_poc``: Q_gfm by PoC, a PoC missing or None taking 0 with a note. ``limit_pct``: the
    rapid-voltage-change limit, None for not set (every verdict ``unknown``)."""
    for col in ("unit", "poc", "motor_kw", "start_method"):
        if col not in units.columns:
            raise ContractError(f"units needs a {col!r} column")
    if units["unit"].duplicated().any():
        raise ContractError(f"duplicate unit(s): {sorted(units.loc[units['unit'].duplicated(), 'unit'])}")

    rows = []
    for rec in units.to_dict("records"):
        poc, name = rec["poc"], rec["unit"]
        kw, own_k = _num(rec["motor_kw"]), _num(rec.get("i_start_pu"))
        method = rec["start_method"]
        method = None if method is None or (isinstance(method, float) and math.isnan(method)) else str(method).strip()
        method = method or None
        for label, v in ((MOTOR_LABEL, kw), (MULTIPLE_LABEL, own_k)):
            if v is not None and v < 0:
                raise ContractError(f"{name}: {label} must be 0 or more, got {v:g}.")
        if method is not None and method not in START_METHODS:
            raise ContractError(f"{name}: {METHOD_LABEL} {method!r} is not one of {', '.join(START_METHODS)}.")
        s_sc, sk, case, c, sk_note = short_circuit_power(sources.get(poc))
        k = own_k if own_k is not None else START_MULTIPLE.get(method)
        pf = START_PF.get(method)
        row = {"poc": poc, "unit": name, "motor_kw": kw, "start_method": method, "start_multiple": k,
               "start_pf": pf, "s_start_mva": None, "q_start_mvar": None, "q_gfm_mvar": None, "sk_mva": sk,
               "sk_case": case, "c": c, "s_sc_mva": s_sc, "limit_pct": limit_pct, "label": LABEL}
        missing = [label for label, v in ((MOTOR_LABEL, kw), (METHOD_LABEL, method)) if v is None]
        if missing:
            rows.append(_unknown(row, f"no input: {' and '.join(missing)} not set"))
            continue
        if s_sc is None:
            rows.append(_unknown(row, NO_SK))
            continue
        q_gfm = _num((q_gfm_mvar_by_poc or {}).get(poc))
        notes = []
        if q_gfm is None:
            q_gfm = 0.0
            notes.append("Q_gfm 0 (conservative): the grid-forming storage's reactive power at the PoC cannot be "
                         "stated")
        s_start = s_start_mva(kw, k)
        d = du_pct(s_start, pf, q_gfm, s_sc)
        row.update(s_start_mva=s_start, q_start_mvar=s_start * math.sqrt(1.0 - pf ** 2), q_gfm_mvar=q_gfm, du_pct=d)
        head = (f"{METHOD_NAMES[method]} start of {kw:g} kW at {k:g} × rated, pf {pf:g}: S_start {s_start:.4g} MVA; "
                f"dU {d:.4g} %")
        if limit_pct is None:
            row.update(margin_pct=None, verdict="unknown")
            notes.append("the grid code sets no rapid-voltage-change limit")
        else:
            fail = d > limit_pct + TOLERANCE
            row.update(margin_pct=limit_pct - d, verdict="fail" if fail else "pass")
            head += f" ({'above' if fail else 'within'} {limit_pct:g} %)"
        row["note"] = "; ".join([head, sk_note, *notes])
        rows.append(row)
    return pd.DataFrame(rows, columns=list(UNIT_COLUMNS))
