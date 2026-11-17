"""Issue 06 slice (b), GK B R1: the returned GLB is untrusted; every structure is bounded."""

from __future__ import annotations

import copy
import json
import math
import struct
from pathlib import Path

import numpy as np
import pytest

from services.site_scene import glb_read
from services.site_scene.contract import SceneError

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/site3d/__fixtures__/scene-export.glb"
)


def pack(doc: dict, blob: bytes = b"") -> bytes:
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + (8 + len(blob) if blob else 0)
    out = (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(js), b"JSON")
        + js
    )
    if blob:
        out += struct.pack("<I4s", len(blob), b"BIN\x00") + blob
    return out


def tri(extras=None, **node):
    """One triangle mesh under one node: a valid baseline for each refusal."""
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 0, 1)
    idx = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(pos) + len(idx)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [
            {
                "mesh": 0,
                "extras": extras if extras is not None else {"feature_id": "osm:1"},
                **node,
            }
        ],
    }
    return doc, pos + idx


def refused(doc, blob=b"", kind="invalid_geometry", reason=None):
    with pytest.raises(SceneError) as e:
        glb_read.parse(pack(doc, blob))
    assert e.value.kind == kind
    if reason:
        assert e.value.detail["reason"] == reason
    return e.value


def test_the_native_export_fixture_parses_with_its_feature_ids():
    nodes = glb_read.parse(FIXTURE.read_bytes())
    ids = [n.feature_id for n in nodes if n.feature_id]
    assert ids and all(isinstance(i, str) for i in ids)
    assert all(n.positions for n in nodes if n.feature_id)


def test_a_baseline_triangle_parses():
    doc, blob = tri()
    (n,) = glb_read.parse(pack(doc, blob))
    assert n.feature_id == "osm:1" and len(n.positions) == 3


@pytest.mark.parametrize("where", ["buffer", "image"])
def test_an_external_uri_is_refused(where):
    doc, blob = tri()
    if where == "buffer":
        doc["buffers"][0]["uri"] = "file:///etc/passwd"
    else:
        doc["images"] = [{"uri": "http://example.invalid/x.png"}]
    refused(doc, blob, reason="external_uri")


def test_a_data_uri_buffer_is_refused_too():
    doc, blob = tri()
    doc["buffers"][0]["uri"] = "data:application/octet-stream;base64,AAAA"
    refused(doc, blob, reason="external_uri")


def test_a_second_buffer_is_refused():
    doc, blob = tri()
    doc["buffers"].append({"byteLength": 4})
    refused(doc, blob, kind="resource_limit")


def test_a_buffer_longer_than_the_bin_chunk_is_refused():
    doc, blob = tri()
    doc["buffers"][0]["byteLength"] = len(blob) + 4096
    refused(doc, blob, reason="buffer")


def test_a_view_past_its_buffer_is_refused():
    doc, blob = tri()
    doc["bufferViews"][1]["byteLength"] = 4096
    refused(doc, blob, reason="bounds")


def test_an_accessor_past_its_view_is_refused():
    doc, blob = tri()
    doc["accessors"][0]["count"] = 4
    refused(doc, blob, reason="bounds")


def test_an_accessor_offset_past_its_view_is_refused():
    doc, blob = tri()
    doc["accessors"][0]["byteOffset"] = 4
    refused(doc, blob, reason="bounds")


def test_an_index_out_of_range_is_refused():
    doc, _ = tri()
    blob = (
        struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 0, 1)
        + struct.pack("<3H", 0, 1, 3)
        + b"\x00\x00"
    )
    refused(doc, blob, reason="bounds")


def test_position_must_be_float_vec3():
    doc, blob = tri()
    doc["accessors"][0]["componentType"] = 5123
    refused(doc, blob, reason="accessor")


def test_a_non_finite_vertex_is_refused():
    doc, _ = tri()
    blob = (
        struct.pack("<9f", 0, 0, 0, math.inf, 0, 0, 0, 0, 1)
        + struct.pack("<3H", 0, 1, 2)
        + b"\x00\x00"
    )
    refused(doc, blob, reason="transform")


def test_oversized_extras_are_refused_and_never_read():
    doc, blob = tri(
        extras={"feature_id": "osm:1", "pad": "x" * glb_read.MAX_EXTRAS_BYTES}
    )
    refused(doc, blob, kind="resource_limit")


