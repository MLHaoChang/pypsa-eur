"""Apply a generated profile to a network component.

Originally the renewable path (generator p_max_pu); since the time-series
generators ticket 01 the target is any ``(component_type, attribute)`` in
`TARGETS`, and the meta's family (`families.py`) decides which targets it may
write. The alignment and write contract below hold for every target.

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

Write contract: store entry ``(component_type, attribute, name)`` + provenance
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
from dataclasses import dataclass
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd

from services import change_log_service
from services.dispatch_status import clear_dispatch
from services.profile_gen.engine import ProfileGenError
from services.profile_gen.families import family_of, get_family
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
    """The named component does not exist on the network."""


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


class UnknownFamilyError(ProfileApplyError):
    """The meta names a profile family nobody registered."""


class DcLoadOwnedError(ProfileApplyError):
    """The project's `dc_load` spec owns the data-centre load (#421)."""


class UnsupportedTargetError(ProfileApplyError):
    """The (component_type, attribute) pair is not in `TARGETS`."""


class FamilyTargetMismatchError(CarrierMismatchError):
    """The profile's family may not write this target (a renewable capacity
    factor onto a load, a load shape onto a generator bound). A subclass of
    CarrierMismatchError: the renewable refusal it generalises."""


class ProfileBoundConflictError(ProfileApplyError):
    """The new bound crosses the component's other bound (p_min_pu above
    p_max_pu at some snapshot), which makes the LP infeasible."""


class InvalidSeriesUnitError(ProfileApplyError):
    """The meta's unit does not fit the target, or the series does not match
    the normalisation its unit states."""


# The mapping basis when the snapshots' clock is not stated (see
# `resolve_time_basis`): the UTC weather hours map straight on.
DEFAULT_TIME_BASIS = "UTC"

TIME_BASIS_ASSUMED_WARNING = (
    "time_basis_assumed_utc: the profile is in UTC and was applied unshifted; "
    "if this project's snapshots are local time, set the time zone (a shift "
    "of the site's UTC offset, e.g. +1/+2 h in Central Europe)"
)


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
# Targets: what the apply path can write, and the bounds of each
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetSpec:
    """How one ``(component_type, attribute)`` is written.

    ``kind``:
      * ``per_unit`` — a [0, 1] bound; out-of-range values are clipped and the
        clip recorded; p_min_pu and p_max_pu are checked against each other;
      * ``power`` — MW, never negative (refused, not clipped: a net export is
        a generator beside the load, not a negative load); a shape is scaled
        by its unit, and its realised energy is recorded;
      * ``price`` — currency/MWh of any sign, with its money basis recorded;
      * ``ratio`` — strictly positive (an efficiency or COP).
    """

    kind: Literal["per_unit", "power", "price", "ratio"]
    display: str  # changelog class, e.g. "Generator"
    noun: str     # error wording, e.g. "generator"
    # The snapshot weighting a power series' energy is counted with.
    weighting: str = "generators"


TARGETS: dict[tuple[str, str], TargetSpec] = {
    ("generators", "p_max_pu"): TargetSpec("per_unit", "Generator", "generator"),
    ("generators", "p_min_pu"): TargetSpec("per_unit", "Generator", "generator"),
    ("loads", "p_set"): TargetSpec("power", "Load", "load"),
    ("storage_units", "inflow"): TargetSpec("power", "StorageUnit", "storage unit",
                                            weighting="stores"),
    ("generators", "marginal_cost"): TargetSpec("price", "Generator", "generator"),
    ("links", "marginal_cost"): TargetSpec("price", "Link", "link"),
    ("links", "efficiency"): TargetSpec("ratio", "Link", "link"),
}

# The other half of a per-unit pair: writing one is checked against the other.
_BOUND_PAIRS = {"p_min_pu": "p_max_pu", "p_max_pu": "p_min_pu"}

# A shape family's realised energy may differ from its profile's by this much
# before the apply warns (gatekeeper review 2026-10-20).
ENERGY_TOLERANCE = 0.02
ENERGY_MISMATCH_WARNING = "energy_mismatch"
BOUND_FORCES_OFF_WARNING = "bound_forces_off"
MUST_RUN_SINK_WARNING = "must_run_exceeds_sink"
MIN_STABLE_WARNING = "min_stable_not_must_run"
REPLACES_SERIES_WARNING = "replaces_existing_series"

