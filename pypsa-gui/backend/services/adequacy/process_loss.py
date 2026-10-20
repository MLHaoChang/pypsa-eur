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
* ``lost_eur`` = undelivered_t × the product's lost-production value (its shortfall Generator's marginal
  cost, ``process_products.lost_production_value``, per t). Never VoLL and never 0. With no value it is None,
  flagged ``lost_production_value_missing`` (ADR-0001).

Snapshots run cyclically within the period (each period is one representative week that repeats).
Imports no router.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from models.process_unit import CRITICALITY_TIERS, product_role_generators
from services import process_products as PP
from services.solver.process_uc import process_mask


def _period_index(n, period) -> pd.Index:
    sns = n.snapshots
    if isinstance(sns, pd.MultiIndex):
        return sns[sns.get_level_values(0) == period]
    return sns


def _weights(n, sns) -> np.ndarray:
    return n.snapshot_weightings.stores.reindex(sns).to_numpy(dtype=float)


def _t_per_unit(n, bus: str) -> float | None:
    """Tonnes per bus unit: 1 on a bus in t, 1 / mwh_per_t on a bus in MWh, None when not known."""
    if PP.bus_unit(n, bus) == "t":
        return 1.0
    f = PP._positive(PP._cell(n.buses, bus, "mwh_per_t"))
    return None if f is None else 1.0 / f


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
        total += max(0.0, float(e[(start - 1) % len(sns)]) - float(e_min[start]) * e_nom)
    return total


def _delivery(n, bus: str, sns) -> np.ndarray:
    loads = n.loads.index[n.loads.bus.astype(str) == bus]
    if len(loads) == 0:
        return np.zeros(len(sns))
    p = n.loads_t.p.reindex(columns=loads).reindex(sns).fillna(0.0)
    return p.sum(axis=1).to_numpy(dtype=float)


def outage_loss(n, period, start: int, hours: float, units=None) -> dict:
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
        tpu = _t_per_unit(n, bus)
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
        tpu = _t_per_unit(n, bus)
        span = max(s for s, b in down.values() if b == bus)
        idx = [(start + i) % len(sns) for i in range(span)]
        delivery = float((_delivery(n, bus, sns)[idx] * w[idx]).sum())
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
        values = [PP.lost_production_value(n, g) for g in product_role_generators(n, "shortfall")
                  if str(n.generators.at[g, "bus"]) == bus]
        value = next((v for v in values if v is not None), None)
        if value is None:
            row["flag"] = "lost_production_value_missing"
        else:
            row["value_eur_per_t"] = value / tpu
            row["lost_eur"] = undelivered * value / tpu
        products.append(row)
    return {"period": period, "start": start, "hours": hours, "outage_snapshots": out_span,
            "units": rows, "products": products, "flags": flags}
