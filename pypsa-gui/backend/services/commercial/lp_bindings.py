"""
Commercial terms → the dispatch-grade LP (Edge Investment Case spec §5, §5.1).

P1 WP1.3: point-of-connection energy prices.

`materialise_poc_prices(n, commercial)` writes the energy tariff onto the PoC
Links as a time-varying `marginal_cost` before the linopy model is built:

  * import Link (`poc_link`): base cost + Σ energy items measured on `import`
    (and `net` cost items), in €/MWh (per_kwh × 1000); a `revenue` item enters
    with a minus sign;
  * export Link (`export_link`, a `poc→grid` Link): base cost + Σ export items
    (and `net` revenue items) − the export price. The price is read from the
    network's own `links_t["ic_export_price"]` column; the config route resolves
    `export_price_ref` from the Library and writes it there (see
    `routers/simulation._bind_commercial`).

**Base cost and restore (review round 1 #1, #2).** The base is what the Link
costs without the commercial layer: a user-uploaded time-varying cost (an
indexed/spot price, re-applied from the user-TS store before this runs) or else
the static `marginal_cost`. Two persisted custom frames remember, per Link, the
base (`ic_base_marginal_cost`, NaN = the static cost) and the column last
written (`ic_written_marginal_cost`). On every LOPF solve, including one with no
commercial config, a Link that was priced before and is not priced now goes back
to its base. A column that differs from what was written was changed by someone
else (a re-upload) and becomes the new base. The columns persist with the
network, and the reported energy cost is recomputed from them after a reload
(spec §5.1: rows from persisted data, never transient stashes).
`n.meta["ic_poc_links"]` records which Links the last materialisation priced.

**Two-way Links are refused (#3).** An import item bills `max(import, 0)`; a
PoC Link with `p_min_pu < 0` would be paid the import tariff on reverse flow.

**Simultaneous import and export (#4).** `net` items are split by direction (a
cost item on import, a revenue item on export). That is exact when the site
does not import and export in the same interval. When the export revenue
exceeds the import cost at a snapshot, the LP can LOWER its cost by circulating
power through the grid. Such snapshots are counted in the terms
(`simultaneous_flow_risk_snapshots`), and `energy_cost_rows` flags a solve that
actually did it (`simultaneous_import_export`); WP1.8's preflight warns
(`commercial.arbitrage_loop`).

Tariff periods are evaluated on the SITE clock. When `commercial["timezone"]` is
set, naive snapshots are read as UTC (the PyPSA-Eur convention) and converted.
When it is None, the snapshots already are the site clock, the same rule as
`tariff_engine.rate(timezone=None)`.

Items the LP does not carry here are listed in `not_in_lp` with a reason: fixed
items (never in the LP, §5.5), tiers (WP1.5c), demand (WP1.5a), capacity (the
connection agreement, WP1.4a).

`_wrap_with_commercial_bindings` is the `extra_functionality` hook for the
LP-level terms (peaks, ratchets, tiers, group caps) that later work packages
add. In WP1.3 it only chains.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig, TariffItem
from services.commercial.tariff_engine import _rates

EXPORT_PRICE_ATTR = "ic_export_price"
BASE_ATTR = "ic_base_marginal_cost"
WRITTEN_ATTR = "ic_written_marginal_cost"
META_LINKS = "ic_poc_links"
META_PRICE_AXIS = "ic_export_price_axis"
_KWH_PER_MWH = 1000.0


class CommercialBindingError(ValueError):
    """The commercial config cannot be bound to this network as it stands."""

    code = "commercial_binding_invalid"


class TimezoneRequired(CommercialBindingError):
    code = "timezone_required"


# ── small helpers ──────────────────────────────────────────────────────────


def _local_clock(snapshots: pd.Index, timezone: str | None) -> pd.DatetimeIndex:
    ts = snapshots.get_level_values(-1) if isinstance(snapshots, pd.MultiIndex) else snapshots
    idx = pd.DatetimeIndex(ts)
    if timezone is None:
        return idx
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(timezone)


def _axis_hash(snapshots: pd.Index) -> str:
    raw = "|".join(map(str, snapshots.tolist())).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _lp_reason(item: TariffItem) -> str | None:
    """Why `item` is not a per-interval energy price on a PoC Link, or None."""
    if item.kind == "fixed":
        return "fixed_not_in_lp"
    if item.kind == "demand" or item.measured_on == "peak_import":
        return "demand_WP1.5a"
    if item.kind == "capacity":
        return "capacity_WP1.4a"
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


def _require_one_way(n, name: str, what: str) -> None:
    static = float(n.links.at[name, "p_min_pu"])
    dyn = n.links_t.p_min_pu[name] if name in n.links_t.p_min_pu.columns else None
    if static < 0 or (dyn is not None and (dyn < 0).any()):
        raise CommercialBindingError(
            f"commercial.{what} {name!r} allows reverse flow (p_min_pu < 0); the PoC Links "
            "must be one-way (model export with a separate export_link)")


def _frame(n, attr: str) -> pd.DataFrame:
    store = n.links_t.get(attr) if hasattr(n.links_t, "get") else None
    if store is None or not isinstance(store, pd.DataFrame):
        return pd.DataFrame(index=n.snapshots)
    if not store.index.equals(n.snapshots):
        store = store.reindex(n.snapshots)
    return store.copy()


# ── validation and the adders ──────────────────────────────────────────────


def _parse(commercial) -> CommercialConfig:
    return (commercial if isinstance(commercial, CommercialConfig)
            else CommercialConfig.model_validate(commercial))


def _adders(n, cfg: CommercialConfig) -> tuple[dict[str, np.ndarray], list[str], dict, list]:
    """(€/MWh per side, energy item ids, not_in_lp, notes). Raises on items the
    network cannot carry (an export item with no export Link, an unrated
    snapshot)."""
    local = _local_clock(n.snapshots, cfg.timezone)
    adders = {"import": np.zeros(len(n.snapshots)), "export": np.zeros(len(n.snapshots))}
    energy_items: list[str] = []
    not_in_lp: dict[str, str] = {}
    notes: list[str] = []
    for item in (cfg.import_tariff.items if cfg.import_tariff is not None else []):
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
        adders[side] += (1.0 if item.direction == "cost" else -1.0) * r * _KWH_PER_MWH
        energy_items.append(item.id)
        if item.measured_on == "net" and "net_split_by_direction" not in notes:
            notes.append("net_split_by_direction")
    return adders, energy_items, not_in_lp, notes


def validate_for_network(n, cfg: CommercialConfig | dict) -> None:
    """Refuse a commercial config this network cannot bind. The config route
    runs this, so the user hears about a bad config when editing it rather than
    at solve time. It checks everything except the export price, which the
    route writes after this."""
    cfg = _parse(cfg)
    _require_link(n, cfg.poc_link, "poc_link")
    _require_one_way(n, cfg.poc_link, "poc_link")
    if cfg.export_link is not None:
        _require_link(n, cfg.export_link, "export_link")
        _require_one_way(n, cfg.export_link, "export_link")
    if cfg.import_tariff_id is not None and cfg.import_tariff is None:
        raise CommercialBindingError(
            "import_tariff_id names a Library tariff, which arrives in P2 (WP2.4); "
            "carry the tariff inline as import_tariff")
    _adders(n, cfg)  # dry run: export items without an export Link, unrated snapshots


# ── export price (resolved at config time) ─────────────────────────────────


def align_to_snapshots(series: pd.Series, snapshots: pd.Index,
                       timezone: str | None = None) -> pd.Series:
    """A Library series on the network's snapshot axis.

    Clock: a tz-aware series is converted to the snapshot clock. With
    `timezone` set, the snapshots are UTC-naive and the series is converted to
    UTC. With `timezone` None, the snapshots are the site clock and a zoned
    series is ambiguous: `TimezoneRequired`. A naive series is taken to be on
    the snapshot clock.

    Resolution: a FINER series is averaged over each snapshot's step; a coarser
    one is held across the snapshots inside each of its steps. Anything not
    covered is NaN, and the caller refuses it rather than filling it with 0.
    """
    s = series.copy()
    if isinstance(s.index, pd.DatetimeIndex) and s.index.tz is not None:
        if timezone is None:
            raise TimezoneRequired(
                "the Library series carries a time zone but commercial.timezone is not set; "
                "set it so the series can be placed on the (UTC) snapshot axis")
        s.index = s.index.tz_convert("UTC").tz_localize(None)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    target = pd.DatetimeIndex(snapshots.get_level_values(-1)
                              if isinstance(snapshots, pd.MultiIndex) else snapshots)
    vals = np.full(len(target), np.nan)
    if len(s) == 0:
        return pd.Series(vals, index=snapshots, name=series.name)
    src_step = pd.Series(s.index[1:] - s.index[:-1]).median() if len(s) > 1 else None
    tgt_step = pd.Series(target[1:] - target[:-1]).median() if len(target) > 1 else None
    sv = s.to_numpy(dtype=float)
    if src_step is not None and tgt_step is not None and src_step < tgt_step:
        # Finer source: mean over [t, t + step) for each snapshot.
        nxt = np.append(target[1:].asi8, target[-1].value + tgt_step.value)
        lo = np.searchsorted(s.index.asi8, target.asi8, side="left")
        hi = np.searchsorted(s.index.asi8, nxt, side="left")
        csum = np.concatenate([[0.0], np.cumsum(sv)])
        count = hi - lo
        ok = count > 0
        vals[ok] = (csum[hi[ok]] - csum[lo[ok]]) / count[ok]
        return pd.Series(vals, index=snapshots, name=series.name)
    pos = s.index.get_indexer(target)
    if src_step is not None and (pos < 0).any():
        held = s.index.get_indexer(target, method="ffill")
        ok = held >= 0
        within = np.zeros(len(target), dtype=bool)
        within[ok] = (target[ok] - s.index[held[ok]]) < src_step
        pos = np.where(pos >= 0, pos, np.where(ok & within, held, -1))
    vals = np.where(pos >= 0, sv[np.clip(pos, 0, None)], np.nan)
    return pd.Series(vals, index=snapshots, name=series.name)


def write_export_price(n, link: str, series: pd.Series, timezone: str | None = None) -> int:
    """Put a resolved Library price series on `link`'s `ic_export_price` column.
    Aligns first and writes ONLY when every snapshot is covered; returns the
    number of uncovered snapshots (0 = written). Records the snapshot axis the
    column belongs to, so a later resample is caught at solve time."""
    aligned = align_to_snapshots(series, n.snapshots, timezone)
    uncovered = int(aligned.isna().sum())
    if uncovered:
        return uncovered
    store = _frame(n, EXPORT_PRICE_ATTR)
    store[link] = aligned.to_numpy()
    n.links_t[EXPORT_PRICE_ATTR] = store
    n.meta[META_PRICE_AXIS] = _axis_hash(n.snapshots)
    return 0


def _export_price(n, cfg: CommercialConfig) -> np.ndarray:
    store = n.links_t.get(EXPORT_PRICE_ATTR) if hasattr(n.links_t, "get") else None
    if n.meta.get(META_PRICE_AXIS) not in (None, _axis_hash(n.snapshots)):
        raise CommercialBindingError(
            "the snapshots changed since the export price was materialised; re-apply the "
            "commercial config to place the Library series on the current axis")
    col = None if store is None or cfg.export_link not in store.columns \
        else store[cfg.export_link].reindex(n.snapshots)
    if col is None or col.isna().any():
        missing = len(n.snapshots) if col is None else int(col.isna().sum())
        raise CommercialBindingError(
            f"export price is not established for {missing} snapshot(s) of "
            f"{cfg.export_link!r}; re-apply the commercial config to materialise the "
            "Library series on the current snapshot axis")
    return col.to_numpy(dtype=float)


# ── materialisation with base / restore ────────────────────────────────────


def _base_for(n, link: str, base: pd.DataFrame, written: pd.DataFrame) -> np.ndarray | None:
    """The link's base cost per snapshot (None = the static cost)."""
    mc = n.links_t.marginal_cost
    current = mc[link].to_numpy(dtype=float) if link in mc.columns else None
    if link in written.columns and current is not None:
        was = written[link].to_numpy(dtype=float)
        if np.allclose(current, was, equal_nan=True, rtol=0, atol=1e-9):
            remembered = base[link].to_numpy(dtype=float) if link in base.columns else None
            if remembered is None or np.isnan(remembered).all():
                return None
            return np.where(np.isnan(remembered), float(n.links.at[link, "marginal_cost"]),
                            remembered)
    return current  # someone else's column (a re-upload) or none: the new base