# How close a normalised shape's peak/mean must be to 1.
_NORM_RTOL = 1e-3


def _target(component_type: str, attribute: str) -> TargetSpec:
    try:
        return TARGETS[(component_type, attribute)]
    except KeyError:
        known = ", ".join(f"{c}/{a}" for c, a in sorted(TARGETS))
        raise UnsupportedTargetError(
            f"profiles cannot be applied to {component_type}/{attribute}; "
            f"supported targets: {known}"
        ) from None


def _scale_power(series: pd.Series, meta: Mapping[str, Any]) -> tuple[float, dict]:
    """The factor that turns ``series`` into MW, per its stated unit, checked
    against the SOURCE series (the source year is what a per-unit mean or
    peak refers to)."""
    unit = str(meta.get("unit") or "MW")
    if unit == "MW":
        return 1.0, {"unit": "MW"}
    if unit not in ("per_unit_peak", "per_unit_mean"):
        raise InvalidSeriesUnitError(
            f"unit {unit!r} does not fit a power target; expected MW, "
            "per_unit_peak or per_unit_mean"
        )
    try:
        scale = float(meta.get("scale_mw"))
    except (TypeError, ValueError):
        scale = float("nan")
    if not (np.isfinite(scale) and scale > 0):
        raise InvalidSeriesUnitError(
            f"a {unit} shape needs a positive scale_mw, got "
            f"{meta.get('scale_mw')!r}"
        )
    vals = series.to_numpy(dtype=float)
    ref = float(np.nanmax(vals)) if unit == "per_unit_peak" else float(np.nanmean(vals))
    if not np.isclose(ref, 1.0, rtol=_NORM_RTOL, atol=0.0):
        what = "peak" if unit == "per_unit_peak" else "mean"
        raise InvalidSeriesUnitError(
            f"the series is declared {unit} but its source-year {what} is "
            f"{ref:.4g}, not 1"
        )
    extra = {"unit": unit, "scale_mw": scale}
    if unit == "per_unit_mean":
        extra["per_unit_mean_of"] = "source_year"
    return scale, extra


def _money(meta: Mapping[str, Any]) -> dict:
    """A price series' unit and money basis, in the money-year convention's
    words ("real basis, 2020 EUR")."""
    unit = str(meta.get("unit") or "")
    cur, _, per = unit.partition("/")
    if per != "MWh" or not (len(cur) == 3 and cur.isalpha() and cur.isupper()):
        raise InvalidSeriesUnitError(
            f"a price series must be in currency/MWh (e.g. EUR/MWh), got "
            f"{unit!r}"
        )
    basis = str(meta.get("price_basis") or "").strip().lower()
    if basis not in ("real", "nominal"):
        raise InvalidSeriesUnitError(
            f"a price series must state price_basis real or nominal, got "
            f"{meta.get('price_basis')!r}"
        )
    year = meta.get("currency_year")
    # Required: every price in the model is real base-year money read from
    # its price-data year (the money-year convention), so a price series
    # without one would carry an unknown into the objective (gatekeeper
    # review 2026-10-20).
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise InvalidSeriesUnitError(
            f"a price series must state its currency_year (the year of its "
            f"price data), got {year!r}") from None
    return {"unit": unit, "price_basis": basis, "currency_year": year,
            "money": f"{basis} basis, {year} {cur}"}


def _current_bound(n, component_type: str, component: str, attribute: str):
    """The component's present value of ``attribute`` per snapshot: its
    series when it has one, else its static value."""
    static = float(getattr(n, component_type).at[component, attribute])
    table = getattr(n, f"{component_type}_t")[attribute]
    if component in table.columns:
        # A gap in the series is the static value, as PyPSA reads it.
        return (table[component].reindex(n.snapshots).fillna(static)
                .to_numpy(dtype=float))
    return np.full(len(n.snapshots), static)


