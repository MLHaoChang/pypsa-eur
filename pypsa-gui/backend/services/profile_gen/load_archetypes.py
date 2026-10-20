"""Load archetypes: stated demand shapes for the load family (ticket 01, slice B1).

Each archetype turns a few parameters into an hourly shape over the source
year, peak-normalised, one column per load id. The engine
(`engine.generate_profile(family="load", technology=<archetype>)`) adds the
common meta; `apply.assign_profile` scales the shape by ``scale_mw`` onto a
load's ``p_set``.

Every shape here is assumed, not measured: meta ``provenance:
synthetic/assumed``. A measured series comes in as an upload.

Archetypes
  * ``industrial``: 1, 2 or 3 shifts on weekdays, or ``continuous``; a base
    load outside the shifts; ISO-week shutdowns.
  * ``commercial``: office, retail or hospital opening hours, with a summer
    cooling uplift while open.
  * ``residential``: households' morning and evening peaks; electric heating
    (``heat_pump`` / ``direct``) adds load by heating degree; an EV share
    adds evening charging.
  * ``ev_charging``: ``home`` (evening), ``depot`` (night) or ``public``
    (daytime) charging.
  * ``heat``: heat demand from temperature (heating degrees under a base
    temperature) on a hot-water floor.
  * ``data_centre``: wraps `services.dc_load_profile.hourly_profile`
    (IT MW × utilisation × PUE(T)); the dynamic envelope stays with #421's
    `read_envelope`.
  * ``library``: a month × daytype × hour shape from
    ``study_library/load_profiles/`` (the guided-study shapes).

Common parameters: exactly one of ``peak_mw`` / ``annual_mwh`` (the scale:
a peak gives a peak-normalised shape, an energy a source-year-mean-normalised
one, so every load holds it exactly); ``holidays`` (a list of MM-DD, or
YYYY-MM-DD in the source year, run like a Sunday); ``timezone`` (IANA zone
whose wall clock the working hours are on — the series stays on UTC
snapshots, the `resolve_time_basis` rule; without one the hours sit on the
snapshot clock, so on a project whose snapshots are UTC an office opens at
08:00 UTC); ``seasonal_amplitude`` (a winter-peaking cosine on any shape);
``noise`` (multiplicative, seeded; the seed used is recorded); ``seed``;
``preset``. A key an archetype does not know is refused, so a typo is never
silently ignored. Shapes are hourly: a day or week mean would lose the shape.

``shutdown_weeks`` are ISO weeks of the source year's ISO calendar; days at
the year's edges that belong to the neighbouring ISO year never match.

Temperature: a closed-form assumed ambient (annual mean, a seasonal cosine
peaking on day 200, a diurnal cosine peaking at 15:00 local) — the same form
as the DC model's, not weather data.

Country holiday calendars are not built in (no holiday library is a
dependency); ``holidays`` takes the dates.
"""
from __future__ import annotations

import csv
import math
import re
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from services.profile_gen.engine import ProfileGenError
# The synthetic source's numpy-version-stable normal stream, shared so both
# families draw noise the same way.
from services.profile_gen.synthetic import _normals

LIBRARY_DIR = Path(__file__).resolve().parents[2] / "study_library" / "load_profiles"
PROVENANCE = "synthetic/assumed"

_TWO_PI = 2.0 * math.pi


class UnknownArchetypeError(ProfileGenError):
    """The load family has no archetype (or preset) of that name."""


class InvalidArchetypeParamsError(ProfileGenError):
    """An archetype's parameters are missing, unknown or out of range."""


# ---------------------------------------------------------------------------
# Calendar + temperature helpers
# ---------------------------------------------------------------------------


class _Clock:
    """The local-clock features of a UTC hourly index."""

    def __init__(self, idx: pd.DatetimeIndex, timezone: str | None,
                 holidays: Any, year: int):
        if timezone:
            try:
                local = idx.tz_localize("UTC").tz_convert(timezone).tz_localize(None)
            except Exception as exc:  # noqa: BLE001 — pytz/zoneinfo raise many types
                raise InvalidArchetypeParamsError(
                    f"timezone {timezone!r} is not an IANA zone ({exc})") from None
        else:
            local = idx
        self.hour = local.hour.to_numpy()
        self.doy = local.dayofyear.to_numpy().astype(float)
        self.month = local.month.to_numpy()
        iso = local.isocalendar()
        # A shutdown week is the source year's ISO week: days at the year's
        # edges that belong to the neighbouring ISO year never match.
        self.week = np.where(iso.year.to_numpy() == year,
                             iso.week.to_numpy().astype(int), 0)
        md = local.strftime("%m-%d")
        dow = local.dayofweek.to_numpy()
        self.holiday = np.isin(np.asarray(md), _holidays(holidays, year))
        self.saturday = (dow == 5) & ~self.holiday
        self.sunday = (dow == 6) | self.holiday
        self.weekday = ~(self.saturday | self.sunday)


