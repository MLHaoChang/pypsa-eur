"""
SC-06-layout (V39/V40): a calibrated layout drawing becomes native buildings.

`import_layout` proposals carry one `layout` input of one of two kinds:

* ``{kind: "geojson", document}``: an RFC 7946 FeatureCollection or Feature in
  WGS84 longitude/latitude. A ``crs`` member may only name CRS84; any other
  named CRS is refused by name, never reprojected or guessed. Polygon and
  MultiPolygon features become footprints; other geometry is skipped and
  counted. Coordinates go to Site-local metres through the one projection
  the scene uses (`frames.lnglat_to_local`).
* ``{kind: "raster", image, calibration, footprints}``: the outlines a user
  traced over a PNG/JPEG/WebP drawing. The image itself never reaches the
  server (the browser keeps it as a local underlay), so ``image`` is the
  client-DECLARED frame ``{width_px, height_px, media_type}``: the 50 MP and
  side limits and the "inside the image" check bound that declared frame; the
  server decodes nothing. ``calibration`` is a scale bar ``{a_px, b_px,
  distance, unit}``, an anchor pixel placed at ``anchor_local`` [e, n] metres,
  and ``rotation_deg``: the bearing of the drawing's "up", clockwise from
  north. Pixel y grows downwards, so it is flipped to north.

`normalize` validates the shape, refuses unsafe or oversized input and keeps
only the fields generation uses (no properties, foreign members or names), all
BEFORE any geometry work. `generate` builds the frozen native records with the
``layout`` namespace. Heights: ``height`` (GeoJSON) / ``height_m`` (raster) in
metres is ``tagged``; else ``levels``/``building:levels`` × 3.0 m (D08) is
``levels``; else the 6.0 m ``fallback``. A text height is refused, not parsed.

DXF, IFC, PDF and SVG are not accepted: no adapter has its own units/CRS
tests yet (ticket 07), so they are refused by name rather than half-read.
"""

from __future__ import annotations

import math
from typing import Any

from services.site_scene import frames, native
from services.site_scene.contract import SceneError

RASTER_TYPES = ("image/png", "image/jpeg", "image/webp")
UNSUPPORTED_KINDS = ("dxf", "ifc", "pdf", "svg", "dwg")
CRS84_NAMES = (
    "urn:ogc:def:crs:OGC:1.3:CRS84",
    "urn:ogc:def:crs:OGC::CRS84",
    "OGC:CRS84",
    "CRS84",
)
MAX_DECODED_PIXELS = 50_000_000  # D05 per-file pixel quota
MAX_IMAGE_SIDE_PX = 20_000
MAX_SITE_DISTANCE_M = 10_000.0
MAX_ID_CHARS = 128
_REMOTE_KEYS = ("$ref", "href")
_MAX_DEPTH = 64


def _refuse(code: str, message: str, field: str = "layout") -> SceneError:
    return SceneError("invalid_input", message, {"field": field, "code": code})


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: Any) -> bool:
    return _is_number(v) and math.isfinite(v)


# ── normalize: shape, safety and limits, no geometry ────────────────────────


def normalize(raw: Any) -> dict[str, Any]:
    """The validated, stripped layout a proposal stores. Raises SceneError."""
    if not isinstance(raw, dict):
        raise _refuse(
            "layout_kind",
            "The layout could not be read: choose a GeoJSON file or a drawing",
        )
    kind = raw.get("kind")
    if isinstance(kind, str) and kind.lower() in UNSUPPORTED_KINDS:
        raise _refuse(
            "unsupported_format",
            f"{kind.upper()} files are not accepted: export the layout as "
            "GeoJSON, or as a PNG, JPEG or WebP image and trace it",
        )
    if kind == "geojson":
        out = _normalize_geojson(raw.get("document"))
    elif kind == "raster":
        out = _normalize_raster(raw)
    else:
        raise _refuse(
            "layout_kind",
            "The layout could not be read: choose a GeoJSON file or a drawing",
        )
    if not out["footprints"]:
        raise _refuse("layout_empty", "The layout has no building outlines")
    _admit(out["footprints"], "ring" if kind == "geojson" else "ring_px")
    return out


def _admit(footprints: list[dict[str, Any]], key: str) -> None:
    """Count, vertices, edge pairs and triangles from the vertex counts alone (V40)."""
    counts = []
    for fp in footprints:
        ring = fp[key]
        n = len(ring) - 1 if len(ring) > 1 and ring[0] == ring[-1] else len(ring)
        if n > native.MAX_FOOTPRINT_VERTICES:
            raise native._limit("footprint_vertices", native.MAX_FOOTPRINT_VERTICES, n)
        counts.append(n)
    native.admit_inputs(len(footprints), sum(native.segment_pairs(n) for n in counts))
    triangles = sum(native.prism_triangles(n) for n in counts)
    if triangles > native.MAX_TRIANGLES:
        raise native._limit("triangles", native.MAX_TRIANGLES, triangles)


