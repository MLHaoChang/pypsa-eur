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

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig, TariffItem
from services.commercial.tariff_engine import _is_demand, _period_index, _rates

EXPORT_PRICE_ATTR = "ic_export_price"
ENERGY_PRICE_ATTR = "ic_energy_price"
META_LINKS = "ic_poc_links"
META_PRICE_AXIS = "ic_export_price_axis"
META_DEMAND = "ic_demand_peaks"
DEMAND_SPEC_ATTR = "_ic_demand_spec"   # transient: set by apply, read by the LP wrapper
DEMAND_BUILT_ATTR = "_ic_demand_built"  # transient: set by the LP wrapper
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
    if _is_demand(item):
        if item.ratchet is not None:
            return "ratchet_WP1.5b"
        if item.tiers:
            return "tiers_WP1.5c"
        if item.unit != "per_kw_month":
            return f"unit_{item.unit}_not_demand"
        if item.direction != "cost":
            return "demand_revenue_not_supported"
        if item.measured_on == "export":
            return "export_demand_not_supported"
        return None
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
        if _is_demand(item):
            continue  # a peak term, built by `_demand_spec`
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
    bounds = (n.meta.get("vintage_bounds") or {}).get("Link") if hasattr(n, "meta") else None
    if isinstance(bounds, dict):
        clash = [l for l in (cfg.poc_link, cfg.export_link) if l and l in bounds]
        if clash:
            raise CommercialBindingError(
                f"per-period vintage bounds on the PoC Link(s) {clash} are not supported with "
                "the commercial layer in P1 (the vintage clones would carry dispatch the "
                "commercial rows do not read)")
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


# ── peak demand (WP1.5a) ───────────────────────────────────────────────────


def _demand_spec(n, cfg: CommercialConfig) -> tuple[dict | None, list[str], list[str]]:
    """(spec for the LP wrapper, demand item ids, months not established).

    One key per (item, period window, investment period, local month) with at
    least one snapshot; the months between the first and last snapshot that
    have none are listed as not established (spec §5.2), and never weighted
    from their neighbours."""
    items = [i for i in (cfg.import_tariff.items if cfg.import_tariff is not None else [])
             if _is_demand(i) and _lp_reason(i) is None]
    if not items:
        return None, [], []
    local = _local_clock(n.snapshots, cfg.timezone)
    months = np.asarray(local.strftime("%Y-%m"))
    inv = (np.asarray(n.snapshots.get_level_values(0)) if isinstance(n.snapshots, pd.MultiIndex)
           else np.full(len(n.snapshots), None, dtype=object))
    missing: list[str] = []
    for p in pd.unique(inv):
        sel = inv == p
        span = pd.period_range(local[sel].min().strftime("%Y-%m"),
                               local[sel].max().strftime("%Y-%m"), freq="M").strftime("%Y-%m")
        gone = sorted(set(span) - set(months[sel]))
        missing += [m if p is None else f"{p}:{m}" for m in gone]
    keys: list[dict] = []
    for item in items:
        pidx = _period_index(item, local)
        for k, per in enumerate(item.periods):
            if per.rate == 0:
                continue
            for p in pd.unique(inv):
                for m in sorted(set(months[(inv == p) & (pidx == k)])):
                    pos = np.flatnonzero((inv == p) & (pidx == k) & (months == m))
                    keys.append({
                        "key": f"{item.id}|{k}|{'' if p is None else p}|{m}",
                        "item": item.id, "period": per.name, "month": m,
                        "inv_period": None if p is None else int(p),
                        "eur_per_mw": float(per.rate) * _KWH_PER_MWH,
                        "net": item.measured_on == "net", "positions": pos})
    spec = {"import_link": cfg.poc_link, "export_link": cfg.export_link, "keys": keys}
    return spec, [i.id for i in items], missing


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
    link_p = m["Link-p"]
    imp = link_p.sel(name=spec["import_link"])
    exp = link_p.sel(name=spec["export_link"]) if spec["export_link"] else None
    for i, k in enumerate(spec["keys"]):
        flow = imp.isel(snapshot=k["positions"])
        if k["net"] and exp is not None:
            flow = flow - exp.isel(snapshot=k["positions"])
        m.add_constraints(flow - peak.sel(key=k["key"]) <= 0, name=f"ic_peak_import_def_{i}")
    if isinstance(n.snapshots, pd.MultiIndex):
        w_obj = n.investment_period_weightings["objective"]
        weight = [float(w_obj.loc[k["inv_period"]]) for k in spec["keys"]]
    else:
        weight = [1.0] * len(names)
    coef = xr.DataArray([w * k["eur_per_mw"] for w, k in zip(weight, spec["keys"])],
                        coords={"key": names}, dims="key")
    m.objective += (peak * coef).sum()
    setattr(n, DEMAND_BUILT_ATTR, True)


