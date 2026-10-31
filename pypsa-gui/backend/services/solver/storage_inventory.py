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
    p_nom_mw: float           # P, AC-side MW: the smaller of p_charge_mw and p_dispatch_mw
    e_nom_mwh: float          # E, MWh
    eff_store: float
    eff_dispatch: float
    standing_loss: float = 0.0  # per hour, the StorageUnit's or the Store's
    p_charge_mw: float = 0.0    # AC-side MW the storage can draw (a split storage: charger size)
    p_dispatch_mw: float = 0.0  # AC-side MW it can deliver (a split storage: discharger size x efficiency)
    charger: str | None = None
    discharger: str | None = None


#: Relative gap above which a split storage's charge and dispatch power are reported as differing (the PyPSA-Eur
#: power tie makes them equal; nothing enforces the tie on fixed sizes).
TIE_RTOL = 1e-6


def ac_power(p_charge: float, p_dispatch: float) -> float:
    """One AC-side P for a split storage: the charge power when the two agree within `TIE_RTOL` (so a migrated
    StorageUnit reads its own P exactly), else the smaller."""
    if abs(p_charge - p_dispatch) <= TIE_RTOL * max(abs(p_charge), abs(p_dispatch)):
        return p_charge
    return min(p_charge, p_dispatch)


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


def _loss(row) -> float:
    try:
        v = float(row["standing_loss"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0 else 0.0


def _max_hours(row) -> float:
    try:
        h = float(row["max_hours"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return h if math.isfinite(h) and h >= 0 else 0.0


def list_storages(n: pypsa.Network) -> list[StorageEntry]:
    """Every StorageUnit (in frame order), then every split storage (by Store name). A split storage whose
    charger or discharger Link is absent (for example removed with its bus) is not listed; `storage_notes`
    names it."""
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
                eff_store=efficiency(row, "efficiency_store"), eff_dispatch=efficiency(row, "efficiency_dispatch"),
                standing_loss=_loss(row), p_charge_mw=p, p_dispatch_mw=p))
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
        eff_d = efficiency(drow, "efficiency")
        p_ch, p_dis = float(solved_capacity(crow)), float(solved_capacity(drow)) * eff_d
        out.append(StorageEntry(
            name=str(name), layout=SPLIT, bus=str(crow.get("bus0")), carrier=str(s.get("carrier") or ""),
            power_component="links", power_row=str(ch), p_nom_mw=ac_power(p_ch, p_dis),
            e_nom_mwh=_energy_capacity(s), eff_store=efficiency(crow, "efficiency"), eff_dispatch=eff_d,
            standing_loss=_loss(s), p_charge_mw=p_ch, p_dispatch_mw=p_dis, charger=str(ch), discharger=str(dis)))
    return out


def storage_notes(n: pypsa.Network, entries: list[StorageEntry] | None = None) -> list[str]:
    """One sentence per storage the readers do not count as built, or count at less than one of its sizes:
    a split storage missing a Link, a split storage whose charge and dispatch power differ, and a `Store` with
    charging and discharging Links but no split-storage tag (an imported PyPSA-Eur battery; not read yet)."""
    entries = list_storages(n) if entries is None else entries
    notes: list[str] = []
    for e in entries:
        if e.layout == SPLIT and ac_power(e.p_charge_mw, e.p_dispatch_mw) != e.p_charge_mw:
            notes.append(f"{e.name}: charger {e.p_charge_mw:g} MW and discharger {e.p_dispatch_mw:g} MW (AC side) "
                         f"differ; counted at {e.p_nom_mw:g} MW")
    stores = getattr(n, "stores", None)
    if stores is None or stores.empty:
        return notes
    links = n.links
    tagged = _tagged(n)
    tagged_names = set() if tagged is None else set(tagged.index)
    listed = {e.name for e in entries if e.layout == SPLIT}
    for name in sorted(tagged_names - listed):
        notes.append(f"{name}: split storage without its charger or discharger link; not counted")
    for name in sorted(set(stores.index) - tagged_names):
        bus = stores.at[name, "bus"]
        if (links["bus1"] == bus).any() and (links["bus0"] == bus).any():
            notes.append(f"{name}: Store with charging and discharging links but no split-storage tag; not counted")
    return notes


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
