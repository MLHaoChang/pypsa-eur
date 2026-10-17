"""Deterministic synthetic capacity-factor source — no I/O, no network.

This is the `default` preset's source: always available, clearly labelled
`source: synthetic` so it can never masquerade as weather-derived (spec D5).

Solar model (closed form, documented):
    cf(t) = bell(t) x peak(doy) x cloud(day), exactly 0.0 outside daylight.
  - bell(t): sunrise-equation daylight window. Declination
    d(doy) = 23.45 deg x sin(2pi (284 + doy) / 365); daylight where the hour
    angle |w(t)| < w0 with cos w0 = -tan(lat) tan(dec) (clamped for polar
    day/night); inside the window a sin(pi x fraction) clear-sky bell centred
    on solar noon (noon_utc = 12 - lon/15). The representative instant of an
    hourly slot is its midpoint (hh:30).
  - peak(doy): seasonal envelope of the noon peak, clip(cos(lat - dec), 0, 1).
  - cloud(day): seeded per-day uniform in [0.5, 1.0].

Wind model:
    cf(t) = clip(mean(doy) + x(t), 0, 1)
  - mean(doy) = 0.32 + 0.10 cos(2pi (doy - 15 - shift) / 365.25), peaking in
    the *local* winter: shift = 0 in the northern hemisphere, 182 in the
    southern.
  - x(t): seeded AR(1), x_t = 0.97 x_{t-1} + 0.05 e_t, e ~ N(0, 1),
    x_0 drawn from the stationary distribution.

Determinism: every random number comes from a seeded PCG64 raw-integer
stream — np.random.default_rng(seed).integers, whose stream is stable across
numpy versions — mapped to uniforms as (raw >> 11) * 2**-53 (+0.5 ulp so
log() never sees 0) and to normals via Box-Muller. No numpy distribution API
is involved. The default seed is a CRC32 of (technology, year); each point's
coordinates are mixed in so two sites never share one noise stream.
"""
from __future__ import annotations

import calendar
import math
import zlib
from typing import Any, Mapping, TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # avoid a runtime cycle: engine imports this module
    from services.profile_gen.engine import ProfilePoint

_TWO_PI = 2.0 * math.pi


# ---------------------------------------------------------------------------
# Stable seeded randomness
# ---------------------------------------------------------------------------


def _seed_for(technology: str, year: int, point: "ProfilePoint",
              params: Mapping[str, Any]) -> int:
    base = params.get("seed")
    if base is None:
        base = zlib.crc32(f"{technology}|{year}".encode("utf-8"))
    coord = zlib.crc32(
        f"{point.id}|{point.lon:.4f}|{point.lat:.4f}".encode("utf-8")
    )
    return (int(base) * 1_000_003 + coord) % (2**63)


def _uniforms(seed: int, n: int) -> np.ndarray:
    """n uniforms in (0, 1) from a raw PCG64 stream — stable across numpy."""
    raw = np.random.default_rng(seed).integers(
        0, 2**64, size=n, dtype=np.uint64
    )
    return ((raw >> 11).astype(np.float64) + 0.5) * 2.0**-53


def _normals(seed: int, n: int) -> np.ndarray:
    """n standard normals via Box-Muller over the stable uniform stream."""
    m = (n + 1) // 2
    u = _uniforms(seed, 2 * m)
    u1, u2 = u[:m], u[m:]
    r = np.sqrt(-2.0 * np.log(u1))
    out = np.concatenate([r * np.cos(_TWO_PI * u2), r * np.sin(_TWO_PI * u2)])
    return out[:n]


# ---------------------------------------------------------------------------
# Solar
# ---------------------------------------------------------------------------


def _solar_point(point: "ProfilePoint", idx: pd.DatetimeIndex,
                 seed: int) -> np.ndarray:
    lat = math.radians(point.lat)
    doy = idx.dayofyear.to_numpy().astype(np.float64)
    t = idx.hour.to_numpy().astype(np.float64) + 0.5  # slot midpoint

    decl = math.radians(23.45) * np.sin(_TWO_PI * (284.0 + doy) / 365.0)
    cos_w0 = np.clip(-math.tan(lat) * np.tan(decl), -1.0, 1.0)
    w0 = np.arccos(cos_w0)  # daylight half-angle, rad
    noon_utc = 12.0 - point.lon / 15.0
    sunrise = noon_utc - np.degrees(w0) / 15.0
    daylen = 2.0 * np.degrees(w0) / 15.0

    omega = math.radians(15.0) * (t - noon_utc)
    daylight = np.abs(omega) < w0
    frac = np.clip((t - sunrise) / np.maximum(daylen, 1e-9), 0.0, 1.0)
    # sin(pi * frac) is ~0 at the window EDGES but not beyond (frac clamps to
    # exactly 1.0 on the sunset side, and sin(pi) != 0.0) — so the mask below
    # is the ONLY source of an exact night zero. Mutation target.
    bell = np.sin(math.pi * frac)

    peak = np.clip(np.cos(lat - decl), 0.0, 1.0)  # seasonal noon-peak envelope

    ndays = len(idx) // 24
    cloud_daily = 0.5 + 0.5 * _uniforms(seed ^ 0x5EED, ndays)
    cloud = cloud_daily[(doy - 1).astype(int)]

    cf = bell * peak * cloud
    cf[~daylight] = 0.0  # exact night zero — mutation target
    return np.clip(cf, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Wind
# ---------------------------------------------------------------------------


def _wind_point(point: "ProfilePoint", idx: pd.DatetimeIndex,
                seed: int) -> np.ndarray:
    doy = idx.dayofyear.to_numpy().astype(np.float64)
    shift = 0.0 if point.lat >= 0 else 182.0  # winter-peaking, local hemisphere
    mean = 0.32 + 0.10 * np.cos(_TWO_PI * (doy - 15.0 - shift) / 365.25)

    n = len(idx)
    eps = _normals(seed, n)
    phi, sigma = 0.97, 0.05
    x = np.empty(n, dtype=np.float64)
    x[0] = eps[0] * sigma / math.sqrt(1.0 - phi * phi)  # stationary start
    for t in range(1, n):
        x[t] = phi * x[t - 1] + sigma * eps[t]

    return np.clip(mean + x, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Source entry point (registered in engine._SOURCES)
# ---------------------------------------------------------------------------


def generate_frame(points: "list[ProfilePoint]", technology: str,
                   params: Mapping[str, Any], year: int) -> pd.DataFrame:
    """Hourly per-unit profiles for every point, one column per point id.

    Always spans the full weather year: 8760 h, or 8784 h with Feb 29 kept
    for leap years (the apply path owns any dropping — spec §4).
    """
    hours = 8784 if calendar.isleap(year) else 8760
    idx = pd.date_range(f"{year}-01-01 00:00", periods=hours, freq="h")
    gen = _solar_point if technology == "solar" else _wind_point
    data = {
        p.id: gen(p, idx, _seed_for(technology, year, p, params))
        for p in points
    }
    return pd.DataFrame(data, index=idx)
