"""
The reference scene for the SC-04 export tests, and the generator of the
committed GLB fixture that the frontend's GLTFLoader test reads (GK B E2).

Regenerate the fixture from `pypsa-gui/backend` with

    python -m tests.site_scene_export_fixture

and never edit the .glb by hand: `test_committed_fixture_is_the_generator_output`
byte-compares it with this generator.
"""
from __future__ import annotations

import copy
import pathlib

from services.site_scene import frames, store

FIXTURE = (pathlib.Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "site3d" / "__fixtures__" / "scene-export.glb")

# The routes test's greenfield box, by the same name.
HALL = {"id": "hall", "center": [0, 0], "width": 10, "depth": 20, "height": 8, "heading": 0, "unit": "m"}

_GENERATION = {
    "version": 1,
    "parent": "g000002-0123456789ab",
    "site": {
        "id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]],
        "placements": {
            "Generator:pv_roof": {"x": 30.0, "y": 10.0, "heading": 30.0},
            "Load:office": {"x": 0.0, "y": 0.0, "heading": 0.0},
            "Battery:bess": {"x": -20.0, "y": 0.0, "heading": 0.0},
        },
    },
    "buildings": [
        {"id": "greenfield:hall", "source": {"namespace": "greenfield", "source_id": "hall"},
         "footprint": [list(p) for p in frames.rectangle((0.0, 0.0), 10.0, 20.0, 30.0)],
         "height_m": 8.0, "height_evidence": "greenfield", "levels": None},
        # An L: 20 x 10 plus 10 x 10, area 300 m², concave at (50, 10).
        {"id": "osm:42", "source": {"namespace": "osm", "source_id": "42"},
         "footprint": [[40.0, 0.0], [60.0, 0.0], [60.0, 10.0], [50.0, 10.0], [50.0, 20.0], [40.0, 20.0]],
         "height_m": 12.0, "height_evidence": "tagged", "levels": None},
    ],
    "asset_footprints": {
        "Generator:pv_roof": {"width_m": 4.0, "depth_m": 2.0, "height_m": 2.5},
        "Battery:bess": {"width_m": 3.0, "depth_m": 2.0},
    },
    "associations": [
        {"id": "a1", "feature": "greenfield:hall", "class": "Load", "uid": "u-office",
         "name_at_bind": "office", "role": "consumer", "weight": 1.0},
    ],
    "groups": [{"id": "g1", "label": "Main hall", "members": ["greenfield:hall"]}],
    "receipt": None,
}


def reference_snapshot() -> dict:
    """What `export.read_snapshot` returns for the reference generation at revision 3."""
    gen = copy.deepcopy(_GENERATION)
    return {"project": "demo", "site_id": "campus", "revision": 3, "generation": "g000003-abcdef012345",
            "generation_sha256": store.sha256(gen), "site": gen["site"], "buildings": gen["buildings"],
            "asset_footprints": gen["asset_footprints"], "associations": gen["associations"],
            "groups": gen["groups"]}


def generate() -> bytes:
    from services.site_scene import export

    return export.build(reference_snapshot())[0]


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_bytes(generate())
    print(f"wrote {FIXTURE} ({FIXTURE.stat().st_size} bytes)")
