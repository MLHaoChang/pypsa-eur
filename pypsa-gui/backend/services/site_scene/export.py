"""
SC-04 (issue 05, Q-SS-2 part a): the neutral "Export for Blender" bundle.

One pointed scene generation becomes a deterministic zip of two files:

* ``scene.glb``: glTF 2.0 binary, Y-up, metres (``frames.enu_to_gltf``). A root
  node ``site:<id>`` holds one node per exported feature, named by its feature
  id. Buildings are prisms about their footprint centroid; placed equipment is
  a proxy box whose heading is the node's rotation, never baked into the
  vertices, and no node carries a scale (native size, V18). Node ``extras``
  carry the feature id, kind, source class and direct bindings, which glTF
  importers such as Blender's read as custom properties. The extras, not the
  node name, are the authoritative id: some importers rewrite names (three.js
  drops the ``:``).
* ``manifest.json``: the canonical record (sorted keys, compact): Site origin,
  axes and units, scene revision, generation and its sha256, features,
  bindings, groups, named omissions, media policy and content hashes.
  ``semantic_sha256`` covers everything except ``exported_at`` and ``hashes``,
  so an equivalent export has the same semantic records (V29).

Nothing here needs Blender, starts a process or writes a file (V31): the zip is
built in memory and returned. A feature that cannot be exported faithfully
(non-finite numbers, fewer than three distinct corners, no area, a footprint
that crosses itself, a ring of rings, a missing footprint or height) is a
named omission, never a guessed shape. No source media exist before SC-06, so
the only media policy is ``none``: no photo, image or texture is ever included.

The GLB writer is stdlib ``struct``/``json``: the geometry is prisms and boxes,
so no glTF library is needed (GK B plan review).
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import zipfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from services.site_scene import frames, native, store
from services.site_scene.contract import SceneError

FORMAT = "pypsa-studio.site-scene-export"
SCHEMA_VERSION = 1
# Checked before any geometry is built (features, triangles) and after the zip
# is written (bytes). Native admission already caps buildings at
# native.MAX_BUILDINGS and their prisms at native.MAX_TRIANGLES; equipment
# boxes add 12 triangles each.
MAX_EXPORT_FEATURES = 4000
MAX_TRIANGLES = 250_000
MAX_EXPORT_BYTES = 16_000_000
BOX_TRIANGLES = native.prism_triangles(4)

NOTE = (
    "A snapshot of one scene revision: not a live link to the model, and not a certified model. "
    "Later edits in PyPSA Studio do not update it."
)
SOURCE_CLASS = {
    "osm": "mapped",
    "greenfield": "proposed",
    "manual": "drawn",
    "layout": "traced",
}
BINDING_FIELDS = (
    "id",
    "feature",
    "class",
    "uid",
    "name_at_bind",
    "carrier",
    "role",
    "weight",
)
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_FLOAT, _UINT16, _UINT32 = 5126, 5123, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


class ExportRequest(BaseModel):
    """POST .../scene/exports: format and media policy are explicit; nothing else is accepted (E8)."""

    model_config = ConfigDict(extra="forbid")
    format: Literal["glb_manifest"]
    media_policy: Literal["none"]


def parse_request(body: Any) -> ExportRequest:
    if not isinstance(body, dict):
        raise SceneError(
            "invalid_input", "Export: send format and media_policy", {"field": None}
        )
    try:
        return ExportRequest.model_validate(body)
    except ValidationError as exc:
        err = exc.errors()[0]
        field = str(err["loc"][0]) if err.get("loc") else None
        words = {
            "format": "Export format: only glb_manifest is available",
            "media_policy": "Media: only none is available, since no source photos exist yet",
        }
        raise SceneError(
            "invalid_input",
            words.get(field, f"Export: {field} is not accepted"),
            {"field": field},
        ) from None


# ── reading one generation (E4) ──────────────────────────────────────────────


def read_snapshot(project_dir, site_id: str) -> dict[str, Any]:
    """
    The pointed generation, read once under the scene mutex. The generation's
    bytes are checked against the pointer's sha256 by the store; the hash in
    the manifest is recomputed over what was parsed, so it names exactly the
    records exported. Everything after this works on the returned copy, so a
    later commit cannot mix generations into one bundle.
    """
    with store.project_lock(project_dir):
        pointer = store._read_pointer(project_dir, site_id)
        if pointer is None:
            raise SceneError(
                "not_found",
                "This site has no 3D scene yet: generate its buildings first, then export",
            )
        gen = store._read_generation(project_dir, site_id, pointer)
    return {
        "site_id": site_id,
        "revision": pointer["revision"],
        "generation": pointer["generation"],
        "generation_sha256": store.sha256(gen),
        "site": gen["site"],
        "buildings": gen["buildings"],
        "asset_footprints": gen.get("asset_footprints") or {},
        "associations": gen.get("associations") or [],
        "groups": gen.get("groups") or [],
    }


# ── geometry ─────────────────────────────────────────────────────────────────


class _Omit(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ring(raw: Any) -> list[tuple[float, float]]:
    """A counter-clockwise simple ring with distinct consecutive corners, or `_Omit`."""
    if not isinstance(raw, list) or not all(
        isinstance(p, (list, tuple)) and len(p) == 2 and all(_finite(c) for c in p)
        for p in raw
    ):
        raise _Omit("invalid_footprint")
    ring: list[tuple[float, float]] = []
    for p in raw:
        q = (float(p[0]), float(p[1]))
        if not ring or ring[-1] != q:
            ring.append(q)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    if len(ring) < 3:
        raise _Omit("invalid_footprint")
    try:
        return native.check_footprint(ring, "export")
    except SceneError as e:
        # Over MAX_FOOTPRINT_VERTICES is only too detailed, not invalid (GK F3).
        reason = "footprint_too_detailed" if e.kind == "resource_limit" else "invalid_footprint"
        raise _Omit(reason) from None


def _positive(v: Any) -> float:
    if not _finite(v) or not 0 < v <= native.MAX_HEIGHT_M:
        raise _Omit("invalid_height")
    return float(v)


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _triangulate(ring: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Ear clipping of a simple counter-clockwise ring (n <= MAX_FOOTPRINT_VERTICES); collinear corners are skipped."""
    left, tris = list(range(len(ring))), []
    while len(left) > 3:
        for k in range(len(left)):
            a, b, c = left[k - 1], left[k], left[(k + 1) % len(left)]
            turn = _cross(ring[a], ring[b], ring[c])
            if abs(turn) < 1e-12:  # b lies on a-c: drop it, no sliver triangle
                left.pop(k)
                break
            if turn < 0:
                continue  # reflex corner
            if any(
                _inside(ring[p], ring[a], ring[b], ring[c])
                for p in left
                if p not in (a, b, c)
            ):
                continue
            tris.append((a, b, c))
            left.pop(k)
            break
        else:
            raise _Omit("invalid_footprint")
    if abs(_cross(ring[left[0]], ring[left[1]], ring[left[2]])) >= 1e-12:
        tris.append((left[0], left[1], left[2]))
    return tris


