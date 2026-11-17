"""Issue 06 slice (b), GK B R1: the returned GLB is untrusted; every structure is bounded."""

from __future__ import annotations

import copy
import json
import math
import struct
from pathlib import Path

import pytest

from services.site_scene import glb_read
from services.site_scene.contract import SceneError

FIXTURE = Path(__file__).resolve().parents[2] / "frontend/src/site3d/__fixtures__/scene-export.glb"


def pack(doc: dict, blob: bytes = b"") -> bytes:
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + (8 + len(blob) if blob else 0)
    out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
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
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36},
                        {"buffer": 0, "byteOffset": 36, "byteLength": 6}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                      {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0, "extras": extras if extras is not None else {"feature_id": "osm:1"}, **node}],
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
    blob = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 0, 1) + struct.pack("<3H", 0, 1, 3) + b"\x00\x00"
    refused(doc, blob, reason="bounds")


def test_position_must_be_float_vec3():
    doc, blob = tri()
    doc["accessors"][0]["componentType"] = 5123
    refused(doc, blob, reason="accessor")


def test_a_non_finite_vertex_is_refused():
    doc, _ = tri()
    blob = struct.pack("<9f", 0, 0, 0, math.inf, 0, 0, 0, 0, 1) + struct.pack("<3H", 0, 1, 2) + b"\x00\x00"
    refused(doc, blob, reason="transform")


def test_oversized_extras_are_refused_and_never_read():
    doc, blob = tri(extras={"feature_id": "osm:1", "pad": "x" * glb_read.MAX_EXTRAS_BYTES})
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
    doc, blob = tri(extras={"feature_id": 7, "height_m": 99, "bindings": [{"uid": "x"}]})
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
    doc["nodes"][0]["translation"] = [0, "inf", 0]  # JSON has no inf; a string is the attempt
    refused(doc, blob, reason="transform")


def test_a_nan_literal_in_the_json_is_refused():
    doc, blob = tri()
    raw = pack(doc, blob).replace(b'"asset"', b'"x":NaN,"asset"', 1)
    # keep the declared lengths true for the edited JSON
    js = raw[20:20 + struct.unpack_from("<I", raw, 12)[0]]
    js = js.rstrip(b" ")
    js += b" " * (-len(js) % 4)
    blobpart = raw[20 + struct.unpack_from("<I", raw, 12)[0]:]
    body = struct.pack("<I4s", len(js), b"JSON") + js + blobpart
    data = struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    with pytest.raises(SceneError) as e:
        glb_read.parse(data)
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
    doc["meshes"][0]["primitives"].append(copy.deepcopy(doc["meshes"][0]["primitives"][0]))
    refused(doc, blob, kind="resource_limit")


def test_a_mesh_shared_by_two_nodes_is_read_once(monkeypatch):
    monkeypatch.setattr(glb_read, "MAX_VERTICES", 3)
    doc, blob = tri()
    doc["nodes"].append({"mesh": 0})
    assert len(glb_read.parse(pack(doc, blob))) == 2


@pytest.mark.parametrize("edit", ["magic", "version", "length", "chunk"])
def test_a_malformed_container_is_refused(edit):
    doc, blob = tri()
    data = bytearray(pack(doc, blob))
    if edit == "magic":
        data[0:4] = b"glTX"
    elif edit == "version":
        data[4:8] = struct.pack("<I", 1)
    elif edit == "length":
        data += b"\x00\x00\x00\x00"
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
    doc["nodes"].append({"children": [0], "translation": [10.0, 0.0, 0.0], "rotation": [0, half, 0, half]})
    n = glb_read.parse(pack(doc, blob))[0]
    # The parent turns +90 degrees about +Y: its local +X becomes -Z.
    assert n.translation == pytest.approx((10.0, 0.0, -1.0))
    assert n.rotation == pytest.approx((0, half, 0, half))
