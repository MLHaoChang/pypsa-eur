"""
Grid-to-process ticket 08 (design §1.2, §5): an outage of the electricity supply turned into tonnes, then EUR.

``outage_loss(n, period, start, hours, units=None)`` is the feed-forward of design §5, on a SOLVED network and
with no re-solve. An outage of ``hours`` (elapsed, by ``snapshot_weightings.stores``) starts at snapshot
``start`` of ``period``. It stops ``units`` (the process units whose supply is lost; default every one).

Per unit, by its criticality tier (``models.process_unit.CRITICALITY_TIERS``):

* ``safety``: no tonnes. Its electricity draw over the outage is unserved MWh, priced at VoLL by the caller.
* ``process_critical``: down for the outage plus its restart. The restart is ``min_down_time``, which PyPSA
  counts in SNAPSHOTS, so it is converted to hours by the weights of the snapshots after the outage and
  reported as ``restart_h``.
* ``process_flexible``: down for the outage only. It catches up from inventory.
* ``deferrable``: 0 t. It shifts within the day.
* no tier: as ``process_critical``, flagged ``tier_unset:<unit>``.

``gross_t`` is what the unit would have made while down: its solved output in those snapshots.

Per product (the units' output bus), the window is the longest of its units' down spans. The rest is priced
on UNDELIVERED tonnes, since the lost-production value is the offtake's shortfall penalty (gatekeeper):

    undelivered_t = max(0, Σ_window delivery − usable inventory − output of the units still running)

* The usable inventory is the product's Stores' level at the START of the outage: PyPSA's e(t) is
  end-of-snapshot, so e(t−1), wrapped cyclically within the period, less e_min_pu(t)·e_nom_opt.
* Units sharing a tank are capped together.
* ``covered_t`` = min(Σ gross_t, usable inventory) is information. So is ``gross_t``.
* ``lost_eur`` = undelivered_t × the product's lost-production value, per t, from the one accessor
  (``process_products.shortfalls``: its shortfall Generator's value, else the bus's ``lost_production_value``;
  ``lost_load`` is the solve's capture record). Never VoLL and never 0. With no value it is None, flagged
  ``lost_production_value_missing`` (ADR-0001).

Snapshots run cyclically within the period (each period is one representative week that repeats).
Imports no router.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from models.process_unit import CRITICALITY_TIERS
from services import process_products as PP
from services.solver.process_uc import process_mask


def _period_index(n, period) -> pd.Index:
    sns = n.snapshots
    if isinstance(sns, pd.MultiIndex):
        return sns[sns.get_level_values(0) == period]
    return sns


def _weights(n, sns) -> np.ndarray:
    return n.snapshot_weightings.stores.reindex(sns).to_numpy(dtype=float)


def _span(w: np.ndarray, start: int, hours: float) -> int:
    """How many snapshots from `start` (cyclic) cover `hours` of elapsed time."""
    k, acc = 0, 0.0
    while acc < hours - 1e-9 and k < len(w):
        acc += w[(start + k) % len(w)]
        k += 1
    return k


def _output(n, unit: str, sns) -> np.ndarray:
    """The unit's product output per snapshot (bus units per hour): −p1, never negative."""
    return np.clip(-n.links_t.p1[unit].reindex(sns).fillna(0.0).to_numpy(dtype=float), 0.0, None)


def _usable_inventory(n, bus: str, sns, start: int) -> float:
    """The product's Stores' usable level at the start of the outage (bus units)."""
    total = 0.0
    for s in n.stores.index[n.stores.bus.astype(str) == bus]:
        e = n.stores_t.e[s].reindex(sns).fillna(0.0).to_numpy(dtype=float)
        opt = float(n.stores.at[s, "e_nom_opt"]) if "e_nom_opt" in n.stores.columns else float("nan")
        e_nom = opt if bool(n.stores.at[s, "e_nom_extendable"]) and math.isfinite(opt) \
            else float(n.stores.at[s, "e_nom"])
        e_min = n.get_switchable_as_dense("Store", "e_min_pu")[s].reindex(sns).to_numpy(dtype=float)
        before = (start - 1) % len(sns)              # the level and its floor at the same snapshot
        total += max(0.0, float(e[before]) - float(e_min[before]) * e_nom)
    return total


def _delivered(n, bus: str, sns) -> np.ndarray:
    loads = n.loads.index[n.loads.bus.astype(str) == bus]
    if len(loads) == 0:
        return np.zeros(len(sns))
    p = n.loads_t.p.reindex(columns=loads).reindex(sns).fillna(0.0)
    return p.sum(axis=1).to_numpy(dtype=float)


