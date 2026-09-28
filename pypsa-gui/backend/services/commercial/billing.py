"""
Site billing adapter (Edge Investment Case P2 WP2.1b).

`bill_site(n, commercial)` rates the solved network's point-of-connection meter
with `tariff_engine.rate`, exactly as the LP charged it:

  * import = Σ `import_links(cfg)` `p0` (a group contract is metered on its
    members' combined import), export = the export Link's `p0`;
  * one `RatingResult` per investment period (a flat network: key None);
  * the site clock: naive snapshots are UTC when `cfg.timezone` is set;
  * the step from the axis (gaps between sampled stretches excluded);
  * representative weeks: `represents_hours` from the objective weights, and
    the represented calendar year as the `billing_period` when a period's
    weights sum to a year (the P1 rule of `lp_bindings._demand_spec`);
  * capacity items on the PoC's `p_nom_opt` (kW) in the periods the PoC is
    ACTIVE (0 otherwise), the LP fee's rule (WP2.1a-i review #12);
  * meter history seeds the FIRST period only (the site's past; P1 rule).

`compact_frames(bill)` flattens a bill into the store's frames: per period
`"{period}:lines"` / `":quantities"` (wide, one float32 column per item, index
= interval), `":monthly"` and `":demand_lines"` — the restricted results
unpickler admits them (no Categorical, the zone kept as a string by
`services.finance.report.store_billing_frames`).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig
from services.commercial import hashing as _H
from services.commercial import lp_bindings as _lp
from services.commercial.tariff_engine import RatingResult, rate

_KW_PER_MW = 1000.0
_GAP_H = 24.0  # steps longer than this are gaps between sampled stretches, not the step


@dataclass
class SiteBill:
    per_period: dict                       # {period | None: RatingResult}
    flags: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


def _step_hours(ts: pd.DatetimeIndex) -> float | np.ndarray:
    """Per-row interval length in hours. A regular axis gives a scalar; the
    last row and the rows before a gap take the axis step (median)."""
    if len(ts) < 2:
        return 1.0
    d = np.diff(ts.asi8) / 3.6e12  # ns → h: a unit
    inside = d[(d > 0) & (d <= _GAP_H)]
    step = float(np.median(inside)) if len(inside) else 1.0
    per_row = np.append(d, step)
    per_row = np.where((per_row > 0) & (per_row <= _GAP_H), per_row, step)
    return step if np.allclose(per_row, step) else per_row


def _represented(ts: pd.DatetimeIndex, weights: np.ndarray, step) -> tuple:
    """(represents_hours or None, billing_period or None) for one period."""
    step_arr = np.broadcast_to(np.asarray(step, dtype=float), weights.shape)
    represents = None if np.allclose(weights, step_arr) else weights
    total = float(weights.sum())
    billing_period = None
    if abs(total - 8760.0) <= 0.01 * 8760.0:
        years = pd.Series(weights).groupby(np.asarray(ts.year)).sum()
        y = int(years.idxmax())
        billing_period = (f"{y}-01-01", f"{y + 1}-01-01")
    return represents, billing_period


def _solved(n, links: list[str]) -> bool:
    p0 = getattr(n.links_t, "p0", None)
    return (p0 is not None and not p0.empty and all(link in p0.columns for link in links))


def bill_site(n, commercial, *, meter_history: dict | None = None) -> SiteBill:
    cfg: CommercialConfig = _lp._parse(commercial)
    tariff = cfg.import_tariff
    links = _lp.import_links(cfg)
    needed = links + ([cfg.export_link] if cfg.export_link else [])
    if tariff is None or not _solved(n, needed):
        return SiteBill(per_period={}, flags=["not_solved"] if tariff is not None
                        else ["no_import_tariff"])
    history = meter_history if meter_history is not None else (cfg.meter_history_peaks_kw or None)
    p0 = n.links_t.p0
    imp_all = p0[links].sum(axis=1).to_numpy(dtype=float)
    exp_all = (p0[cfg.export_link].to_numpy(dtype=float) if cfg.export_link
               else np.zeros(len(n.snapshots)))
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    multi = isinstance(n.snapshots, pd.MultiIndex)
    periods = list(n.snapshots.get_level_values(0).unique()) if multi else [None]
    poc = cfg.poc_link
    pn = n.links.at[poc, "p_nom_opt"] if "p_nom_opt" in n.links.columns else np.nan
    p_nom_mw = float(pn) if np.isfinite(pn) else float(n.links.at[poc, "p_nom"])
    years_w = (n.investment_period_weightings["years"] if multi else None)

    per_period: dict = {}
    for i, p in enumerate(periods):
        sel = (n.snapshots.get_level_values(0) == p) if multi else np.ones(len(n.snapshots), bool)
        ts = pd.DatetimeIndex(n.snapshots[sel].get_level_values(-1) if multi
                              else n.snapshots[sel])
        idx = ts.tz_localize("UTC") if cfg.timezone and ts.tz is None else ts
        step = _step_hours(ts)
        represents, billing_period = _represented(ts, w_all[sel], step)
        active = True
        if multi:
            active = bool(n.get_active_assets("Link", p).reindex([poc]).fillna(False).iloc[0])
        dispatch = pd.DataFrame({"import_mw": imp_all[sel], "export_mw": exp_all[sel]},
                                index=idx)
        per_period[None if p is None else int(p)] = rate(
            dispatch, tariff, step_hours=step, timezone=cfg.timezone,
            billing_period=billing_period, represents_hours=represents,
            meter_history=history if i == 0 else None,
            capacity_kw=p_nom_mw * _KW_PER_MW if active else 0.0,
            power_factor=cfg.power_factor)

    solved_links = n.meta.get(_lp.META_LINKS) or {}
    demand_info = n.meta.get(_lp.META_DEMAND_INFO) or {}
    provenance = {
        "tariff_hash": _H.digest(tariff),
        "energy_hash": solved_links.get("energy_hash"),
        "demand_hash": demand_info.get("items_hash"),
        "period_years": ({int(p): float(years_w.loc[p]) for p in periods} if multi else None),
        "timezone": cfg.timezone,
    }
    return SiteBill(per_period=per_period, flags=[], provenance=provenance)


def compact_frames(bill: SiteBill) -> dict[str, pd.DataFrame]:
    """The store's compact billing frames (see module docstring)."""
    out: dict[str, pd.DataFrame] = {}
    for p, res in bill.per_period.items():
        key = "_" if p is None else str(p)
        lines: RatingResult = res
        if not lines.lines.empty:
            wide = lines.lines.pivot_table(index="interval", columns="tariff_item",
                                           values=["amount", "quantity_kwh"], aggfunc="sum",
                                           dropna=False)
            amount = wide["amount"].astype(np.float32)
            qty = wide["quantity_kwh"].astype(np.float32)
            amount.columns.name = qty.columns.name = None
            out[f"{key}:lines"] = amount
            out[f"{key}:quantities"] = qty
        out[f"{key}:monthly"] = lines.monthly
        dl = lines.demand_lines.copy()
        for c in ("month", "tariff_item", "period"):
            if c in dl.columns:
                dl[c] = dl[c].astype(str).astype(object)
        out[f"{key}:demand_lines"] = dl
    return out
