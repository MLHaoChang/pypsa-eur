"""
The SSTI unit-interaction-factor screen (grid-compliance issue GC-08, TRGC 19).

Sub-synchronous torsional interaction (SSTI) between a converter plant and a
nearby synchronous machine's shaft is screened with the EPRI unit interaction
factor:

    UIF_i = (S_plant / S_i) * (1 - SC_i / SC_tot)^2

* ``S_plant``: the converter plant's rating (MVA) at the evaluation bus;
* ``S_i``: machine i's rating (MVA);
* ``SC_tot``: the three-phase fault level (MVA) at the evaluation bus with
  every machine in;
* ``SC_i``: the same fault level with machine i out.

A machine whose fault infeed at the bus is nil (SC_i = SC_tot) has UIF 0. A
UIF above the threshold (the grid-code profile's ``ssti_uif_threshold``;
without it ``DEFAULT_THRESHOLD``, marked ``assumed``) flags the machine for an
EMT SSTI study; a UIF equal to the threshold clears.

**Campus.** The machines are the campus's ``genset`` units. The evaluation
buses are every bus where converter-interfaced units (BESS, PV, wind, fuel
cell, UPS) or the rectifier-fed load connect, each with its own ``S_plant``:
at the PCC the grid's infeed dominates SC_tot, which would understate UIF.
The PCC row is kept for information and does not set the verdict. Fault
levels come from ``campus_sc.campus_fault_levels`` (IEC 60909 through
pandapower) in its max and min cases; the verdict is the worst over buses,
cases and machines, since a weak grid raises a machine's share.

Screening only: the binding answer is the EMT study.
"""

from __future__ import annotations

import dataclasses
import math

import pandas as pd

from gridspine.schema.contracts import ContractError

LABEL = "screening; binding answer is the EMT study"
DEFAULT_THRESHOLD = 0.1
#: SC_i above SC_tot by at most this fraction of SC_tot is rounding: clamped and noted; beyond it, refused.
SC_TOLERANCE = 1e-6
PROFILE_KEY = "ssti_uif_threshold"
CASES = ("max", "min")
#: Unit kinds that connect through a converter (the load is rectifier-fed).
CONVERTER_KINDS = ("bess", "pv", "wind", "fuel_cell", "ups", "load")
MACHINE_KIND = "genset"
ROLE_CONVERTER = "converter bus"
ROLE_PCC = "PCC (information)"
PLANT_NOTE = (
    "S_plant is the sum at the bus of each converter-interfaced unit's s_mva and the rectifier-fed load's "
    "p_mw / pf (conservative)"
)
NO_THRESHOLD = "the grid code sets no UIF threshold; demo default"
NO_MACHINES = "no synchronous machine (genset) in the campus: no SSTI screen"
NO_CONVERTERS = "no converter-interfaced unit or load in the campus: no SSTI screen"
CLAMPED = "SC_i above SC_tot within the rounding tolerance: taken as SC_tot"
COLUMNS = (
    "bus",
    "role",
    "case",
    "c",
    "machine",
    "s_plant_mva",
    "s_machine_mva",
    "sc_without_mva",
    "sc_tot_mva",
    "uif",
    "threshold",
    "verdict",
    "assumed",
    "note",
    "label",
)


@dataclasses.dataclass
class UifStudy:
    rows: pd.DataFrame
    verdict: str  # "flag", "clear" or "not evaluated"
    worst_uif: float
    notes: list


