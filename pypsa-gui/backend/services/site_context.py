"""
Site context for the 3D site view: building footprints with heights and the
roads, fences, land use and power features around a site from OpenStreetMap
(Overpass), plus a terrain height grid from the AWS Terrarium tiles. Fetched
on demand, cached per site under ``<project>/sites/<id>/context.json``, and
never including imagery (Esri free-tier terms; design D10).

Design: docs/superpowers/specs/2026-09-29-3d-site-view-phase1-design.md §4.2.
Plan: docs/superpowers/plans/2026-09-29-3d-site-view-phase1.md Tasks 5.1–5.3.

Pure core first (``padded_bbox``, ``overpass_query``, ``elements_to_context``,
``terrain_grid``, ``context_document``), network I/O second (``fetch_context``),
so every rule is unit-tested on recorded/synthetic data and the only thing a
mocked transport has to prove is the wire.

Overpass etiquette: one request per fetch, an identifying User-Agent (the
public instance's usage policy requires one), a bounded timeout, and a
configurable endpoint (``PYPSAGUI_OVERPASS_URL``) so an organisation can run
its own.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from PIL import Image
from shapely.geometry import Polygon

log = logging.getLogger(__name__)

CONTEXT_FILE = "context.json"
SCHEMA_VERSION = 1

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_URL_ENV = "PYPSAGUI_OVERPASS_URL"
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
# A private mirror of the Terrarium tiles (same {z}/{x}/{y} template), for
# deployments that cannot reach S3 — the terrain twin of OVERPASS_URL_ENV.
TERRAIN_URL_ENV = "PYPSAGUI_TERRAIN_URL"
USER_AGENT = "pypsa-gui/1.0.0 (3D site view; site context fetch)"

# The frontend's axios client times out at 30 s; the whole fetch stays under it.
TOTAL_BUDGET_S = 25.0
OVERPASS_TIMEOUT_S = 20.0
TERRAIN_GRID = 64
TERRAIN_ZOOM = 12
MIN_PAD_M = 150.0
PAD_FRACTION = 0.25

ATTRIBUTION = [
    "© OpenStreetMap contributors (ODbL)",
    "Terrain: Mapzen / AWS Terrain Tiles",
]

# Height rule (D10): tag → levels → land-use default → fallback.
LEVEL_HEIGHT_M = 3.3
LANDUSE_DEFAULT_HEIGHT_M = {"industrial": 8.0, "retail": 6.0, "commercial": 6.0, "residential": 6.0}
FALLBACK_HEIGHT_M = 5.0
BUILDING_TYPE_DEFAULT_HEIGHT_M = {"industrial": 8.0, "warehouse": 8.0, "retail": 6.0, "commercial": 6.0, "house": 6.0, "residential": 6.0}


class SiteContextUnavailable(RuntimeError):
    """An upstream refused or failed; the message says which and what to do."""


# ── pure core ──────────────────────────────────────────────────────────────

def padded_bbox(boundary: list[list[float]]) -> tuple[float, float, float, float]:
    """
    (min_lng, min_lat, max_lng, max_lat) of the boundary padded by the larger
    of MIN_PAD_M or PAD_FRACTION of its extent, so the site has surroundings.
    """
    lngs = [v[0] for v in boundary]
    lats = [v[1] for v in boundary]
    min_lng, max_lng, min_lat, max_lat = min(lngs), max(lngs), min(lats), max(lats)
    mid_lat = (min_lat + max_lat) / 2
    m_per_deg_lat = 111_319.5
    m_per_deg_lng = m_per_deg_lat * math.cos(math.radians(mid_lat))
    width_m = (max_lng - min_lng) * m_per_deg_lng
    depth_m = (max_lat - min_lat) * m_per_deg_lat
    pad_x = max(MIN_PAD_M, width_m * PAD_FRACTION)
    pad_y = max(MIN_PAD_M, depth_m * PAD_FRACTION)
    return (
        min_lng - pad_x / m_per_deg_lng,
        min_lat - pad_y / m_per_deg_lat,
        max_lng + pad_x / m_per_deg_lng,
        max_lat + pad_y / m_per_deg_lat,
    )


def overpass_query(bbox: tuple[float, float, float, float], timeout_s: int = int(OVERPASS_TIMEOUT_S)) -> str:
    """One query for everything the scene draws; Overpass wants (south, west, north, east)."""
    min_lng, min_lat, max_lng, max_lat = bbox
    b = f"({min_lat:.6f},{min_lng:.6f},{max_lat:.6f},{max_lng:.6f})"
    return (
        f"[out:json][timeout:{timeout_s}];("
        f'way["building"]{b};'
        f'way["highway"]{b};'
        f'way["railway"]{b};'
        f'way["barrier"]{b};'
        f'way["landuse"]{b};'
        f'way["power"]{b};'
        f'way["natural"="water"]{b};'
        ");out geom;"
    )


def _parse_float(v: Any) -> float | None:
    try:
        f = float(str(v).replace("m", "").strip())
        return f if math.isfinite(f) and f > 0 else None
    except (TypeError, ValueError):
        return None


def building_height(tags: dict[str, str], landuse: str | None) -> tuple[float, str]:
    """The height rule, and which step produced it."""
    h = _parse_float(tags.get("height"))
    if h is not None:
        return h, "tag"
    levels = _parse_float(tags.get("building:levels"))
    if levels is not None:
        return levels * LEVEL_HEIGHT_M, "levels"
    btype = (tags.get("building") or "").lower()
    if btype in BUILDING_TYPE_DEFAULT_HEIGHT_M:
        return BUILDING_TYPE_DEFAULT_HEIGHT_M[btype], "landuse"
    if landuse and landuse in LANDUSE_DEFAULT_HEIGHT_M:
        return LANDUSE_DEFAULT_HEIGHT_M[landuse], "landuse"
    return FALLBACK_HEIGHT_M, "default"


def _ring(el: dict) -> list[list[float]]:
    pts = [[p["lon"], p["lat"]] for p in el.get("geometry", []) if "lon" in p and "lat" in p]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _line_kind(tags: dict[str, str]) -> str | None:
    if "highway" in tags:
        return "road"
    if "railway" in tags:
        return "rail"
    if "barrier" in tags:
        return "fence"
    if tags.get("power") in ("line", "minor_line", "cable"):
        return "power_line"
    return None


def _area_kind(tags: dict[str, str]) -> str | None:
    if tags.get("power") == "substation":
        return "power_substation"
    if "landuse" in tags:
        return "landuse"
    if tags.get("natural") == "water":
        return "water"
    return None


def elements_to_context(elements: list[dict]) -> dict:
    """
    Overpass ``out geom`` elements → the §4.2 ``buildings`` / ``lines`` /
    ``areas`` lists. Only ways are read (relations are Phase 2); a building
    with fewer than three distinct vertices is dropped; a line needs two.
    Land use is resolved per building by containment in a landuse area.
    """
    buildings: list[dict] = []
    lines: list[dict] = []
    areas: list[dict] = []
    landuse_polys: list[tuple[Polygon, str]] = []

    ways = [e for e in elements if e.get("type") == "way"]
    for el in ways:
        tags = el.get("tags") or {}
        kind = _area_kind(tags)
        if kind is None:
            continue
        ring = _ring(el)
        if len(ring) < 3:
            continue
        areas.append({"id": el["id"], "kind": kind, "polygon": ring, "tags": tags})
        if kind == "landuse":
            try:
                landuse_polys.append((Polygon(ring), str(tags.get("landuse"))))
            except ValueError:
                pass

    for el in ways:
        tags = el.get("tags") or {}
        if "building" in tags:
            ring = _ring(el)
            if len(ring) < 3:
                continue
            landuse = None
            try:
                centroid = Polygon(ring).centroid
                for poly, lu in landuse_polys:
                    if poly.contains(centroid):
                        landuse = lu
                        break
            except ValueError:
                pass
            height, source = building_height(tags, landuse)
            buildings.append({"id": el["id"], "polygon": ring, "height_m": height, "height_source": source, "tags": tags})
            continue
        kind = _line_kind(tags)
        if kind is not None:
            pts = [[p["lon"], p["lat"]] for p in el.get("geometry", []) if "lon" in p and "lat" in p]
            if len(pts) >= 2:
                lines.append({"id": el["id"], "kind": kind, "points": pts, "tags": tags})

    return {"buildings": buildings, "lines": lines, "areas": areas}


# ── terrain ────────────────────────────────────────────────────────────────

def lnglat_to_tile(lng: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    lat_r = math.radians(lat)
    return ((lng + 180.0) / 360.0) * n, ((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0) * n


def terrain_tiles_for(bbox: tuple[float, float, float, float], z: int = TERRAIN_ZOOM) -> list[tuple[int, int, int]]:
    """The (z, x, y) tiles covering the bbox, row-major."""
    min_lng, min_lat, max_lng, max_lat = bbox
    x0, y0 = lnglat_to_tile(min_lng, max_lat, z)
    x1, y1 = lnglat_to_tile(max_lng, min_lat, z)
    n = 2 ** z - 1
    xs = range(max(0, int(x0)), min(n, int(x1)) + 1)
    ys = range(max(0, int(y0)), min(n, int(y1)) + 1)
    return [(z, x, y) for y in ys for x in xs]


def decode_terrarium(png_bytes: bytes) -> list[list[float]]:
    """Terrarium PNG → 256×256 heights in metres: (R·256 + G + B/256) − 32768."""
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = im.size
    px = im.load()
    return [[px[x, y][0] * 256 + px[x, y][1] + px[x, y][2] / 256 - 32768 for x in range(w)] for y in range(h)]


def terrain_grid(
    tiles: dict[tuple[int, int, int], list[list[float]] | None],
    bbox: tuple[float, float, float, float],
    z: int = TERRAIN_ZOOM,
    grid: int = TERRAIN_GRID,
) -> dict:
    """
    Sample a ``grid``×``grid`` height field over the bbox (row 0 = north),
    nearest-pixel, from decoded tiles. A missing tile (None) is filled with
    the mean of the cells that do exist, so the ground never has a hole.
    """
    min_lng, min_lat, max_lng, max_lat = bbox
    heights: list[float | None] = []
    for j in range(grid):
        lat = max_lat - (max_lat - min_lat) * (j / max(grid - 1, 1))
        for i in range(grid):
            lng = min_lng + (max_lng - min_lng) * (i / max(grid - 1, 1))
            tx, ty = lnglat_to_tile(lng, lat, z)
            key = (z, int(tx), int(ty))
            tile = tiles.get(key)
            if tile is None:
                heights.append(None)
                continue
            px = min(255, max(0, int((tx - int(tx)) * 256)))
            py = min(255, max(0, int((ty - int(ty)) * 256)))
            heights.append(tile[py][px])
    known = [h for h in heights if h is not None]
    fill = sum(known) / len(known) if known else 0.0
    return {
        "z": z, "grid": grid, "bbox": list(bbox),
        "heights_m": [h if h is not None else fill for h in heights],
        "source": "terrarium",
        "missing_tiles": sum(1 for v in tiles.values() if v is None),
    }


# ── the document ───────────────────────────────────────────────────────────

def context_document(bbox: tuple[float, float, float, float], features: dict, terrain: dict | None, source: str = "overpass") -> dict:
    return {
        "version": SCHEMA_VERSION,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "bbox": list(bbox),
        "buildings": features["buildings"],
        "lines": features["lines"],
        "areas": features["areas"],
        "terrain": terrain,
        "attribution": list(ATTRIBUTION),
    }


# ── the wire ───────────────────────────────────────────────────────────────

def overpass_url() -> str:
    return os.environ.get(OVERPASS_URL_ENV, "").strip() or DEFAULT_OVERPASS_URL


def terrain_url() -> str:
    return os.environ.get(TERRAIN_URL_ENV, "").strip() or TERRARIUM_URL


def _client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    # Default `trust_env` so proxies and the CA bundle behave exactly as the
    # packaged app's LLM calls do (services/llm_openai_compat.py).
    return httpx.Client(
        timeout=httpx.Timeout(OVERPASS_TIMEOUT_S, connect=10.0),
        headers={"User-Agent": USER_AGENT},
        transport=transport,
        follow_redirects=True,
    )


def fetch_context(
    boundary: list[list[float]],
    *,
    transport: httpx.BaseTransport | None = None,
    budget_s: float = TOTAL_BUDGET_S,
    clock=time.monotonic,
) -> dict:
    """
    Fetch and assemble the context for a boundary. One Overpass POST, then
    the terrain tiles, within ``budget_s`` overall. Raises
    ``SiteContextUnavailable`` with an actionable message on an upstream
    refusal; a missing terrain tile is tolerated.
    """
    start = clock()
    bbox = padded_bbox(boundary)
    url = overpass_url()
    with _client(transport) as client:
        try:
            r = client.post(url, data={"data": overpass_query(bbox)})
        except httpx.HTTPError as exc:
            raise SiteContextUnavailable(
                f"Overpass at {url} could not be reached ({exc.__class__.__name__}); "
                f"set {OVERPASS_URL_ENV} to a reachable endpoint or try again later."
            ) from exc
        if r.status_code != 200:
            raise SiteContextUnavailable(
                f"Overpass at {url} answered HTTP {r.status_code}"
                + (" (rate limited)" if r.status_code == 429 else "")
                + f"; set {OVERPASS_URL_ENV} to a private endpoint or try again later."
            )
        try:
            elements = r.json().get("elements", [])
        except ValueError as exc:
            raise SiteContextUnavailable(f"Overpass at {url} returned something that is not JSON.") from exc
        features = elements_to_context(elements)

        tiles: dict[tuple[int, int, int], list[list[float]] | None] = {}
        for key in terrain_tiles_for(bbox):
            if clock() - start > budget_s:
                log.warning("site context: terrain fetch stopped at the %.0f s budget", budget_s)
                tiles[key] = None
                continue
            z, x, y = key
            try:
                tr = client.get(terrain_url().format(z=z, x=x, y=y), timeout=10.0)
                tiles[key] = decode_terrarium(tr.content) if tr.status_code == 200 else None
            except (httpx.HTTPError, OSError):
                tiles[key] = None
        terrain = terrain_grid(tiles, bbox) if tiles else None

    return context_document(bbox, features, terrain)


# ── cache ──────────────────────────────────────────────────────────────────

def read_cached(site_dir) -> dict | None:
    path = site_dir / CONTEXT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == SCHEMA_VERSION else None


def write_cached(site_dir, doc: dict) -> None:
    from services.atomic_io import atomic_write_text

    site_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(site_dir / CONTEXT_FILE, json.dumps(doc, separators=(",", ":"), allow_nan=False))


def clear_cached(site_dir) -> bool:
    path = site_dir / CONTEXT_FILE
    if path.is_file():
        path.unlink()
        return True
    return False
