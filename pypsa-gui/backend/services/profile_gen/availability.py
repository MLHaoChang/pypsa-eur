"""Availability profiles: generator bounds over the year (ticket 02).

One column per generator over the source year's hours, for one bound:

  * ``maintenance`` → p_max_pu. ``windows``: [{"from", "to", "available"}],
    each end MM-DD (applies in every investment period, tiled like every
    profile) or YYYY-MM-DD in the source year (applies only on a
    single-period network of that year — checked at apply — and is refused
    otherwise, never shifted). ``available`` is the share left during the
    window (0 = fully out). A window may wrap the new year. Or ``rule``:
    {"weeks": N, "season": "summer" | "winter"}, N consecutive weeks centred
    on mid-season (1 August / 15 January).
  * ``must_run`` → p_min_pu. ``share`` (constant), ``winter_share`` and
    ``summer_share`` (a heat-led floor following heating degrees under
    ``heating_base_c``), or ``monthly`` (12 shares).
  * ``derating`` → p_max_pu = clip(1 − per_k · max(0, T − threshold_c),
    floor, 1).
  * ``forced_outages`` → refused. The reliability study samples forced
    outages from each asset's own rate and MTTR (`services/adequacy/mc.py`);
    one draw written into the dispatch would be a single arbitrary scenario
    that biases sizing (gatekeeper review 2026-10-20).

Temperature is `services.dc_load_profile.ambient_c` — the one assumed
ambient model, keyed to the snapshot dates — with ``timezone`` placing the
diurnal term on the site's wall clock. The series is on UTC hours (apply's
contract); the clock used is recorded as ``source_time_basis``.

Every shape here is stated, not measured: ``provenance: synthetic/assumed``.
How adequacy sees the result: the Monte Carlo capacity sampling nets a
p_max_pu series as availability and ignores p_min_pu (a floor does not
reduce available capacity); the ENS and dispatch solves honour both.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from services.profile_gen.apply import ProfileApplyError

PROVENANCE = "synthetic/assumed"
KINDS = ("maintenance", "must_run", "derating", "forced_outages")
_TARGET = {"maintenance": "p_max_pu", "derating": "p_max_pu", "must_run": "p_min_pu"}
_AMBIENT = {"ambient_annual_mean_c": 10.0, "ambient_seasonal_amplitude_k": 9.0,
            "ambient_diurnal_amplitude_k": 5.0}
_SEASON_CENTRE = {"summer": (8, 1), "winter": (1, 15)}
_DATE = re.compile(r"(?:(\d{4})-)?(\d{2})-(\d{2})")


class AvailabilityError(ProfileApplyError):
    """Base of the availability family's typed refusals."""


class UnknownAvailabilityKindError(AvailabilityError):
    """No availability kind of that name."""


class InvalidAvailabilityParamsError(AvailabilityError):
    """An availability kind's parameters are missing, unknown or out of range."""


class ForcedOutagesNotWrittenError(AvailabilityError):
    """Forced outages belong to the reliability study, not the dispatch."""


class MaintenanceYearError(AvailabilityError):
    """A dated maintenance window does not fall in the year it would apply to."""


_DEFAULTS: dict[str, dict[str, Any]] = {
    "maintenance": {"windows": None, "rule": None},
    "must_run": {"share": None, "winter_share": None, "summer_share": None,
                 "monthly": None, "heating_base_c": 15.0, **_AMBIENT},
    "derating": {"threshold_c": 25.0, "per_k": 0.007, "floor": 0.7, **_AMBIENT},
}
_COMMON = {"timezone": None}


def _share(raw: Any, key: str) -> float:
    if isinstance(raw, bool):
        raise InvalidAvailabilityParamsError(f"{key} must be a number in [0, 1], got {raw!r}")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise InvalidAvailabilityParamsError(
            f"{key} must be a number in [0, 1], got {raw!r}") from None
    if not 0.0 <= v <= 1.0:
        raise InvalidAvailabilityParamsError(f"{key} must be in [0, 1], got {v!r}")
    return v


def _num(p: Mapping[str, Any], key: str) -> float:
    try:
        v = float(p[key])
    except (TypeError, ValueError):
        raise InvalidAvailabilityParamsError(f"{key} must be a number, got {p[key]!r}") from None
    if not np.isfinite(v):
        raise InvalidAvailabilityParamsError(f"{key} must be finite")
    return v


