"""
Issue 06 slice (b), GK B R1: read a returned GLB as untrusted input.

The GLB a Blender job returns (`blender_job`) was written by a process this app
does not trust, so every structure is bounded before it is used:

* the container: magic, version 2, a declared length equal to the bytes, one
  JSON chunk (capped) and at most one BIN chunk;
* exactly one buffer, embedded in the BIN chunk. Any ``uri`` anywhere (a
  buffer, an image) and any ``extensionsRequired`` is refused, so nothing is
  ever fetched or opened;
* every bufferView, and every accessor that is read, lies inside its buffer
  and view: offset, length, stride and count times element size. POSITION is
  FLOAT VEC3 and every index is in range;
* counts of nodes, meshes, accessors, primitives, vertices and triangles are
  capped, the node graph is a forest with bounded depth, and every number read
  is finite;
* transforms: a ``matrix`` and a scale other than 1 are refused (native size,
  V18);
* ``extras`` are capped in size wherever they appear. Only a node's
  ``extras.feature_id`` is read, and only as a string. Nothing else in them is
  read, evaluated or passed on.

`parse` returns plain tuples: per node its feature id (or None), its world
translation and rotation in glTF axes, and its mesh's vertex positions in the
node's own frame.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from typing import Any

from services.site_scene import export
from services.site_scene.contract import SceneError

MAX_RETURN_BYTES = 64_000_000
MAX_JSON_BYTES = 8_000_000
MAX_NODES = 2 * export.MAX_EXPORT_FEATURES + 16
MAX_MESHES = 2 * export.MAX_EXPORT_FEATURES
MAX_ACCESSORS = 8 * export.MAX_EXPORT_FEATURES
MAX_PRIMITIVES = 2 * export.MAX_EXPORT_FEATURES
MAX_VERTICES = 3 * export.MAX_TRIANGLES
MAX_TRIANGLES = 2 * export.MAX_TRIANGLES
MAX_DEPTH = 16
MAX_EXTRAS_BYTES = 4096
MAX_ID_LENGTH = 512
MAX_NAME_LENGTH = 128
SCALE_TOLERANCE = 1e-6

_FLOAT, _UINT8, _UINT16, _UINT32 = 5126, 5121, 5123, 5125
_INDEX_SIZE = {_UINT8: 1, _UINT16: 2, _UINT32: 4}
_INDEX_FMT = {_UINT8: "B", _UINT16: "H", _UINT32: "I"}


@dataclass(frozen=True)
class Node:
    index: int
    name: str | None
    feature_id: str | None
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    positions: tuple[tuple[float, float, float], ...] | None


def _bad(reason: str, message: str) -> SceneError:
    return SceneError(
        "invalid_geometry",
        f"The returned scene was refused: {message}",
        {"field": "artifact", "reason": reason},
    )


def _limit(name: str, cap: int, actual: int) -> SceneError:
    return SceneError(
        "resource_limit",
        f"The returned scene is too large: {name} over {cap}",
        {"limit": f"return_{name}", "max": cap, "actual": actual},
    )


def _int(v: Any, what: str, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < hi:
        raise _bad("index", f"{what} is not a valid index")
    return v


def _nums(v: Any, n: int, what: str) -> tuple[float, ...]:
    if (
        not isinstance(v, list)
        or len(v) != n
        or not all(
            isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c)
            for c in v
        )
    ):
        raise _bad("transform", f"{what} must be {n} finite numbers")
    return tuple(float(c) for c in v)


def _no_uri(obj: Any, depth: int = 0) -> None:
    """Refuse a `uri` key at any depth, so nothing outside the GLB is ever named for reading."""
    if depth > 64:
        raise _bad("depth", "the document nests too deep")
    if isinstance(obj, dict):
        if "uri" in obj:
            raise _bad("external_uri", "it names an external file")
        for v in obj.values():
            _no_uri(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _no_uri(v, depth + 1)


def _extras_capped(obj: Any) -> None:
    """Every `extras` value, wherever it is, encodes to at most MAX_EXTRAS_BYTES. It is never read further.

    It runs after `_no_uri`, which walks every path this walks and has already
    refused any nesting past 64, so it needs no depth guard of its own.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "extras":
                size = len(json.dumps(v, separators=(",", ":")).encode("utf-8"))
                if size > MAX_EXTRAS_BYTES:
                    raise _limit("extras_bytes", MAX_EXTRAS_BYTES, size)
            else:
                _extras_capped(v)
    elif isinstance(obj, list):
        for v in obj:
            _extras_capped(v)


