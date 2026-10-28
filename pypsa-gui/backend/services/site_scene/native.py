"""
D04/D12, V05/V07/V08/V49/V53: model-free native generation and its limits.

Generation reads only the Site envelope and the submitted inputs: no network,
no Bus, no solve, no worker. It runs bounded IN the request (D04): it is pure
arithmetic over at most `MAX_BUILDINGS` prisms, measured in
sc-00-n/evidence/d12-native-budgets.json, so it needs no queue adapter and adds
no job kind.

Building record (frozen):
    {id, source: {namespace, source_id}, footprint: [[e, n], ...] (local metres,
     counter-clockwise), height_m, height_evidence, levels}
`id` is `<namespace>:<source_id>`, so equal numeric ids from different source
namespaces (OSM way 7 vs node 7, two uploads) stay distinct buildings.
`height_evidence` is one of tagged | levels | fallback | greenfield and is
stored, so it survives reload.

Equipment proxies reuse #712's `asset_footprints` (width_m/depth_m/optional
height_m) and the Site's `placements` {x, y, heading}: no second footprint
store. A missing height_m is an unavailable quantity, not a guessed one.
"""
from __future__ import annotations

import json
import math
from typing import Any

from services.site_scene import frames
from services.site_scene.contract import SceneError, quantity

MAX_BUILDINGS = 2000
MAX_FOOTPRINT_VERTICES = 256
MAX_TRIANGLES = 200_000
MAX_PREVIEW_BYTES = 4 * 1024 * 1024  # = site_service.MAX_SITES_BYTES
# The self-intersection check is O(n^2) per footprint: sum n(n-3)/2 segment
# pairs over a request. Measured ~0.9 us/pair (d12-native-budgets.json), so
# this keeps the worst admitted request near a quarter of a second.
MAX_SEGMENT_PAIRS = 250_000
MAX_HEIGHT_M = 1000.0
LEVEL_HEIGHT_M = 3.0
FALLBACK_HEIGHT_M = 6.0
NAMESPACES = ("osm", "greenfield", "manual", "layout")
EVIDENCE = ("tagged", "levels", "fallback", "greenfield")


def prism_triangles(n_vertices: int) -> int:
    """Roof and floor fans (n-2 each) plus two per wall: 4n - 4."""
    return 4 * n_vertices - 4


def segment_pairs(n_vertices: int) -> int:
    """Non-adjacent edge pairs the crossing check compares: n(n-3)/2."""
    return max(0, n_vertices * (n_vertices - 3) // 2)


_LIMIT_WORDS = {"buildings": "buildings", "footprint_vertices": "footprint corners",
                "segment_pairs": "edge pairs to check", "triangles": "triangles"}


def size_text(n: int) -> str:
    """A byte count in kB or MB, for messages."""
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(n / 1000, 0.1):.1f} kB"


def _limit(name: str, limit: int, actual: int) -> SceneError:
    if name == "preview_bytes":
        text = f"Too large: the preview is {size_text(actual)}, the limit is {size_text(limit)}"
    else:
        text = f"Too large: {actual} {_LIMIT_WORDS[name]}, the limit is {limit}"
    return SceneError("resource_limit", text, {"limit": name, "max": limit, "actual": actual})


def _segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def check_footprint(ring: list[tuple[float, float]], where: str) -> list[tuple[float, float]]:
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        raise SceneError("invalid_geometry", f"{where}: a footprint needs at least three vertices")
    if len(ring) > MAX_FOOTPRINT_VERTICES:
        raise _limit("footprint_vertices", MAX_FOOTPRINT_VERTICES, len(ring))
    if not all(len(p) == 2 and all(isinstance(c, (int, float)) and math.isfinite(c) for c in p) for p in ring):
        raise SceneError("invalid_geometry", f"{where}: vertices must be finite [x, y] pairs")
    n = len(ring)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_cross(ring[i], ring[(i + 1) % n], ring[j], ring[(j + 1) % n]):
                raise SceneError("invalid_geometry", f"{where}: the footprint crosses itself")
    area = frames.polygon_area(ring)
    if abs(area) < 1e-6:
        raise SceneError("invalid_geometry", f"{where}: the footprint has no area")
    return ring if area > 0 else ring[::-1]


def _height(value: float, where: str) -> float:
    if not (0 < value <= MAX_HEIGHT_M):
        raise SceneError("invalid_geometry", f"{where}: height must be above 0 m and at most {MAX_HEIGHT_M:g} m")
    return value