def test_extras_at_exactly_the_cap_parse():
    base = {"feature_id": "osm:1", "pad": ""}
    room = glb_read.MAX_EXTRAS_BYTES - len(json.dumps(base, separators=(",", ":")))
    doc, blob = tri(extras={**base, "pad": "x" * room})
    assert glb_read.parse(pack(doc, blob))[0].feature_id == "osm:1"


def test_oversized_extras_anywhere_are_refused():
    doc, blob = tri()
    doc["scenes"] = [{"nodes": [0], "extras": {"pad": "x" * glb_read.MAX_EXTRAS_BYTES}}]
    refused(doc, blob, kind="resource_limit")


def test_only_a_string_feature_id_is_read_from_extras():
    doc, blob = tri(
        extras={"feature_id": 7, "height_m": 99, "bindings": [{"uid": "x"}]}
    )
    (n,) = glb_read.parse(pack(doc, blob))
    assert n.feature_id is None
    assert not hasattr(n, "extras")


def test_a_matrix_is_refused():
    doc, blob = tri(matrix=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    refused(doc, blob, reason="transform")


def test_a_scale_is_refused_and_unit_scale_is_not():
    doc, blob = tri(scale=[1.0, 2.0, 1.0])
    refused(doc, blob, reason="scale")
    doc, blob = tri(scale=[1.0, 1.0, 1.0])
    glb_read.parse(pack(doc, blob))


def test_a_non_finite_translation_is_refused():
    doc, blob = tri()
    doc["nodes"][0]["translation"] = [
        0,
        "inf",
        0,
    ]  # JSON has no inf; a string is the attempt
    refused(doc, blob, reason="transform")


@pytest.mark.parametrize("literal", [b"NaN", b"Infinity", b"-Infinity"])
def test_a_nan_literal_in_the_json_is_refused(literal):
    # The JSON is otherwise valid, so only the literal can be what refuses it.
    with pytest.raises(SceneError) as e:
        glb_read.parse(_raw_json(b'{"asset":{"version":"2.0"},"x":' + literal + b"}"))
    assert e.value.detail["reason"] == "json"


def test_a_cycle_is_refused():
    doc, blob = tri()
    doc["nodes"].append({"children": [0]})
    doc["nodes"][0]["children"] = [1]
    refused(doc, blob, reason="graph")


def test_a_node_with_two_parents_is_refused():
    doc, blob = tri()
    doc["nodes"] += [{"children": [0]}, {"children": [0]}]
    refused(doc, blob, reason="graph")


def test_a_chain_deeper_than_the_cap_is_refused():
    doc, blob = tri()
    for i in range(glb_read.MAX_DEPTH):
        doc["nodes"].append({"children": [len(doc["nodes"]) - 1]})
    refused(doc, blob, reason="graph")


def test_too_many_nodes_is_refused():
    doc, blob = tri()
    doc["nodes"] += [{} for _ in range(glb_read.MAX_NODES)]
    refused(doc, blob, kind="resource_limit")


def test_aliased_accessors_cannot_amplify_past_the_vertex_total(monkeypatch):
    monkeypatch.setattr(glb_read, "MAX_VERTICES", 5)
    doc, blob = tri()
    doc["meshes"][0]["primitives"].append(
        copy.deepcopy(doc["meshes"][0]["primitives"][0])
    )
    refused(doc, blob, kind="resource_limit")


def test_a_mesh_shared_by_two_nodes_is_read_once(monkeypatch):
    monkeypatch.setattr(glb_read, "MAX_VERTICES", 3)
    doc, blob = tri()
    doc["nodes"].append({"mesh": 0})
    assert len(glb_read.parse(pack(doc, blob))) == 2


@pytest.mark.parametrize("edit", ["magic", "version", "length", "declared", "chunk"])
def test_a_malformed_container_is_refused(edit):
    doc, blob = tri()
    data = bytearray(pack(doc, blob))
    if edit == "magic":
        data[0:4] = b"glTX"
    elif edit == "version":
        data[4:8] = struct.pack("<I", 1)
    elif edit == "length":
        data += b"\x00\x00\x00\x00"
    elif edit == "declared":
        data[8:12] = struct.pack("<I", len(data) + 4)
    else:
        data[16:20] = b"JSOX"
    with pytest.raises(SceneError) as e:
        glb_read.parse(bytes(data))
    assert e.value.detail["reason"] == "container"


def test_a_required_extension_is_refused():
    doc, blob = tri()
    doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    refused(doc, blob, reason="extension")


def test_world_transforms_compose_through_the_parent():
    doc, blob = tri(translation=[1.0, 0.0, 0.0])
    half = math.sqrt(0.5)
    doc["nodes"].append(
        {
            "children": [0],
            "translation": [10.0, 0.0, 0.0],
            "rotation": [0, half, 0, half],
        }
    )
    n = glb_read.parse(pack(doc, blob))[0]
    # The parent turns +90 degrees about +Y: its local +X becomes -Z.
    assert n.translation == pytest.approx((10.0, 0.0, -1.0))
    assert n.rotation == pytest.approx((0, half, 0, half))


# ── GK B G1 to G3 ───────────────────────────────────────────────────────────


def _raw_json(js: bytes, blob: bytes = b"") -> bytes:
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + (8 + len(blob) if blob else 0)
    out = (
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<I4s", len(js), b"JSON")
        + js
    )
    return out + (struct.pack("<I4s", len(blob), b"BIN\x00") + blob if blob else b"")


def test_a_repeated_json_key_is_refused():
    with pytest.raises(SceneError) as e:
        glb_read.parse(_raw_json(b'{"asset":{"version":"2.0"},"nodes":[],"nodes":[]}'))
    assert e.value.detail["reason"] == "duplicate_key"


def test_json_nested_past_the_interpreter_is_a_typed_refusal():
    with pytest.raises(SceneError) as e:
        glb_read.parse(_raw_json(b'{"a":' + b"[" * 100_000 + b"]" * 100_000 + b"}"))
    assert e.value.detail["reason"] == "depth"


def test_json_nested_past_the_cap_is_refused():
    with pytest.raises(SceneError) as e:
        glb_read.parse(_raw_json(b'{"a":' + b"[" * 80 + b"]" * 80 + b"}"))
    assert e.value.detail["reason"] == "depth"


@pytest.mark.parametrize(
    "key, value, reason",
    [
        ("extensionsUsed", ["KHR_materials_unlit"], "extension"),
        ("animations", [{"channels": [], "samplers": []}], "animations"),
        ("skins", [{"joints": [0]}], "skins"),
    ],
)
def test_animation_skin_and_any_extension_are_refused(key, value, reason):
    doc, blob = tri()
    doc[key] = value
    refused(doc, blob, reason=reason)


def test_morph_targets_are_refused():
    doc, blob = tri()
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}]
    refused(doc, blob, reason="morph_targets")


