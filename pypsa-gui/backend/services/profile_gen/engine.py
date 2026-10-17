"""Engine interface for the renewable profile generator.

Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md
Plan: docs/superpowers/plans/2026-10-17-renewable-profile-generator.md

One entry point — `generate_profile` — over a source registry. `synthetic` is
eagerly registered; `atlite` resolves lazily on first use (the import cage).
Adding a source is registering a callable, never editing call sites.

Determinism contract: for a fixed `now`, identical inputs produce a
byte-identical `ProfileResult.series` (and identical meta). The synthetic
source achieves this by deriving every random number from a seeded
PCG64 raw-integer stream (see synthetic.py).
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from services.profile_gen import synthetic

# ---------------------------------------------------------------------------
# Typed errors — every refusal at the engine boundary is one of these, so the
# router/chat layers can map them to 422s and readable chat messages.
# ---------------------------------------------------------------------------


class ProfileGenError(Exception):
    """Base class for all typed profile-generation refusals."""


class UnsupportedTechnologyError(ProfileGenError):
    """Carrier/technology is not one the engine can generate (wind, solar)."""


class UnknownSourceError(ProfileGenError):
    """Requested source is not registered in the engine."""


class InvalidCoordinatesError(ProfileGenError):
    """A point is malformed, duplicated, or outside lon/lat ranges."""


SUPPORTED_TECHNOLOGIES = ("wind", "solar")


# ---------------------------------------------------------------------------
# Public data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfilePoint:
    """One generation site: a stable id plus WGS-84 coordinates.

    Bus `x`/`y` in the backend are geographic longitude/latitude in degrees
    (asset_schema FieldSpec "x": "Geographic longitude, east positive"), so
    points are plain lon/lat — no reprojection anywhere in the pipeline.
    """

    id: str
    lon: float
    lat: float


@dataclass(frozen=True)
class ProfileResult:
    """`series`: one column per point id, hourly tz-naive UTC index spanning
    the full weather year. `meta`: the spec-D6 record (see generate_profile).
    """

    series: pd.DataFrame
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# Source registry. Sources return (frame, meta_extra): the frame is one column
# per point id on the source's time axis; meta_extra is merged into the
# spec-D6 meta (e.g. the atlite source records atlite_version there).
# ---------------------------------------------------------------------------

_SourceFn = Callable[
    [list[ProfilePoint], str, Mapping[str, Any], int],
    "tuple[pd.DataFrame, dict[str, Any]]",
]

_SOURCES: dict[str, _SourceFn] = {
    "synthetic": synthetic.generate_frame,
}

# Sources whose module must not be imported at engine import time (the
# profile_gen import cage: atlite is only imported lazily inside
# atlite_source functions). Resolved on first use.
_LAZY_SOURCES: dict[str, tuple[str, str]] = {
    "atlite": ("services.profile_gen.atlite_source", "generate_frame"),
}


def register_source(name: str, fn: _SourceFn) -> None:
    """Register an additional generation source."""
    _SOURCES[name] = fn


def _resolve_source(source: str) -> _SourceFn:
    if source in _SOURCES:
        return _SOURCES[source]
    if source in _LAZY_SOURCES:
        import importlib

        mod_name, fn_name = _LAZY_SOURCES[source]
        fn = getattr(importlib.import_module(mod_name), fn_name)
        _SOURCES[source] = fn
        return fn
    raise UnknownSourceError(
        f"unknown profile source {source!r}; available: "
        f"{', '.join(sorted([*_SOURCES, *_LAZY_SOURCES]))}"
    )


# ---------------------------------------------------------------------------
# Point normalisation / validation
# ---------------------------------------------------------------------------


def _as_point(raw: Any) -> ProfilePoint:
    if isinstance(raw, ProfilePoint):
        return raw
    if isinstance(raw, Mapping):
        try:
            return ProfilePoint(
                id=str(raw["id"]), lon=float(raw["lon"]), lat=float(raw["lat"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidCoordinatesError(
                f"malformed point {raw!r}: expected keys id/lon/lat ({exc})"
            ) from exc
    try:
        pid, lon, lat = raw  # (id, lon, lat) tuple/list
        return ProfilePoint(id=str(pid), lon=float(lon), lat=float(lat))
    except (TypeError, ValueError) as exc:
        raise InvalidCoordinatesError(
            f"malformed point {raw!r}: expected (id, lon, lat)"
        ) from exc


def _normalize_points(points: Iterable[Any]) -> list[ProfilePoint]:
    pts = [_as_point(p) for p in points]
    if not pts:
        raise ProfileGenError("at least one point is required")
    ids = [p.id for p in pts]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise InvalidCoordinatesError(
            f"point ids must be unique; duplicated: {', '.join(dupes)}"
        )
    for p in pts:
        if not (-180.0 <= p.lon <= 180.0 and -90.0 <= p.lat <= 90.0):
            raise InvalidCoordinatesError(
                f"point {p.id!r}: lon/lat ({p.lon}, {p.lat}) outside "
                "[-180, 180] x [-90, 90] — bus x/y are WGS-84 degrees"
            )
    return pts


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_profile(
    points: Iterable[Any],
    technology: str,
    params: Mapping[str, Any] | None = None,
    source: str = "synthetic",
    year: int | None = None,
    now: _dt.datetime | str | None = None,
) -> ProfileResult:
    """Generate hourly per-unit capacity-factor profiles for `points`.

    - `technology`: "wind" or "solar" (spec §4 carrier guard, typed refusal).
    - `source`: a registered source name; "synthetic" is always available and
      never performs I/O. Unknown names are a typed refusal listing what is
      available.
    - `year`: the weather year; defaults to the last completed calendar year.
      Leap years yield 8784 hours with Feb 29 kept (meta `leap_day: kept`;
      dropping is the apply path's job).
    - `now`: injectable clock so tests (and reproducible runs) get
      byte-identical meta.
    """
    params = dict(params or {})
    tech = str(technology).strip().lower()
    if tech not in SUPPORTED_TECHNOLOGIES:
        raise UnsupportedTechnologyError(
            f"cannot generate a profile for technology {technology!r}; "
            f"supported: {', '.join(SUPPORTED_TECHNOLOGIES)}"
        )
    source_fn = _resolve_source(source)
    pts = _normalize_points(points)
    if year is None:
        year = _dt.datetime.now(_dt.timezone.utc).year - 1
    year = int(year)

    frame, meta_extra = source_fn(pts, tech, params, year)

    if now is None:
        generated_at = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(now, str):
        generated_at = _dt.datetime.fromisoformat(now)
    else:
        generated_at = now

    meta: dict[str, Any] = {
        "source": source,
        "technology": tech,
        "preset": str(params.get("preset", "default")),
        "params": params,
        "weather_year": year,
        "bbox": [
            min(p.lon for p in pts),
            min(p.lat for p in pts),
            max(p.lon for p in pts),
            max(p.lat for p in pts),
        ],
        "leap_day": "kept",  # Feb 29 is dropped at apply time, never here
        "generated_at": generated_at.isoformat(),
        "atlite_version": None,  # the atlite source overwrites this via extra
    }
    meta.update(meta_extra)
    return ProfileResult(series=frame, meta=meta)
