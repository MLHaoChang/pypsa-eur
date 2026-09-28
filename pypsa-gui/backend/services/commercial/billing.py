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
  * meter history seeds the FIRST period only (the site's past; P1 rule);
  * solver round-off below 0 on a one-way Link (≥ −1e-6 MW) is 0; a larger
    negative flow is flagged `negative_flow:<link>:<n>` and rated unknown (NaN);
  * a period whose sampled rows' weights stand for something other than a
    calendar year has no billing period the engine can state: it is `None`,
    flagged `period_not_billed:<period>:billing_period_unknown` — the results
    path never raises;
  * the solve's recorded energy / demand / tier hashes are compared with the
    current config, each with its record's recipe: `config_changed_since_solve`
    when the bill rates the old dispatch against a changed tariff.

An unsolved network (or no import tariff) gives `per_period={}` flagged
`not_solved` (`no_import_tariff`).

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
_NEG_TOL_MW = 1e-6  # solver round-off below a one-way Link's 0 bound


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
    if len(inside):
        step, bound = float(np.median(inside)), _GAP_H
    else:
        # Every step is longer than the gap bound: that IS the axis, as
        # `preflight._max_step_h` reads it (WP2.1b review #7).
        step = float(d[d > 0].min()) if (d > 0).any() else 1.0
        bound = step
    per_row = np.append(d, step)
    per_row = np.where((per_row > 0) & (per_row <= bound + 1e-9), per_row, step)
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


def _flow(p0: pd.DataFrame, link: str, flags: list[str]) -> np.ndarray:
    """A one-way Link's flow with the solver's round-off below 0 (within
    `_NEG_TOL_MW`) set to 0; anything more negative is flagged, and NaN is
    returned for those rows so the bill is unknown there, never guessed
    (WP2.1b review #3)."""
    v = p0[link].to_numpy(dtype=float).copy()
    small = (v < 0) & (v >= -_NEG_TOL_MW)
    v[small] = 0.0
    bad = v < -_NEG_TOL_MW
    if bad.any():
        flags.append(f"negative_flow:{link}:{int(bad.sum())}")
        v[bad] = np.nan
    return v


def _drift_flags(n, cfg: CommercialConfig) -> tuple[list[str], dict]:
    """Compare the solve's recorded hashes with the current config, each with
    the recipe its record was made with (WP2.1b review #5, as `cost_rows`):
    a bill of the old dispatch against a changed tariff says so — also when an
    LP-carried term (demand, convex tiers) was ADDED after the solve, since the
    dispatch never saw it (round 2 #1).

    Items the LP does not carry (fixed, per-day, capacity until WP2.1c, …) do
    not shape the dispatch: editing them changes the bill but not what it
    rates, so it is not flagged here; the bill uses their current values."""
    flags: list[str] = []
    rec = n.meta.get(_lp.META_LINKS) or {}
    info = n.meta.get(_lp.META_DEMAND_INFO) or {}
    tiers = n.meta.get(_lp.META_TIERS) or {}
    solve = {"energy_hash": rec.get("energy_hash"), "energy_hash_version": _H.version_of(rec),
             "demand_hash": info.get("items_hash"), "demand_hash_version": _H.version_of(info)}
    changed = False
    state = _lp.energy_record_state(n, cfg, rec)
    if state == "config":
        changed = True
    elif state == "recipe":
        flags.append("energy_recipe_changed")  # re-solve: windowed tiers bound since
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if _lp._is_demand(i) and _lp._lp_reason(i) is None]
    if info:
        if sorted(info.get("items", [])) != sorted(i.id for i in items) or (
                info.get("items_hash") is not None and items
                and info["items_hash"] != _lp.demand_hash(n, cfg, items, _H.version_of(info))):
            if _lp.demand_only_newly_bound(n, info, items, cfg):
                # The config is unchanged; WP2.1c-i's recipe binds more of it
                # (as `cost_rows`): re-solve.
                flags.append("demand_recipe_changed")
            else:
                changed = True
    elif items:
        if _lp.demand_all_newly_bound(rec, items):
            flags.append("demand_recipe_changed")  # the old recipe bound none of them
        else:
            changed = True  # demand terms added after the solve
    tier_rec = next(iter(tiers.values()), None)
    solve["tier_hash"] = (tier_rec or {}).get("items_hash")
    solve["tier_hash_version"] = _H.version_of(tier_rec)
    recipe = _lp.energy_recipe_of(rec)
    if tier_rec is not None:
        if tier_rec.get("items_hash") is not None and tier_rec["items_hash"] != \
                _lp.tier_items_hash(cfg, _H.version_of(tier_rec), recipe=recipe):
            changed = True
    elif _lp.tier_items_hash(cfg, recipe=recipe) is not None:
        changed = True  # convex tiers added after the solve
    if not rec:
        flags.append("solve_provenance_unknown")
    if changed:
        flags.append("config_changed_since_solve")
    return flags, solve