_MD = re.compile(r"(?:(\d{4})-)?(\d{2})-(\d{2})")


def _holidays(raw: Any, year: int) -> list[str]:
    """``holidays`` as MM-DD strings: a list of MM-DD, or of YYYY-MM-DD in
    the source year. Anything else is refused, never dropped."""
    if not isinstance(raw, (list, tuple)):
        raise InvalidArchetypeParamsError(
            f"holidays must be a list of MM-DD dates, got {raw!r}")
    out = []
    for item in raw:
        m = _MD.fullmatch(item.strip()) if isinstance(item, str) else None
        if m is None:
            raise InvalidArchetypeParamsError(
                f"holiday {item!r} is not MM-DD or YYYY-MM-DD")
        y, month, day = m.groups()
        if y is not None and int(y) != year:
            raise InvalidArchetypeParamsError(
                f"holiday {item!r} is not in the source year {year}")
        try:
            pd.Timestamp(year=2024, month=int(month), day=int(day))  # leap: 02-29 ok
        except ValueError:
            raise InvalidArchetypeParamsError(
                f"holiday {item!r} is not a calendar date") from None
        out.append(f"{month}-{day}")
    return out


def _num(p: Mapping[str, Any], key: str, lo: float | None = None,
         hi: float | None = None, *, hi_open: bool = False) -> float:
    """A numeric parameter in range, or a typed refusal naming it."""
    raw = p[key]
    if isinstance(raw, bool):
        raise InvalidArchetypeParamsError(f"{key} must be a number, got {raw!r}")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise InvalidArchetypeParamsError(
            f"{key} must be a number, got {raw!r}") from None
    too_high = hi is not None and (v >= hi if hi_open else v > hi)
    if not math.isfinite(v) or (lo is not None and v < lo) or too_high:
        bound = (f"[{lo}, {hi}{')' if hi_open else ']'}" if hi is not None
                 else f">= {lo}")
        raise InvalidArchetypeParamsError(f"{key} must be in {bound}, got {v!r}")
    return v


def _ambient_c(clock: _Clock, p: Mapping[str, Any]) -> np.ndarray:
    return (_num(p, "ambient_annual_mean_c", -40.0, 40.0)
            + _num(p, "ambient_seasonal_amplitude_k", 0.0, 40.0)
            * np.cos(_TWO_PI * (clock.doy - 200.0) / 365.0)
            + _num(p, "ambient_diurnal_amplitude_k", 0.0, 30.0)
            * np.cos(_TWO_PI * (clock.hour - 15.0) / 24.0))


_AMBIENT = {"ambient_annual_mean_c": 10.0, "ambient_seasonal_amplitude_k": 9.0,
            "ambient_diurnal_amplitude_k": 5.0}


def _in_hours(hour: np.ndarray, start: int, end: int) -> np.ndarray:
    """Whether start <= hour < end on a 24 h clock (end may wrap past midnight)."""
    if start <= end:
        return (hour >= start) & (hour < end)
    return (hour >= start) | (hour < end)


# ---------------------------------------------------------------------------
# Archetypes: params defaults + shape(clock, params, idx) -> raw >= 0
# ---------------------------------------------------------------------------

_SHIFT_HOURS = {1: (6, 14), 2: (6, 22), 3: (0, 24)}


