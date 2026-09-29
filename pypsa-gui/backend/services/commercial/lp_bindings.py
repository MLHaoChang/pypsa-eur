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

**LP coverage (after P2 WP2.1c):** everything the engine bills is an LP term
except fixed and per-day items (§5.5), `per_kva_year` capacity items, and
contract settlements other than WP2.2d (`not_in_lp` / `settlement_only`, each
with a reason). Tariff capacity items (WP2.1c-iii): on the CONTRACTED capacity,
a term on the PoC's `p_nom` when it is extendable
(`connection.capacity_fee_coefficient`, the connection fee's rule), else a
constant reported as `tariff_capacity_fixed` outside the reconciled total; on
`peak_import`, an annual peak per (period, local year) over settlement-interval
means. A tariff capacity item AND a connection capacity fee on one PoC is
refused (`commercial.capacity_double_count`).

Demand (P2 WP2.1c-i): every ratchet mode (range, cyclic range, designated
months — `_ratchet_months`, the engine's rule) is a linear row on
`ic_billed_demand`; demand tiers are stacked `ic_demand_tier_q` segments summing
to it (rising rates; a first threshold above 0 adds a free segment), and
falling tiers are priced at the first tier, noted `nonconvex_tier`. The demand
record carries the segments (`tiers`, open top width None) and
`demand_amount(rec)` prices it; the demand info records `lp_recipe` so a record
from before this recipe is told apart (`demand_only_newly_bound`).

Energy tiers (P2 WP2.1c-ii): plain and windowed (`tier_rates`) tiers share one
LP form — per (item, period, month) key, `q[period, segment] ≥ 0` with Σ_seg
q = the period's volume and Σ_period q ≤ the segment's width; a first
threshold above 0 adds a free segment (the engine bills that volume at 0; P1
charged it at the first rate). Rates rising in EVERY period are convex; the
engine's proportional URDB split is feasible for this LP, so the LP optimum is
≤ the bill (WP2.3 `tier_allocation` ≥ 0). Any other shape is an adder at one
tier per period: the tier the same month a year earlier landed in
(`meter_history_energy_kwh`, first period only), else the first; a free tier
falls back to the period's first charged rate. `ic_poc_links.lp_recipe`
(`LP_RECIPE`) dates a solve, so one made before a recipe bound more of an
unchanged config reads `energy_recipe_changed` / `demand_recipe_changed`, not
a drift.

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
    _HOURS_PER_YEAR,
    _period_index,
    is_windowed_tiered,
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
META_CAPACITY = "ic_tariff_capacity"
META_PPA = "ic_ppa"                    # the dispatch PPAs a solve bound (WP2.2d)
PPA_PRICE_ATTR = "ic_ppa_price"        # generators_t: €/MWh added per Generator
CAPACITY_SPEC_ATTR = "_ic_capacity_spec"   # transient: set by apply, read by the LP wrapper
CAPACITY_BUILT_ATTR = "_ic_capacity_built"  # transient: set by the LP wrapper
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
        # DE Leistungspreis) is a capacity charge, not a demand one. LP terms
        # since P2 WP2.1c-iii (`_capacity_spec`); per kVA stays out (the plan's
        # LP coverage), as do the shapes the engine does not bill.
        if item.unit == "per_kva_year":
            return "per_kva_year_not_in_lp"
        if item.unit != "per_kw_year":
            return f"unit_{item.unit}_not_capacity"
        if item.direction != "cost":
            return "capacity_revenue_not_supported"
        if item.tiers:
            return "tiers_on_capacity_not_supported"
        p = item.periods[0]
        if len(item.periods) != 1 or p.months or p.weekdays or p.start_hour is not None:
            return "capacity_with_windows"
        if item.measured_on not in ("import", "peak_import"):
            return f"capacity_measured_on_{item.measured_on}"
        return None
    if _is_demand(item):
        # Demand tiers (stacked on the billed demand) and every ratchet mode are
        # LP terms since P2 WP2.1c-i.
        if item.unit != "per_kw_month":
            return f"unit_{item.unit}_not_demand"
        if item.direction != "cost":
            return "demand_revenue_not_supported"
        if item.measured_on == "export":
            return "export_demand_not_supported"
        return None
    if item.tiers:
        # WP1.5c: tiers on cumulative monthly import volume; per-period tier
        # rates (`tier_rates`, WP2.1a-ii) are LP terms since WP2.1c-ii. Tiers in
        # windows WITHOUT per-period rates cannot occur after the P1 migration
        # (`TariffItem._migrate_p1_windowed_tiers`) and stay refused.
        p = item.periods[0]
        if not is_windowed_tiered(item) and (len(item.periods) != 1 or p.months
                                             or p.weekdays or p.start_hour is not None):
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


# The LP recipe of a solve, recorded in `ic_poc_links` (absent ⇒ 1):
#   2 — demand tiers and every ratchet mode bound (WP2.1c-i);
#   3 — windowed energy tiers bound (convex: LP terms; non-convex: adders at
#       the predicted tier), the energy history prices non-convex tiers
#       (WP2.1c-ii).
#   4 — tariff capacity items bound (WP2.1c-iii);
#   5 — changes_dispatch PPAs bound (WP2.2d).
LP_RECIPE = 5
PPA_DISPATCH_RECIPE = 5
WINDOWED_TIERS_RECIPE = 3
CAPACITY_RECIPE = 4


def energy_recipe_of(rec: dict | None) -> int:
    return int((rec or {}).get("lp_recipe") or 1)


def item_tier_rates(item: TariffItem) -> list[list[float]]:
    """The tier rates per period index: each period's `tier_rates` for a
    windowed item, else one set (the item's single catch-all period)."""
    if is_windowed_tiered(item):
        return [list(p.tier_rates) for p in item.periods]
    return [[float(t.rate) for t in item.tiers]]


def item_tiers_convex(item: TariffItem) -> bool:
    """Rising marginal rates in EVERY period (WP2.1c-ii)."""
    return all(all(b >= a for a, b in zip(r, r[1:])) for r in item_tier_rates(item))


def _energy_tiered(cfg: CommercialConfig, recipe: int = LP_RECIPE) -> list:
    """The tiered energy items the LP binds under `recipe`."""
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if i.tiers and not _is_demand(i) and i.kind != "capacity"
             and _lp_reason(i) is None]
    return (items if recipe >= WINDOWED_TIERS_RECIPE
            else [i for i in items if not is_windowed_tiered(i)])


def tier_items_hash(cfg: CommercialConfig, version: int = _H.HASH_VERSION,
                    recipe: int = LP_RECIPE) -> str | None:
    """The hash of the convex tiered items a solve would bind (drift check)."""
    items = [i for i in _energy_tiered(cfg, recipe) if item_tiers_convex(i)]
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
        if item.kind == "capacity":
            if item.periods[0].rate < 0:
                # A capacity credit would pay the LP to build the PoC to its
                # max, or leave the annual peak unbounded (review #2).
                raise CommercialBindingError(
                    f"capacity item {item.id!r} has a negative rate; a capacity credit is "
                    "not a capacity charge")
            continue  # a p_nom or annual-peak term, built by `_capacity_spec`
        if _is_demand(item):
            neg = [p.name for p in item.periods if p.rate < 0
                   or any(r < 0 for r in (p.tier_rates or []))]
            if item.tiers and any(t.rate < 0 for t in item.tiers):
                neg.append("tiers")
            if neg:
                # A negative €/kW would pay the LP to raise its peak without
                # bound (review round 3 #5).
                raise CommercialBindingError(
                    f"demand item {item.id!r} has a negative rate in period(s) {neg}; a "
                    "demand credit is not a peak charge")
            continue  # a peak term, built by `_demand_spec`
        if item.tiers:
            if item_tiers_convex(item):
                continue  # stacked volume terms, built by `_tier_spec`
            # Non-convex (spec §5.3): each snapshot priced at ONE tier of its
            # period — the tier the same month a year earlier landed in
            # (`meter_history_energy_kwh`), else the first; billed exactly.
            price, _got = _predicted_tier_price(n, cfg, item, local)
            adders["import"] += price
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


def predicted_tiers(n, cfg: CommercialConfig) -> dict[str, dict[str, int]]:
    """{item: {month: tier}} for the non-convex tiered items priced from the
    energy history (the facts' `nonconvex_tier_predicted`)."""
    local = _local_clock(n.snapshots, cfg.timezone)
    out = {}
    for item in _energy_tiered(cfg):
        if not item_tiers_convex(item):
            _price, got = _predicted_tier_price(n, cfg, item, local)
            if got:
                out[item.id] = got
    return out


def _tier_position(thresholds_kwh: list[float], volume_kwh: float) -> int:
    """The tier `volume_kwh` lands in (−1: below the first threshold, free)."""
    k = -1
    for j, th in enumerate(thresholds_kwh):
        if volume_kwh >= th:
            k = j
    return k


def _predicted_tier_price(n, cfg: CommercialConfig, item: TariffItem,
                          local: pd.DatetimeIndex) -> tuple[np.ndarray, dict[str, int]]:
    """€/MWh per snapshot for a non-convex tiered item, and {month: tier} for
    the months priced from history. History is the site's past: it seeds the
    FIRST investment period only (as the ratchet's), later periods take the
    first tier."""
    months = np.asarray(local.strftime("%Y-%m"))
    frag = (_period_index(item, local) if is_windowed_tiered(item)
            else np.zeros(len(local), dtype=int))
    if (frag < 0).any():
        raise CommercialBindingError(
            f"tariff item {item.id!r} has no period covering {int((frag < 0).sum())} "
            "snapshot(s); add a catch-all period")
    rates = item_tier_rates(item)
    thresholds = [float(t.threshold) for t in item.tiers]
    inv = (np.asarray(n.snapshots.get_level_values(0)) if isinstance(n.snapshots, pd.MultiIndex)
           else np.full(len(n.snapshots), None, dtype=object))
    first = None if not isinstance(n.snapshots, pd.MultiIndex) else min(n.investment_periods)
    hist = cfg.meter_history_energy_kwh
    tier = np.zeros(len(local), dtype=int)
    got: dict[str, int] = {}
    for m in sorted(set(months[inv == first] if first is not None else months)):
        before = (pd.Period(m, freq="M") - 12).strftime("%Y-%m")
        if before not in hist:
            continue
        k = _tier_position(thresholds, float(hist[before]))
        tier[(months == m) & ((inv == first) if first is not None else True)] = k
        got[m] = k
    table = np.array([[0.0] + list(r) for r in rates])  # column 0: below the first threshold
    price = table[frag, tier + 1]
    # A free tier (predicted, or the first) would take the item out of the LP:
    # the period's first CHARGED rate instead, as a non-convex demand key
    # (WP2.1c-i review #1; a deviation from "first tier" only when it is free).
    first_charged = np.array([next((x for x in r if x != 0), 0.0) for r in rates])
    price = np.where(price == 0, first_charged[frag], price)
    return price * _KWH_PER_MWH, got


# Where each contract type's assets may live (P2 WP2.2c): what `contracts.settle`
# can read for it. A DR contract names loads, never assets (P5).
_CONTRACT_ASSET_CLASSES = {"ppa": ("generators",), "cfd": ("generators",),
                           "lease": ("generators", "storage_units", "stores", "links"),
                           "eaas": ("generators", "storage_units", "links")}


def same_party(a: str | None, b: str | None) -> bool:
    """Party names compare trimmed and case-insensitive (review 2.2c #3)."""
    return a is not None and b is not None and a.strip().casefold() == b.strip().casefold()


def _meter_sides(n, cfg: CommercialConfig) -> tuple[set[str], set[str]]:
    """(site-side buses, grid-side buses reached from them). The search runs
    from the import members' site side (bus1) over lines, transformers and
    Links other than the meter's; the meter's grid side (import members' bus0,
    the export Link's bus1) is never entered (review 2.2c round 2 #2). A
    grid-side bus the search reaches is a connection that bypasses the meter."""
    meter = set(import_links(cfg)) | ({cfg.export_link} if cfg.export_link else set())
    starts = [str(n.links.at[m, "bus1"]) for m in import_links(cfg) if m in n.links.index]
    grid = {str(n.links.at[m, "bus0"]) for m in import_links(cfg) if m in n.links.index}
    if cfg.export_link and cfg.export_link in n.links.index:
        grid.add(str(n.links.at[cfg.export_link, "bus1"]))
    grid -= set(starts)
    edges: dict[str, set[str]] = {}

    def join(a, b):
        if a and b and a != "nan" and b != "nan":
            edges.setdefault(a, set()).add(b)
            edges.setdefault(b, set()).add(a)

    for comp, ends in (("lines", ("bus0", "bus1")), ("transformers", ("bus0", "bus1")),
                       ("links", ("bus0", "bus1", "bus2", "bus3", "bus4"))):
        df = getattr(n, comp)
        cols = [c for c in ends if c in df.columns]
        for name, row in df[cols].iterrows():
            if comp == "links" and name in meter:
                continue
            buses = [str(row[c]).strip() for c in cols if str(row[c]).strip()]
            for x in buses[1:]:
                join(buses[0], x)
    seen, bypass, todo = set(), set(), list(starts)
    while todo:
        b = todo.pop()
        if b in seen or b in bypass:
            continue
        if b in grid:
            bypass.add(b)
            continue
        seen.add(b)
        todo.extend(edges.get(b, ()))
    return seen, bypass


def site_generators(n, cfg: CommercialConfig) -> list[str]:
    """The Generators BEHIND the commercial meter (`_meter_sides`). One
    definition for the preflight and the settlement inputs (review 2.2c #3)."""
    seen, _ = _meter_sides(n, cfg)
    return [str(g) for g in n.generators.index if str(n.generators.at[g, "bus"]) in seen]


def meter_bypass_buses(n, cfg: CommercialConfig) -> list[str]:
    """Grid-side buses reachable from the site side without the meter."""
    return sorted(_meter_sides(n, cfg)[1])


def contract_problems(n, cfg: CommercialConfig) -> list[tuple[str, str, bool]]:
    """(code, message, changes_dispatch) for each contract naming what the
    network cannot settle (`commercial.contract_asset_missing` /
    `commercial.contract_tariff_mismatch`). A settlement-only contract does
    not shape the LP: it is refused when the config is bound, a WARNING at
    preflight and solve time; a dispatch-changing one is an error everywhere
    (review 2.2c #4)."""
    out: list[tuple[str, str, bool]] = []
    for c in cfg.contracts:
        dispatch = c.type == "ppa" and bool(getattr(c, "changes_dispatch", False))
        allowed = _CONTRACT_ASSET_CLASSES.get(c.type)
        if allowed is not None:
            missing = [a for a in c.asset_ids
                       if not any(a in getattr(n, cls).index for cls in allowed)]
            if missing:
                out.append(("commercial.contract_asset_missing",
                            f"{c.type} contract {c.id!r} names {missing}, which are not "
                            f"{' / '.join(allowed)} of this network", dispatch))
        if c.type == "dr":
            if c.asset_ids:
                out.append(("commercial.contract_asset_missing",
                            f"DR contract {c.id!r} names assets {list(c.asset_ids)}; DR on "
                            "assets arrives in P5 — name load_ids", False))
            missing = [l for l in c.load_ids if l not in n.loads.index]
            if missing:
                out.append(("commercial.contract_asset_missing",
                            f"DR contract {c.id!r} names loads {missing} that are not in this "
                            "network", False))
        if c.type == "retail":
            tid = cfg.import_tariff.id if cfg.import_tariff is not None else None
            if c.tariff_id != tid:
                out.append(("commercial.contract_tariff_mismatch",
                            f"retail contract {c.id!r} names tariff {c.tariff_id!r}, but the "
                            f"import tariff is {tid!r}", False))
    return out


def validate_for_network(n, cfg: CommercialConfig | dict, *,
                         refuse_settlement_contracts: bool = False) -> None:
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
    for code, msg, dispatch in contract_problems(n, cfg):
        # Binding refuses them all; a solve only what shapes its LP.
        if dispatch or refuse_settlement_contracts:
            raise CommercialBindingError(f"{code}: {msg}")
    _ppa_dispatch_spec(n, cfg)   # a dispatch PPA that cannot bind is refused here too
    if cfg.demand_items:
        # Not implemented in P1: every demand item of the tariff is charged, so
        # a selection would be silently ignored (Phase 1 gate finding #4).
        raise CommercialBindingError(
            "commercial.demand_items (a selection of demand items) is not supported in P1; "
            "every demand item of the tariff is charged — remove the unwanted items instead")
    if cfg.import_tariff_ref is not None:
        # A Library tariff (P2 WP2.4a): `PUT /solver_config` resolves it into
        # the inline copy the solve uses; a copy that is missing, or that no
        # longer hashes to the ref, is not what the ref names.
        ref = cfg.import_tariff_ref
        if ref.kind != "tariff":
            raise CommercialBindingError(
                f"import_tariff_ref names a Library {ref.kind}, not a tariff")
        if cfg.import_tariff is None:
            raise CommercialBindingError(
                f"import_tariff_ref names Library tariff {ref.id!r} v{ref.version}, which is not "
                "resolved into import_tariff; re-apply the commercial config")
        if _H.library_item_digest(cfg.import_tariff) != ref.hash:
            raise CommercialBindingError(
                f"the inline import_tariff is not Library tariff {ref.id!r} v{ref.version} "
                "(content differs from the ref's hash); re-apply the config to resolve the "
                "ref, or drop import_tariff_ref to keep the edited tariff")
    if cfg.import_tariff_id is not None and cfg.import_tariff is None:
        raise CommercialBindingError(
            "import_tariff_id is a label: a Library tariff is named by import_tariff_ref "
            "(P2 WP2.4a); carry the tariff inline as import_tariff, or set "
            "import_tariff_ref")
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


def _ratchet_months(ratchet, month: str) -> list[str]:
    """The months whose ACTUAL peak the ratchet reads for `month`, as the
    engine's `_ratchet_floor_prior` (WP2.1a-iii): range — the lookback months
    before it; cyclic range — the same, wrapping inside its rate year; months —
    the designated months of its rate year."""
    year, mm = int(month[:4]), int(month[5:])
    if ratchet.months is not None:
        return [f"{year}-{d:02d}" for d in ratchet.months]
    if ratchet.cyclic_year:
        return [f"{year}-{((mm - back - 1) % 12) + 1:02d}"
                for back in range(1, ratchet.lookback_months + 1)]
    here = pd.Period(month, freq="M")
    return [(here - back).strftime("%Y-%m") for back in range(1, ratchet.lookback_months + 1)]


def _demand_segments(item: TariffItem, rates: list[float],
                     seen_kw: float | None = None) -> tuple[list[dict] | None, float]:
    """(tier segments of the billed demand in MW and €/MW, or None when the
    rates are not rising; the €/kW rate a key without segments is priced at).
    Rising rates are convex: stacked segments, with a free one below a first
    threshold above 0, as `_tier_cost_with` bills it.

    Non-convex rates price the WHOLE billed kW at one marginal rate: the tier
    the same month a year earlier (`seen_kw`, metered history) landed in, else
    the FIRST NON-ZERO rate — a free tier (predicted or first) must not take
    the charge out of the LP (WP2.1c-i review #1, round 2). The engine bills the tiers exactly; the gap
    (incl. the free part below a first threshold above 0, over-priced here) is
    the item's `nonconvex_tier` cause."""
    if not all(b >= a for a, b in zip(rates, rates[1:])):
        first_charged = float(next((r for r in rates if r != 0), 0.0))
        if seen_kw is not None:
            k = -1
            for j, t in enumerate(item.tiers):
                if seen_kw >= t.threshold:
                    k = j
            predicted = 0.0 if k < 0 else float(rates[k])
            # A last-year peak inside a free tier would price the key at 0 and
            # stop the LP shaving (round 2 residue): the first charged rate.
            return None, (predicted if predicted != 0 else first_charged)
        return None, first_charged
    th = [t.threshold / _KWH_PER_MWH for t in item.tiers] + [np.inf]
    segs = [{"width_mw": th[0], "eur_per_mw": 0.0}] if th[0] > 0 else []
    segs += [{"width_mw": float(th[k + 1] - th[k]), "eur_per_mw": float(r) * _KWH_PER_MWH}
             for k, r in enumerate(rates)]
    return segs, 0.0


def demand_amount(rec: dict) -> float:
    """A committed demand key's charge: its tier segments filled in order (the
    LP's optimum on rising rates), else €/MW × billed MW."""
    billed = float(rec.get("billed_mw", rec["peak_mw"]))
    segs = rec.get("tiers")
    if not segs:
        return float(rec["eur_per_mw"]) * billed
    out, left = 0.0, billed
    for sg in segs:
        q = left if sg["width_mw"] is None else min(left, float(sg["width_mw"]))
        out += q * float(sg["eur_per_mw"])
        left -= q
        if left <= 0:
            break
    return out


def demand_lp_items(cfg: CommercialConfig) -> list:
    """The demand items the LP carries as peak terms (a `peak_import`
    capacity item is a capacity charge, never a demand key)."""
    return [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
            if _is_demand(i) and i.kind != "capacity" and _lp_reason(i) is None]


def _demand_spec(n, cfg: CommercialConfig) -> tuple[dict | None, list[str], list[str], list[str]]:
    """(spec for the LP wrapper, demand item ids, months not established).

    One key per (item, period window, investment period, local month) with at
    least one snapshot; the months between the first and last snapshot that
    have none are listed as not established (spec §5.2), and never weighted
    from their neighbours."""
    items = demand_lp_items(cfg)
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
    tier_notes: list[str] = []
    first_inv = (min(n.investment_periods) if isinstance(n.snapshots, pd.MultiIndex) else None)
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
                    segments = None
                    if item.tiers:
                        # Tiers replace the period rate (the engine's `_charged`):
                        # a window is free only if all its tier rates are 0.
                        rates_k = (list(item.periods[int(frag[pos][0])].tier_rates)
                                   if is_windowed_tiered(item)
                                   else [t.rate for t in item.tiers])
                        if not any(r != 0 for r in rates_k):
                            continue
                        seen_kw = (cfg.meter_history_peaks_kw.get(
                            (pd.Period(m, freq="M") - 12).strftime("%Y-%m"))
                            if p is None or p == first_inv else None)
                        segments, rate = _demand_segments(item, rates_k, seen_kw)
                        if segments is None:
                            for note in ("nonconvex_tier", f"nonconvex_tier:{item.id}"):
                                if note not in tier_notes:
                                    tier_notes.append(note)
                    elif rate == 0:
                        continue
                    _, group = np.unique(interval[pos], return_inverse=True)
                    keys.append({
                        "group": group,
                        "key": f"{item.id}|{name}|{'' if p is None else p}|{m}",
                        "item": item.id, "period": name, "month": m,
                        "inv_period": None if p is None else int(p),
                        "eur_per_mw": rate * _KWH_PER_MWH, "tiers": segments,
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
        for m in _ratchet_months(r, k["month"]):
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
    notes += [x for x in tier_notes if x not in notes]
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
                     "hash_version": DEMAND_HASH_VERSION,
                     # The LP recipe (P2 WP2.1c-i: demand tiers and every
                     # ratchet mode bound); absent on older records.
                     "lp_recipe": DEMAND_LP_RECIPE}}
    return spec, [i.id for i in items], missing, notes


def _energy_priced(cfg: CommercialConfig, recipe: int = LP_RECIPE) -> list:
    """The items priced per interval (adders) under `recipe`: energy items and
    non-convex tiers."""
    tiered = {i.id for i in _energy_tiered(cfg, recipe)}
    return [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
            if not _is_demand(i) and i.kind != "capacity" and _lp_reason(i) is None
            and (not i.tiers or (i.id in tiered and not item_tiers_convex(i)))]


def energy_hash(n, cfg: CommercialConfig, version: int = _H.HASH_VERSION,
                recipe: int = LP_RECIPE) -> str:
    """What decides the energy rows besides the dispatch: the per-interval
    energy items (incl. non-convex tiers priced at one tier), the export price
    version, the site clock, the charged Links and the axis (Phase 1 gate
    binding condition 2); with `recipe` the item set a solve under that recipe
    priced. The energy history enters only when it prices a non-convex tier,
    so a config without one hashes as before (WP2.1c-ii)."""
    items = _energy_priced(cfg, recipe)
    payload = {"items": _H.canonical(items, version=version),
               "export_price_ref": (_H.canonical(cfg.export_price_ref, version=version)
                                    if cfg.export_price_ref is not None else None),
               "timezone": cfg.timezone, "import": sorted(import_links(cfg)),
               "export": cfg.export_link, "axis": _axis_hash(n.snapshots)}
    if (recipe >= WINDOWED_TIERS_RECIPE and cfg.meter_history_energy_kwh
            and any(i.tiers for i in items)):
        payload["energy_history"] = sorted(cfg.meter_history_energy_kwh.items())
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def energy_record_state(n, cfg: CommercialConfig, rec: dict | None) -> str | None:
    """How the current config relates to the solve's `ic_poc_links` record:
    None — unchanged; "config" — the energy terms changed; "recipe" — unchanged,
    but this recipe binds more of it than the one the record was solved with
    (a windowed tier item before WP2.1c-ii): re-solve, not a drift."""
    if not rec or rec.get("energy_hash") is None:
        return None
    r, v = energy_recipe_of(rec), _H.version_of(rec)
    if rec["energy_hash"] != energy_hash(n, cfg, v, recipe=r):
        return "config"
    if r < WINDOWED_TIERS_RECIPE and (
            [i.id for i in _energy_priced(cfg, r)] != [i.id for i in _energy_priced(cfg)]
            or [i.id for i in _energy_tiered(cfg, r)] != [i.id for i in _energy_tiered(cfg)]
            or _repriced_since_recipe_2(cfg)):
        return "recipe"
    return None


def _repriced_since_recipe_2(cfg: CommercialConfig) -> bool:
    """Whether recipe 3 prices a tier item the older recipes also bound
    DIFFERENTLY, so the old dispatch is not this recipe's (WP2.1c-ii review
    #1, #2): a non-convex item with a free tier (it had a 0 adder) or with
    energy history (the predicted tier), or a convex item whose first threshold
    is above 0 (the free volume was charged at the first rate)."""
    for item in _energy_tiered(cfg, recipe=2):
        rates = item_tier_rates(item)
        if item_tiers_convex(item):
            if item.tiers[0].threshold > 0:
                return True
        elif cfg.meter_history_energy_kwh or any(r[0] == 0 for r in rates):
            return True
    return False


DEMAND_HASH_VERSION = _H.HASH_VERSION  # recorded in the demand info (recipe of items_hash)
DEMAND_LP_RECIPE = 2  # recorded in the demand info: 2 = WP2.1c-i (tiers, all ratchet modes)


def demand_hash(n, cfg: CommercialConfig, items, version: int = _H.HASH_VERSION) -> str:
    """What decides the billed demand besides the items: the site clock, the
    meter history, the peak floors and the snapshot axis (review round 3 #4)."""
    raw = json.dumps({"items": _items_hash(items, version), "timezone": cfg.timezone,
                      "history": sorted(cfg.meter_history_peaks_kw.items()),
                      "floors": sorted(cfg.initial_peak_lower_bound.items()),
                      "axis": _axis_hash(n.snapshots)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _bound_before_wp21c(item) -> bool:
    """Whether the recipe before WP2.1c-i bound this demand item in the LP
    (it left out demand tiers and designated-month / cyclic ratchets)."""
    r = item.ratchet
    return not item.tiers and not (r is not None and (r.months is not None or r.cyclic_year))


def demand_all_newly_bound(links_rec: dict | None, wanted_items: list) -> bool:
    """A solve with NO demand record, made before WP2.1c-i (its `ic_poc_links`
    has no `lp_recipe` ≥ 2), whose current demand items are all of a kind that
    recipe could not bind: it wrote no record because it bound nothing — a
    recipe change, not a drift (WP2.1c-i review #2)."""
    return (bool(links_rec) and int(links_rec.get("lp_recipe") or 1) < 2
            and bool(wanted_items)
            and not any(_bound_before_wp21c(i) for i in wanted_items))


def demand_only_newly_bound(n, info: dict, wanted_items: list, cfg) -> bool:
    """True when a demand record from before WP2.1c-i differs from the current
    config only by the items that recipe could not bind: the rest hashes as
    solved, so the config is unchanged and a re-solve binds the new terms.

    An item of such a kind ADDED after the old solve reads the same way (the
    records cannot tell them apart); both mean re-solve (review #6)."""
    if cfg is None or info.get("lp_recipe") is not None:
        return False
    old = [i for i in wanted_items if _bound_before_wp21c(i)]
    if len(old) == len(wanted_items) or sorted(i.id for i in old) != sorted(info.get("items", [])):
        return False
    if not old:
        return False
    return info.get("items_hash") is None or \
        demand_hash(n, cfg, old, _H.version_of(info)) == info["items_hash"]


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
    # Rising demand tiers (P2 WP2.1c-i): Σ_k q[key,k] = billed[key], 0 ≤ q ≤
    # width_k, objective += Σ w_obj · €/MW_k · q (convex: the cheap segments
    # fill first, as the engine's cumulative tiers bill).
    seg_names, seg_upper, seg_coef, seg_of = [], [], [], []
    for w, k in zip(weight, spec["keys"]):
        for j, sg in enumerate(k.get("tiers") or []):
            seg_names.append(f"{k['key']}|{j}")
            seg_upper.append(sg["width_mw"])
            seg_coef.append(w * sg["eur_per_mw"])
            seg_of.append(k["key"])
    if seg_names:
        q = m.add_variables(lower=0, upper=xr.DataArray(seg_upper, coords={"seg": seg_names},
                                                          dims="seg"),
                            name="ic_demand_tier_q", coords=[pd.Index(seg_names, name="seg")])
        for i, key in enumerate(dict.fromkeys(seg_of)):
            mine = [s for s, o in zip(seg_names, seg_of) if o == key]
            m.add_constraints(q.sel(seg=mine).sum() - billed.sel(key=key) == 0,
                              name=f"ic_demand_tier_sum_{i}")
        m.objective += (q * xr.DataArray(seg_coef, coords={"seg": seg_names}, dims="seg")).sum()
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
    peaks_now: dict[str, float] = {}
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
        if k.get("tiers"):
            # The open top segment is recorded as width None (JSON has no inf;
            # the record rides network.nc).
            out[k["key"]]["tiers"] = [
                {"width_mw": float(sg["width_mw"]) if np.isfinite(sg["width_mw"]) else None,
                 "eur_per_mw": float(sg["eur_per_mw"])} for sg in k["tiers"]]
            peaks_now[k["key"]] = v
    # A tiered key's billed demand is free inside a 0-rate segment: record the
    # rule's value (max of the peak and its ratchet rows), which the bill uses
    # and which costs the same (WP2.1c-i).
    for key, v in peaks_now.items():
        floor = v
        for r in spec.get("ratchets", []):
            if r["key"] != key:
                continue
            if "of_key" in r:
                floor = max(floor, r["share"] * out[r["of_key"]]["peak_mw"])
            else:
                floor = max(floor, r["share"] * r["floor_mw"])
        out[key]["billed_mw"] = floor
    return out


# ── convex tiers (WP1.5c) ──────────────────────────────────────────────────


def _tier_spec(n, cfg: CommercialConfig) -> tuple[dict | None, list[str], list[str]]:
    """(spec, convex tiered item ids, non-convex tiered item ids). One key per
    (item, investment period, local month) with snapshots. Each key lists the
    item's PERIODS present in it (one for a plain tiered item; the matched
    `tier_rates` period for a windowed one, WP2.1c-ii) with their positions and
    €/MWh per segment, and the SEGMENTS (MWh widths) the periods share: a free
    one below a first threshold above 0 (the engine bills that volume at 0),
    then one per tier."""
    items = _energy_tiered(cfg)
    convex = [i for i in items if item_tiers_convex(i)]
    nonconvex = [i.id for i in items if not item_tiers_convex(i)]
    if not convex:
        return None, [], nonconvex
    local = _local_clock(n.snapshots, cfg.timezone)
    months = np.asarray(local.strftime("%Y-%m"))
    inv = (np.asarray(n.snapshots.get_level_values(0)) if isinstance(n.snapshots, pd.MultiIndex)
           else np.full(len(n.snapshots), None, dtype=object))
    keys: list[dict] = []
    for item in convex:
        windowed = is_windowed_tiered(item)
        frag = _period_index(item, local) if windowed else np.zeros(len(local), dtype=int)
        if (frag < 0).any():
            raise CommercialBindingError(
                f"tariff item {item.id!r} has no period covering {int((frag < 0).sum())} "
                "snapshot(s); add a catch-all period")
        th = [t.threshold / _KWH_PER_MWH for t in item.tiers] + [np.inf]
        free = th[0] > 0
        segments = ([{"k": -1, "width_mwh": th[0]}] if free else []) + \
            [{"k": k, "width_mwh": th[k + 1] - th[k]} for k in range(len(item.tiers))]
        rates = item_tier_rates(item)
        for p in pd.unique(inv):
            for m in sorted(set(months[inv == p])):
                sel = (inv == p) & (months == m)
                periods = []
                for f in sorted(set(frag[sel])):
                    periods.append({
                        "frag": int(f), "name": item.periods[int(f)].name,
                        "positions": np.flatnonzero(sel & (frag == f)),
                        "rates_eur_per_mwh": ([0.0] if free else [])
                        + [float(r) * _KWH_PER_MWH for r in rates[int(f)]]})
                keys.append({"key": f"{item.id}|{'' if p is None else p}|{m}", "item": item.id,
                             "month": m, "inv_period": None if p is None else int(p),
                             "windowed": windowed, "periods": periods, "segments": segments})
    return ({"import_links": import_links(cfg), "keys": keys, "items_hash": _items_hash(convex),
             "hash_version": _H.HASH_VERSION},
            [i.id for i in convex], nonconvex)


def _tier_var(key: dict, period: dict, seg: dict) -> str:
    """`ic_tier_q` coordinate. A plain item keeps the P1 names (`key|k`)."""
    k = "free" if seg["k"] < 0 else str(seg["k"])
    if not key.get("windowed"):
        return f"{key['key']}|{k}"
    return f"{key['key']}|{period['name']}#{period['frag']}|{k}"


def add_tier_terms(n) -> None:
    """Per key: Σ_k q[p, k] = period p's import energy (Σ w_t · p_t, MWh) for
    each period p, Σ_p q[p, k] ≤ width_k for each segment k (one period: the
    variable bound), 0 ≤ q ≤ width_k; objective += Σ w_obj · rate[p][k] · q."""
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
        for per in key["periods"]:
            for seg, r in zip(key["segments"], per["rates_eur_per_mwh"]):
                names.append(_tier_var(key, per, seg))
                uppers.append(seg["width_mwh"])
                coefs.append(wk * r)
    idx = pd.Index(names, name="tier")
    q = m.add_variables(lower=0, upper=xr.DataArray(uppers, coords={"tier": names}, dims="tier"),
                        name="ic_tier_q", coords=[idx])
    imp = m["Link-p"].sel(name=spec["import_links"]).sum("name")
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    c = 0
    for key in spec["keys"]:
        for per in key["periods"]:
            pos = per["positions"]
            energy = (imp.isel(snapshot=pos) * w[pos]).sum()
            mine = q.sel(tier=[_tier_var(key, per, seg) for seg in key["segments"]]).sum()
            m.add_constraints(mine - energy == 0, name=f"ic_tier_volume_{c}")
            c += 1
        if len(key["periods"]) > 1:
            for seg in key["segments"]:
                if not np.isfinite(seg["width_mwh"]):
                    continue
                shared = q.sel(tier=[_tier_var(key, per, seg) for per in key["periods"]]).sum()
                m.add_constraints(shared <= seg["width_mwh"], name=f"ic_tier_width_{c}")
                c += 1
    m.objective += (q * xr.DataArray(coefs, coords={"tier": names}, dims="tier")).sum()
    setattr(n, TIER_BUILT_ATTR, True)


def _read_tier_solution(n, spec: dict) -> dict | None:
    """The solved allocation per (key, period, segment) — what the rows price
    after a reload (`ic_tier_volumes`)."""
    model = getattr(n, "model", None)
    if not getattr(n, TIER_BUILT_ATTR, False) or model is None:
        return None
    try:
        sol = model.variables["ic_tier_q"].solution.to_pandas()
    except Exception:  # noqa: BLE001
        return None
    out = {}
    for key in spec["keys"]:
        for per in key["periods"]:
            for seg, r in zip(key["segments"], per["rates_eur_per_mwh"]):
                name = _tier_var(key, per, seg)
                v = float(sol.loc[name])
                if not np.isfinite(v):
                    return None
                out[name] = {"item": key["item"], "month": key["month"],
                             "inv_period": key["inv_period"], "tier": seg["k"],
                             "period": per["name"], "rate_eur_per_mwh": r, "q_mwh": v,
                             "items_hash": spec.get("items_hash"),
                             "hash_version": spec.get("hash_version")}
    return out


# ── tariff capacity items (P2 WP2.1c-iii) ──────────────────────────────────


def capacity_lp_items(cfg: CommercialConfig) -> list:
    return [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
            if i.kind == "capacity" and _lp_reason(i) is None]


def capacity_items_hash(cfg: CommercialConfig, version: int = _H.HASH_VERSION) -> str | None:
    items = capacity_lp_items(cfg)
    return _items_hash(items, version) if items else None


def _capacity_spec(n, cfg: CommercialConfig) -> dict | None:
    """Contracted-capacity items (a term on the PoC's p_nom, decided at LP
    build time: extendable ⇒ LP term, fixed ⇒ a constant reported outside the
    total) and measured-peak items (one annual peak per investment period and
    LOCAL calendar year, over settlement-interval means, at €/MW × the year's
    represented hours / a year's hours — the engine's rule)."""
    items = capacity_lp_items(cfg)
    if not items:
        return None
    local = _local_clock(n.snapshots, cfg.timezone)
    years = np.asarray(local.strftime("%Y"))
    inv = (np.asarray(n.snapshots.get_level_values(0)) if isinstance(n.snapshots, pd.MultiIndex)
           else np.full(len(n.snapshots), None, dtype=object))
    w = n.snapshot_weightings.objective.to_numpy(dtype=float)
    contracted, peaks = [], []
    for item in items:
        eur_per_mw_year = float(item.periods[0].rate) * _KWH_PER_MWH
        if item.measured_on != "peak_import":
            contracted.append({"item": item.id, "eur_per_mw_year": eur_per_mw_year})
            continue
        interval = interval_key(local, {"15min": "15min", "30min": "30min",
                                        "h": "h"}[item.settlement])
        for p in pd.unique(inv):
            for y in sorted(set(years[inv == p])):
                pos = np.flatnonzero((inv == p) & (years == y))
                _, group = np.unique(interval[pos], return_inverse=True)
                peaks.append({"key": f"{item.id}|{'' if p is None else p}|{y}", "item": item.id,
                              "inv_period": None if p is None else int(p), "year": y,
                              "eur_per_mw": eur_per_mw_year * float(w[pos].sum()) / _HOURS_PER_YEAR,
                              "positions": pos, "group": group})
    # A contracted item on a FIXED PoC is a constant, harmless per window;
    # the PoC's extendability at apply time is the LP build's (the connection
    # agreement applied next never changes it: review #6).
    extendable = bool(n.links.at[cfg.poc_link, "p_nom_extendable"]) \
        if cfg.poc_link in n.links.index else False
    return {"link": cfg.poc_link, "import_links": import_links(cfg),
            "contracted": contracted, "peaks": peaks,
            "extendable_poc": extendable and bool(contracted),
            "items_hash": _items_hash(items), "hash_version": _H.HASH_VERSION}


# Convention (review #3): a contracted capacity charge accrues over every
# represented hour of each ACTIVE period (the LP fee's nyears; on a flat axis
# the whole horizon), also before a connection's `available_from` — the DSO
# bills the contracted capacity, the agreement gates the flow. In a
# multi-period solve `available_from` moves the PoC's build_year, so a period
# before it is not active and is not charged (the record lists the periods).


def add_capacity_terms(n) -> None:
    """objective += Σ contracted coef · p_nom[PoC] (extendable PoC) and
    Σ w_obj · €/MW · ic_capacity_peak[key], ic_capacity_peak ≥ each interval's
    mean import. Called from the LP wrapper."""
    import xarray as xr

    from services.commercial import connection  # lazy: connection imports this module

    spec = getattr(n, CAPACITY_SPEC_ATTR, None)
    if not spec:
        return
    m = n.model
    link = spec["link"]
    built = {"contracted": {}, "fixed": {}, "peaks": bool(spec["peaks"])}
    extendable = bool(n.links.at[link, "p_nom_extendable"])
    for c in spec["contracted"]:
        coef, by_period = connection.capacity_fee_coefficient(n, link, c["eur_per_mw_year"])
        if extendable:
            m.objective += coef * m["Link-p_nom"].sel(name=link)
            built["contracted"][c["item"]] = {"link": link, "eur_per_mw_by_period": by_period}
        else:
            # A fixed PoC: the charge is a constant the LP cannot change —
            # reported outside the reconciled total (as a fixed connection fee).
            p_nom = float(n.links.at[link, "p_nom"])
            built["fixed"][c["item"]] = {"link": link, "p_nom_mw": p_nom,
                                         "eur_by_period": {k: v * p_nom
                                                           for k, v in by_period.items()}}
    if spec["peaks"]:
        names = [k["key"] for k in spec["peaks"]]
        peak = m.add_variables(lower=0, name="ic_capacity_peak",
                               coords=[pd.Index(names, name="key")])
        imp = m["Link-p"].sel(name=spec["import_links"]).sum("name")
        w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
        for i, k in enumerate(spec["peaks"]):
            pos, group = k["positions"], k["group"]
            flow = imp.isel(snapshot=pos)
            if group.max() + 1 == len(pos):
                m.add_constraints(flow - peak.sel(key=k["key"]) <= 0,
                                  name=f"ic_capacity_peak_def_{i}")
                continue
            gda = xr.DataArray(group, coords={"snapshot": flow.indexes["snapshot"]},
                               dims="snapshot", name="interval")
            weighted = (flow * w_all[pos]).groupby(gda).sum()
            wsum = np.bincount(group, weights=w_all[pos])
            wda = xr.DataArray(wsum, coords={"interval": np.arange(len(wsum))}, dims="interval")
            m.add_constraints(weighted - wda * peak.sel(key=k["key"]) <= 0,
                              name=f"ic_capacity_peak_def_{i}")
        if isinstance(n.snapshots, pd.MultiIndex):
            w_obj = n.investment_period_weightings["objective"]
            weight = [float(w_obj.loc[k["inv_period"]]) for k in spec["peaks"]]
        else:
            weight = [1.0] * len(names)
        coef = xr.DataArray([w * k["eur_per_mw"] for w, k in zip(weight, spec["peaks"])],
                            coords={"key": names}, dims="key")
        m.objective += (peak * coef).sum()
    setattr(n, CAPACITY_BUILT_ATTR, built)


def _read_capacity_solution(n, spec: dict) -> dict | None:
    """The committed record: contracted €/MW per period (rows × p_nom_opt),
    fixed-PoC charges, and each annual peak read from the solved dispatch
    (interval means, as billed)."""
    built = getattr(n, CAPACITY_BUILT_ATTR, None)
    model = getattr(n, "model", None)
    if built is None or model is None:
        return None
    out = {"contracted": built["contracted"], "fixed": built["fixed"], "peaks": {},
           "items_hash": spec["items_hash"], "hash_version": spec["hash_version"]}
    if spec["peaks"]:
        try:
            flow = model.variables["Link-p"].solution.sel(
                name=spec["import_links"]).sum("name").values
        except Exception:  # noqa: BLE001 — unsolved: nothing to commit
            return None
        w_all = n.snapshot_weightings.objective.to_numpy(dtype=float)
        for k in spec["peaks"]:
            pos, group = k["positions"], k["group"]
            f = flow[pos]
            wsum = np.bincount(group, weights=w_all[pos])
            ok = wsum > 0
            means = (f if group.max() + 1 == len(pos)
                     else np.bincount(group, weights=f * w_all[pos])[ok] / wsum[ok])
            v = max(0.0, float(means.max())) if len(means) else float("nan")
            if not np.isfinite(v):
                return None
            out["peaks"][k["key"]] = {"item": k["item"], "inv_period": k["inv_period"],
                                      "year": k["year"], "eur_per_mw": k["eur_per_mw"],
                                      "peak_mw": v}
    return out


def capacity_all_newly_bound(links_rec: dict | None) -> bool:
    """A solve made before WP2.1c-iii bound no capacity item (no record)."""
    return bool(links_rec) and int(links_rec.get("lp_recipe") or 1) < CAPACITY_RECIPE


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
                          solve_strategy: str, multi_period: bool,
                          capacity: dict | None = None) -> None:
    """Monthly demand peaks and tier volumes cannot be carried across the
    windows of a rolling or multi-period myopic solve (P6); a capacity item
    would be sized and paid (contracted) or its annual peak restarted
    (measured) per window, as a connection fee would (WP2.1c-iii)."""
    windowed = solve_strategy == "rolling" or (solve_strategy == "myopic" and multi_period)
    if not windowed:
        return
    if capacity is not None and (capacity["peaks"] or capacity.get("extendable_poc")):
        raise CommercialBindingError(
            f"tariff capacity items with solve_strategy={solve_strategy!r} would be paid (or "
            "their annual peak restarted) per window; not supported until P6 — solve the "
            "full horizon")
    if tier_spec is not None:
        raise CommercialBindingError(
            f"tiered rates with solve_strategy={solve_strategy!r} would restart the monthly "
            "volume per window; not supported until P6")
    if demand is not None:
        raise CommercialBindingError(
            f"demand charges with solve_strategy={solve_strategy!r} would be re-created per "
            "window without the month's running peak (spec §5.2); not supported until P6")


def tier_floor_eur_per_mwh(cfg: CommercialConfig) -> float:
    """The cheapest marginal import rate the convex tiers can charge (their
    first tier), summed over tiered items: tiers are LP terms, not adders."""
    total = 0.0
    for i in _energy_tiered(cfg):
        if not item_tiers_convex(i):
            continue
        if i.tiers[0].threshold > 0:
            continue  # the volume below the first threshold is free
        # A windowed item: the cheapest period's first rate (conservative).
        total += min(r[0] for r in item_tier_rates(i)) * _KWH_PER_MWH
    return float(total)


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


# ── changes_dispatch PPAs (P2 WP2.2d) ──────────────────────────────────────


def dispatch_ppas(cfg: CommercialConfig) -> list:
    return [c for c in cfg.contracts if c.type == "ppa" and c.changes_dispatch]


def ppa_dispatch_hash(cfg: CommercialConfig, version: int = _H.HASH_VERSION) -> str | None:
    ppas = dispatch_ppas(cfg)
    return _H.digest(ppas, version=version) if ppas else None


def _ppa_dispatch_spec(n, cfg: CommercialConfig) -> dict[str, np.ndarray] | None:
    """{generator: €/MWh adder per snapshot} for the dispatch PPAs, or None.
    v1 binds a fixed-price pay-as-produced PPA on ON-SITE Generators with the
    site as the BUYER: the site pays price_y for every MWh produced, a marginal
    cost of the asset. Everything else is refused with its reason (an annual
    cap is not a marginal cost; the seller's revenue is not the site's cost)."""
    from services.commercial.contracts import indexed

    ppas = dispatch_ppas(cfg)
    if not ppas:
        return None
    onsite = set(site_generators(n, cfg))
    inv = (np.asarray(n.snapshots.get_level_values(0)) if isinstance(n.snapshots, pd.MultiIndex)
           else None)
    out: dict[str, np.ndarray] = {}
    for c in ppas:
        why = None
        if not same_party(c.buyer, cfg.site_party):
            why = (f"the site ({cfg.site_party!r}) is not the buyer; only the buyer case "
                   "changes dispatch in P2")
        elif c.kind != "pay_as_produced":
            why = f"kind {c.kind!r}: only pay_as_produced changes dispatch in P2"
        elif c.pricing != "fixed":
            why = "pricing market_plus_premium: only a fixed price changes dispatch in P2"
        elif c.volume_cap_mwh_per_year is not None:
            why = "a volume cap is an annual limit, not a marginal cost"
        if why is None:
            for a in c.asset_ids:
                if a not in n.generators.index:
                    why = f"asset {a!r} is not a Generator"
                elif a not in onsite:
                    why = f"asset {a!r} is not on-site (not behind the PoC meter)"
                elif a in out:
                    why = f"asset {a!r} is in two changes_dispatch PPAs"
                if why:
                    break
        if why is not None:
            raise CommercialBindingError(
                f"changes_dispatch PPA {c.id!r} cannot bind: {why}")
        if inv is None:
            w = n.snapshot_weightings.objective.to_numpy(dtype=float)
            years = pd.Series(w, index=pd.DatetimeIndex(n.snapshots).year).groupby(level=0).sum()
            price = np.full(len(n.snapshots), indexed(c.price, c.indexation_pct_per_year,
                                                      c.base_year, int(years.idxmax())))
        else:
            price = np.array([indexed(c.price, c.indexation_pct_per_year, c.base_year, int(p))
                              for p in inv], dtype=float)
        for a in c.asset_ids:
            out[a] = price
    return out


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
            n.meta.pop(META_CAPACITY, None)
            n.meta.pop(META_PPA, None)
            n.meta.pop("ic_contracts", None)   # the pre-review solve record (WP2.2c #2)
            if n.generators_t.get(PPA_PRICE_ATTR) is not None:
                n.generators_t[PPA_PRICE_ATTR] = pd.DataFrame(index=n.snapshots)

        applied._commit.append(clear)
        return applied

    cfg = _parse(commercial)
    validate_for_network(n, cfg)
    adders, energy_items, not_in_lp, notes = _adders(n, cfg)
    demand, demand_items, demand_missing, demand_notes = _demand_spec(n, cfg)
    notes = notes + [x for x in demand_notes if x not in notes]
    tier_spec, tiered_items, nonconvex_items = _tier_spec(n, cfg)
    capacity = _capacity_spec(n, cfg)
    refuse_windowed_terms(demand, tier_spec, solve_strategy, multi_period, capacity)
    ppa_adders = _ppa_dispatch_spec(n, cfg) or {}
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

    gmc = n.generators_t.marginal_cost
    for gen, add in ppa_adders.items():
        had = gen in gmc.columns
        old = gmc[gen].copy() if had else None
        base = old.to_numpy(dtype=float) if had else float(n.generators.at[gen, "marginal_cost"])
        gmc[gen] = base + add

        def undo_gen(gen=gen, had=had, old=old) -> None:
            live = n.generators_t.marginal_cost
            if had:
                live[gen] = old
            elif gen in live.columns:
                n.generators_t.marginal_cost = live.drop(columns=[gen])

        applied._undo.append(undo_gen)

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

    solved_capacity: dict = {}
    if capacity is not None:
        setattr(n, CAPACITY_SPEC_ATTR, capacity)

        def undo_capacity() -> None:
            got = _read_capacity_solution(n, capacity)
            if got is not None:
                solved_capacity["v"] = got
            for attr in (CAPACITY_SPEC_ATTR, CAPACITY_BUILT_ATTR):
                if hasattr(n, attr):
                    delattr(n, attr)

        applied._undo.append(undo_capacity)

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
        if "v" in solved_capacity:
            n.meta[META_CAPACITY] = solved_capacity["v"]
        else:
            n.meta.pop(META_CAPACITY, None)
        frame = pd.DataFrame({link: add for link, add in targets.items()}, index=n.snapshots)
        n.links_t[ENERGY_PRICE_ATTR] = frame
        n.meta[META_LINKS] = {"import": cfg.poc_link, "export": cfg.export_link,
                              "import_members": import_links(cfg),
                              "priced": sorted(targets), "energy_hash": energy_hash(n, cfg),
                              "hash_version": _H.HASH_VERSION,
                              # This solve records its connection agreement in
                              # `ic_connection` (P2 WP2.0); a P1 solve did not.
                              "agreement_recorded": True,
                              # The LP recipe of this solve (see `LP_RECIPE`):
                              # dates a solve with no demand or tier record.
                              "lp_recipe": LP_RECIPE}
        # Settlement-only contracts are not solve state (review 2.2c #5): the
        # settlement records their hash; a dispatch PPA is `ic_ppa` below.
        n.meta.pop("ic_contracts", None)
        if ppa_adders:
            n.generators_t[PPA_PRICE_ATTR] = pd.DataFrame(ppa_adders, index=n.snapshots)
            n.meta[META_PPA] = {"contracts": [c.id for c in dispatch_ppas(cfg)],
                                "generators": sorted(ppa_adders),
                                "hash": ppa_dispatch_hash(cfg), "hash_version": _H.HASH_VERSION}
        else:
            if n.generators_t.get(PPA_PRICE_ATTR) is not None:
                n.generators_t[PPA_PRICE_ATTR] = pd.DataFrame(index=n.snapshots)
            n.meta.pop(META_PPA, None)
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
        "nonconvex_tier_predicted": predicted_tiers(n, cfg),
        "capacity_items": [i.id for i in capacity_lp_items(cfg)],
        "ppa_dispatch": {c.id: list(c.asset_ids) for c in dispatch_ppas(cfg)},
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
        add_capacity_terms(n)
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
                            or energy_record_state(n, cfg, solved) == "config"):
        flags.append("config_changed_since_solve")
    elif cfg is not None and energy_record_state(n, cfg, solved) == "recipe":
        # The config is unchanged; this recipe binds more of it (windowed
        # energy tiers, WP2.1c-ii): re-solve.
        flags.append("energy_recipe_changed")
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