def _inside(p, a, b, c) -> bool:
    return _cross(a, b, p) >= 0 and _cross(b, c, p) >= 0 and _cross(c, a, p) >= 0


def _centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    area, cx, cy = 0.0, 0.0, 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        w = x0 * y1 - x1 * y0
        area += w
        cx += (x0 + x1) * w
        cy += (y0 + y1) * w
    return cx / (3 * area), cy / (3 * area)


def _prism(
    ring: list[tuple[float, float]], height: float, pivot: tuple[float, float]
) -> tuple[list[tuple[float, float, float]], list[int]]:
    """Positions in glTF axes (relative to `pivot` at ground) and outward-wound triangle indices of a prism."""
    n = len(ring)
    local = [(x - pivot[0], y - pivot[1]) for x, y in ring]
    positions = [frames.enu_to_gltf((e, nn, 0.0)) for e, nn in local]
    positions += [frames.enu_to_gltf((e, nn, height)) for e, nn in local]
    indices: list[int] = []
    for a, b, c in _triangulate(ring):
        indices += [a, c, b]  # floor faces down
        indices += [n + a, n + b, n + c]  # roof faces up
    for i in range(n):
        j = (i + 1) % n
        indices += [i, j, n + j, i, n + j, n + i]
    return positions, indices


