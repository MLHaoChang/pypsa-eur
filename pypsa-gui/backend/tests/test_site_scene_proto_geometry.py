"""
SC-00 N, V05/V06/V07/V08/V49/V53: frames, the reference building, native
generation and its limits (tests/site_scene_proto/{frames,native_scene}.py).

Expected values are written out by hand (fixtures-and-oracles.md style), never
derived from the code under test.
"""
from __future__ import annotations

import math

import pytest

from tests.site_scene_proto import frames, native_scene
from tests.site_scene_proto.contract import SceneError

SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141], [11.579, 48.141]], "placements": {}}


def _ref(heading=0.0, unit="m", scale=1.0):
    return {"id": "ref", "center": [5 * scale, 7 * scale], "width": 10 * scale, "depth": 20 * scale,
            "height": 8 * scale, "heading": heading, "unit": unit}


@pytest.mark.parametrize("heading", [0, 30, 90, 137.5, 180, 270, -45, 359.9])
def test_reference_building_keeps_10_by_20_by_8_m_under_any_heading(heading):
    b = native_scene.generate(SITE, {"greenfield": [_ref(heading)]})["buildings"][0]
    w, d = frames.measure_rectangle([tuple(p) for p in b["footprint"]])
    assert (w, d) == (pytest.approx(10, abs=1e-9), pytest.approx(20, abs=1e-9))
    assert b["height_m"] == 8
    assert frames.polygon_area([tuple(p) for p in b["footprint"]]) == pytest.approx(200, abs=1e-9)
    cx = sum(p[0] for p in b["footprint"]) / 4
    cy = sum(p[1] for p in b["footprint"]) / 4
    assert (cx, cy) == (pytest.approx(5), pytest.approx(7))
    assert b["id"] == "greenfield:ref" and b["height_evidence"] == "greenfield"


def test_heading_zero_puts_depth_along_north():
    fp = native_scene.generate(SITE, {"greenfield": [_ref(0)]})["buildings"][0]["footprint"]
    xs, ys = [p[0] for p in fp], [p[1] for p in fp]
    assert (max(xs) - min(xs), max(ys) - min(ys)) == (pytest.approx(10), pytest.approx(20))


@pytest.mark.parametrize("unit,scale", [("m", 1.0), ("mm", 1000.0), ("ft", 1 / 0.3048)])
def test_literal_units_agree(unit, scale):
    b = native_scene.generate(SITE, {"greenfield": [_ref(30, unit, scale)]})["buildings"][0]
    w, d = frames.measure_rectangle([tuple(p) for p in b["footprint"]])
    assert (w, d, b["height_m"]) == (pytest.approx(10), pytest.approx(20), pytest.approx(8))


@pytest.mark.parametrize("unit", [None, "", "metre", "in", "km"])
def test_a_missing_or_unknown_unit_is_refused_not_guessed(unit):
    with pytest.raises(SceneError) as e:
        native_scene.generate(SITE, {"greenfield": [_ref(0, unit)]})
    assert e.value.kind == "invalid_input" and e.value.detail == {"field": "unit"}


def test_gltf_is_y_up_and_round_trips():
    assert frames.enu_to_gltf((1.0, 2.0, 3.0)) == (1.0, 3.0, -2.0)
    assert frames.gltf_to_enu(frames.enu_to_gltf((1.0, 2.0, 3.0))) == (1.0, 2.0, 3.0)


def test_local_frame_is_the_existing_site_projection():
    # 0.001 deg north at 48.14 N is 111.319 m on the 6378137 m sphere (2*pi*R/360/1000).
    e, n = frames.lnglat_to_local((11.58, 48.14), (11.58, 48.141))
    assert (e, n) == (pytest.approx(0, abs=1e-9), pytest.approx(111.3195, abs=1e-3))
    e, n = frames.lnglat_to_local((11.58, 48.14), (11.581, 48.14))
    assert e == pytest.approx(111.3195 * math.cos(math.radians(48.14)), abs=1e-3)


def test_equal_source_ids_in_different_namespaces_stay_two_buildings():
    ring = [[11.5795, 48.1395], [11.5799, 48.1395], [11.5799, 48.1399], [11.5795, 48.1399]]
    out = native_scene.generate(SITE, {"sources": [{"namespace": "osm", "source_id": "7", "ring": ring},
                                                   {"namespace": "layout", "source_id": "7", "ring": ring}]})
    assert [b["id"] for b in out["buildings"]] == ["osm:7", "layout:7"]
    with pytest.raises(SceneError, match="duplicate"):
        native_scene.generate(SITE, {"sources": [{"namespace": "osm", "source_id": "7", "ring": ring}] * 2})


def test_height_evidence_classes_are_distinguishable():
    ring = [[11.5795, 48.1395], [11.5799, 48.1395], [11.5799, 48.1399]]
    srcs = [{"namespace": "osm", "source_id": "t", "ring": ring, "height_m": 12.5},
            {"namespace": "osm", "source_id": "l", "ring": ring, "levels": 4},
            {"namespace": "osm", "source_id": "f", "ring": ring}]
    out = native_scene.generate(SITE, {"sources": srcs, "greenfield": [_ref()]})["buildings"]
    assert [(b["id"], b["height_evidence"], b["height_m"]) for b in out] == [
        ("greenfield:ref", "greenfield", 8), ("osm:t", "tagged", 12.5), ("osm:l", "levels", 12.0),
        ("osm:f", "fallback", 6.0)]


@pytest.mark.parametrize("ring,msg", [([[0, 0], [1, 0]], "three vertices"),
                                      ([[0, 0], [1, 0], [2, 0]], "no area"),
                                      ([[0, 0], [2, 2], [2, 0], [0, 2]], "crosses itself")])
