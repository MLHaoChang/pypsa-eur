"""
Tariff rating engine — core (Edge Investment Case P1 WP1.2; spec §5.5).

Rates a dispatch against a `Tariff` item by item and interval by interval. It
is the ORACLE the dispatch-grade LP bindings are checked against, so it knows
nothing about the LP: plain pandas in, frames out.

Conventions
-----------
* ``dispatch`` has columns ``import_mw`` / ``export_mw`` (MW, ≥ 0) on a
  DatetimeIndex. A tz-aware index is converted to the tariff's ``timezone``
  for period matching; a naive index is taken to be local already. Each row
  is one interval of ``step_hours`` (inferred from the index when omitted), so
  a DST day rates its 92 or 100 real quarter-hours, each once.
* energy (kWh) = MW × step_hours × 1000.
* ``amount`` is signed from the SITE's view: + cost, − revenue.
* A TOU item's periods are tried in order on the LOCAL clock; the first match
  wins. An interval no period covers is NOT rated: its amount is NaN, the item
  total is ``None`` and the item is flagged (ADR-0001 — never a silent 0).
* ``fixed`` items with unit ``per_month`` are pro-rated by the local calendar
  days the dispatch covers in each month.
* ``demand`` / ``capacity`` items (and anything measured on ``peak_import``)
  are P2 WP2.1: listed in ``unsupported_items``, never priced here.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from models.commercial import Tariff, TariffItem

_KWH_PER_MWH = 1000.0


@dataclass
class RatingResult:
    lines: pd.DataFrame            # interval-rated items: interval, tariff_item, quantity_kwh, rate, amount
    fixed_lines: pd.DataFrame      # month, tariff_item, days_covered, days_in_month, amount
    monthly: pd.DataFrame          # index "YYYY-MM" (local), columns = items
    annual: pd.DataFrame           # index year (local), columns = items
    per_item: dict[str, float | None]
    # None when ANY item is unrated or unsupported: a bill missing its demand
    # charge is not a total (ADR-0001). `total_supported` is the honest partial.
    total: float | None
    total_supported: float | None
    flags: dict[str, list[str]] = field(default_factory=dict)
    unsupported_items: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unsupported_items and not any(self.flags.values())


def _infer_step_hours(idx: pd.DatetimeIndex) -> float:
    if len(idx) < 2:
        raise ValueError("step_hours must be given for a dispatch with fewer than two intervals")
    deltas = np.diff(idx.asi8) / 3.6e12  # ns → hours
    step = float(np.median(deltas))
    if step <= 0:
        raise ValueError("dispatch index must be increasing")
    return step


def _local(idx: pd.DatetimeIndex, timezone: str | None) -> pd.DatetimeIndex:
    if idx.tz is not None and timezone:
        return idx.tz_convert(timezone)
    return idx


def _quantity_mw(item: TariffItem, imp: np.ndarray, exp: np.ndarray) -> np.ndarray:
    if item.measured_on == "import":
        return imp
    if item.measured_on == "export":
        return exp
    # net: the flow in the item's billing direction, floored at 0
    net = imp - exp
    return np.clip(net if item.direction == "cost" else -net, 0.0, None)


def _rates(item: TariffItem, local: pd.DatetimeIndex) -> np.ndarray:
    n = len(local)
    rates = np.full(n, np.nan)
    matched = np.zeros(n, dtype=bool)
    month = np.asarray(local.month)
    weekday = np.asarray(local.weekday)
    hour = np.asarray(local.hour + local.minute / 60.0, dtype=float)
    for p in item.periods:
        mask = ~matched
        if p.months:
            mask &= np.isin(month, p.months)
        if p.weekdays:
            mask &= np.isin(weekday, p.weekdays)
        if p.start_hour is not None:
            mask &= (hour >= p.start_hour) & (hour < p.end_hour)
        rates[mask] = p.rate
        matched |= mask
    return rates


def _supported_interval_item(item: TariffItem) -> bool:
    return item.kind in ("energy", "certificate", "tax_levy") and item.unit == "per_kwh" \
        and item.measured_on != "peak_import" and not item.tiers


def rate(dispatch: pd.DataFrame, tariff: Tariff, *, step_hours: float | None = None,
         timezone: str | None = None, meter_history: pd.DataFrame | None = None) -> RatingResult:
    """Rate ``dispatch`` against ``tariff``. ``meter_history`` is accepted for
    the P2 demand/ratchet items and unused by the core."""
    idx = pd.DatetimeIndex(dispatch.index)
    step_h = float(step_hours) if step_hours is not None else _infer_step_hours(idx)
    local = _local(idx, timezone)
    imp = dispatch["import_mw"].to_numpy(dtype=float) if "import_mw" in dispatch else np.zeros(len(idx))
    exp = dispatch["export_mw"].to_numpy(dtype=float) if "export_mw" in dispatch else np.zeros(len(idx))
    month_key = np.asarray(local.strftime("%Y-%m"))
    sign_of = {"cost": 1.0, "revenue": -1.0}

    interval_frames: list[pd.DataFrame] = []
    fixed_rows: list[dict] = []
    per_item: dict[str, float | None] = {}
    flags: dict[str, list[str]] = {}
    unsupported: list[str] = []
    monthly_parts: dict[str, pd.Series] = {}

    for item in tariff.items:
        sign = sign_of[item.direction]
        if _supported_interval_item(item):
            q_kwh = _quantity_mw(item, imp, exp) * step_h * _KWH_PER_MWH
            r = _rates(item, local)
            amount = sign * q_kwh * r
            interval_frames.append(pd.DataFrame({
                "interval": idx, "tariff_item": item.id, "quantity_kwh": q_kwh,
                "rate": r, "amount": amount}))
            n_unrated = int(np.isnan(r).sum())
            flags[item.id] = [f"unrated_intervals:{n_unrated}"] if n_unrated else []
            per_item[item.id] = None if n_unrated else float(np.nansum(amount))
            monthly_parts[item.id] = pd.Series(amount).groupby(month_key).sum(min_count=1)
            if n_unrated:
                monthly_parts[item.id] = pd.Series(amount).groupby(month_key).apply(
                    lambda s: np.nan if s.isna().any() else s.sum())
        elif item.kind == "fixed" and item.unit == "per_month":
            if len(item.periods) != 1 or item.periods[0].months or item.periods[0].weekdays \
                    or item.periods[0].start_hour is not None:
                unsupported.append(item.id)
                continue
            monthly_rate = item.periods[0].rate
            dates = pd.Series(pd.DatetimeIndex(local.normalize()).tz_localize(None)
                              if local.tz is not None else local.normalize()).drop_duplicates()
            by_month: dict[str, float] = {}
            for key, grp in dates.groupby(dates.dt.strftime("%Y-%m")):
                y, m = map(int, key.split("-"))
                dim = calendar.monthrange(y, m)[1]
                days = int(grp.nunique())
                amt = sign * monthly_rate * days / dim
                by_month[key] = amt
                fixed_rows.append({"month": key, "tariff_item": item.id,
                                   "days_covered": days, "days_in_month": dim, "amount": amt})
            flags[item.id] = []
            per_item[item.id] = float(sum(by_month.values()))
            monthly_parts[item.id] = pd.Series(by_month)
        else:
            unsupported.append(item.id)

    lines = (pd.concat(interval_frames, ignore_index=True) if interval_frames else
             pd.DataFrame(columns=["interval", "tariff_item", "quantity_kwh", "rate", "amount"]))
    fixed_lines = pd.DataFrame(fixed_rows, columns=["month", "tariff_item", "days_covered",
                                                    "days_in_month", "amount"])
    monthly = pd.DataFrame(monthly_parts).sort_index()
    monthly.index.name = "month"
    annual = monthly.groupby(monthly.index.str.slice(0, 4)).sum(min_count=1)
    annual.index.name = "year"
    totals = list(per_item.values())
    total_supported = None if any(v is None for v in totals) else float(sum(totals))
    total = None if unsupported else total_supported
    return RatingResult(lines=lines, fixed_lines=fixed_lines, monthly=monthly, annual=annual,
                        per_item=per_item, total=total, total_supported=total_supported,
                        flags=flags,
                        unsupported_items=unsupported)
