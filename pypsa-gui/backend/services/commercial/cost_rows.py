"""
The commercial terms a solve charged, as cost rows (Edge Investment Case spec
§5.1; P1 WP1.3 / WP1.4a).

The commercial layer is transient: the PoC prices and the connection agreement
are applied for the solve and undone, so their money is NOT in
`n.statistics()`. `commercial_cost_terms` recomputes it from what the solve
committed (`links_t["ic_energy_price"]`, `n.meta["ic_poc_links"]`,
`n.meta["ic_connection_fee"]`, `n.meta["ic_connection_fixed_fee"]`) and the
dispatch. The rows are therefore identical after a reload, and every total
built on `n.statistics()` adds the same items:

  * `cost_breakdown` feeds each item through its `_accumulate` as the
    component "Commercial", so `Σ by_component == capex/opex`, and the same
    holds per period;
  * `cost_totals.horizon_system_cost` adds the same sum, so the solve-queue
    and status-bar total keep agreeing with `/results/cost_breakdown`.

Items carry the period (None on a flat axis) and are NOT years-weighted; each
caller applies its own `years(period)`, exactly as for the statistics rows.
A fixed connection fee (on fixed capacity) is not an LP term: it is reported
under `network_capacity_fixed` with `included_in_total: False`. A term the
config names but the solve did not commit is None plus a flag (ADR-0001).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import pandas as pd

from services.commercial import connection as _conn
from services.commercial import lp_bindings as _lp


def _energy_by_period(n, link: str, prices: pd.DataFrame) -> dict | None:
    if link not in prices.columns or link not in n.links_t.p0.columns:
        return None
    add = prices[link]
    if add.isna().any():
        return None
    amount = n.snapshot_weightings.objective * n.links_t.p0[link] * add
    if isinstance(n.snapshots, pd.MultiIndex):
        return {p: float(v) for p, v in amount.groupby(level=0).sum().items()}
    return {None: float(amount.sum())}


def commercial_cost_terms(n, commercial: dict | None) -> dict:
    """{"items": [(label, period, capex, opex)], "block": {...}, "flags": [...]}.

    `block` is the `cost_breakdown["commercial"]` payload (horizon totals per
    label, before the caller's years weighting on a multi-period axis).
    """
    flags: list[str] = []
    items: list[tuple] = []
    block: dict = {"included_in_total": True}

    # Energy import / export (WP1.3).
    rows = _lp.energy_cost_rows(n, commercial)
    if rows is not None:
        flags += [f for f in rows.get("flags", []) if f not in flags]
        solved = n.meta.get(_lp.META_LINKS) or {}
        prices = _lp._frame(n, _lp.ENERGY_PRICE_ATTR)
        for label, role in (("energy_import", "import"), ("energy_export", "export")):
            link = solved.get(role)
            block[label] = rows.get(label)
            if link is None or rows.get(label) is None:
                continue
            per = _energy_by_period(n, link, prices)
            for p, v in (per or {}).items():
                if v:
                    items.append((label, p, 0.0, v))
        if "simultaneous_snapshots" in rows:
            block["simultaneous_snapshots"] = rows["simultaneous_snapshots"]

    # Peak-demand charges (WP1.5a): €/MW × solved monthly peak, per period.
    peaks = n.meta.get(_lp.META_DEMAND)
    if peaks:
        total = 0.0
        for v in peaks.values():
            amount = float(v["eur_per_mw"]) * float(v["peak_mw"])
            items.append(("demand_charge", v.get("inv_period"), 0.0, amount))
            total += amount
        block["demand_charge"] = total

    # Connection capacity fee in the LP (WP1.4a).
    fee = n.meta.get(_conn.META_FEE)
    agreement = None
    if commercial:
        try:
            agreement = _lp._parse(commercial).connection
        except Exception:  # noqa: BLE001
            agreement = None
    wants_fee = (agreement is not None and agreement.kind == "firm"
                 and agreement.capacity_fee is not None)
    if fee:
        link = fee.get("link")
        if link in n.links.index and "p_nom_opt" in n.links.columns:
            p_nom_opt = float(n.links.at[link, "p_nom_opt"])
            total = 0.0
            for key, eur_per_mw in fee.get("eur_per_mw_by_period", {}).items():
                period = None if key == "_" else int(key)
                amount = float(eur_per_mw) * p_nom_opt
                items.append(("network_capacity", period, amount, 0.0))
                total += amount
            block["network_capacity"] = total
        else:
            block["network_capacity"] = None
            flags.append("network_capacity_not_established")
        if not wants_fee:
            flags.append("config_changed_since_solve" if commercial else
                         "config_cleared_since_solve")
    elif wants_fee:
        block["network_capacity"] = None
        flags.append("network_capacity_not_established")

    # Fixed connection fee — reported, NOT in the reconciled total (§5.5).
    fixed = n.meta.get(_conn.META_FIXED_FEE)
    if fixed:
        block["network_capacity_fixed"] = {
            "eur": float(fixed["eur"]), "kind": fixed.get("kind"),
            "included_in_total": False, "flags": ["fixed_charge_not_in_lp"]}

    block["flags"] = list(dict.fromkeys(flags))
    if len(block) == 2 and not items:  # only included_in_total + empty flags
        return {"items": [], "block": None, "flags": []}
    return {"items": items, "block": block, "flags": block["flags"]}