def _zero(v: float) -> float:
    return v + 0.0  # -0.0 -> 0.0, so equal scenes write equal bytes


def _check_building(b: dict[str, Any]) -> tuple[list[tuple[float, float]], float]:
    """The validated (ring, height) of a building, or `_Omit`; no geometry is built."""
    return _ring(b.get("footprint")), _positive(b.get("height_m"))


def _building(
    b: dict[str, Any], ring: list[tuple[float, float]], height: float
) -> dict[str, Any]:
    pivot = _centroid(ring)
    positions, indices = _prism(ring, height, pivot)
    src = b.get("source") or {}
    return {
        "id": b["id"],
        "kind": "building",
        "translation": frames.enu_to_gltf((pivot[0], pivot[1], 0.0)),
        "rotation": None,
        "positions": positions,
        "indices": indices,
        "record": {
            "source": {
                "namespace": src.get("namespace"),
                "source_id": src.get("source_id"),
            },
            "source_class": SOURCE_CLASS.get(src.get("namespace"), "unknown"),
            "height_evidence": b.get("height_evidence"),
            "representation": "prism",
            "dims": {
                "height_m": height,
                "footprint_area_m2": frames.polygon_area(ring),
            },
        },
    }


def _check_equipment(placement: Any, fp: Any) -> tuple[float, float, float]:
    """The validated (width, depth, height) of a placed box, or `_Omit`; no geometry is built."""
    if not isinstance(fp, dict):
        raise _Omit("no_footprint")
    if not isinstance(placement, dict) or not all(
        _finite(placement.get(k)) for k in ("x", "y", "heading")
    ):
        raise _Omit("invalid_placement")
    width, depth = fp.get("width_m"), fp.get("depth_m")
    if not (_finite(width) and _finite(depth) and width > 0 and depth > 0):
        raise _Omit("invalid_footprint")
    if fp.get("height_m") is None:
        raise _Omit("height_not_set")
    return float(width), float(depth), _positive(fp["height_m"])


def _equipment(
    key: str, placement: dict[str, Any], width: float, depth: float, height: float
) -> dict[str, Any]:
    # The box in its own frame (heading 0); the heading is the node's rotation about up.
    ring = frames.rectangle((0.0, 0.0), width, depth, 0.0)
    positions, indices = _prism(ring, height, (0.0, 0.0))
    h = math.radians(placement["heading"])
    # Heading is clockwise from north seen from above: a rotation of -h about up (+Y in glTF).
    rotation = (0.0, math.sin(-h / 2), 0.0, math.cos(-h / 2))
    return {
        "id": key,
        "kind": "equipment",
        "translation": frames.enu_to_gltf(
            (float(placement["x"]), float(placement["y"]), 0.0)
        ),
        "rotation": rotation,
        "positions": positions,
        "indices": indices,
        "record": {
            "source": {"namespace": "placement", "source_id": key},
            "source_class": "model_placement",
            "representation": "proxy_box",
            "heading_deg": float(placement["heading"]),
            "dims": {
                "width_m": width,
                "depth_m": depth,
                "height_m": height,
            },
        },
    }


def _limit(name: str, cap: int, actual: int) -> SceneError:
    if name == "export_bytes":
        words = f"the file would be {native.size_text(actual)}, over the {native.size_text(cap)} limit"
    else:
        words = f"{actual:,} {name} is over the {cap:,} limit"
    return SceneError(
        "resource_limit",
        f"Too large to export: {words}",
        {"limit": name, "max": cap, "actual": actual},
    )