class _Duplicate(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out = dict(pairs)
    if len(out) != len(pairs):
        raise _Duplicate()
    return out


def _no_constant(name: str) -> Any:
    raise ValueError(name)  # NaN, Infinity, -Infinity are not JSON numbers here


def _chunks(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) > MAX_RETURN_BYTES:
        raise _limit("bytes", MAX_RETURN_BYTES, len(data))
    if len(data) < 20:
        raise _bad("container", "it is not a GLB")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise _bad("container", "it is not a glTF 2.0 binary of the length it declares")
    js_len, js_type = struct.unpack_from("<I4s", data, 12)
    if js_type != b"JSON":
        raise _bad("container", "its first chunk is not JSON")
    if js_len > MAX_JSON_BYTES:
        raise _limit("json_bytes", MAX_JSON_BYTES, js_len)
    end = 20 + js_len
    if end > len(data):
        raise _bad("container", "its JSON chunk runs past the end")
    try:
        doc = json.loads(
            data[20:end].decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_no_constant,
        )
    except _Duplicate:
        raise _bad("duplicate_key", "its JSON repeats a key") from None
    except RecursionError:
        raise _bad("depth", "the document nests too deep") from None
    except (UnicodeDecodeError, ValueError):
        raise _bad("json", "its JSON chunk does not parse") from None
    if not isinstance(doc, dict):
        raise _bad("json", "its JSON chunk is not an object")
    blob = b""
    if end < len(data):
        if end + 8 > len(data):
            raise _bad("container", "a chunk header runs past the end")
        bin_len, bin_type = struct.unpack_from("<I4s", data, end)
        if bin_type != b"BIN\x00" or end + 8 + bin_len != len(data):
            raise _bad(
                "container", "it holds a chunk other than one trailing BIN chunk"
            )
        blob = data[end + 8 :]
    return doc, blob


def _list(doc: dict[str, Any], key: str, cap: int) -> list[Any]:
    v = doc.get(key, [])
    if not isinstance(v, list):
        raise _bad("json", f"{key} is not a list")
    if len(v) > cap:
        raise _limit(key, cap, len(v))
    if not all(isinstance(x, dict) for x in v):
        raise _bad("json", f"every {key} entry must be an object")
    return v


def _views(doc: dict[str, Any], blob: bytes) -> list[tuple[int, int, int | None]]:
    buffers = _list(doc, "buffers", 1)
    if buffers:
        size = buffers[0].get("byteLength")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= len(blob)
        ):
            raise _bad("buffer", "the buffer is not embedded in the BIN chunk")
    out = []
    for v in _list(doc, "bufferViews", MAX_ACCESSORS):
        _int(v.get("buffer"), "a bufferView's buffer", len(buffers))
        off, ln = v.get("byteOffset", 0), v.get("byteLength")
        stride = v.get("byteStride")
        for x in (off, ln):
            if not isinstance(x, int) or isinstance(x, bool) or x < 0:
                raise _bad("bounds", "a bufferView's offset or length is invalid")
        if off + ln > buffers[0]["byteLength"]:
            raise _bad("bounds", "a bufferView runs past its buffer")
        # A bool is 0 or 1, outside the grid, so it needs no check of its own.
        if stride is not None and (
            not isinstance(stride, int) or not 4 <= stride <= 252 or stride % 4
        ):
            raise _bad("bounds", "a bufferView's stride is invalid")
        out.append((off, ln, stride))
    return out