def test_a_sparse_accessor_is_refused():
    doc, blob = tri()
    doc["accessors"][0]["sparse"] = {"count": 1}
    refused(doc, blob, reason="accessor")


@pytest.mark.parametrize("stride", [2, 13, 256])
def test_a_stride_off_the_4_to_252_grid_is_refused(stride):
    doc, blob = tri()
    doc["bufferViews"][0]["byteStride"] = stride
    refused(doc, blob, reason="bounds")


def test_an_index_accessor_must_be_unsigned_scalar():
    doc, blob = tri()
    doc["accessors"][1]["componentType"] = 5126
    refused(doc, blob, reason="accessor")


def test_two_nodes_with_one_feature_id_are_ambiguous():
    doc, blob = tri()
    doc["nodes"].append(dict(doc["nodes"][0]))
    refused(doc, blob, reason="ambiguous_feature_id")


def test_a_node_name_is_kept_short():
    doc, blob = tri(name="n" * 1000)
    assert glb_read.parse(pack(doc, blob))[0].name == "n" * glb_read.MAX_NAME_LENGTH


# ── GK B G2: one test per guard, each pinning its reason or limit ───────────


def _fix_length(data: bytes) -> bytes:
    return data[:8] + struct.pack("<I", len(data)) + data[12:]


def _empty() -> bytes:
    return pack({"asset": {"version": "2.0"}})


def _limit(e, name, cap, actual):
    assert e.detail == {"limit": f"return_{name}", "max": cap, "actual": actual}


def test_the_caps_follow_the_export_caps():
    f, t = glb_read.export.MAX_EXPORT_FEATURES, glb_read.export.MAX_TRIANGLES
    assert (glb_read.MAX_NODES, glb_read.MAX_MESHES, glb_read.MAX_ACCESSORS) == (
        2 * f + 16,
        2 * f,
        8 * f,
    )
    assert (glb_read.MAX_PRIMITIVES, glb_read.MAX_VERTICES) == (2 * f, 3 * t)
    assert glb_read.MAX_TRIANGLES == 2 * t


