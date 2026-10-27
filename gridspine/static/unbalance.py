"""The voltage-unbalance screen at the point of connection (PoC)
(datacenter grid readiness issue 12, part 2).

A load connected between two phases, or between a phase and neutral, draws
negative-sequence current, and that current raises a negative-sequence
voltage at the PoC through the network's negative-sequence impedance:

    u2 %  = 100 * S_unbal / S_sc2                        (one PoC)
    S_sc2 = U^2 / |Z2|                                   (Z2: the PoC's own entry of inv(Y2))

Both connections give the same formula, as in both |I2| = S / (3 U_ph):
phase to phase, |I2| = |I_bc| / sqrt 3 with
|I_bc| = S / U_ll = S / (sqrt 3 U_ph); phase to neutral, |I2| = |I_a| / 3
with |I_a| = S / U_ph. Then u2 = |I2| |Z2| / U_ph = S |Z2| / (3 U_ph^2)
= S / S_sc2, with S_sc2 = 3 U_ph^2 / |Z2| = U^2 / |Z2|.

``S_unbal`` of a load is its single-phase share times its apparent power.
The loads at a PoC are summed arithmetically, as if all sat on one phase
pair: the triangle inequality makes that an upper bound. With several PoCs
a load at PoC j raises u2 at PoC k through the transfer impedance Z2[k, j]:

    u2_k pu = sum_j S_unbal,j * |Z2[k, j]| / S_base      (Z2 in pu on S_base)

which is S_unbal / S_sc2 for a single PoC.

Z2 is the inverse of ``island_sc_seq.SeqNet.y2`` over the PoC's connected
part of the network. A part with no negative-sequence path to earth (a
singular Y2) has no S_sc2, and its rows are ``unknown``. The grid is
c * Un^2 / Sk'' (the physical Thevenin impedance, so S_sc2 = Sk'' / c).

Each row is one PoC in one short-circuit case, its ``band``: ``min`` binds
(the weaker network, higher u2) and ``max`` is shown beside it. Verdict
``pass``/``fail`` against the profile's ``unbalance_limit_pct``, or
``unknown`` when it sets none, there is no S_sc2, or a load lacks its rating
or share (a missing load blocks a pass, not a fail).

Pure pandas and numpy; no engine import beyond the sequence network.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static import island_sc_seq as SEQ

TOLERANCE = 1e-9
#: Short-circuit cases, the binding one first.
BANDS = ("min", "max")
#: Above this condition number Y2 is taken as singular (no negative-sequence path).
SINGULAR_COND = 1e12

LABEL = "screening; binding answer is an unbalanced load-flow study with the grid's impedance"

LEDGER = (
    "loads at a PoC are summed arithmetically, as if all sat on one phase pair, and the transfer "
    "from other PoCs is added by magnitude: an upper bound (conservative)",
    "a converter's negative-sequence admittance (neg_seq) is not credited, so S_sc2 is lower "
    "than with it (conservative)",
    "genset impedances carry IEC 60909's K_G correction and the grid c * Un^2 / Sk'', as the "
    "sequence networks of island_sc_seq do (screening only)",
    "background unbalance from the grid is not added (non-conservative; the operator's "
    "planning level covers it)",
    "an installation is compared with the whole limit given, not an allocated share of a "
    "planning level, which is lenient where the limit is a planning level",
)

COLUMNS = (
    "poc", "band", "loads", "no_input_loads", "s_unbalanced_mva", "s_sc2_mva", "u2_pct",
    "u2_own_pct", "limit_pct", "margin_pct", "verdict", "note", "label",
)

NO_NEG = "No negative-sequence path at the PoC (singular Y2), so no S_sc2"
NO_LIMIT = "The grid code sets no unbalance limit"


def u2_pct(s_unbalanced_mva: float, s_sc2_mva: float) -> float:
    """u2 % = 100 * S_unbal / S_sc2 at one PoC."""
    return 100.0 * s_unbalanced_mva / s_sc2_mva


def judge(u2: float | None, limit_pct: float | None) -> tuple[str, float | None]:
    """``(verdict, margin_pct)`` of u2 against the limit; ``unknown`` without either."""
    if u2 is None or limit_pct is None:
        return "unknown", None
    margin = limit_pct - u2
    return ("pass" if u2 <= limit_pct + TOLERANCE else "fail"), margin


def z2_transfer(sn: SEQ.SeqNet, pocs) -> pd.DataFrame:
    """|Z2[k, j]| in pu on ``sn.sbase`` between the PoCs (index k, columns j).
    A PoC in a part with no negative-sequence path is NaN; two PoCs in
    different parts do not couple (0)."""
    pocs = [str(p) for p in pocs]
    at = {b: i for i, b in enumerate(sn.buses)}
    unknown = [p for p in pocs if p not in at]
    if unknown:
        raise ContractError(f"PoC bus(es) not in the network: {unknown}")
    out = pd.DataFrame(0.0, index=pocs, columns=pocs)
    done = set()
    for p in pocs:
        if p in done:
            continue
        part = SEQ._component(sn, at[p])
        here = [q for q in pocs if at[q] in part]
        done.update(here)
        y = sn.y2[np.ix_(part, part)]
        if np.linalg.cond(y) > SINGULAR_COND:
            out.loc[here, :] = math.nan
            out.loc[:, here] = math.nan
            continue
        z = np.linalg.inv(y)
        pos = {b: n for n, b in enumerate(part)}
        for k in here:
            for j in here:
                out.at[k, j] = abs(z[pos[at[k]], pos[at[j]]])
    return out


def s_sc2_mva(sn: SEQ.SeqNet, bus) -> float | None:
    """The negative-sequence short-circuit power at ``bus``, S_base / |Z2[k, k]|;
    None with no negative-sequence path."""
    z = z2_transfer(sn, [bus]).iat[0, 0]
    return None if not math.isfinite(z) or z <= 0 else sn.sbase / z


def _share(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if not 0.0 <= v <= 1.0:
        raise ContractError(f"single_phase_share must be within 0..1, got {v:g}")
    return v


def _mva(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def unbalanced_mva(loads: pd.DataFrame) -> tuple[dict, dict, dict]:
    """``({poc: S_unbal MVA}, {poc: [load]}, {poc: [no-input load]})``.

    ``loads``: ``load``, ``poc``, ``s_mva`` and ``single_phase_share`` (0..1);
    a load with either missing is "no input"."""
    for col in ("load", "poc", "s_mva", "single_phase_share"):
        if col not in loads.columns:
            raise ContractError(f"loads needs a {col!r} column")
    if loads["load"].duplicated().any():
        raise ContractError(f"duplicate load(s): {sorted(loads.loc[loads['load'].duplicated(), 'load'])}")
    s, names, missing = {}, {}, {}
    for rec in loads.to_dict("records"):
        poc = str(rec["poc"])
        names.setdefault(poc, []).append(rec["load"])
        missing.setdefault(poc, [])
        s.setdefault(poc, 0.0)
        share, mva = _share(rec["single_phase_share"]), _mva(rec["s_mva"])
        if share is None or mva is None:
            missing[poc].append(rec["load"])
            continue
        s[poc] += share * mva
    return s, names, missing


def unbalance_table(loads: pd.DataFrame, nets: dict, limit_pct: float | None) -> pd.DataFrame:
    """One row per PoC and band (``COLUMNS``).

    ``nets``: ``{band: SeqNet}`` for the bands in ``BANDS`` (``min``, ``max``),
    each from ``island_sc_seq.sequence_net`` with that case. ``u2_pct`` adds
    the transfer from the other PoCs' loads; ``u2_own_pct`` is the PoC's own
    loads alone (S_unbal / S_sc2)."""
    bad = sorted(set(nets) - set(BANDS))
    if bad:
        raise ContractError(f"unknown band(s) {bad}; allowed {list(BANDS)}")
    s, names, missing = unbalanced_mva(loads)
    pocs = list(s)
    rows = []
    for band in [b for b in BANDS if b in nets]:
        sn = nets[band]
        z = z2_transfer(sn, pocs)
        for k in pocs:
            zkk = z.at[k, k]
            row = {"poc": k, "band": band, "loads": names[k], "no_input_loads": missing[k],
                   "s_unbalanced_mva": s[k], "limit_pct": limit_pct, "label": LABEL}
            notes = []
            if not math.isfinite(zkk) or zkk <= 0:
                u2 = own = sc2 = None
                notes.append(NO_NEG)
            else:
                sc2 = sn.sbase / zkk
                own = u2_pct(s[k], sc2)
                u2 = 100.0 * sum(s[j] * z.at[k, j] for j in pocs if math.isfinite(z.at[k, j])) / sn.sbase
                if u2 > own + TOLERANCE:
                    notes.append("includes the transfer from other PoCs' loads")
            verdict, margin = judge(u2, limit_pct)
            if limit_pct is None:
                notes.append(NO_LIMIT)
            if missing[k]:
                if verdict != "fail":
                    verdict = "unknown"
                notes.append("no input for " + ", ".join(map(str, missing[k]))
                             + " (rating or single-phase share): a missing load blocks a pass")
            if band == "min":
                notes.append("min case binds")
            row.update(s_sc2_mva=sc2, u2_pct=u2, u2_own_pct=own, margin_pct=margin, verdict=verdict,
                       note="; ".join(notes))
            rows.append(row)
    return pd.DataFrame(rows, columns=list(COLUMNS))


def limit_from_profile(profile: dict | None) -> tuple[float | None, list]:
    """The profile's ``unbalance_limit_pct`` and where it comes from:
    ``(value or None, [{"key", "clause", "source"}])``."""
    lim = (profile or {}).get("unbalance_limit_pct")
    if not isinstance(lim, dict):
        return None, []
    return float(lim["value"]), [{"key": "unbalance_limit_pct", "clause": lim.get("clause"),
                                  "source": lim.get("source")}]
