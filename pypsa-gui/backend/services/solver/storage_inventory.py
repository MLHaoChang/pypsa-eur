"""Every storage of a network, in either layout (AS-11).

A storage is either a `StorageUnit` (energy = p_nom x max_hours) or the split form of
`services/solver/split_storage.py`: a `Store` (energy E) with a charger and a discharger `Link` (power P). Readers
that need power, energy and efficiencies take them from `list_storages` rather than from `n.storage_units`, so a
split storage is counted like the equal `StorageUnit`.

Units follow the AS-02 convention: P is the AC-side MW (the charger's p_nom, which the power tie makes equal to
discharger efficiency x discharger p_nom), E is store-side MWh, as a `StorageUnit`'s state of charge is.
Sizes follow `services.adequacy.copt.solved_capacity`: the solved size of an extendable row, else its nominal.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from services.solver.split_storage import _tagged

if TYPE_CHECKING:
    import pypsa

STORAGE_UNIT = "storage_unit"
SPLIT = "split"


@dataclass(frozen=True)
class StorageEntry:
    name: str                 # the StorageUnit, or the split storage's Store
    layout: str               # STORAGE_UNIT or SPLIT
    bus: str                  # the AC bus it charges from and discharges to
    carrier: str
    power_component: str      # "storage_units", or "links" (the charger) for a split storage
    power_row: str            # the row of `power_component` whose size is P
    p_nom_mw: float           # P, AC-side MW
    e_nom_mwh: float          # E, MWh
    eff_store: float
    eff_dispatch: float
    charger: str | None = None
    discharger: str | None = None


def efficiency(row, col: str) -> float:
    """PyPSA's default is 1.0; a missing, non-finite or non-positive value degrades to lossless."""
    try:
        v = float(row[col])
    except (KeyError, TypeError, ValueError):
        return 1.0
    return v if math.isfinite(v) and v > 0.0 else 1.0


def _energy_capacity(row) -> float:
    """`solved_capacity`'s rule on a Store's e_nom columns."""
    from services.adequacy.copt import solved_capacity
    renamed = pd.Series({"p_nom": row.get("e_nom"), "p_nom_opt": row.get("e_nom_opt"),
                         "p_nom_extendable": row.get("e_nom_extendable")})
    return float(solved_capacity(renamed))


def _max_hours(row) -> float:
    try:
        h = float(row["max_hours"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return h if math.isfinite(h) and h >= 0 else 0.0


def list_storages(n: pypsa.Network) -> list[StorageEntry]:
    """Every StorageUnit (in frame order), then every split storage (by Store name). A split storage whose
    charger or discharger Link is absent (for example removed with its bus) is not listed."""
    from services.adequacy.copt import solved_capacity

    out: list[StorageEntry] = []
    su = getattr(n, "storage_units", None)
    if su is not None and not su.empty:
        for s in su.index:
            row = su.loc[s]
            p = float(solved_capacity(row))
            out.append(StorageEntry(
                name=str(s), layout=STORAGE_UNIT, bus=str(row.get("bus")), carrier=str(row.get("carrier") or ""),
                power_component="storage_units", power_row=str(s), p_nom_mw=p, e_nom_mwh=_max_hours(row) * p,
                eff_store=efficiency(row, "efficiency_store"), eff_dispatch=efficiency(row, "efficiency_dispatch")))
    tagged = _tagged(n)
    if tagged is None:
        return out
    links = n.links
    for name in sorted(tagged.index):
        s = tagged.loc[name]
        ch, dis = s.get("charger"), s.get("discharger")
        if ch not in links.index or dis not in links.index:
            continue
        crow, drow = links.loc[ch], links.loc[dis]
        out.append(StorageEntry(
            name=str(name), layout=SPLIT, bus=str(crow.get("bus0")), carrier=str(s.get("carrier") or ""),
            power_component="links", power_row=str(ch), p_nom_mw=float(solved_capacity(crow)),
            e_nom_mwh=_energy_capacity(s), eff_store=efficiency(crow, "efficiency"),
            eff_dispatch=efficiency(drow, "efficiency"), charger=str(ch), discharger=str(dis)))
    return out


def split_ac_flows(n: pypsa.Network, entry: StorageEntry) -> tuple[pd.Series, pd.Series] | None:
    """(charge, discharge) MW at the AC bus of a solved split storage, both >= 0 and indexed by snapshot: the
    charger's draw (p0) and the discharger's delivery (-p1), the two halves of a StorageUnit's signed `p`. None
    when the solve wrote no Link flows for it."""
    lt = getattr(n, "links_t", None)
    p0, p1 = getattr(lt, "p0", None), getattr(lt, "p1", None)
    if p0 is None or p1 is None or entry.charger not in p0.columns or entry.discharger not in p1.columns:
        return None
    sns = n.snapshots
    charge = p0[entry.charger].reindex(sns).fillna(0.0).astype(float).clip(lower=0.0)
    discharge = (-p1[entry.discharger].reindex(sns).fillna(0.0).astype(float)).clip(lower=0.0)
    return charge, discharge