def test_the_byte_cap_is_inclusive(monkeypatch):
    data = pack(*tri())
    monkeypatch.setattr(glb_read, "MAX_RETURN_BYTES", len(data))
    glb_read.parse(data)
    monkeypatch.setattr(glb_read, "MAX_RETURN_BYTES", len(data) - 1)
    with pytest.raises(SceneError) as e:
        glb_read.parse(data)
    _limit(e.value, "bytes", len(data) - 1, len(data))


def test_the_json_cap_is_inclusive(monkeypatch):
    data = pack(*tri())
    n = struct.unpack_from("<I", data, 12)[0]
    monkeypatch.setattr(glb_read, "MAX_JSON_BYTES", n)
    glb_read.parse(data)
    monkeypatch.setattr(glb_read, "MAX_JSON_BYTES", n - 1)
    with pytest.raises(SceneError) as e:
        glb_read.parse(data)
    _limit(e.value, "json_bytes", n - 1, n)


def test_a_header_shorter_than_20_bytes_is_not_a_glb():
    with pytest.raises(SceneError) as e:
        glb_read.parse(struct.pack("<4sII", b"glTF", 2, 19) + bytes(7))
    assert e.value.detail["reason"] == "container"


def test_a_20_byte_glb_reaches_its_empty_json():
    with pytest.raises(SceneError) as e:
        glb_read.parse(struct.pack("<4sIII4s", b"glTF", 2, 20, 0, b"JSON"))
    assert e.value.detail["reason"] == "json"


def _container(data: bytes) -> None:
    with pytest.raises(SceneError) as e:
        glb_read.parse(data)
    assert e.value.detail["reason"] == "container"


def test_a_json_chunk_past_the_end_is_refused():
    data = bytearray(_empty())
    data[12:16] = struct.pack("<I", len(data) - 16)
    _container(bytes(data))


def test_a_short_trailing_chunk_header_is_refused():
    _container(_fix_length(_empty() + bytes(4)))


def test_a_trailing_chunk_must_be_one_whole_bin_chunk():
    _container(_fix_length(_empty() + struct.pack("<I4s", 0, b"XXXX")))
    _container(_fix_length(_empty() + struct.pack("<I4s", 0, b"BIN\x00") + bytes(4)))


def test_an_empty_bin_chunk_parses():
    assert (
        glb_read.parse(_fix_length(_empty() + struct.pack("<I4s", 0, b"BIN\x00"))) == []
    )


def test_a_json_chunk_that_is_not_an_object_is_refused():
    with pytest.raises(SceneError) as e:
        glb_read.parse(_raw_json(b"[]"))
    assert e.value.detail["reason"] == "json"


@pytest.mark.parametrize("nodes", [{}, [1]])
def test_a_top_level_list_of_non_objects_is_refused(nodes):
    doc, blob = tri()
    doc["nodes"] = nodes
    refused(doc, blob, reason="json")


def _nested(depth):
    v = []
    for _ in range(depth - 1):
        v = [v]
    return v


def test_nesting_is_capped_at_exactly_64():
    doc, blob = tri()
    doc["x"] = _nested(64)
    glb_read.parse(pack(doc, blob))
    doc["x"] = _nested(65)
    refused(doc, blob, reason="depth")


@pytest.mark.parametrize("size", [True, "44"])
def test_a_buffer_length_must_be_an_integer(size):
    doc, blob = tri()
    doc["buffers"][0]["byteLength"] = size
    refused(doc, blob, reason="buffer")


def test_an_empty_buffer_parses():
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 0}]}
    assert glb_read.parse(pack(doc)) == []


def _unused_view(**kw):
    doc, blob = tri()
    doc["bufferViews"].append({"buffer": 0, "byteOffset": 0, "byteLength": 8, **kw})
    return doc, blob


@pytest.mark.parametrize(
    "kw",
    [
        {"byteOffset": False},
        {"byteOffset": -4},
        {"byteLength": "8"},
        {"byteLength": None},
        {"byteStride": 4.0},
        {"byteStride": "8"},
        {"byteStride": 13},
        {"byteStride": 256},
    ],
)
def test_a_view_is_checked_even_when_nothing_reads_it(kw):
    refused(*_unused_view(**kw), reason="bounds")


