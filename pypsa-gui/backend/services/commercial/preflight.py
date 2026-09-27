"""
Preflight findings for the commercial layer (Edge Investment Case P1 WP1.8).

`commercial_findings(n, commercial)` returns `(severity, code, component_class,
name, message)` tuples; `validation_service._check_commercial` turns them into
`Issue`s. Nothing here mutates the network. The bindings are evaluated the way
the solve would evaluate them, so a preflight error is the solve's own refusal,
stated before any solver time is spent.

  * `commercial.binding_invalid` (error): the config cannot bind: a PoC, export
    or group Link that is missing or two-way, a bare Library tariff id, an
    export item with no export Link, an unrated snapshot, a missing or stale
    export price or envelope.
  * `commercial.arbitrage_loop` (warning): in some snapshots exporting pays
    more than importing costs, so the LP can lower its cost by circulating
    power through the grid (both PoC Links at once).
  * `commercial.demand_resolution` (warning): a demand item's interval is finer
    than the snapshot step, so the LP and the bill measure a peak the axis
    cannot resolve (spec §5.5 gap cause `resolution`).
  * `commercial.demand_partial_months` (warning): months charged a full
    demand charge against part of a month's operation (spec §5.2).
  * `commercial.tariff_out_of_validity` (warning): modelled dates outside the
    tariff's `valid_from` / `valid_to`.

The PPA and DR-contract double-count checks need P2's contracts (plan WP1.8
deviation).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.commercial import lp_bindings as _lp

_SETTLE_H = {"15min": 0.25, "30min": 0.5, "h": 1.0}


def _link_cost(n, link: str) -> np.ndarray:
    mc = n.links_t.marginal_cost
    if link in mc.columns:
        return mc[link].to_numpy(dtype=float)
    return np.full(len(n.snapshots), float(n.links.at[link, "marginal_cost"]))


def commercial_findings(n, commercial) -> list[tuple[str, str, str, str, str]]:
    if not commercial:
        return []
    out: list[tuple[str, str, str, str, str]] = []
    try:
        cfg = _lp._parse(commercial)
        _lp.validate_for_network(n, cfg)
        adders, _items, _nil, _notes = _lp._adders(n, cfg)
        price = _lp._export_price(n, cfg) if cfg.export_price_ref is not None else None
        if cfg.connection is not None:
            from services.commercial import connection as conn

            conn._validate(n, cfg.connection, cfg.poc_link, cfg.export_link, "full", False)
    except Exception as exc:  # noqa: BLE001 — every refusal the solve would make
        return [("error", "commercial.binding_invalid", "Link",
                 getattr(commercial, "poc_link", None) or (commercial or {}).get("poc_link", ""),
                 f"The commercial config cannot bind to this network: {exc}")]

    if cfg.export_link is not None:
        imp = _link_cost(n, cfg.poc_link) + adders["import"]
        exp = _link_cost(n, cfg.export_link) + adders["export"]
        if price is not None:
            exp = exp - price
        loops = int(((imp + exp) < -1e-9).sum())
        if loops:
            out.append(("warning", "commercial.arbitrage_loop", "Link", cfg.export_link,
                        f"In {loops} snapshot(s) exporting through {cfg.export_link!r} pays more "
                        f"than importing through {cfg.poc_link!r} costs: the LP can lower its "
                        "cost by importing and exporting at once. Check the export price and "
                        "the import tariff, or add a cost on the export Link."))

    step_h = None
    ts = n.snapshots.get_level_values(-1) if isinstance(n.snapshots, pd.MultiIndex) else n.snapshots
    if len(ts) > 1:
        step_h = float(pd.Series(pd.DatetimeIndex(ts)[1:] - pd.DatetimeIndex(ts)[:-1])
                       .median() / pd.Timedelta(hours=1))
    items = cfg.import_tariff.items if cfg.import_tariff is not None else []
    for item in items:
        if _lp._is_demand(item) and step_h is not None and \
                step_h > _SETTLE_H[item.settlement] + 1e-9:
            out.append(("warning", "commercial.demand_resolution", "", item.id,
                        f"Demand item {item.id!r} is measured on {item.settlement} intervals but "
                        f"the snapshots are {step_h:g} h apart: the peak within an interval is "
                        "not resolved, so the modelled demand charge can understate the bill."))
    spec, _ids, _missing, _dnotes = _lp._demand_spec(n, cfg)
    partial = (spec or {}).get("info", {}).get("partial_months", [])
    if partial:
        out.append(("warning", "commercial.demand_partial_months", "", "",
                    f"Months {', '.join(partial)} are charged a full monthly demand charge "
                    "against only part of the month's operation (spec §5.2), which over-weights "
                    "peak shaving against energy. Model whole months to avoid it."))

    tariff = cfg.import_tariff
    if tariff is not None and len(ts):
        local = _lp._local_clock(n.snapshots, cfg.timezone)
        first, last = local.min().date(), local.max().date()
        if first < tariff.valid_from or (tariff.valid_to is not None and last > tariff.valid_to):
            out.append(("warning", "commercial.tariff_out_of_validity", "", tariff.id,
                        f"The modelled dates {first}..{last} fall outside tariff {tariff.id!r}'s "
                        f"validity {tariff.valid_from}..{tariff.valid_to or 'open'}."))
    return out
