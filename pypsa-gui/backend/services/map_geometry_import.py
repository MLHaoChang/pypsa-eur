"""
Adopt real topology from outside: GeoJSON / KML → routes and sites (plan 2, M3).

`POST /api/projects/{name}/map_layout/import` hands a FeatureCollection (or
a KML document, converted here to the same features) to this module. Every
LineString is matched to a branch — by its `name` property first, else by
its two end vertices snapping to two placed buses within a tolerance — and
every Polygon becomes a site whose members are the placed buses inside it.
The result is a MATCH REPORT the user confirms before anything is written;
`apply_report` is the write, and it is the route's job to call it only with
`?apply=true` and under the lock check.

Pure over dicts: the parsers take text, the matcher takes the branch and bus
tables the route reads from the live network, and `apply_report` takes the
project directory. No FastAPI, no PyPSA, no network I/O. The KML parser is
dependency-free beyond a hardened XML parser, and reads the subset a
surveyor's export carries: Placemark name, LineString, Polygon outer ring,
MultiGeometry. A document that is not UTF-8, or declares a DOCTYPE or an
entity, is refused before parsing, and the parse itself uses a hardened
parser only (defusedxml, else lxml with entities off).

Coordinates are GeoJSON `[lng, lat]` throughout, the order `map_layout.json`
stores. A route's stored `points` are its INTERIOR vertices: an end vertex
that lies within the tolerance of its bus IS that bus and is dropped, one
further away is kept as the first bend (the chord from the bus reaches it).
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from services import map_layout_service, site_service

MAX_IMPORT_BYTES = 8 * 1024 * 1024
DEFAULT_TOLERANCE_M = 50.0
MAX_TOLERANCE_M = 5000.0
# Name lookup prefers a Line over a Link over a Transformer when two kinds
# share a name and the feature does not say which it means (`kind` property).
KIND_ORDER = ("line", "link", "tr")
KIND_ALIASES = {
    "line": "line", "lines": "line", "Line": "line",
    "link": "link", "links": "link", "Link": "link",
    "tr": "tr", "transformer": "tr", "transformers": "tr", "Transformer": "tr",
}
_EARTH_KM = 6371.0


class GeometryImportInvalid(ValueError):
    """The upload is not GeoJSON or KML we can read; the message says what is wrong."""


@dataclass
class Feature:
    index: int
    geometry: str                      # "LineString" | "Polygon"
    coords: list[list[float]]          # [lng, lat] vertices; a Polygon's outer ring, unclosed
    name: str | None = None
    properties: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.name or f"feature {self.index}"


@dataclass(frozen=True)
class Branch:
    kind: str                          # line | link | tr
    name: str
    bus0: str
    bus1: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


# ── parsing ─────────────────────────────────────────────────────────────────

def _finite(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_vertex(v: object, where: str) -> list[float]:
    if not (isinstance(v, (list, tuple)) and len(v) >= 2 and _finite(v[0]) and _finite(v[1])):
        raise GeometryImportInvalid(f"{where}: each position must be [lng, lat] with finite numbers")
    lng, lat = float(v[0]), float(v[1])
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise GeometryImportInvalid(f"{where}: position out of range (lng ±180, lat ±90)")
    return [lng, lat]


def _ring(coords: object, where: str) -> list[list[float]]:
    if not isinstance(coords, list):
        raise GeometryImportInvalid(f"{where}: coordinates must be a list")
    ring = [_check_vertex(v, where) for v in coords]
    if len(ring) >= 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    return ring


def _name_of(props: dict) -> str | None:
    for key in ("name", "Name", "NAME", "id", "label"):
        v = props.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return None


def _geometry_features(geom: object, index: int, props: dict, out: list[Feature]) -> None:
    """Flatten one GeoJSON geometry (Multi* and GeometryCollection included) into features."""
    if not isinstance(geom, dict):
        raise GeometryImportInvalid(f"feature {index}: geometry must be an object")
    gtype = geom.get("type")
    where = f"feature {index} ({gtype})"
    name = _name_of(props)
    if gtype == "LineString":
        out.append(Feature(index, "LineString", [_check_vertex(v, where) for v in _as_list(geom.get("coordinates"), where)], name, props))
    elif gtype == "Polygon":
        rings = _as_list(geom.get("coordinates"), where)
        if not rings:
            raise GeometryImportInvalid(f"{where}: a polygon needs an outer ring")
        out.append(Feature(index, "Polygon", _ring(rings[0], where), name, props))
    elif gtype == "MultiLineString":
        for part in _as_list(geom.get("coordinates"), where):
            out.append(Feature(index, "LineString", [_check_vertex(v, where) for v in _as_list(part, where)], name, props))
    elif gtype == "MultiPolygon":
        for poly in _as_list(geom.get("coordinates"), where):
            rings = _as_list(poly, where)
            if rings:
                out.append(Feature(index, "Polygon", _ring(rings[0], where), name, props))
    elif gtype == "GeometryCollection":
        for g in _as_list(geom.get("geometries"), where):
            _geometry_features(g, index, props, out)
    else:
        # Points and anything else carry no route and no boundary: skipped,
        # reported by the matcher as unmatched so the user sees them.
        out.append(Feature(index, str(gtype or "unknown"), [], name, props))


def _as_list(v: object, where: str) -> list:
    if not isinstance(v, list):
        raise GeometryImportInvalid(f"{where}: coordinates must be a list")
    return v


def parse_geojson(obj: object) -> list[Feature]:
    """A FeatureCollection, a single Feature, or a bare geometry → features. Raises `GeometryImportInvalid`."""
    if not isinstance(obj, dict):
        raise GeometryImportInvalid("GeoJSON must be an object")
    t = obj.get("type")
    out: list[Feature] = []
    if t == "FeatureCollection":
        feats = obj.get("features")
        if not isinstance(feats, list):
            raise GeometryImportInvalid("FeatureCollection.features must be a list")
        for i, f in enumerate(feats):
            if not isinstance(f, dict) or f.get("type") != "Feature":
                raise GeometryImportInvalid(f"features[{i}]: must be a Feature")
            props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
            _geometry_features(f.get("geometry"), i, props, out)
    elif t == "Feature":
        props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
        _geometry_features(obj.get("geometry"), 0, props, out)
    elif t in ("LineString", "Polygon", "MultiLineString", "MultiPolygon", "GeometryCollection"):
        _geometry_features(obj, 0, {}, out)
    else:
        raise GeometryImportInvalid("GeoJSON must be a FeatureCollection, a Feature or a geometry")
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kml_coords(text: str | None, where: str) -> list[list[float]]:
    out: list[list[float]] = []
    for token in (text or "").split():
        parts = token.split(",")
        if len(parts) < 2:
            raise GeometryImportInvalid(f"{where}: KML coordinates are lng,lat[,alt] triples")
        try:
            out.append(_check_vertex([float(parts[0]), float(parts[1])], where))
        except ValueError as exc:
            raise GeometryImportInvalid(f"{where}: KML coordinate is not a number") from exc
    return out


_DTD = re.compile(r"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
_DECLARED_ENCODING = re.compile(r"""^\s*<\?xml[^>]*encoding\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def _xml_fromstring(text: str):
    """Parse with a hardened parser only: defusedxml (the frozen app pins it),
    else lxml with entities, DTDs and the network off (the dev environments
    carry it). Neither importable refuses the KML instead of falling back."""
    try:
        from defusedxml.ElementTree import fromstring
    except ImportError:
        pass
    else:
        return fromstring(text)
    try:
        from lxml import etree
    except ImportError:
        raise GeometryImportInvalid("KML can't be read here: neither defusedxml nor lxml is installed.") from None
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False,
                             remove_comments=True, remove_pis=True)
    return etree.fromstring(text.encode("utf-8"), parser)