def _read_accessor(doc, views, blob: bytes, i: Any, kind: str, room: int) -> list[Any]:
    """Accessor `i`, read only when its count fits in `room`, what is left of the totals' cap."""
    accessors = doc.get("accessors", [])
    a = accessors[_int(i, "an accessor", len(accessors))]
    if "sparse" in a:
        raise _bad("accessor", "sparse accessors are not read")
    count = a.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise _bad("accessor", "an accessor's count is invalid")
    off_v, len_v, stride = views[
        _int(a.get("bufferView"), "an accessor's bufferView", len(views))
    ]
    off_a = a.get("byteOffset", 0)
    if not isinstance(off_a, int) or isinstance(off_a, bool) or off_a < 0:
        raise _bad("bounds", "an accessor's offset is invalid")
    if kind == "position":
        if a.get("componentType") != _FLOAT or a.get("type") != "VEC3":
            raise _bad("accessor", "POSITION must be FLOAT VEC3")
        if count > room:
            raise _limit("vertices", MAX_VERTICES, MAX_VERTICES - room + count)
        elem, step = 12, stride or 12
        if step < elem:
            raise _bad("bounds", "a stride is shorter than its element")
    else:
        ct = a.get("componentType")
        if ct not in _INDEX_SIZE or a.get("type") != "SCALAR":
            raise _bad("accessor", "indices must be unsigned SCALAR")
        if count > room:
            raise _limit(
                "triangles", MAX_TRIANGLES, (3 * MAX_TRIANGLES - room + count) // 3
            )
        elem = step = _INDEX_SIZE[ct]
        if stride is not None:
            raise _bad("bounds", "index data may not be strided")
    need = off_a + step * (count - 1) + elem
    if need > len_v:
        raise _bad("bounds", "an accessor runs past its bufferView")
    base = off_v + off_a
    if kind == "position":
        out = [struct.unpack_from("<3f", blob, base + step * k) for k in range(count)]
        if not all(math.isfinite(c) for p in out for c in p):
            raise _bad("transform", "a vertex position is not finite")
        return out
    return list(struct.unpack_from(f"<{count}{_INDEX_FMT[ct]}", blob, base))


def _mesh_positions(
    doc, views, blob: bytes, m: Any, budget: list[int]
) -> tuple[tuple[float, float, float], ...]:
    meshes = doc.get("meshes", [])
    mesh = meshes[_int(m, "a node's mesh", len(meshes))]
    prims = mesh.get("primitives")
    if (
        not isinstance(prims, list)
        or not prims
        or not all(isinstance(p, dict) for p in prims)
    ):
        raise _bad("json", "a mesh has no primitives")
    budget[0] -= len(prims)
    if budget[0] < 0:
        raise _limit("primitives", MAX_PRIMITIVES, MAX_PRIMITIVES - budget[0])
    # Accessors may alias one view, so the totals read, not the bytes, are what is capped.
    positions: list[tuple[float, float, float]] = []
    for p in prims:
        if p.get("targets"):
            raise _bad("morph_targets", "it carries morph targets")
        if p.get("mode", 4) != 4:
            raise _bad("accessor", "only triangle meshes are read")
        attrs = p.get("attributes")
        if not isinstance(attrs, dict) or "POSITION" not in attrs:
            raise _bad("accessor", "a primitive has no POSITION")
        pos = _read_accessor(doc, views, blob, attrs["POSITION"], "position", budget[1])
        budget[1] -= len(pos)
        if "indices" in p:
            idx = _read_accessor(doc, views, blob, p["indices"], "index", budget[2])
            budget[2] -= len(idx)
            if len(idx) % 3 or any(k >= len(pos) for k in idx):
                raise _bad("bounds", "an index is out of range")
        positions += pos
    return tuple(positions)


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def rotate(q, v):
    """Rotate vector `v` by unit quaternion `q` (x, y, z, w)."""
    x, y, z, _ = _qmul(_qmul(q, (*v, 0.0)), (-q[0], -q[1], -q[2], q[3]))
    return (x, y, z)