@pytest.mark.parametrize("stride", [4, 252])
def test_a_stride_at_either_end_of_the_grid_parses(stride):
    glb_read.parse(pack(*_unused_view(byteStride=stride)))


def test_strided_positions_are_read_at_their_stride():
    pts = [(0, 0, 0), (1, 0, 0), (0, 0, 1)]
    pos = b"".join(struct.pack("<3f4x", *p) for p in pts)
    doc, _ = tri()
    doc["buffers"][0]["byteLength"] = 56
    doc["bufferViews"][0].update(byteLength=48, byteStride=16)
    doc["bufferViews"][1]["byteOffset"] = 48
    blob = pos + struct.pack("<3H", 0, 1, 2) + b"\x00\x00"
    assert glb_read.parse(pack(doc, blob))[0].positions == tuple(pts)


def test_a_position_stride_shorter_than_a_vertex_is_refused():
    doc, blob = tri()
    doc["bufferViews"][0]["byteStride"] = 8
    refused(doc, blob, reason="bounds")


def test_index_data_may_not_be_strided():
    doc, blob = tri()
    doc["bufferViews"][1]["byteStride"] = 4
    refused(doc, blob, reason="bounds")


@pytest.mark.parametrize("count", [True, 0, "3", None])
def test_an_accessor_count_must_be_a_positive_integer(count):
    doc, blob = tri()
    doc["accessors"][0]["count"] = count
    refused(doc, blob, reason="accessor")


def test_a_one_vertex_accessor_parses():
    doc, blob = tri()
    doc["accessors"][0]["count"] = 1
    del doc["meshes"][0]["primitives"][0]["indices"]
    assert glb_read.parse(pack(doc, blob))[0].positions == ((0, 0, 0),)


@pytest.mark.parametrize("off", [False, -4, "0"])
def test_an_accessor_offset_must_be_a_non_negative_integer(off):
    doc, blob = tri()
    doc["accessors"][0]["byteOffset"] = off
    refused(doc, blob, reason="bounds")


@pytest.mark.parametrize("i, kind", [(0, "VEC2"), (1, "VEC3")])
def test_an_accessor_of_the_wrong_type_is_refused(i, kind):
    doc, blob = tri()
    doc["accessors"][i]["type"] = kind
    refused(doc, blob, reason="accessor")


def test_an_index_count_not_a_multiple_of_3_is_refused():
    doc, blob = tri()
    doc["accessors"][1]["count"] = 2
    refused(doc, blob, reason="bounds")


@pytest.mark.parametrize("prims", [None, 5, [], [1]])
def test_a_mesh_needs_a_list_of_primitive_objects(prims):
    doc, blob = tri()
    doc["meshes"][0]["primitives"] = prims
    refused(doc, blob, reason="json")


def test_only_triangle_mode_is_read():
    doc, blob = tri()
    doc["meshes"][0]["primitives"][0]["mode"] = 1
    refused(doc, blob, reason="accessor")


@pytest.mark.parametrize("attrs", [None, {}, ["POSITION"]])
def test_a_primitive_needs_a_position(attrs):
    doc, blob = tri()
    doc["meshes"][0]["primitives"][0]["attributes"] = attrs
    refused(doc, blob, reason="accessor")


def _prims(doc, n):
    doc["meshes"][0]["primitives"] *= n
    return doc


def test_the_primitive_cap_is_inclusive(monkeypatch):
    monkeypatch.setattr(glb_read, "MAX_PRIMITIVES", 1)
    doc, blob = tri()
    glb_read.parse(pack(doc, blob))
    e = refused(_prims(doc, 2), blob, kind="resource_limit")
    _limit(e, "primitives", 1, 2)


def test_the_triangle_cap_is_inclusive(monkeypatch):
    monkeypatch.setattr(glb_read, "MAX_TRIANGLES", 1)
    doc, blob = tri()
    glb_read.parse(pack(doc, blob))
    monkeypatch.setattr(glb_read, "MAX_TRIANGLES", 2)
    e = refused(_prims(doc, 3), blob, kind="resource_limit")
    _limit(e, "triangles", 2, 3)


@pytest.mark.parametrize("mesh", [False, -1, 1, "0"])
def test_an_index_that_is_a_bool_negative_or_past_the_end_is_refused(mesh):
    doc, blob = tri()
    doc["nodes"][0]["mesh"] = mesh
    refused(doc, blob, reason="index")