def parse_kml(text: str) -> list[Feature]:
    """
    KML Placemarks → the same features a GeoJSON parse yields: a LineString's
    coordinates, a Polygon's outer ring, each geometry of a MultiGeometry.
    Namespace-agnostic (surveyor exports mix KML 2.0, 2.2 and none).
    """
    declared = _DECLARED_ENCODING.match(text)
    if declared and declared.group(1).lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise GeometryImportInvalid(
            f"KML declares the {declared.group(1)} encoding; save it as UTF-8 and upload it again.")
    if _DTD.search(text):
        raise GeometryImportInvalid("KML declares a DOCTYPE or entity; remove it and upload the file again.")
    try:
        root = _xml_fromstring(text)
    except (ET.ParseError, ValueError, SyntaxError) as exc:  # lxml's XMLSyntaxError is a SyntaxError
        raise GeometryImportInvalid(f"KML is not well-formed XML: {exc}") from exc
    out: list[Feature] = []
    index = 0
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        name = None
        for child in pm:
            if _local(child.tag) == "name" and child.text and child.text.strip():
                name = child.text.strip()
        where = f"placemark {index}" + (f" ({name})" if name else "")
        found = False
        for el in pm.iter():
            tag = _local(el.tag)
            if tag == "LineString":
                coords = next((c for c in el.iter() if _local(c.tag) == "coordinates"), None)
                out.append(Feature(index, "LineString", _kml_coords(coords.text if coords is not None else "", where), name, {"name": name} if name else {}))
                found = True
            elif tag == "Polygon":
                outer = next((c for c in el.iter() if _local(c.tag) == "outerBoundaryIs"), None)
                coords = next((c for c in (outer if outer is not None else el).iter() if _local(c.tag) == "coordinates"), None)
                ring = _kml_coords(coords.text if coords is not None else "", where)
                if len(ring) >= 2 and ring[0] == ring[-1]:
                    ring = ring[:-1]
                out.append(Feature(index, "Polygon", ring, name, {"name": name} if name else {}))
                found = True
        if not found:
            out.append(Feature(index, "unknown", [], name, {"name": name} if name else {}))
        index += 1
    if not out:
        raise GeometryImportInvalid("KML holds no Placemark")
    return out


