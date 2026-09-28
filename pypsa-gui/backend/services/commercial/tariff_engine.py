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
  reason in ``flags`` (tiers → P1 WP1.5c, ratchets → P1 WP1.5b, capacity → the connection agreement, …); single-rate demand items bill rate × monthly max kW per period window (WP1.5a, `demand_lines`).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from models.commercial import Tariff, TariffItem

_HOURS_PER_YEAR = 8760.0  # capacity items: represented hours → years, a unit
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
    # One row per (month, demand item, period window): the peak and its bill
    # (WP1.5a). Empty when the tariff has no demand items.
    demand_lines: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["month", "tariff_item", "period", "peak_kw", "billed_kw", "rate", "amount"]))

    @property
    def complete(self) -> bool:
        partial = any(("energy_on_partial_coverage" in v) or ("ratchet_seed_missing" in v)
                      or ("capacity_on_partial_coverage" in v)
                      for v in self.notes.values())
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


def _is_demand(item: TariffItem) -> bool:
    return item.kind == "demand" or item.measured_on == "peak_import"


def interval_key(local: pd.DatetimeIndex, freq: str) -> np.ndarray:
    """The start instant (int ns) of each row's demand interval on the local
    clock, WITHOUT re-localising a floored wall time: the repeated fall-back
    hour stays two distinct intervals instead of raising AmbiguousTimeError
    (WP1.5a review round 2 #1)."""
    if local.tz is None:
        return np.asarray(local.floor(freq).asi8)
    wall = local.tz_localize(None)
    return np.asarray(local.asi8 - (wall.asi8 - wall.floor(freq).asi8))


def _period_index(item: TariffItem, local: pd.DatetimeIndex) -> np.ndarray:
    """Index of the first period matching each interval (-1: none) — the
    same first-match rule as `_rates`."""
    n = len(local)
    out = np.full(n, -1)
    month = np.asarray(local.month)
    weekday = np.asarray(local.weekday)
    hour = np.asarray(local.hour + local.minute / 60.0, dtype=float)
    for k, p in enumerate(item.periods):
        mask = out < 0
        if p.months:
            mask &= np.isin(month, p.months)
        if p.weekdays:
            mask &= np.isin(weekday, p.weekdays)
        if p.start_hour is not None:
            mask &= (hour >= p.start_hour) & (hour < p.end_hour)
        out[mask] = k
    return out


def demand_windows(item: TariffItem, local: pd.DatetimeIndex) -> tuple[np.ndarray, list[str],
                                                                       np.ndarray]:
    """(window index per interval (-1: none), window names, fragment index).

    A demand WINDOW is the set of the item's periods sharing a name: a URDB
    demand period is several `[start, end)` fragments, billed on ONE monthly
    peak (IC P2 WP2.1a-0). Unique names reproduce the P1 behaviour. Shared by
    the engine and the LP so both key peaks the same way."""
    frag = _period_index(item, local)
    names = list(dict.fromkeys(p.name for p in item.periods))
    to_window = np.array([names.index(p.name) for p in item.periods])
    window = np.where(frag >= 0, to_window[np.clip(frag, 0, None)], -1)
    return window, names, frag


def window_rate(item: TariffItem, frag_rows: np.ndarray) -> float:
    """The window's rate for a set of its intervals (one month): the rate of
    the fragments that matched them — one value, by the `TariffItem`
    validator (one effective rate per window and month). Raises if not."""
    rates = {float(item.periods[int(k)].rate) for k in np.unique(frag_rows)}
    if len(rates) != 1:
        raise ValueError(f"demand item {item.id!r}: one window matched several rates {sorted(rates)}")
    return rates.pop()


def _history_kw(meter_history) -> dict[str, float]:
    """`meter_history` as {"YYYY-MM": metered peak kW} (a mapping or Series)."""
    if meter_history is None:
        return {}
    items = meter_history.items() if hasattr(meter_history, "items") else meter_history
    return {str(k): float(v) for k, v in items}