def test_invalid_footprints_are_refused(ring, msg):
    with pytest.raises(SceneError, match=msg) as e:
        native_scene.check_footprint([tuple(p) for p in ring], "b")
    assert e.value.kind == "invalid_geometry"


def test_clockwise_rings_are_stored_counter_clockwise():
    cw = native_scene.check_footprint([(0, 0), (0, 1), (1, 1), (1, 0)], "b")
    assert frames.polygon_area(cw) > 0


# ── V49: both sides of each limit ───────────────────────────────────────────

def _box(i, n=4):
    """An n-gon of radius 3 m centred 10 m apart."""
    cx = (i % 100) * 10.0
    cy = (i // 100) * 10.0
    return {"id": str(i), "center": [cx, cy], "width": 4, "depth": 4, "height": 3, "unit": "m"}


def test_building_count_limit_both_sides():
    at = native_scene.generate(SITE, {"greenfield": [_box(i) for i in range(native_scene.MAX_BUILDINGS)]})
    assert at["stats"]["buildings"] == native_scene.MAX_BUILDINGS
    with pytest.raises(SceneError) as e:
        native_scene.generate(SITE, {"greenfield": [_box(i) for i in range(native_scene.MAX_BUILDINGS + 1)]})
    assert e.value.kind == "resource_limit" and e.value.status == 413
    assert e.value.detail == {"limit": "buildings", "max": 2000, "actual": 2001}


def _ngon(n):
    return [(10 * math.cos(2 * math.pi * k / n), 10 * math.sin(2 * math.pi * k / n)) for k in range(n)]


def test_footprint_vertex_limit_both_sides():
    assert len(native_scene.check_footprint(_ngon(native_scene.MAX_FOOTPRINT_VERTICES), "b")) == 256
    with pytest.raises(SceneError) as e:
        native_scene.check_footprint(_ngon(native_scene.MAX_FOOTPRINT_VERTICES + 1), "b")
    assert e.value.detail["limit"] == "footprint_vertices"


def test_triangle_limit_both_sides(monkeypatch):
    assert native_scene.prism_triangles(4) == 12  # 2 roof + 2 floor + 8 wall
    monkeypatch.setattr(native_scene, "MAX_TRIANGLES", 24)
    assert native_scene.generate(SITE, {"greenfield": [_box(0), _box(1)]})["stats"]["triangles"] == 24
    with pytest.raises(SceneError) as e:
        native_scene.generate(SITE, {"greenfield": [_box(0), _box(1), _box(2)]})
    assert e.value.detail == {"limit": "triangles", "max": 24, "actual": 36}


def test_segment_pair_limit_both_sides(monkeypatch):
    # A 256-vertex footprint alone is 32384 pairs; the request budget bounds the O(n^2) check.
    assert native_scene.segment_pairs(4) == 2 and native_scene.segment_pairs(256) == 32384
    ring = [[11.58 + 0.0001 * math.cos(2 * math.pi * j / 8), 48.14 + 0.0001 * math.sin(2 * math.pi * j / 8)]
            for j in range(8)]
    src = lambda i: {"namespace": "osm", "source_id": str(i), "ring": ring}  # noqa: E731
    monkeypatch.setattr(native_scene, "MAX_SEGMENT_PAIRS", 40)  # two octagons are 2 x 20
    assert native_scene.generate(SITE, {"sources": [src(0), src(1)]})["stats"]["buildings"] == 2
    with pytest.raises(SceneError) as e:
        native_scene.generate(SITE, {"sources": [src(0), src(1), src(2)]})
    assert e.value.detail == {"limit": "segment_pairs", "max": 40, "actual": 60}


def test_preview_byte_limit_both_sides(monkeypatch):
    one = native_scene.generate(SITE, {"greenfield": [_box(0)]})["stats"]["bytes"]
    monkeypatch.setattr(native_scene, "MAX_PREVIEW_BYTES", one)
    native_scene.generate(SITE, {"greenfield": [_box(0)]})
    with pytest.raises(SceneError) as e:
        native_scene.generate(SITE, {"greenfield": [_box(0), _box(1)]})
    assert e.value.detail["limit"] == "preview_bytes"


def test_generation_needs_no_bus_and_writes_nothing(tmp_path):
    # V53: a Site with buses=[] and no network at all generates.
    out = native_scene.generate(SITE, {"greenfield": [_ref()]})
    assert SITE["buses"] == [] and out["stats"]["buildings"] == 1
    assert list(tmp_path.iterdir()) == []


def test_equipment_proxies_reuse_asset_footprints_and_never_guess_height():
    site = {**SITE, "placements": {"Generator:pv": {"x": 0, "y": 0, "heading": 90},
                                   "StorageUnit:bess": {"x": 10, "y": 0, "heading": 0},
                                   "Load:it": {"x": 20, "y": 0, "heading": 0}}}
    fps = {"Generator:pv": {"width_m": 4, "depth_m": 2, "height_m": 1.5},
           "StorageUnit:bess": {"width_m": 12, "depth_m": 2.5}}
    proxies = native_scene.equipment_proxies(site, fps)
    assert [p["target_key"] for p in proxies] == ["Generator:pv", "StorageUnit:bess"]
    w, d = frames.measure_rectangle([tuple(v) for v in proxies[0]["footprint"]])
    assert (w, d) == (pytest.approx(4), pytest.approx(2))
    assert proxies[0]["height"] == {"value": 1.5, "available": True, "reason": None, "unit": "m"}
    assert proxies[1]["height"] == {"value": None, "available": False, "reason": "height_not_set", "unit": "m"}
