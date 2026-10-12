"""
OSM route suggestions for the map view (plan 2, M3).

`GET /api/projects/{name}/map_layout/osm_suggestions?bbox=…` asks Overpass
for the `power=line|minor_line|cable` ways and `power=substation` areas in a
bounding box and the map draws them as a dimmed suggestion layer; clicking a
way beside a chord offers "Use as route for <line>". Nothing is computed
from them — a suggestion is OSM's geometry, adopted or not by the user.

The wire is `services/site_context.py`'s Overpass client (its endpoint
setting `PYPSAGUI_OVERPASS_URL`, User-Agent, timeouts and body cap), reused
rather than duplicated. What this module adds is the query, the shape, and
an in-memory cache: one entry per bbox for `SUGGESTION_TTL_S`, and a backoff
after an upstream refusal so a flapping toggle cannot hammer the public
instance (its usage policy is the reason the endpoint is configurable). No
sidecar — a suggestion is not project state until it becomes a route.
"""
from __future__ import annotations

import json
import math
import time

import httpx

from services import site_context as sc

SUGGESTION_TTL_S = 600.0
BACKOFF_S = 60.0
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
POWER_LINE_TAGS = ("line", "minor_line", "cable")


class OsmUnavailable(RuntimeError):
    """Overpass refused or failed; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class OsmBboxInvalid(ValueError):
    """The bbox parameter is malformed or too large."""


# ── pure core ──────────────────────────────────────────────────────────────

def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """`min_lng,min_lat,max_lng,max_lat` → the tuple; raises `OsmBboxInvalid`."""
    parts = [p.strip() for p in (raw or "").split(",")]
    if len(parts) != 4:
        raise OsmBboxInvalid("bbox must be min_lng,min_lat,max_lng,max_lat")
    try:
        min_lng, min_lat, max_lng, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise OsmBboxInvalid("bbox must be four numbers") from exc
    if not all(math.isfinite(v) for v in (min_lng, min_lat, max_lng, max_lat)):
        raise OsmBboxInvalid("bbox must be finite")
    if not (-180 <= min_lng < max_lng <= 180 and -90 <= min_lat < max_lat <= 90):
        raise OsmBboxInvalid("bbox must be ordered and within lng ±180, lat ±90")
    bbox = (min_lng, min_lat, max_lng, max_lat)
    area = sc.bbox_area_km2(bbox)
    if area > sc.MAX_BBOX_KM2:
        raise OsmBboxInvalid(
            f"The view spans {area:.0f} km²; OSM suggestions cover at most {sc.MAX_BBOX_KM2:.0f} km². Zoom in."
        )
    return bbox


def suggestions_query(bbox: tuple[float, float, float, float], timeout_s: int = int(sc.OVERPASS_TIMEOUT_S)) -> str:
    """Power ways and substation areas only; Overpass wants (south, west, north, east)."""
    min_lng, min_lat, max_lng, max_lat = bbox
    b = f"({min_lat:.6f},{min_lng:.6f},{max_lat:.6f},{max_lng:.6f})"
    return (
        f"[out:json][timeout:{timeout_s}];("
        f'way["power"~"^({"|".join(POWER_LINE_TAGS)})$"]{b};'
        f'way["power"="substation"]{b};'
        ");out geom;"
    )


def elements_to_suggestions(elements: list[dict]) -> dict:
    """
    Overpass `out geom` elements → `{lines, areas}`: a power way needs two
    vertices, a substation three. Everything else (nodes, relations, other
    tags) is ignored. Each entry keeps the OSM id and tags so the UI can
    name what it shows (`voltage`, `name`).
    """
    lines: list[dict] = []
    areas: list[dict] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags") or {}
        power = tags.get("power")
        pts = [[p["lon"], p["lat"]] for p in el.get("geometry", []) if "lon" in p and "lat" in p]
        if power in POWER_LINE_TAGS and len(pts) >= 2:
            lines.append({"id": el["id"], "kind": power, "points": pts, "tags": tags})
        elif power == "substation":
            if len(pts) >= 2 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) >= 3:
                areas.append({"id": el["id"], "kind": "substation", "polygon": pts, "tags": tags})
    return {"lines": lines, "areas": areas}


def suggestions_document(bbox: tuple[float, float, float, float], features: dict, cached: bool = False) -> dict:
    return {
        "bbox": list(bbox),
        "lines": features["lines"],
        "areas": features["areas"],
        "attribution": ATTRIBUTION,
        "cached": cached,
    }


# ── the wire, with a cache ─────────────────────────────────────────────────

_cache: dict[tuple[float, float, float, float], tuple[float, dict]] = {}
_backoff_until: float = 0.0
_backoff_reason: str = ""


def _cache_key(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(round(v, 4) for v in bbox)  # type: ignore[return-value]


def clear_cache() -> None:
    """Test seam."""
    global _backoff_until, _backoff_reason
    _cache.clear()
    _backoff_until = 0.0
    _backoff_reason = ""


def _refuse(status: int, url: str) -> OsmUnavailable:
    if status == 429:
        msg = f"Overpass at {url} is rate limiting (HTTP 429); set {sc.OVERPASS_URL_ENV} to a private endpoint or try again later."
        return OsmUnavailable(429, msg)
    if status in (504, 503, 502):
        msg = f"Overpass at {url} timed out (HTTP {status}); zoom in, or set {sc.OVERPASS_URL_ENV} to a private endpoint."
        return OsmUnavailable(504, msg)
    return OsmUnavailable(502, f"Overpass at {url} answered HTTP {status}; set {sc.OVERPASS_URL_ENV} to a reachable endpoint.")


def fetch_suggestions(
    bbox: tuple[float, float, float, float],
    *,
    transport: httpx.BaseTransport | None = None,
    clock=time.monotonic,
) -> dict:
    """
    The suggestions for a bbox: from the cache while fresh, else one Overpass
    POST. Raises `OsmUnavailable` (with the status to answer) on a refusal,
    and again — without a request — for `BACKOFF_S` after one.
    """
    global _backoff_until, _backoff_reason
    now = clock()
    key = _cache_key(bbox)
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return suggestions_document(bbox, hit[1], cached=True)
    if now < _backoff_until:
        raise OsmUnavailable(429, _backoff_reason)
    url = sc.overpass_url()
    with sc._client(transport) as client:
        try:
            with client.stream("POST", url, data={"data": suggestions_query(bbox)}) as r:
                if r.status_code != 200:
                    err = _refuse(r.status_code, url)
                    _backoff_until, _backoff_reason = now + BACKOFF_S, str(err)
                    raise err
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > sc.MAX_OVERPASS_BYTES:
                        raise OsmUnavailable(502, f"Overpass returned more than {sc.MAX_OVERPASS_BYTES / 1e6:.0f} MB; zoom in.")
                    chunks.append(chunk)
                body = b"".join(chunks)
        except httpx.TimeoutException as exc:
            err = OsmUnavailable(504, f"Overpass at {url} did not answer in time ({exc.__class__.__name__}); set {sc.OVERPASS_URL_ENV} to a reachable endpoint or try again later.")
            _backoff_until, _backoff_reason = now + BACKOFF_S, str(err)
            raise err from exc
        except httpx.HTTPError as exc:
            raise OsmUnavailable(502, f"Overpass at {url} could not be reached ({exc.__class__.__name__}); set {sc.OVERPASS_URL_ENV} to a reachable endpoint.") from exc
    try:
        elements = json.loads(body).get("elements", [])
    except ValueError as exc:
        raise OsmUnavailable(502, f"Overpass at {url} returned something that is not JSON.") from exc
    features = elements_to_suggestions(elements)
    _cache[key] = (now + SUGGESTION_TTL_S, features)
    return suggestions_document(bbox, features)