def outage_loss(n, period, start: int, hours: float, units=None, lost_load=None) -> dict:
    """The tonnes and EUR an outage of `hours` from snapshot `start` of `period` costs (module docstring)."""
    links = n.links
    process = [str(u) for u in links.index[process_mask(links)]]
    stopped = process if units is None else [u for u in process if u in set(units)]
    sns = _period_index(n, period)
    w = _weights(n, sns)
    out_span = _span(w, start, hours)
    flags: list[str] = []
    rows: list[dict] = []
    down: dict[str, tuple[int, str]] = {}            # unit → (snapshots down, product bus)
    tiers = links["criticality_tier"].astype(str).str.strip() if "criticality_tier" in links.columns \
        else pd.Series("", index=links.index)
    for u in stopped:
        tier = tiers.get(u, "")
        if tier not in CRITICALITY_TIERS:
            flags.append(f"tier_unset:{u}")
            tier = ""
        bus = str(links.at[u, "bus1"])
        restart_sn = int(links.at[u, "min_down_time"] or 0) if tier in ("process_critical", "") else 0
        restart_h = float(sum(w[(start + out_span + i) % len(w)] for i in range(restart_sn)))
        span = {"safety": 0, "deferrable": 0, "process_flexible": out_span}.get(tier, out_span + restart_sn)
        made = _output(n, u, sns)
        idx = [(start + i) % len(sns) for i in range(span)]
        tpu = PP.t_per_unit(n, bus)
        gross = float((made[idx] * w[idx]).sum()) * tpu if tpu is not None else None
        unserved = None
        if tier == "safety":
            p0 = np.clip(n.links_t.p0[u].reindex(sns).fillna(0.0).to_numpy(dtype=float), 0.0, None)
            o = [(start + i) % len(sns) for i in range(out_span)]
            unserved = float((p0[o] * w[o]).sum())
        rows.append({"unit": u, "tier": tier or None, "product": bus,
                     "rate_t_h": None if tpu is None else float(made[start]) * tpu,
                     "restart_snapshots": restart_sn, "restart_h": restart_h,
                     "down_h": float(w[idx].sum()) if idx else 0.0,
                     "gross_t": gross, "unserved_mwh": unserved})
        if span:
            down[u] = (span, bus)
    products = []
    for bus in sorted({b for _s, b in down.values()}):
        tpu = PP.t_per_unit(n, bus)
        span = max(s for s, b in down.values() if b == bus)
        idx = [(start + i) % len(sns) for i in range(span)]
        delivery = float((_delivered(n, bus, sns)[idx] * w[idx]).sum())
        running = 0.0
        for u in (x for x in process if str(links.at[x, "bus1"]) == bus):
            made = _output(n, u, sns)
            own = down.get(u, (0, bus))[0]
            running += float(sum(made[(start + i) % len(sns)] * w[(start + i) % len(sns)]
                                 for i in range(own, span)))
        inventory = _usable_inventory(n, bus, sns, start)
        gross = sum(r["gross_t"] or 0.0 for r in rows if r["product"] == bus)
        row = {"product": bus, "window_h": float(w[idx].sum()), "delivery_t": None, "inventory_t": None,
               "running_t": None, "covered_t": None, "undelivered_t": None, "value_eur_per_t": None,
               "lost_eur": None, "flag": None}
        if tpu is None:
            row["flag"] = "tonne_factor_missing"
            products.append(row)
            continue
        undelivered = max(0.0, delivery - inventory - running) * tpu
        row.update(delivery_t=delivery * tpu, inventory_t=inventory * tpu, running_t=running * tpu,
                   covered_t=min(gross, inventory * tpu), undelivered_t=undelivered)
        values = [s.value for s in PP.shortfalls(n, lost_load) if s.bus == bus]
        value = next((v for v in values if v is not None), None)
        if value is None:
            row["flag"] = "lost_production_value_missing"
        else:
            row["value_eur_per_t"] = value / tpu
            row["lost_eur"] = undelivered * value / tpu
        products.append(row)
    return {"period": period, "start": start, "hours": hours, "outage_snapshots": out_span,
            "units": rows, "products": products, "flags": flags}


# ── the failure-mode sweep's link outages (ticket 08b, FMEA class B) ────────


def _year_hours(period) -> float:
    """The period year's hours (24 × 365 or 366), never a literal 8760; 8760 on a single-period network."""
    try:
        return 24.0 * (366 if pd.Timestamp(year=int(period), month=1, day=1).is_leap_year else 365)
    except (TypeError, ValueError):
        return 8760.0


def _source_buses(n) -> set[str]:
    """Buses with a source of electricity: a Generator (not an LP slack, not a product's buy-in or shortfall), a
    StorageUnit, or a Store that is not on a product bus."""
    from services.adequacy.slack import slack_generator_mask
    from models.process_unit import product_roles

    gens = n.generators
    roles = product_roles(n)
    keep = ~slack_generator_mask(gens) if len(gens) else pd.Series([], dtype=bool)
    out = {str(gens.at[g, "bus"]) for g in gens.index[keep.to_numpy()] if str(g) not in roles}
    out |= {str(b) for b in n.storage_units.bus}
    out |= {str(n.stores.at[s, "bus"]) for s in n.stores.index if not PP.is_product_bus(n, str(n.stores.at[s, "bus"]))}
    return out


