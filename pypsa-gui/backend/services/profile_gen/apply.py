"""Apply a generated renewable profile to a network generator.

Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md §4
Plan: docs/superpowers/plans/2026-10-17-renewable-profile-generator.md Task 5

`assign_profile` is the ONE write path from the profile generator into the
model. It implements spec §4's alignment table exactly — never silent
reindexing:

  * target snapshots span the weather year, hourly → align by timestamp
    (the same-year case of month-day-hour mapping);
  * leap weather year onto a non-leap target → drop Feb 29, meta
    ``leap_day: dropped``;
  * non-leap weather year onto a leap target → typed refusal (fabricating
    24 h is worse than refusing);
  * partial-year / sub-hourly / segmented snapshots → typed refusal naming
    the supported shapes;
  * MultiIndex (multi-investment-period) snapshots → tile the weather year
    into EVERY period by month-day-hour, leap rules applied per period
    (owner ruling D7=B); meta ``tiled: true`` + the weather year.

Write contract: store entry ``("generators", "p_max_pu", name)`` + provenance
meta (the `_user_ts_meta` channel), `_t` reapply, changelog, dispatch
invalidation via `services/dispatch_status.clear_dispatch` — a profile change
invalidates solved results exactly like a topology edit does. Overwrite
semantics: regenerating overwrites the same key, changelog-stamped. Undo is
the caller's job (push a snapshot BEFORE calling); the restored payload then
brings back the prior state, including a network-native profile that had no
user entry (see tests/test_profile_gen_apply.py).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Mapping

import numpy as np
import pandas as pd

from services import change_log_service
from services.dispatch_status import clear_dispatch
from services.profile_gen.engine import ProfileGenError
from services.pypsa_service import PyPSAService
from services.user_timeseries import (
    _reapply_user_ts_to_network,
    _user_ts_lock,
)

# ---------------------------------------------------------------------------
# Typed errors — the router maps ProfileApplyError to a 422 with the message.
# ---------------------------------------------------------------------------


class ProfileApplyError(ProfileGenError):
    """Base class for typed refusals at the apply boundary."""


class ComponentNotFoundError(ProfileApplyError):
    """The named generator does not exist on the network."""


class CarrierMismatchError(ProfileApplyError):
    """The asset's carrier is not wind/solar, or the artifact's technology
    disagrees with it (spec §4: refuse at generate AND at apply)."""


class UnsupportedSnapshotsError(ProfileApplyError):
    """The network's snapshots are not a shape a generated profile can be
    aligned to (spec §4: partial-year, sub-hourly, segmented)."""


class LeapYearMismatchError(ProfileApplyError):
    """The target calendar needs a Feb 29 the weather year does not have."""


class InvalidTimeBasisError(ProfileApplyError):
    """The declared snapshot time basis is not "UTC" or an IANA zone."""


# The mapping basis when the snapshots' clock is not stated (see
# `resolve_time_basis`): the UTC weather hours map straight on.
DEFAULT_TIME_BASIS = "UTC"

TIME_BASIS_ASSUMED_WARNING = (
    "time_basis_assumed_utc: the profile is in UTC and was applied unshifted; "
    "if this project's snapshots are local time, set the time zone (a shift "
    "of the site's UTC offset, e.g. +1/+2 h in Central Europe)"
)


def carrier_flags(carrier: str) -> tuple[bool, bool]:
    """(is_wind, is_solar) for a generator carrier — the one rule for
    which assets a generated profile applies to (apply, readiness)."""
    cl = str(carrier or "").lower()
    return "wind" in cl, ("solar" in cl or "pv" in cl)


def _commercial_timezone(ctx) -> str | None:
    state = getattr(ctx, "solver_state", None) or {}
    cfg = state.get("solver_config") if isinstance(state, Mapping) else None
    commercial = getattr(cfg, "commercial", None)
    if commercial is None:
        return None
    tz = (commercial.get("timezone") if isinstance(commercial, Mapping)
          else getattr(commercial, "timezone", None))
    return str(tz) if tz else None


def resolve_time_basis(stated: str | None,
                       commercial_timezone: str | None,
                       ) -> tuple[str, str, list[str]]:
    """(mapping basis, recorded basis, warnings) — the rule
    `commercial/lp_bindings.py` reads snapshots by (gatekeeper review
    2026-10-19):

    - stated: map in that basis ("UTC" or an IANA zone's wall clock);
    - not stated, `commercial.timezone` set: the snapshots are UTC;
    - not stated, no timezone: the snapshots are the site clock, of unknown
      offset — map unshifted so a one-click default still works, record the
      assumption, and warn.
    """
    if stated:
        basis = _check_time_basis(stated)
        return basis, basis, []
    if commercial_timezone:
        return "UTC", "UTC", []
    return ("UTC", "UTC (assumed; site clock unknown)",
            [TIME_BASIS_ASSUMED_WARNING])


_ONE_HOUR = pd.Timedelta(hours=1)
_ONE_DAY = pd.Timedelta(days=1)
_ONE_WEEK = pd.Timedelta(days=7)


# ---------------------------------------------------------------------------
# Shape validation + month-day-hour alignment
# ---------------------------------------------------------------------------


def _validate_year_block(idx: pd.DatetimeIndex, *, what: str) -> int:
    """Pin the one shape a generated profile can align to: a tz-naive,
    contiguous, hourly index spanning ONE full calendar year. Returns the
    year. ``what`` names the block in error messages ("generated series",
    "network snapshots", "period 2032")."""
    if not isinstance(idx, pd.DatetimeIndex):
        raise UnsupportedSnapshotsError(
            f"{what}: expected a DatetimeIndex, got {type(idx).__name__}"
        )
    if idx.tz is not None:
        raise UnsupportedSnapshotsError(
            f"{what}: timezone-aware snapshots are not supported — generated "
            "profiles are tz-naive UTC"
        )
    if len(idx) < 2 or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise UnsupportedSnapshotsError(
            f"{what}: snapshots must be a strictly increasing, duplicate-free "
            "full calendar year (Jan 1 00:00 – Dec 31 23:00, hourly)"
        )
    diffs = idx.to_series().diff().dropna()
    if (diffs != _ONE_HOUR).any():
        med = diffs.median()
        if med < _ONE_HOUR:
            raise UnsupportedSnapshotsError(
                f"{what}: sub-hourly snapshots ({med}) are not supported — "
                "generated profiles are hourly"
            )
        raise UnsupportedSnapshotsError(
            f"{what}: snapshots must be contiguous hourly steps; found steps "
            f"from {diffs.min()} to {diffs.max()} — segmented or non-hourly "
            "horizons are not supported"
        )
    years = idx.year.unique().tolist()
    year = years[0] if len(years) == 1 else None
    if (
        year is None
        or idx[0] != pd.Timestamp(year, 1, 1, 0)
        or idx[-1] != pd.Timestamp(year, 12, 31, 23)
    ):
        raise UnsupportedSnapshotsError(
            f"{what}: snapshots must span one full calendar year (Jan 1 00:00 "
            "– Dec 31 23:00, hourly); partial-year horizons are not supported "
            "yet. Generated profiles apply to full-year hourly projects; a "
            "representative-week template (e.g. an Energy Hub study week) "
            "needs its weeks sampled from the weather year, which is "
            "planned — until then, upload a profile for the template's "
            "snapshots instead"
        )
    return int(year)


def _check_time_basis(time_basis: str) -> str:
    if time_basis == "UTC":
        return time_basis
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(time_basis)
    except Exception as exc:  # ValueError / ZoneInfoNotFoundError / TypeError
        raise InvalidTimeBasisError(
            f"time basis {time_basis!r} is neither 'UTC' nor an IANA time "
            "zone (e.g. 'Europe/Berlin') — name the zone whose wall clock "
            "the snapshots are"
        ) from exc
    return time_basis


def _utc_keys(target_idx: pd.DatetimeIndex,
              time_basis: str) -> list[list[tuple[int, int, int]]]:
    """For each target snapshot, the month-day-hour key(s) of the UTC
    instant(s) it stands for. UTC: the snapshot itself. A zone's wall clock:
    the repeated autumn hour stands for both UTC hours (their mean is
    taken), the skipped spring hour for the next valid one. Keys outside
    the weather year (local new-year midnight east of Greenwich) wrap by
    month-day-hour, the same rule as tiling."""
    if time_basis == "UTC":
        return [[(t.month, t.day, t.hour)] for t in target_idx]
    first = target_idx.tz_localize(
        time_basis, ambiguous=np.ones(len(target_idx), dtype=bool),
        nonexistent="shift_forward",
    ).tz_convert("UTC")
    second = target_idx.tz_localize(
        time_basis, ambiguous=np.zeros(len(target_idx), dtype=bool),
        nonexistent="shift_forward",
    ).tz_convert("UTC")
    out = []
    for a, b in zip(first, second):
        keys = [(a.month, a.day, a.hour)]
        if b != a:
            keys.append((b.month, b.day, b.hour))
        out.append(keys)
    return out


def _map_by_mdh(series: pd.Series, target_idx: pd.DatetimeIndex, *,
                src_year: int, label: str,
                time_basis: str = DEFAULT_TIME_BASIS,
                ) -> tuple[np.ndarray, bool]:
    """Map weather-year values onto `target_idx` by month-day-hour of the
    UTC instant each snapshot stands for (see `_utc_keys`).

    Returns (values, feb29_dropped). Raises LeapYearMismatchError when the
    target needs a Feb 29 the weather year does not have — per spec §4,
    fabricating 24 h is worse than refusing.
    """
    src_lookup = {
        (t.month, t.day, t.hour): v for t, v in series.items()
    }
    src_has_feb29 = (2, 29, 0) in src_lookup
    target_has_feb29 = any(t.month == 2 and t.day == 29 for t in target_idx)
    if target_has_feb29 and not src_has_feb29:
        raise LeapYearMismatchError(
            f"{label} is a leap year but weather year {src_year} has no "
            "Feb 29 — refusing to fabricate 24 hours. Pick a leap weather "
            "year, or rebuild the horizon on a non-leap year."
        )
    values = np.empty(len(target_idx), dtype=float)
    for i, keys in enumerate(_utc_keys(target_idx, time_basis)):
        values[i] = float(np.mean([src_lookup[k] for k in keys]))
    return values, bool(src_has_feb29 and not target_has_feb29)


def _validate_daily_block(idx: pd.DatetimeIndex, *, what: str) -> int:
    """Pin a daily source: tz-naive, contiguous 1-day steps spanning ONE
    full calendar year (Jan 1 – Dec 31, midnight labels). Returns the year."""
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is not None:
        raise UnsupportedSnapshotsError(
            f"{what}: expected a tz-naive DatetimeIndex of daily means"
        )
    if len(idx) < 2 or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise UnsupportedSnapshotsError(
            f"{what}: a daily profile must be strictly increasing and "
            "duplicate-free (Jan 1 – Dec 31, one row per day)"
        )
    diffs = idx.to_series().diff().dropna()
    if (diffs != _ONE_DAY).any():
        raise UnsupportedSnapshotsError(
            f"{what}: a daily profile must step in contiguous 1-day "
            f"increments; found steps from {diffs.min()} to {diffs.max()}"
        )
    years = idx.year.unique().tolist()
    year = years[0] if len(years) == 1 else None
    if (
        year is None
        or idx[0] != pd.Timestamp(year, 1, 1)
        or idx[-1] != pd.Timestamp(year, 12, 31)
    ):
        raise UnsupportedSnapshotsError(
            f"{what}: a daily profile must span one full calendar year "
            "(Jan 1 – Dec 31); partial years are not supported"
        )
    return int(year)


def _validate_weekly_block(idx: pd.DatetimeIndex, *, what: str) -> int:
    """Pin a weekly source: tz-naive, contiguous 7-day steps (W-MON labels)
    covering ONE full calendar year. The first label may fall in the previous
    December (the week containing Jan 1); the weather year is the maximum
    label year. Returns that year."""
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is not None:
        raise UnsupportedSnapshotsError(
            f"{what}: expected a tz-naive DatetimeIndex of weekly means"
        )
    if len(idx) < 2 or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise UnsupportedSnapshotsError(
            f"{what}: a weekly profile must be strictly increasing and "
            "duplicate-free (one row per week)"
        )
    diffs = idx.to_series().diff().dropna()
    if (diffs != _ONE_WEEK).any():
        raise UnsupportedSnapshotsError(
            f"{what}: a weekly profile must step in contiguous 7-day "
            f"increments; found steps from {diffs.min()} to {diffs.max()}"
        )
    year = int(idx.year.max())
    if (
        idx[0] > pd.Timestamp(year, 1, 1)
        or idx[-1] + pd.Timedelta(days=6) < pd.Timestamp(year, 12, 31)
    ):
        raise UnsupportedSnapshotsError(
            f"{what}: a weekly profile must cover one full calendar year "
            f"(the weeks containing Jan 1 and Dec 31 of {year})"
        )
    return year


def _map_by_md(series: pd.Series, target_idx: pd.DatetimeIndex, *,
               src_year: int, label: str) -> tuple[np.ndarray, bool]:
    """Map daily weather-year values onto an hourly target by month-day —
    every hour of a day gets that day's mean. Same leap rules as
    `_map_by_mdh`."""
    src_lookup = {(t.month, t.day): v for t, v in series.items()}
    src_has_feb29 = (2, 29) in src_lookup
    target_has_feb29 = any(t.month == 2 and t.day == 29 for t in target_idx)
    if target_has_feb29 and not src_has_feb29:
        raise LeapYearMismatchError(
            f"{label} is a leap year but weather year {src_year} has no "
            "Feb 29 — refusing to fabricate a day. Pick a leap weather "
            "year, or rebuild the horizon on a non-leap year."
        )
    values = np.empty(len(target_idx), dtype=float)
    for i, t in enumerate(target_idx):
        values[i] = src_lookup[(t.month, t.day)]
    return values, bool(src_has_feb29 and not target_has_feb29)


def _map_weekly(series: pd.Series, target_idx: pd.DatetimeIndex, *,
                src_year: int, label: str) -> np.ndarray:
    """Upsample weekly means onto an hourly target by step-hold: every hour
    gets the mean of the week containing it (asof on the Monday labels).
    Same-calendar-year only — weeks do not align across years (ruling), so a
    different target year is a typed refusal rather than a silent shift."""
    target_year = int(target_idx[0].year)
    if target_year != src_year:
        raise UnsupportedSnapshotsError(
            f"weekly profiles apply to their own weather year only: source "
            f"covers {src_year}, {label} is {target_year}. Regenerate at "
            "hourly or daily resolution to reach a different target year."
        )
    src = series.sort_index()
    values = src.to_numpy(dtype=float)[
        src.index.searchsorted(target_idx, side="right") - 1
    ]
    return values


def _align_to_snapshots(series: pd.Series, snapshots, *,
                        resolution: str = "hourly",
                        time_basis: str = DEFAULT_TIME_BASIS,
                        ) -> tuple[pd.Series, dict]:
    """The spec §4 alignment table, extended for C2 resolutions. Returns
    (aligned, align_meta) where align_meta carries ``weather_year``,
    ``leap_day``, ``tiled`` and ``resolution``.

    - hourly: the original month-day-hour mapping (plus D7=B period tiling);
    - daily: month-day mapping, every hour of a day gets the day's mean,
      same leap rules, same period tiling;
    - weekly: step-hold upsample, SAME-CALENDAR-YEAR only (weeks do not
      align across years), no multi-period tiling.

    ``time_basis`` shifts the hourly mapping only (`_utc_keys`); a day or
    week mean is not re-binned by a 1–2 h offset.
    """
    resolution = str(resolution or "hourly").strip().lower()

    if resolution == "hourly":
        src_year = _validate_year_block(
            pd.DatetimeIndex(series.index), what="generated series",
        )

        if isinstance(snapshots, pd.MultiIndex):
            # D7=B: tile the weather year into EVERY investment period, leap
            # rules per period.
            lvl0 = snapshots.get_level_values(0)
            periods = [int(p) for p in lvl0.unique()]
            out = np.empty(len(snapshots), dtype=float)
            dropped_any = False
            for p in periods:
                mask = np.asarray(lvl0 == p)
                block = pd.DatetimeIndex(snapshots[mask].get_level_values(1))
                _validate_year_block(block, what=f"period {p}")
                values, dropped = _map_by_mdh(
                    series, block, src_year=src_year, label=f"period {p}",
                    time_basis=time_basis,
                )
                out[mask] = values
                dropped_any = dropped_any or dropped
            aligned = pd.Series(out, index=snapshots, name=series.name)
            return aligned, {
                "weather_year": src_year,
                "leap_day": "dropped" if dropped_any else "kept",
                "tiled": True,
                "periods": periods,
                "resolution": "hourly",
            }

        target_idx = pd.DatetimeIndex(snapshots)
        _validate_year_block(target_idx, what="network snapshots")
        values, dropped = _map_by_mdh(
            series, target_idx, src_year=src_year, label="the target year",
            time_basis=time_basis,
        )
        aligned = pd.Series(values, index=target_idx, name=series.name)
        return aligned, {
            "weather_year": src_year,
            "leap_day": "dropped" if dropped else "kept",
            "tiled": False,
            "periods": None,
            "resolution": "hourly",
        }

    if resolution == "daily":
        src_year = _validate_daily_block(
            pd.DatetimeIndex(series.index), what="generated series",
        )

        if isinstance(snapshots, pd.MultiIndex):
            lvl0 = snapshots.get_level_values(0)
            periods = [int(p) for p in lvl0.unique()]
            out = np.empty(len(snapshots), dtype=float)
            dropped_any = False
            for p in periods:
                mask = np.asarray(lvl0 == p)
                block = pd.DatetimeIndex(snapshots[mask].get_level_values(1))
                _validate_year_block(block, what=f"period {p}")
                values, dropped = _map_by_md(
                    series, block, src_year=src_year, label=f"period {p}",
                )
                out[mask] = values
                dropped_any = dropped_any or dropped
            aligned = pd.Series(out, index=snapshots, name=series.name)
            return aligned, {
                "weather_year": src_year,
                "leap_day": "dropped" if dropped_any else "kept",
                "tiled": True,
                "periods": periods,
                "resolution": "daily",
            }

        target_idx = pd.DatetimeIndex(snapshots)
        _validate_year_block(target_idx, what="network snapshots")
        values, dropped = _map_by_md(
            series, target_idx, src_year=src_year, label="the target year",
        )
        aligned = pd.Series(values, index=target_idx, name=series.name)
        return aligned, {
            "weather_year": src_year,
            "leap_day": "dropped" if dropped else "kept",
            "tiled": False,
            "periods": None,
            "resolution": "daily",
        }

    if resolution == "weekly":
        src_year = _validate_weekly_block(
            pd.DatetimeIndex(series.index), what="generated series",
        )
        if isinstance(snapshots, pd.MultiIndex):
            raise UnsupportedSnapshotsError(
                "weekly profiles cannot tile multi-investment-period "
                "snapshots — regenerate at hourly or daily resolution"
            )
        target_idx = pd.DatetimeIndex(snapshots)
        _validate_year_block(target_idx, what="network snapshots")
        values = _map_weekly(
            series, target_idx, src_year=src_year, label="the target year",
        )
        aligned = pd.Series(values, index=target_idx, name=series.name)
        return aligned, {
            "weather_year": src_year,
            "leap_day": "kept",  # same-year step-hold covers Feb 29 itself
            "tiled": False,
            "periods": None,
            "resolution": "weekly",
        }

    raise UnsupportedSnapshotsError(
        f"unknown profile resolution {resolution!r}; expected one of "
        "hourly, daily, weekly"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def assign_profile(
    ctx,
    component: str,
    series: pd.Series,
    meta: Mapping[str, Any],
    *,
    now: _dt.datetime | str | None = None,
    time_basis: str | None = None,
) -> dict:
    """Apply a generated per-unit profile to ``component``'s ``p_max_pu``.

    ``ctx`` is the ProjectContext that owns the network AND the user-ts
    stores — the two are written as a pair (the tenancy rule of
    `services/user_timeseries.py`). Returns a summary dict for the caller
    (router response / chat tool payload). Raises a typed ProfileApplyError
    subclass on every refusal, having written nothing.

    ``time_basis``: what the network's naive snapshots mean — "UTC", or an
    IANA zone ("Europe/Berlin") whose wall clock they are (the series is
    shifted by that zone's offset, DST included). None (not stated) follows
    `resolve_time_basis`: UTC when the project's commercial timezone is set,
    else an unshifted apply recorded as assumed, with a warning.
    """
    if not isinstance(series, pd.Series):
        raise ProfileApplyError(
            f"expected a pandas Series (one component's profile), got "
            f"{type(series).__name__}"
        )
    if not isinstance(meta, Mapping):
        raise ProfileApplyError("meta must be the engine's provenance dict")

    n = ctx.network
    if component not in n.generators.index:
        raise ComponentNotFoundError(
            f"generator {component!r} not found on the network"
        )
    carrier = str(n.generators.at[component, "carrier"] or "")
    is_wind, is_solar = carrier_flags(carrier)
    if not (is_wind or is_solar):
        raise CarrierMismatchError(
            f"generator {component!r} has carrier {carrier!r}; generated "
            "profiles apply to wind/solar assets only"
        )
    tech = str(meta.get("technology", "")).strip().lower()
    if tech not in ("wind", "solar"):
        raise CarrierMismatchError(
            f"profile meta carries technology {tech!r}; expected wind/solar"
        )
    if (tech == "wind" and not is_wind) or (tech == "solar" and not is_solar):
        raise CarrierMismatchError(
            f"profile technology {tech!r} does not match generator "
            f"{component!r}'s carrier {carrier!r}"
        )

    resolution = str(meta.get("resolution") or "hourly").strip().lower()
    time_basis, recorded_basis, warnings = resolve_time_basis(
        time_basis, _commercial_timezone(ctx))
    aligned, align_meta = _align_to_snapshots(
        series, n.snapshots, resolution=resolution, time_basis=time_basis,
    )

    # A p_max_pu bound must be finite at every snapshot — a NaN masks the
    # constraint row out of the LP (the Phase 12f rule). Values are clipped
    # into [0, 1] defensively; a clip is recorded in meta.
    vals = aligned.to_numpy(dtype=float)
    if not np.isfinite(vals).all():
        raise ProfileApplyError(
            f"profile for {component!r} has non-finite values after "
            "alignment — refusing to write a bound with holes"
        )
    clipped = bool(((vals < 0.0) | (vals > 1.0)).any())
    if clipped:
        vals = np.clip(vals, 0.0, 1.0)
        aligned = pd.Series(vals, index=aligned.index, name=aligned.name)

    if now is None:
        applied_at = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(now, str):
        applied_at = _dt.datetime.fromisoformat(now)
    else:
        applied_at = now

    key = ("generators", "p_max_pu", component)
    full_meta: dict[str, Any] = {
        **dict(meta),
        "weather_year": align_meta["weather_year"],
        "leap_day": align_meta["leap_day"],
        "tiled": align_meta["tiled"],
        "resolution": align_meta["resolution"],
        "time_basis": recorded_basis,
        "applied_at": applied_at.isoformat(),
    }
    if align_meta["periods"] is not None:
        full_meta["periods"] = align_meta["periods"]
    if align_meta["resolution"] != "hourly":
        # The basis is recorded, but a day/week mean is not re-binned by a
        # 1–2 h offset — say so, so the provenance cannot imply a shift.
        full_meta["time_basis_note"] = (
            f"not re-binned at {align_meta['resolution']} resolution")
    if clipped:
        full_meta["clipped"] = True

    # Store write under the store lock, network write under the PyPSA lock —
    # the same two-lock dance as the upload path (`_apply_profile_upload`).
    # Overwrite semantics: regeneration replaces the same key.
    with _user_ts_lock:
        ctx.user_ts[key] = aligned
        ctx.user_ts_meta[key] = full_meta
    with PyPSAService.get_lock():
        _reapply_user_ts_to_network(n, store=ctx.user_ts)
        # A new profile invalidates solved dispatch exactly like a topology
        # edit — flip freshness so Results can't serve the old solve.
        dispatch_cleared = clear_dispatch(n)

    desc = (
        f"Applied generated {tech} profile to {component}: source "
        f"{meta.get('source', '?')}, weather year {align_meta['weather_year']}"
    )
    if align_meta["resolution"] != "hourly":
        desc += f", {align_meta['resolution']} means upsampled to hourly"
    if align_meta["tiled"]:
        desc += f", tiled over {len(align_meta['periods'])} investment periods"
    if align_meta["leap_day"] == "dropped":
        desc += ", Feb 29 dropped"
    change_log_service.log("timeseries", "Generator", component, desc)

    return {
        "component": component,
        "attribute": "p_max_pu",
        "hours": int(len(aligned)),
        "weather_year": align_meta["weather_year"],
        "leap_day": align_meta["leap_day"],
        "tiled": align_meta["tiled"],
        "periods": align_meta["periods"],
        "resolution": align_meta["resolution"],
        "time_basis": recorded_basis,
        "warnings": warnings,
        "dispatch_cleared": bool(dispatch_cleared),
        "mean": float(aligned.mean()),
        "peak": float(aligned.max()),
    }
