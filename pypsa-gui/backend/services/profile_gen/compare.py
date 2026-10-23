"""Read-only profile resolution and comparison on the model's snapshot clock."""

from __future__ import annotations

import calendar
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from services.profile_gen.apply import ProfileApplyError, prepare_profile
from services.profile_gen.current import CurrentSeriesError
from services.profile_gen.engine import ProfileGenError
from services.profile_gen.shape import profile_stats


class InvalidCompareSideError(ProfileGenError):
    """A side must name a current generator or owned pending/shaped artifact."""


class NotAShapedProfileError(ProfileGenError):
    """A shaped side must carry an explicit calibration receipt."""


def _parse_side(side):
    parts = side.split(":") if isinstance(side, str) else []
    if (
        len(parts) not in (2, 3)
        or not all(parts)
        or parts[0] not in ("current", "pending", "shaped")
        or (parts[0] == "current" and len(parts) != 2)
    ):
        raise InvalidCompareSideError(
            f"{side!r}: use current:<generator>, pending:<job>[:<point>] "
            "or shaped:<job>[:<point>]"
        )
    return parts[0], parts[1], parts[2] if len(parts) == 3 else None


def resolve_sides(ctx, proj_dir, a, b, component=None):
    """A current side supplies the default target for its pending counterpart."""
    parsed = [_parse_side(a), _parse_side(b)]
    if component is None:
        component = next((name for kind, name, _ in parsed if kind == "current"), None)
    return (
        side_series(ctx, proj_dir, a, component),
        side_series(ctx, proj_dir, b, component),
    )


def side_series(ctx, proj_dir, side, component):
    """Resolve one side without writes; pending alignment is Apply's pure half.

    The route has already authorized proj_dir. The directory-only holder below
    lets the existing router artifact readers enforce their exact path/refusal
    contract without resolving a project again or loading a background context.
    """
    kind, name, point = _parse_side(side)
    if kind == "current":
        s = ctx.user_ts.get(("generators", "p_max_pu", name))
        if s is None and name in ctx.network.generators_t.p_max_pu:
            s = ctx.network.generators_t.p_max_pu[name]
        if s is None:
            raise CurrentSeriesError(f"{name!r} has no current p_max_pu series")
        aligned = s.reindex(ctx.network.snapshots).copy()
        if not np.isfinite(aligned.to_numpy(dtype=float)).all():
            raise CurrentSeriesError(
                f"{name!r} has no complete finite series on the model snapshots"
            )
        return aligned

    from routers.profiles import _pending_artifact, _read_point_series

    nc, meta = _pending_artifact(SimpleNamespace(directory=Path(proj_dir)), name)
    if kind == "shaped" and meta.get("calibrated") is not True:
        raise NotAShapedProfileError(f"{name!r} is not a calibrated shaped profile")
    if component is None:
        points = meta.get("points", [])
        component = point or (points[0] if len(points) == 1 else None)
    point = point or component
    if not component or not point:
        raise InvalidCompareSideError(
            "Choose a component or point for this multi-point profile"
        )
    s = _read_point_series(nc, name, point, component)
    return prepare_profile(ctx, component, s, meta).aligned


def _stamps(index):
    return pd.DatetimeIndex(
        index.get_level_values(-1) if isinstance(index, pd.MultiIndex) else index
    )


def _stats(series, weights):
    stamps = _stamps(series.index)
    indexed = pd.Series(series.to_numpy(dtype=float), index=stamps)
    stats = profile_stats(indexed)
    stats["cf"] = float(np.average(indexed.to_numpy(), weights=weights))
    stats["flh"] = stats["cf"] * (8784 if calendar.isleap(stamps[0].year) else 8760)
    stats = {k: round(v, 4) if v is not None else None for k, v in stats.items()}
    stats["monthly"] = {
        str(m): round(float(value), 4)
        for m, value in indexed.groupby(stamps.month).mean().items()
    }
    descending = np.sort(indexed.to_numpy())[::-1]
    stats["duration"] = [
        round(float(v), 4)
        for v in descending[np.linspace(0, len(descending) - 1, 100).astype(int)]
    ]
    return stats


def compare(a: pd.Series, b: pd.Series, weights: pd.Series) -> dict:
    """Compute only from aligned native series and generator snapshot weights."""
    if a.empty or not a.index.equals(b.index):
        raise ProfileApplyError(
            "Both profiles must share the nonempty model snapshot clock"
        )
    w = weights.reindex(a.index).to_numpy(dtype=float)
    if (
        not np.isfinite(w).all()
        or (w < 0).any()
        or w.sum() <= 0
        or not np.isfinite(a.to_numpy(dtype=float)).all()
        or not np.isfinite(b.to_numpy(dtype=float)).all()
    ):
        raise ProfileApplyError(
            "Comparison needs finite profiles and nonnegative snapshot weights with a positive sum"
        )
    left, right = _stats(a, w), _stats(b, w)
    delta = {
        key: round(right[key] - left[key], 4)
        if right[key] is not None and left[key] is not None
        else None
        for key in ("cf", "flh", "p10", "p50", "p90", "peak", "peak_hour")
    }
    delta["monthly"] = {
        month: round(right["monthly"][month] - value, 4)
        for month, value in left["monthly"].items()
    }
    stamps = _stamps(b.index)
    peak = int(np.argmax(b.to_numpy()))
    start = stamps[peak].normalize() - pd.Timedelta(days=stamps[peak].dayofweek)
    mask = (stamps >= start) & (stamps < start + pd.Timedelta(days=7))
    week = {}
    if isinstance(b.index, pd.MultiIndex):
        period = b.index.get_level_values(0)[peak]
        mask &= b.index.get_level_values(0) == period
        week["period"] = period.item() if isinstance(period, np.generic) else period
    week.update(
        snapshots=[stamp.isoformat() for stamp in stamps[mask]],
        a=[round(float(v), 4) for v in a.to_numpy()[mask]],
        b=[round(float(v), 4) for v in b.to_numpy()[mask]],
    )
    return {"a": left, "b": right, "delta": delta, "week": week}