def units_cut_off(n, link: str) -> list[str]:
    """The process units a forced outage of `link` leaves with no supply, first order: no path to their bus0 from a
    source bus (`_source_buses`) over Lines, Transformers (both ways) and the remaining Links (bus0 → bus1, and back
    when p_min_pu < 0), process units excluded. A path is not a capacity: a unit still reachable counts as supplied."""
    edges: dict[str, set[str]] = {}

    def edge(a, b):
        edges.setdefault(a, set()).add(b)

    for comp in ("lines", "transformers"):
        df = getattr(n, comp)
        for name in df.index:
            a, b = str(df.at[name, "bus0"]), str(df.at[name, "bus1"])
            edge(a, b)
            edge(b, a)
    process = set(n.links.index[process_mask(n.links)])
    for name in n.links.index:
        if name == link or name in process:
            continue
        a, b = str(n.links.at[name, "bus0"]), str(n.links.at[name, "bus1"])
        edge(a, b)
        if float(n.links.at[name, "p_min_pu"]) < 0:
            edge(b, a)
    seen, todo = set(), list(_source_buses(n))
    while todo:
        b = todo.pop()
        if b in seen:
            continue
        seen.add(b)
        todo.extend(edges.get(b, ()))
    return [str(u) for u in n.links.index[process_mask(n.links)] if str(n.links.at[u, "bus0"]) not in seen]


def expected_outage_loss(n, link: str, mttr_hours: float, q: float, lost_load=None) -> dict:
    """A class-B link outage's lost production per year: the units it cuts off (`units_cut_off`), each period's
    event = the mean of `outage_loss` over every start snapshot (stores-weighted), and per year = occurrence × event,
    occurrence = the year's hours × q / MTTR. An outage longer than the period's cycle is capped at one cycle and
    flagged `outage_longer_than_period` (never wrapped twice)."""
    units = units_cut_off(n, link)
    out = {"stopped_units": units, "flags": [],
           "source_set": "Generators (no slacks, buy-ins or shortfalls), StorageUnits and Stores off product buses, "
                         "reached over Lines, Transformers and the remaining Links",
           "by_period": {}}
    if not units:
        return out
    if not (mttr_hours and math.isfinite(mttr_hours) and mttr_hours > 0):
        out["flags"].append("mttr_missing")
        return out
    periods = list(n.snapshots.get_level_values(0).unique()) if isinstance(n.snapshots, pd.MultiIndex) else [None]
    for p in periods:
        sns = _period_index(n, p)
        w = _weights(n, sns)
        span = float(w.sum())
        hours, flags = float(mttr_hours), []
        if hours > span:
            hours, flags = span, ["outage_longer_than_period"]
        acc: dict[str, dict] = {}
        for start in range(len(sns)):
            ev = outage_loss(n, p, start, hours, units=units, lost_load=lost_load)
            for row in ev["products"]:
                a = acc.setdefault(row["product"], {"t": 0.0, "eur": 0.0, "eur_known": True, "t_known": True,
                                                    "flag": row["flag"]})
                if row["undelivered_t"] is None:
                    a["t_known"] = False
                else:
                    a["t"] += row["undelivered_t"] * w[start]
                if row["lost_eur"] is None:
                    a["eur_known"] = False
                else:
                    a["eur"] += row["lost_eur"] * w[start]
        occ = _year_hours(p) * q / mttr_hours
        key = "_" if p is None else str(int(p))
        out["by_period"][key] = {
            "occurrence_per_year": occ, "outage_hours": hours, "flags": flags,
            "products": [{"product": b,
                          "undelivered_t_per_event": a["t"] / span if a["t_known"] else None,
                          "lost_t_per_year": occ * a["t"] / span if a["t_known"] else None,
                          "lost_eur_per_year": occ * a["eur"] / span if a["eur_known"] else None,
                          "flag": a["flag"]} for b, a in sorted(acc.items())]}
    return out


def lost_production_summary(n, lost_load=None) -> list[dict]:
    """A solve's own lost production per product (the shortfall accessor, `process_products.shortfalls`): tonnes on
    `snapshot_weightings.generators`, EUR on `objective` at the price the LP gave it. None and flagged when the
    dispatch is not known (never 0). Empty on a network with no product shortfall."""
    wg = n.snapshot_weightings.generators.reindex(n.snapshots).to_numpy(dtype=float)
    wo = n.snapshot_weightings.objective.reindex(n.snapshots).to_numpy(dtype=float)
    out = []
    for s in PP.shortfalls(n, lost_load):
        tpu = PP.t_per_unit(n, s.bus)
        if s.p is None:
            out.append({"product": s.bus, "source": s.source, "lost_t": None, "lost_eur": None, "flag": s.flag})
            continue
        p = s.p.to_numpy(dtype=float).clip(min=0.0)
        out.append({"product": s.bus, "source": s.source,
                    "lost_t": float((p * wg).sum()) * tpu if tpu is not None else None,
                    "lost_eur": float((p * wo * s.cost.to_numpy(dtype=float)).sum()) if s.value is not None else None,
                    "flag": s.flag or (None if tpu is not None else "tonne_factor_missing")
                    or (None if s.value is not None else "lost_production_value_missing")})
    return out