def _ratchet_prior(month: str, k: int, lookback: int, actual: dict,
                   meter_history, in_dispatch: set[str]) -> tuple[float | None, bool]:
    """(max ACTUAL peak kW over the `lookback` months before `month` for window
    `k`, whether any of those months is unknown). A month inside the dispatch
    uses its actual peak (none when window `k` has no interval in it); one
    before it uses the metered history. `in_dispatch` is every month of the
    dispatch, whatever the item's windows (WP1.5a review round 3 #2)."""
    hist = _history_kw(meter_history)
    here = pd.Period(month, freq="M")
    values: list[float] = []
    missing = False
    for back in range(1, lookback + 1):
        m = (here - back).strftime("%Y-%m")
        if (m, k) in actual:
            values.append(actual[(m, k)])
        elif m in in_dispatch:
            continue  # modelled month with no interval in this window: no demand
        elif m in hist:
            values.append(hist[m])
        else:
            missing = True
    if any(not np.isfinite(v) for v in values):
        return float("nan"), missing  # an unknown lookback peak: unknown floor (never order-dependent)
    return (max(values) if values else None), missing


def _ratchet_floor_prior(ratchet, month: str, k: int, actual: dict, meter_history,
                        in_dispatch: set[str]) -> tuple[float | None, bool]:
    """(max ACTUAL peak kW the ratchet reads for `month`, window `k`; whether a
    month it needs is unknown) for the three modes (P2 WP2.1a-iii):

    * range, non-cyclic — `_ratchet_prior` (P1: months before, history before the
      horizon);
    * range, cyclic — the `lookback_months` before `month`, wrapping within its
      rate year (January reads December of the SAME year);
    * months — year-wide: the designated months of the rate year.

    Cyclic and months mode read an unmodelled month's meter history under its
    SAME-rate-year key; absent ⇒ unknown (never inferred from other months).
    That key resolves only when the modelled rate year is a METERED year: for a
    future year use non-cyclic range mode with meter history (or `cyclic_year`,
    which reads the modelled months themselves). A NaN lookback peak returns a
    NaN prior — the dependent month is unknown, never floored at random."""
    if ratchet.months is None and not ratchet.cyclic_year:
        return _ratchet_prior(month, k, ratchet.lookback_months, actual, meter_history,
                              in_dispatch)
    hist = _history_kw(meter_history)
    year, mm = int(month[:4]), int(month[5:])
    if ratchet.months is not None:
        wanted = [f"{year}-{d:02d}" for d in ratchet.months]
    else:
        wanted = [f"{year}-{((mm - back - 1) % 12) + 1:02d}"
                  for back in range(1, ratchet.lookback_months + 1)]
    values: list[float] = []
    missing = False
    for m in wanted:
        if (m, k) in actual:
            values.append(actual[(m, k)])
        elif m in in_dispatch:
            continue  # modelled month with no charged interval in this window
        elif m in hist:
            values.append(hist[m])
        else:
            missing = True
    if any(not np.isfinite(v) for v in values):
        return float("nan"), missing  # an unknown lookback peak: unknown floor (never order-dependent)
    return (max(values) if values else None), missing


def _tier_cost(tiers, volume_kwh: np.ndarray) -> np.ndarray:
    """Cumulative cost of `volume_kwh` under `tiers` (each tier's rate applies
    to the volume between its threshold and the next)."""
    th = [t.threshold for t in tiers] + [np.inf]
    out = np.zeros_like(volume_kwh, dtype=float)
    for k, t in enumerate(tiers):
        out += t.rate * np.clip(volume_kwh - th[k], 0.0, th[k + 1] - th[k])
    return out


def is_windowed_tiered(item: TariffItem) -> bool:
    """Tiers whose rates sit on the periods (`tier_rates`, WP2.1a-ii)."""
    return bool(item.tiers) and item.periods[0].tier_rates is not None


def _tier_cost_with(thresholds: list[float], rates: list[float], volume) -> np.ndarray:
    """`_tier_cost` for explicit per-period rates on shared thresholds."""
    th = list(thresholds) + [np.inf]
    v = np.asarray(volume, dtype=float)
    out = np.zeros_like(v)
    for k, r in enumerate(rates):
        out += r * np.clip(v - th[k], 0.0, th[k + 1] - th[k])
    return out


def tiers_are_convex(tiers) -> bool:
    """Rising marginal rates: convex in a cost minimisation."""
    rates = [t.rate for t in tiers]
    return all(b >= a for a, b in zip(rates, rates[1:]))