def _check_bound_pair(n, component_type: str, component: str, attribute: str,
                      vals: np.ndarray) -> list[str]:
    """Refuse a p_min_pu above p_max_pu at any snapshot — the LP would be
    infeasible. A committable unit is the exception: its bounds are
    status·p_min_pu ≤ p/p_nom ≤ status·p_max_pu, so a crossing hour forces it
    off instead (gatekeeper review 2026-10-20); that is written and warned."""
    other = _BOUND_PAIRS.get(attribute)
    if other is None:
        return []
    current = _current_bound(n, component_type, component, other)
    lo, hi = (vals, current) if attribute == "p_min_pu" else (current, vals)
    bad = np.flatnonzero(lo > hi + 1e-9)
    if not bad.size:
        return []
    i = int(bad[0])
    table = getattr(n, component_type)
    if "committable" in table.columns and bool(table.at[component, "committable"]):
        return [(
            f"{BOUND_FORCES_OFF_WARNING}: {component!r} is committable and its "
            f"p_min_pu exceeds p_max_pu at {bad.size} snapshots (first "
            f"{n.snapshots[i]}); the unit is forced off in those hours"
        )]
    raise ProfileBoundConflictError(
        f"{component!r}: p_min_pu {lo[i]:.4g} would exceed p_max_pu "
        f"{hi[i]:.4g} at snapshot {n.snapshots[i]} ({bad.size} snapshots "
        "in all) — the LP would be infeasible"
    )


def _replaced_series(ctx, key: tuple[str, str, str], family: str,
                     ) -> tuple[str | None, list[str]]:
    """What series this write replaces — a profile of some family, a user
    upload, or the network's own — and a warning unless it is a profile of
    the same family (regeneration). The old series stays in the undo
    snapshot the caller pushed (gatekeeper review 2026-10-20: replace, and
    say so; never compose silently)."""
    component_type, attribute, component = key
    previous_meta = ctx.user_ts_meta.get(key)
    if previous_meta is not None:
        prev_family = family_of(previous_meta)
        what = f"{prev_family} profile"
        same = prev_family == family
    elif key in ctx.user_ts:
        what, same = "uploaded series", False
    elif component in getattr(ctx.network, f"{component_type}_t")[attribute].columns:
        what, same = "network series", False
    else:
        return None, []
    if same:
        return what, []
    return what, [f"{REPLACES_SERIES_WARNING}: this replaces {component!r}'s "
                  f"{attribute} {what}; undo restores it"]


def _bus_sink_mw(n, bus: str) -> np.ndarray:
    """Per snapshot, what a bus can absorb: its loads, its storage units'
    charging power and its outgoing links' capacity."""
    total = np.zeros(len(n.snapshots))
    for name in n.loads.index[n.loads.bus == bus]:
        if name in n.loads_t.p_set.columns:
            total += n.loads_t.p_set[name].reindex(n.snapshots).fillna(
                float(n.loads.at[name, "p_set"])).to_numpy(dtype=float)
        else:
            total += float(n.loads.at[name, "p_set"])
    su = n.storage_units[n.storage_units.bus == bus]
    total += float(su.p_nom.sum())
    links = n.links[n.links.bus0 == bus]
    total += float((links.p_nom * links.p_max_pu).sum())
    return total


def _floor_warnings(n, component: str, vals: np.ndarray) -> tuple[str, list[str]]:
    """The meaning of a generator p_min_pu, and its warnings: on a committable
    unit a floor is a minimum stable level when committed, not a must-run;
    on any other unit, a floor above what its bus can absorb forces spill
    or makes the dispatch and ENS solves infeasible."""
    gens = n.generators
    if "committable" in gens.columns and bool(gens.at[component, "committable"]):
        return "min stable level when committed", [
            f"{MIN_STABLE_WARNING}: {component!r} is committable, so this "
            "floor holds only while it is committed; it does not force it on"]
    floor = vals * float(gens.at[component, "p_nom"])
    sink = _bus_sink_mw(n, str(gens.at[component, "bus"]))
    over = np.flatnonzero(floor > sink + 1e-9)
    if not over.size:
        return "must-run floor", []
    i = int(over[0])
    return "must-run floor", [
        f"{MUST_RUN_SINK_WARNING}: {component!r}'s must-run floor of "
        f"{floor[i]:.0f} MW exceeds the {sink[i]:.0f} MW its bus can absorb "
        f"(loads, storage charging, export links) at {n.snapshots[i]} "
        f"({over.size} snapshots); the dispatch and ENS solves must spill or "
        "go infeasible"]


