"""`/results/reserves`: reserve products on backup storage (DC grid readiness 06).

`reserve_view(n, cfg, *, solved)` is what the storage view and the DC backup
line read: per storage that may carry a reserve, its backup floor (kept for
outages), its energy capacity and state of charge, and per product the MW it
committed in each snapshot; the capacity-payment lines (`billing.reserve_lines`)
and what they leave out. A solve that is missing or read other inputs gives
`committed` None and the flag `reserve_not_solved`, never zeros.

None (the route's 204) without reserve products. Pure service: imports neither
routers nor `solver_service`.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd


def _series(values) -> list[float | None]:
    return [
        None if not math.isfinite(float(v)) else float(v)
        for v in np.asarray(values, dtype=float)
    ]


def _e_cap(n, s: str) -> float | None:
    df = n.storage_units
    p = (
        df.at[s, "p_nom_opt"]
        if bool(df.at[s, "p_nom_extendable"]) and "p_nom_opt" in df.columns
        else df.at[s, "p_nom"]
    )
    v = float(p) * float(df.at[s, "max_hours"])
    return v if math.isfinite(v) else None


def _headroom_h(storage_dir) -> float | None:
    """The saved DC spec's ``reserve_headroom_h`` (None without one)."""
    if storage_dir is None:
        return None
    from project_templates import eh_templates as tpl
    from services.adequacy import dc_backup

    try:
        _tid, spec, _planning = dc_backup.project_spec(storage_dir, tpl)
    except dc_backup.DcBackupError:
        return None
    return float(spec.backup.reserve_headroom_h)


def _money(n, cfg) -> tuple[str | None, int | None]:
    """(currency, money year) the revenue lines are in: the project's stated currency,
    else the one currency the bound prices were recorded in (None when unknown); the
    investment case's base year for them (the first period's year, or a flat
    horizon's weight-majority year; `finance_case`), None when it cannot be read."""
    from services.commercial.binding import project_currency
    from services.results.finance_case import _period_year_and_hours
    from services.solver import reserve_products as RP

    currency = project_currency(getattr(cfg, "finance", None))
    if currency is None:
        bound = {
            v
            for v in (
                (getattr(n, "meta", None) or {}).get(RP.CURRENCY_META) or {}
            ).values()
            if v
        }
        currency = bound.pop() if len(bound) == 1 else None
    try:
        multi = isinstance(n.snapshots, pd.MultiIndex)
        first = n.investment_periods[0] if multi and len(n.investment_periods) else None
        year = _period_year_and_hours(n, first)[0]
    except Exception:  # noqa: BLE001 — a header never fails the view
        year = None
    return currency, year


def reserve_view(
    n, cfg, *, solved: bool, backup=None, storage_dir=None
) -> dict[str, Any] | None:
    from services.results.billing import RESERVE_NOT_MODELLED, reserve_lines
    from services.solver import reserve_products as RP

    products = RP.products_of(cfg)
    if not products:
        return None
    floors, strings = RP.project_backup(n) if backup is None else backup
    used = sorted({s for p in products for s in (p.storages or floors)})
    if solved:
        frame, flags = RP.solved(n, lambda: RP.inputs_digest(products, floors))
    else:
        frame, flags = None, ["reserve_not_solved"]
    soc = n.storage_units_t.get("state_of_charge") if solved else None
    commercial = getattr(cfg, "commercial", None)
    site = (
        commercial.get("site_party")
        if isinstance(commercial, dict)
        else getattr(commercial, "site_party", None)
    ) or "site"
    lines, _ = reserve_lines(n, cfg, site, backup=(floors, strings))
    storages = []
    for s in used:
        here = s in n.storage_units.index
        held = (
            RP.reserved_energy(n, frame, products, s)
            if frame is not None and here
            else None
        )
        storages.append(
            {
                "name": s,
                "string": s in strings,
                "floor_mwh": floors.get(s),
                "e_cap_mwh": _e_cap(n, s) if here else None,
                "soc_mwh": _series(soc[s])
                if soc is not None and s in soc.columns
                else None,
                "reserved_up_mwh": None if held is None else _series(held[0]),
                "reserved_down_mwh": None if held is None else _series(held[1]),
                "committed": None
                if frame is None
                else {
                    p.id: _series(frame[f"{s}{RP.SEP}{p.id}"])
                    if f"{s}{RP.SEP}{p.id}" in frame.columns
                    else [0.0] * len(n.snapshots)
                    for p in products
                    if (not p.storages or s in p.storages)
                },
            }
        )
    currency, money_year = _money(n, cfg)
    sns = n.snapshots
    times = sns.get_level_values(-1) if isinstance(sns, pd.MultiIndex) else sns
    return {
        "products": [
            {
                "id": p.id,
                "kind": p.kind,
                "direction": p.direction,
                "sustain_h": p.sustain_h,
                "counterparty": p.counterparty,
                "price_source": p.capacity_price.source,
            }
            for p in products
        ],
        "snapshots": [str(t) for t in times],
        "storages": storages,
        "lines": [asdict(ln) for ln in lines],
        "flags": sorted(set(flags)),
        "headroom_h_per_string": _headroom_h(storage_dir),
        "currency": currency,
        "money_year": money_year,
        "not_modelled": RESERVE_NOT_MODELLED,
    }