def _single_catch_all(item: TariffItem) -> bool:
    p = item.periods[0]
    return len(item.periods) == 1 and not p.months and not p.weekdays and p.start_hour is None


def _unsupported_reason(item: TariffItem) -> str | None:
    if item.kind == "capacity":
        # P2 WP2.1a-i: rated on the capacity the caller passes (the adapter
        # passes the PoC's p_nom_opt), one catch-all period.
        if item.unit not in ("per_kw_year", "per_kva_year"):
            return f"unsupported:unit_{item.unit}_for_capacity"
        if item.tiers:
            return "unsupported:tiers_on_capacity"
        if not _single_catch_all(item):
            return "unsupported:capacity_with_windows"
        return None
    if _is_demand(item):
        if item.unit != "per_kw_month":
            return f"unsupported:unit_{item.unit}_for_demand"
        # Windowed demand tiers carry per-period `tier_rates` (validated, WP2.1a-ii).
        return None
    if item.tiers:
        # WP1.5c: tiers on cumulative monthly volume; windows allowed since
        # WP2.1a-ii (per-period `tier_rates`, validated by the model).
        if item.unit != "per_kwh":
            return f"unsupported:unit_{item.unit}_for_tiers"
    if item.kind == "fixed":
        if item.unit not in ("per_month", "per_day"):
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


def _covered_days_by_month(local: pd.DatetimeIndex, hours: np.ndarray,
                           tz: str | None) -> dict[str, float]:
    """Covered CALENDAR days per local month: each local day contributes its
    covered hours ÷ that day's real length (23, 24 or 25 h across DST), so a
    fully covered spring-forward day is one day (P2 WP2.1a-i review #6)."""
    naive = local.tz_localize(None) if local.tz is not None else local
    per_day = pd.Series(hours, index=naive.normalize()).groupby(level=0).sum()
    out: dict[str, float] = {}
    for day, h in per_day.items():
        if tz is None:
            length = 24.0
        else:
            start = pd.Timestamp(day).tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
            end = (pd.Timestamp(day) + pd.Timedelta(days=1)).tz_localize(
                tz, ambiguous=True, nonexistent="shift_forward")
            length = (end - start).total_seconds() / 3600.0
        key = day.strftime("%Y-%m")
        out[key] = out.get(key, 0.0) + float(h) / length
    return out


