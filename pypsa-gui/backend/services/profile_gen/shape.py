"""Calibration / shaping ops over a generated per-unit profile (RP-06).

Spec: .scratch/renewable-profiles/spec.md R5; gatekeeper conditions of
2026-10-19 (see tests/test_profile_gen_shape.py).

Every op is a pure function of an HOURLY per-unit series on the WEATHER year,
before alignment: same index, values in [0, 1], deterministic. Daily/weekly
artifacts are refused (their FLH bound is not the hourly one); regenerate
at hourly resolution to calibrate. `apply_chain` runs an
ordered list of ops and returns the shaped series plus its provenance. It
never touches a network: the router writes the result as a new pending
artifact and `apply.assign_profile` stays the one writer.

Ops (``{"op": name, **params}``):

- ``target_flh {hours}`` / ``target_cf {cf}``: scale with clip-at-1 until the
  full-load hours hit the target (bisection; monotone, so the rank order is
  kept). A target at or above what clipping can reach (every positive hour
  at 1) is an `UnreachableTargetError` naming that maximum.
- ``quantile_map {quantiles: {q: value}} | {observed: [values]}``: map each
  hour's rank onto a target duration curve, either named quantiles
  (interpolated linearly, the series' own min/max as the 0/1 anchors unless
  given) or an observed series' empirical quantiles. Exact zeros stay zero
  (solar nights). Timing comes from the weather year; only the levels move.
- ``tail_damp {side: low|high, beyond_q, factor, keep_flh=true}``: damp the
  tail beyond the ``beyond_q`` quantile, continuously and monotonically. Low
  pulls the hours below the threshold toward zero (``t·(v/t)^(1/factor)``,
  "too much low-wind generation"); high pulls the hours above it toward the
  threshold (``t + (v−t)·factor``, "the peaks are too high"). Then the
  original FLH is re-hit unless ``keep_flh`` is false.
- ``peak_shift {hours}``: roll an hourly series by whole hours, wrapping at
  the year boundary. Length and FLH are unchanged. It is NOT a time-zone
  fix: apply's ``time_basis`` maps the UTC weather onto the snapshots'
  clock, so shifting by a UTC offset here would shift it twice.
"""
from __future__ import annotations

import calendar
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from services.profile_gen.engine import ProfileGenError


class ShapeError(ProfileGenError):
    """Base class for typed shaping refusals."""


class InvalidShapeOpError(ShapeError):
    """An op is unknown, or its parameters are out of range."""


class UnreachableTargetError(ShapeError):
    """An FLH/CF target the series cannot reach by scaling with clip-at-1."""

    def __init__(self, message: str, *, reachable_max: float):
        super().__init__(message)
        self.reachable_max = reachable_max


def _year_hours(s: pd.Series) -> int:
    year = int(pd.DatetimeIndex(s.index)[0].year)
    return 8784 if calendar.isleap(year) else 8760


def _is_hourly(s: pd.Series) -> bool:
    idx = pd.DatetimeIndex(s.index)
    return len(idx) > 1 and (idx[1] - idx[0]) == pd.Timedelta(hours=1)


def profile_stats(s: pd.Series) -> dict[str, float | int | None]:
    """FLH, CF, P10/P50/P90, peak, and the hour of day of the mean diurnal
    peak (hourly series only). FLH = mean CF × the weather year's hours, so
    daily/weekly means give the same FLH as their hourly source."""
    v = s.to_numpy(dtype=float)
    cf = float(v.mean())
    peak_hour = None
    if _is_hourly(s):
        by_hour = s.groupby(pd.DatetimeIndex(s.index).hour).mean()
        peak_hour = int(by_hour.idxmax())
    return {
        "flh": cf * _year_hours(s),
        "cf": cf,
        "p10": float(np.quantile(v, 0.1)),
        "p50": float(np.quantile(v, 0.5)),
        "p90": float(np.quantile(v, 0.9)),
        "peak": float(v.max()),
        "peak_hour": peak_hour,
    }