def _modelled_mwh(n, aligned: pd.Series, weighting: str) -> dict[str, float]:
    """Σ value × snapshot_weightings[weighting], per investment period."""
    w = n.snapshot_weightings[weighting].reindex(aligned.index).to_numpy(dtype=float)
    energy = pd.Series(aligned.to_numpy(dtype=float) * w, index=aligned.index)
    if isinstance(aligned.index, pd.MultiIndex):
        per = energy.groupby(level=0).sum()
        return {str(int(p)): float(v) for p, v in per.items()}
    return {"total": float(energy.sum())}


def _energy_record(n, series: pd.Series, aligned: pd.Series, scale: float,
                   target: TargetSpec, src_year: int) -> tuple[dict, list[str]]:
    """``profile_annual_mwh`` (the source year's energy: its mean power over
    its hours — the plain sum for an hourly series, while a daily or weekly
    one is a mean per step whose edge weeks straddle the year),
    ``modelled_mwh`` per period, and the energy_mismatch warning."""
    year_hours = 8784 if pd.Timestamp(year=src_year, month=1, day=1).is_leap_year else 8760
    profile_mwh = float(np.nanmean(series.to_numpy(dtype=float)) * scale * year_hours)
    modelled = _modelled_mwh(n, aligned, target.weighting)
    warnings: list[str] = []
    if profile_mwh > 0:
        off = {k: v for k, v in modelled.items()
               if abs(v - profile_mwh) / profile_mwh > ENERGY_TOLERANCE}
        if off:
            parts = ", ".join(f"{k}: {v:,.0f} MWh" for k, v in off.items())
            warnings.append(
                f"{ENERGY_MISMATCH_WARNING}: the profile's source year "
                f"holds {profile_mwh:,.0f} MWh but the model sees "
                f"{parts} (snapshot weightings), more than "
                f"{ENERGY_TOLERANCE:.0%} apart"
            )
    return {"profile_annual_mwh": profile_mwh, "modelled_mwh": modelled}, warnings


