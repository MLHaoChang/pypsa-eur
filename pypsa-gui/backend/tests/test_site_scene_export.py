"""
SC-04 (issue 05): the neutral "Export for Blender" bundle, a GLB plus a
canonical manifest, written with no Blender installed (V29, V31; V05/V06/V18
for geometry, V45 privacy, V49 limits). V30, a genuine pinned Blender
round trip, needs SC-00 B worker evidence and is not claimed here.

The GLB reader below is the test's own: it checks the container rules
(GK B E1) independently of the writer, and the metric oracle recomputes
areas, volumes and world corners from the decoded vertices.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import pathlib
import struct
import zipfile

import pytest

from services.site_scene import export, frames, native, store
from services.site_scene.contract import SceneError
from tests import site_scene_export_fixture as fixture
from tests.test_site_scene_routes import (
    _adopted,
    _empty_network,
    _kind,
    _propose,
    _put_site,
)

URL = "/api/projects/geo/sites/campus/scene/exports"
BODY = {"format": "glb_manifest", "media_policy": "none"}


@pytest.fixture
def geo(client, install_network, project_storage_dir):
    """The routes test's saved project with an empty network."""
    install_network(_empty_network(), name="geo")
    assert (
        client.post(
            "/api/projects/geo", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    return project_storage_dir("geo")


# ── the test-side GLB reader (E1) ────────────────────────────────────────────

_COMPONENTS = {5121: ("B", 1), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_WIDTH = {"SCALAR": 1, "VEC3": 3}


def read_glb(data: bytes) -> tuple[dict, bytes]:
    """Parse a GLB, asserting every structural rule E1 names; returns (json, bin)."""
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert (magic, version) == (b"glTF", 2)
    assert length == len(data), "header length must equal the file length"
    jlen, jtype = struct.unpack_from("<I4s", data, 12)
    assert jtype == b"JSON" and jlen % 4 == 0
    raw = data[20 : 20 + jlen]
    end = raw.rindex(b"}") + 1
    assert set(raw[end:]) <= {0x20} and jlen - end < 4, "JSON is padded with spaces only"
    doc = json.loads(raw.decode("utf-8"))
    rest = data[20 + jlen :]
    blen, btype = struct.unpack_from("<I4s", rest, 0)
    assert btype == b"BIN\x00" and blen % 4 == 0
    assert len(rest) == 8 + blen
    binary = rest[8:]
    assert doc["buffers"] == [{"byteLength": blen}]
    covered = bytearray(blen)
    for view in doc["bufferViews"]:
        assert (
            view["byteOffset"] % 4 == 0
            and view["byteOffset"] + view["byteLength"] <= blen
        )
        covered[view["byteOffset"] : view["byteOffset"] + view["byteLength"]] = (
            b"\x01" * view["byteLength"]
        )
    assert all(b == 0 for b, c in zip(binary, covered) if not c), (
        "BIN padding is 0x00"
    )
    for i, acc in enumerate(doc["accessors"]):
        fmt, size = _COMPONENTS[acc["componentType"]]
        view = doc["bufferViews"][acc["bufferView"]]
        assert acc["count"] * _WIDTH[acc["type"]] * size <= view["byteLength"]
        if acc["type"] == "VEC3":
            assert acc["componentType"] == 5126 and "min" in acc and "max" in acc, (
                f"accessor {i}"
            )
            pts = _floats(doc, binary, i)
            for k in range(3):
                assert acc["min"][k] == min(p[k] for p in pts) and acc["max"][k] == max(
                    p[k] for p in pts
                )
    for mesh in doc["meshes"]:
        prim = mesh["primitives"][0]
        n_vertices = doc["accessors"][prim["attributes"]["POSITION"]]["count"]
        idx = doc["accessors"][prim["indices"]]
        limit = {5121: 0xFF, 5123: 0xFFFF, 5125: 0xFFFFFFFF}[idx["componentType"]]
        assert (
            n_vertices - 1 <= limit
            and max(_ints(doc, binary, prim["indices"])) < n_vertices
        )
    return doc, binary


def _floats(doc, binary, i) -> list[tuple[float, float, float]]:
    acc = doc["accessors"][i]
    off = doc["bufferViews"][acc["bufferView"]]["byteOffset"]
    vals = struct.unpack_from(f"<{acc['count'] * 3}f", binary, off)
    return [tuple(vals[k : k + 3]) for k in range(0, len(vals), 3)]


def _ints(doc, binary, i) -> list[int]:
    acc = doc["accessors"][i]
    fmt, _size = _COMPONENTS[acc["componentType"]]
    off = doc["bufferViews"][acc["bufferView"]]["byteOffset"]
    return list(struct.unpack_from(f"<{acc['count']}{fmt}", binary, off))


def _quat_rotate(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    # v' = v + 2w(q x v) + 2 q x (q x v)
    cx, cy, cz = y * vz - z * vy, z * vx - x * vz, x * vy - y * vx
    ccx, ccy, ccz = y * cz - z * cy, z * cx - x * cz, x * cy - y * cx
    return (vx + 2 * (w * cx + ccx), vy + 2 * (w * cy + ccy), vz + 2 * (w * cz + ccz))


def node_mesh(
    doc, binary, name
) -> tuple[dict, list[tuple[float, float, float]], list[int]]:
    """A named node, its WORLD vertices (translation and rotation applied) and triangle indices."""
    node = next(n for n in doc["nodes"] if n["name"] == name)
    assert "scale" not in node and "matrix" not in node, (
        "no node scale: native size is kept (V18)"
    )
    prim = doc["meshes"][node["mesh"]]["primitives"][0]
    t, q = node.get("translation", [0, 0, 0]), node.get("rotation", [0, 0, 0, 1])
    world = [
        tuple(a + b for a, b in zip(_quat_rotate(q, p), t))
        for p in _floats(doc, binary, prim["attributes"]["POSITION"])
    ]
    return node, world, _ints(doc, binary, prim["indices"])


def volume(points, indices) -> float:
    """Signed volume by the divergence theorem: positive iff every face winds outward."""
    v = 0.0
    for a, b, c in zip(indices[0::3], indices[1::3], indices[2::3]):
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = points[a], points[b], points[c]
        v += (
            x1 * (y2 * z3 - y3 * z2)
            - x2 * (y1 * z3 - y3 * z1)
            + x3 * (y1 * z2 - y2 * z1)
        ) / 6
    return v


def enu(p):
    return frames.gltf_to_enu(p)


def corners_match(world, expected_enu, tol=1e-4):
    """Every expected ENU corner (at ground and roof) is a vertex of the world mesh."""
    got = [enu(p) for p in world]
    for e in expected_enu:
        assert min(math.dist(e, g) for g in got) < tol, e


def unzip(data: bytes) -> tuple[dict, bytes, zipfile.ZipFile]:
    z = zipfile.ZipFile(io.BytesIO(data))
    return json.loads(z.read("manifest.json")), z.read("scene.glb"), z


# ── units: frame, geometry, manifest (no app, no HTTP) ───────────────────────


def test_enu_to_gltf_signs_pinned_on_one_point():
    # E6: east stays +X, up is +Y, north is -Z.
    assert frames.enu_to_gltf((1.0, 2.0, 3.0)) == (1.0, 3.0, -2.0)


def test_reference_bundle_is_a_valid_glb_with_one_node_per_feature():
    glb, manifest = export.build(fixture.reference_snapshot())
    doc, binary = read_glb(glb)
    root = doc["nodes"][doc["scenes"][0]["nodes"][0]]
    assert root["name"] == "site:campus" and root["extras"] == {
        "kind": "site",
        "site_id": "campus",
    }
    names = sorted(doc["nodes"][c]["name"] for c in root["children"])
    assert names == sorted(f["id"] for f in manifest["features"])
    assert len(set(names)) == len(names), "no two features share a node name"
    for f in manifest["features"]:
        node = doc["nodes"][f["node"]]
        assert node["name"] == f["id"] and node["extras"]["feature_id"] == f["id"]
        assert (
            node["extras"]["kind"] == f["kind"]
            and node["extras"]["source_class"] == f["source_class"]
        )
    assert manifest["hashes"]["glb_sha256"] == hashlib.sha256(glb).hexdigest()


@pytest.mark.parametrize("heading", [0, 30, 90])
def test_building_keeps_its_metric_size_and_world_corners(heading):
    # V05/E6: a 10 x 20 x 8 m building at a heading: corners in world space, area, height, outward winding.
    snap = fixture.reference_snapshot()
    ring = frames.rectangle((5.0, -7.0), 10, 20, heading)
    snap["buildings"] = [
        {
            "id": "greenfield:hall",
            "source": {"namespace": "greenfield", "source_id": "hall"},
            "footprint": [list(p) for p in ring],
            "height_m": 8.0,
            "height_evidence": "greenfield",
            "levels": None,
        }
    ]
    doc, binary = read_glb(export.build(snap)[0])
    _node, world, idx = node_mesh(doc, binary, "greenfield:hall")
    corners_match(world, [(x, y, z) for x, y in ring for z in (0.0, 8.0)])
    assert volume(world, idx) == pytest.approx(10 * 20 * 8, rel=1e-6)
    assert max(p[1] for p in world) == pytest.approx(8.0) and min(
        p[1] for p in world
    ) == pytest.approx(0.0)
    assert len(idx) // 3 == native.prism_triangles(4)


def test_heading_90_puts_the_long_side_along_x_and_north_at_minus_z():
    # E6: a mirrored or reversed rotation fails here.
    snap = fixture.reference_snapshot()
    snap["site"]["placements"] = {
        "Generator:pv": {"x": 0.0, "y": 50.0, "heading": 90.0}
    }
    snap["asset_footprints"] = {
        "Generator:pv": {"width_m": 2.0, "depth_m": 6.0, "height_m": 1.0}
    }
    snap["buildings"] = []
    doc, binary = read_glb(export.build(snap)[0])
    node, world, idx = node_mesh(doc, binary, "Generator:pv")
    xs, zs = [p[0] for p in world], [p[2] for p in world]
    assert max(xs) - min(xs) == pytest.approx(
        6.0, abs=1e-5
    )  # depth (along heading) runs east
    assert max(zs) - min(zs) == pytest.approx(2.0, abs=1e-5)
    assert node["translation"] == [0.0, 0.0, -50.0]  # 50 m north is -Z
    corners_match(
        world,
        [
            (x, y, z)
            for x, y in frames.rectangle((0.0, 50.0), 2.0, 6.0, 90.0)
            for z in (0.0, 1.0)
        ],
    )
    assert volume(world, idx) == pytest.approx(2 * 6 * 1, rel=1e-6)


def test_equipment_heading_is_a_node_rotation_not_baked_geometry():
    glb, manifest = export.build(fixture.reference_snapshot())
    doc, binary = read_glb(glb)
    node, world, _ = node_mesh(doc, binary, "Generator:pv_roof")
    h = math.radians(30.0)
    assert node["rotation"] == pytest.approx(
        [0.0, math.sin(-h / 2), 0.0, math.cos(-h / 2)]
    )
    corners_match(
        world,
        [
            (x, y, z)
            for x, y in frames.rectangle((30.0, 10.0), 4.0, 2.0, 30.0)
            for z in (0.0, 2.5)
        ],
    )
    feat = next(f for f in manifest["features"] if f["id"] == "Generator:pv_roof")
    assert (feat["representation"], feat["dims"]) == (
        "proxy_box",
        {"width_m": 4.0, "depth_m": 2.0, "height_m": 2.5},
    )


def test_concave_l_shaped_building_keeps_its_area_and_volume():
    # E5: a concave footprint is triangulated without spilling outside it.
    glb, manifest = export.build(fixture.reference_snapshot())
    doc, binary = read_glb(glb)
    _node, world, idx = node_mesh(doc, binary, "osm:42")
    ring = next(
        b for b in fixture.reference_snapshot()["buildings"] if b["id"] == "osm:42"
    )["footprint"]
    area = frames.polygon_area([tuple(p) for p in ring])
    assert area == pytest.approx(300.0)
    assert volume(world, idx) == pytest.approx(area * 12.0, rel=1e-6)
    roof = [
        i
        for t in range(0, len(idx), 3)
        for i in idx[t : t + 3]
        if all(world[k][1] > 11.9 for k in idx[t : t + 3])
    ]
    roof_area = sum(
        abs(frames.polygon_area([enu(world[k])[:2] for k in roof[t : t + 3]]))
        for t in range(0, len(roof), 3)
    )
    assert roof_area == pytest.approx(area, rel=1e-6), (
        "roof triangles cover the L exactly, no overlap"
    )


@pytest.mark.parametrize(
    "bad, reason",
    [
        (
            {
                "footprint": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "height_m": float("nan"),
            },
            "invalid_height",
        ),
        (
            {"footprint": [[0, 0], [10, 0], [10, 10], [0, 10]], "height_m": 0},
            "invalid_height",
        ),
        ({"footprint": [[0, 0], [10, 0], [0, 0]], "height_m": 5}, "invalid_footprint"),
        (
            {"footprint": [[0, 0], [10, 0], [20, 0]], "height_m": 5},
            "invalid_footprint",
        ),  # zero area
        (
            {"footprint": [[0, 0], [10, 10], [10, 0], [0, 10]], "height_m": 5},
            "invalid_footprint",
        ),  # crosses itself
        (
            {"footprint": [[0, 0], [float("inf"), 0], [10, 10]], "height_m": 5},
            "invalid_footprint",
        ),
        # Holes: the native record has one outer ring; a ring-of-rings is omitted, never guessed.
        (
            {
                "footprint": [[[0, 0], [10, 0], [10, 10]], [[1, 1], [2, 1], [2, 2]]],
                "height_m": 5,
            },
            "invalid_footprint",
        ),
        # F4: bounded like the native path (0 < h <= MAX_HEIGHT_M).
        (
            {
                "footprint": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "height_m": native.MAX_HEIGHT_M + 0.001,
            },
            "invalid_height",
        ),
        # _finite: a bool or a string is not a number.
        (
            {"footprint": [[0, 0], [10, 0], [10, 10], [0, 10]], "height_m": True},
            "invalid_height",
        ),
        (
            {"footprint": [[0, 0], [10, 0], [10, 10], [0, 10]], "height_m": "5"},
            "invalid_height",
        ),
        ({"footprint": [[0, 0], [10, "0"], [10, 10]], "height_m": 5}, "invalid_footprint"),
        ({"footprint": [[0, 0], [True, 0], [10, 10]], "height_m": 5}, "invalid_footprint"),
        # Vertex shape: a non-list point, a 3-element point, a non-list footprint.
        ({"footprint": [[0, 0], "10,0", [10, 10]], "height_m": 5}, "invalid_footprint"),
        ({"footprint": [[0, 0], [10, 0, 1], [10, 10]], "height_m": 5}, "invalid_footprint"),
        ({"footprint": {"0": [0, 0]}, "height_m": 5}, "invalid_footprint"),
        # F3: over MAX_FOOTPRINT_VERTICES is too detailed, not invalid.
        (
            {
                "footprint": [
                    [
                        10 * math.cos(2 * math.pi * k / (native.MAX_FOOTPRINT_VERTICES + 1)),
                        10 * math.sin(2 * math.pi * k / (native.MAX_FOOTPRINT_VERTICES + 1)),
                    ]
                    for k in range(native.MAX_FOOTPRINT_VERTICES + 1)
                ],
                "height_m": 5,
            },
            "footprint_too_detailed",
        ),
    ],
)
def test_unexportable_building_is_a_named_omission(bad, reason):
    snap = fixture.reference_snapshot()
    snap["buildings"] = [
        {
            "id": "manual:bad",
            "source": {"namespace": "manual", "source_id": "bad"},
            "height_evidence": "tagged",
            "levels": None,
            **bad,
        }
    ]
    glb, manifest = export.build(snap)
    doc, _ = read_glb(glb)
    assert "manual:bad" not in {n["name"] for n in doc["nodes"]}
    assert {"feature": "manual:bad", "kind": "building", "reason": reason} in manifest[
        "omissions"
    ]
    assert b"NaN" not in glb and b"Infinity" not in glb


def test_equipment_without_footprint_or_height_is_omitted_not_guessed():
    _glb, manifest = export.build(fixture.reference_snapshot())
    om = {o["feature"]: o["reason"] for o in manifest["omissions"]}
    assert om == {"Load:office": "no_footprint", "Battery:bess": "height_not_set"}
    assert {f["id"] for f in manifest["features"]}.isdisjoint(om)


def test_manifest_carries_origin_axes_revision_bindings_and_sources():
    # V29: exact IDs, origin, axes, units, hashes and revision.
    snap = fixture.reference_snapshot()
    glb, m = export.build(snap)
    assert (m["format"], m["schema_version"], m["snapshot"]) == (
        "pypsa-studio.site-scene-export",
        1,
        True,
    )
    assert m["site"] == {"id": "campus", "name": "Campus"}
    assert m["scene"] == {
        "revision": 3,
        "generation": snap["generation"],
        "generation_sha256": snap["generation_sha256"],
    }
    assert m["origin"] == {
        "lng": 11.58,
        "lat": 48.14,
        "crs": "EPSG:4326",
        "frame": "local east/north/up metres, equirectangular about the origin",
    }
    assert m["axes"] == {
        "units": "m",
        "gltf": {"x": "east", "y": "up", "-z": "north"},
        "heading": "degrees clockwise from north",
        "up": "metres above the site ground plane",
    }
    hall = next(f for f in m["features"] if f["id"] == "greenfield:hall")
    assert hall["source"] == {"namespace": "greenfield", "source_id": "hall"}
    assert (hall["source_class"], hall["height_evidence"], hall["representation"]) == (
        "proposed",
        "greenfield",
        "prism",
    )
    assert (
        next(f for f in m["features"] if f["id"] == "osm:42")["source_class"]
        == "mapped"
    )
    assert m["bindings"] == [
        {
            "id": "a1",
            "feature": "greenfield:hall",
            "class": "Load",
            "uid": "u-office",
            "name_at_bind": "office",
            "role": "consumer",
            "weight": 1.0,
        }
    ]
    doc, _ = read_glb(glb)
    hall_node = next(n for n in doc["nodes"] if n["name"] == "greenfield:hall")
    assert hall_node["extras"]["bindings"] == [
        {
            "class": "Load",
            "uid": "u-office",
            "name_at_bind": "office",
            "role": "consumer",
        }
    ]
    assert m["groups"] == [
        {"id": "g1", "label": "Main hall", "members": ["greenfield:hall"]}
    ]
    for f in m["features"]:
        assert len(f["geometry_sha256"]) == 64
    sem = {k: v for k, v in m.items() if k not in ("exported_at", "hashes")}
    assert (
        m["hashes"]["semantic_sha256"]
        == hashlib.sha256(store.canonical(sem)).hexdigest()
    )
    assert m["media"] == {"policy": "none", "included": []}
    assert "not a live link" in m["note"] and "not a certified model" in m["note"]


def test_equivalent_exports_are_byte_stable_apart_from_exported_at():
    # E3: two builds of one generation agree; the zip layout is fixed.
    snap = fixture.reference_snapshot()
    a = export.bundle(snap, exported_at="2026-01-01T00:00:00Z")
    b = export.bundle(copy.deepcopy(snap), exported_at="2026-06-30T12:00:00Z")
    (ma, ga, za), (mb, gb, _zb) = unzip(a), unzip(b)
    assert ga == gb and ma["hashes"] == mb["hashes"]
    assert {**ma, "exported_at": None} == {**mb, "exported_at": None}
    assert [i.filename for i in za.infolist()] == ["manifest.json", "scene.glb"]
    for info in za.infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.external_attr == 0o644 << 16
    assert export.bundle(snap, exported_at="2026-01-01T00:00:00Z") == a
    assert za.read("manifest.json") == store.canonical(ma)


def test_a_new_revision_changes_the_semantic_hash():
    snap = fixture.reference_snapshot()
    _g, before = export.build(snap)
    snap["buildings"][0]["height_m"] = 9.0
    _g, after = export.build(snap)
    assert before["hashes"]["semantic_sha256"] != after["hashes"]["semantic_sha256"]


# ── limits (V49, E7): just under and just over, each a 413 resource_limit ───


def _limit_kind(fn):
    with pytest.raises(SceneError) as err:
        fn()
    assert err.value.kind == "resource_limit"
    return err.value.detail["limit"]


def test_feature_count_limit_both_sides(monkeypatch):
    snap = fixture.reference_snapshot()
    n = len(snap["buildings"]) + len(snap["site"]["placements"])
    monkeypatch.setattr(export, "MAX_EXPORT_FEATURES", n)
    export.build(snap)
    monkeypatch.setattr(export, "MAX_EXPORT_FEATURES", n - 1)
    assert _limit_kind(lambda: export.build(snap)) == "features"


def test_triangle_limit_both_sides(monkeypatch):
    snap = fixture.reference_snapshot()
    _glb, m = export.build(snap)
    tris = m["stats"]["triangles"]
    monkeypatch.setattr(export, "MAX_TRIANGLES", tris)
    export.build(snap)
    monkeypatch.setattr(export, "MAX_TRIANGLES", tris - 1)
    assert _limit_kind(lambda: export.build(snap)) == "triangles"


def test_triangle_limit_refuses_before_any_mesh_is_built(monkeypatch):
    # E7: counts first; an over-limit export never reaches the geometry.
    snap = fixture.reference_snapshot()

    def no_geometry(*a, **k):
        raise AssertionError(
            "no mesh may be built before the triangle count is admitted"
        )

    monkeypatch.setattr(export, "MAX_TRIANGLES", 1)
    monkeypatch.setattr(export, "_prism", no_geometry)
    assert _limit_kind(lambda: export.build(snap)) == "triangles"


def test_byte_limit_both_sides(monkeypatch):
    snap = fixture.reference_snapshot()
    size = len(export.bundle(snap, exported_at="2026-01-01T00:00:00Z"))
    monkeypatch.setattr(export, "MAX_EXPORT_BYTES", size)
    export.bundle(snap, exported_at="2026-01-01T00:00:00Z")
    monkeypatch.setattr(export, "MAX_EXPORT_BYTES", size - 1)
    assert (
        _limit_kind(lambda: export.bundle(snap, exported_at="2026-01-01T00:00:00Z"))
        == "export_bytes"
    )
    with pytest.raises(SceneError) as err:
        export.bundle(snap, exported_at="2026-01-01T00:00:00Z")
    assert err.value.message == (
        f"Too large to export: the file would be {native.size_text(size)}, "
        f"over the {native.size_text(size - 1)} limit"
    )


# ── the committed fixture the frontend's GLTFLoader reads (E2) ──────────────


def test_committed_fixture_is_the_generator_output():
    # Regenerate with `python -m tests.site_scene_export_fixture` from pypsa-gui/backend; never hand-edit.
    assert fixture.FIXTURE.read_bytes() == fixture.generate(), (
        "run the fixture generator"
    )


# ── the route (V31, V45, E4, E8, E10, E11) ───────────────────────────────────


def _scene_with_buildings(client, project):
    _adopted(client, project)
    prop = _propose(client, 1).json()
    r = client.post(
        f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}/apply",
        json={"expected_revision": 1},
    )
    assert r.status_code == 200, r.text


def _tree_digest(root: pathlib.Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_export_route_downloads_the_bundle_and_writes_nothing(client, geo):
    _scene_with_buildings(client, geo)
    before = _tree_digest(geo)
    r = client.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert (
        r.headers["content-disposition"] == 'attachment; filename="Campus-scene-r2.zip"'
    )
    assert r.headers["x-scene-revision"] == "2"
    manifest, glb, _z = unzip(r.content)
    assert manifest["scene"]["revision"] == 2 and manifest["project"] == "geo"
    assert [f["id"] for f in manifest["features"]] == ["greenfield:hall"]
    pointer = json.loads((geo / "sites/campus/scene/current.json").read_text())
    assert manifest["scene"]["generation"] == pointer["generation"]
    assert manifest["scene"]["generation_sha256"] == pointer["sha256"]
    read_glb(glb)
    assert _tree_digest(geo) == before, "an export writes nothing to the project"


def test_export_works_with_no_blender_and_launches_nothing(client, geo, monkeypatch):
    # V31: no Blender on PATH, and any attempt to start a process fails the test.
    import shutil
    import subprocess

    _scene_with_buildings(client, geo)
    monkeypatch.setenv("PATH", "")
    assert shutil.which("blender") is None

    def refuse(*a, **k):
        raise AssertionError("the export must not start a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    r = client.post(URL, json=BODY)
    assert r.status_code == 200, r.text


def test_export_never_includes_photos_paths_or_exif(client, geo):
    # V45/E10: planted originals and a layout image stay out of the bundle and the manifest.
    _scene_with_buildings(client, geo)
    exif = b"\xff\xd8\xff\xe1\x00\x16Exif\x00\x00MM\x00*PRIVATE-GPS-MARKER\xff\xd9"
    for rel in (
        "uploads/IMG_secret_roof.jpg",
        "sites/campus/IMG_secret_roof.jpg",
        "sites/campus/scene/layout-raster.png",
    ):
        (geo / rel).parent.mkdir(parents=True, exist_ok=True)
        (geo / rel).write_bytes(exif)
    r = client.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    manifest, glb, z = unzip(r.content)
    assert [i.filename for i in z.infolist()] == ["manifest.json", "scene.glb"]
    for blob in (r.content, z.read("manifest.json"), glb):
        for needle in (
            b"PRIVATE-GPS-MARKER",
            b"Exif",
            b"IMG_secret_roof",
            str(geo).encode(),
            b"uploads/",
        ):
            assert needle not in blob
    text = json.dumps(manifest)
    assert "exif" not in text.lower() and "/" not in json.dumps(
        manifest["features"]
    ).replace("\\/", "")


def test_export_stays_on_the_generation_it_read(client, geo, monkeypatch):
    # E4: a commit that lands between the read and the build never mixes generations.
    _scene_with_buildings(client, geo)
    read = export.read_snapshot
    moved = {}

    def read_then_move(project_dir, site_id):
        snap = read(project_dir, site_id)
        prop = _propose(client, 2, greenfield=({**fixture.HALL, "height": 30},)).json()
        r = client.post(
            f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}/apply",
            json={"expected_revision": 2},
        )
        moved["status"] = r.status_code
        return snap

    monkeypatch.setattr(export, "read_snapshot", read_then_move)
    r = client.post(URL, json=BODY)
    assert moved["status"] == 200 and r.status_code == 200, r.text
    manifest, glb, _z = unzip(r.content)
    assert manifest["scene"]["revision"] == 2
    # F1: the header and the file name name the revision the bytes hold, not the moved pointer.
    assert r.headers["x-scene-revision"] == "2"
    assert r.headers["content-disposition"].endswith('-scene-r2.zip"')
    doc, binary = read_glb(glb)
    _node, world, _ = node_mesh(doc, binary, "greenfield:hall")
    assert max(p[1] for p in world) == pytest.approx(8.0), (
        "geometry from revision 2, not 3"
    )
    assert client.get("/api/projects/geo/sites/campus/scene").json()["revision"] == 3


def test_export_of_an_unadopted_or_unknown_site_is_not_found(client, geo):
    _put_site(client)
    r = client.post(URL, json=BODY)
    assert (r.status_code, _kind(r)) == (404, "not_found")
    assert "no 3D scene" in r.json()["detail"]["message"]
    r = client.post("/api/projects/geo/sites/nowhere/scene/exports", json=BODY)
    assert (r.status_code, _kind(r)) == (404, "not_found")


@pytest.mark.parametrize(
    "body, field",
    [
        ({"format": "glb_manifest", "media_policy": "originals"}, "media_policy"),
        ({"format": "blend", "media_policy": "none"}, "format"),
        ({"format": "glb_manifest", "media_policy": "none", "extra": 1}, "extra"),
        ({"format": "glb_manifest"}, "media_policy"),
        ([], None),
    ],
)
def test_export_request_is_strict(client, geo, body, field):
    # E8: format and media policy are explicit; nothing else is accepted.
    _scene_with_buildings(client, geo)
    r = client.post(URL, json=body)
    assert (r.status_code, _kind(r)) == (422, "invalid_input"), r.text
    assert r.json()["detail"].get("field") == field


def test_export_body_cap_is_checked_before_parsing(client, geo):
    _scene_with_buildings(client, geo)
    pad = "x" * (4 * 1024)
    r = client.post(
        URL,
        content=json.dumps({**BODY, "pad": pad}).encode(),
        headers={"content-type": "application/json"},
    )
    assert (r.status_code, _kind(r), r.json()["detail"]["limit"]) == (
        413,
        "resource_limit",
        "request_bytes",
    )
    under = json.dumps(BODY).encode()
    assert (
        len(under) < 4 * 1024
        and client.post(
            URL, content=under, headers={"content-type": "application/json"}
        ).status_code
        == 200
    )


def test_a_failing_build_leaves_nothing_behind(client, geo, monkeypatch):
    # E11: the bundle is built in memory; a raise mid-build writes no file.
    _scene_with_buildings(client, geo)
    before = _tree_digest(geo)

    def boom(*a, **k):
        raise SceneError("resource_limit", "Too large", {"limit": "export_bytes"})

    monkeypatch.setattr(export, "_zip", boom)
    r = client.post(URL, json=BODY)
    assert (r.status_code, _kind(r)) == (413, "resource_limit")
    assert _tree_digest(geo) == before


def test_download_name_of_a_site_with_quotes_and_non_ascii(client, geo):
    # E10: the name goes through http_filenames, with an ASCII fallback and an RFC 5987 form.
    _put_site(client, {**_SITE_NAMED, "name": 'Café "Nord"'})
    _scene_with_buildings_named(client, geo)
    r = client.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    cd = r.headers["content-disposition"]
    assert (
        cd
        == "attachment; filename=\"Cafe _Nord_-scene-r2.zip\"; filename*=UTF-8''Caf%C3%A9%20%22Nord%22-scene-r2.zip"
    )


_SITE_NAMED = {
    "id": "campus",
    "name": "Campus",
    "buses": [],
    "origin": {"lng": 11.58, "lat": 48.14},
    "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]],
    "placements": {},
}


def _scene_with_buildings_named(client, project):
    from tests.test_site_scene_routes import _digest

    r = client.post(
        "/api/projects/geo/sites/campus/scene/adopt",
        json={"expected_digest": _digest(project)},
    )
    assert r.status_code == 200, r.text
    prop = _propose(client, 1).json()
    r = client.post(
        f"/api/projects/geo/sites/campus/scene/proposals/{prop['id']}/apply",
        json={"expected_revision": 1},
    )
    assert r.status_code == 200, r.text


def test_export_verdict_is_a_read_that_needs_buildings():
    # The /flow verdict: viewers may export; no buildings names the Generate tab; unsaved says save first.
    from services.site_scene import verdicts

    def verdict(**kw):
        args = {
            "saved": True,
            "can_edit": False,
            "anchor_confirmed": True,
            "site": "accepted",
            "boundary": True,
            "buildings": 2,
            "targets": 0,
            "native_runs": 0,
            "pending_proposal": False,
            **kw,
        }
        return next(
            a for a in verdicts.actions(**args) if a["action"] == "export_scene"
        )

    assert verdict()["available"] is True and verdict()["blocker"] is None
    none = verdict(buildings=0, site="unfinished")
    assert none["available"] is False
    assert none["blocker"]["code"] == "no_buildings" and none["blocker"]["home"] == {
        "site_part": "generate"
    }
    assert (
        none["blocker"]["reason"]
        == "Generate the 3D buildings first (Generate tab): only a 3D site can be exported"
    )
    assert verdict(saved=False)["blocker"]["code"] == "save_first"
    blender = next(
        a
        for a in verdicts.actions(
            saved=True,
            can_edit=True,
            anchor_confirmed=True,
            site="accepted",
            boundary=True,
            buildings=2,
            targets=0,
            native_runs=0,
            pending_proposal=False,
        )
        if a["action"] == "generate_blender"
    )
    assert (
        blender["available"] is False
        and blender["blocker"]["code"] == "worker_unavailable"
    )


# ── GK B F2–F8 and the E12 mutant gaps ───────────────────────────────────────


def _feature(fid: str, n_tris: int) -> dict:
    """A hand-made feature for the writer: `n_tris` triangles over 3 vertices."""
    return {
        "id": fid,
        "kind": "building",
        "translation": (0.0, 0.0, 0.0),
        "rotation": None,
        "positions": [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0)],
        "indices": [0, 1, 2] * n_tris,
        "record": {"source_class": "manual"},
    }


def test_glb_padding_runs_and_is_spaces_and_zeros():
    # F2: real features always have 4-aligned blobs, so a one-triangle uint16 index
    # blob (6 bytes) is what makes the BIN padding run; ids of 1–4 chars make the JSON pad run.
    snap = fixture.reference_snapshot()
    json_padded = False
    for fid in ("a", "ab", "abc", "abcd"):
        glb, _geometry = export._glb(snap, [_feature(fid, 1)], {})
        doc, binary = read_glb(glb)
        idx_view = doc["bufferViews"][doc["accessors"][1]["bufferView"]]
        assert idx_view["byteLength"] == 6 and len(binary) == 36 + 8
        assert binary[36 + 6 :] == b"\x00\x00"
        jlen = struct.unpack_from("<I", glb, 12)[0]
        raw = glb[20 : 20 + jlen]
        json_padded |= raw.endswith(b" ")
    assert json_padded, "at least one id length makes the JSON padding run"


def test_index_width_switches_to_uint32_only_past_65535_vertices():
    # 422 is out of reach of real features (<= 2 * 256 vertices); pinned on the writer directly.
    snap = fixture.reference_snapshot()
    for n, ctype in ((0x10000, 5123), (0x10001, 5125)):
        f = {**_feature("big", 1), "positions": [(float(k), 0.0, 0.0) for k in range(n)]}
        f["indices"] = [0, 1, n - 1]
        glb, _ = export._glb(snap, [f], {})
        doc, _binary = read_glb(glb)
        assert doc["accessors"][1]["componentType"] == ctype


def test_building_at_the_height_cap_is_exported():
    snap = fixture.reference_snapshot()
    snap["buildings"][0]["height_m"] = native.MAX_HEIGHT_M
    _glb, manifest = export.build(snap)
    assert snap["buildings"][0]["id"] in {f["id"] for f in manifest["features"]}


@pytest.mark.parametrize(
    "placement, footprint, reason",
    [
        ("30,10", None, "invalid_placement"),
        ([30.0, 10.0, 0.0], None, "invalid_placement"),
        ({"x": float("nan"), "y": 10.0, "heading": 0.0}, None, "invalid_placement"),
        ({"x": 30.0, "y": float("inf"), "heading": 0.0}, None, "invalid_placement"),
        ({"x": 30.0, "y": 10.0, "heading": float("nan")}, None, "invalid_placement"),
        ({"x": 30.0, "y": 10.0, "heading": "north"}, None, "invalid_placement"),
        ({"x": True, "y": 10.0, "heading": 0.0}, None, "invalid_placement"),
        ({"x": 30.0, "y": 10.0}, None, "invalid_placement"),
        (None, {"width_m": 0.0, "depth_m": 2.0, "height_m": 2.5}, "invalid_footprint"),
        (None, {"width_m": 4.0, "depth_m": 0.0, "height_m": 2.5}, "invalid_footprint"),
        (None, {"width_m": -4.0, "depth_m": 2.0, "height_m": 2.5}, "invalid_footprint"),
        (None, {"width_m": 4.0, "depth_m": -2.0, "height_m": 2.5}, "invalid_footprint"),
        (None, {"width_m": float("nan"), "depth_m": 2.0, "height_m": 2.5}, "invalid_footprint"),
        (None, {"width_m": 4.0, "depth_m": "2", "height_m": 2.5}, "invalid_footprint"),
        (None, {"width_m": 4.0, "depth_m": 2.0, "height_m": 0.0}, "invalid_height"),
        (
            None,
            {"width_m": 4.0, "depth_m": 2.0, "height_m": native.MAX_HEIGHT_M + 1},
            "invalid_height",
        ),
        (None, ["not", "a", "dict"], "no_footprint"),
    ],
)
def test_unexportable_equipment_is_a_named_omission(placement, footprint, reason):
    # F5: every equipment guard, each on the otherwise valid pv_roof box.
    snap = fixture.reference_snapshot()
    key = "Generator:pv_roof"
    if placement is not None:
        snap["site"]["placements"][key] = placement
    if footprint is not None:
        snap["asset_footprints"][key] = footprint
    glb, manifest = export.build(snap)
    assert {"feature": key, "kind": "equipment", "reason": reason} in manifest[
        "omissions"
    ]
    assert key not in {f["id"] for f in manifest["features"]}
    assert b"NaN" not in glb and b"Infinity" not in glb


def _check_triangulation(ring, n_tris):
    """Oracle: every triangle strictly counter-clockwise and the areas sum to the ring's."""
    tris = export._triangulate(ring)
    assert len(tris) == n_tris
    areas = [export._cross(ring[a], ring[b], ring[c]) / 2 for a, b, c in tris]
    assert all(a > 0 for a in areas), "every ear is wound counter-clockwise"
    assert sum(areas) == pytest.approx(frames.polygon_area(ring), rel=1e-9)


def test_ear_clipping_with_a_reflex_first_corner():
    # F6: the L of the metric oracle, started at its reflex corner (4, 4).
    _check_triangulation([(4, 4), (4, 10), (0, 10), (0, 0), (10, 0), (10, 4)], 4)


def test_ear_clipping_skips_a_collinear_corner():
    # F6: (5, 0) lies on the bottom edge. Met as the ear tip it is dropped, never cut
    # into a zero-area sliver; clipped first from elsewhere it is an ordinary corner.
    _check_triangulation([(5, 0), (10, 0), (10, 10), (0, 10), (0, 0)], 2)
    _check_triangulation([(0, 0), (5, 0), (10, 0), (10, 10), (0, 10)], 3)


def test_ear_clipping_refuses_an_ear_with_a_corner_on_its_edge():
    # _inside's >= boundary: (2, 2) lies on A–C of the ear at B, so that ear is refused.
    _check_triangulation([(0, 0), (4, 0), (4, 4), (2, 2), (0, 4)], 3)


def test_geometry_sha256_pins_positions_and_indices():
    # F7: the hash is sha256(position bytes + index bytes) of the feature's own buffers.
    _glb_bytes, manifest = export.build(fixture.reference_snapshot())
    doc, binary = read_glb(_glb_bytes)
    for f in manifest["features"]:
        prim = doc["meshes"][doc["nodes"][f["node"]]["mesh"]]["primitives"][0]
        chunks = []
        for acc in (prim["attributes"]["POSITION"], prim["indices"]):
            view = doc["bufferViews"][doc["accessors"][acc]["bufferView"]]
            chunks.append(binary[view["byteOffset"] : view["byteOffset"] + view["byteLength"]])
        assert f["geometry_sha256"] == hashlib.sha256(b"".join(chunks)).hexdigest()
    taller = fixture.reference_snapshot()
    taller["buildings"][0]["height_m"] += 1
    before = {f["id"]: f["geometry_sha256"] for f in manifest["features"]}
    after = {f["id"]: f["geometry_sha256"] for f in export.build(taller)[1]["features"]}
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {taller["buildings"][0]["id"]}


def test_manifest_stats_count_what_was_written():
    glb, manifest = export.build(fixture.reference_snapshot())
    stats = manifest["stats"]
    assert stats["features"] == len(manifest["features"]) > 0
    assert stats["omitted"] == len(manifest["omissions"]) == 2
    assert stats["glb_bytes"] == len(glb)
    doc, binary = read_glb(glb)
    assert stats["triangles"] == sum(
        doc["accessors"][m["primitives"][0]["indices"]]["count"] // 3
        for m in doc["meshes"]
    )


def test_a_non_finite_binding_weight_is_an_integrity_error():
    # 602: _canonical refuses NaN, so the manifest never holds it.
    snap = fixture.reference_snapshot()
    snap["associations"][0]["weight"] = float("nan")
    with pytest.raises(SceneError) as err:
        export.build(snap)
    assert err.value.kind == "integrity_error"


def test_missing_or_null_optional_keys_export_as_empty():
    # The `or {}` / `or []` defaults: a snapshot without the optional parts still exports.
    snap = fixture.reference_snapshot()
    building = {**snap["buildings"][0], "source": None}
    snap.update(
        buildings=[building],
        asset_footprints=None,
        associations=None,
        groups=None,
        site={**snap["site"], "origin": None, "placements": None},
    )
    glb, manifest = export.build(snap)
    read_glb(glb)
    assert [f["id"] for f in manifest["features"]] == [building["id"]]
    assert manifest["features"][0]["source"] == {"namespace": None, "source_id": None}
    assert manifest["features"][0]["source_class"] == "unknown"
    assert manifest["omissions"] == [] and manifest["bindings"] == []
    assert manifest["groups"] == []
    assert (manifest["origin"]["lng"], manifest["origin"]["lat"]) == (None, None)
    for k in ("buildings", "asset_footprints", "associations", "groups"):
        snap.pop(k)
    snap["site"].pop("placements")
    _glb, manifest = export.build(snap)
    assert manifest["features"] == [] and manifest["stats"]["features"] == 0


def test_a_negative_zero_writes_the_same_bytes_as_zero():
    def glb_at(x):
        snap = fixture.reference_snapshot()
        snap["site"]["placements"]["Generator:pv_roof"] = {"x": x, "y": 0.0, "heading": 0.0}
        return export.build(snap)[0]

    glb = glb_at(-0.0)
    assert glb == glb_at(0.0)
    doc, _ = read_glb(glb)
    node = next(n for n in doc["nodes"] if n["name"] == "Generator:pv_roof")
    assert all(math.copysign(1.0, c) == 1.0 for c in node["translation"])


@pytest.mark.parametrize("extra, status", [(0, 200), (1, 413)])
def test_export_body_cap_at_exactly_the_cap(client, geo, extra, status):
    _scene_with_buildings(client, geo)
    raw = json.dumps(BODY).encode()
    raw += b" " * (4 * 1024 - len(raw) + extra)
    r = client.post(URL, content=raw, headers={"content-type": "application/json"})
    assert r.status_code == status, r.text


def test_a_count_limit_is_an_http_413(client, geo, monkeypatch):
    _scene_with_buildings(client, geo)
    monkeypatch.setattr(export, "MAX_EXPORT_FEATURES", 0)
    r = client.post(URL, json=BODY)
    assert (r.status_code, _kind(r), r.json()["detail"]["limit"]) == (
        413,
        "resource_limit",
        "features",
    )


def test_a_viewer_member_without_the_lock_can_export(client, geo, _auth_db, seeded_identity):
    # F8: the contract row says viewer; a plain member of the project's org,
    # assigned to the project and holding no edit lock, gets the download.
    import uuid
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    import main
    from db.models import OrgMembership, Project, ProjectMembership, User
    from services.auth_service import hash_password
    from tests.conftest import attach_session

    _scene_with_buildings(client, geo)
    _engine, session_local = _auth_db
    with session_local() as db:
        user = User(
            id=uuid.uuid4(),
            email=f"viewer-{uuid.uuid4().hex[:6]}@example.com",
            password_hash=hash_password("irrelevant"),
            status="active",
            is_super_admin=False,
            created_at=datetime.now(tz=UTC),
        )
        db.add(user)
        db.flush()
        db.add(OrgMembership(id=uuid.uuid4(), user_id=user.id,
                             org_id=seeded_identity["org_id"], role="member"))
        project = db.query(Project).filter_by(
            org_id=seeded_identity["org_id"], name="geo"
        ).one()
        db.add(ProjectMembership(project_id=project.id, user_id=user.id,
                                 assigned_by=project.created_by,
                                 assigned_at=datetime.now(tz=UTC)))
        db.commit()
        uid = user.id
    with TestClient(main.app) as viewer:
        attach_session(viewer, session_local, uid)
        r = viewer.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    assert r.headers["x-scene-revision"] == "2"
    manifest, glb, _z = unzip(r.content)
    assert [f["id"] for f in manifest["features"]] == ["greenfield:hall"]