def _num(params: Mapping[str, Any], key: str, op: str) -> float:
    try:
        x = float(params[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidShapeOpError(f"{op}: needs a number `{key}`") from exc
    if not np.isfinite(x):
        raise InvalidShapeOpError(f"{op}: `{key}` must be finite")
    return x


def _scale_to_cf(v: np.ndarray, cf: float, *, hours: int, op: str) -> np.ndarray:
    if cf <= 0.0:
        raise InvalidShapeOpError(f"{op}: the target must be positive")
    reachable = float((v > 0).mean())
    if abs(cf - reachable) <= 1e-12:
        return (v > 0).astype(float)  # every producing hour at full output
    if cf > reachable:
        raise UnreachableTargetError(
            f"{op}: unreachable — this profile reaches at most "
            f"{reachable * hours:.0f} full-load hours (CF {reachable:.1%}, "
            "every hour it produces at full output); pick a lower target "
            "or a different weather year/preset",
            reachable_max=reachable * hours,
        )
    lo, hi = 0.0, 1.0
    while np.clip(v * hi, 0.0, 1.0).mean() < cf:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if np.clip(v * mid, 0.0, 1.0).mean() < cf:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-12 * hi:
            break
    return np.clip(v * hi, 0.0, 1.0)


def _target_flh(s: pd.Series, p: Mapping[str, Any]) -> tuple[np.ndarray, dict]:
    hours = _year_hours(s)
    flh = _num(p, "hours", "target_flh")
    v = _scale_to_cf(s.to_numpy(dtype=float), flh / hours, hours=hours,
                     op="target_flh")
    return v, {"hours": flh}


def _target_cf(s: pd.Series, p: Mapping[str, Any]) -> tuple[np.ndarray, dict]:
    cf = _num(p, "cf", "target_cf")
    v = _scale_to_cf(s.to_numpy(dtype=float), cf, hours=_year_hours(s),
                     op="target_cf")
    return v, {"cf": cf}


def _ranks(v: np.ndarray) -> np.ndarray:
    """Each value's position on [0, 1] (ties share their mean rank)."""
    r = pd.Series(v).rank(method="average").to_numpy()
    return (r - 1.0) / max(len(v) - 1, 1)


def _quantile_map(s: pd.Series, p: Mapping[str, Any]) -> tuple[np.ndarray, dict]:
    v = s.to_numpy(dtype=float)
    u = _ranks(v)
    if "observed" in p:
        try:
            obs = np.asarray(p["observed"], dtype=float)
        except (TypeError, ValueError) as exc:
            raise InvalidShapeOpError("quantile_map: `observed` is a list of "
                                      "per-unit values") from exc
        if obs.ndim != 1 or len(obs) < 2:
            raise InvalidShapeOpError("quantile_map: `observed` needs at least "
                                      "two values")
        if not np.isfinite(obs).all() or obs.min() < 0.0 or obs.max() > 1.0:
            raise InvalidShapeOpError("quantile_map: `observed` values must be "
                                      "finite per-unit values in [0, 1] — "
                                      "divide MW by the capacity first")
        out = np.quantile(obs, u)
        record = {"observed_n": int(len(obs))}
    elif "quantiles" in p and isinstance(p["quantiles"], Mapping) and p["quantiles"]:
        try:
            pts = {float(q): float(x) for q, x in p["quantiles"].items()}
        except (TypeError, ValueError) as exc:
            raise InvalidShapeOpError("quantile_map: `quantiles` maps a "
                                      "quantile (0–1) to a value (0–1)") from exc
        if any(not (0.0 <= q <= 1.0 and 0.0 <= x <= 1.0) for q, x in pts.items()):
            raise InvalidShapeOpError("quantile_map: quantiles and values must "
                                      "lie in [0, 1]")
        pts.setdefault(0.0, float(v.min()))
        pts.setdefault(1.0, float(v.max()))
        qs = sorted(pts)
        xs = [pts[q] for q in qs]
        if any(b < a for a, b in zip(xs, xs[1:])):
            raise InvalidShapeOpError("quantile_map: target values must be "
                                      f"non-decreasing in the quantile; got {pts}")
        zero_share = float((v == 0.0).mean())
        night = [q for q, x in pts.items()
                 if 0.0 < q <= zero_share and x > 0.0]
        if night:
            raise InvalidShapeOpError(
                f"quantile_map: {zero_share:.0%} of the hours are zero (e.g. "
                f"solar nights) and stay zero, so quantile(s) {sorted(night)} "
                "cannot be raised above 0 — name quantiles above that share")
        # Invert the series' own quantile positions, so the named quantile of
        # the result (np.quantile's linear rule) lands on the target value.
        out = np.interp(u, qs, xs)
        record = {"quantiles": {str(q): pts[q] for q in qs}}
    else:
        raise InvalidShapeOpError("quantile_map: give `quantiles` "
                                  "({q: value}) or `observed` (a list of values)")
    out = np.where(v == 0.0, 0.0, out)
    return np.clip(out, 0.0, 1.0), record


def _tail_damp(s: pd.Series, p: Mapping[str, Any]) -> tuple[np.ndarray, dict]:
    side = str(p.get("side", "")).lower()
    if side not in ("low", "high"):
        raise InvalidShapeOpError("tail_damp: `side` is 'low' or 'high'")
    q = _num(p, "beyond_q", "tail_damp")
    factor = _num(p, "factor", "tail_damp")
    if not (0.0 < q < 1.0):
        raise InvalidShapeOpError("tail_damp: `beyond_q` must lie in (0, 1)")
    if not (0.0 < factor <= 1.0):
        raise InvalidShapeOpError("tail_damp: `factor` must lie in (0, 1] "
                                  "(1 leaves the tail as it is)")
    keep_flh = p.get("keep_flh", True)
    if not isinstance(keep_flh, bool):
        raise InvalidShapeOpError("tail_damp: `keep_flh` is true or false")
    v = s.to_numpy(dtype=float)
    t = float(np.quantile(v, q))
    out = v.copy()
    if side == "low":
        if t <= 0.0:
            raise InvalidShapeOpError(
                f"tail_damp: the hours below the {q:.0%} quantile are all "
                "zero (e.g. solar nights) — nothing to damp; pick a higher "
                "`beyond_q`")
        m = v < t
        out[m] = t * (v[m] / t) ** (1.0 / factor)
    else:
        m = v > t
        out[m] = t + (v[m] - t) * factor
    if keep_flh:
        out = _scale_to_cf(out, float(v.mean()), hours=_year_hours(s),
                           op="tail_damp(keep_flh)")
    return np.clip(out, 0.0, 1.0), {
        "side": side, "beyond_q": q, "factor": factor, "keep_flh": keep_flh,
        "threshold": t,
    }


def _peak_shift(s: pd.Series, p: Mapping[str, Any]) -> tuple[np.ndarray, dict]:
    hours = _num(p, "hours", "peak_shift")
    if hours != int(hours) or abs(hours) > 24 * 7:
        raise InvalidShapeOpError("peak_shift: `hours` is a whole number of "
                                  "hours, at most one week either way")
    return np.roll(s.to_numpy(dtype=float), int(hours)), {
        "hours": int(hours), "boundary": "wrap",
    }


_OPS: dict[str, Callable[[pd.Series, Mapping[str, Any]], tuple[np.ndarray, dict]]] = {
    "target_flh": _target_flh,
    "target_cf": _target_cf,
    "quantile_map": _quantile_map,
    "tail_damp": _tail_damp,
    "peak_shift": _peak_shift,
}
OP_NAMES = tuple(_OPS)


def apply_chain(series: pd.Series,
                ops: list[Mapping[str, Any]]) -> tuple[pd.Series, dict]:
    """Run `ops` in order. Returns (shaped series, provenance): ``calibrated:
    true``, each op with its resolved parameters and the FLH before/after it,
    and the parent's and result's `profile_stats`."""
    if not ops:
        raise InvalidShapeOpError("give at least one shaping op")
    if not _is_hourly(series):
        raise InvalidShapeOpError("shaping needs an hourly profile; "
                                  "regenerate at hourly resolution")
    if not np.isfinite(series.to_numpy(dtype=float)).all():
        raise InvalidShapeOpError("the profile has non-finite values; "
                                  "regenerate it")
    current = pd.Series(series.to_numpy(dtype=float), index=series.index,
                        name=series.name)
    parent_stats = profile_stats(current)
    record_ops = []
    for raw in ops:
        name = str((raw or {}).get("op", ""))
        fn = _OPS.get(name)
        if fn is None:
            raise InvalidShapeOpError(f"unknown shaping op {name!r}; known: "
                                      + ", ".join(OP_NAMES))
        before = profile_stats(current)["flh"]
        values, params = fn(current, raw)
        current = pd.Series(values, index=current.index, name=current.name)
        record_ops.append({"op": name, **params, "flh_before": before,
                           "flh_after": profile_stats(current)["flh"]})
    return current, {
        "calibrated": True,
        "ops": record_ops,
        "parent_stats": parent_stats,
        "result_stats": profile_stats(current),
    }