def greenfield_building(item: dict[str, Any], i: int) -> dict[str, Any]:
    """A proposed rectangle `{id, center, width, depth, height, heading, unit}` in Site-local metres."""
    where = f"Building {i + 1}"
    unit = item.get("unit")
    w, d, h = (frames.to_metres(item.get(k), unit) for k in ("width", "depth", "height"))
    cx, cy = (frames.to_metres(c, unit) for c in item.get("center", (None, None)))
    heading = item.get("heading", 0.0)
    if not isinstance(heading, (int, float)) or not math.isfinite(heading):
        raise SceneError("invalid_input", f"{where}: heading must be a finite number")
    ring = check_footprint(frames.rectangle((cx, cy), w, d, heading), where)
    return {"id": f"greenfield:{item['id']}", "source": {"namespace": "greenfield", "source_id": str(item["id"])},
            "footprint": [list(p) for p in ring], "height_m": _height(h, where),
            "height_evidence": "greenfield", "levels": None}


def source_building(item: dict[str, Any], origin: tuple[float, float], i: int) -> dict[str, Any]:
    """A source footprint `{namespace, source_id, ring: [[lng, lat], ...], height_m?, levels?}`."""
    where = f"Mapped building {i + 1}"
    ns, sid = item.get("namespace"), item.get("source_id")
    if ns not in NAMESPACES or not isinstance(sid, str) or not sid:
        raise SceneError("invalid_input", f"{where}: namespace must be osm or manual, with a source_id")
    ring = check_footprint([frames.lnglat_to_local(origin, tuple(p)) for p in item.get("ring") or []], where)
    if item.get("height_m") is not None:
        height, evidence = _height(item["height_m"], where), "tagged"
    elif item.get("levels") is not None:
        height, evidence = _height(item["levels"] * LEVEL_HEIGHT_M, where), "levels"
    else:
        height, evidence = FALLBACK_HEIGHT_M, "fallback"
    return {"id": f"{ns}:{sid}", "source": {"namespace": ns, "source_id": sid},
            "footprint": [list(p) for p in ring], "height_m": height,
            "height_evidence": evidence, "levels": item.get("levels")}


def generate(site: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """
    POST .../scene/proposals with action `generate_native`: the immediate preview.

    `inputs` = {greenfield: [...], sources: [...]}. Returns
    {buildings, stats: {buildings, triangles, bytes}}; never writes anything.
    """
    origin = (site["origin"]["lng"], site["origin"]["lat"])
    greenfield, sources = inputs.get("greenfield") or [], inputs.get("sources") or []
    if len(greenfield) + len(sources) > MAX_BUILDINGS:
        raise _limit("buildings", MAX_BUILDINGS, len(greenfield) + len(sources))
    pairs = sum(segment_pairs(len(b.get("ring") or [])) for b in sources) + segment_pairs(4) * len(greenfield)
    if pairs > MAX_SEGMENT_PAIRS:
        raise _limit("segment_pairs", MAX_SEGMENT_PAIRS, pairs)
    buildings = [greenfield_building(b, i) for i, b in enumerate(greenfield)]
    buildings += [source_building(b, origin, i) for i, b in enumerate(sources)]
    seen: set[str] = set()
    for b in buildings:
        if b["id"] in seen:
            raise SceneError("invalid_input", f"Two buildings share the id {b['id']}: duplicate ids are not allowed")
        seen.add(b["id"])
    triangles = sum(prism_triangles(len(b["footprint"])) for b in buildings)
    if triangles > MAX_TRIANGLES:
        raise _limit("triangles", MAX_TRIANGLES, triangles)
    size = len(json.dumps(buildings, separators=(",", ":")).encode("utf-8"))
    if size > MAX_PREVIEW_BYTES:
        raise _limit("preview_bytes", MAX_PREVIEW_BYTES, size)
    return {"buildings": buildings, "stats": {"buildings": len(buildings), "triangles": triangles, "bytes": size}}


def equipment_proxies(site: dict[str, Any], asset_footprints: dict[str, Any]) -> list[dict[str, Any]]:
    """Boxes for placed equipment that has a #712 footprint; width/depth/height from that record."""
    out = []
    for key, p in sorted((site.get("placements") or {}).items()):
        fp = asset_footprints.get(key)
        if not fp:
            continue
        ring = frames.rectangle((p["x"], p["y"]), fp["width_m"], fp["depth_m"], p["heading"])
        h = fp.get("height_m")
        out.append({"target_key": key, "footprint": [list(v) for v in ring],
                    "height": quantity(h, "m") if h is not None else quantity(None, "m", "height_not_set")})
    return out
