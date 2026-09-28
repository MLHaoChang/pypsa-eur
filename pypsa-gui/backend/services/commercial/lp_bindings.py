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

**Transient, like every other LP transform (re-review #1, #2).** The prices are
applied for ONE solve on top of whatever the Link costs without the commercial
layer (a user-uploaded time-varying cost re-applied from the user-TS store, or
else the static `marginal_cost`), and `Applied.undo()` puts the column back
exactly as it was. The user's `marginal_cost` is therefore never modified on
disk. A Properties-panel edit, a snapshot-axis change, a re-upload or a
cleared config cannot turn an old tariff into a new base. What the report needs
afterwards, the €/MWh ADDED per priced Link, is written to the persisted custom
frame `links_t["ic_energy_price"]` by `Applied.commit()`, which `run_simulation`
calls only after a successful solve (#5), together with
`n.meta["ic_poc_links"]` (which Links that solve priced). `energy_cost_rows`
recomputes the energy import/export rows from those persisted frames and the
dispatch, so the rows are identical after a reload (spec §5.1). They are NOT in
`n.statistics()` (the prices were undone) and are ADDED to the total.

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
import json

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig, TariffItem
from services.commercial import hashing as _H
from services.commercial.tariff_engine import (
    _is_demand,
    _rates,
    demand_windows,
    interval_key,
    tiers_are_convex,
    window_rate,
)

EXPORT_PRICE_ATTR = "ic_export_price"
ENERGY_PRICE_ATTR = "ic_energy_price"
META_LINKS = "ic_poc_links"
META_PRICE_AXIS = "ic_export_price_axis"
META_DEMAND = "ic_demand_peaks"
META_DEMAND_INFO = "ic_demand_info"
DEMAND_SPEC_ATTR = "_ic_demand_spec"   # transient: set by apply, read by the LP wrapper
DEMAND_BUILT_ATTR = "_ic_demand_built"  # transient: set by the LP wrapper
META_TIERS = "ic_tier_volumes"
META_GROUP = "ic_group"
GROUP_SPEC_ATTR = "_ic_group_spec"      # transient: set by apply, read by the LP wrapper
TIER_SPEC_ATTR = "_ic_tier_spec"        # transient: set by apply, read by the LP wrapper
TIER_BUILT_ATTR = "_ic_tier_built"
_KWH_PER_MWH = 1000.0


class CommercialBindingError(ValueError):
    """The commercial config cannot be bound to this network as it stands."""

    code = "commercial_binding_invalid"


class TimezoneRequired(CommercialBindingError):
    code = "timezone_required"


class Applied:
    """A transient transform for one solve: `undo()` restores every mutated
    attribute (reverse order, once); `commit()` persists what the report needs
    and is called only after a successful solve."""

    def __init__(self, facts: dict | None = None):
        self.facts: dict = facts or {}
        self._undo: list = []
        self._commit: list = []
        self._done = False

    def undo(self) -> None:
        if self._done:
            return
        self._done = True
        for fn in reversed(self._undo):
            fn()

    def commit(self) -> None:
        for fn in self._commit:
            fn()

    def chain(self, other: "Applied | None") -> "Applied":
        """Compose `other` (applied AFTER self): undone first, committed after."""
        if other is None:
            return self
        both = Applied({**self.facts, **other.facts})
        both._undo = [self.undo, other.undo]
        both._commit = [self.commit, other.commit]
        return both


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
    """Why `item` is neither a per-interval energy price nor a peak-demand
    term the LP carries (WP1.5a), or None."""
    if item.kind == "fixed":
        return "fixed_not_in_lp"
    if item.kind == "capacity":
        # Before the demand check: a capacity item measured on peak_import (the
        # DE Leistungspreis) is a capacity charge, not a demand one. Tariff
        # capacity items enter the LP in P2 WP2.1c.
        return "capacity_not_in_lp_until_WP2.1c"
    if _is_demand(item):
        if item.tiers:
            return "tiers_on_demand_not_supported"
        if item.unit != "per_kw_month":
            return f"unit_{item.unit}_not_demand"
        if item.direction != "cost":
            return "demand_revenue_not_supported"
        if item.measured_on == "export":
            return "export_demand_not_supported"
        return None
    if item.tiers and item.periods[0].tier_rates is not None:
        # Per-period tier rates (WP2.1a-ii) — its `Tier.rate` are 0 by rule, so
        # the P1 tier terms would price it at 0; the LP carries them in WP2.1c.
        return "tiers_with_windows"
    if item.tiers:
        # WP1.5c: tiers on cumulative monthly import volume, one catch-all period.
        p = item.periods[0]
        if len(item.periods) != 1 or p.months or p.weekdays or p.start_hour is not None:
            return "tiers_with_windows"
        if item.direction != "cost":
            return "tiers_on_revenue_not_supported"
        if item.measured_on != "import":
            return "tiers_on_import_only"
    if item.unit != "per_kwh":
        return f"unit_{item.unit}_not_energy"
    return None


def _side(item: TariffItem) -> str:
    if item.measured_on == "import":
        return "import"
    if item.measured_on == "export":
        return "export"
    return "import" if item.direction == "cost" else "export"  # net


def tier_items_hash(cfg: CommercialConfig, version: int = _H.HASH_VERSION) -> str | None:
    """The hash of the convex tiered items a solve would bind (drift check)."""
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if i.tiers and not _is_demand(i) and _lp_reason(i) is None
             and tiers_are_convex(i.tiers)]
    return _items_hash(items, version) if items else None


def group_spec(cfg: CommercialConfig) -> dict | None:
    """The group contract a solve would bind, as committed to `ic_group`."""
    if not cfg.group_members:
        return None
    return {"name": cfg.group_contract, "members": list(cfg.group_members),
            "cap_mw": float(cfg.group_cap_mw)}


def import_links(cfg: CommercialConfig) -> list[str]:
    """The Links the import tariff is charged on: the group's members (one
    customer under one tariff, WP1.6 round 2), else the PoC alone."""
    return list(cfg.group_members) if cfg.group_members else [cfg.poc_link]


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
        if _is_demand(item):
            neg = [p.name for p in item.periods if p.rate < 0]
            if neg:
                # A negative €/kW would pay the LP to raise its peak without
                # bound (review round 3 #5).
                raise CommercialBindingError(
                    f"demand item {item.id!r} has a negative rate in period(s) {neg}; a "
                    "demand credit is not a peak charge")
            continue  # a peak term, built by `_demand_spec`
        if item.tiers:
            if tiers_are_convex(item.tiers):
                continue  # stacked volume terms, built by `_tier_spec`
            # Falling marginal rates are non-convex (spec §5.3): priced at the
            # first tier (no volume history in P1), flagged; billed exactly.
            adders["import"] += float(item.tiers[0].rate) * _KWH_PER_MWH
            energy_items.append(item.id)
            if "nonconvex_tier" not in notes:
                notes.append("nonconvex_tier")
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
    if cfg.import_tariff is not None or cfg.connection is not None:
        ts = (n.snapshots.get_level_values(-1) if isinstance(n.snapshots, pd.MultiIndex)
              else n.snapshots)
        if not isinstance(ts, pd.DatetimeIndex):
            # A tariff's windows, months and validity are dates; an integer or
            # 'now' axis would be rated on 1970 or today (WP1.8 review #6).
            raise CommercialBindingError(
                "a tariff or connection agreement needs datetime snapshots; this network's "
                "snapshot axis is not datetime")
    if cfg.export_link is not None:
        _require_link(n, cfg.export_link, "export_link")
        _require_one_way(n, cfg.export_link, "export_link")
    if cfg.connection is not None and cfg.connection.capacity_fee is not None and any(
            i.kind == "capacity" for i in (cfg.import_tariff.items if cfg.import_tariff else [])):
        # Two capacity charges on one PoC (P2 WP2.1a-i): the connection fee and a
        # tariff capacity item would bill the same contracted capacity twice.
        raise CommercialBindingError(
            "commercial.capacity_double_count: the import tariff has a capacity item and the "
            "connection agreement a capacity_fee on the same PoC; keep one")
    if cfg.demand_items:
        # Not implemented in P1: every demand item of the tariff is charged, so
        # a selection would be silently ignored (Phase 1 gate finding #4).
        raise CommercialBindingError(
            "commercial.demand_items (a selection of demand items) is not supported in P1; "
            "every demand item of the tariff is charged — remove the unwanted items instead")
    if cfg.import_tariff_id is not None and cfg.import_tariff is None:
        raise CommercialBindingError(
            "import_tariff_id names a Library tariff, which arrives in P2 (WP2.4); "
            "carry the tariff inline as import_tariff")
    bounds = (n.meta.get("vintage_bounds") or {}).get("Link") if hasattr(n, "meta") else None
    if isinstance(bounds, dict):
        clash = sorted(l for l in {*import_links(cfg), cfg.export_link} if l and l in bounds)
        if clash:
            raise CommercialBindingError(
                f"per-period vintage bounds on the PoC / group Link(s) {clash} are not supported "
                "with the commercial layer in P1 (the vintage clones would carry dispatch the "
                "group cap, the meters and the commercial rows do not read)")
    if cfg.group_members:
        # One customer under one tariff: every member is an import connection
        # on the PoC's grid side, and the tariffed PoC is one of them.
        if cfg.poc_link not in cfg.group_members:
            raise CommercialBindingError(
                f"group_members must include poc_link {cfg.poc_link!r}: the group's tariff "
                "is the PoC's")
        grid_bus = n.links.at[cfg.poc_link, "bus0"]
        net = [i.id for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
               if i.measured_on == "net" and not _is_demand(i)]
        if net and cfg.export_link is not None and len(cfg.group_members) > 1:
            # The bill nets the group's import against its export on the group
            # meter; per-member adders charge each member's gross import.
            raise CommercialBindingError(
                f"energy items measured on net ({net}) on a multi-member group with an export "
                "Link would charge gross member import the group meter nets out; not supported "
                "in P1 (price them on import and export separately)")
        for member in cfg.group_members:
            _require_link(n, member, "group_members")
            _require_one_way(n, member, "group_members")
            if member == cfg.export_link:
                raise CommercialBindingError(
                    f"group member {member!r} is the export_link; members are import "
                    "connections")
            if n.links.at[member, "bus0"] != grid_bus:
                raise CommercialBindingError(
                    f"group member {member!r} has bus0 {n.links.at[member, 'bus0']!r}, not the "
                    f"PoC's grid bus {grid_bus!r}: members are import connections")
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
        # The window ends at the next snapshot OR one step on, whichever is
        # first: across a gap (representative weeks, a period boundary) the
        # last snapshot must not average the whole gap (re-review #3).
        nxt = np.minimum(np.append(target[1:].asi8, np.iinfo(np.int64).max),
                         target.asi8 + tgt_step.value)
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


def _aligned_once(n, series: pd.Series, timezone: str | None) -> pd.Series:
    """A series already on the snapshot axis (the route aligns before it
    writes) is used as is; anything else is aligned here."""
    if series.index.equals(n.snapshots):
        return series
    return align_to_snapshots(series, n.snapshots, timezone)


def write_export_price(n, link: str, series: pd.Series, timezone: str | None = None) -> int:
    """Put a resolved Library price series on `link`'s `ic_export_price` column.
    Aligns first and writes ONLY when every snapshot is covered; returns the
    number of uncovered snapshots (0 = written). Records the snapshot axis the
    column belongs to, so a later resample is caught at solve time."""
    aligned = _aligned_once(n, series, timezone)
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


# ── peak demand (WP1.5a) ───────────────────────────────────────────────────


def _items_hash(items, version: int = _H.HASH_VERSION) -> str:
    """Content hash of the items (periods, rates, settlement, ratchet, tiers)
    — the rows compare it to tell a rate change after the solve (round 2 #5).
    Versioned recipe: `services/commercial/hashing.py`."""
    return _H.digest(list(items), version=version)


def _demand_spec(n, cfg: CommercialConfig) -> tuple[dict | None, list[str], list[str], list[str]]:
    """(spec for the LP wrapper, demand item ids, months not established).

    One key per (item, period window, investment period, local month) with at
    least one snapshot; the months between the first and last snapshot that
    have none are listed as not established (spec §5.2), and never weighted
    from their neighbours."""
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if _is_demand(i) and _lp_reason(i) is None]
    if not items:
        return None, [], [], []
    local = _local_clock(n.snapshots, cfg.timezone)
    months = np.asarray(local.strftime("%Y-%m"))
    inv = (np.asarray(n.snapshots.get_level_values(0)) if isinstance(n.snapshots, pd.MultiIndex)
           else np.full(len(n.snapshots), None, dtype=object))
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    missing: list[str] = []
    partial: list[str] = []
    for p in pd.unique(inv):
        sel = inv == p
        if abs(w[sel].sum() - 8760.0) <= 0.01 * 8760.0:
            # The period's snapshots represent a whole year: every calendar
            # month of the year holding most of the weight is billed (review
            # round 2 #2 — a UTC year read in Bogotá touches 5 h of the
            # previous December, which must not pull the span to 2029).
            years = pd.Series(w[sel]).groupby(np.asarray(local[sel].year)).sum()
            year = int(years.idxmax())
            span = pd.period_range(f"{year}-01", f"{year}-12", freq="M").strftime("%Y-%m")
        else:
            span = pd.period_range(local[sel].min().strftime("%Y-%m"),
                                   local[sel].max().strftime("%Y-%m"),
                                   freq="M").strftime("%Y-%m")
        # A full monthly charge against part of a month's operation (§5.2,
        # decided: full month, as billed) is disclosed, edge months included.
        for m in sorted(set(months[sel])):
            hours = pd.Period(m, freq="M").days_in_month * 24.0
            if w[sel & (months == m)].sum() < hours - 1e-6:
                partial.append(m if p is None else f"{p}:{m}")
        gone = sorted(set(span) - set(months[sel]))
        missing += [m if p is None else f"{p}:{m}" for m in gone]
    keys: list[dict] = []
    for item in items:
        # Windows are the item's period NAMES (fragments of one URDB period are
        # one window with one monthly peak; IC P2 WP2.1a-0), as in the engine.
        window, names, frag = demand_windows(item, local)
        # Demand INTERVALS (`settlement`): the bound is on the interval mean,
        # as billed (review #5); singleton groups when snapshots are that fine.
        interval = interval_key(local, {"15min": "15min", "30min": "30min",
                                        "h": "h"}[item.settlement])
        for k, name in enumerate(names):
            for p in pd.unique(inv):
                for m in sorted(set(months[(inv == p) & (window == k)])):
                    pos = np.flatnonzero((inv == p) & (window == k) & (months == m))
                    rate = window_rate(item, frag[pos])
                    if rate == 0:
                        continue
                    _, group = np.unique(interval[pos], return_inverse=True)
                    keys.append({
                        "group": group,
                        "key": f"{item.id}|{name}|{'' if p is None else p}|{m}",
                        "item": item.id, "period": name, "month": m,
                        "inv_period": None if p is None else int(p),
                        "eur_per_mw": rate * _KWH_PER_MWH,
                        "net": item.measured_on == "net", "positions": pos})
    if len({k["key"] for k in keys}) != len(keys):  # '|' is refused in ids and names
        raise CommercialBindingError("demand peak keys are not unique")
    # Ratchets (WP1.5b): billed[key] ≥ ρ · actual[key'] for each lookback
    # month modelled in the SAME investment period, ≥ ρ · metered history for
    # one before the horizon; unknown months are disclosed, not constrained.
    by_ident = {(k["item"], k["period"], k["inv_period"], k["month"]): k["key"] for k in keys}
    # Every month of the dispatch is modelled, whatever the item's windows or
    # rates, as in the engine (review round 3 #2).
    modelled = {(None if p is None else int(p), m) for p, m in zip(inv, months)}
    ratchets: list[dict] = []
    notes: list[str] = []
    item_by_id = {i.id: i for i in items}
    first_period = None if not isinstance(n.snapshots, pd.MultiIndex) \
        else int(min(n.investment_periods))
    for k in keys:
        r = item_by_id[k["item"]].ratchet
        if r is None:
            continue
        # Meter history is the site's past: it seeds the FIRST period only; a
        # later investment period's lookback is unknown (review round 2 #3).
        history_ok = k["inv_period"] in (None, first_period)
        here = pd.Period(k["month"], freq="M")
        for back in range(1, r.lookback_months + 1):
            m = (here - back).strftime("%Y-%m")
            other = by_ident.get((k["item"], k["period"], k["inv_period"], m))
            if other is not None:
                ratchets.append({"key": k["key"], "share": r.share, "of_key": other})
            elif (k["inv_period"], m) in modelled:
                continue  # modelled month with no interval in this window: no demand
            elif history_ok and m in cfg.meter_history_peaks_kw:
                ratchets.append({"key": k["key"], "share": r.share,
                                 "floor_mw": cfg.meter_history_peaks_kw[m] / _KWH_PER_MWH})
            elif "ratchet_seed_missing" not in notes:
                notes.append("ratchet_seed_missing")
    floors = {k["key"]: cfg.initial_peak_lower_bound[k["month"]] for k in keys
              if k["month"] in cfg.initial_peak_lower_bound}
    spec = {"import_links": import_links(cfg), "export_link": cfg.export_link, "keys": keys,
            "ratchets": ratchets, "floors": floors,
            "info": {"items": [i.id for i in items], "not_established": missing,
                     "partial_months": partial, "notes": list(notes),
                     "items_hash": demand_hash(n, cfg, items),
                     # Recipe version of items_hash (P2 WP2.1a-0): a future recipe
                     # change compares a P1 record (no version) with the P1 recipe
                     # instead of flagging every solved project as drift.
                     "hash_version": DEMAND_HASH_VERSION}}
    return spec, [i.id for i in items], missing, notes