def _local(idx: pd.DatetimeIndex, timezone: str | None) -> pd.DatetimeIndex:
    if not timezone:
        return idx
    try:
        return idx.tz_localize("UTC").tz_convert(timezone).tz_localize(None)
    except Exception as exc:  # noqa: BLE001 — pytz/zoneinfo raise many types
        raise InvalidAvailabilityParamsError(
            f"timezone {timezone!r} is not an IANA zone ({exc})") from None


def _day(raw: Any, year: int, what: str) -> tuple[pd.Timestamp, bool]:
    """A window end as a date in the source year, and whether it was dated."""
    m = _DATE.fullmatch(raw.strip()) if isinstance(raw, str) else None
    if m is None:
        raise InvalidAvailabilityParamsError(f"{what} {raw!r} is not MM-DD or YYYY-MM-DD")
    y, month, day = m.groups()
    if y is not None and int(y) != year:
        raise MaintenanceYearError(
            f"{what} {raw!r} is in {y}, not the source year {year}; a dated "
            "window is never shifted to another year")
    try:
        return pd.Timestamp(year=year, month=int(month), day=int(day)), y is not None
    except ValueError:
        raise InvalidAvailabilityParamsError(f"{what} {raw!r} is not a calendar date") from None


def _maintenance(local: pd.DatetimeIndex, p: Mapping[str, Any], year: int,
                 ) -> tuple[np.ndarray, dict]:
    windows, rule = p["windows"], p["rule"]
    if (windows is None) == (rule is None):
        raise InvalidAvailabilityParamsError("maintenance takes exactly one of windows or rule")
    avail = np.ones(len(local))
    days = local.normalize()
    record: list[dict] = []
    if rule is not None:
        if not isinstance(rule, Mapping):
            raise InvalidAvailabilityParamsError(f"rule must be an object, got {rule!r}")
        season = rule.get("season")
        weeks = rule.get("weeks")
        if season not in _SEASON_CENTRE:
            raise InvalidAvailabilityParamsError(
                f"rule.season must be summer or winter, got {season!r}")
        if not isinstance(weeks, int) or isinstance(weeks, bool) or not 1 <= weeks <= 26:
            raise InvalidAvailabilityParamsError(f"rule.weeks must be 1-26, got {weeks!r}")
        month, day = _SEASON_CENTRE[season]
        centre = pd.Timestamp(year=year, month=month, day=day)
        start = centre - pd.Timedelta(days=7 * weeks // 2)
        windows = [{"from": f"{start:%m-%d}",
                    "to": f"{start + pd.Timedelta(days=7 * weeks - 1):%m-%d}"}]
    if not isinstance(windows, list) or not windows:
        raise InvalidAvailabilityParamsError(
            f"windows must be a non-empty list of {{from, to, available}}, got {windows!r}")
    for i, w in enumerate(windows):
        if not isinstance(w, Mapping):
            raise InvalidAvailabilityParamsError(f"windows[{i}] must be an object")
        start, dated_a = _day(w.get("from"), year, f"windows[{i}].from")
        end, dated_b = _day(w.get("to"), year, f"windows[{i}].to")
        share = _share(w.get("available", 0.0), f"windows[{i}].available")
        if dated_a or dated_b:
            if end < start:
                raise InvalidAvailabilityParamsError(
                    f"windows[{i}] ends before it starts")
            inside = (days >= start) & (days <= end)
        elif end >= start:
            inside = (days >= start) & (days <= end)
        else:  # MM-DD across the new year: the end of December and January
            span = (end + pd.DateOffset(years=1) - start).days + 1
            if span > 183:
                raise InvalidAvailabilityParamsError(
                    f"windows[{i}] ends before it starts (a window across the "
                    "new year must be shorter than half a year)")
            inside = (days >= start) | (days <= end)
        avail = np.where(inside, np.minimum(avail, share), avail)
        record.append({"from": w.get("from"), "to": w.get("to"), "available": share,
                       "dated": bool(dated_a or dated_b)})
    return avail, {"windows": record}


def _ambient(p: Mapping[str, Any]) -> dict[str, float]:
    return {k: _num(p, k) for k in _AMBIENT}


def _must_run(local: pd.DatetimeIndex, p: Mapping[str, Any], idx, timezone,
              ) -> tuple[np.ndarray, dict]:
    from services.dc_load_profile import ambient_c

    modes = [k for k in ("share", "winter_share", "monthly") if p[k] is not None]
    if len(modes) != 1 or (p["winter_share"] is None) != (p["summer_share"] is None):
        raise InvalidAvailabilityParamsError(
            "must_run takes one of share, winter_share + summer_share, or monthly")
    if p["share"] is not None:
        return np.full(len(local), _share(p["share"], "share")), {"mode": "constant"}
    if p["monthly"] is not None:
        monthly = p["monthly"]
        if not isinstance(monthly, list) or len(monthly) != 12:
            raise InvalidAvailabilityParamsError("monthly must be a list of 12 shares")
        shares = np.array([_share(v, f"monthly[{i}]") for i, v in enumerate(monthly)])
        return shares[local.month.to_numpy() - 1], {"mode": "monthly"}
    winter, summer = _share(p["winter_share"], "winter_share"), _share(p["summer_share"], "summer_share")
    amb = _ambient(p)
    temp = ambient_c(SimpleNamespace(**amb), idx, timezone)
    degrees = np.clip(_num(p, "heating_base_c") - temp, 0.0, None)
    peak = max(float(degrees.max()), 1e-9)
    return summer + (winter - summer) * degrees / peak, {"mode": "seasonal", "ambient": amb}


def _derating(local: pd.DatetimeIndex, p: Mapping[str, Any], idx, timezone,
              ) -> tuple[np.ndarray, dict]:
    from services.dc_load_profile import ambient_c

    amb = _ambient(p)
    per_k = _num(p, "per_k")
    floor = _share(p["floor"], "floor")
    if per_k < 0:
        raise InvalidAvailabilityParamsError(f"per_k must be >= 0, got {per_k!r}")
    temp = ambient_c(SimpleNamespace(**amb), idx, timezone)
    v = np.clip(1.0 - per_k * np.maximum(0.0, temp - _num(p, "threshold_c")), floor, 1.0)
    return v, {"ambient": amb}


def generate_frame(ids: list[str], kind: str, params: Mapping[str, Any],
                   year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """One column per generator id over ``year`` (all equal: the kind and its
    parameters describe a bound, not a site), and the meta the engine merges."""
    if kind == "forced_outages":
        raise ForcedOutagesNotWrittenError(
            "forced outages are sampled by the reliability study from each "
            "asset's own outage rate and repair time; they are not written "
            "into the dispatch")
    if kind not in _DEFAULTS:
        raise UnknownAvailabilityKindError(
            f"unknown availability kind {kind!r}; available: {', '.join(KINDS)}")
    allowed = {**_COMMON, **_DEFAULTS[kind]}
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise InvalidAvailabilityParamsError(
            f"{kind} does not take {', '.join(unknown)}; it takes {', '.join(sorted(allowed))}")
    p = {**allowed, **dict(params)}
    idx = pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")
    tz = p["timezone"]
    local = _local(idx, tz)
    if kind == "maintenance":
        values, extra = _maintenance(local, p, int(year))
    elif kind == "must_run":
        values, extra = _must_run(local, p, idx, tz)
    else:
        values, extra = _derating(local, p, idx, tz)
    frame = pd.DataFrame({i: values for i in ids}, index=idx)
    stored = {k: v for k, v in p.items() if v is not None}
    meta = {"kind": kind, "params": stored, "target": ["generators", _TARGET[kind]],
            "provenance": PROVENANCE, "unit": "per_unit",
            "source_time_basis": (f"UTC (from {tz} wall clock)" if tz
                                  else "UTC (assumed; site clock unknown)"),
            **extra}
    return frame, meta


def check_dated_windows(ctx, meta: Mapping[str, Any]) -> None:
    """A dated window applies only on a single-period network of its year."""
    windows = meta.get("windows") or []
    dated = [w for w in windows if w.get("dated")]
    if not dated:
        return
    snaps = ctx.network.snapshots
    if isinstance(snaps, pd.MultiIndex):
        raise MaintenanceYearError(
            "a dated maintenance window cannot be placed on a multi-period "
            "network (it would repeat in every investment period); use MM-DD "
            "windows to repeat it, or a single-period network of that year")
    years = set(pd.DatetimeIndex(snaps).year)
    src = int(meta.get("weather_year"))
    if years != {src}:
        raise MaintenanceYearError(
            f"the dated windows are in {src} but the network's snapshots are "
            f"in {sorted(years)}; a dated window is never shifted to another year")
