"""
Tariff rating engine — core (Edge Investment Case P1 WP1.2; spec §5.5).

Rates a dispatch against a `Tariff` item by item and interval by interval. It
is the ORACLE the dispatch-grade LP bindings are checked against, so it knows
nothing about the LP and refuses to turn bad input into a confident number.

Input contract (violations raise ``ValueError`` / ``TypeError``)
-------------------------------------------------------------
* ``dispatch`` has columns ``import_mw`` and ``export_mw`` (MW, ≥ 0; NaN is
  allowed but flagged) on a DatetimeIndex whose INSTANTS are strictly
  increasing (duplicates would double-bill).
* ``step_hours`` is REQUIRED: a float, a per-row Series of hours, or a 1-D
  array of the dispatch's length (time-segmented snapshots). Rows may not
  overlap (row start + duration must not pass the next row's start). Gaps are
  allowed (representative weeks).
* ``represents_hours`` (optional, same shapes): the real-year hours each row
  STANDS FOR — the snapshot weightings of a representative-period model. When
  given, energy is MW × represents_hours; when omitted it is MW × step_hours.
* ``billing_period=(start, end)`` is half-open ``[start, end)`` on the local
  clock; it bills fixed items for the months it spans. It is REQUIRED with
  ``represents_hours`` on a dispatch with gaps, so energy and fixed charges
  describe the same period. Under ``represents_hours`` the ``monthly`` frame puts
  each sampled row's represented energy in its own month — it is not a
  calendar bill (note ``monthly_shows_sampled_months_only``).
* ``timezone``: required when the index is tz-aware (TOU windows and billing
  months are LOCAL); refused when the index is naive, because a naive local
  index cannot represent the repeated fall-back hour. A naive index with no
  timezone is a wall clock without DST.

Semantics
---------
* energy (kWh) = MW × row hours × 1000; ``amount`` signed from the SITE's view:
  + cost, − revenue.
* TOU periods are tried in order on the LOCAL clock; first match wins.
* ``measured_on="net"`` is per-interval (instantaneous) netting: a cost item
  bills net import floored at 0, a revenue item pays net export floored at 0.
  Monthly netting (NEM 1.0) is not expressible here.
* An interval that no period covers, or whose quantity is NaN, is NOT rated:
  the item total is None, its month and year are NaN, and the item is flagged
  (``unrated_intervals:N`` / ``nan_quantity:N``) — ADR-0001, never a silent 0.
* ``fixed`` ``per_month`` items are pro-rated by the HOURS covered in each local
  month over that month's real hours (743 / 745 in DST months). With gaps and
  no ``billing_period``, the item carries the note
  ``fixed_prorated_on_partial_coverage``; ``billing_period=(start, end)`` bills
  the months it spans instead (representative weeks standing for a year).
  With a ``billing_period`` over gappy data and NO ``represents_hours``, energy
  items cover only the sampled rows: they carry ``energy_on_partial_coverage``
  and ``total`` is None — 12 months of fixed charges plus 4 weeks of energy is
  not a bill.
* A dispatch step different from the item's ``settlement`` is DISCLOSED in
  ``notes`` (``resolution:dispatch_Xh_settlement_Yh``); it does not make the
  result incomplete.
* Anything the core cannot price is listed in ``unsupported_items`` with a
  reason in ``flags`` (tiers → P1 WP1.5c, demand/capacity → P2 WP2.1, …).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from models.commercial import Tariff, TariffItem

_KWH_PER_MWH = 1000.0
_SETTLEMENT_HOURS = {"15min": 0.25, "30min": 0.5, "h": 1.0}
_REQUIRED = ("import_mw", "export_mw")


@dataclass
class RatingResult:
    lines: pd.DataFrame            # interval, tariff_item, quantity_kwh, rate, amount
    fixed_lines: pd.DataFrame      # month, tariff_item, hours_covered, hours_in_month, amount
    monthly: pd.DataFrame          # index "YYYY-MM" (local), columns = items; NaN = unrated
    annual: pd.DataFrame           # index "YYYY" (local); NaN if any month is NaN
    per_item: dict[str, float | None]
    # None when ANY item is unrated or unsupported: a bill missing its demand
    # charge is not a total (ADR-0001). `total_supported` is the honest partial.
    total: float | None
    total_supported: float | None
    flags: dict[str, list[str]] = field(default_factory=dict)   # incompleteness
    notes: dict[str, list[str]] = field(default_factory=dict)   # disclosures
    unsupported_items: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        partial = any("energy_on_partial_coverage" in v for v in self.notes.values())
        return not self.unsupported_items and not any(self.flags.values()) and not partial


# ── input validation ──────────────────────────────────────────────────────────


def _validate(dispatch: pd.DataFrame, timezone: str | None) -> pd.DatetimeIndex:
    for col in _REQUIRED:
        if col not in dispatch.columns:
            raise ValueError(f"dispatch is missing the required column {col!r}")
    if not isinstance(dispatch.index, pd.DatetimeIndex):
        raise ValueError("dispatch must have a DatetimeIndex")
    idx = dispatch.index
    if idx.tz is not None and not timezone:
        raise ValueError("a tz-aware dispatch needs a `timezone` to rate TOU windows and "
                         "billing months on the local clock")
    if idx.tz is None and timezone:
        raise ValueError("a naive dispatch index cannot be rated in a timezone: a naive "
                         "local clock cannot represent the repeated fall-back hour — "
                         "localise the index first")
    instants = idx.asi8
    if len(instants) > 1 and not (np.diff(instants) > 0).all():
        raise ValueError("dispatch instants must be strictly increasing (no duplicates)")
    if len(idx) == 0:
        raise ValueError("dispatch is empty")
    vals = dispatch[list(_REQUIRED)].to_numpy(dtype=float)
    present = vals[~np.isnan(vals)]
    if not np.isfinite(present).all():
        raise ValueError("import_mw / export_mw must be finite (NaN is flagged, inf is refused)")
    if (present < 0).any():
        raise ValueError("import_mw / export_mw must not be negative")
    return idx


def _per_row(idx: pd.DatetimeIndex, value, what: str) -> np.ndarray:
    if isinstance(value, pd.Series):
        arr = value.reindex(idx).to_numpy(dtype=float)
        if np.isnan(arr).any():
            raise ValueError(f"{what} Series must cover every dispatch row")
    elif isinstance(value, (np.ndarray, list, tuple)):
        arr = np.asarray(value, dtype=float)
        if arr.ndim != 1 or len(arr) != len(idx):
            raise ValueError(f"{what} array length {arr.shape} must match the dispatch length {len(idx)}")
    else:
        arr = np.full(len(idx), float(value))
    if not np.isfinite(arr).all() or (arr <= 0).any():
        raise ValueError(f"{what} must be positive finite hours")
    return arr


def _durations(idx: pd.DatetimeIndex, step_hours) -> np.ndarray:
    dur = _per_row(idx, step_hours, "step_hours")
    if len(idx) > 1:
        gap_h = np.diff(idx.asi8) / 3.6e12
        if (dur[:-1] > gap_h + 1e-9).any():
            raise ValueError("dispatch rows overlap: a row's duration runs past the next row")
    return dur


# ── rating helpers ────────────────────────────────────────────────────────────


def _quantity_mw(item: TariffItem, imp: np.ndarray, exp: np.ndarray) -> np.ndarray:
    if item.measured_on == "import":
        return imp
    if item.measured_on == "export":
        return exp
    net = imp - exp  # per-interval netting, floored at 0 in the billing direction
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


def _unsupported_reason(item: TariffItem) -> str | None:
    if item.kind in ("demand", "capacity") or item.measured_on == "peak_import":
        return "unsupported:demand_P2_WP2.1"
    if item.tiers:
        return "unsupported:tiers_P1_WP1.5c"
    if item.kind == "fixed":
        if item.unit != "per_month":
            return f"unsupported:unit_{item.unit}_for_fixed"
        p = item.periods[0]
        if len(item.periods) != 1 or p.months or p.weekdays or p.start_hour is not None:
            return "unsupported:fixed_with_windows"
        return None
    if item.unit != "per_kwh":
        return f"unsupported:unit_{item.unit}_for_{item.kind}"
    return None


def _nan_aware_sum(values: pd.Series, keys) -> pd.Series:
    """Group sum where any NaN in a group makes the group NaN."""
    frame = pd.DataFrame({"v": values.to_numpy(), "k": np.asarray(keys)})
    sums = frame.groupby("k")["v"].sum()
    bad = frame["v"].isna().groupby(frame["k"]).any()
    sums[bad] = np.nan
    return sums


def _month_hours(key: str, tz: str | None) -> float:
    y, m = map(int, key.split("-"))
    if tz is None:
        return calendar.monthrange(y, m)[1] * 24.0
    start = pd.Timestamp(year=y, month=m, day=1).tz_localize(tz)
    end = (pd.Timestamp(year=y, month=m, day=1) + pd.offsets.MonthBegin(1)).tz_localize(tz)
    return (end - start).total_seconds() / 3600.0


def _hours_by_month_in_period(start, end, tz: str | None) -> dict[str, float]:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    if tz is not None:
        s = s.tz_localize(tz) if s.tz is None else s.tz_convert(tz)
        e = e.tz_localize(tz) if e.tz is None else e.tz_convert(tz)
    out: dict[str, float] = {}
    cur = s
    while cur < e:
        nxt_naive = (cur.tz_localize(None) if cur.tz is not None else cur).normalize().replace(day=1) \
            + pd.offsets.MonthBegin(1)
        nxt = nxt_naive.tz_localize(tz) if tz is not None else nxt_naive
        stop = min(nxt, e)
        key = cur.strftime("%Y-%m")
        out[key] = out.get(key, 0.0) + (stop - cur).total_seconds() / 3600.0
        cur = stop
    return out


# ── the engine ────────────────────────────────────────────────────────────────


def rate(dispatch: pd.DataFrame, tariff: Tariff, *, step_hours, timezone: str | None,
         billing_period: tuple | None = None, represents_hours=None,
         meter_history: pd.DataFrame | None = None) -> RatingResult:
    """Rate ``dispatch`` against ``tariff`` (see module docstring).
    ``meter_history`` is accepted for the P2 demand/ratchet items and unused here."""
    idx = _validate(dispatch, timezone)
    dur = _durations(idx, step_hours)
    energy_h = dur if represents_hours is None else _per_row(idx, represents_hours, "represents_hours")
    local = idx.tz_convert(timezone) if idx.tz is not None else idx
    imp = dispatch["import_mw"].to_numpy(dtype=float)
    exp = dispatch["export_mw"].to_numpy(dtype=float)
    month_key = np.asarray(local.strftime("%Y-%m"))
    sign_of = {"cost": 1.0, "revenue": -1.0}
    covered_span_h = ((idx.asi8[-1] - idx.asi8[0]) / 3.6e12 + dur[-1]) if len(idx) else 0.0
    has_gaps = len(idx) > 0 and dur.sum() < covered_span_h - 1e-9
    if represents_hours is not None and has_gaps and billing_period is None:
        # Energy would be scaled to the represented period while fixed items
        # bill only the sampled weeks (review C4): the two must describe the
        # same period, so the caller states it.
        raise ValueError("represents_hours over a dispatch with gaps needs a billing_period "
                         "stating the period the rows represent")

    interval_frames: list[pd.DataFrame] = []
    fixed_rows: list[dict] = []
    per_item: dict[str, float | None] = {}
    flags: dict[str, list[str]] = {}
    notes: dict[str, list[str]] = {}
    unsupported: list[str] = []
    monthly_parts: dict[str, pd.Series] = {}

    for item in tariff.items:
        reason = _unsupported_reason(item)
        if reason is not None:
            unsupported.append(item.id)
            flags[item.id] = [reason]
            continue
        sign = sign_of[item.direction]
        if item.kind == "fixed":
            monthly_rate = item.periods[0].rate
            if billing_period is not None:
                covered = _hours_by_month_in_period(billing_period[0], billing_period[1], timezone)
            else:
                covered = pd.Series(dur).groupby(month_key).sum().to_dict()
            by_month: dict[str, float] = {}
            for key, hours in sorted(covered.items()):
                mh = _month_hours(key, timezone)
                amt = sign * monthly_rate * hours / mh
                by_month[key] = amt
                fixed_rows.append({"month": key, "tariff_item": item.id, "hours_covered": hours,
                                   "hours_in_month": mh, "amount": amt})
            flags[item.id] = []
            if has_gaps and billing_period is None:
                notes[item.id] = ["fixed_prorated_on_partial_coverage"]
            per_item[item.id] = float(sum(by_month.values()))
            monthly_parts[item.id] = pd.Series(by_month, dtype=float)
            continue

        q_kwh = _quantity_mw(item, imp, exp) * energy_h * _KWH_PER_MWH
        r = _rates(item, local)
        amount = sign * q_kwh * r
        interval_frames.append(pd.DataFrame({
            "interval": idx, "tariff_item": item.id, "quantity_kwh": q_kwh,
            "rate": r, "amount": amount}))
        item_flags = []
        n_unrated = int(np.isnan(r).sum())
        n_nan_q = int(np.isnan(q_kwh).sum())
        if n_unrated:
            item_flags.append(f"unrated_intervals:{n_unrated}")
        if n_nan_q:
            item_flags.append(f"nan_quantity:{n_nan_q}")
        flags[item.id] = item_flags
        per_item[item.id] = None if item_flags else float(amount.sum())
        if billing_period is not None and has_gaps and represents_hours is None:
            notes.setdefault(item.id, []).append("energy_on_partial_coverage")
        if represents_hours is not None and has_gaps:
            # Each sampled row's represented energy lands in its OWN month; the
            # monthly frame is not a calendar bill (NaN months = no rows).
            notes.setdefault(item.id, []).append("monthly_shows_sampled_months_only")
        monthly_parts[item.id] = _nan_aware_sum(pd.Series(amount), month_key)
        settle_h = _SETTLEMENT_HOURS[item.settlement]
        uniq = np.unique(dur)
        if len(uniq) != 1 or abs(uniq[0] - settle_h) > 1e-9:
            d = f"{uniq[0]:g}h" if len(uniq) == 1 else "mixed"
            notes.setdefault(item.id, []).append(f"resolution:dispatch_{d}_settlement_{settle_h:g}h")

    lines = (pd.concat(interval_frames, ignore_index=True) if interval_frames else
             pd.DataFrame(columns=["interval", "tariff_item", "quantity_kwh", "rate", "amount"]))
    fixed_lines = pd.DataFrame(fixed_rows, columns=["month", "tariff_item", "hours_covered",
                                                    "hours_in_month", "amount"])
    # Annual per item from ITS OWN months: after the join below, a month where
    # an item has no rows would read as NaN ("unrated") — it is not (C1).
    annual_parts = {}
    for c, ser in monthly_parts.items():
        ser = ser.copy()
        ser.index = ser.index.astype(str)
        annual_parts[c] = _nan_aware_sum(ser, np.asarray([m[:4] for m in ser.index]))
    monthly = pd.DataFrame(monthly_parts).sort_index()
    monthly.index = monthly.index.astype(str)
    monthly.index.name = "month"
    annual = pd.DataFrame(annual_parts).sort_index()
    annual.index.name = "year"
    totals = list(per_item.values())
    total_supported = None if any(v is None for v in totals) else float(sum(totals))
    partial_energy = any("energy_on_partial_coverage" in v for v in notes.values())
    total = None if (unsupported or partial_energy) else total_supported
    return RatingResult(lines=lines, fixed_lines=fixed_lines, monthly=monthly, annual=annual,
                        per_item=per_item, total=total, total_supported=total_supported,
                        flags=flags, notes=notes, unsupported_items=unsupported)