def test_a_child_index_is_checked():
    doc, blob = tri()
    doc["nodes"].append({"children": [5]})
    refused(doc, blob, reason="index")


def test_children_must_be_a_list():
    doc, blob = tri()
    doc["nodes"][0]["children"] = {}
    refused(doc, blob, reason="json")


def test_a_node_that_lists_itself_as_a_child_is_refused():
    doc, blob = tri()
    doc["nodes"][0]["children"] = [0]
    refused(doc, blob, reason="graph")


def test_a_chain_exactly_at_the_depth_cap_parses():
    doc, blob = tri()
    for _ in range(glb_read.MAX_DEPTH - 1):
        doc["nodes"].append({"children": [len(doc["nodes"]) - 1]})
    assert len(glb_read.parse(pack(doc, blob))) == glb_read.MAX_DEPTH


@pytest.mark.parametrize("t", [5, [0, 0], [True, 0, 0], [9.5, 0, 0]])
def test_a_translation_must_be_three_finite_numbers(t):
    doc, blob = tri(translation=t)
    js = json.dumps(doc).encode().replace(b"9.5", b"1e400")  # parses to inf
    with pytest.raises(SceneError) as e:
        glb_read.parse(_raw_json(js, blob))
    assert e.value.detail["reason"] == "transform"


def test_a_rotation_that_is_not_a_unit_quaternion_is_refused():
    refused(*tri(rotation=[0, 0, 0, 2]), reason="transform")


def test_a_scale_exactly_at_the_tolerance_parses(monkeypatch):
    monkeypatch.setattr(glb_read, "SCALE_TOLERANCE", 0.5)
    glb_read.parse(pack(*tri(scale=[1.5, 1, 1])))


@pytest.mark.parametrize(
    "fid, kept",
    [("", None), ("a" * 512, "a" * 512), ("a" * 513, None)],
)
def test_a_feature_id_is_one_to_max_id_length_characters(fid, kept):
    assert glb_read.MAX_ID_LENGTH == 512
    assert glb_read.parse(pack(*tri({"feature_id": fid})))[0].feature_id == kept


def _matrix(q):
    x, y, z, w = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def test_a_general_rotation_composes_like_its_matrices():
    q1 = list(np.array([0.1, 0.2, 0.3, 0.9]) / np.linalg.norm([0.1, 0.2, 0.3, 0.9]))
    q2 = list(np.array([0.4, -0.1, 0.2, 0.8]) / np.linalg.norm([0.4, -0.1, 0.2, 0.8]))
    t1, t2 = [10.0, -5.0, 2.0], [1.0, 2.0, 3.0]
    doc, blob = tri(translation=t2, rotation=q2)
    doc["nodes"].append({"children": [0], "translation": t1, "rotation": q1})
    n = glb_read.parse(pack(doc, blob))[0]
    r1 = _matrix(q1)
    assert _matrix(n.rotation) == pytest.approx(r1 @ _matrix(q2))
    assert n.translation == pytest.approx(np.array(t1) + r1 @ np.array(t2))


# ── GK B (62) NOT OK @781b75c7 ──────────────────────────────────────────────


@pytest.mark.parametrize("ct", [[5123], {"t": 5123}, True, "5123", None])
def test_an_index_component_type_of_any_other_shape_is_a_typed_refusal(ct):
    doc, blob = tri()
    doc["accessors"][1]["componentType"] = ct
    refused(doc, blob, reason="accessor")


def test_translations_that_overflow_when_composed_are_refused():
    doc, blob = tri(translation=[1.7e308, 0.0, 0.0])
    doc["nodes"].append({"children": [0], "translation": [1.7e308, 0.0, 0.0]})
    refused(doc, blob, reason="non_finite_transform")


def test_a_nan_from_composition_is_refused():
    # inf - inf: the child turns the parent's +X into -X, so the sum is +inf + -inf.
    doc, blob = tri(translation=[1.7e308, 0.0, 0.0])
    doc["nodes"].append(
        {
            "children": [0],
            "translation": [1.7e308, 0.0, 0.0],
            "rotation": [0.0, 1.0, 0.0, 0.0],
        }
    )
    doc["nodes"].append({"children": [1], "translation": [1.7e308, 0.0, 0.0]})
    refused(doc, blob, reason="non_finite_transform")
