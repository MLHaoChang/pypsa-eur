"""atlite-backed profile source (profile_gen Task 3).

Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md
§3.1. Registered with the engine as the "atlite" source (lazily — see
engine._LAZY_SOURCES).

CAGE RULE: `import atlite` happens inside the function body ONLY, and this is
the ONLY module in profile_gen allowed to import it. The cage test in
tests/test_profile_gen_atlite_source.py enforces both directions.

Time-series spelling for atlite 0.6.1 (verified against the tagged source):
`aggregate_time=None` with no layout/matrix/shapes returns the per-cell
hourly capacity factor with dims (time, y, x). `capacity_factor=True` is
deprecated sugar for `aggregate_time="mean"` — a time-averaged SCALAR, never
what we want.

Turbine/panel configs arrive as explicit dicts from the presets (offline by
construction): wind needs V/POW/hub_height/P, pv the full huld parameter set.
"""
from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

import numpy as np
import pandas as pd

from services.profile_gen.engine import (
    AtliteNotInstalledError,
    InvalidCoordinatesError,
    ProfileGenError,
)

if TYPE_CHECKING:
    from services.profile_gen.engine import ProfilePoint

_REQUIRED_PARAMS = {
    "wind": ("cutout_path", "turbine"),
    "solar": ("cutout_path", "panel", "orientation"),
}


def _require(params: Mapping[str, Any], technology: str) -> None:
    missing = [k for k in _REQUIRED_PARAMS[technology] if k not in params]
    if missing:
        raise ProfileGenError(
            f"atlite {technology} generation requires params "
            f"{', '.join(repr(m) for m in missing)} "
            f"(got: {', '.join(sorted(params)) or 'none'})"
        )


def generate_frame(points: "list[ProfilePoint]", technology: str,
                   params: Mapping[str, Any], year: int
                   ) -> "tuple[pd.DataFrame, dict[str, Any]]":
    """Per-point hourly CF from a prepared cutout via nearest-cell selection.

    Returns (frame, meta_extra); the frame's index is the cutout's own time
    axis (its length contract is the cutout's, not necessarily 8760).
    """
    _require(params, technology)
    cutout_path = params["cutout_path"]

    try:
        import atlite  # noqa: F401 — the cage: lazy, and only in this module
    except ImportError as exc:
        raise AtliteNotInstalledError(
            "the atlite source needs the optional 'atlite' package, which is "
            "not installed in this environment — use the synthetic source or "
            "install atlite"
        ) from exc

    cutout = atlite.Cutout(path=str(cutout_path))
    times = pd.DatetimeIndex(cutout.data.time.values)

    # The cutout's weather must live inside the requested weather year.
    if not ((times >= pd.Timestamp(year, 1, 1)).all()
            and (times < pd.Timestamp(year + 1, 1, 1)).all()):
        raise ProfileGenError(
            f"cutout time axis ({times.min()} .. {times.max()}) does not lie "
            f"inside weather year {year} — regenerate the cutout or pass the "
            "matching year"
        )

    if technology == "wind":
        da = cutout.wind(
            turbine=params["turbine"],
            aggregate_time=None,
            interpolation_method=params.get("interpolation_method", "logarithmic"),
        )
    else:
        da = cutout.pv(
            panel=params["panel"],
            orientation=params["orientation"],
            tracking=params.get("tracking"),
            clearsky_model=params.get("clearsky_model", "simple"),
            aggregate_time=None,
        )

    xs = np.asarray(cutout.data.x.values, dtype=float)
    ys = np.asarray(cutout.data.y.values, dtype=float)
    dx = float(np.abs(np.diff(xs)).min()) if len(xs) > 1 else 0.25
    dy = float(np.abs(np.diff(ys)).min()) if len(ys) > 1 else 0.25

    cols: dict[str, np.ndarray] = {}
    for p in points:
        if not (xs.min() - dx / 2 - 1e-9 <= p.lon <= xs.max() + dx / 2 + 1e-9
                and ys.min() - dy / 2 - 1e-9 <= p.lat <= ys.max() + dy / 2 + 1e-9):
            raise InvalidCoordinatesError(
                f"point {p.id!r} ({p.lon}, {p.lat}) lies outside the cutout "
                f"(x {xs.min()}..{xs.max()}, y {ys.min()}..{ys.max()}) — "
                "regenerate the cutout with a bbox covering the asset"
            )
        # Nearest grid cell (spec §3.1: ~25-30 km, energy profiles only).
        cell = da.sel(y=p.lat, x=p.lon, method="nearest")
        cols[p.id] = np.asarray(cell.values, dtype=float)

    frame = pd.DataFrame(cols, index=times)
    return frame, {"atlite_version": atlite.__version__}