def _features(
    snap: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Every feature validated and counted before any geometry is built (E7):
    the feature count first, then the triangle count of what passed, then the meshes.
    """
    buildings = snap.get("buildings") or []
    placements = (snap.get("site") or {}).get("placements") or {}
    footprints = snap.get("asset_footprints") or {}
    if len(buildings) + len(placements) > MAX_EXPORT_FEATURES:
        raise _limit("features", MAX_EXPORT_FEATURES, len(buildings) + len(placements))
    admitted, omissions = [], []
    for b in buildings:
        try:
            ring, height = _check_building(b)
            admitted.append(
                (
                    native.prism_triangles(len(ring)),
                    lambda b=b, r=ring, h=height: _building(b, r, h),
                )
            )
        except _Omit as o:
            omissions.append(
                {"feature": b.get("id"), "kind": "building", "reason": o.reason}
            )
    for key in sorted(placements):
        try:
            dims = _check_equipment(placements[key], footprints.get(key))
            admitted.append(
                (BOX_TRIANGLES, lambda k=key, d=dims: _equipment(k, placements[k], *d))
            )
        except _Omit as o:
            omissions.append({"feature": key, "kind": "equipment", "reason": o.reason})
    triangles = sum(n for n, _make in admitted)
    if triangles > MAX_TRIANGLES:
        raise _limit("triangles", MAX_TRIANGLES, triangles)
    return [make() for _n, make in admitted], omissions


# ── GLB writer (E1) ──────────────────────────────────────────────────────────


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % 4)


def _glb(
    snap: dict[str, Any],
    features: list[dict[str, Any]],
    bindings: dict[str, list[dict[str, Any]]],
) -> tuple[bytes, dict[str, str]]:
    blob, views, accessors, meshes, nodes, geometry = bytearray(), [], [], [], [], {}

    def view(data: bytes, target: int) -> int:
        views.append(
            {
                "buffer": 0,
                "byteOffset": len(blob),
                "byteLength": len(data),
                "target": target,
            }
        )
        blob.extend(_pad(data, b"\x00"))
        return len(views) - 1

    for i, f in enumerate(features):
        pos = struct.pack(
            f"<{len(f['positions']) * 3}f", *(c for p in f["positions"] for c in p)
        )
        stored = [
            tuple(struct.unpack_from("<3f", pos, 12 * k))
            for k in range(len(f["positions"]))
        ]
        wide = len(stored) - 1 > 0xFFFF
        idx = struct.pack(f"<{len(f['indices'])}{'I' if wide else 'H'}", *f["indices"])
        geometry[f["id"]] = hashlib.sha256(pos + idx).hexdigest()
        accessors.append(
            {
                "bufferView": view(pos, _ARRAY_BUFFER),
                "componentType": _FLOAT,
                "count": len(stored),
                "type": "VEC3",
                "min": [min(p[k] for p in stored) for k in range(3)],
                "max": [max(p[k] for p in stored) for k in range(3)],
            }
        )
        accessors.append(
            {
                "bufferView": view(idx, _ELEMENT_ARRAY_BUFFER),
                "componentType": _UINT32 if wide else _UINT16,
                "count": len(f["indices"]),
                "type": "SCALAR",
            }
        )
        meshes.append(
            {
                "name": f["id"],
                "primitives": [
                    {"attributes": {"POSITION": 2 * i}, "indices": 2 * i + 1, "mode": 4}
                ],
            }
        )
        node = {
            "name": f["id"],
            "mesh": i,
            "translation": [_zero(c) for c in f["translation"]],
            "extras": {
                "feature_id": f["id"],
                "kind": f["kind"],
                "source_class": f["record"]["source_class"],
                "bindings": bindings.get(f["id"], []),
            },
        }
        if f["rotation"] is not None:
            node["rotation"] = [_zero(c) for c in f["rotation"]]
        nodes.append(node)
    site = snap["site"]
    nodes.append(
        {
            "name": f"site:{snap['site_id']}",
            "children": list(range(len(features))),
            "extras": {"kind": "site", "site_id": snap["site_id"]},
        }
    )
    doc = {
        "asset": {"version": "2.0", "generator": "PyPSA Studio site-scene export"},
        "scene": 0,
        "scenes": [
            {
                "name": str(site.get("name") or snap["site_id"]),
                "nodes": [len(nodes) - 1],
                "extras": {
                    "format": FORMAT,
                    "site_id": snap["site_id"],
                    "revision": snap["revision"],
                    "generation_sha256": snap["generation_sha256"],
                    "units": "m",
                },
            }
        ],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    if not features:
        for k in ("meshes", "accessors", "bufferViews", "buffers"):
            del doc[k]
    js = _pad(
        json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        ),
        b" ",
    )
    out = bytearray()
    total = 12 + 8 + len(js) + (8 + len(blob) if blob else 0)
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(js), b"JSON") + js
    if blob:
        out += struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob)
    return bytes(out), geometry


# ── manifest and bundle ──────────────────────────────────────────────────────


def _binding(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in BINDING_FIELDS if k in row}


def build(snap: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """The GLB and the manifest (without `exported_at`) of one snapshot."""
    features, omissions = _features(snap)
    rows = [_binding(a) for a in snap.get("associations") or [] if isinstance(a, dict)]
    direct: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        direct.setdefault(r.get("feature"), []).append(
            {k: r[k] for k in ("class", "uid", "name_at_bind", "role") if k in r}
        )
    glb, geometry = _glb(snap, features, direct)
    site = snap["site"]
    origin = site.get("origin") or {}
    manifest = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "snapshot": True,
        "note": NOTE,
        "project": snap.get("project"),
        "site": {"id": snap["site_id"], "name": site.get("name")},
        "scene": {
            "revision": snap["revision"],
            "generation": snap["generation"],
            "generation_sha256": snap["generation_sha256"],
        },
        "origin": {
            "lng": origin.get("lng"),
            "lat": origin.get("lat"),
            "crs": "EPSG:4326",
            "frame": "local east/north/up metres, equirectangular about the origin",
        },
        "axes": {
            "units": "m",
            "gltf": {"x": "east", "y": "up", "-z": "north"},
            "heading": "degrees clockwise from north",
            "up": "metres above the site ground plane",
        },
        "features": [
            {
                "id": f["id"],
                "kind": f["kind"],
                "node": i,
                "geometry_sha256": geometry[f["id"]],
                **f["record"],
            }
            for i, f in enumerate(features)
        ],
        "omissions": omissions,
        "bindings": rows,
        "groups": [
            {
                "id": g.get("id"),
                "label": g.get("label"),
                "members": list(g.get("members") or []),
            }
            for g in snap.get("groups") or []
            if isinstance(g, dict)
        ],
        "media": {"policy": "none", "included": []},
        "stats": {
            "features": len(features),
            "omitted": len(omissions),
            "triangles": sum(len(f["indices"]) // 3 for f in features),
            "glb_bytes": len(glb),
        },
    }
    manifest["hashes"] = {
        "glb_sha256": hashlib.sha256(glb).hexdigest(),
        "semantic_sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
    }
    return glb, manifest


def _canonical(obj: Any) -> bytes:
    try:
        return json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except ValueError:
        raise SceneError(
            "integrity_error",
            "This site's saved scene holds a number that is not finite",
        ) from None


def _zip(manifest: bytes, glb: bytes) -> bytes:
    """Fixed entry order, time, compression and attributes (E3)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in (("manifest.json", manifest), ("scene.glb", glb)):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            zf.writestr(info, data, compresslevel=6)
    return buf.getvalue()


def bundle(snap: dict[str, Any], *, exported_at: str) -> bytes:
    """The zip of one snapshot; `MAX_EXPORT_BYTES` is checked on the finished bytes."""
    glb, manifest = build(snap)
    data = _zip(_canonical({**manifest, "exported_at": exported_at}), glb)
    if len(data) > MAX_EXPORT_BYTES:
        raise _limit("export_bytes", MAX_EXPORT_BYTES, len(data))
    return data


def export_site(
    project_dir, project_name: str, site_id: str, exported_at: str
) -> tuple[bytes, dict[str, Any]]:
    """POST .../scene/exports: (zip bytes, {revision, site_name}). Reads one generation; writes nothing."""
    snap = {**read_snapshot(project_dir, site_id), "project": project_name}
    return bundle(snap, exported_at=exported_at), {
        "revision": snap["revision"],
        "site_name": snap["site"].get("name"),
    }
