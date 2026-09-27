"""
Connection agreements → the PoC Link, for one solve (Edge Investment Case
P1 WP1.4a/WP1.4b; spec §5 table).

`apply_connection_agreement(n, agreement, poc_link=, …)` mutates the PoC Link
and returns an `Applied`. Its `undo()` restores every attribute it touched
(the `apply_archetype_pack_detailed().undo()` discipline), and its `commit()`
persists what the report needs after a successful solve. `run_simulation`
applies it with the WP1.3 prices (`apply_commercial_for_solve`) just before the
modelling assumptions, and chains the undo after their restore.

  * `firm` with a `capacity_fee`: the Link is extendable in [0, cap] for the
    solve and the fee is an EXPLICIT objective term on its `p_nom`, added by
    `lp_bindings._wrap_with_commercial_bindings` (review round 1 #1, #4). It is
    not smuggled into `capital_cost`, where PyPSA ignores it for fixed
    capacity and replaces it when `overnight_cost` is set. Without a fee: a
    fixed Link is set to `p_nom = cap`; a user-extendable Link keeps its own
    costs and is capped at `p_nom_max = min(p_nom_max, cap)` (#7).
  * `non_firm_static`: the physical `p_nom` stays; availability is
    `min(existing p_max_pu (static or time-varying), cap / p_nom)` (#3).
  * `non_firm_dynamic` (WP1.4b): availability `min(existing, min(envelope[t],
    cap) / p_nom)`. The envelope is a Library series the config route resolves
    into `links_t["ic_envelope_mw"]` (`write_envelope`); a missing envelope, or
    one on another snapshot axis, is refused, never ignored.
  * `fca` (WP1.4b): with an envelope, as `non_firm_dynamic`; without one,
    capped at the contracted capacity like `non_firm_static`. Its curtailment
    hours are a `profiles` stress entry on the PoC Link (`fca_stress_entry`):
    the deterministic worst-case snapshots, disclosed as `fca_synthetic_hours`.
  * A fee on a FIXED-capacity agreement (non-firm, fca) cannot move the LP. It
    is a fixed charge, like a tariff's fixed items (§5.5): reported as
    `network_capacity_fixed` OUTSIDE the reconciled total, flagged
    `fixed_charge_not_in_lp`, and billed by the P2 billing pass (#1).
  * `export_cap_mw`: the export Link's `p_nom`.
  * `available_from`: on a multi-period axis, periods before its year are
    closed and `build_year` moves to the first open period (#2). The period
    level is read, not the timestamps, which repeat across periods when flat
    snapshots are promoted. On a flat axis, snapshots before the date are
    closed. The date is local midnight on the site clock, converted to UTC
    when `commercial.timezone` is set (#10).

**Fee units and weighting (#8).** `per_kw_year` and `per_kw_month` (×12) →
€/MW/yr. Per period the LP charges `w_obj(p) × fee × nyears(p) × p_nom`, where
`nyears(p) = Σ_{t∈p} w_t / 8760` is the operating time the period's snapshots
represent. That is the same horizon-fraction rule on flat and multi-period
axes; a flat axis is one period with `w_obj = 1`. The per-period €/MW is
committed to `n.meta["ic_connection_fee"]` after a successful solve, so the
cost row is `€/MW × p_nom_opt` from persisted data (see `cost_rows`).

**Windowed dispatch (#5, #6).** Rolling-horizon windows and myopic periods
would each size and pay the connection on their own. A fee under
`solve_strategy` rolling or myopic is refused in P1 (P6 scope).

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig, ConnectionAgreement, TariffItem
from services.commercial.lp_bindings import (
    Applied,
    CommercialBindingError,
    _axis_hash,
    _frame,
    align_to_snapshots,
)

_HOURS_PER_YEAR = 8760.0
_UNIT_TO_PER_MW_YEAR = {"per_kw_year": 1000.0, "per_kw_month": 12_000.0}
ENVELOPE_ATTR = "ic_envelope_mw"
META_ENV_AXIS = "ic_envelope_axis"
META_FEE = "ic_connection_fee"
META_FIXED_FEE = "ic_connection_fixed_fee"
FEE_SPEC_ATTR = "_ic_fee_spec"          # transient: set by apply, read by the LP wrapper
FEE_BUILT_ATTR = "_ic_fee_built"        # transient: set by the LP wrapper, read by commit
FCA_DISCLOSURE = "fca_synthetic_hours"

__all__ = ["Applied", "CommercialBindingError", "ENVELOPE_ATTR", "apply_commercial_for_solve",
           "apply_connection_agreement", "apply_for_config", "fca_stress_entry",
           "register_fca_entry", "write_envelope"]


# ── units and weights ──────────────────────────────────────────────────────


def fee_eur_per_mw_year(item: TariffItem) -> float:
    if item.unit not in _UNIT_TO_PER_MW_YEAR:
        raise CommercialBindingError(
            f"capacity fee {item.id!r}: unit {item.unit} is not supported (per_kw_year, "
            "per_kw_month)")
    p = item.periods[0]
    if len(item.periods) != 1 or p.months or p.weekdays or p.start_hour is not None:
        raise CommercialBindingError(
            f"capacity fee {item.id!r}: a fee with seasonal or time windows is not supported")
    return float(p.rate) * _UNIT_TO_PER_MW_YEAR[item.unit]


def horizon_years(n) -> float:
    """Operating years the whole axis represents (Σ objective weights / 8760)."""
    return float(n.snapshot_weightings.objective.sum()) / _HOURS_PER_YEAR


def nyears_by_period(n) -> dict:
    """{period: Σ_{t∈p} w_t / 8760}; a flat axis is the single period `None`."""
    w = n.snapshot_weightings.objective
    if isinstance(n.snapshots, pd.MultiIndex):
        return {p: float(v) / _HOURS_PER_YEAR
                for p, v in w.groupby(level=0).sum().items()}
    return {None: float(w.sum()) / _HOURS_PER_YEAR}


def active_periods(n, link: str) -> list:
    """Periods in which `link` exists (build_year ≤ p < build_year + lifetime)."""
    if not isinstance(n.snapshots, pd.MultiIndex):
        return [None]
    by = float(n.links.at[link, "build_year"]) if "build_year" in n.links.columns else 0.0
    life = float(n.links.at[link, "lifetime"]) if "lifetime" in n.links.columns else np.inf
    if not np.isfinite(life) or life <= 0:
        life = np.inf
    return [p for p in n.investment_periods if by <= float(p) < by + life]


# ── mutation helpers (each registers its undo) ─────────────────────────────


def _set_static(n, applied: Applied, link: str, col: str, value) -> None:
    old = n.links.at[link, col]
    n.links.at[link, col] = value

    def undo() -> None:
        n.links.at[link, col] = old

    applied._undo.append(undo)


def _set_p_max_pu_t(n, applied: Applied, link: str, values: np.ndarray) -> None:
    frame = n.links_t.p_max_pu
    had = link in frame.columns
    old = frame[link].copy() if had else None
    frame[link] = values

    def undo() -> None:
        cur = n.links_t.p_max_pu
        if had:
            cur[link] = old
        elif link in cur.columns:
            n.links_t.p_max_pu = cur.drop(columns=[link])

    applied._undo.append(undo)


def _base_p_max_pu(n, link: str) -> np.ndarray:
    return (n.links_t.p_max_pu[link].to_numpy(dtype=float)
            if link in n.links_t.p_max_pu.columns
            else np.full(len(n.snapshots), float(n.links.at[link, "p_max_pu"])))


# ── envelope (resolved at config time) ─────────────────────────────────────


def write_envelope(n, link: str, series: pd.Series, timezone: str | None = None) -> int:
    """Put a resolved envelope (MW) on `link`'s `ic_envelope_mw` column. Same
    rules as `lp_bindings.write_export_price`: aligned first, written only when
    every snapshot is covered, axis recorded. Returns uncovered snapshots."""
    aligned = align_to_snapshots(series, n.snapshots, timezone)
    uncovered = int(aligned.isna().sum())
    if uncovered:
        return uncovered
    store = _frame(n, ENVELOPE_ATTR)
    store[link] = aligned.to_numpy()
    n.links_t[ENVELOPE_ATTR] = store
    n.meta[META_ENV_AXIS] = _axis_hash(n.snapshots)
    return 0


def _envelope_mw(n, link: str) -> np.ndarray:
    if n.meta.get(META_ENV_AXIS) not in (None, _axis_hash(n.snapshots)):
        raise CommercialBindingError(
            "the snapshots changed since the connection envelope was materialised; re-apply "
            "the commercial config to place it on the current axis")
    store = n.links_t.get(ENVELOPE_ATTR) if hasattr(n.links_t, "get") else None
    col = None if store is None or link not in store.columns else store[link].reindex(n.snapshots)
    if col is None or col.isna().any():
        raise CommercialBindingError(
            f"the connection envelope for {link!r} is not established on every snapshot; "
            "re-apply the commercial config to materialise it")
    return col.to_numpy(dtype=float)


# ── apply ──────────────────────────────────────────────────────────────────


def _validate(n, agreement: ConnectionAgreement, poc_link: str, export_link: str | None,
              solve_strategy: str, multi_period: bool) -> float | None:
    if poc_link not in n.links.index:
        raise CommercialBindingError(f"commercial.poc_link {poc_link!r} is not a Link")
    if agreement.kind == "non_firm_dynamic" or (agreement.kind == "fca" and agreement.envelope):
        _envelope_mw(n, poc_link)  # present and on this axis, or refuse
    if agreement.export_cap_mw is not None and export_link is None:
        raise CommercialBindingError("connection.export_cap_mw needs commercial.export_link")
    if export_link is not None and export_link not in n.links.index:
        raise CommercialBindingError(f"commercial.export_link {export_link!r} is not a Link")
    fee = fee_eur_per_mw_year(agreement.capacity_fee) if agreement.capacity_fee else None
    if agreement.kind == "firm" and fee is not None:
        windowed = solve_strategy == "rolling" or (solve_strategy == "myopic" and multi_period)
        if windowed:
            raise CommercialBindingError(
                f"a connection capacity fee with solve_strategy={solve_strategy!r} (myopic / "
                "rolling windows would each size and pay the connection) is not supported in "
                "P1; solve the full horizon")
    if agreement.kind != "firm":
        p_nom = float(n.links.at[poc_link, "p_nom"])
        if bool(n.links.at[poc_link, "p_nom_extendable"]) or p_nom <= 0:
            raise CommercialBindingError(
                f"{agreement.kind} needs a PoC Link with a fixed physical p_nom > 0")
        if agreement.import_cap_mw > p_nom:
            raise CommercialBindingError(
                f"{agreement.kind} cap {agreement.import_cap_mw} MW exceeds the PoC Link's "
                f"p_nom {p_nom} MW")
    return fee


def _available_mask(n, agreement: ConnectionAgreement, timezone: str | None):
    """(closed-snapshot mask, first open period when it moved, else None)."""
    start = pd.Timestamp(agreement.available_from)
    if isinstance(n.snapshots, pd.MultiIndex):
        periods = np.asarray(n.snapshots.get_level_values(0).astype(int))
        closed = periods < start.year
        later = [int(p) for p in n.investment_periods if int(p) >= start.year]
        first_open = later[0] if later else None
        moved = first_open is not None and first_open > int(min(n.investment_periods))
        return closed, (first_open if moved else None)
    if timezone is not None:
        start = start.tz_localize(timezone).tz_convert("UTC").tz_localize(None)
    return np.asarray(pd.DatetimeIndex(n.snapshots) < start), None


def apply_connection_agreement(n, agreement: ConnectionAgreement, *, poc_link: str,
                               export_link: str | None = None, timezone: str | None = None,
                               solve_strategy: str = "full",
                               multi_period: bool = False) -> Applied:
    """Apply `agreement` to the PoC (and export) Link for one solve; see the
    module docstring. Validates everything before the first mutation."""
    fee = _validate(n, agreement, poc_link, export_link, solve_strategy, multi_period)
    applied = Applied()
    cap = float(agreement.import_cap_mw)
    fixed_fee_eur = None
    try:
        if agreement.kind == "firm":
            if fee is not None:
                _set_static(n, applied, poc_link, "p_nom_extendable", True)
                _set_static(n, applied, poc_link, "p_nom_min", 0.0)
                _set_static(n, applied, poc_link, "p_nom_max", cap)
                setattr(n, FEE_SPEC_ATTR, {"link": poc_link, "fee_eur_per_mw_year": fee})

                def drop_spec() -> None:
                    for attr in (FEE_SPEC_ATTR, FEE_BUILT_ATTR):
                        if hasattr(n, attr):
                            delattr(n, attr)

                applied._undo.append(drop_spec)
            elif bool(n.links.at[poc_link, "p_nom_extendable"]):
                _set_static(n, applied, poc_link, "p_nom_max",
                            min(float(n.links.at[poc_link, "p_nom_max"]), cap))
            else:
                _set_static(n, applied, poc_link, "p_nom", cap)
        else:
            p_nom = float(n.links.at[poc_link, "p_nom"])
            dynamic = agreement.kind == "non_firm_dynamic" or agreement.envelope is not None
            if dynamic:
                limit = np.minimum(_envelope_mw(n, poc_link), cap) / p_nom
            else:  # non_firm_static, or fca without an envelope
                limit = np.full(len(n.snapshots), cap / p_nom)
            if dynamic or poc_link in n.links_t.p_max_pu.columns:
                _set_p_max_pu_t(n, applied, poc_link,
                                np.minimum(_base_p_max_pu(n, poc_link), limit))
            else:
                _set_static(n, applied, poc_link, "p_max_pu",
                            min(float(n.links.at[poc_link, "p_max_pu"]), cap / p_nom))
            if fee is not None:
                fixed_fee_eur = fee * cap * horizon_years(n)
        if agreement.export_cap_mw is not None:
            _set_static(n, applied, export_link, "p_nom", float(agreement.export_cap_mw))

        closed, first_open = _available_mask(n, agreement, timezone)
        if closed.any():
            _set_p_max_pu_t(n, applied, poc_link,
                            np.where(closed, 0.0, _base_p_max_pu(n, poc_link)))
        if first_open is not None:
            _set_static(n, applied, poc_link, "build_year", int(first_open))
    except BaseException:
        applied.undo()
        raise

    def commit() -> None:
        built = getattr(n, FEE_BUILT_ATTR, None)
        if built is not None:
            n.meta[META_FEE] = built
        else:
            n.meta.pop(META_FEE, None)
        if fixed_fee_eur is not None:
            n.meta[META_FIXED_FEE] = {"eur": float(fixed_fee_eur), "kind": agreement.kind,
                                      "link": poc_link}
        else:
            n.meta.pop(META_FIXED_FEE, None)

    # Commit runs AFTER undo in run_simulation, which drops the transient
    # attributes; read the built fee at undo time instead.
    built_box: dict = {}

    def keep_built() -> None:
        if hasattr(n, FEE_BUILT_ATTR):
            built_box["v"] = getattr(n, FEE_BUILT_ATTR)

    applied._undo.append(keep_built)  # last in, so it runs FIRST on undo (reverse order)

    def commit_from_box() -> None:
        if "v" in built_box:
            setattr(n, FEE_BUILT_ATTR, built_box["v"])
        try:
            commit()
        finally:
            if hasattr(n, FEE_BUILT_ATTR):
                delattr(n, FEE_BUILT_ATTR)

    applied._commit.append(commit_from_box)
    applied.facts = {"connection": {
        "kind": agreement.kind, "poc_link": poc_link, "import_cap_mw": cap,
        "fee_eur_per_mw_year": fee, "fee_in_lp": agreement.kind == "firm" and fee is not None,
        "fixed_fee_eur": fixed_fee_eur, "available_from": agreement.available_from.isoformat(),
        "snapshots_before_available": int(closed.sum())}}
    return applied


def apply_for_config(n, commercial: dict | None, *, solve_strategy: str = "full",
                     multi_period: bool = False) -> Applied | None:
    """The agreement in a `SolverConfig.commercial` dict, applied; None if none."""
    if not commercial:
        return None
    cfg = CommercialConfig.model_validate(commercial)
    if cfg.connection is None:
        return None
    return apply_connection_agreement(n, cfg.connection, poc_link=cfg.poc_link,
                                      export_link=cfg.export_link, timezone=cfg.timezone,
                                      solve_strategy=solve_strategy, multi_period=multi_period)


def apply_commercial_for_solve(n, commercial: dict | None, *, log=None,
                               solve_strategy: str = "full",
                               multi_period: bool = False) -> Applied:
    """Everything the commercial layer does to the network for ONE solve: PoC
    energy prices (WP1.3), then the connection agreement (WP1.4). Undo reverses
    both; commit (after a successful solve) persists the price frame and fee.
    With no commercial config nothing is mutated and commit clears stale records."""
    from services.commercial.lp_bindings import materialise_poc_prices

    prices = materialise_poc_prices(n, commercial, log=log)
    try:
        conn = apply_for_config(n, commercial, solve_strategy=solve_strategy,
                                multi_period=multi_period)
    except BaseException:
        prices.undo()
        raise
    if conn is None:
        def clear_fee() -> None:
            n.meta.pop(META_FEE, None)
            n.meta.pop(META_FIXED_FEE, None)

        conn = Applied()
        conn._commit.append(clear_fee)
    elif log is not None:
        log(f"[COMMERCIAL] connection agreement applied: {conn.facts['connection']}")
    return prices.chain(conn)


# ── the LP term (called from lp_bindings' extra_functionality wrapper) ─────


def add_fee_term(n) -> None:
    """Objective += Σ_{p active} w_obj(p) · fee · nyears(p) · p_nom[link].

    Runs inside `extra_functionality` (after the modelling assumptions, so
    `w_obj` is the discounted weight the LP uses). Records the per-period
    €/MW (without w_obj, which `cost_breakdown` applies as it does to every
    capex) for `commit()`.
    """
    spec = getattr(n, FEE_SPEC_ATTR, None)
    if not spec:
        return
    link, fee = spec["link"], float(spec["fee_eur_per_mw_year"])
    ny = nyears_by_period(n)
    periods = active_periods(n, link)
    if isinstance(n.snapshots, pd.MultiIndex):
        w_obj = n.investment_period_weightings["objective"]
        coef = sum(float(w_obj.loc[p]) * fee * ny[p] for p in periods)
    else:
        coef = fee * ny[None]
    p_nom = n.model["Link-p_nom"].sel(name=link)
    n.model.objective += coef * p_nom
    setattr(n, FEE_BUILT_ATTR, {
        "link": link, "fee_eur_per_mw_year": fee,
        "eur_per_mw_by_period": {("_" if p is None else str(p)): fee * ny[p] for p in periods}})


# ── FCA stress entry (WP1.4b) ──────────────────────────────────────────────


def _site_load(n) -> np.ndarray:
    """Total load per snapshot (MW): static p_set, overridden by any profile."""
    total = np.zeros(len(n.snapshots))
    p_set_t = n.loads_t.p_set
    for name in n.loads.index:
        if name in p_set_t.columns:
            total += p_set_t[name].to_numpy(dtype=float)
        else:
            total += float(n.loads.at[name, "p_set"])
    return total


def fca_stress_entry(n, agreement: ConnectionAgreement, *, poc_link: str) -> dict:
    """The FCA curtailment hours as a `profiles` stress entry on the PoC Link.

    Which hours are curtailed is not in the agreement, so the entry takes the
    deterministic worst case. It zeroes the highest-load snapshots (earliest
    first on ties) until their weighted hours reach
    `curtailment_hours_per_year × horizon years`, and discloses the choice as
    `fca_synthetic_hours`. Every other snapshot keeps the agreement's normal
    availability. `frequency_per_year = 1`: the curtailment is a yearly
    condition, not a rare event.
    """
    if agreement.kind != "fca" or agreement.curtailment_hours_per_year is None:
        raise CommercialBindingError("fca_stress_entry needs an fca agreement with "
                                     "curtailment_hours_per_year")
    p_nom = float(n.links.at[poc_link, "p_nom"])
    cap = float(agreement.import_cap_mw)
    base = (np.minimum(_envelope_mw(n, poc_link), cap) / p_nom if agreement.envelope is not None
            else np.full(len(n.snapshots), cap / p_nom))
    base = np.minimum(_base_p_max_pu(n, poc_link), base)
    weights = n.snapshot_weightings.objective.to_numpy(dtype=float)
    target_h = float(agreement.curtailment_hours_per_year) * weights.sum() / _HOURS_PER_YEAR
    order = np.argsort(-_site_load(n), kind="stable")
    step = float(np.median(weights)) if len(weights) else 1.0
    k = int(round(target_h / step)) if step > 0 else 0
    k = max(0, min(k, len(order)))
    series = base.copy()
    series[order[:k]] = 0.0
    slug = re.sub(r"[^a-z0-9_-]+", "_", poc_link.lower()).strip("_")[:55] or "poc"
    return {"id": f"fca_{slug}", "kind": "profiles", "frequency_per_year": 1.0,
            "label": f"FCA curtailment on {poc_link} ({agreement.curtailment_hours_per_year:g} h/yr)",
            "disclosure": FCA_DISCLOSURE,
            "links_p_max_pu": {poc_link: [float(v) for v in series]}}


def register_fca_entry(project_dir, entry: dict) -> None:
    """Add or replace the entry (by id) in the project's stress registry."""
    from services.adequacy import stress

    current = [e for e in stress.load_scenarios(project_dir) if e.get("id") != entry["id"]]
    stress.save_scenarios(project_dir, current + [entry])