def _period_days_by_month(lo: pd.Timestamp, hi: pd.Timestamp,
                          tz: str | None) -> dict[str, float]:
    """Calendar days of `[lo, hi)` per local month: each local day contributes
    its exact overlap with the period ÷ its real length (WP2.1a-i review round 2
    #2 — no hourly walk, so sub-hour bounds and 30-min DST shifts are exact)."""
    def midnight(d: pd.Timestamp) -> pd.Timestamp:
        return d if tz is None else d.tz_localize(tz, ambiguous=True,
                                                  nonexistent="shift_forward")

    out: dict[str, float] = {}
    day = (lo.tz_localize(None) if lo.tz is not None else lo).normalize()
    end_naive = hi.tz_localize(None) if hi.tz is not None else hi
    while day < end_naive:
        start, stop = midnight(day), midnight(day + pd.Timedelta(days=1))
        overlap = (min(hi, stop) - max(lo, start)).total_seconds()
        if overlap > 0:
            key = day.strftime("%Y-%m")
            out[key] = out.get(key, 0.0) + overlap / (stop - start).total_seconds()
        day += pd.Timedelta(days=1)
    return out


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
         meter_history: pd.DataFrame | None = None, capacity_kw: float | None = None,
         power_factor: float | None = None) -> RatingResult:
    """Rate ``dispatch`` against ``tariff`` (see module docstring).
    ``meter_history`` seeds ratchets (WP1.5b). ``capacity_kw`` is the contracted
    capacity capacity items rate on (the PoC's p_nom_opt), ``power_factor`` turns
    it into kVA for `per_kva_year` items (P2 WP2.1a-i)."""
    idx = _validate(dispatch, timezone)
    if power_factor is not None and not (np.isfinite(power_factor) and 0 < power_factor <= 1):
        raise ValueError(f"power_factor must be in (0, 1], got {power_factor}")
    if capacity_kw is not None and np.isfinite(capacity_kw) and capacity_kw < 0:
        raise ValueError(f"capacity_kw must not be negative, got {capacity_kw}")
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
    demand_rows: list[dict] = []

    for item in tariff.items:
        reason = _unsupported_reason(item)
        if reason is not None:
            unsupported.append(item.id)
            flags[item.id] = [reason]
            continue
        sign = sign_of[item.direction]
        if item.kind == "capacity":
            # Capacity items (P2 WP2.1a-i), €/kW(A)-year, pro-rated like the LP's
            # fee by the represented hours (the energy hours) / 8760:
            #   * measured on `peak_import` — the year's MEASURED peak (settlement-
            #     interval mean), e.g. the German Leistungspreis on the
            #     Jahreshöchstleistung; once per year, never per month;
            #   * otherwise — the contracted capacity the caller passes
            #     (`capacity_kw`, the PoC's p_nom_opt).
            flags[item.id] = []
            by_month: dict[str, float] = {}
            hours_by_month = pd.Series(energy_h).groupby(month_key).sum()
            year_key = np.asarray([m[:4] for m in month_key])
            if item.measured_on == "peak_import":
                interval = interval_key(local, {"15min": "15min", "30min": "30min",
                                                "h": "h"}[item.settlement])
                kw = imp * _KWH_PER_MWH
                caps = {}
                for y in sorted(set(year_key)):
                    sel = year_key == y
                    # An interval holding a NaN is unknown, never 0 (review #1).
                    g = pd.DataFrame({"g": interval[sel], "qd": kw[sel] * dur[sel], "d": dur[sel],
                                      "nan": np.isnan(kw[sel])})
                    agg = g.groupby("g").agg(qd=("qd", "sum"), d=("d", "sum"), nan=("nan", "any"))
                    caps[y] = (float("nan") if agg["nan"].any()
                               else float((agg["qd"] / agg["d"]).max()))
                if any(not np.isfinite(v) for v in caps.values()):
                    flags[item.id].append(f"nan_quantity:{int(np.isnan(kw).sum())}")
            else:
                cap = capacity_kw
                if cap is None or not np.isfinite(cap):
                    flags[item.id].append("capacity_not_established")
                caps = {y: cap for y in set(year_key)}
            # Disclose the conventions (review #10): pro-rated by represented
            # hours / 8760 like the LP fee (a leap or partial year differs from
            # the calendar), and a measured peak from part of the year.
            for y in sorted(set(year_key)):
                sel = year_key == y
                cal_h = 8784.0 if calendar.isleap(int(y)) else _HOURS_PER_YEAR
                note = notes.setdefault(item.id, [])
                # The divisor is 8760 (the LP fee's nyears), so any year whose
                # represented hours differ — partial, or a full leap year (8784)
                # — is pro-rated off the calendar (WP2.1a-i review round 2 #1).
                if abs(float(energy_h[sel].sum()) - _HOURS_PER_YEAR) > 1e-6 and \
                        "capacity_prorated_by_represented_hours" not in note:
                    note.append("capacity_prorated_by_represented_hours")
                if item.measured_on == "peak_import" and float(dur[sel].sum()) < cal_h - 1e-6 \
                        and "peak_from_partial_year" not in note:
                    note.append("peak_from_partial_year")
            if has_gaps and represents_hours is None:
                # Sampled weeks without represents_hours bill only the sampled
                # hours while fixed items bill the whole billing period (C4).
                notes.setdefault(item.id, []).append("capacity_on_partial_coverage")
            if item.unit == "per_kva_year" and not flags[item.id]:
                if power_factor is None:
                    flags[item.id].append("power_factor_missing")
                else:
                    caps = {y: v / power_factor for y, v in caps.items()}
            if not flags[item.id]:
                for key, hours in sorted(hours_by_month.items()):
                    amt = (sign * item.periods[0].rate * caps[key[:4]] * float(hours)
                           / _HOURS_PER_YEAR)
                    by_month[key] = amt
                    fixed_rows.append({"month": key, "tariff_item": item.id,
                                       "hours_covered": float(hours),
                                       "hours_in_month": _month_hours(key, timezone),
                                       "amount": amt})
            per_item[item.id] = None if flags[item.id] else float(sum(by_month.values()))
            monthly_parts[item.id] = pd.Series(by_month, dtype=float)
            continue
        if _is_demand(item):
            # WP1.5a: per local month and period window, rate × max kW over
            # the item's demand INTERVALS (`settlement`): finer dispatch is
            # averaged to the interval first. A window list without a
            # catch-all measures only inside its windows.
            if item.measured_on == "net":
                # A net interval meter reads the interval's NET energy: signed
                # here, clipped after the interval mean (review round 3 #1).
                net = imp - exp
                q_kw = (net if item.direction == "cost" else -net) * _KWH_PER_MWH
            else:
                q_kw = _quantity_mw(item, imp, exp) * _KWH_PER_MWH
            if item.measured_on == "peak_import":
                q_kw = imp * _KWH_PER_MWH
            settle_freq = {"15min": "15min", "30min": "30min", "h": "h"}[item.settlement]
            interval = interval_key(local, settle_freq)
            grp = pd.DataFrame({"g": interval, "qd": q_kw * dur, "d": dur,
                                "nan": np.isnan(q_kw)})
            agg = grp.groupby("g", sort=True).agg(qd=("qd", "sum"), d=("d", "sum"),
                                                  nan=("nan", "any"))
            first = pd.Series(np.arange(len(interval))).groupby(interval).first()
            q_int = np.where(agg["nan"].to_numpy(), np.nan,
                             agg["qd"].to_numpy() / agg["d"].to_numpy())
            if item.measured_on == "net":
                q_int = np.clip(q_int, 0.0, None)  # NaN stays NaN
            g_month = month_key[first.to_numpy()]
            window, names, frag = demand_windows(item, local)
            g_win = window[first.to_numpy()]
            g_frag = frag[first.to_numpy()]
            flags[item.id] = []
            months_sorted = sorted(set(month_key))
            # Months the bill covers but the dispatch does not (spec §5.2):
            # not established — never a "complete" bill missing a month.
            if billing_period is not None:
                lo = pd.Timestamp(billing_period[0])
                hi = pd.Timestamp(billing_period[1]) - pd.Timedelta(seconds=1)
                if timezone is not None:
                    lo = lo.tz_localize(timezone) if lo.tz is None else lo.tz_convert(timezone)
                    hi = hi.tz_localize(timezone) if hi.tz is None else hi.tz_convert(timezone)
                span = pd.period_range(lo.strftime("%Y-%m"), hi.strftime("%Y-%m"), freq="M")
            else:
                span = pd.period_range(months_sorted[0], months_sorted[-1], freq="M")
            for m in span.strftime("%Y-%m"):
                if m not in set(months_sorted):
                    flags[item.id].append(f"demand_month_not_established:{m}")
            # Actual peak per (month, window) first: a ratchet reads ACTUAL
            # peaks of earlier months, never billed ones (WP1.5b).
            actual: dict[tuple[str, int], float] = {}
            rate_of: dict[tuple[str, int], float] = {}
            tier_rates_of: dict[tuple[str, int], tuple[float, ...]] = {}
            windowed = is_windowed_tiered(item)
            item_tier_rates = tuple(t.rate for t in item.tiers) if item.tiers else ()

            def _charged(mk) -> bool:
                # Tiers replace the period rate (R2's convention: period rate 0),
                # so a tiered window is free only if every tier rate of it is 0
                # (WP2.1a-i #2; per window since WP2.1a-ii).
                if item.tiers:
                    return any(r != 0 for r in tier_rates_of.get(mk, item_tier_rates))
                return rate_of[mk] != 0
            nan_windows = 0
            for key in months_sorted:
                for k in range(len(names)):
                    sel = (g_month == key) & (g_win == k)
                    if not sel.any():
                        continue
                    rate_of[(key, k)] = window_rate(item, g_frag[sel])
                    if windowed:  # one set per window and month (model validator)
                        tier_rates_of[(key, k)] = tuple(
                            item.periods[int(g_frag[sel][0])].tier_rates)
                    if np.isnan(q_int[sel]).any():
                        nan_windows += 1  # only the window's own intervals matter
                        actual[(key, k)] = float("nan")
                    else:
                        actual[(key, k)] = float(q_int[sel].max())
            if nan_windows:
                flags[item.id].append(f"nan_quantity:{int(np.isnan(q_kw).sum())}")
            by_month: dict[str, float] = {}
            for key in months_sorted:
                amt = 0.0
                for k, name in enumerate(names):
                    if (key, k) not in actual:
                        continue
                    peak = actual[(key, k)]
                    rate_k = rate_of[(key, k)]
                    billed = peak
                    # A free window bills nothing: no ratchet, no seed gap.
                    if item.ratchet is not None and np.isfinite(peak) and _charged((key, k)):
                        # A free (month, window) bills nothing, so it sets no
                        # ratchet either — the LP has no peak for it (WP2.1a-0
                        # review #1); its month still counts as modelled.
                        charged = {mk: v for mk, v in actual.items() if _charged(mk)}
                        prior, missing = _ratchet_floor_prior(item.ratchet, key, k, charged,
                                                              meter_history, set(months_sorted))
                        if prior is not None and np.isfinite(prior):
                            billed = max(peak, item.ratchet.share * prior)
                        elif prior is not None:
                            # A NaN among the lookback peaks (WP2.1a-iii review #1).
                            billed = float("nan")
                            flags[item.id].append(f"ratchet_prior_unknown:{key}")
                        if missing:
                            note = notes.setdefault(item.id, [])
                            if "ratchet_seed_missing" not in note:
                                note.append("ratchet_seed_missing")
                    if item.tiers:
                        # Demand tiers (P2 WP2.1a-i): the billed kW priced
                        # through the tiers (thresholds in kW, rates per kW).
                        rates_k = tier_rates_of.get((key, k), item_tier_rates)
                        line = (sign * float(_tier_cost_with(
                                    [t.threshold for t in item.tiers], list(rates_k),
                                    np.array([billed]))[0])
                                if np.isfinite(billed) else float("nan"))
                    else:
                        line = sign * rate_k * billed
                    # A tiered item has no single rate (the tiers price it): NaN, not 0.
                    demand_rows.append({"month": key, "tariff_item": item.id, "period": name,
                                        "peak_kw": peak, "billed_kw": billed,
                                        "rate": float("nan") if item.tiers else rate_k,
                                        "amount": line})
                    amt += line
                by_month[key] = amt
                covered = float(dur[month_key == key].sum())
                if covered < _month_hours(key, timezone) - 1e-9:
                    note = notes.setdefault(item.id, [])
                    if "demand_on_partial_month" not in note:
                        note.append("demand_on_partial_month")
            settle_h = _SETTLEMENT_HOURS[item.settlement]
            uniq = np.unique(dur)
            if len(uniq) != 1 or abs(uniq[0] - settle_h) > 1e-9:
                d = f"{uniq[0]:g}h" if len(uniq) == 1 else "mixed"
                notes.setdefault(item.id, []).append(
                    f"resolution:dispatch_{d}_settlement_{settle_h:g}h")
            per_item[item.id] = None if flags[item.id] else float(sum(by_month.values()))
            monthly_parts[item.id] = pd.Series(by_month, dtype=float)
            continue
        if item.kind == "fixed":
            monthly_rate = item.periods[0].rate
            if billing_period is not None:
                covered = _hours_by_month_in_period(billing_period[0], billing_period[1], timezone)
            else:
                covered = pd.Series(dur).groupby(month_key).sum().to_dict()
            by_month: dict[str, float] = {}
            days_by_month: dict[str, float] = {}
            if item.unit == "per_day":
                if billing_period is not None:
                    lo = pd.Timestamp(billing_period[0])
                    hi = pd.Timestamp(billing_period[1])
                    if timezone is not None:
                        lo = lo.tz_localize(timezone) if lo.tz is None else lo.tz_convert(timezone)
                        hi = hi.tz_localize(timezone) if hi.tz is None else hi.tz_convert(timezone)
                    days_by_month = _period_days_by_month(lo, hi, timezone)
                else:
                    days_by_month = _covered_days_by_month(local, dur, timezone)
            for key, hours in sorted(covered.items()):
                mh = _month_hours(key, timezone)
                # per_day: covered CALENDAR days on the local clock (review #6);
                # per_month: the covered share of the month.
                amt = (sign * monthly_rate * days_by_month.get(key, 0.0) if item.unit == "per_day"
                       else sign * monthly_rate * hours / mh)
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
        if is_windowed_tiered(item):
            # URDB semantics (WP2.1a-ii): the month's TOTAL energy positions the
            # tiers; each period's energy is split into them in proportion to
            # the total, so a period's interval pays its blended month rate
            # Σ_k tier_rates[p][k] · Q_k / E. Exact and traceable per line.
            frag = _period_index(item, local)
            thresholds = [t.threshold for t in item.tiers]
            amount = np.full(len(idx), np.nan)
            r = np.full(len(idx), np.nan)
            wt_unknown_months: list[str] = []
            for key in sorted(set(month_key)):
                pos = np.flatnonzero(month_key == key)
                if np.isnan(q_kwh[pos]).any() or (frag[pos] < 0).any():
                    # An unknown volume or an unrated interval: the month's tier
                    # position is unknown, so the whole month is (review #3).
                    wt_unknown_months.append(key)
                    continue
                total = float(q_kwh[pos].sum())
                widths = np.diff(np.clip(np.array(thresholds + [np.inf]), 0.0, total))
                for k_frag in np.unique(frag[pos]):
                    rates_p = np.asarray(item.periods[int(k_frag)].tier_rates, dtype=float)
                    blended = (float((rates_p * widths).sum() / total) if total > 0
                               else float(rates_p[0]))
                    sel = pos[frag[pos] == k_frag]
                    r[sel] = blended
                    amount[sel] = sign * q_kwh[sel] * blended
        elif item.tiers:
            # Cumulative monthly volume, each interval priced at the tiers its
            # kWh fall into (chronologically): exact and traceable per line.
            amount = np.full(len(idx), np.nan)
            for key in sorted(set(month_key)):
                pos = np.flatnonzero(month_key == key)
                cv = np.concatenate([[0.0], np.cumsum(q_kwh[pos])])
                cost = _tier_cost(item.tiers, cv)
                amount[pos] = sign * np.diff(cost)
            with np.errstate(invalid="ignore", divide="ignore"):
                r = np.where(q_kwh > 0, np.abs(amount) / q_kwh, np.nan)
            r = np.where(np.isnan(r) & ~np.isnan(q_kwh), 0.0, r)
        else:
            r = _rates(item, local)
            amount = sign * q_kwh * r
        interval_frames.append(pd.DataFrame({
            "interval": idx, "tariff_item": item.id, "quantity_kwh": q_kwh,
            "rate": r, "amount": amount}))
        item_flags = []
        if is_windowed_tiered(item):
            # Count what is actually unknown (review #3): uncovered intervals and
            # NaN quantities, and name the months they make unknown.
            n_unrated = int((_period_index(item, local) < 0).sum())
            item_flags += [f"tier_month_not_established:{m}" for m in wt_unknown_months]
        else:
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
        if item.tiers and represents_hours is not None:
            # Monthly tier thresholds see the REPRESENTED volume (a sampled week
            # standing for more); disclosed, WP2.1b revisits (review #4).
            notes.setdefault(item.id, []).append("tiers_on_represented_volume")
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
    partial_energy = any(("energy_on_partial_coverage" in v) or ("capacity_on_partial_coverage" in v)
                         for v in notes.values())
    # A ratchet whose lookback reaches unknown months bills a lower bound.
    seed_missing = any("ratchet_seed_missing" in v for v in notes.values())
    total = None if (unsupported or partial_energy or seed_missing) else total_supported
    demand_lines = pd.DataFrame(demand_rows, columns=["month", "tariff_item", "period",
                                                      "peak_kw", "billed_kw", "rate", "amount"])
    return RatingResult(lines=lines, fixed_lines=fixed_lines, monthly=monthly, annual=annual,
                        per_item=per_item, total=total, total_supported=total_supported,
                        flags=flags, notes=notes, unsupported_items=unsupported,
                        demand_lines=demand_lines)