def energy_hash(n, cfg: CommercialConfig, version: int = _H.HASH_VERSION) -> str:
    """What decides the energy rows besides the dispatch: the per-interval
    energy items (incl. non-convex tiers priced at their first tier), the
    export price version, the site clock, the charged Links and the axis
    (Phase 1 gate binding condition 2)."""
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if not _is_demand(i) and _lp_reason(i) is None
             and not (i.tiers and tiers_are_convex(i.tiers))]
    raw = json.dumps({"items": _H.canonical(items, version=version),
                      "export_price_ref": (_H.canonical(cfg.export_price_ref, version=version)
                                           if cfg.export_price_ref is not None else None),
                      "timezone": cfg.timezone, "import": sorted(import_links(cfg)),
                      "export": cfg.export_link, "axis": _axis_hash(n.snapshots)},
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


DEMAND_HASH_VERSION = _H.HASH_VERSION  # recorded in the demand info (recipe of items_hash)


def demand_hash(n, cfg: CommercialConfig, items, version: int = _H.HASH_VERSION) -> str:
    """What decides the billed demand besides the items: the site clock, the
    meter history, the peak floors and the snapshot axis (review round 3 #4)."""
    raw = json.dumps({"items": _items_hash(items, version), "timezone": cfg.timezone,
                      "history": sorted(cfg.meter_history_peaks_kw.items()),
                      "floors": sorted(cfg.initial_peak_lower_bound.items()),
                      "axis": _axis_hash(n.snapshots)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def add_demand_terms(n) -> None:
    """`ic_peak_import[key] ≥ p_import[t]` for the key's snapshots, and
    objective += Σ w_obj(period) · €/MW · ic_peak_import (no snapshot weights:
    a demand charge is per month). Called from the LP wrapper."""
    import xarray as xr

    spec = getattr(n, DEMAND_SPEC_ATTR, None)
    if not spec or not spec["keys"]:
        return
    m = n.model
    names = [k["key"] for k in spec["keys"]]
    peak = m.add_variables(lower=0, name="ic_peak_import",
                           coords=[pd.Index(names, name="key")])
    # The BILLED demand (WP1.5b): ≥ the month's actual peak, ≥ each ratchet
    # term. Without a ratchet it equals the actual peak at the optimum.
    billed = m.add_variables(lower=0, name="ic_billed_demand",
                             coords=[pd.Index(names, name="key")])
    m.add_constraints(billed - peak >= 0, name="ic_billed_demand_def")
    for i, r in enumerate(spec.get("ratchets", [])):
        if "of_key" in r:
            m.add_constraints(billed.sel(key=r["key"]) - r["share"] * peak.sel(key=r["of_key"])
                              >= 0, name=f"ic_ratchet_{i}")
        else:
            m.add_constraints(billed.sel(key=r["key"]) >= r["share"] * r["floor_mw"],
                              name=f"ic_ratchet_{i}")
    for i, (key, floor) in enumerate(spec.get("floors", {}).items()):
        m.add_constraints(peak.sel(key=key) >= float(floor), name=f"ic_peak_floor_{i}")
    link_p = m["Link-p"]
    # The group meter: the members' combined import (a single PoC otherwise).
    imp = link_p.sel(name=spec["import_links"]).sum("name")
    exp = link_p.sel(name=spec["export_link"]) if spec["export_link"] else None
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    for i, k in enumerate(spec["keys"]):
        pos = k["positions"]
        flow = imp.isel(snapshot=pos)
        if k["net"] and exp is not None:
            flow = flow - exp.isel(snapshot=pos)
        group = k["group"]
        if group.max() + 1 == len(pos):  # one snapshot per demand interval
            m.add_constraints(flow - peak.sel(key=k["key"]) <= 0, name=f"ic_peak_import_def_{i}")
            continue
        # Interval mean ≤ peak:  Σ_{t∈g} w_t·flow_t − (Σ_{t∈g} w_t)·peak ≤ 0.
        gda = xr.DataArray(group, coords={"snapshot": flow.indexes["snapshot"]},
                           dims="snapshot", name="interval")
        weighted = (flow * w_all[pos]).groupby(gda).sum()
        wsum = np.bincount(group, weights=w_all[pos])
        wda = xr.DataArray(wsum, coords={"interval": np.arange(len(wsum))}, dims="interval")
        m.add_constraints(weighted - wda * peak.sel(key=k["key"]) <= 0,
                          name=f"ic_peak_import_def_{i}")
    if isinstance(n.snapshots, pd.MultiIndex):
        w_obj = n.investment_period_weightings["objective"]
        weight = [float(w_obj.loc[k["inv_period"]]) for k in spec["keys"]]
    else:
        weight = [1.0] * len(names)
    coef = xr.DataArray([w * k["eur_per_mw"] for w, k in zip(weight, spec["keys"])],
                        coords={"key": names}, dims="key")
    m.objective += (billed * coef).sum()
    setattr(n, DEMAND_BUILT_ATTR, True)


def _read_demand_solution(n, spec: dict) -> dict | None:
    """The solved peaks, read from `n.model` while it still exists."""
    model = getattr(n, "model", None)
    if not getattr(n, DEMAND_BUILT_ATTR, False) or model is None:
        return None
    try:
        sol = model.variables["ic_peak_import"].solution.to_pandas()
        billed = model.variables["ic_billed_demand"].solution.to_pandas()
    except Exception:  # noqa: BLE001 — unsolved / infeasible: nothing to commit
        return None
    # The ACTUAL peak comes from the solved dispatch: the `peak` variable is
    # free between it and the billed demand when a ratchet binds (review
    # round 3 #3). Interval means as billed, floored at 0 like the variable.
    try:
        link_p = model.variables["Link-p"].solution
        flow = link_p.sel(name=spec["import_links"]).sum("name").values
        exp = (link_p.sel(name=spec["export_link"]).values if spec["export_link"] else None)
    except Exception:  # noqa: BLE001
        return None
    w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
    out = {}
    for k in spec["keys"]:
        b = float(billed.loc[k["key"]])
        pos, group = k["positions"], k["group"]
        f = flow[pos] - (exp[pos] if k["net"] and exp is not None else 0.0)
        if group.max() + 1 == len(pos):
            means = f  # one snapshot per interval: constrained as is, like the LP
        else:
            # An interval weighing nothing has no mean and no constraint
            # (WP1.6/1.7 round 3): left out, never read as 0.
            wsum = np.bincount(group, weights=w_all[pos])
            ok = wsum > 0
            means = np.bincount(group, weights=f * w_all[pos])[ok] / wsum[ok]
        top = float(means.max()) if len(means) else float("nan")
        if not np.isfinite(top):
            top = float(sol.loc[k["key"]])  # nothing to read: the solved variable
        v = max(0.0, top)
        # A floor is the month's running peak from earlier windows (P6 hook).
        v = max(v, float(spec.get("floors", {}).get(k["key"], 0.0)))
        if not (np.isfinite(v) and np.isfinite(b)):
            return None
        out[k["key"]] = {"item": k["item"], "period": k["period"], "month": k["month"],
                         "inv_period": k["inv_period"], "eur_per_mw": k["eur_per_mw"],
                         "peak_mw": v, "billed_mw": b}
    return out


# ── convex tiers (WP1.5c) ──────────────────────────────────────────────────


def _tier_spec(n, cfg: CommercialConfig) -> tuple[dict | None, list[str], list[str]]:
    """(spec, convex tiered item ids, non-convex tiered item ids). One key per
    (item, investment period, local month) with snapshots; tier widths in MWh
    and rates in €/MWh."""
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if i.tiers and not _is_demand(i) and _lp_reason(i) is None]
    convex = [i for i in items if tiers_are_convex(i.tiers)]
    nonconvex = [i.id for i in items if not tiers_are_convex(i.tiers)]
    if not convex:
        return None, [], nonconvex
    local = _local_clock(n.snapshots, cfg.timezone)
    months = np.asarray(local.strftime("%Y-%m"))
    inv = (np.asarray(n.snapshots.get_level_values(0)) if isinstance(n.snapshots, pd.MultiIndex)
           else np.full(len(n.snapshots), None, dtype=object))
    keys: list[dict] = []
    for item in convex:
        th = [t.threshold / _KWH_PER_MWH for t in item.tiers] + [np.inf]
        for p in pd.unique(inv):
            for m in sorted(set(months[inv == p])):
                pos = np.flatnonzero((inv == p) & (months == m))
                keys.append({"key": f"{item.id}|{'' if p is None else p}|{m}", "item": item.id,
                             "month": m, "inv_period": None if p is None else int(p),
                             "positions": pos,
                             "tiers": [{"k": k, "width_mwh": th[k + 1] - th[k],
                                        "rate_eur_per_mwh": float(t.rate) * _KWH_PER_MWH}
                                       for k, t in enumerate(item.tiers)]})
    return ({"import_links": import_links(cfg), "keys": keys, "items_hash": _items_hash(convex),
             "hash_version": _H.HASH_VERSION},
            [i.id for i in convex], nonconvex)


def add_tier_terms(n) -> None:
    """Σ_k ic_tier_q[key,k] = the month's import energy (Σ w_t · p_t, MWh),
    0 ≤ ic_tier_q[key,k] ≤ width_k, objective += Σ w_obj · rate_k · ic_tier_q."""
    import xarray as xr

    spec = getattr(n, TIER_SPEC_ATTR, None)
    if not spec or not spec["keys"]:
        return
    m = n.model
    names, uppers, coefs = [], [], []
    w_obj = (n.investment_period_weightings["objective"]
             if isinstance(n.snapshots, pd.MultiIndex) else None)
    for key in spec["keys"]:
        wk = 1.0 if w_obj is None else float(w_obj.loc[key["inv_period"]])
        for t in key["tiers"]:
            names.append(f"{key['key']}|{t['k']}")
            uppers.append(t["width_mwh"])
            coefs.append(wk * t["rate_eur_per_mwh"])
    idx = pd.Index(names, name="tier")
    q = m.add_variables(lower=0, upper=xr.DataArray(uppers, coords={"tier": names}, dims="tier"),
                        name="ic_tier_q", coords=[idx])
    imp = m["Link-p"].sel(name=spec["import_links"]).sum("name")
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    for i, key in enumerate(spec["keys"]):
        pos = key["positions"]
        energy = (imp.isel(snapshot=pos) * w[pos]).sum()
        tiers = q.sel(tier=[f"{key['key']}|{t['k']}" for t in key["tiers"]]).sum()
        m.add_constraints(tiers - energy == 0, name=f"ic_tier_volume_{i}")
    m.objective += (q * xr.DataArray(coefs, coords={"tier": names}, dims="tier")).sum()
    setattr(n, TIER_BUILT_ATTR, True)


def _read_tier_solution(n, spec: dict) -> dict | None:
    model = getattr(n, "model", None)
    if not getattr(n, TIER_BUILT_ATTR, False) or model is None:
        return None
    try:
        sol = model.variables["ic_tier_q"].solution.to_pandas()
    except Exception:  # noqa: BLE001
        return None
    out = {}
    for key in spec["keys"]:
        for t in key["tiers"]:
            name = f"{key['key']}|{t['k']}"
            v = float(sol.loc[name])
            if not np.isfinite(v):
                return None
            out[name] = {"item": key["item"], "month": key["month"],
                         "inv_period": key["inv_period"], "tier": t["k"],
                         "rate_eur_per_mwh": t["rate_eur_per_mwh"], "q_mwh": v,
                         "items_hash": spec.get("items_hash"),
                         "hash_version": spec.get("hash_version")}
    return out


# ── energy-hub group contract (WP1.6) ──────────────────────────────────────


def add_group_terms(n) -> None:
    """Σ_members p_import[t] ≤ group cap, every snapshot. A pure constraint:
    no objective term, nothing to reconcile."""
    spec = getattr(n, GROUP_SPEC_ATTR, None)
    if not spec:
        return
    flow = n.model["Link-p"].sel(name=spec["members"]).sum("name")
    n.model.add_constraints(flow <= float(spec["cap_mw"]), name="ic_group_cap")


def _group_shares(n, spec: dict) -> dict | None:
    """Each member's share of the group's import energy (cost allocation is P3)."""
    p0 = n.links_t.p0
    if any(m not in p0.columns for m in spec["members"]):
        return None
    w = n.snapshot_weightings.objective
    if isinstance(n.snapshots, pd.MultiIndex):  # each period stands for its years
        years = n.investment_period_weightings["years"]
        w = w * years.reindex(n.snapshots.get_level_values(0)).to_numpy()
    energy = {m: float((w * p0[m].clip(lower=0.0)).sum()) for m in spec["members"]}
    total = sum(energy.values())
    if total <= 0:
        return {m: None for m in spec["members"]}
    return {m: e / total for m, e in energy.items()}


# ── solve-strategy guard and circulation risk (shared with preflight) ──────


def effective_strategy(solve_strategy: str | None, *, sclopf: bool = False,
                       multi_period: bool = False) -> str:
    """The strategy that will actually RUN: rolling falls back to full with
    SCLOPF or multi-period (WP1.5a review #7)."""
    strategy = solve_strategy or "full"
    if strategy == "rolling" and (sclopf or multi_period):
        return "full"
    return strategy


def refuse_windowed_terms(demand: dict | None, tier_spec: dict | None,
                          solve_strategy: str, multi_period: bool) -> None:
    """Monthly demand peaks and tier volumes cannot be carried across the
    windows of a rolling or multi-period myopic solve (P6)."""
    windowed = solve_strategy == "rolling" or (solve_strategy == "myopic" and multi_period)
    if not windowed:
        return
    if tier_spec is not None:
        raise CommercialBindingError(
            f"tiered rates with solve_strategy={solve_strategy!r} would restart the monthly "
            "volume per window (P6); not supported in P1")
    if demand is not None:
        raise CommercialBindingError(
            f"demand charges with solve_strategy={solve_strategy!r} would be re-created per "
            "window without the month's running peak (spec §5.2, P6); not supported in P1")


def tier_floor_eur_per_mwh(cfg: CommercialConfig) -> float:
    """The cheapest marginal import rate the convex tiers can charge (their
    first tier), summed over tiered items: tiers are LP terms, not adders."""
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if i.tiers and not _is_demand(i) and _lp_reason(i) is None
             and tiers_are_convex(i.tiers)]
    return float(sum(float(i.tiers[0].rate) * _KWH_PER_MWH for i in items))


def circulation_risk_snapshots(n, cfg: CommercialConfig, adders: dict[str, np.ndarray],
                               export_price: np.ndarray | None) -> int:
    """Snapshots where exporting pays more than importing through the
    cheapest charged import Link costs (base cost + adders + the first tier),
    so the LP could circulate power through the grid. Link efficiencies are
    not considered. `adders` are the unapplied per-side adders."""
    if cfg.export_link is None:
        return 0

    def base(link: str) -> np.ndarray:
        mc = n.links_t.marginal_cost
        if link in mc.columns:
            return mc[link].to_numpy(dtype=float)
        return np.full(len(n.snapshots), float(n.links.at[link, "marginal_cost"]))

    floor = tier_floor_eur_per_mwh(cfg)
    imp = np.min([base(link) for link in import_links(cfg)], axis=0) + adders["import"] + floor
    exp = base(cfg.export_link) + adders["export"]
    if export_price is not None:
        exp = exp - export_price
    return int(((imp + exp) < -1e-9).sum())


# ── materialisation (transient, committed on success) ──────────────────────


def materialise_poc_prices(n, commercial: dict | CommercialConfig | None,
                           *, log=None, solve_strategy: str = "full",
                           multi_period: bool = False) -> Applied:
    """Apply PoC energy prices for one solve (see module docstring).

    Always returns an `Applied`: with no commercial config it mutates nothing,
    and its `commit()` clears the persisted price frame, so a plain solve does
    not keep reporting a previous config's energy rows. `facts` holds the terms
    (`last_commercial_terms`), or is empty. Raises `CommercialBindingError`
    when the config cannot bind; a missing price is refused, never priced at 0.
    Validation runs before the first mutation.
    """
    if not commercial:
        applied = Applied()

        def clear() -> None:
            if hasattr(n.links_t, "get") and n.links_t.get(ENERGY_PRICE_ATTR) is not None:
                n.links_t[ENERGY_PRICE_ATTR] = pd.DataFrame(index=n.snapshots)
            n.meta.pop(META_LINKS, None)
            n.meta.pop(META_DEMAND, None)
            n.meta.pop(META_DEMAND_INFO, None)
            n.meta.pop(META_TIERS, None)
            n.meta.pop(META_GROUP, None)

        applied._commit.append(clear)
        return applied

    cfg = _parse(commercial)
    validate_for_network(n, cfg)
    adders, energy_items, not_in_lp, notes = _adders(n, cfg)
    demand, demand_items, demand_missing, demand_notes = _demand_spec(n, cfg)
    notes = notes + [x for x in demand_notes if x not in notes]
    tier_spec, tiered_items, nonconvex_items = _tier_spec(n, cfg)
    refuse_windowed_terms(demand, tier_spec, solve_strategy, multi_period)
    has_price = cfg.export_price_ref is not None
    price = _export_price(n, cfg) if has_price else None
    risk = circulation_risk_snapshots(n, cfg, adders, price)
    if has_price:
        adders["export"] = adders["export"] - price

    targets: dict[str, np.ndarray] = {}
    if adders["import"].any():
        for link in import_links(cfg):
            targets[link] = adders["import"]
    if cfg.export_link is not None and (has_price or adders["export"].any()):
        targets[cfg.export_link] = adders["export"]

    applied = Applied()
    mc = n.links_t.marginal_cost
    for link, add in targets.items():
        had = link in mc.columns
        old = mc[link].copy() if had else None
        base = old.to_numpy(dtype=float) if had else float(n.links.at[link, "marginal_cost"])
        mc[link] = base + add

        def undo(link=link, had=had, old=old) -> None:
            live = n.links_t.marginal_cost
            if had:
                live[link] = old
            elif link in live.columns:
                n.links_t.marginal_cost = live.drop(columns=[link])

        applied._undo.append(undo)

    solved_peaks: dict = {}
    if demand is not None:
        setattr(n, DEMAND_SPEC_ATTR, demand)

        def undo_demand() -> None:
            # Read the solved peaks BEFORE the spec goes: commit runs after undo.
            got = _read_demand_solution(n, demand)
            if got is not None:
                solved_peaks["v"] = got
            for attr in (DEMAND_SPEC_ATTR, DEMAND_BUILT_ATTR):
                if hasattr(n, attr):
                    delattr(n, attr)

        applied._undo.append(undo_demand)

    group = group_spec(cfg)
    if group is not None:
        setattr(n, GROUP_SPEC_ATTR, group)

        def undo_group() -> None:
            if hasattr(n, GROUP_SPEC_ATTR):
                delattr(n, GROUP_SPEC_ATTR)

        applied._undo.append(undo_group)

    solved_tiers: dict = {}
    if tier_spec is not None:
        setattr(n, TIER_SPEC_ATTR, tier_spec)

        def undo_tiers() -> None:
            got = _read_tier_solution(n, tier_spec)
            if got is not None:
                solved_tiers["v"] = got
            for attr in (TIER_SPEC_ATTR, TIER_BUILT_ATTR):
                if hasattr(n, attr):
                    delattr(n, attr)

        applied._undo.append(undo_tiers)

    def commit() -> None:
        if group is not None:
            n.meta[META_GROUP] = {**group, "energy_share": _group_shares(n, group)}
        else:
            n.meta.pop(META_GROUP, None)
        if "v" in solved_tiers:
            n.meta[META_TIERS] = solved_tiers["v"]
        else:
            n.meta.pop(META_TIERS, None)
        frame = pd.DataFrame({link: add for link, add in targets.items()}, index=n.snapshots)
        n.links_t[ENERGY_PRICE_ATTR] = frame
        n.meta[META_LINKS] = {"import": cfg.poc_link, "export": cfg.export_link,
                              "import_members": import_links(cfg),
                              "priced": sorted(targets), "energy_hash": energy_hash(n, cfg),
                              "hash_version": _H.HASH_VERSION,
                              # This solve records its connection agreement in
                              # `ic_connection` (P2 WP2.0); a P1 solve did not.
                              "agreement_recorded": True}
        if "v" in solved_peaks:
            n.meta[META_DEMAND] = solved_peaks["v"]
            n.meta[META_DEMAND_INFO] = demand["info"]
        else:
            n.meta.pop(META_DEMAND, None)
            n.meta.pop(META_DEMAND_INFO, None)

    applied._commit.append(commit)

    if log is not None:
        log(f"[COMMERCIAL] PoC prices applied on {sorted(targets)} "
            f"({len(energy_items)} energy item(s); {len(not_in_lp)} left to later bindings"
            + (f"; WARNING {risk} snapshot(s) pay more to export than to import" if risk else "")
            + ")")
    applied.facts = {
        "poc_link": cfg.poc_link, "export_link": cfg.export_link,
        "import_links": import_links(cfg), "priced_links": sorted(targets), "energy_items": energy_items,
        "demand_items": demand_items, "demand_not_established_months": demand_missing,
        "demand_partial_months": (demand or {}).get("info", {}).get("partial_months", []),
        "tiered_items": tiered_items, "nonconvex_tier_items": nonconvex_items,
        "not_in_lp": not_in_lp, "notes": notes, "timezone": cfg.timezone,
        "simultaneous_flow_risk_snapshots": risk,
        "export_price_ref": (cfg.export_price_ref.model_dump(mode="json")
                             if cfg.export_price_ref is not None else None)}
    return applied


def _wrap_with_commercial_bindings(network, user_fn, cfg, log_queue=None):
    """`extra_functionality` hook for LP-level commercial terms (spec §5.1).

    Same closure/chaining shape as `_wrap_with_capex_budget`: the user's
    callback runs first, then the commercial terms. WP1.4a adds the connection
    capacity fee on the PoC Link's `p_nom` (`connection.add_fee_term`); peaks,
    ratchets, tiers and group caps (WP1.5-WP1.6) join here. Returns `user_fn`
    unchanged when there is no commercial config.
    """
    if not getattr(cfg, "commercial", None):
        return user_fn

    def fn(n, sns):
        if user_fn is not None:
            user_fn(n, sns)
        from services.commercial import connection  # lazy: connection imports this module

        connection.add_fee_term(n)
        add_demand_terms(n)
        add_tier_terms(n)
        add_group_terms(n)

    return fn


# ── cost rows (persisted data) ─────────────────────────────────────────────


def solved_import_links(solved: dict | None) -> list[str]:
    """The import Links a committed solve charged (`ic_poc_links`); records
    written before group pricing name the PoC alone."""
    solved = solved or {}
    members = solved.get("import_members")
    if members:
        return list(members)
    return [solved["import"]] if solved.get("import") else []


def energy_cost_rows(n, commercial: dict | None) -> dict | None:
    """`energy_import` / `energy_export` for `cost_breakdown`: the tariff (and,
    on export, − the export price) the last successful solve charged, from the
    PERSISTED `links_t["ic_energy_price"]`, `n.meta["ic_poc_links"]` and the
    dispatch. They are identical after a reload.

    The prices were applied transiently, so these amounts are NOT inside
    `n.statistics()` OPEX: the caller ADDS them to the total. A value that
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
                            or cfg.export_link != solved.get("export")
                            or sorted(import_links(cfg)) != sorted(solved_import_links(solved))
                            or (solved.get("energy_hash") is not None
                                and solved["energy_hash"] != energy_hash(
                                    n, cfg, _H.version_of(solved)))):
        flags.append("config_changed_since_solve")
    if not commercial:
        flags.append("config_cleared_since_solve")
    w = n.snapshot_weightings.objective
    if isinstance(n.snapshots, pd.MultiIndex):
        years = n.investment_period_weightings["objective"]
        w = w * years.reindex(n.snapshots.get_level_values(0)).to_numpy()
    prices = _frame(n, ENERGY_PRICE_ATTR)

    def p0_of(link):
        return n.links_t.p0[link] if link in n.links_t.p0.columns else None

    def one(link: str) -> float | None:
        if link not in prices.columns:
            # 0.0 only for a Link the solve deliberately left unpriced; a
            # priced Link whose record is gone is not established (ADR-0001).
            if link in n.links.index and link not in (solved.get("priced") or []):
                return 0.0
            return None
        add = prices[link]
        p0 = p0_of(link)
        if p0 is None or add.isna().any():
            return None
        return float((w * p0 * add).sum())

    def row(links: list[str], label: str) -> float | None:
        if not links:
            return None
        parts = [one(link) for link in links]
        if any(v is None for v in parts):
            flags.append(f"{label}_not_established")
            return None
        return float(sum(parts))

    imp_links, exp_link = solved_import_links(solved), solved.get("export")
    out = {"energy_import": row(imp_links, "energy_import"),
           "energy_export": row([exp_link] if exp_link else [], "energy_export"),
           "included_in_total": True}
    imp_p0 = [p0_of(link) for link in imp_links]
    if imp_links and exp_link and all(v is not None for v in imp_p0) \
            and p0_of(exp_link) is not None:
        both = (sum(imp_p0) > 1e-6) & (p0_of(exp_link) > 1e-6)
        if bool(both.any()):
            flags.append("simultaneous_import_export")
            out["simultaneous_snapshots"] = int(both.sum())
    out["flags"] = flags
    return out