def _read_demand_solution(n, spec: dict) -> dict | None:
    """The solved peaks, read from `n.model` while it still exists."""
    model = getattr(n, "model", None)
    if not getattr(n, DEMAND_BUILT_ATTR, False) or model is None:
        return None
    try:
        sol = model.variables["ic_peak_import"].solution.to_pandas()
    except Exception:  # noqa: BLE001 — unsolved / infeasible: nothing to commit
        return None
    out = {}
    for k in spec["keys"]:
        v = float(sol.loc[k["key"]])
        if not np.isfinite(v):
            return None
        out[k["key"]] = {"item": k["item"], "period": k["period"], "month": k["month"],
                         "inv_period": k["inv_period"], "eur_per_mw": k["eur_per_mw"],
                         "peak_mw": v}
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

        applied._commit.append(clear)
        return applied

    cfg = _parse(commercial)
    validate_for_network(n, cfg)
    adders, energy_items, not_in_lp, notes = _adders(n, cfg)
    demand, demand_items, demand_missing = _demand_spec(n, cfg)
    if demand is not None and (solve_strategy == "rolling"
                               or (solve_strategy == "myopic" and multi_period)):
        raise CommercialBindingError(
            f"demand charges with solve_strategy={solve_strategy!r} would be re-created per "
            "window without the month's running peak (spec §5.2, P6); not supported in P1")
    has_price = cfg.export_price_ref is not None
    if has_price:
        adders["export"] = adders["export"] - _export_price(n, cfg)

    targets: dict[str, np.ndarray] = {}
    if adders["import"].any():
        targets[cfg.poc_link] = adders["import"]
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

    def commit() -> None:
        frame = pd.DataFrame({link: add for link, add in targets.items()}, index=n.snapshots)
        n.links_t[ENERGY_PRICE_ATTR] = frame
        n.meta[META_LINKS] = {"import": cfg.poc_link, "export": cfg.export_link,
                              "priced": sorted(targets)}
        if "v" in solved_peaks:
            n.meta[META_DEMAND] = solved_peaks["v"]
        else:
            n.meta.pop(META_DEMAND, None)

    applied._commit.append(commit)

    risk = 0
    if cfg.export_link is not None:
        def cost(link):
            return (mc[link].to_numpy(dtype=float) if link in mc.columns
                    else np.full(len(n.snapshots), float(n.links.at[link, "marginal_cost"])))
        risk = int(((cost(cfg.poc_link) + cost(cfg.export_link)) < -1e-9).sum())
    if log is not None:
        log(f"[COMMERCIAL] PoC prices applied on {sorted(targets)} "
            f"({len(energy_items)} energy item(s); {len(not_in_lp)} left to later bindings"
            + (f"; WARNING {risk} snapshot(s) pay more to export than to import" if risk else "")
            + ")")
    applied.facts = {
        "poc_link": cfg.poc_link, "export_link": cfg.export_link,
        "priced_links": sorted(targets), "energy_items": energy_items,
        "demand_items": demand_items, "demand_not_established_months": demand_missing,
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

    return fn


# ── cost rows (persisted data) ─────────────────────────────────────────────


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
                            or cfg.export_link != solved.get("export")):
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

    def row(link: str | None, label: str) -> float | None:
        if link is None:
            return None
        if link not in prices.columns:
            # 0.0 only for a Link the solve deliberately left unpriced; a
            # priced Link whose record is gone is not established (ADR-0001).
            if link in n.links.index and link not in (solved.get("priced") or []):
                return 0.0
            flags.append(f"{label}_not_established")
            return None
        add = prices[link]
        p0 = p0_of(link)
        if p0 is None or add.isna().any():
            flags.append(f"{label}_not_established")
            return None
        return float((w * p0 * add).sum())

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