def _restore(n, links, base: pd.DataFrame, written: pd.DataFrame) -> None:
    mc = n.links_t.marginal_cost
    for link in list(links):
        if link in mc.columns and link in written.columns and np.allclose(
                mc[link].to_numpy(dtype=float), written[link].to_numpy(dtype=float),
                equal_nan=True, rtol=0, atol=1e-9):
            b = base[link].to_numpy(dtype=float) if link in base.columns else None
            if b is None or np.isnan(b).all():
                n.links_t.marginal_cost = mc.drop(columns=[link])
                mc = n.links_t.marginal_cost
            else:
                mc[link] = np.where(np.isnan(b), float(n.links.at[link, "marginal_cost"]), b)
        base.drop(columns=[link], inplace=True, errors="ignore")
        written.drop(columns=[link], inplace=True, errors="ignore")


def _store_markers(n, base: pd.DataFrame, written: pd.DataFrame) -> None:
    n.links_t[BASE_ATTR] = base
    n.links_t[WRITTEN_ATTR] = written


def materialise_poc_prices(n, commercial: dict | CommercialConfig | None,
                           *, log=None) -> dict | None:
    """Write PoC energy prices onto the network (see module docstring).

    Runs on EVERY LOPF solve: with no commercial config it restores any Link a
    previous config priced and returns None. Otherwise returns the terms
    applied (for `last_commercial_terms`). Raises `CommercialBindingError` when
    the config cannot bind; a missing price is refused, never priced at 0.
    """
    base = _frame(n, BASE_ATTR)
    written = _frame(n, WRITTEN_ATTR)
    if not commercial:
        if len(written.columns):
            _restore(n, list(written.columns), base, written)
            _store_markers(n, base, written)
            if log is not None:
                log("[COMMERCIAL] no commercial config: PoC Links restored to their base cost")
        n.meta.pop(META_LINKS, None)
        return None

    cfg = _parse(commercial)
    validate_for_network(n, cfg)
    adders, energy_items, not_in_lp, notes = _adders(n, cfg)
    has_price = cfg.export_price_ref is not None
    if has_price:
        adders["export"] = adders["export"] - _export_price(n, cfg)

    targets: dict[str, np.ndarray] = {}
    if adders["import"].any():
        targets[cfg.poc_link] = adders["import"]
    if cfg.export_link is not None and (has_price or adders["export"].any()):
        targets[cfg.export_link] = adders["export"]

    _restore(n, [l for l in written.columns if l not in targets], base, written)
    mc = n.links_t.marginal_cost
    for link, add in targets.items():
        b = _base_for(n, link, base, written)
        static = float(n.links.at[link, "marginal_cost"])
        values = (static if b is None else b) + add
        mc[link] = values
        base[link] = np.nan if b is None else b
        written[link] = values
    _store_markers(n, base, written)
    n.meta[META_LINKS] = {"import": cfg.poc_link, "export": cfg.export_link}

    risk = 0
    if cfg.export_link is not None:
        imp = (mc[cfg.poc_link].to_numpy(dtype=float) if cfg.poc_link in mc.columns
               else np.full(len(n.snapshots), float(n.links.at[cfg.poc_link, "marginal_cost"])))
        exp = (mc[cfg.export_link].to_numpy(dtype=float) if cfg.export_link in mc.columns
               else np.full(len(n.snapshots),
                            float(n.links.at[cfg.export_link, "marginal_cost"])))
        risk = int(((imp + exp) < -1e-9).sum())  # export revenue > import cost
    if log is not None:
        log(f"[COMMERCIAL] PoC prices materialised on {sorted(targets)} "
            f"({len(energy_items)} energy item(s); {len(not_in_lp)} left to later bindings"
            + (f"; WARNING {risk} snapshot(s) pay more to export than to import" if risk else "")
            + ")")
    return {"poc_link": cfg.poc_link, "export_link": cfg.export_link,
            "priced_links": sorted(targets), "energy_items": energy_items,
            "not_in_lp": not_in_lp, "notes": notes, "timezone": cfg.timezone,
            "simultaneous_flow_risk_snapshots": risk,
            "export_price_ref": (cfg.export_price_ref.model_dump(mode="json")
                                 if cfg.export_price_ref is not None else None)}


