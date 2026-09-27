"""
Connection agreements → the PoC Link, for one solve (Edge Investment Case
P1 WP1.4a; spec §5 table).

`apply_connection_agreement(n, agreement, poc_link=, export_link=)` mutates the
PoC Link and returns an `Applied` whose `undo()` restores every attribute it
touched. It follows the `apply_archetype_pack_detailed().undo()` discipline:
the user's model on disk never carries the transform. `run_simulation` applies
it just before the modelling assumptions and chains the undo after their
restore.

  * `firm` with a `capacity_fee`: the Link becomes extendable in
    [0, import_cap] and pays the fee as `capital_cost`, so the optimiser sizes
    the connection. Without a fee, `p_nom` is fixed at the cap.
  * `non_firm_static`: the physical `p_nom` stays; `p_max_pu = cap / p_nom`. A
    fee is charged on the contracted cap (`capital_cost += fee · cap / p_nom`).
  * `export_cap_mw`: the export Link's `p_nom`.
  * `available_from`: `p_max_pu = 0` on snapshots before the date. On a
    multi-period axis `build_year` also moves to that year, so the connection
    is neither used nor paid for in earlier periods.
  * `non_firm_dynamic` / `fca` (envelopes) are WP1.4b.

Fee units: `per_kw_year` and `per_kw_month` (×12), converted to €/MW/yr. The
LP charges `capital_cost` once per horizon (PyPSA does not scale it on a
single-period axis), so the annual fee is scaled by the horizon length in
years (Σ objective weights / 8760). On a multi-period axis the period
weightings already count years, so the annual fee is used as is.
`network_capacity_row` recomputes the same figure from the persisted config
and `p_nom_opt` for `cost_breakdown`, so the objective gap stays 0.

Pure service: imports neither routers nor `solver_service`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from models.commercial import CommercialConfig, ConnectionAgreement, TariffItem
from services.commercial.lp_bindings import CommercialBindingError

_HOURS_PER_YEAR = 8760.0
_UNIT_TO_PER_MW_YEAR = {"per_kw_year": 1000.0, "per_kw_month": 12_000.0}

__all__ = ["Applied", "CommercialBindingError", "apply_connection_agreement",
           "apply_for_config", "network_capacity_row"]


@dataclass
class Applied:
    facts: dict = field(default_factory=dict)
    _undo: list[Callable[[], None]] = field(default_factory=list)
    _done: bool = False

    def undo(self) -> None:
        """Restore every mutated attribute, in reverse order; runs once."""
        if self._done:
            return
        self._done = True
        for fn in reversed(self._undo):
            fn()


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
    """Years the LP's `capital_cost` must cover: the horizon on a single-period
    axis, 1 on a multi-period axis (period weightings count the years)."""
    if isinstance(n.snapshots, pd.MultiIndex):
        return 1.0
    return float(n.snapshot_weightings.objective.sum()) / _HOURS_PER_YEAR


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


def _validate(n, agreement: ConnectionAgreement, poc_link: str, export_link: str | None):
    if poc_link not in n.links.index:
        raise CommercialBindingError(f"commercial.poc_link {poc_link!r} is not a Link")
    if agreement.kind in ("non_firm_dynamic", "fca"):
        raise CommercialBindingError(
            f"connection kind {agreement.kind!r} (envelope) is bound in WP1.4b")
    if agreement.export_cap_mw is not None and export_link is None:
        raise CommercialBindingError("connection.export_cap_mw needs commercial.export_link")
    if export_link is not None and export_link not in n.links.index:
        raise CommercialBindingError(f"commercial.export_link {export_link!r} is not a Link")
    fee = fee_eur_per_mw_year(agreement.capacity_fee) if agreement.capacity_fee else None
    if agreement.kind == "non_firm_static":
        p_nom = float(n.links.at[poc_link, "p_nom"])
        if bool(n.links.at[poc_link, "p_nom_extendable"]) or p_nom <= 0:
            raise CommercialBindingError(
                "non_firm_static needs a PoC Link with a fixed physical p_nom > 0")
        if agreement.import_cap_mw > p_nom:
            raise CommercialBindingError(
                f"non_firm_static cap {agreement.import_cap_mw} MW exceeds the PoC Link's "
                f"p_nom {p_nom} MW")
    return fee


def apply_connection_agreement(n, agreement: ConnectionAgreement, *, poc_link: str,
                               export_link: str | None = None) -> Applied:
    """Apply `agreement` to the PoC (and export) Link; see the module docstring.
    Validates everything before the first mutation."""
    fee = _validate(n, agreement, poc_link, export_link)
    applied = Applied()
    years = horizon_years(n)
    cap = float(agreement.import_cap_mw)
    fee_lp = None if fee is None else fee * years
    try:
        if agreement.kind == "firm":
            if fee_lp is not None:
                _set_static(n, applied, poc_link, "p_nom_extendable", True)
                _set_static(n, applied, poc_link, "p_nom_min", 0.0)
                _set_static(n, applied, poc_link, "p_nom_max", cap)
                _set_static(n, applied, poc_link, "capital_cost",
                            float(n.links.at[poc_link, "capital_cost"]) + fee_lp)
            else:
                _set_static(n, applied, poc_link, "p_nom_extendable", False)
                _set_static(n, applied, poc_link, "p_nom", cap)
        else:  # non_firm_static
            p_nom = float(n.links.at[poc_link, "p_nom"])
            _set_static(n, applied, poc_link, "p_max_pu",
                        min(float(n.links.at[poc_link, "p_max_pu"]), cap / p_nom))
            if fee_lp is not None:
                _set_static(n, applied, poc_link, "capital_cost",
                            float(n.links.at[poc_link, "capital_cost"]) + fee_lp * cap / p_nom)
        if agreement.export_cap_mw is not None:
            _set_static(n, applied, export_link, "p_nom", float(agreement.export_cap_mw))

        ts = n.snapshots.get_level_values(-1) if isinstance(n.snapshots, pd.MultiIndex) \
            else n.snapshots
        before = np.asarray(pd.DatetimeIndex(ts) < pd.Timestamp(agreement.available_from))
        if before.any():
            base = (n.links_t.p_max_pu[poc_link].to_numpy(dtype=float)
                    if poc_link in n.links_t.p_max_pu.columns
                    else np.full(len(n.snapshots), float(n.links.at[poc_link, "p_max_pu"])))
            _set_p_max_pu_t(n, applied, poc_link, np.where(before, 0.0, base))
            if isinstance(n.snapshots, pd.MultiIndex):
                _set_static(n, applied, poc_link, "build_year", int(agreement.available_from.year))
    except BaseException:
        applied.undo()
        raise
    applied.facts = {"kind": agreement.kind, "poc_link": poc_link, "import_cap_mw": cap,
                     "fee_eur_per_mw_year": fee, "horizon_years": years,
                     "available_from": agreement.available_from.isoformat(),
                     "snapshots_before_available": int(before.sum())}
    return applied


def apply_for_config(n, commercial: dict | None) -> Applied | None:
    """The agreement in a `SolverConfig.commercial` dict, applied; None if none."""
    if not commercial:
        return None
    cfg = CommercialConfig.model_validate(commercial)
    if cfg.connection is None:
        return None
    return apply_connection_agreement(n, cfg.connection, poc_link=cfg.poc_link,
                                      export_link=cfg.export_link)


def network_capacity_row(n, commercial: dict | None) -> float | None:
    """The connection fee the LP charged, from PERSISTED data (config +
    `p_nom_opt`), for `cost_breakdown`. None when there is no fee. The fee is
    not on the Link after the undo, so this row is ADDED to the total."""
    if not commercial:
        return None
    try:
        cfg = CommercialConfig.model_validate(commercial)
    except Exception:  # noqa: BLE001 — a config that cannot bind has no row
        return None
    agr = cfg.connection
    if agr is None or agr.capacity_fee is None or cfg.poc_link not in n.links.index:
        return None
    try:
        fee = fee_eur_per_mw_year(agr.capacity_fee)
    except CommercialBindingError:
        return None
    if agr.kind == "firm":
        cap_mw = float(n.links.at[cfg.poc_link, "p_nom_opt"])
    elif agr.kind == "non_firm_static":
        cap_mw = float(agr.import_cap_mw)
    else:
        return None
    if isinstance(n.snapshots, pd.MultiIndex):
        w = n.investment_period_weightings["objective"]
        active = [p for p in w.index if int(p) >= int(agr.available_from.year)] \
            if int(agr.available_from.year) > int(min(w.index)) else list(w.index)
        return float(fee * cap_mw * w.loc[active].sum())
    return float(fee * horizon_years(n) * cap_mw)