def _no_remote(node: Any, depth: int = 0) -> None:
    """Refuse a JSON reference or link anywhere in the document: nothing is ever fetched."""
    if depth > _MAX_DEPTH:
        raise _refuse("layout_too_deep", "The layout is nested too deeply")
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "properties":
                continue  # never fetched or stored: only height/levels/id are read from it
            if k in _REMOTE_KEYS:
                raise _refuse(
                    "remote_reference",
                    "The layout refers to another file or address: "
                    "include everything in the file itself",
                )
            _no_remote(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _no_remote(v, depth + 1)


def _check_crs(doc: dict[str, Any]) -> None:
    crs = doc.get("crs")
    if crs is None:
        return  # RFC 7946: WGS84 longitude/latitude
    if not isinstance(crs, dict) or crs.get("type") == "link":
        raise _refuse(
            "remote_reference",
            "The layout's coordinate system is a link: save it as WGS84 GeoJSON",
        )
    name = (
        (crs.get("properties") or {}).get("name") if crs.get("type") == "name" else None
    )
    if name not in CRS84_NAMES:
        raise _refuse(
            "crs_unsupported",
            f"The layout uses coordinate system {name or 'unknown'}: "
            "save it as WGS84 (longitude/latitude) GeoJSON",
        )


def _lnglat(p: Any) -> list[float]:
    if (
        not isinstance(p, list)
        or len(p) not in (2, 3)
        or not all(_finite(c) for c in p)
    ):
        raise _refuse(
            "layout_coordinates",
            "The layout has a coordinate that is not a pair of numbers",
        )
    if not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
        raise _refuse(
            "crs_mismatch",
            "The layout's coordinates are not longitude/latitude: "
            "save it as WGS84 GeoJSON",
        )
    return [float(p[0]), float(p[1])]


def _feature_id(f: dict[str, Any], index: int) -> str:
    fid = f.get("id")
    if fid is None:
        fid = (
            (f.get("properties") or {}).get("id")
            if isinstance(f.get("properties"), dict)
            else None
        )
    if (_is_number(fid) and math.isfinite(fid)) or (
        isinstance(fid, str) and fid.strip()
    ):
        text = str(fid).strip()
        if len(text) <= MAX_ID_CHARS:
            return text
    return f"f{index + 1}"


def _heights(props: dict[str, Any], height_key: str, name: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    h = props.get(height_key)
    if h is not None:
        if not _is_number(h):
            raise _refuse(
                "height_not_number",
                f"Layout building {name}: height must be a number in metres",
            )
        out["height_m"] = h
    levels = props.get("building:levels", props.get("levels"))
    if levels is not None:
        if not _finite(levels) or levels != int(levels) or levels <= 0:
            raise _refuse(
                "levels_invalid",
                f"Layout building {name}: levels must be a whole number above 0",
            )
        out["levels"] = int(levels)
    return out


def _normalize_geojson(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("type") not in (
        "FeatureCollection",
        "Feature",
    ):
        raise _refuse(
            "layout_document",
            "The GeoJSON file must hold a FeatureCollection or a Feature",
        )
    _no_remote(doc)
    _check_crs(doc)
    features = doc.get("features") if doc["type"] == "FeatureCollection" else [doc]
    if not isinstance(features, list):
        raise _refuse(
            "layout_document", "The GeoJSON file's features could not be read"
        )
    if len(features) > native.MAX_BUILDINGS:  # parts can only add to this
        raise native._limit("buildings", native.MAX_BUILDINGS, len(features))
    footprints: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for i, f in enumerate(features):
        if not isinstance(f, dict) or f.get("type") != "Feature":
            raise _refuse(
                "layout_document", f"GeoJSON feature {i + 1} is not a Feature"
            )
        _check_crs(f)
        geom = f.get("geometry")
        gtype = geom.get("type") if isinstance(geom, dict) else None
        if gtype not in ("Polygon", "MultiPolygon"):
            key = "null" if geom is None else str(gtype)
            skipped[key] = skipped.get(key, 0) + 1
            continue
        fid = _feature_id(f, i)
        coords = geom.get("coordinates")
        polygons = [coords] if gtype == "Polygon" else coords
        if not isinstance(polygons, list) or not all(
            isinstance(p, list) and p for p in polygons
        ):
            raise _refuse(
                "layout_coordinates", f"Layout building {fid}: its outline is missing"
            )
        props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
        heights = _heights(props, "height", fid)
        for k, poly in enumerate(polygons):
            if len(poly) > 1:
                raise _refuse(
                    "layout_hole",
                    f"Layout building {fid} has a courtyard or hole: "
                    "draw its outline without holes",
                )
            ring = poly[0]
            if not isinstance(ring, list):
                raise _refuse(
                    "layout_coordinates",
                    f"Layout building {fid}: its outline is not a list of points",
                )
            if len(ring) > native.MAX_FOOTPRINT_VERTICES + 1:
                raise native._limit(
                    "footprint_vertices", native.MAX_FOOTPRINT_VERTICES, len(ring) - 1
                )
            sid = fid if len(polygons) == 1 else f"{fid}#{k + 1}"
            footprints.append(
                {"id": sid, "ring": [_lnglat(p) for p in ring], **heights}
            )
        if len(footprints) > native.MAX_BUILDINGS:
            raise native._limit("buildings", native.MAX_BUILDINGS, len(footprints))
    return {"kind": "geojson", "footprints": footprints, "skipped": skipped}


def _pixel(p: Any, image: dict[str, int], what: str) -> list[float]:
    if not isinstance(p, list) or len(p) != 2 or not all(_finite(c) for c in p):
        raise _refuse(
            "layout_coordinates",
            f"{what}: a pixel must be a pair of numbers",
            "calibration",
        )
    if not (0 <= p[0] <= image["width_px"] and 0 <= p[1] <= image["height_px"]):
        raise _refuse(
            "outside_image", f"{what}: the point is outside the drawing", "calibration"
        )
    return [float(p[0]), float(p[1])]


def _image(image: Any) -> dict[str, Any]:
    if not isinstance(image, dict):
        raise _refuse(
            "layout_image",
            "The drawing's size is missing: choose the drawing again",
            "image",
        )
    media = image.get("media_type")
    if media not in RASTER_TYPES:
        raise _refuse(
            "unsupported_format",
            f"Images of type {media or 'unknown'} are not accepted: "
            "use PNG, JPEG or WebP. GeoJSON layouts are accepted too",
            "image",
        )
    w, h = image.get("width_px"), image.get("height_px")
    if not all(
        isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (w, h)
    ):
        raise _refuse(
            "layout_image",
            "The drawing's size could not be read: choose the drawing again",
            "image",
        )
    if max(w, h) > MAX_IMAGE_SIDE_PX:
        raise SceneError(
            "resource_limit",
            f"Too large: the drawing is {max(w, h)} pixels across, "
            f"the limit is {MAX_IMAGE_SIDE_PX}",
            {"limit": "image_side", "max": MAX_IMAGE_SIDE_PX, "actual": max(w, h)},
        )
    if w * h > MAX_DECODED_PIXELS:
        raise SceneError(
            "resource_limit",
            f"Too large: the drawing is {w * h / 1e6:.1f} megapixels, "
            f"the limit is {MAX_DECODED_PIXELS // 1_000_000}",
            {"limit": "decoded_pixels", "max": MAX_DECODED_PIXELS, "actual": w * h},
        )
    return {"width_px": w, "height_px": h, "media_type": media}


def _normalize_raster(raw: dict[str, Any]) -> dict[str, Any]:
    image = _image(raw.get("image"))
    cal = raw.get("calibration")
    if not isinstance(cal, dict) or not isinstance(cal.get("scale"), dict):
        raise _refuse(
            "scale_missing",
            "The drawing has no scale: mark two points and enter the distance "
            "between them",
            "calibration",
        )
    scale = cal["scale"]
    a, b = (
        _pixel(scale.get("a_px"), image, "Scale start"),
        _pixel(scale.get("b_px"), image, "Scale end"),
    )
    distance_m = frames.to_metres(scale.get("distance"), scale.get("unit"))
    if distance_m <= 0:
        raise _refuse(
            "scale_distance", "The scale distance must be above 0", "calibration"
        )
    if a == b:
        raise _refuse(
            "scale_points_equal",
            "The two scale points are the same point: mark two different points",
            "calibration",
        )
    for key in ("anchor_px", "anchor_local", "rotation_deg"):
        if key not in cal:
            raise _refuse(
                "placement_missing",
                "The drawing is not placed on the site: set its anchor point "
                "and rotation",
                "calibration",
            )
    rotation = cal["rotation_deg"]
    if not _finite(rotation):
        raise _refuse(
            "rotation_invalid",
            "The drawing's rotation must be a number of degrees",
            "calibration",
        )
    anchor_local = cal["anchor_local"]
    if (
        not isinstance(anchor_local, list)
        or len(anchor_local) != 2
        or not all(_finite(c) for c in anchor_local)
    ):
        raise _refuse(
            "layout_coordinates",
            "The anchor's site position must be east and north in metres",
            "calibration",
        )
    footprints = raw.get("footprints")
    if not isinstance(footprints, list):
        raise _refuse(
            "layout_footprints",
            "The traced buildings could not be read: trace them again",
            "footprints",
        )
    if len(footprints) > native.MAX_BUILDINGS:
        raise native._limit("buildings", native.MAX_BUILDINGS, len(footprints))
    out_fps = []
    for i, fp in enumerate(footprints):
        fid = fp.get("id") if isinstance(fp, dict) else None
        if not isinstance(fid, str) or not fid.strip() or len(fid) > MAX_ID_CHARS:
            raise _refuse(
                "layout_footprints",
                f"Traced building {i + 1}: it needs a name of up to "
                f"{MAX_ID_CHARS} characters",
                "footprints",
            )
        ring = fp.get("ring_px")
        if not isinstance(ring, list):
            raise _refuse(
                "layout_coordinates",
                f"Traced building {fid}: its outline is missing",
                "footprints",
            )
        if len(ring) > native.MAX_FOOTPRINT_VERTICES + 1:
            raise native._limit(
                "footprint_vertices", native.MAX_FOOTPRINT_VERTICES, len(ring) - 1
            )
        out_fps.append(
            {
                "id": fid,
                "ring_px": [_pixel(p, image, f"Traced building {fid}") for p in ring],
                **_heights(fp, "height_m", fid),
            }
        )
    return {
        "kind": "raster",
        "image": image,
        "calibration": {
            "a_px": a,
            "b_px": b,
            "distance_m": distance_m,
            "anchor_px": _pixel(cal["anchor_px"], image, "Anchor"),
            "anchor_local": [float(c) for c in anchor_local],
            "rotation_deg": float(rotation),
        },
        "footprints": out_fps,
    }


# ── generate: geometry, after admission ─────────────────────────────────────


def pixel_transform(cal: dict[str, Any]):
    """Pixel (x right, y down) → Site-local (e, n) metres: scale, flip y, turn clockwise, then anchor."""
    (ax, ay), (bx, by) = cal["a_px"], cal["b_px"]
    s = cal["distance_m"] / math.hypot(bx - ax, by - ay)
    t = math.radians(cal["rotation_deg"])
    c, sn = math.cos(t), math.sin(t)
    (px0, py0), (e0, n0) = cal["anchor_px"], cal["anchor_local"]

    def to_local(p):
        dx, dy = (p[0] - px0) * s, -(p[1] - py0) * s  # right, up on the drawing
        return (e0 + dx * c + dy * sn, n0 - dx * sn + dy * c)

    return to_local, s


def _building(
    fid: str, ring: list[tuple[float, float]], fp: dict[str, Any]
) -> dict[str, Any]:
    where = f"Layout building {fid}"
    if any(math.hypot(e, n) > MAX_SITE_DISTANCE_M for e, n in ring):
        # Both kinds: a GeoJSON in the wrong place or a drawing anchored far off the Site.
        raise _refuse(
            "far_from_site",
            f"{where} is more than {MAX_SITE_DISTANCE_M / 1000:g} km from this site: "
            "check the layout is for this site and where it is placed",
        )
    ring = native.check_footprint(ring, where)
    if "height_m" in fp:
        height, evidence = native._height(fp["height_m"], where), "tagged"
    elif "levels" in fp:
        height, evidence = (
            native._height(fp["levels"] * native.LEVEL_HEIGHT_M, where),
            "levels",
        )
    else:
        height, evidence = native.FALLBACK_HEIGHT_M, "fallback"
    return {
        "id": f"layout:{fid}",
        "source": {"namespace": "layout", "source_id": fid},
        "footprint": [list(p) for p in ring],
        "height_m": height,
        "height_evidence": evidence,
        "levels": fp.get("levels"),
    }


def generate(site: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    """The preview for a normalized layout: {buildings, stats, layout: summary}. Writes nothing."""
    origin = (site["origin"]["lng"], site["origin"]["lat"])
    buildings = []
    if layout["kind"] == "raster":
        to_local, mpp = pixel_transform(layout["calibration"])
        for fp in layout["footprints"]:
            buildings.append(
                _building(fp["id"], [to_local(p) for p in fp["ring_px"]], fp)
            )
        summary = {
            "kind": "raster",
            "metres_per_pixel": mpp,
            "crs": None,
            "skipped": {},
        }
    else:
        for fp in layout["footprints"]:
            ring = [frames.lnglat_to_local(origin, tuple(p)) for p in fp["ring"]]
            buildings.append(_building(fp["id"], ring, fp))
        summary = {
            "kind": "geojson",
            "metres_per_pixel": None,
            "crs": "CRS84",
            "skipped": dict(layout["skipped"]),
        }
    stats = native.admit_output(buildings)
    es = [p[0] for b in buildings for p in b["footprint"]]
    ns = [p[1] for b in buildings for p in b["footprint"]]
    summary["extent_m"] = [max(es) - min(es), max(ns) - min(ns)]
    summary["footprints"] = len(buildings)
    return {"buildings": buildings, "stats": stats, "layout": summary}