def bill_site(n, commercial, *, meter_history: dict | None = None) -> SiteBill:
    cfg: CommercialConfig = _lp._parse(commercial)
    tariff = cfg.import_tariff
    links = _lp.import_links(cfg)
    needed = links + ([cfg.export_link] if cfg.export_link else [])
    if tariff is None or not _solved(n, needed):
        return SiteBill(per_period={}, flags=["not_solved"] if tariff is not None
                        else ["no_import_tariff"])
    flags: list[str] = []
    history = meter_history if meter_history is not None else (cfg.meter_history_peaks_kw or None)
    p0 = n.links_t.p0
    imp_all = np.sum([_flow(p0, link, flags) for link in links], axis=0)
    exp_all = (_flow(p0, cfg.export_link, flags) if cfg.export_link
               else np.zeros(len(n.snapshots)))
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    multi = isinstance(n.snapshots, pd.MultiIndex)
    periods = list(n.snapshots.get_level_values(0).unique()) if multi else [None]
    poc = cfg.poc_link
    pn = n.links.at[poc, "p_nom_opt"] if "p_nom_opt" in n.links.columns else np.nan
    p_nom_mw = float(pn) if np.isfinite(pn) else float(n.links.at[poc, "p_nom"])
    years_w = (n.investment_period_weightings["years"] if multi else None)

    per_period: dict = {}
    calendar: dict = {}
    errors: dict = {}
    for i, p in enumerate(periods):
        key = None if p is None else int(p)
        sel = (n.snapshots.get_level_values(0) == p) if multi else np.ones(len(n.snapshots), bool)
        ts = pd.DatetimeIndex(n.snapshots[sel].get_level_values(-1) if multi
                              else n.snapshots[sel])
        idx = ts.tz_localize("UTC") if cfg.timezone and ts.tz is None else ts
        step = _step_hours(ts)
        represents, billing_period = _represented(ts, w_all[sel], step)
        calendar[key] = billing_period
        active = True
        if multi:
            active = bool(n.get_active_assets("Link", p).reindex([poc]).fillna(False).iloc[0])
        dispatch = pd.DataFrame({"import_mw": imp_all[sel], "export_mw": exp_all[sel]},
                                index=idx)
        try:
            per_period[key] = rate(
                dispatch, tariff, step_hours=step, timezone=cfg.timezone,
                billing_period=billing_period, represents_hours=represents,
                meter_history=history if i == 0 else None,
                capacity_kw=p_nom_mw * _KW_PER_MW if active else 0.0,
                power_factor=cfg.power_factor)
        except ValueError as exc:
            # Sampled stretches whose weights stand for something other than a
            # calendar year have no billing period the engine can state
            # (review #2); the results path never raises — the period is
            # unknown and says why.
            per_period[key] = None
            flags.append(f"period_not_billed:{'_' if key is None else key}:"
                         f"{'billing_period_unknown' if 'billing_period' in str(exc) else 'invalid_dispatch'}")
            errors[key] = str(exc)[:300]  # what the engine refused (round 2 #3)

    drift, solve = _drift_flags(n, cfg)
    flags += drift
    provenance = {
        "tariff_hash": _H.digest(tariff), "tariff_hash_version": _H.HASH_VERSION,
        **solve,
        "period_years": ({int(p): float(years_w.loc[p]) for p in periods} if multi else None),
        # The calendar each period is billed on: with `set_investment_periods`
        # reusing one weather year, every period bills that year's calendar
        # (review #8), as the LP's demand months do.
        "billing_calendar": calendar,
        # Capacity items bill the PoC Link's size, as the LP fee does — also
        # for a group contract, whose DSO may bill `group_cap_mw` (review #8).
        "capacity_basis": {"link": poc, "p_nom_mw": p_nom_mw},
        "timezone": cfg.timezone,
        "period_errors": errors,
    }
    return SiteBill(per_period=per_period, flags=flags, provenance=provenance)


def compact_frames(bill: SiteBill) -> dict[str, pd.DataFrame]:
    """The store's compact billing frames (see module docstring).

    The wide `lines` / `quantities` are float32 for size: they are for display
    and inspection. Totals come from the bill's `per_item` / `monthly` (float64)
    — a float32 sum over a 15-min year can miss the cent (WP2.1b review #6)."""
    out: dict[str, pd.DataFrame] = {}
    for p, res in bill.per_period.items():
        if res is None:
            continue  # a period not billed (flagged on the bill)
        key = "_" if p is None else str(p)
        lines: RatingResult = res
        if not lines.lines.empty:
            # (interval, item) is unique; `min_count=1` keeps an unrated or
            # unknown cell NaN — never a confident 0 (review #1, ADR-0001).
            wide = (lines.lines.groupby(["interval", "tariff_item"], observed=True)
                    [["amount", "quantity_kwh"]].sum(min_count=1).unstack("tariff_item"))
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