def _industrial(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    shifts = p["shifts"]
    base = _num(p, "base_share", 0.0, 1.0, hi_open=True)
    if shifts == "continuous":
        on = np.ones(len(clock.hour), dtype=bool)
    else:
        try:
            start, end = _SHIFT_HOURS[int(shifts)]
        except (KeyError, TypeError, ValueError):
            raise InvalidArchetypeParamsError(
                f"shifts must be 1, 2, 3 or 'continuous', got {shifts!r}") from None
        days = clock.weekday | (clock.saturday if p["saturday_operation"] else False)
        on = days & ((clock.hour >= start) & (clock.hour < end))
    weeks = p["shutdown_weeks"]
    if not isinstance(weeks, (list, tuple)) or not all(
            isinstance(w, int) and not isinstance(w, bool) and 1 <= w <= 53
            for w in weeks):
        raise InvalidArchetypeParamsError(
            f"shutdown_weeks must be a list of ISO week numbers 1-53, got {weeks!r}")
    on &= ~np.isin(clock.week, list(weeks))
    return np.where(on, 1.0, base)


class _Opening(NamedTuple):
    opens: int
    closes: int
    saturday: float  # share of a weekday's opening on Saturday
    sunday: float    # ... and on Sunday or a holiday
    closed: float    # load while closed, as a share of the open load


_OPENING = {
    "office": _Opening(8, 18, 0.0, 0.0, 0.30),
    "retail": _Opening(9, 20, 1.0, 0.0, 0.25),
    "hospital": _Opening(7, 20, 1.0, 1.0, 0.70),
}


def _commercial(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    kind = str(p["kind"])
    if kind not in _OPENING:
        raise InvalidArchetypeParamsError(
            f"commercial kind must be one of {', '.join(_OPENING)}, got {kind!r}")
    o = _OPENING[kind]
    day = np.where(clock.weekday, 1.0, np.where(clock.saturday, o.saturday, o.sunday))
    open_ = _in_hours(clock.hour, o.opens, o.closes) * day
    closed = o.closed
    cooling = _num(p, "cooling_share", 0.0, 2.0) * np.clip(
        np.cos(_TWO_PI * (clock.doy - 200.0) / 365.0), 0.0, None)
    return closed + (1.0 - closed) * open_ * (1.0 + cooling)


def _gauss(hour: np.ndarray, centre: float, width: float) -> np.ndarray:
    d = (hour - centre + 12.0) % 24.0 - 12.0
    return np.exp(-0.5 * (d / width) ** 2)


def _residential(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    weekend = ~clock.weekday
    morning = np.where(weekend, _gauss(clock.hour, 9.0, 2.0), _gauss(clock.hour, 7.0, 1.5))
    shape = 0.35 + 0.35 * morning + 0.65 * _gauss(clock.hour, 19.0, 2.0)
    heating = str(p["heating"])
    if heating not in ("none", "heat_pump", "direct"):
        raise InvalidArchetypeParamsError(
            f"heating must be none, heat_pump or direct, got {heating!r}")
    if heating != "none":
        degrees = np.clip(_num(p, "heating_base_c", -30.0, 30.0) - _ambient_c(clock, p),
                          0.0, None)
        # Direct electric heating draws ~3x a heat pump's power per degree.
        per_degree = 0.06 if heating == "heat_pump" else 0.18
        shape = shape + per_degree * degrees
    ev = _num(p, "ev_share", 0.0, 5.0)
    if ev:
        shape = shape + ev * 2.0 * _ev_home(clock)
    return shape


def _ev_home(clock: _Clock) -> np.ndarray:
    return _gauss(clock.hour, 20.0, 2.0)


def _ev_charging(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    mode = str(p["mode"])
    if mode == "home":
        shape = 0.05 + _ev_home(clock)
    elif mode == "depot":
        shape = 0.05 + _in_hours(clock.hour, 22, 6) * 1.0
    elif mode == "public":
        shape = 0.05 + _gauss(clock.hour, 14.0, 3.5)
    else:
        raise InvalidArchetypeParamsError(
            f"mode must be home, depot or public, got {mode!r}")
    return shape * np.where(clock.weekday, 1.0, _num(p, "weekend_factor", 0.0, 5.0))


def _heat(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    degrees = np.clip(_num(p, "base_temp_c", -30.0, 30.0) - _ambient_c(clock, p),
                      0.0, None)
    peak = max(float(degrees.max()), 1e-9)
    hw = _num(p, "hot_water_share", 0.0, 1.0, hi_open=True)
    return hw + (1.0 - hw) * degrees / peak


def dc_spec(params: Mapping[str, Any], year: int):
    """The `DataCentreLoadSpec` the data-centre archetype runs: one 1 MW
    phase in service from the source year, the caller's PUE, ambient and
    AI-workload parameters over the model's own defaults."""
    import pydantic

    from models.flex_archetypes import DataCentreLoadSpec

    fields = {k: params[k] for k in _DC_SPEC_KEYS if k in params}
    raw = {"phases": [{"cod": f"{int(year)}-01-01", "it_mw": 1.0}],
           "utilisation": "ai_training", "redundancy": "N+1",
           "critical_share": 1.0, "curtailable_share": 0.0,
           "ai_workload": params.get("ai_workload") or {}, **fields}
    try:
        return DataCentreLoadSpec.model_validate(raw)
    except pydantic.ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(x) for x in err["loc"])
        raise InvalidArchetypeParamsError(f"data_centre {where}: {err['msg']}") from None


_DC_SPEC_KEYS = ("pue_at_design", "pue_temperature_slope_per_k", "pue_design_temp_c",
                 *_AMBIENT)


def _data_centre(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    from services.dc_load_profile import hourly_profile

    spec = dc_spec(p, int(idx[0].year))
    return hourly_profile(spec, idx, 1.0, timezone=p.get("timezone"))


def _library_table(profile_id: str) -> dict[tuple[int, str, int], float]:
    path = LIBRARY_DIR / f"{profile_id}.csv"
    known = sorted(f.stem for f in LIBRARY_DIR.glob("*.csv") if f.stem != "index")
    if profile_id not in known:
        raise InvalidArchetypeParamsError(
            f"no library load profile {profile_id!r}; available: {', '.join(known)}")
    with path.open(encoding="utf-8", newline="") as fh:
        return {(int(r["month"]), r["daytype"], int(r["hour"])): float(r["factor"])
                for r in csv.DictReader(fh)}


def _library(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    table = _library_table(str(p["profile_id"]))
    daytype = np.where(clock.weekday, "weekday", "weekend")
    return np.array([table[(int(m), str(d), int(h))]
                     for m, d, h in zip(clock.month, daytype, clock.hour)])


_Shape = Callable[[_Clock, Mapping[str, Any], pd.DatetimeIndex], np.ndarray]

#: archetype -> (shape, its own parameter defaults)
ARCHETYPES: dict[str, tuple[_Shape, dict[str, Any]]] = {
    "industrial": (_industrial, {"shifts": 2, "base_share": 0.25,
                                 "saturday_operation": False, "shutdown_weeks": []}),
    "commercial": (_commercial, {"kind": "office", "cooling_share": 0.15}),
    "residential": (_residential, {"heating": "none", "heating_base_c": 15.0,
                                   "ev_share": 0.0, **_AMBIENT}),
    "ev_charging": (_ev_charging, {"mode": "home", "weekend_factor": 0.8}),
    "heat": (_heat, {"base_temp_c": 15.0, "hot_water_share": 0.15, **_AMBIENT}),
    "data_centre": (_data_centre, {"pue_at_design": 1.3,
                                   "pue_temperature_slope_per_k": 0.02,
                                   "pue_design_temp_c": 20.0, "ai_workload": None,
                                   **_AMBIENT}),
    "library": (_library, {"profile_id": "commercial_office"}),
}

_COMMON = {"peak_mw": None, "annual_mwh": None, "holidays": [], "timezone": None,
           "seasonal_amplitude": 0.0, "noise": 0.0, "seed": None, "preset": None}

#: Named starting points: the study-library shapes and common variants.
PRESETS: dict[str, tuple[str, dict[str, Any]]] = {
    "commercial_office": ("library", {"profile_id": "commercial_office"}),
    "industrial_two_shift": ("library", {"profile_id": "industrial_two_shift"}),
    "industrial_one_shift": ("industrial", {"shifts": 1}),
    "industrial_three_shift": ("industrial", {"shifts": 3}),
    "continuous_process": ("industrial", {"shifts": "continuous", "base_share": 0.6}),
    "retail": ("commercial", {"kind": "retail"}),
    "hospital": ("commercial", {"kind": "hospital"}),
    "households": ("residential", {}),
    "households_heat_pumps": ("residential", {"heating": "heat_pump"}),
    "ev_depot": ("ev_charging", {"mode": "depot"}),
    "space_heat": ("heat", {}),
    "ai_training_dc": ("data_centre", {}),
}


def list_presets() -> list[dict[str, Any]]:
    return [{"name": name, "archetype": arch, "params": dict(params)}
            for name, (arch, params) in PRESETS.items()]


def resolve_params(archetype: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Defaults < preset < the caller's params; unknown keys refused."""
    if archetype not in ARCHETYPES:
        raise UnknownArchetypeError(
            f"unknown load archetype {archetype!r}; available: "
            f"{', '.join(ARCHETYPES)}")
    _, defaults = ARCHETYPES[archetype]
    allowed = {**_COMMON, **defaults}
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise InvalidArchetypeParamsError(
            f"{archetype} does not take {', '.join(unknown)}; it takes "
            f"{', '.join(sorted(allowed))}")
    preset = params.get("preset")
    preset_params: dict[str, Any] = {}
    if preset is not None:
        if preset not in PRESETS:
            raise UnknownArchetypeError(
                f"unknown load preset {preset!r}; available: {', '.join(PRESETS)}")
        p_arch, preset_params = PRESETS[preset]
        if p_arch != archetype:
            raise InvalidArchetypeParamsError(
                f"preset {preset!r} is a {p_arch} preset, not {archetype}")
    merged = {**allowed, **preset_params, **dict(params)}
    if (merged["peak_mw"] is None) == (merged["annual_mwh"] is None):
        raise InvalidArchetypeParamsError(
            "state exactly one of peak_mw or annual_mwh (the load's scale)")
    for key in ("peak_mw", "annual_mwh"):
        if merged[key] is not None and _num(merged, key, 0.0) == 0.0:
            raise InvalidArchetypeParamsError(f"{key} must be positive")
    _num(merged, "noise", 0.0, 0.5)
    _num(merged, "seasonal_amplitude", 0.0, 1.0, hi_open=True)
    seed = merged["seed"]
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise InvalidArchetypeParamsError(f"seed must be an integer, got {seed!r}")
    return merged


def generate_frame(ids: list[str], archetype: str, params: Mapping[str, Any],
                   year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """One normalised hourly column per load id over ``year``, and the meta
    the engine merges (archetype, resolved params, unit and scale)."""
    p = resolve_params(archetype, params)
    idx = pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")
    clock = _Clock(idx, p["timezone"], p["holidays"], int(year))
    shape_fn, _ = ARCHETYPES[archetype]
    raw = np.asarray(shape_fn(clock, p, idx), dtype=float)
    season = float(p["seasonal_amplitude"])
    if season:
        # Winter-peaking, as most European demand is.
        raw = raw * (1.0 + season * np.cos(_TWO_PI * (clock.doy - 15.0) / 365.0))
    if not (np.isfinite(raw).all() and (raw >= 0).all() and raw.max() > 0):
        raise InvalidArchetypeParamsError(
            f"{archetype}: these parameters give no positive load")

    # A stated peak normalises each column to peak 1; a stated annual energy
    # to a source-year mean of 1, so every load holds that energy exactly,
    # noise or not.
    by_peak = p["peak_mw"] is not None
    if float(p["noise"]) > 0 and p["seed"] is None:
        p["seed"] = zlib.crc32(f"{archetype}|{year}".encode())
    cols: dict[str, np.ndarray] = {}
    for load_id in ids:
        v = raw
        if float(p["noise"]) > 0:
            seed = (int(p["seed"]) * 1_000_003 + zlib.crc32(load_id.encode())) % (2**63)
            v = np.clip(raw * (1.0 + float(p["noise"]) * _normals(seed, len(raw))), 0.0, None)
        cols[load_id] = v / (v.max() if by_peak else v.mean())
    frame = pd.DataFrame(cols, index=idx)

    if by_peak:
        unit, scale = "per_unit_peak", float(p["peak_mw"])
    else:
        unit, scale = "per_unit_mean", float(p["annual_mwh"]) / len(idx)
    stored = {k: v for k, v in p.items() if v is not None and v != []}
    meta = {"archetype": archetype, "preset": p["preset"] or "custom",
            "params": stored, "unit": unit, "scale_mw": scale,
            **({} if by_peak else {"per_unit_mean_of": "source_year"}),
            "provenance": PROVENANCE,
            # The series is on UTC hours (apply's contract); this says how the
            # working hours were placed. `time_basis` itself is apply's to
            # record: the snapshots' basis it mapped with.
            "source_time_basis": (f"UTC (from {p['timezone']} wall clock)"
                                  if p["timezone"]
                                  else "UTC (assumed; site clock unknown)")}
    if archetype == "library":
        meta["library_profile"] = str(p["profile_id"])
    return frame, meta
