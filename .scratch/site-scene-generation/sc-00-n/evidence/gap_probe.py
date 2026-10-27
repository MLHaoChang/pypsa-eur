"""
Evidence for two recorded gaps on master (not fixed in SC-00 N):

  D06  the <Class>:<name> key aliases a same-name recreation;
  D03  today's recursive `_copy_bundle_dirs` copies orphan/superseded scene
       generations, so SC-01 must route enabled Sites through the closure.

Run from pypsa-gui/backend:
    python ../../.scratch/site-scene-generation/sc-00-n/evidence/gap_probe.py
"""
from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path.cwd()))

from routers import projects as projects_router  # noqa: E402
from services import site_service  # noqa: E402
from tests.site_scene_proto import scene_store  # noqa: E402

SITE = {"id": "campus", "name": "Campus", "buses": [], "origin": {"lng": 11.58, "lat": 48.14},
        "boundary": [[11.579, 48.139], [11.581, 48.139], [11.581, 48.141]],
        "placements": {"Generator:pv": {"x": 40.0, "y": 12.0, "heading": 90.0}}}
DOC = {"version": 1, "sites": [SITE], "asset_footprints": {"Generator:pv": {"width_m": 4, "depth_m": 2}}}


def d06() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        site_service.write_sites(p, copy.deepcopy(DOC))
        # 1. The delete hook network_crud.purge_component_side_data calls.
        site_service.remove_asset_footprint_on_disk(p, "Generator", "pv")
        doc = site_service.read_sites(p)
        print("D06-1 after delete: footprint kept =", "Generator:pv" in doc.get("asset_footprints", {}),
              "| placement kept =", "Generator:pv" in doc["sites"][0]["placements"],
              "-> a new Generator 'pv' inherits", doc["sites"][0]["placements"].get("Generator:pv"))
        # 2. The hook is best-effort: a write failure is logged and swallowed.
        site_service.write_sites(p, copy.deepcopy(DOC))
        with mock.patch.object(site_service, "write_sites", side_effect=OSError("disk full")):
            site_service.remove_asset_footprint_on_disk(p, "Generator", "pv")
        doc = site_service.read_sites(p)
        print("D06-2 after a failed delete hook: footprint kept =", "Generator:pv" in doc["asset_footprints"],
              "-> a new Generator 'pv' inherits", doc["asset_footprints"].get("Generator:pv"))


def closure_gap() -> None:
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "src", Path(d) / "dst"
        src.mkdir()
        dst.mkdir()
        site_service.write_sites(src, copy.deepcopy(DOC))
        scene_store.adopt(src, "campus", scene_store.legacy_site_digest(site_service.read_sites(src), "campus"))
        orphan = scene_store.scene_dir(src, "campus") / "generations" / "g000009-orphan" / "generation.json"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("{}")
        projects_router._copy_bundle_dirs(src, dst)
        copied = sorted(q.relative_to(dst).as_posix() for q in dst.rglob("*.json"))
        print("D03 recursive _copy_bundle_dirs copies:", copied)
        print("D03 closure would copy:", [q.as_posix() for q in scene_store.closure(src, "campus")])


if __name__ == "__main__":
    d06()
    closure_gap()