def preview_energy(ctx, series: pd.Series, meta: Mapping[str, Any], *,
                   component_type: str, attribute: str,
                   time_basis: str | None = None) -> dict:
    """What `assign_profile` would record for a power series' energy —
    ``profile_annual_mwh``, ``modelled_mwh`` and its warnings, the time-basis
    one included — aligned exactly as apply aligns, written nowhere. Lets a
    caller show the gap on a sampled model (a one-week template) before the
    user applies."""
    target = _target(component_type, attribute)
    if target.kind != "power":
        raise UnsupportedTargetError(
            f"{component_type}/{attribute} is not a power target; it has no energy")
    scale, _ = _scale_power(series, meta)
    basis, _recorded, warnings = resolve_time_basis(time_basis, _commercial_timezone(ctx))
    resolution = str(meta.get("resolution") or "hourly").strip().lower()
    aligned, align_meta = _align_to_snapshots(
        series, ctx.network.snapshots, resolution=resolution, time_basis=basis)
    aligned = aligned * scale
    energy, energy_warnings = _energy_record(
        ctx.network, series, aligned, scale, target, int(align_meta["weather_year"]))
    return {**energy, "warnings": [*warnings, *energy_warnings]}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def assign_profile(
    ctx,
    component: str,
    series: pd.Series,
    meta: Mapping[str, Any],
    *,
    component_type: str = "generators",
    attribute: str = "p_max_pu",
    now: _dt.datetime | str | None = None,
    time_basis: str | None = None,
) -> dict:
    """Apply a generated profile to ``component``'s ``attribute``.

    The target ``(component_type, attribute)`` must be in `TARGETS`, and the
    meta's ``family`` (renewable when absent) must allow it. The defaults are
    the original renewable case, generator p_max_pu.

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

    target = _target(component_type, attribute)
    family_name = family_of(meta)
    family = get_family(family_name)
    if (component_type, attribute) not in family.targets:
        allowed = ", ".join(f"{c}/{a}" for c, a in sorted(family.targets))
        raise FamilyTargetMismatchError(
            f"a {family_name} profile cannot be applied to "
            f"{component_type}/{attribute}; it applies to {allowed}"
        )

    n = ctx.network
    if component not in getattr(n, component_type).index:
        raise ComponentNotFoundError(
            f"{target.noun} {component!r} not found on the network"
        )
    if family.guard is not None:
        family.guard(ctx, component_type, component, meta)

    resolution = str(meta.get("resolution") or "hourly").strip().lower()
    unit_meta: dict[str, Any] = {}
    scale = 1.0
    if target.kind == "power":
        scale, unit_meta = _scale_power(series, meta)
    elif target.kind == "price":
        unit_meta = _money(meta)

    time_basis, recorded_basis, warnings = resolve_time_basis(
        time_basis, _commercial_timezone(ctx))
    aligned, align_meta = _align_to_snapshots(
        series, n.snapshots, resolution=resolution, time_basis=time_basis,
    )

    # Every written value must be finite at every snapshot — a NaN masks the
    # constraint row out of the LP (the Phase 12f rule).
    vals = aligned.to_numpy(dtype=float) * scale
    if not np.isfinite(vals).all():
        raise ProfileApplyError(
            f"profile for {component!r} has non-finite values after "
            "alignment — refusing to write a bound with holes"
        )
    clipped = False
    if target.kind == "per_unit":
        # Clipped into [0, 1] defensively; a clip is recorded in meta.
        clipped = bool(((vals < 0.0) | (vals > 1.0)).any())
        if clipped:
            vals = np.clip(vals, 0.0, 1.0)
        warnings = [*warnings, *_check_bound_pair(n, component_type, component,
                                                  attribute, vals)]
    elif target.kind == "power":
        if (vals < 0.0).any():
            i = int(np.flatnonzero(vals < 0.0)[0])
            raise ProfileApplyError(
                f"{target.noun} {component!r}: {attribute} is negative "
                f"({vals[i]:.4g} MW at {aligned.index[i]}); model a net "
                "export as a generator beside it, not a negative value"
            )
    elif target.kind == "ratio":
        if (vals <= 0.0).any():
            i = int(np.flatnonzero(vals <= 0.0)[0])
            raise ProfileApplyError(
                f"{target.noun} {component!r}: {attribute} must be positive, "
                f"got {vals[i]:.4g} at {aligned.index[i]}"
            )
    aligned = pd.Series(vals, index=aligned.index, name=aligned.name)

    if target.kind == "power":
        energy, energy_warnings = _energy_record(
            n, series, aligned, scale, target, int(align_meta["weather_year"]))
        unit_meta.update(energy)
        warnings = [*warnings, *energy_warnings]

    if now is None:
        applied_at = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(now, str):
        applied_at = _dt.datetime.fromisoformat(now)
    else:
        applied_at = now

    key = (component_type, attribute, component)
    replaced, replace_warnings = _replaced_series(ctx, key, family_name)
    warnings = [*warnings, *replace_warnings]
    meaning = None
    if (component_type, attribute) == ("generators", "p_min_pu"):
        meaning, floor_warnings = _floor_warnings(n, component, vals)
        warnings = [*warnings, *floor_warnings]
    full_meta: dict[str, Any] = {
        **dict(meta),
        **unit_meta,
        "family": family_name,
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
    if replaced:
        full_meta["replaced_series"] = replaced
    if meaning:
        full_meta["meaning"] = meaning

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

    if family_name == "renewable":
        what = f"generated {meta.get('technology', '?')} profile"
    else:
        what = f"{family_name} profile"
    desc = (
        f"Applied {what} to {component} {attribute}: source "
        f"{meta.get('source', '?')}, weather year {align_meta['weather_year']}"
    )
    if align_meta["resolution"] != "hourly":
        desc += f", {align_meta['resolution']} means upsampled to hourly"
    if align_meta["tiled"]:
        desc += f", tiled over {len(align_meta['periods'])} investment periods"
    if align_meta["leap_day"] == "dropped":
        desc += ", Feb 29 dropped"
    change_log_service.log("timeseries", target.display, component, desc)

    return {
        "component": component,
        "component_type": component_type,
        "attribute": attribute,
        "family": family_name,
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
        **({"modelled_mwh": unit_meta["modelled_mwh"],
            "profile_annual_mwh": unit_meta["profile_annual_mwh"]}
           if target.kind == "power" else {}),
    }
