"""Storage with separate power and energy (AS-02).

A `StorageUnit` sizes energy as p_nom x a fixed `max_hours`, so power and energy are one decision. Here each
storage is a `Store` (energy E) with a charger and a discharger `Link` (power P), the layout PyPSA-Eur uses for
"battery charger" and "battery discharger". The `Store` carries the technology's duration range and C-rate in
custom columns, and `_wrap_with_split_storage` adds, per store:

  * the power tie (PyPSA-Eur's): charger p_nom == discharger efficiency x discharger p_nom. P is the AC-side MW,
    the charger's p_nom: it draws P from the bus charging and delivers P to it discharging. E is store-side MWh;
  * min_hours x P <= E <= max_hours x P;
  * P <= c_rate_max x E.

`migrate_storage_unit` turns a `StorageUnit` into the same layout with min_hours = max_hours = its `max_hours`,
which leaves the LP, and so the objective, unchanged. Every limit below is a generic engineering value marked
"assumed" (SAGC-1); the costs are in the defaults pack (`supercapacitor`, `high-c-battery`, `battery`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from services.solver.runtime import _safe_log

if TYPE_CHECKING:
    import pypsa

#: Custom `Store` columns that mark a split storage and hold its limits.
STORE_COLUMNS = ("storage_technology", "min_hours", "max_hours", "c_rate_max", "charger", "discharger")


class SplitStorageError(ValueError):
    """A split storage cannot be added or a `StorageUnit` cannot be migrated as asked."""


@dataclass(frozen=True)
class StorageTech:
    label: str
    min_hours: float
    max_hours: float
    #: P <= c_rate_max x E, per hour, i.e. E/P >= 1 / c_rate_max. Every shipped default equals 1 / min_hours, so
    #: the row is implied by min_hours until a project sets a tighter chemistry limit (tested with one).
    c_rate_max: float
    #: The defaults-pack technology that prices its power and energy parts.
    pack_technology: str
    source: str = "assumed"


#: Duration ranges from research note section 4.2.1 (generic engineering, "assumed").
STORAGE_TECHNOLOGIES: dict[str, StorageTech] = {
    "supercap": StorageTech("Supercapacitor", 5 / 3600, 60 / 3600, 720.0, "supercapacitor"),
    "high_c_bess": StorageTech("High-C-rate BESS", 0.25, 1.0, 4.0, "high-c-battery"),
    "energy_bess": StorageTech("Energy BESS", 2.0, 8.0, 0.5, "battery"),
}


def _technology(key: str) -> StorageTech:
    try:
        return STORAGE_TECHNOLOGIES[key]
    except KeyError:
        raise SplitStorageError(
            f"unknown storage technology {key!r} (known: {', '.join(STORAGE_TECHNOLOGIES)})") from None


def add_split_storage(
    n: "pypsa.Network",
    name: str,
    bus: str,
    technology: str,
    *,
    p_nom: float | None = None,
    p_nom_max: float = math.inf,
    e_nom_max: float = math.inf,
    hours: tuple[float, float] | None = None,
    c_rate_max: float | None = None,
    power_capital_cost: float = 0.0,
    energy_capital_cost: float = 0.0,
    efficiency_charge: float = 0.96,
    efficiency_discharge: float = 0.96,
    standing_loss: float = 0.0,
    marginal_cost: float = 0.0,
    carrier: str | None = None,
) -> None:
    """Add Store `name` on a new bus `<name> store` with Links `<name> charger` and `<name> discharger`.

    `p_nom` None makes power extendable up to `p_nom_max` (AC-side MW); energy, up to `e_nom_max`, is always extendable and bound by `hours` (default: the
    technology's range, which `hours` may narrow but not leave). `power_capital_cost` is per AC-side MW and
    sits on the charger, as PyPSA-Eur books the inverter; `marginal_cost` is per MWh delivered to `bus`.
    """
    tech = _technology(technology)
    lo, hi = hours if hours is not None else (tech.min_hours, tech.max_hours)
    eps = 1e-12
    if not (tech.min_hours - eps <= lo <= hi <= tech.max_hours + eps):
        raise SplitStorageError(
            f"{name}: hours {lo:g}–{hi:g} h are outside the {tech.label} range "
            f"{tech.min_hours:g}–{tech.max_hours:g} h")
    c_rate = tech.c_rate_max if c_rate_max is None else float(c_rate_max)
    if not c_rate > 0:
        raise SplitStorageError(f"{name}: c_rate_max must be positive, got {c_rate!r}")
    if not (0 < efficiency_charge <= 1 and 0 < efficiency_discharge <= 1):
        raise SplitStorageError(f"{name}: efficiencies must be in (0, 1]")
    carrier = carrier or technology
    extendable = p_nom is None
    store_bus = f"{name} store"
    n.add("Bus", store_bus, carrier=carrier)
    n.add("Store", name, bus=store_bus, carrier=carrier, e_nom_extendable=True, e_cyclic=True,
          e_nom_max=e_nom_max, capital_cost=energy_capital_cost, standing_loss=standing_loss)
    _add_links(n, name, bus, store_bus, carrier, p_nom=p_nom or 0.0, extendable=extendable,
               efficiency_charge=efficiency_charge, efficiency_discharge=efficiency_discharge,
               power_capital_cost=power_capital_cost, marginal_cost=marginal_cost,
               charger={"p_nom_max": p_nom_max}, discharger={"p_nom_max": p_nom_max / efficiency_discharge})
    _tag(n, name, technology, lo, hi, c_rate)


def _add_links(n, name, bus, store_bus, carrier, *, p_nom, extendable, efficiency_charge,
               efficiency_discharge, power_capital_cost, marginal_cost, **common) -> None:
    n.add("Link", f"{name} charger", bus0=bus, bus1=store_bus, carrier=f"{carrier} charger",
          efficiency=efficiency_charge, p_nom=p_nom, p_nom_extendable=extendable,
          capital_cost=power_capital_cost, **common.get("charger", {}), **common.get("both", {}))
    # Link p_nom bounds the input (store) side; P / efficiency there delivers P at the AC bus.
    n.add("Link", f"{name} discharger", bus0=store_bus, bus1=bus, carrier=f"{carrier} discharger",
          efficiency=efficiency_discharge, p_nom=p_nom / efficiency_discharge, p_nom_extendable=extendable,
          marginal_cost=marginal_cost * efficiency_discharge,
          **common.get("discharger", {}), **common.get("both", {}))


def _tag(n, name, technology, lo, hi, c_rate) -> None:
    for col, value in zip(STORE_COLUMNS, (technology, lo, hi, c_rate, f"{name} charger", f"{name} discharger")):
        if col not in n.stores.columns:
            n.stores[col] = pd.Series(dtype=object if isinstance(value, str) else float)
        n.stores.loc[name, col] = value


#: StorageUnit attributes the migration maps exactly; any other attribute must keep its default.
_CARRIED = {"bus", "carrier", "p_nom", "p_nom_extendable", "p_nom_min", "p_nom_max", "p_min_pu", "p_max_pu",
            "capital_cost", "marginal_cost", "efficiency_store", "efficiency_dispatch", "standing_loss",
            "max_hours", "cyclic_state_of_charge", "state_of_charge_initial", "build_year", "lifetime"}
#: Attributes that do not change the LP, or that `_migration_blockers` checks by name.
_IGNORED = {"active", "sign", "type", "control", "q_set"}


def migrate_storage_unit(n: "pypsa.Network", name: str, technology: str | None = None) -> None:
    """Replace StorageUnit `name` by a Store `name` with charger and discharger Links, E/P fixed at its
    `max_hours`, so the LP and its objective are unchanged. Exact or refused: anything the layout does not map
    exactly (inflow, spill cost, marginal_cost_storage, a set SOC or dispatch, any time-varying input, the
    per-period SOC options, build_year or lifetime in a multi-period network, any other non-default attribute)
    raises `SplitStorageError` naming it, and the network is left unchanged.

    The mapping: P is the AC-side MW, so charger p_nom = P and discharger p_nom = P / efficiency_dispatch
    (the PyPSA-Eur tie); E = max_hours x P; the unit's capital_cost goes on the charger; its marginal_cost,
    per MWh dispatched, becomes the discharger's per MWh drawn (x efficiency_dispatch). No route or chat tool
    calls this yet; a caller that offers it to users needs its own review.
    """
    if name not in n.storage_units.index:
        raise SplitStorageError(f"no StorageUnit {name!r}")
    su = n.storage_units.loc[name]
    blockers = _migration_blockers(n, name, su)
    if blockers:
        raise SplitStorageError(f"{name}: cannot migrate: {'; '.join(blockers)}")

    hours = float(su["max_hours"])
    if technology is None:
        technology = next((k for k, t in STORAGE_TECHNOLOGIES.items()
                           if t.min_hours <= hours <= t.max_hours), "energy_bess")
    tech = _technology(technology)
    c_rate = max(tech.c_rate_max, 1.0 / hours)       # never tighter than the unit's own duration
    eff_d = float(su["efficiency_dispatch"])
    extendable = bool(su["p_nom_extendable"])
    carrier = su["carrier"] or "storage"
    both = {"build_year": su["build_year"], "lifetime": su["lifetime"]}

    n.remove("StorageUnit", name)
    store_bus = f"{name} store"
    n.add("Bus", store_bus, carrier=carrier)
    n.add("Store", name, bus=store_bus, carrier=carrier, e_nom=hours * float(su["p_nom"]),
          e_nom_extendable=extendable, e_nom_min=hours * float(su["p_nom_min"]),
          e_nom_max=hours * float(su["p_nom_max"]), standing_loss=su["standing_loss"],
          e_cyclic=bool(su["cyclic_state_of_charge"]), e_initial=su["state_of_charge_initial"], **both)
    _add_links(n, name, su["bus"], store_bus, carrier, p_nom=float(su["p_nom"]), extendable=extendable,
               efficiency_charge=float(su["efficiency_store"]), efficiency_discharge=eff_d,
               power_capital_cost=float(su["capital_cost"]), marginal_cost=float(su["marginal_cost"]),
               charger={"p_max_pu": -float(su["p_min_pu"]), "p_nom_min": su["p_nom_min"],
                        "p_nom_max": su["p_nom_max"]},
               discharger={"p_max_pu": float(su["p_max_pu"]), "p_nom_min": su["p_nom_min"] / eff_d,
                           "p_nom_max": su["p_nom_max"] / eff_d}, both=both)
    _tag(n, name, technology, hours, hours, c_rate)


def _migration_blockers(n, name, su) -> list[str]:
    out: list[str] = []
    for attr, frame in n.components["StorageUnit"].dynamic.items():
        if name not in frame.columns:
            continue
        col = frame[name]
        if attr in ("inflow", "spill_cost"):
            if (col.fillna(0) != 0).any():
                out.append(f"{attr} is set")
        elif attr in ("state_of_charge_set", "p_set") and col.notna().any():
            out.append(f"{attr} is set")
        elif attr in _CARRIED:
            out.append(f"{attr} varies in time")
    if not float(su["efficiency_dispatch"]) > 0:
        out.append("efficiency_dispatch is 0")
    if not float(su["max_hours"]) > 0:
        out.append("max_hours is 0")
    if not bool(su.get("active", True)):
        out.append("the unit is inactive")
    defaults = n.components["StorageUnit"].defaults["default"]
    multi_period = bool(getattr(n, "has_investment_periods", False))
    for attr, value in su.items():
        if attr in _IGNORED or attr not in defaults.index or attr.endswith("_opt"):
            continue
        if attr in _CARRIED and not (multi_period and attr in ("build_year", "lifetime")):
            continue
        default = defaults[attr]
        if not (value == default or (pd.isna(value) and pd.isna(default))):
            why = " in a multi-period network" if attr in ("build_year", "lifetime") else ""
            out.append(f"{attr} = {value!r} is not carried{why}")
    return out


def _priced(frame: pd.DataFrame, name: str) -> bool:
    def pos(col):
        v = frame.at[name, col] if col in frame.columns else None
        return v is not None and pd.notna(v) and math.isfinite(float(v)) and float(v) > 0
    return pos("capital_cost") or pos("overnight_cost")


def tied_unpriced_members(n: "pypsa.Network") -> set[tuple[str, str]]:
    """(component, name) of each unpriced Store or Link of a split storage whose size another, priced member
    of the same storage fixes through the power tie and the E/P-hour rows. Validation does not report those
    as "built free"; a storage with no priced member at all is still reported."""
    stores = getattr(n, "stores", None)
    if stores is None or "min_hours" not in stores.columns:
        return set()
    out: set[tuple[str, str]] = set()
    for name, s in stores[stores["min_hours"].notna()].iterrows():
        members = [("Store", name, stores)] + [("Link", s[k], n.links) for k in ("charger", "discharger")
                                                if s[k] in n.links.index]
        if any(_priced(frame, m) for _, m, frame in members):
            out |= {(c, m) for c, m, frame in members if not _priced(frame, m)}
    return out


def split_storage_tags(n: "pypsa.Network") -> pd.DataFrame | None:
    """The tagged Stores' `STORE_COLUMNS`, sorted by name, or None when no Store is tagged."""
    stores = getattr(n, "stores", None)
    if stores is None or "min_hours" not in stores.columns or not stores["min_hours"].notna().any():
        return None
    return stores.loc[stores["min_hours"].notna(), list(STORE_COLUMNS)].sort_index()


def size_conflicts(n: "pypsa.Network") -> list[tuple[str, str]]:
    """(store, reason) for each split storage whose fixed sizes or bounds leave no P and E that meet its
    power tie, hours and C-rate. Validation refuses these before the solve, so the LP is never infeasible
    for this reason."""
    stores = getattr(n, "stores", None)
    if stores is None or "min_hours" not in stores.columns:
        return []
    out: list[tuple[str, str]] = []
    for name, s in stores[stores["min_hours"].notna()].iterrows():
        ch, dis = s["charger"], s["discharger"]
        if ch not in n.links.index or dis not in n.links.index:
            out.append((name, "its charger or discharger link is missing"))
            continue
        lo, hi, c = float(s["min_hours"]), float(s["max_hours"]), float(s["c_rate_max"])
        lo_eff = max(lo, 1.0 / c) if math.isfinite(c) and c > 0 else lo   # E >= P / c_rate_max
        link_ext = (bool(n.links.at[ch, "p_nom_extendable"]), bool(n.links.at[dis, "p_nom_extendable"]))
        if link_ext[0] != link_ext[1]:
            out.append((name, "the charger and discharger must both be extendable or both fixed"))
            continue
        eff = float(n.links.at[dis, "efficiency"])
        if link_ext[0]:
            p_lo, p_hi = float(n.links.at[ch, "p_nom_min"]), float(n.links.at[ch, "p_nom_max"])
        else:
            p_lo = p_hi = float(n.links.at[ch, "p_nom"])
            p_dis = eff * float(n.links.at[dis, "p_nom"])
            if abs(p_lo - p_dis) > 1e-6 * max(1.0, p_lo):
                out.append((name, f"the charger is {p_lo:g} MW but the discharger delivers {p_dis:g} MW"))
                continue
        if bool(stores.at[name, "e_nom_extendable"]):
            e_lo, e_hi = float(stores.at[name, "e_nom_min"]), float(stores.at[name, "e_nom_max"])
        else:
            e_lo = e_hi = float(stores.at[name, "e_nom"])
        # Some P in [p_lo, p_hi] and E in [e_lo, e_hi] with lo_eff x P <= E <= hi x P.
        if lo_eff * p_lo > e_hi * (1 + 1e-9) or hi * p_hi < e_lo * (1 - 1e-9):
            out.append((name, f"power {p_lo:g}–{p_hi:g} MW and energy {e_lo:g}–{e_hi:g} MWh cannot meet "
                              f"{lo_eff:g}–{hi:g} h"))
    return out


def _wrap_with_split_storage(network: "pypsa.Network", user_fn, log_queue=None):
    """Chain the split-storage rows after `user_fn`. Returns `user_fn` unchanged when no Store is tagged."""
    stores = getattr(network, "stores", None)
    if stores is None or "min_hours" not in stores.columns or not stores["min_hours"].notna().any():
        return user_fn

    def emit(msg: str) -> None:
        _safe_log(log_queue, f"[STORAGE] {msg}")

    def split_storage_fn(n, snapshots):
        if user_fn is not None:
            user_fn(n, snapshots)
        _add_rows(n, emit)

    return split_storage_fn


def _nominal(n, component: str, attr: str, name: str):
    """The LP variable for an extendable asset, else its fixed value."""
    frame = n.components[component].static
    if bool(frame.at[name, f"{attr}_extendable"]):
        vname = f"{component}-{attr}"
        if vname in n.model.variables and name in n.model.variables[vname].indexes["name"]:
            return n.model.variables[vname].sel(name=name)
        return None          # extendable but not active in any period: nothing to bind
    return float(frame.at[name, attr])


def _add_rows(n, emit) -> None:
    m = n.model
    rows: dict[str, list] = {"tie": [], "min_hours": [], "max_hours": [], "c_rate": []}
    tagged = n.stores[n.stores["min_hours"].notna()]
    for name, s in tagged.iterrows():
        ch, dis = s["charger"], s["discharger"]
        if ch not in n.links.index or dis not in n.links.index:
            emit(f"{name}: charger or discharger link is missing; no rows added")
            continue
        p = _nominal(n, "Link", "p_nom", ch)
        p_dis = _nominal(n, "Link", "p_nom", dis)
        e = _nominal(n, "Store", "e_nom", name)
        if p is None or p_dis is None or e is None:
            continue
        eff = float(n.links.at[dis, "efficiency"])
        lo, hi, c = float(s["min_hours"]), float(s["max_hours"]), float(s["c_rate_max"])
        rows["tie"].append((name, p - eff * p_dis, "=="))
        rows["min_hours"].append((name, e - lo * p, ">="))
        rows["max_hours"].append((name, e - hi * p, "<="))
        if math.isfinite(c):
            rows["c_rate"].append((name, p - c * e, "<="))
    added = 0
    for kind, items in rows.items():
        for name, expr, sign in items:
            if isinstance(expr, (int, float)):
                ok = {"==": abs(expr) < 1e-6, ">=": expr >= -1e-6, "<=": expr <= 1e-6}[sign]
                if not ok:
                    emit(f"{name}: fixed sizes break the {kind.replace('_', ' ')} limit ({expr:+.4g})")
                continue
            m.add_constraints(expr, sign, 0, name=f"split_storage_{kind}-{name}")
            added += 1
    emit(f"{added} split-storage rows on {len(tagged)} store(s)")