def _local(
    node: dict[str, Any],
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    if "matrix" in node:
        raise _bad("transform", "a node uses a matrix")
    t = _nums(node.get("translation", [0, 0, 0]), 3, "a translation")
    q = _nums(node.get("rotation", [0, 0, 0, 1]), 4, "a rotation")
    s = _nums(node.get("scale", [1, 1, 1]), 3, "a scale")
    if any(abs(c - 1.0) > SCALE_TOLERANCE for c in s):
        raise _bad("scale", "a node is scaled (native size only)")
    norm = math.sqrt(sum(c * c for c in q))
    if not abs(norm - 1.0) < 1e-3:
        raise _bad("transform", "a rotation is not a unit quaternion")
    return t, tuple(c / norm for c in q)


def _feature_id(node: dict[str, Any]) -> str | None:
    extras = node.get("extras")
    fid = extras.get("feature_id") if isinstance(extras, dict) else None
    if isinstance(fid, str) and 0 < len(fid) <= MAX_ID_LENGTH:
        return fid
    return None


def parse(data: bytes) -> list[Node]:
    """The returned GLB's nodes, after every check above. Raises `SceneError` on anything else."""
    doc, blob = _chunks(data)
    _no_uri(doc)
    if doc.get("extensionsRequired") or doc.get("extensionsUsed"):
        raise _bad("extension", "it uses an extension")
    for key in ("animations", "skins"):
        if doc.get(key):
            raise _bad(key, f"it carries {key}")
    _extras_capped(doc)
    nodes = _list(doc, "nodes", MAX_NODES)
    _list(doc, "meshes", MAX_MESHES)
    _list(doc, "accessors", MAX_ACCESSORS)
    views = _views(doc, blob)
    parent: dict[int, int] = {}
    for i, n in enumerate(nodes):
        kids = n.get("children", [])
        if not isinstance(kids, list):
            raise _bad("json", "a node's children is not a list")
        for c in kids:
            c = _int(c, "a child", len(nodes))
            # A node that is its own child is a cycle, refused by the depth walk below.
            if c in parent:
                raise _bad("graph", "the node graph is not a tree")
            parent[c] = i
    budget = [MAX_PRIMITIVES, MAX_VERTICES, 3 * MAX_TRIANGLES]
    meshes: dict[int, tuple[tuple[float, float, float], ...]] = {}
    out = []
    for i, n in enumerate(nodes):
        chain, k = [i], i
        while k in parent:
            k = parent[k]
            chain.append(k)
            if len(chain) > MAX_DEPTH:
                raise _bad("graph", "the node graph is too deep or cyclic")
        t, q = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
        for k in reversed(chain):
            lt, lq = _local(nodes[k])
            r = rotate(q, lt)
            t = (t[0] + r[0], t[1] + r[1], t[2] + r[2])
            q = _qmul(q, lq)
        positions = None
        if "mesh" in n:
            m = _int(n["mesh"], "a node's mesh", len(doc.get("meshes", [])))
            if m not in meshes:
                meshes[m] = _mesh_positions(doc, views, blob, m, budget)
            positions = meshes[m]
        name = n.get("name")
        name = name[:MAX_NAME_LENGTH] if isinstance(name, str) else None
        out.append(Node(i, name, _feature_id(n), t, q, positions))
    seen: set[str] = set()
    for node in out:
        if node.feature_id is not None:
            if node.feature_id in seen:
                # G3: an id on two nodes is ambiguous; neither is picked.
                raise _bad(
                    "ambiguous_feature_id", "two nodes carry the same feature id"
                )
            seen.add(node.feature_id)
    return out