def _finite(name: str, v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ContractError(f"UIF screen: {name} must be a number, got {v!r}") from None
    if not math.isfinite(x):
        raise ContractError(f"UIF screen: {name} must be finite, got {v!r}")
    return x


def uif(
    s_plant_mva,
    s_machine_mva,
    sc_without_mva,
    sc_tot_mva,
    tolerance: float = SC_TOLERANCE,
) -> tuple[float, bool]:
    """
    ``(UIF, clamped)`` for one machine. ``clamped`` when SC_i stood above SC_tot within ``tolerance``
    (a fraction of SC_tot) and was taken as SC_tot; further above is refused.
    """
    s_plant = _finite("S_plant", s_plant_mva)
    s_i = _finite("S_i", s_machine_mva)
    sc_i = _finite("SC_i", sc_without_mva)
    sc_tot = _finite("SC_tot", sc_tot_mva)
    if s_plant <= 0:
        raise ContractError(f"UIF screen: S_plant must be > 0, got {s_plant}")
    if s_i <= 0:
        raise ContractError(f"UIF screen: S_i must be > 0, got {s_i}")
    if sc_tot <= 0:
        raise ContractError(f"UIF screen: SC_tot must be > 0, got {sc_tot}")
    if sc_i < 0:
        raise ContractError(f"UIF screen: SC_i must be >= 0, got {sc_i}")
    clamped = False
    if sc_i > sc_tot:
        if sc_i > sc_tot * (1.0 + tolerance):
            raise ContractError(
                f"UIF screen: SC_i {sc_i} above SC_tot {sc_tot}: a machine out cannot raise the "
                f"fault level"
            )
        sc_i, clamped = sc_tot, True
    return s_plant / s_i * (1.0 - sc_i / sc_tot) ** 2, clamped


def judge(value: float, threshold: float) -> str:
    """``flag`` when UIF is above ``threshold``; equal clears."""
    return "flag" if value > threshold else "clear"


def threshold_from_profile(profile: dict | None) -> tuple[float | None, list]:
    """
    The profile's ``ssti_uif_threshold`` and where it comes from: ``(value or None, [{"key", "clause",
    "source"}])``.
    """
    lim = (profile or {}).get(PROFILE_KEY)
    if not isinstance(lim, dict):
        return None, []
    return _finite(PROFILE_KEY, lim["value"]), [
        {"key": PROFILE_KEY, "clause": lim.get("clause"), "source": lim.get("source")}
    ]


def screen(
    s_plant_mva,
    machines: dict,
    sc_tot_mva,
    threshold: float | None = None,
    *,
    assumed: bool = False,
    bus: str = "",
    role: str = ROLE_CONVERTER,
    case: str = "",
    c: float = math.nan,
    notes=(),
) -> pd.DataFrame:
    """
    One row per machine at one evaluation bus. ``machines`` is ``{name: (S_i MVA, SC_i MVA)}``.
    ``threshold`` None takes ``DEFAULT_THRESHOLD`` and marks every row ``assumed``, as does ``assumed``
    (a threshold that is not the code's).
    """
    limit = DEFAULT_THRESHOLD if threshold is None else _finite("threshold", threshold)
    if limit <= 0:
        raise ContractError(f"UIF screen: threshold must be > 0, got {limit}")
    rows = []
    for name, (s_i, sc_i) in machines.items():
        value, clamped = uif(s_plant_mva, s_i, sc_i, sc_tot_mva)
        note = [
            *notes,
            *([NO_THRESHOLD] if threshold is None else []),
            *([CLAMPED] if clamped else []),
        ]
        rows.append(
            {
                "bus": bus,
                "role": role,
                "case": case,
                "c": c,
                "machine": name,
                "s_plant_mva": float(s_plant_mva),
                "s_machine_mva": float(s_i),
                "sc_without_mva": min(float(sc_i), float(sc_tot_mva)),
                "sc_tot_mva": float(sc_tot_mva),
                "uif": value,
                "threshold": limit,
                "verdict": judge(value, limit),
                "assumed": threshold is None or assumed,
                "note": "; ".join(note),
                "label": LABEL,
            }
        )
    return pd.DataFrame(rows, columns=list(COLUMNS))


def plant_mva_by_bus(units: pd.DataFrame) -> dict:
    """``{bus: S_plant MVA}`` over the converter-interfaced units and loads (``PLANT_NOTE``)."""
    out: dict = {}
    for _, u in units[units["kind"].isin(CONVERTER_KINDS)].iterrows():
        s = (
            float(u["p_mw"]) / float(u["pf"])
            if u["kind"] == "load"
            else float(u["s_mva"])
        )
        out[str(u["bus"])] = out.get(str(u["bus"]), 0.0) + s
    return out


def _s_mva(levels: pd.DataFrame, bus: str, case: str) -> float:
    row = levels[levels["bus"] == bus].iloc[0]
    return math.sqrt(3.0) * float(row["vn_kv"]) * float(row[f"ikss_{case}_ka"])


def campus_uif(
    campus, threshold: float | None = None, *, assumed: bool = False, fault_levels=None
) -> UifStudy:
    """
    The UIF screen of a built campus (module docstring). ``fault_levels(campus, installed)`` defaults to
    ``campus_sc.campus_fault_levels``.
    """
    from gridspine.static.harmonic_scan import grid_c

    if fault_levels is None:
        from gridspine.static.campus_sc import campus_fault_levels as fault_levels
    units = campus.units
    machines = units[units["kind"] == MACHINE_KIND]
    plants = plant_mva_by_bus(units)
    if machines.empty or not plants:
        note = NO_MACHINES if machines.empty else NO_CONVERTERS
        return UifStudy(
            rows=pd.DataFrame(columns=list(COLUMNS)),
            verdict="not evaluated",
            worst_uif=math.nan,
            notes=[note],
        )
    pcc = str(campus.net.bus.at[int(campus.net.ext_grid.iloc[0]["bus"]), "name"])
    every = set(units.index)
    full = fault_levels(campus, every)
    out = {uid: fault_levels(campus, every - {uid}) for uid in machines.index}
    evaluate = [(b, s, ROLE_CONVERTER) for b, s in sorted(plants.items())] + [
        (pcc, sum(plants.values()), ROLE_PCC)
    ]
    frames = []
    for case in CASES:
        for bus, s_plant, role in evaluate:
            vn = float(full.loc[full["bus"] == bus, "vn_kv"].iloc[0])
            ms = {
                uid: (float(machines.at[uid, "s_mva"]), _s_mva(out[uid], bus, case))
                for uid in machines.index
            }
            frames.append(
                screen(
                    s_plant,
                    ms,
                    _s_mva(full, bus, case),
                    threshold,
                    assumed=assumed,
                    bus=bus,
                    role=role,
                    case=case,
                    c=grid_c(vn, case),
                    notes=[PLANT_NOTE],
                )
            )
    rows = pd.concat(frames, ignore_index=True)
    judged = rows[rows["role"] == ROLE_CONVERTER]
    worst = float(judged["uif"].max())
    verdict = "flag" if (judged["verdict"] == "flag").any() else "clear"
    return UifStudy(rows=rows, verdict=verdict, worst_uif=worst, notes=[PLANT_NOTE])
