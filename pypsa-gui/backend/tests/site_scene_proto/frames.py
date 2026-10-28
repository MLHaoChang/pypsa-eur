"""
V05/V06: the metric frames every native scene record uses.

Frozen frame: a Site's local frame is east/north/up (ENU) in metres about the
Site's `origin` {lng, lat}, fixed when the Site is created. Horizontal
projection is `site_service._to_local`/`_from_local` (equirectangular at the
origin's latitude), the one the existing Site 3D already uses, so no second
projection exists. Heights are metres above the Site's ground plane.
Headings are degrees clockwise from north (the existing `placements[].heading`).
glTF exchange (SC-04) maps ENU (e, n, u) to Y-up (x, y, z) = (e, u, -n).
"""
from __future__ import annotations

import math

from services.site_service import _from_local, _to_local
from tests.site_scene_proto.contract import SceneError

# Literal length units accepted at an input seam, with their exact factor to
# metres. An input with no unit, or any other unit, is refused: never guessed.
UNIT_TO_M: dict[str, float] = {"m": 1.0, "mm": 0.001, "ft": 0.3048}


def to_metres(value: object, unit: object) -> float:
    if unit not in UNIT_TO_M:
        raise SceneError("invalid_input", f"unit {unit!r} is not one of m, mm, ft", {"field": "unit"})
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SceneError("invalid_input", "length must be a finite number", {"field": "value"})
    return float(value) * UNIT_TO_M[unit]


def enu_to_gltf(p: tuple[float, float, float]) -> tuple[float, float, float]:
    e, n, u = p
    return (e, u, -n)


def gltf_to_enu(p: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = p
    return (x, -z, y)


def lnglat_to_local(origin: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
    return _to_local(origin, p)


def local_to_lnglat(origin: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
    return _from_local(origin, p)


def rectangle(center: tuple[float, float], width_m: float, depth_m: float, heading_deg: float) -> list[tuple[float, float]]:
    """
    Footprint of a `width_m` (along the heading's right) × `depth_m` (along the
    heading) rectangle about `center`, counter-clockwise, in local metres.
    Same width/depth meaning as #712's `asset_footprints`.
    """
    h = math.radians(heading_deg)
    fwd = (math.sin(h), math.cos(h))   # heading: clockwise from north
    right = (math.cos(h), -math.sin(h))
    hw, hd = width_m / 2, depth_m / 2
    cx, cy = center
    corners = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
    return [(cx + a * right[0] + b * fwd[0], cy + a * right[1] + b * fwd[1]) for a, b in corners]


def measure_rectangle(footprint: list[tuple[float, float]]) -> tuple[float, float]:
    """(width, depth): the lengths of the first two edges, measured, not stored."""
    (x0, y0), (x1, y1), (x2, y2) = footprint[0], footprint[1], footprint[2]
    return math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)


def polygon_area(footprint: list[tuple[float, float]]) -> float:
    """Signed shoelace area; positive for counter-clockwise."""
    s = 0.0
    for (x0, y0), (x1, y1) in zip(footprint, footprint[1:] + footprint[:1]):
        s += x0 * y1 - x1 * y0
    return s / 2