def _wrap_with_commercial_bindings(network, user_fn, cfg, log_queue=None):
    """`extra_functionality` hook for LP-level commercial terms (spec §5.1).

    Same closure/chaining shape as `_wrap_with_capex_budget`. WP1.3 adds no
    LP rows (energy prices are `marginal_cost`), so this returns `user_fn`
    unchanged; WP1.5–WP1.6 add their constraints here.
    """
    return user_fn


# ── cost rows (persisted data) ─────────────────────────────────────────────


def energy_cost_rows(n, commercial: dict | None) -> dict | None:
    """`energy_import` / `energy_export` for `cost_breakdown`, from the
    PERSISTED `links_t.marginal_cost`, dispatch and `n.meta["ic_poc_links"]`
    (the Links the last solve priced), so identical after a reload.

    Both are already inside the statistics OPEX of their Links, so the rows
    are a labelled split of the total, not an addition to it. A value that
    cannot be computed is None with a flag (ADR-0001), never 0.
    """
    solved = n.meta.get(META_LINKS) if hasattr(n, "meta") else None
    if not commercial and not solved:
        return None
    flags: list[str] = []
    cfg = None
    if commercial:
        try:
            cfg = _parse(commercial)
        except Exception:  # noqa: BLE001
            flags.append("commercial_config_invalid")
    if not solved:
        flags.append("not_solved_with_commercial_config")
        return {"energy_import": None, "energy_export": None, "included_in_total": True,
                "flags": flags}
    if cfg is not None and (cfg.poc_link != solved.get("import")
                            or cfg.export_link != solved.get("export")):
        flags.append("config_changed_since_solve")
    w = n.snapshot_weightings.objective
    if isinstance(n.snapshots, pd.MultiIndex):
        years = n.investment_period_weightings["objective"]
        w = w * years.reindex(n.snapshots.get_level_values(0)).to_numpy()

    def p0_of(link):
        return n.links_t.p0[link] if link in n.links_t.p0.columns else None

    def row(link: str | None, label: str) -> float | None:
        if link is None:
            return None
        if link not in n.links.index or p0_of(link) is None:
            flags.append(f"{label}_not_established")
            return None
        mc = (n.links_t.marginal_cost[link] if link in n.links_t.marginal_cost.columns
              else pd.Series(float(n.links.at[link, "marginal_cost"]), index=n.snapshots))
        return float((w * p0_of(link) * mc).sum())

    imp_link, exp_link = solved.get("import"), solved.get("export")
    out = {"energy_import": row(imp_link, "energy_import"),
           "energy_export": row(exp_link, "energy_export"),
           "included_in_total": True}
    if imp_link and exp_link and p0_of(imp_link) is not None and p0_of(exp_link) is not None:
        both = (p0_of(imp_link) > 1e-6) & (p0_of(exp_link) > 1e-6)
        if bool(both.any()):
            flags.append("simultaneous_import_export")
            out["simultaneous_snapshots"] = int(both.sum())
    out["flags"] = flags
    return out