def parse_features(data: bytes, filename: str | None = None) -> list[Feature]:
    """Sniff the upload (XML → KML, else JSON) and parse it. Raises `GeometryImportInvalid`."""
    if len(data) > MAX_IMPORT_BYTES:
        raise GeometryImportInvalid(f"Geometry upload exceeds the {MAX_IMPORT_BYTES // (1024 * 1024)} MB cap.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GeometryImportInvalid("Geometry upload is not UTF-8 text (GeoJSON or KML expected).") from exc
    stripped = text.lstrip()
    is_kml = stripped.startswith("<") or (filename or "").lower().endswith(".kml")
    if is_kml:
        return parse_kml(text)
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise GeometryImportInvalid(f"Geometry upload is neither JSON nor XML: {exc}") from exc
    return parse_geojson(obj)


# ── matching ────────────────────────────────────────────────────────────────

def _haversine_m(a: list[float], b: list[float]) -> float:
    phi1, phi2 = math.radians(a[1]), math.radians(b[1])
    dphi = math.radians(b[1] - a[1])
    dlam = math.radians(b[0] - a[0])
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_KM * 1000.0 * math.asin(min(1.0, math.sqrt(h)))


def _nearest_bus(p: list[float], buses: dict[str, tuple[float, float]], tolerance_m: float) -> str | None:
    best, best_d = None, tolerance_m
    for name, (lng, lat) in buses.items():
        d = _haversine_m(p, [lng, lat])
        if d <= best_d:
            best, best_d = name, d
    return best


def _by_name(name: str, props: dict, branches: list[Branch]) -> Branch | None:
    hint = KIND_ALIASES.get(str(props.get("kind") or props.get("class") or props.get("component_class") or ""))
    kinds = (hint,) if hint else KIND_ORDER
    for kind in kinds:
        for b in branches:
            if b.kind == kind and b.name == name:
                return b
    return None


def _orient(coords: list[list[float]], branch: Branch, buses: dict, tolerance_m: float) -> tuple[list[list[float]], bool]:
    """
    Put the vertices in bus0→bus1 order (reversed when the far end is nearer
    bus0), then drop an end vertex that lies within the tolerance of its bus:
    it IS the bus, and the document stores only the interior.
    """
    c0, c1 = buses.get(branch.bus0), buses.get(branch.bus1)
    reversed_ = False
    if c0 is not None and c1 is not None:
        c0l, c1l = [c0[0], c0[1]], [c1[0], c1[1]]
        straight = _haversine_m(coords[0], c0l) + _haversine_m(coords[-1], c1l)
        flipped = _haversine_m(coords[0], c1l) + _haversine_m(coords[-1], c0l)
        if flipped < straight:
            coords = list(reversed(coords))
            reversed_ = True
    pts = list(coords)
    if c0 is not None and pts and _haversine_m(pts[0], [c0[0], c0[1]]) <= tolerance_m:
        pts = pts[1:]
    if c1 is not None and pts and _haversine_m(pts[-1], [c1[0], c1[1]]) <= tolerance_m:
        pts = pts[:-1]
    return pts, reversed_


def _new_site_id(taken: set[str]) -> str:
    while True:
        sid = f"imp_{uuid.uuid4().hex[:8]}"
        if sid not in taken:
            return sid


def match_features(
    features: list[Feature],
    branches: list[Branch],
    buses: dict[str, tuple[float, float]],
    *,
    tolerance_m: float = DEFAULT_TOLERANCE_M,
    existing_site_ids: set[str] | None = None,
) -> dict:
    """
    The match report: `routes` (one per LineString matched by `name` or by
    `endpoints`), `sites` (one per Polygon with ≥ 3 vertices) and `unmatched`
    (everything else, with a reason). `buses` holds PLACED buses only
    (`name → (lng, lat)`). Nothing is written.
    """
    routes: list[dict] = []
    sites: list[dict] = []
    unmatched: list[dict] = []
    taken = set(existing_site_ids or ())
    claimed: set[str] = set()
    for f in features:
        if f.geometry == "LineString":
            if len(f.coords) < 2:
                unmatched.append({"feature": f.index, "label": f.label, "reason": "fewer than two vertices"})
                continue
            branch, how = None, None
            if f.name:
                branch = _by_name(f.name, f.properties, branches)
                how = "name" if branch else None
            if branch is None:
                a = _nearest_bus(f.coords[0], buses, tolerance_m)
                b = _nearest_bus(f.coords[-1], buses, tolerance_m)
                if a and b and a != b:
                    ends = {a, b}
                    branch = next((x for x in branches if {x.bus0, x.bus1} == ends and x.key not in claimed), None)
                    how = "endpoints" if branch else None
                    if branch is None:
                        unmatched.append({"feature": f.index, "label": f.label, "reason": f"no branch joins {a} and {b}"})
                        continue
                else:
                    reason = ("both ends snap to the same bus" if a and b else
                              f"no placed bus within {tolerance_m:g} m of " + ("either end" if not a and not b else "one end"))
                    unmatched.append({"feature": f.index, "label": f.label, "reason": reason})
                    continue
            if branch.key in claimed:
                unmatched.append({"feature": f.index, "label": f.label, "reason": f"{branch.key} already matched by an earlier feature"})
                continue
            claimed.add(branch.key)
            points, reversed_ = _orient(f.coords, branch, buses, tolerance_m)
            routes.append({
                "feature": f.index, "label": f.label, "key": branch.key, "how": how,
                "points": points, "reversed": reversed_,
            })
        elif f.geometry == "Polygon":
            if len(f.coords) < 3:
                unmatched.append({"feature": f.index, "label": f.label, "reason": "polygon needs at least three vertices"})
                continue
            sid = _new_site_id(taken)
            taken.add(sid)
            lng, lat = site_service.centroid(f.coords)
            sites.append({
                "feature": f.index, "id": sid,
                "name": f.name or f"Imported site {len(sites) + 1}",
                "boundary": f.coords,
                "buses": site_service.buses_inside(buses, f.coords),
                "origin": {"lng": lng, "lat": lat},
            })
        else:
            unmatched.append({"feature": f.index, "label": f.label, "reason": f"unsupported geometry {f.geometry}"})
    return {"routes": routes, "sites": sites, "unmatched": unmatched}


# ── apply ───────────────────────────────────────────────────────────────────

def plan_apply(project_dir: pathlib.Path, report: dict) -> dict:
    """
    What `apply_report` would write, validated and not written: `{layout, sites, routes,
    site_ids}` (a document is None when the report has nothing for it). A matched feature
    with no interior vertex is a chord and REMOVES any route the branch had (the import is
    authoritative); sites are appended. Pure: the edit card shows this plan.
    """
    keys: list[str] = []
    layout = None
    if report.get("routes"):
        layout = map_layout_service.read_map_layout(project_dir)
        routes = layout.setdefault("routes", {})
        for r in report["routes"]:
            key = r["key"]
            keys.append(key)
            if r.get("points"):
                routes[key] = {"points": [list(p) for p in r["points"]], "source": "import"}
            else:
                routes.pop(key, None)
        map_layout_service.validate_map_layout(layout)
    ids: list[str] = []
    sdoc = None
    if report.get("sites"):
        sdoc = site_service.read_sites(project_dir)
        existing = {s.get("id") for s in sdoc["sites"] if isinstance(s, dict)}
        for s in report["sites"]:
            sid = s["id"] if s["id"] not in existing else _new_site_id(existing)
            existing.add(sid)
            ids.append(sid)
            sdoc["sites"].append({
                "id": sid, "name": s["name"], "buses": list(s["buses"]),
                "boundary": [list(v) for v in s["boundary"]],
                "origin": {"lng": float(s["origin"]["lng"]), "lat": float(s["origin"]["lat"])},
                "placements": {},
            })
        site_service.validate_sites(sdoc)
    return {"layout": layout, "sites": sdoc, "routes": keys, "site_ids": ids}


def apply_report(project_dir: pathlib.Path, report: dict) -> dict:
    """
    Write the report (see `plan_apply`): routes into `map_layout.json` with
    `source: 'import'`, sites appended to `sites.json`. Both documents are validated
    before they are written. Returns `{routes: [keys], sites: [ids]}`.
    """
    plan = plan_apply(project_dir, report)
    if plan["layout"] is not None:
        map_layout_service.write_map_layout(project_dir, plan["layout"])
    if plan["sites"] is not None:
        site_service.write_sites(project_dir, plan["sites"])
    return {"routes": plan["routes"], "sites": plan["site_ids"]}
