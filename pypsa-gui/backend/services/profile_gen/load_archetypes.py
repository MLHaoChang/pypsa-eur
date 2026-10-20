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

Common parameters: exactly one of ``peak_mw`` / ``annual_mwh`` (the scale);
``holidays`` (month-day strings, run like a Sunday); ``timezone`` (IANA zone
whose wall clock the working hours are on — the series stays on UTC
snapshots, the `resolve_time_basis` rule); ``noise`` (multiplicative,
seeded); ``seed``; ``preset``. A key an archetype does not know is refused,
so a typo is never silently ignored.

Temperature: a closed-form assumed ambient (annual mean, a seasonal cosine
peaking on day 200, a diurnal cosine peaking at 15:00 local) — the same form
as the DC model's, not weather data.

Country holiday calendars are not built in (no holiday library is a
dependency); ``holidays`` takes the dates.
"""
from __future__ import annotations

import csv
import math
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.profile_gen.engine import ProfileGenError
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
                 holidays: list[str]):
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
        self.week = local.isocalendar().week.to_numpy().astype(int)
        md = local.strftime("%m-%d")
        dow = local.dayofweek.to_numpy()
        self.holiday = np.isin(np.asarray(md), [_month_day(h) for h in holidays])
        self.saturday = (dow == 5) & ~self.holiday
        self.sunday = (dow == 6) | self.holiday
        self.weekday = ~(self.saturday | self.sunday)


def _month_day(raw: Any) -> str:
    text = str(raw).strip()
    if len(text) == 10:  # YYYY-MM-DD
        text = text[5:]
    try:
        pd.Timestamp(f"2024-{text}")  # a leap year accepts 02-29
    except ValueError:
        raise InvalidArchetypeParamsError(
            f"holiday {raw!r} is not MM-DD or YYYY-MM-DD") from None
    return text


def _ambient_c(clock: _Clock, p: Mapping[str, Any]) -> np.ndarray:
    return (float(p["ambient_annual_mean_c"])
            + float(p["ambient_seasonal_amplitude_k"])
            * np.cos(_TWO_PI * (clock.doy - 200.0) / 365.0)
            + float(p["ambient_diurnal_amplitude_k"])
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
    base = float(p["base_share"])
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
    on &= ~np.isin(clock.week, [int(w) for w in p["shutdown_weeks"]])
    return np.where(on, 1.0, base)


_OPENING = {  # kind -> (open, close, saturday factor, sunday factor, closed share)
    "office": (8, 18, 0.0, 0.0, 0.30),
    "retail": (9, 20, 1.0, 0.0, 0.25),
    "hospital": (7, 20, 1.0, 1.0, 0.70),
}


def _commercial(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    kind = str(p["kind"])
    if kind not in _OPENING:
        raise InvalidArchetypeParamsError(
            f"commercial kind must be one of {', '.join(_OPENING)}, got {kind!r}")
    start, end, sat, sun, closed = _OPENING[kind]
    day = np.where(clock.weekday, 1.0, np.where(clock.saturday, sat, sun))
    open_ = _in_hours(clock.hour, start, end) * day
    cooling = float(p["cooling_share"]) * np.clip(
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
        degrees = np.clip(float(p["heating_base_c"]) - _ambient_c(clock, p), 0.0, None)
        # Direct electric heating draws ~3x a heat pump's power per degree.
        per_degree = 0.06 if heating == "heat_pump" else 0.18
        shape = shape + per_degree * degrees
    ev = float(p["ev_share"])
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
    return shape * np.where(clock.weekday, 1.0, float(p["weekend_factor"]))


def _heat(clock: _Clock, p: Mapping[str, Any], idx) -> np.ndarray:
    degrees = np.clip(float(p["base_temp_c"]) - _ambient_c(clock, p), 0.0, None)
    peak = max(float(degrees.max()), 1e-9)
    hw = float(p["hot_water_share"])
    if not 0.0 <= hw < 1.0:
        raise InvalidArchetypeParamsError(
            f"hot_water_share must be in [0, 1), got {hw!r}")
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
           "noise": 0.0, "seed": None, "preset": None}

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
        if merged[key] is not None and not (float(merged[key]) > 0):
            raise InvalidArchetypeParamsError(f"{key} must be positive")
    if not 0.0 <= float(merged["noise"]) <= 0.5:
        raise InvalidArchetypeParamsError("noise must be in [0, 0.5]")
    return merged


def generate_frame(ids: list[str], archetype: str, params: Mapping[str, Any],
                   year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """One peak-normalised hourly column per load id over ``year``, and the
    meta the engine merges (archetype, resolved params, unit and scale)."""
    p = resolve_params(archetype, params)
    idx = pd.date_range(f"{year}-01-01T00:00", f"{year}-12-31T23:00", freq="h")
    clock = _Clock(idx, p["timezone"], list(p["holidays"]))
    shape_fn, _ = ARCHETYPES[archetype]
    raw = np.asarray(shape_fn(clock, p, idx), dtype=float)
    if not (np.isfinite(raw).all() and (raw >= 0).all() and raw.max() > 0):
        raise InvalidArchetypeParamsError(
            f"{archetype}: these parameters give no positive load")

    cols: dict[str, np.ndarray] = {}
    for load_id in ids:
        v = raw
        if float(p["noise"]) > 0:
            base = p["seed"]
            if base is None:
                base = zlib.crc32(f"{archetype}|{year}".encode())
            seed = (int(base) * 1_000_003 + zlib.crc32(load_id.encode())) % (2**63)
            v = np.clip(raw * (1.0 + float(p["noise"]) * _normals(seed, len(raw))), 0.0, None)
        cols[load_id] = v / v.max()
    frame = pd.DataFrame(cols, index=idx)

    first = frame.iloc[:, 0].to_numpy()
    scale = (float(p["peak_mw"]) if p["peak_mw"] is not None
             else float(p["annual_mwh"]) / float(first.sum()))
    stored = {k: v for k, v in p.items() if v is not None and v != []}
    meta = {"archetype": archetype, "preset": p["preset"] or "custom",
            "params": stored, "unit": "per_unit_peak", "scale_mw": scale,
            "provenance": PROVENANCE,
            "time_basis": (f"UTC snapshots, mapped on the {p['timezone']} clock"
                           if p["timezone"] else "site clock (assumed)")}
    if archetype == "library":
        meta["library_profile"] = str(p["profile_id"])
    return frame, meta
