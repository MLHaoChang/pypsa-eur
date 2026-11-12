"""
SC-06-layout: a calibrated raster or GeoJSON layout becomes a native
`import_layout` proposal (issues/07, V39/V40, GK B plan GO 48 L1–L11).

The pure seam is `services.site_scene.layout`: `normalize(raw)` validates,
bounds and strips the submitted layout before any geometry work, and
`generate(site, normalized)` turns it into the frozen building records. The
route tests run the real proposals/apply lifecycle on a real project.
Raster images never reach the server: it receives only the declared frame,
the calibration and the traced pixel rings.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math

import pandas as pd
import pypsa
import pytest

from services import site_service
from services.site_scene import layout, native, proposals
from services.site_scene.contract import SceneError

# ── fixtures ────────────────────────────────────────────────────────────────

ORIGIN = {"lng": 11.58, "lat": 48.14}
SITE = {
    "id": "campus",
    "name": "Campus",
    "buses": [],
    "origin": dict(ORIGIN),
    "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]],
    "placements": {},
}

# 1000 × 800 px drawing; the scale bar is 200 px = 20 m (0.1 m/px).
IMAGE = {"width_px": 1000, "height_px": 800, "media_type": "image/png"}
SCALE = {"a_px": [100, 700], "b_px": [300, 700], "distance": 20, "unit": "m"}
# A 200 × 100 px rectangle (20 × 10 m), its lower-left corner at the anchor.
RECT_PX = [[100, 700], [300, 700], [300, 600], [100, 600]]


def raster(
    rotation=0.0,
    anchor_px=(100, 700),
    anchor_local=(0, 0),
    scale=None,
    footprints=None,
    image=None,
    **extra,
):
    cal = {
        "scale": copy.deepcopy(scale or SCALE),
        "anchor_px": list(anchor_px),
        "anchor_local": list(anchor_local),
        "rotation_deg": rotation,
    }
    return {
        "kind": "raster",
        "image": dict(image or IMAGE),
        "calibration": cal,
        "footprints": footprints
        if footprints is not None
        else [{"id": "hall", "ring_px": RECT_PX, "height_m": 8}],
        **extra,
    }


def _lng(origin_lat, metres):
    return metres / (site_service._M_PER_DEG_LAT * math.cos(math.radians(origin_lat)))


def _lat(metres):
    return metres / site_service._M_PER_DEG_LAT


def rect_lnglat(origin=ORIGIN, e0=0.0, n0=0.0, w=20.0, d=10.0):
    lng, lat = origin["lng"], origin["lat"]
    pts = [(e0, n0), (e0 + w, n0), (e0 + w, n0 + d), (e0, n0 + d), (e0, n0)]
    return [[lng + _lng(lat, e), lat + _lat(n)] for e, n in pts]


def feature(ring, fid="hall", props=None, holes=(), geom_type="Polygon"):
    coords = [ring, *holes]
    if geom_type == "MultiPolygon":
        coords = [coords]
    f = {
        "type": "Feature",
        "geometry": {"type": geom_type, "coordinates": coords},
        "properties": {"height": 8} if props is None else props,
    }
    if fid is not None:
        f["id"] = fid
    return f


def geojson(*features, **members):
    return {
        "kind": "geojson",
        "document": {
            "type": "FeatureCollection",
            "features": list(features),
            **members,
        },
    }


def run(site, raw):
    return layout.generate(site, layout.normalize(raw))


def vertices(building):
    return sorted((round(e, 2), round(n, 2)) for e, n in building["footprint"])


def refused(raw, kind="invalid_input", site=SITE):
    with pytest.raises(SceneError) as exc:
        run(site, raw)
    assert exc.value.kind == kind, exc.value
    return exc.value


def code_of(err):
    return (err.detail or {}).get("code")


# ── V39: calibrated dimensions and vertices (L3) ────────────────────────────


def test_raster_north_up_yields_the_drawn_rectangle():
    out = run(SITE, raster())
    (b,) = out["buildings"]
    assert b["id"] == "layout:hall" and b["source"] == {
        "namespace": "layout",
        "source_id": "hall",
    }
    assert vertices(b) == [(0, 0), (0, 10), (20, 0), (20, 10)]
    assert out["layout"]["metres_per_pixel"] == pytest.approx(0.1)
    assert out["layout"]["extent_m"] == pytest.approx([20, 10])


def test_raster_rotation_anchor_and_local_anchor_place_every_vertex():
    # Drawing "up" points east (90° clockwise from north). Kills: no y-flip,
    # E/N swap, rotation sign, ignored anchor_px or anchor_local.
    out = run(SITE, raster(rotation=90, anchor_px=(100, 700), anchor_local=(50, -30)))
    assert vertices(out["buildings"][0]) == [(50, -50), (50, -30), (60, -50), (60, -30)]


def test_raster_half_turn_and_offset_anchor_pixel():
    # Anchor pixel at the rectangle's top-right; drawing turned 180°.
    out = run(SITE, raster(rotation=180, anchor_px=(300, 600), anchor_local=(5, 7)))
    assert vertices(out["buildings"][0]) == [(5, 7), (5, 17), (25, 7), (25, 17)]


def test_raster_rotation_30_degrees_keeps_size_and_turns_clockwise():
    out = run(SITE, raster(rotation=30))
    fp = out["buildings"][0]["footprint"]
    s, c = math.sin(math.radians(30)), math.cos(math.radians(30))
    expected = sorted(
        (round(dx * c + dy * s, 2), round(-dx * s + dy * c, 2))
        for dx, dy in [(0, 0), (20, 0), (20, 10), (0, 10)]
    )
    assert sorted((round(e, 2), round(n, 2)) for e, n in fp) == expected


@pytest.mark.parametrize(
    "unit,distance", [("m", 20), ("mm", 20_000), ("ft", 20 / 0.3048)]
)
def test_scale_units_agree(unit, distance):
    out = run(SITE, raster(scale={**SCALE, "unit": unit, "distance": distance}))
    assert vertices(out["buildings"][0]) == [(0, 0), (0, 10), (20, 0), (20, 10)]


def test_geojson_rectangle_lands_at_its_metric_offset():
    out = run(SITE, geojson(feature(rect_lnglat(e0=30, n0=-40))))
    (b,) = out["buildings"]
    assert vertices(b) == [(30, -40), (30, -30), (50, -40), (50, -30)]
    assert out["layout"]["crs"] == "CRS84" and out["layout"]["metres_per_pixel"] is None


def test_geojson_at_60_north_uses_the_cosine_of_latitude():
    # Hand-checked: 0.001° of longitude at 60°N is 111 319.49 m × cos 60° × 0.001 = 55.66 m.
    site = {**SITE, "origin": {"lng": 10.0, "lat": 60.0}}
    ring = [
        [10.0, 60.0],
        [10.001, 60.0],
        [10.001, 60.0001],
        [10.0, 60.0001],
        [10.0, 60.0],
    ]
    (b,) = run(site, geojson(feature(ring)))["buildings"]
    assert max(e for e, _ in b["footprint"]) == pytest.approx(55.66, abs=0.01)
    assert max(n for _, n in b["footprint"]) == pytest.approx(11.13, abs=0.01)


def test_a_single_feature_document_is_accepted():
    raw = {"kind": "geojson", "document": feature(rect_lnglat())}
    assert [b["id"] for b in run(SITE, raw)["buildings"]] == ["layout:hall"]


# ── V39: absent scale/CRS named; unsupported formats not silently accepted ──


def test_missing_scale_is_named():
    raw = raster()
    del raw["calibration"]["scale"]
    assert code_of(refused(raw)) == "scale_missing"


@pytest.mark.parametrize("key", ["anchor_px", "anchor_local", "rotation_deg"])
def test_missing_placement_is_named(key):
    raw = raster()
    del raw["calibration"][key]
    assert code_of(refused(raw)) == "placement_missing"


def test_unknown_scale_unit_is_refused_not_guessed():
    err = refused(raster(scale={**SCALE, "unit": "px"}))
    assert err.detail["field"] == "unit"
    no_unit = raster()
    del no_unit["calibration"]["scale"]["unit"]
    assert refused(no_unit).detail["field"] == "unit"


@pytest.mark.parametrize(
    "distance,code", [(0, "scale_distance"), (-5, "scale_distance")]
)
def test_zero_or_negative_scale_distance_is_named(distance, code):
    assert code_of(refused(raster(scale={**SCALE, "distance": distance}))) == code


@pytest.mark.parametrize("distance", [float("inf"), float("nan"), "20 m", None])
def test_non_finite_scale_distance_is_refused(distance):
    assert (
        refused(raster(scale={**SCALE, "distance": distance})).detail["field"]
        == "value"
    )


def test_coincident_scale_points_are_named():
    assert (
        code_of(refused(raster(scale={**SCALE, "b_px": [100, 700]})))
        == "scale_points_equal"
    )


@pytest.mark.parametrize("rotation", [float("inf"), float("nan"), "90", True])
def test_non_finite_rotation_is_named(rotation):
    assert code_of(refused(raster(rotation=rotation))) == "rotation_invalid"


@pytest.mark.parametrize(
    "name,crs",
    [
        (
            "EPSG:25832",
            {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25832"}},
        ),
        ("EPSG:3857", {"type": "name", "properties": {"name": "EPSG:3857"}}),
    ],
)
def test_a_projected_crs_is_named(name, crs):
    err = refused(geojson(feature(rect_lnglat()), crs=crs))
    assert code_of(err) == "crs_unsupported" and name.split(":")[1] in err.message


def test_crs84_named_crs_is_accepted():
    crs = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    assert len(run(SITE, geojson(feature(rect_lnglat()), crs=crs))["buildings"]) == 1


def test_projected_coordinates_without_a_crs_member_are_named():
    ring = [[691000, 5334000], [691020, 5334000], [691020, 5334010], [691000, 5334000]]
    assert code_of(refused(geojson(feature(ring)))) == "crs_mismatch"


def test_a_latitude_out_of_range_is_named():
    # GK B F6: the latitude half of the range check, with an in-range longitude.
    ring = rect_lnglat()
    ring[1] = [ring[1][0], 95.0]
    assert code_of(refused(geojson(feature(ring)))) == "crs_mismatch"


def test_a_layout_far_from_the_site_is_named():
    far = rect_lnglat(e0=10_500, n0=0)
    assert code_of(refused(geojson(feature(far)))) == "far_from_site"
    near = rect_lnglat(e0=9_900, n0=0, w=20)
    assert len(run(SITE, geojson(feature(near)))["buildings"]) == 1


def test_one_vertex_beyond_10_km_is_named():
    # GK B F6: any vertex, not every vertex, beyond 10 km refuses the building.
    ring = rect_lnglat(e0=9_990, n0=0, w=20)
    assert code_of(refused(geojson(feature(ring)))) == "far_from_site"


@pytest.mark.parametrize("kind", ["dxf", "ifc", "pdf", "svg", "DXF"])
def test_cad_and_vector_formats_are_not_accepted(kind):
    err = refused({"kind": kind})
    assert code_of(err) == "unsupported_format" and "GeoJSON" in err.message


@pytest.mark.parametrize(
    "media_type", ["image/svg+xml", "application/pdf", "image/tiff", "", None]
)
def test_raster_media_types_outside_png_jpeg_webp_are_not_accepted(media_type):
    assert (
        code_of(refused(raster(image={**IMAGE, "media_type": media_type})))
        == "unsupported_format"
    )


@pytest.mark.parametrize("media_type", ["image/png", "image/jpeg", "image/webp"])
def test_png_jpeg_webp_are_accepted(media_type):
    assert run(SITE, raster(image={**IMAGE, "media_type": media_type}))["buildings"]


@pytest.mark.parametrize("kind", [5, None, ["dxf"]])
def test_a_kind_that_is_not_text_is_refused(kind):
    assert code_of(refused({"kind": kind})) == "layout_kind"


@pytest.mark.parametrize(
    "document",
    [
        [],
        "x",
        {"type": "Polygon", "coordinates": []},
        {"type": "FeatureCollection", "features": {}},
        {"type": "FeatureCollection"},
    ],
)
def test_a_document_that_is_not_a_feature_collection_is_named(document):
    assert code_of(refused({"kind": "geojson", "document": document})) == (
        "layout_document"
    )


@pytest.mark.parametrize(
    "where,value", [("image", "x"), ("image", None), ("footprints", {})]
)
def test_a_drawing_with_an_unreadable_image_or_footprints_is_named(where, value):
    raw = raster()
    raw[where] = value
    err = refused(raw)
    assert code_of(err) == ("layout_image" if where == "image" else "layout_footprints")
    assert err.detail["field"] == where


def test_an_unknown_kind_is_refused():
    assert refused({"kind": "shapefile"}).detail["field"] == "layout"
    assert refused("not an object").detail["field"] == "layout"


def test_polygon_holes_are_refused_by_name():
    hole = rect_lnglat(e0=5, n0=2, w=2, d=2)
    err = refused(geojson(feature(rect_lnglat(), holes=[hole])))
    assert code_of(err) == "layout_hole" and "hall" in err.message


def test_non_polygon_features_are_skipped_and_counted():
    line = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": rect_lnglat()[:2]},
        "properties": {},
    }
    empty = {"type": "Feature", "geometry": None, "properties": {}}
    out = run(SITE, geojson(feature(rect_lnglat()), line, empty))
    assert len(out["buildings"]) == 1
    assert out["layout"]["skipped"] == {"LineString": 1, "null": 1}


def test_a_layout_with_no_footprints_is_named():
    line = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": rect_lnglat()[:2]},
        "properties": {},
    }
    assert code_of(refused(geojson(line))) == "layout_empty"
    assert code_of(refused(raster(footprints=[]))) == "layout_empty"


# ── V40: unsafe / oversized / remote-linked inputs fail before geometry ─────


@pytest.mark.parametrize(
    "member",
    [
        {
            "crs": {
                "type": "link",
                "properties": {"href": "http://example.org/crs", "type": "proj4"},
            }
        },
        {"$ref": "http://example.org/other.geojson"},
        {
            "crs": {"type": "link", "properties": {"type": "proj4"}}
        },  # a link with no href still names a remote CRS
    ],
)
def test_remote_references_are_refused(member):
    assert (
        code_of(refused(geojson(feature(rect_lnglat()), **member)))
        == "remote_reference"
    )


def test_a_drawing_anchored_far_off_the_site_is_named():
    assert code_of(refused(raster(anchor_local=(10_500, 0)))) == "far_from_site"
    assert run(SITE, raster(anchor_local=(9_000, 0)))["buildings"]


def test_an_href_inside_properties_is_not_a_reference():
    # Properties are never fetched or stored (L2): only height, levels and id are read.
    f = feature(
        rect_lnglat(), props={"height": 8, "href": "http://example.org/photo.jpg"}
    )
    assert run(SITE, geojson(f))["buildings"]


def test_a_nested_remote_reference_is_refused():
    f = feature(rect_lnglat())
    f["geometry"]["$ref"] = "file:///etc/passwd"
    assert code_of(refused(geojson(f))) == "remote_reference"


def test_a_feature_with_its_own_projected_crs_is_named():
    # GK B F4: a crs member on a Feature is checked like the document's.
    f = feature(rect_lnglat())
    f["crs"] = {"type": "name", "properties": {"name": "EPSG:25832"}}
    assert code_of(refused(geojson(f))) == "crs_unsupported"


def _nested(depth):
    node: list = []
    for _ in range(depth - 1):
        node = [node]
    return node


def test_a_document_nested_too_deeply_is_named():
    # GK B F5: the document is depth 0, so a member holding 65 nested lists
    # reaches depth 65; 64 is the deepest accepted.
    assert (
        code_of(refused(geojson(feature(rect_lnglat()), extra=_nested(65))))
        == "layout_too_deep"
    )
    assert run(SITE, geojson(feature(rect_lnglat()), extra=_nested(64)))["buildings"]


def test_echoed_client_text_is_bounded():
    # GK B F9: a crs name or media type is quoted back short, whatever its size.
    crs = {"type": "name", "properties": {"name": "E" * 10_000}}
    err = refused(geojson(feature(rect_lnglat()), crs=crs))
    assert code_of(err) == "crs_unsupported" and len(err.message) < 200
    err = refused(raster(image={**IMAGE, "media_type": "x" * 10_000}))
    assert code_of(err) == "unsupported_format" and len(err.message) < 200
    err = refused(raster(image={**IMAGE, "media_type": ["image/png"] * 1_000}))
    assert "unknown" in err.message and len(err.message) < 200


def test_an_image_over_50_megapixels_is_413():
    err = refused(
        raster(image={**IMAGE, "width_px": 10_000, "height_px": 6_000}),
        kind="resource_limit",
    )
    assert (err.detail["limit"], err.detail["max"], err.detail["actual"]) == (
        "decoded_pixels",
        50_000_000,
        60_000_000,
    )


def test_exactly_50_megapixels_is_admitted_and_one_pixel_more_is_413():
    # GK B F6: 10,000 × 5,000 is the limit; 3,561 × 14,041 is 50,000,001 px.
    assert run(SITE, raster(image={**IMAGE, "width_px": 10_000, "height_px": 5_000}))[
        "buildings"
    ]
    err = refused(
        raster(image={**IMAGE, "width_px": 3_561, "height_px": 14_041}),
        kind="resource_limit",
    )
    assert (err.detail["limit"], err.detail["actual"]) == (
        "decoded_pixels",
        50_000_001,
    )


def test_an_image_side_over_20000_px_is_413():
    err = refused(
        raster(image={**IMAGE, "width_px": 20_001, "height_px": 10}),
        kind="resource_limit",
    )
    assert err.detail["limit"] == "image_side"


@pytest.mark.parametrize("w,h", [(0, 10), (-1, 10), (10.5, 10), (True, 10), ("10", 10)])
def test_image_dimensions_must_be_positive_integers(w, h):
    assert (
        refused(raster(image={**IMAGE, "width_px": w, "height_px": h})).detail["field"]
        == "image"
    )


@pytest.mark.parametrize("where", ["ring", "a_px", "b_px", "anchor_px"])
def test_pixels_outside_the_declared_image_are_named(where):
    raw = raster()
    if where == "ring":
        raw["footprints"][0]["ring_px"] = [[100, 700], [1001, 700], [300, 600]]
    elif where == "anchor_px":
        raw["calibration"]["anchor_px"] = [-1, 0]
    else:
        raw["calibration"]["scale"][where] = [100, 801]
    assert code_of(refused(raw)) == "outside_image"


def test_pixel_on_the_image_edge_is_inside():
    raw = raster(
        footprints=[
            {"id": "edge", "ring_px": [[0, 0], [1000, 0], [1000, 800]], "height_m": 3}
        ]
    )
    assert run(SITE, raw)["buildings"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "1", None, True])
def test_non_finite_coordinates_are_refused(bad):
    # GK B F3: each finite check names its own code; nothing reaches geometry.
    ring = rect_lnglat()
    ring[1] = [bad, ring[1][1]]
    assert code_of(refused(geojson(feature(ring)))) == "layout_coordinates"
    raw = raster()
    raw["footprints"][0]["ring_px"] = [[100, 700], [bad, 700], [300, 600]]
    assert code_of(refused(raw)) == "layout_coordinates"
    assert code_of(refused(raster(anchor_local=(0, bad)))) == "layout_coordinates"


def _spy_geometry(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("geometry ran before admission")

    monkeypatch.setattr(native, "check_footprint", boom)
    monkeypatch.setattr(layout.frames, "lnglat_to_local", boom)
    return calls


def test_too_many_footprints_is_413_before_geometry(monkeypatch):
    feats = [
        feature(rect_lnglat(), fid=f"b{i}") for i in range(native.MAX_BUILDINGS + 1)
    ]
    calls = _spy_geometry(monkeypatch)
    err = refused(geojson(*feats), kind="resource_limit")
    assert err.detail["limit"] == "buildings" and calls == []


def test_too_many_vertices_is_413_before_geometry(monkeypatch):
    n = native.MAX_FOOTPRINT_VERTICES + 1
    ring = [
        [
            500 + 100 * math.cos(2 * math.pi * k / n),
            400 + 100 * math.sin(2 * math.pi * k / n),
        ]
        for k in range(n)
    ]
    calls = _spy_geometry(monkeypatch)
    err = refused(
        raster(footprints=[{"id": "round", "ring_px": ring}]), kind="resource_limit"
    )
    assert err.detail["limit"] == "footprint_vertices" and calls == []


def test_a_closed_ring_at_exactly_the_vertex_limit_is_admitted():
    # GK B F7: the closing vertex repeats the first and is not counted.
    n = native.MAX_FOOTPRINT_VERTICES
    ring = [
        [
            500 + 100 * math.cos(2 * math.pi * k / n),
            400 + 100 * math.sin(2 * math.pi * k / n),
        ]
        for k in range(n)
    ]
    out = layout.normalize(
        raster(footprints=[{"id": "round", "ring_px": [*ring, ring[0]]}])
    )
    assert len(out["footprints"][0]["ring_px"]) == n + 1


@pytest.mark.parametrize("kind", ["geojson", "raster"])
def test_too_many_buildings_is_413_before_geometry(monkeypatch, kind):
    # Survivors of mutants.py: each input path has its own building-count cap.
    n = native.MAX_BUILDINGS + 1
    raw = (
        geojson(*[feature(rect_lnglat(), fid=f"b{i}") for i in range(n)])
        if kind == "geojson"
        else raster(footprints=[{"id": f"b{i}", "ring_px": RECT_PX} for i in range(n)])
    )
    calls = _spy_geometry(monkeypatch)
    err = refused(raw, kind="resource_limit")
    assert (err.detail["limit"], err.detail["actual"]) == ("buildings", n)
    assert calls == []


def test_skipped_features_do_not_count_towards_the_building_cap():
    # GK B G2: the cap counts footprints, so MAX polygons plus one Point is admitted.
    n = native.MAX_BUILDINGS
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [ORIGIN["lng"], ORIGIN["lat"]]},
        "properties": {},
    }
    raw = geojson(*[feature(rect_lnglat(), fid=f"b{i}") for i in range(n)], point)
    out = layout.normalize(raw)
    assert len(out["footprints"]) == n and out["skipped"] == {"Point": 1}


def test_too_many_segment_pairs_is_413_before_geometry(monkeypatch):
    n = native.MAX_FOOTPRINT_VERTICES
    ring = [
        [
            500 + 100 * math.cos(2 * math.pi * k / n),
            400 + 100 * math.sin(2 * math.pi * k / n),
        ]
        for k in range(n)
    ]
    count = native.MAX_SEGMENT_PAIRS // native.segment_pairs(n) + 1
    fps = [{"id": f"r{i}", "ring_px": ring} for i in range(count)]
    calls = _spy_geometry(monkeypatch)
    err = refused(raster(footprints=fps), kind="resource_limit")
    assert err.detail["limit"] == "segment_pairs" and calls == []


def test_too_many_triangles_is_413_before_geometry(monkeypatch):
    monkeypatch.setattr(native, "MAX_TRIANGLES", 10)
    calls = _spy_geometry(monkeypatch)
    err = refused(
        raster(
            footprints=[
                {"id": "a", "ring_px": RECT_PX},
                {"id": "b", "ring_px": RECT_PX},
            ]
        ),
        kind="resource_limit",
    )
    assert (err.detail["limit"], err.detail["actual"]) == (
        "triangles",
        24,
    ) and calls == []


def test_preview_bytes_limit_applies_to_layouts(monkeypatch):
    monkeypatch.setattr(native, "MAX_PREVIEW_BYTES", 100)
    err = refused(raster(), kind="resource_limit")
    assert err.detail["limit"] == "preview_bytes"


# ── L5: ids ─────────────────────────────────────────────────────────────────


def test_multipolygon_parts_get_distinct_ids():
    a, b = rect_lnglat(), rect_lnglat(e0=40)
    f = {
        "type": "Feature",
        "id": "wing",
        "properties": {},
        "geometry": {"type": "MultiPolygon", "coordinates": [[a], [b]]},
    }
    assert [x["id"] for x in run(SITE, geojson(f))["buildings"]] == [
        "layout:wing#1",
        "layout:wing#2",
    ]


def test_a_single_part_multipolygon_keeps_its_id():
    out = run(SITE, geojson(feature(rect_lnglat(), geom_type="MultiPolygon")))
    assert [x["id"] for x in out["buildings"]] == ["layout:hall"]


def test_missing_ids_fall_back_to_properties_then_the_index():
    f1 = feature(rect_lnglat(), fid=None, props={"id": 7})
    f2 = feature(rect_lnglat(e0=40), fid=None, props={})
    out = run(SITE, geojson(f1, f2))
    assert [x["id"] for x in out["buildings"]] == ["layout:7", "layout:f2"]


@pytest.mark.parametrize("where", ["member", "properties"])
def test_a_geojson_id_over_128_characters_is_refused_not_renamed(where):
    # GK B F7: like a traced name, a long id is refused by name, never replaced by f<i>.
    long_id = "x" * 129
    f = (
        feature(rect_lnglat(), fid=long_id)
        if where == "member"
        else feature(rect_lnglat(), fid=None, props={"id": long_id})
    )
    err = refused(geojson(f))
    assert code_of(err) == "layout_document" and "feature 1" in err.message
    assert run(SITE, geojson(feature(rect_lnglat(), fid="x" * 128)))["buildings"]


def test_an_index_fallback_colliding_with_a_real_id_is_refused_by_name():
    f1 = feature(rect_lnglat(), fid="f2")
    f2 = feature(rect_lnglat(e0=40), fid=None, props={})
    err = refused(geojson(f1, f2))
    assert "duplicate" in err.message and "layout:f2" in err.message


def test_raster_footprint_ids_must_be_unique():
    fps = [{"id": "a", "ring_px": RECT_PX}, {"id": "a", "ring_px": RECT_PX}]
    assert "duplicate" in refused(raster(footprints=fps)).message


@pytest.mark.parametrize("fid", ["", " ", "x" * 129, ["a"]])
def test_raster_footprint_ids_must_be_short_non_empty_text(fid):
    assert (
        refused(raster(footprints=[{"id": fid, "ring_px": RECT_PX}])).detail["field"]
        == "footprints"
    )


# ── L6: heights ─────────────────────────────────────────────────────────────


def _height(props, kind="geojson"):
    if kind == "geojson":
        return run(SITE, geojson(feature(rect_lnglat(), props=props)))["buildings"][0]
    return run(SITE, raster(footprints=[{"id": "hall", "ring_px": RECT_PX, **props}]))[
        "buildings"
    ][0]


@pytest.mark.parametrize("kind,key", [("geojson", "height"), ("raster", "height_m")])
def test_height_in_metres_is_tagged(kind, key):
    b = _height({key: 12.5}, kind)
    assert (b["height_m"], b["height_evidence"]) == (12.5, "tagged")


@pytest.mark.parametrize("key", ["building:levels", "levels"])
def test_levels_use_the_d08_storey_height(key):
    b = _height({key: 4})
    assert (b["height_m"], b["height_evidence"], b["levels"]) == (
        4 * native.LEVEL_HEIGHT_M,
        "levels",
        4,
    )


def test_no_height_falls_back_with_evidence():
    b = _height({})
    assert (b["height_m"], b["height_evidence"]) == (
        native.FALLBACK_HEIGHT_M,
        "fallback",
    )


@pytest.mark.parametrize("value", ["12 m", "12", True])
def test_a_text_height_is_refused_not_parsed(value):
    err = refused(geojson(feature(rect_lnglat(), props={"height": value})))
    assert code_of(err) == "height_not_number" and "hall" in err.message


@pytest.mark.parametrize("value", [-3, 0, 1001, float("inf"), float("nan")])
def test_a_negative_zero_huge_or_non_finite_height_is_refused(value):
    err = refused(
        geojson(feature(rect_lnglat(), props={"height": value})),
        kind="invalid_geometry",
    )
    assert "height" in err.message


@pytest.mark.parametrize("levels", [0, -1, 2.5, "3", float("nan")])
def test_bad_levels_are_refused(levels):
    err = refused(geojson(feature(rect_lnglat(), props={"levels": levels})))
    assert code_of(err) == "levels_invalid"


# ── L2/V55: only the used fields are kept ───────────────────────────────────

MARK = "MARK-c0ffee-private"


def _marked_geojson():
    f = feature(
        rect_lnglat(),
        props={"height": 8, "owner": MARK, "p_nom": 999, "marginal_cost": 1},
    )
    f["foreign"] = MARK
    return geojson(f, name=MARK, filename=f"/home/{MARK}/plan.geojson")


def test_normalize_keeps_only_the_used_fields():
    norm = layout.normalize(_marked_geojson())
    assert MARK not in json.dumps(norm)
    norm = layout.normalize(raster(filename=MARK, image={**IMAGE, "name": MARK}))
    assert MARK not in json.dumps(norm)


# ── L7: generate() unchanged by the shared admission ────────────────────────


def test_native_generate_output_is_unchanged():
    inputs = {
        "greenfield": [
            {
                "id": "hall",
                "center": [0, 0],
                "width": 10,
                "depth": 20,
                "height": 8,
                "heading": 0,
                "unit": "m",
            }
        ],
        "sources": [
            {
                "namespace": "osm",
                "source_id": "7",
                "ring": rect_lnglat()[:-1],
                "levels": 2,
            }
        ],
    }
    out = native.generate(SITE, inputs)
    digest = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    # Recorded on master 701328bd9 before the refactor.
    assert digest == NATIVE_DIGEST


NATIVE_DIGEST = "e86a946c331e73af8ad4d122bf0765ecb6cc31b492a3fbf532d9ecaf06aef9ec"


# ══ the real routes ════════════════════════════════════════════════════════


def _empty_network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=24, freq="h"))
    return n


@pytest.fixture
def geo(client, install_network, project_storage_dir):
    install_network(_empty_network(), name="geo")
    assert (
        client.post(
            "/api/projects/geo", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    return project_storage_dir("geo")


URL = "/api/projects/geo/sites/campus/scene"


def _adopted(client, project, site=SITE):
    from services.site_scene import store

    r = client.put(
        "/api/projects/geo/sites", json={"version": 1, "sites": [copy.deepcopy(site)]}
    )
    assert r.status_code == 200, r.text
    digest = store.legacy_site_digest(site_service.read_sites(project), "campus")
    r = client.post(f"{URL}/adopt", json={"expected_digest": digest})
    assert r.status_code == 200, r.text


def _propose(client, base, raw, action="import_layout"):
    return client.post(
        f"{URL}/proposals",
        json={"action": action, "base_revision": base, "inputs": {"layout": raw}},
    )


def _apply(client, pid, expected):
    return client.post(
        f"{URL}/proposals/{pid}/apply", json={"expected_revision": expected}
    )


def _detail(r):
    return r.json()["detail"]


def _nc(project):
    return hashlib.sha256((project / "network.nc").read_bytes()).hexdigest()


def test_route_preview_then_explicit_apply_then_retry(client, geo):
    _adopted(client, geo)
    nc = _nc(geo)
    r = _propose(client, 1, raster())
    assert r.status_code == 200, r.text
    prop = r.json()
    assert (
        prop["action"] == "import_layout"
        and prop["preview"]["layout"]["metres_per_pixel"] == 0.1
    )
    assert [b["id"] for b in prop["preview"]["buildings"]] == ["layout:hall"]
    # V36/V78: the preview writes nothing; nothing applies on its own.
    assert (
        client.get(URL).json()["revision"] == 1
        and client.get(URL).json()["buildings"] == []
    )
    receipt = _apply(client, prop["id"], 1)
    assert receipt.status_code == 200, receipt.text
    scene = client.get(URL).json()
    assert scene["revision"] == 2 and vertices(scene["buildings"][0]) == [
        (0, 0),
        (0, 10),
        (20, 0),
        (20, 10),
    ]
    assert scene["buildings"][0]["height_evidence"] == "tagged"
    again = _apply(client, prop["id"], 1)
    assert again.status_code == 200 and again.json() == receipt.json()
    assert client.get(URL).json()["revision"] == 2
    assert _nc(geo) == nc  # V38


def test_route_import_merges_by_id_and_keeps_other_buildings(client, geo):
    _adopted(client, geo)
    box = {
        "id": "shed",
        "center": [100, 100],
        "width": 5,
        "depth": 5,
        "height": 3,
        "heading": 0,
        "unit": "m",
    }
    r = client.post(
        f"{URL}/proposals",
        json={
            "action": "generate_native",
            "base_revision": 1,
            "inputs": {"greenfield": [box]},
        },
    )
    _apply(client, r.json()["id"], 1)
    pid = _propose(client, 2, raster()).json()["id"]
    _apply(client, pid, 2)
    other = raster(footprints=[{"id": "annex", "ring_px": RECT_PX, "height_m": 4}])
    kept = _propose(client, 3, other).json()["preview"]["layout"]["kept_layout_ids"]
    assert kept == ["layout:hall"]  # V78: said before Apply
    both = raster(
        footprints=[
            {"id": "hall", "ring_px": RECT_PX, "height_m": 8},
            {"id": "annex", "ring_px": RECT_PX, "height_m": 4},
        ]
    )
    # GK B F1: a re-imported id is replaced, so it is not listed as kept.
    assert (
        _propose(client, 3, both).json()["preview"]["layout"]["kept_layout_ids"] == []
    )
    pid = _propose(client, 3, raster(anchor_local=(1, 1))).json()[
        "id"
    ]  # re-import replaces by id
    _apply(client, pid, 3)
    ids = sorted(b["id"] for b in client.get(URL).json()["buildings"])
    assert ids == ["greenfield:shed", "layout:hall"]
    hall = next(
        b for b in client.get(URL).json()["buildings"] if b["id"] == "layout:hall"
    )
    assert vertices(hall)[0] == (1, 1)


def test_route_manual_correction_keeps_the_layout_id(client, geo):
    _adopted(client, geo)
    _apply(client, _propose(client, 1, raster()).json()["id"], 1)
    fixed = {
        "namespace": "layout",
        "source_id": "hall",
        "ring": rect_lnglat(w=22)[:-1],
        "height_m": 9,
    }
    r = client.post(
        f"{URL}/proposals",
        json={
            "action": "edit_buildings",
            "base_revision": 2,
            "inputs": {"sources": [fixed]},
        },
    )
    assert r.status_code == 200, r.text
    _apply(client, r.json()["id"], 2)
    (b,) = client.get(URL).json()["buildings"]
    assert b["id"] == "layout:hall" and b["height_m"] == 9
    assert max(e for e, _ in b["footprint"]) == pytest.approx(22, abs=0.01)


def test_route_stale_proposal_is_refused_and_keeps_the_scene(client, geo):
    _adopted(client, geo)
    first = _propose(client, 1, raster()).json()["id"]
    second = _propose(client, 1, raster(anchor_local=(5, 5))).json()["id"]
    _apply(client, first, 1)
    before = client.get(URL).json()
    r = _apply(client, second, 1)
    assert (r.status_code, _detail(r)["error_kind"]) == (409, "revision_conflict")
    assert client.get(URL).json() == before
    r = _propose(client, 1, raster())
    assert (r.status_code, _detail(r)["error_kind"]) == (409, "revision_conflict")


def test_route_cancelled_layout_cannot_be_applied(client, geo):
    _adopted(client, geo)
    pid = _propose(client, 1, raster()).json()["id"]
    assert client.post(f"{URL}/proposals/{pid}/cancel").status_code == 200
    r = _apply(client, pid, 1)
    assert (r.status_code, _detail(r)["error_kind"]) == (409, "proposal_cancelled")
    assert client.get(URL).json()["buildings"] == []


def test_route_errors_carry_kind_status_and_code(client, geo):
    _adopted(client, geo)
    r = _propose(client, 1, {"kind": "dxf"})
    assert (r.status_code, _detail(r)["error_kind"], _detail(r)["code"]) == (
        422,
        "invalid_input",
        "unsupported_format",
    )
    r = _propose(
        client, 1, raster(image={**IMAGE, "width_px": 10_000, "height_px": 6_000})
    )
    assert (r.status_code, _detail(r)["error_kind"], _detail(r)["limit"]) == (
        413,
        "resource_limit",
        "decoded_pixels",
    )


def test_route_import_layout_takes_only_the_layout_input(client, geo):
    _adopted(client, geo)
    r = client.post(
        f"{URL}/proposals",
        json={
            "action": "import_layout",
            "base_revision": 1,
            "inputs": {"layout": raster(), "greenfield": []},
        },
    )
    assert (r.status_code, _detail(r)["field"]) == (422, "inputs")


def test_route_body_over_the_cap_is_refused_before_parsing(client, geo, monkeypatch):
    # L1: the route's `_json_body` cap; the bytes are not JSON, so parsing them would 422.
    from routers import site_scene as route

    _adopted(client, geo)
    over = b"{" * (route.MAX_PROPOSAL_BODY + 1)
    r = client.post(
        f"{URL}/proposals", content=over, headers={"content-type": "application/json"}
    )
    assert (r.status_code, _detail(r)["limit"]) == (413, "request_bytes")


def test_route_foreign_lock_refuses_propose_and_apply(
    client, geo, _auth_db, seeded_identity, second_identity, project_row
):
    from services import project_locks

    _adopted(client, geo)
    pid = _propose(client, 1, raster()).json()["id"]
    _engine, session_local = _auth_db
    with session_local() as db:
        project_locks.release_lock(
            db, project_row("geo").id, seeded_identity["user_id"]
        )
        assert (
            project_locks.acquire_lock(
                db, project_row("geo").id, second_identity["user_id"]
            )
            is not None
        )
    try:
        r = _propose(client, 1, raster())
        assert (r.status_code, _detail(r)["error_kind"]) == (409, "project_locked")
        r = _apply(client, pid, 1)
        assert (r.status_code, _detail(r)["error_kind"]) == (409, "project_locked")
        assert client.get(URL).json()["revision"] == 1
    finally:
        with session_local() as db:
            project_locks.release_lock(
                db, project_row("geo").id, second_identity["user_id"]
            )
            db.commit()


def test_route_another_org_cannot_propose(client, other_org_client, geo):
    _adopted(client, geo)
    r = other_org_client.post(
        f"{URL}/proposals",
        json={
            "action": "import_layout",
            "base_revision": 1,
            "inputs": {"layout": raster()},
        },
    )
    assert r.status_code == 404  # require_project_access never answers 403
    assert client.get(URL).json()["revision"] == 1


def test_route_v55_no_marked_input_reaches_the_proposal_or_the_project(client, geo):
    _adopted(client, geo)
    r = _propose(client, 1, _marked_geojson())
    assert r.status_code == 200, r.text
    assert MARK not in r.text
    entry = proposals._entries[
        next(k for k in proposals._entries if k[1] == r.json()["id"])
    ]
    assert MARK not in json.dumps(entry, default=str)
    assert _apply(client, r.json()["id"], 1).status_code == 200
    for path in geo.rglob("*"):
        if path.is_file():
            assert MARK.encode() not in path.read_bytes(), path
    (b,) = client.get(URL).json()["buildings"]
    assert set(b) == {
        "id",
        "source",
        "footprint",
        "height_m",
        "height_evidence",
        "levels",
    }


def test_route_only_import_layout_is_invisible_to_the_chat(
    client, geo, project_row, monkeypatch
):
    # GK B F2: import_layout is route-only, so a route-made preview is neither
    # listed by the chat read nor applied or cancelled through the chat tools.
    from fastapi import HTTPException

    from services import chat_tools
    from services.pypsa_service import PyPSAService

    real = PyPSAService.get_active_context()

    class _Foreground:  # `geo` in the foreground, for the chat tools and /flow
        project_uuid = str(project_row("geo").id)
        storage_dir = str(geo)

        def __getattr__(self, name):
            return getattr(real, name)

    foreground = _Foreground()
    monkeypatch.setattr(
        PyPSAService, "get_active_context", classmethod(lambda cls: foreground)
    )
    _adopted(client, geo)
    prop = _propose(client, 1, raster()).json()
    out = chat_tools.site_scene_read("campus", prop["id"])
    assert "my_previews" not in out
    assert out["proposal"] == {"id": prop["id"], "status": "not_found"}
    assert prop["input_hash"] not in json.dumps(out)
    with pytest.raises(HTTPException) as exc:
        chat_tools.site_scene_apply("campus", prop["id"], prop["input_hash"], 1)
    assert exc.value.status_code == 404
    assert chat_tools.site_scene_cancel("campus", prop["id"])["status"] == "not_found"
    scene = client.get(URL).json()
    assert scene["revision"] == 1 and scene["buildings"] == []
    # GK B G3: the /flow verdict offers no chat apply for a route-only preview.
    from services.site_scene import chat

    key, owner = _entry_key_owner(prop)
    assert proposals.has_ready(key, owner)
    assert not proposals.has_ready(key, owner, chat.CHAT_ACTIONS)
    flow = client.get("/api/projects/geo/flow")
    assert flow.status_code == 200, flow.text
    (apply_verdict,) = [
        a for a in flow.json()["scene"]["actions"] if a["action"] == "apply_proposal"
    ]
    assert apply_verdict["available"] is False
    # The route still applies its own preview.
    receipt = _apply(client, prop["id"], 1)
    assert receipt.status_code == 200 and receipt.json()["route_only"] is True
    live = (
        chat_tools.site_scene_read("campus", prop["id"])["proposal"],
        _chat_apply_status(chat_tools, prop),
    )
    # GK B G1: after eviction or a restart the chain receipt answers the same.
    proposals.clear()
    after = (
        chat_tools.site_scene_read("campus", prop["id"])["proposal"],
        _chat_apply_status(chat_tools, prop),
    )
    assert live == after == ({"id": prop["id"], "status": "not_found"}, 404)


def _entry_key_owner(prop):
    return next(
        (key, e["owner"])
        for (key, pid), e in proposals._entries.items()
        if pid == prop["id"]
    )


def _chat_apply_status(chat_tools, prop):
    from fastapi import HTTPException

    try:
        chat_tools.site_scene_apply("campus", prop["id"], prop["input_hash"], 1)
    except HTTPException as exc:
        return exc.status_code
    return 200


def test_route_import_layout_without_a_layout_is_named(client, geo):
    # Survivor of mutants.py on proposals.py: the missing input is refused by name.
    _adopted(client, geo)
    r = client.post(
        f"{URL}/proposals",
        json={"action": "import_layout", "base_revision": 1, "inputs": {}},
    )
    assert r.status_code == 422, r.text
    assert _detail(r)["error_kind"] == "invalid_input"
    assert _detail(r)["message"] == "Proposal: import_layout needs a layout"
    assert client.get(URL).json()["revision"] == 1
