"""
Commercial terms → the dispatch-grade LP (Edge Investment Case spec §5, §5.1).

P1 WP1.3: point-of-connection energy prices.

`materialise_poc_prices(n, commercial)` writes the energy tariff onto the PoC
Links as a time-varying `marginal_cost`, BEFORE the linopy model is built:

  * import Link (`poc_link`): static `marginal_cost` + Σ energy items measured on
    `import` (and `net` cost items), in €/MWh (per_kwh × 1000); a `revenue`
    item enters with a minus sign;
  * export Link (`export_link`, a `poc→grid` Link): static `marginal_cost`
    + Σ export items (and `net` revenue items) − the export price, which is
    read from the network's own `links_t["ic_export_price"]` column (the
    config route resolves `export_price_ref` from the Library and writes it
    there — see `routers/simulation.update_solver_config`).

The columns are recomputed from the static cost on every call (idempotent) and
PERSIST with the network: the reported energy cost is recomputed from them
after a reload. The spec §5.1 rule "rows from persisted data, never transient
stashes" applies.

`net` items are split by direction (a cost item on import, a revenue item on
export). The tariff engine nets per interval, floored at 0; the split is exact
whenever the site does not import and export in the same interval, and an
upper bound on cost otherwise. It is noted in the returned terms.

Tariff periods are evaluated on the SITE clock. When `commercial["timezone"]`
is set, naive snapshots are read as UTC (the PyPSA-Eur convention) and
converted. When it is None, the snapshots already are the site clock, the
same rule as `tariff_engine.rate(timezone=None)`.

Items the LP does not carry here are listed in `not_in_lp` with a reason:
fixed items (never in the LP, §5.5), tiers (WP1.5c), demand and capacity
(WP1.4/WP1.5).

`_wrap_with_commercial_bindings` is the `extra_functionality` hook for the
LP-level terms (peaks, ratchets, tiers, group caps) that later work packages
add. In WP1.3 it only chains.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig, Tariff, TariffItem
from services.commercial.tariff_engine import _rates

EXPORT_PRICE_ATTR = "ic_export_price"
_KWH_PER_MWH = 1000.0


class CommercialBindingError(ValueError):
    """The commercial config cannot be bound to this network as it stands."""


def _local_clock(snapshots: pd.Index, timezone: str | None) -> pd.DatetimeIndex:
    ts = snapshots.get_level_values(-1) if isinstance(snapshots, pd.MultiIndex) else snapshots
    idx = pd.DatetimeIndex(ts)
    if timezone is None:
        return idx
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(timezone)


def _lp_reason(item: TariffItem) -> str | None:
    """Why `item` is not a per-interval energy price on a PoC Link, or None."""
    if item.kind == "fixed":
        return "fixed_not_in_lp"
    if item.kind == "demand" or item.measured_on == "peak_import":
        return "demand_WP1.5a"
    if item.kind == "capacity":
        return "capacity_WP1.4"
    if item.tiers:
        return "tiers_WP1.5c"
    if item.unit != "per_kwh":
        return f"unit_{item.unit}_not_energy"
    return None


def _side(item: TariffItem) -> str:
    if item.measured_on == "import":
        return "import"
    if item.measured_on == "export":
        return "export"
    return "import" if item.direction == "cost" else "export"  # net


def _require_link(n, name: str | None, what: str) -> None:
    if name is None or name not in n.links.index:
        raise CommercialBindingError(f"commercial.{what} {name!r} is not a Link in this network")


def validate_for_network(n, cfg: CommercialConfig) -> None:
    """Refuse a commercial config this network cannot bind (the config route
    runs this so the user hears about it at edit time, not at solve time)."""
    _require_link(n, cfg.poc_link, "poc_link")
    if cfg.export_link is not None:
        _require_link(n, cfg.export_link, "export_link")
    if cfg.import_tariff_id is not None and cfg.import_tariff is None:
        raise CommercialBindingError(
            "import_tariff_id names a Library tariff, which arrives in P2 (WP2.4); "
            "carry the tariff inline as import_tariff")


def write_export_price(n, link: str, series: pd.Series) -> int:
    """Put a resolved Library price series on `link`'s `ic_export_price`
    column, aligned to the snapshots. Returns the number of snapshots the
    series does not cover (NaN there; refused at solve time)."""
    aligned = align_to_snapshots(series, n.snapshots)
    store = n.links_t.get(EXPORT_PRICE_ATTR) if hasattr(n.links_t, "get") else None
    if store is None or not store.index.equals(n.snapshots):
        store = pd.DataFrame(index=n.snapshots)
    store = store.copy()
    store[link] = aligned.to_numpy()
    n.links_t[EXPORT_PRICE_ATTR] = store
    return int(aligned.isna().sum())


def materialise_poc_prices(n, commercial: dict | CommercialConfig | None,
                           *, log=None) -> dict | None:
    """Write PoC energy prices onto the network (see module docstring).

    Returns the terms applied (for `last_commercial_terms`), or None when
    there is no commercial config. Raises `CommercialBindingError` when the
    config names links that do not exist, or an export price is not
    established for every snapshot (a missing price is refused, never 0).
    """
    if not commercial:
        return None
    cfg = (commercial if isinstance(commercial, CommercialConfig)
           else CommercialConfig.model_validate(commercial))
    validate_for_network(n, cfg)

    local = _local_clock(n.snapshots, cfg.timezone)
    zero = np.zeros(len(n.snapshots))
    adders = {"import": zero.copy(), "export": zero.copy()}
    energy_items: list[str] = []
    not_in_lp: dict[str, str] = {}
    notes: list[str] = []
    tariff: Tariff | None = cfg.import_tariff
    for item in (tariff.items if tariff is not None else []):
        reason = _lp_reason(item)
        if reason is not None:
            not_in_lp[item.id] = reason
            continue
        side = _side(item)
        if side == "export" and cfg.export_link is None:
            raise CommercialBindingError(
                f"tariff item {item.id!r} is measured on export but commercial.export_link "
                "is not set")
        r = _rates(item, local)
        if np.isnan(r).any():
            raise CommercialBindingError(
                f"tariff item {item.id!r} has no period covering {int(np.isnan(r).sum())} "
                "snapshot(s); add a catch-all period")
        sign = 1.0 if item.direction == "cost" else -1.0
        adders[side] += sign * r * _KWH_PER_MWH
        energy_items.append(item.id)
        if item.measured_on == "net" and "net_split_by_direction" not in notes:
            notes.append("net_split_by_direction")

    has_export_price = cfg.export_price_ref is not None
    if has_export_price:
        if cfg.export_link is None:
            raise CommercialBindingError("commercial.export_price_ref needs commercial.export_link")
        store = n.links_t.get(EXPORT_PRICE_ATTR) if hasattr(n.links_t, "get") else None
        col = None if store is None or cfg.export_link not in store.columns \
            else store[cfg.export_link].reindex(n.snapshots)
        if col is None or col.isna().any():
            missing = len(n.snapshots) if col is None else int(col.isna().sum())
            raise CommercialBindingError(
                f"export price is not established for {missing} snapshot(s) of "
                f"{cfg.export_link!r}; re-apply the commercial config to materialise the "
                "Library series on the current snapshot axis")
        adders["export"] -= col.to_numpy(dtype=float)

    mc = n.links_t.marginal_cost
    for side, link in (("import", cfg.poc_link), ("export", cfg.export_link)):
        if link is None:
            continue
        if side == "export" and not has_export_price and not adders["export"].any():
            continue
        static = float(n.links.at[link, "marginal_cost"])
        mc[link] = static + adders[side]
    if log is not None:
        log(f"[COMMERCIAL] PoC prices materialised on {cfg.poc_link!r}"
            + (f" and {cfg.export_link!r}" if cfg.export_link else "")
            + f" ({len(energy_items)} energy item(s); {len(not_in_lp)} left to later bindings)")
    return {"poc_link": cfg.poc_link, "export_link": cfg.export_link,
            "energy_items": energy_items, "not_in_lp": not_in_lp, "notes": notes,
            "timezone": cfg.timezone,
            "export_price_ref": (cfg.export_price_ref.model_dump(mode="json")
                                 if cfg.export_price_ref is not None else None)}


def align_to_snapshots(series: pd.Series, snapshots: pd.Index) -> pd.Series:
    """A Library series on the network's snapshot axis.

    Tz-aware series are converted to UTC-naive (the snapshot convention);
    naive series are taken to be on the snapshot clock. A coarser series is
    held across the finer snapshots inside each of its steps; anything not
    covered is NaN (refused at solve time, never filled with 0).
    """
    s = series.copy()
    if isinstance(s.index, pd.DatetimeIndex) and s.index.tz is not None:
        s.index = s.index.tz_convert("UTC").tz_localize(None)
    s = s.sort_index()
    target = pd.DatetimeIndex(snapshots.get_level_values(-1)
                              if isinstance(snapshots, pd.MultiIndex) else snapshots)
    pos = s.index.get_indexer(target)
    if len(s) > 1 and (pos < 0).any():
        step = pd.Series(s.index[1:] - s.index[:-1]).median()
        held = s.index.get_indexer(target, method="ffill")
        ok = held >= 0
        within = np.zeros(len(target), dtype=bool)
        within[ok] = (target[ok] - s.index[held[ok]]) < step
        pos = np.where(pos >= 0, pos, np.where(ok & within, held, -1))
    vals = np.where(pos >= 0, s.to_numpy(dtype=float)[np.clip(pos, 0, None)], np.nan)
    return pd.Series(vals, index=snapshots, name=series.name)


def _wrap_with_commercial_bindings(network, user_fn, cfg, log_queue=None):
    """`extra_functionality` hook for LP-level commercial terms (spec §5.1).

    Same closure/chaining shape as `_wrap_with_capex_budget`. WP1.3 adds no
    LP rows (energy prices are `marginal_cost`), so this returns `user_fn`
    unchanged; WP1.4–WP1.6 add their constraints here.
    """
    return user_fn


def energy_cost_rows(n, commercial: dict | None) -> dict | None:
    """`energy_import` / `energy_export` for `cost_breakdown`, from the
    PERSISTED `links_t.marginal_cost` and dispatch (reload-safe).

    Both are already inside the statistics OPEX of their Links, so the rows
    are a labelled split of the total, not an addition to it.
    """
    if not commercial:
        return None
    try:
        cfg = CommercialConfig.model_validate(commercial)
    except Exception:  # noqa: BLE001 — a config that cannot bind has no rows
        return None
    w = n.snapshot_weightings.objective
    if isinstance(n.snapshots, pd.MultiIndex):
        years = n.investment_period_weightings["objective"]
        w = w * years.reindex(n.snapshots.get_level_values(0)).to_numpy()

    def row(link: str | None) -> float | None:
        if link is None or link not in n.links.index:
            return None
        mc = (n.links_t.marginal_cost[link] if link in n.links_t.marginal_cost.columns
              else pd.Series(float(n.links.at[link, "marginal_cost"]), index=n.snapshots))
        p0 = n.links_t.p0[link] if link in n.links_t.p0.columns else None
        if p0 is None:
            return None
        return float((w * p0 * mc).sum())

    return {"energy_import": row(cfg.poc_link), "energy_export": row(cfg.export_link),
            "included_in_total": True}
